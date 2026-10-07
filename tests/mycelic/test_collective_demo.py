"""G8: the collective demo (demo/collective) and its run-file contract (mycelic/collective/runfiles.py).

A fictional company, synthetic data and a constructed illustration: nothing here is a measurement, and no number in
these tests or in the run files may be quoted. The module records the committed scenario twice (PYTHONHASHSEED 0 and
4242, different work and output directories) in ``setUpModule``; RecordTests and DeterminismTests share those two
recordings. HonestyTests records two scenario variants of its own, and ExportReplayTests runs the server, the export
and the routing paths (against a local fake server; no model is ever called).
"""
from __future__ import annotations

import contextlib
import copy
import getpass
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.collective import collective_demo as demo  # noqa: E402
from demo.collective import lint_numbers  # noqa: E402
from demo.collective import scenario as scn  # noqa: E402
from demo.collective import screen as scr  # noqa: E402
from mycelic.collective import runfiles  # noqa: E402
from mycelic.collective.edge.egress import verdict_buckets  # noqa: E402
from mycelic.collective.edge.extract import TASK_NAME, lexical_handler  # noqa: E402
from mycelic.collective.edge.verify import DEMO_SECRET_PREFIX, JUDGE_TASK, lexical_judge  # noqa: E402
from mycelic.collective.followup.drafts import DRAFT_TASK, template_draft  # noqa: E402
from mycelic.collective.followup.policy import OUTCOME_LABEL  # noqa: E402
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload  # noqa: E402
from mycelic.collective.jsonio import canonical_bytes, sha256_hex  # noqa: E402
from mycelic.collective.leakage import Artifact, scan  # noqa: E402
from mycelic.collective.packs.canonical import Canonicaliser  # noqa: E402

DEMO_DIR = ROOT / "demo" / "collective"
SCRIPT = "demo/collective/collective_demo.py"
LINT = "demo/collective/lint_numbers.py"
RECORDED = DEMO_DIR / "recorded"
BASELINE_CHANNELS = ("R_mf", "U", "single_site")
CHANNEL_KEYS = {"rank", "caught", "related"}
FEATURE_KEYS = {"logp", "p_s", "expected", "c", "n_ep_lb", "surprise", "features", "score", "count", "n"}
STUB = "deterministic stand-in, no model"
HEX16 = re.compile(r"[0-9a-f]{16}")
FIXTURE: dict[str, Any] = {}


# --------------------------------------------------------------------------------------------------- helpers

def run_demo(*args: str, env: dict[str, str] | None = None, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "PYTHONPATH": str(ROOT), **(env or {})})


def run_lint(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, LINT, *args], cwd=ROOT, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def lint_in_process(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lint_numbers.main(list(args))
    return code, out.getvalue(), err.getvalue()


def committed_dir() -> Path:
    dirs = sorted(p for p in RECORDED.iterdir() if p.is_dir() and p.name != "__pycache__")
    assert len(dirs) == 1, "exactly one committed run"
    return dirs[0]


def load(directory: Path) -> dict[str, Any]:
    return runfiles.read_run_files(directory, runfiles.RUN_FILES)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def secret_findings(text: str) -> list[str]:
    """tests/mycelic/test_live_demo_process.secret_findings, with its exact patterns."""
    found = []
    if "mk_" in text:
        found.append("agent api key prefix mk_")
    if re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text):
        found.append("64-hex token (admin token / signing key)")
    for key in ("api_key", "admin_token", "password", "signing_key", "Authorization"):
        if f'"{key}"' in text:
            found.append(f"field {key}")
    return found


def walk(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for n, v in enumerate(value):
            yield from walk(v, f"{path}[{n}]")


def screen_texts(screen: dict[str, Any]) -> list[str]:
    return [p["text"] for b in screen["blocks"] for p in b["parts"] if p["text"] is not None]


def blocks_with_text(screen: dict[str, Any], text: str) -> list[dict[str, Any]]:
    return [b for b in screen["blocks"] if any(p["text"] is not None and text in p["text"] for p in b["parts"])]


def get_json(port: int, path: str) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


def post_control(port: int, body: Any, *, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/control", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def read_events(port: int, *, last_event_id: str | None, want: int, timeout: float = 15) -> tuple[bool, list[int]]:
    """(saw a snapshot, the ids of the first ``want`` messages) from ``GET /events``."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Last-Event-ID": last_event_id} if last_event_id is not None else {}
    conn.request("GET", "/events", headers=headers)
    resp = conn.getresponse()
    snapshot, ids = False, []
    try:
        while len(ids) < want:
            line = resp.fp.readline().decode("utf-8")
            if not line:
                break
            if line.startswith("event: snapshot"):
                snapshot = True
            elif line.startswith("id: "):
                ids.append(int(line[4:].strip()))
    finally:
        conn.close()
    return snapshot, ids


def wait_until(predicate, *, timeout: float = 90, what: str = "a condition") -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            pass
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def seeded_secrets(seed: int, sites: list[str]) -> list[bytes]:
    out = []
    for site in sites:
        raw = hashlib.sha256(f"{DEMO_SECRET_PREFIX}:{seed}:{site}".encode("utf-8")).digest()
        out += [raw, raw.hex().encode("ascii")]
    return out


def write_variant(directory: Path, change) -> Path:
    raw = json.loads((DEMO_DIR / "scenario.json").read_text(encoding="utf-8"))
    change(raw)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "scenario.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def setUpModule() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="collective-demo-test-"))
    FIXTURE["tmp"] = tmp
    for seed in ("0", "4242"):
        out, work = tmp / f"rec-{seed}", tmp / f"work-{seed}"
        t0 = time.monotonic()
        r = run_demo("--record", str(out), "--workdir", str(work), "--run-id", f"fixture-{seed}",
                     env={"PYTHONHASHSEED": seed})
        FIXTURE[seed] = {"out": out, "work": work, "result": r, "elapsed": time.monotonic() - t0}


def tearDownModule() -> None:
    shutil.rmtree(FIXTURE["tmp"], ignore_errors=True)


# --------------------------------------------------------------------------------------------------- run files

class _Entry:
    def __init__(self, seq: int, prev: str, h: str) -> None:
        self.seq, self.at, self.kind, self.key, self.actor = seq, "2024-01-01T00:00:00Z", "proposed", "k", "system"
        self.payload = {"ref": "a" * 64, "n": seq}
        self.prev_hash, self.hash = prev, h


def _row(site: str | None, task: str, *, ok: bool = True, tokens: tuple[Any, Any] = (3, 4),
         fake: bool = True) -> dict[str, Any]:
    return {"boundary": f"site:{site}" if site else "central", "task": task, "attempt": 1, "endpoint": "e",
            "provider": "fake", "model_requested": "fake", "boundary_mode": "own", "data_label": "synthetic",
            "ok": ok, "error_kind": None if ok else "timeout", "tokens_in": tokens[0], "tokens_out": tokens[1],
            "latency_ms": 0.0, "cost_basis": "fake", "fake_marker": fake, "host": "h", "ts": "t",
            "model_served": "m", "ref": "r"}


class RunFilesTests(unittest.TestCase):
    def test_shorten_maps_64_hex_to_32_and_refuses_an_embedded_one(self) -> None:
        h = "ab" * 32
        self.assertEqual(runfiles.shorten({h: [h, 1, None, {"x": h}]}), {h[:32]: [h[:32], 1, None, {"x": h[:32]}]})
        self.assertEqual(runfiles.shorten("short"), "short")
        self.assertEqual(runfiles.digest(h), h[:32])
        for bad in ("x" + h, h + " tail", {"k": "id-" + h}, ["a", f"{h}{h}"]):
            with self.subTest(bad=str(bad)[:20]):
                with self.assertRaises(runfiles.RunFileError) as cm:
                    runfiles.shorten(bad)
                self.assertNotIn(h, str(cm.exception))
        for bad in ("AB" * 32, h[:-1], 7, None):
            with self.assertRaises(runfiles.RunFileError):
                runfiles.digest(bad)

    def test_project_ledger_caps_per_group_and_summarises_every_row(self) -> None:
        rows = ([_row("a", "extract_claims") for _ in range(5)] + [_row("b", "extract_claims", ok=False)]
                + [_row(None, "draft_followup", tokens=(None, 2), fake=False) for _ in range(3)])
        out, summary = runfiles.project_ledger(rows, per_group=2)
        self.assertEqual([(r["site"], r["task"]) for r in out],
                         [("a", "extract_claims")] * 2 + [("b", "extract_claims")] + [("hq", "draft_followup")] * 2)
        for r in out:
            self.assertEqual(tuple(r), runfiles.LEDGER_ROW_KEYS)
        self.assertEqual((summary["rows_total"], summary["rows_written"], summary["per_group"], summary["capped"]),
                         (9, 5, 2, True))
        self.assertEqual(summary["by_task"], [
            {"task": "draft_followup", "calls": 3, "ok": 3, "errors": 0, "tokens_in": 0, "tokens_out": 6,
             "tokens_missing": 3, "fake": False},
            {"task": "extract_claims", "calls": 6, "ok": 5, "errors": 1, "tokens_in": 18, "tokens_out": 24,
             "tokens_missing": 0, "fake": True}])
        _, uncapped = runfiles.project_ledger(rows[:2], per_group=12)
        self.assertFalse(uncapped["capped"])
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(runfiles.RunFileError):
                runfiles.project_ledger(rows, per_group=bad)

    def test_project_entries_ends_with_the_ledger_head(self) -> None:
        h = [f"{n:x}" * 64 for n in range(1, 4)]
        entries = [_Entry(0, "0" * 64, h[0]), _Entry(1, h[0], h[1]), _Entry(2, h[1], h[2])]
        lines = runfiles.project_entries(entries, label="built ahead")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[-1], {"kind": runfiles.HEAD_KIND, "head_hash": h[2][:32], "entries": 3,
                                     "label": "built ahead"})
        self.assertEqual([line["prev_hash"] for line in lines[1:3]], [line["hash"] for line in lines[:2]])
        self.assertEqual(lines[0]["payload"], {"ref": "a" * 32, "n": 0})
        self.assertEqual(runfiles.project_entries([], label="x"),
                         [{"kind": runfiles.HEAD_KIND, "head_hash": None, "entries": 0, "label": "x"}])

    def test_portability_problems_flags_each_rule(self) -> None:
        cases = {
            "absolute_path": [b'{"p": "/home/someone/x"}', b'{"p": "/tmp/a/b"}', b'{"p": "C:\\\\Users\\\\x"}'],
            "forbidden_string": [b'{"host": "on buildhost now"}'],
            "hex64": [b'{"h": "' + b"c" * 64 + b'"}'],
            "credential_key": [b'{"api_key": "x"}', b'{"password" : 1}'],
            "key_prefix": [b'{"k": "mk_live"}'],
        }
        for rule, samples in cases.items():
            for data in samples:
                with self.subTest(rule=rule, data=data):
                    self.assertEqual(runfiles.portability_problems(data, forbidden=["buildhost", "ab"]), [rule])
        clean = (b'{"x": "demo/collective/recorded/run", "h": "' + b"c" * 32 + b'", "n": "buildhostname", '
                 b'"t": "a/home/b", "s": "ab"}')
        self.assertEqual(runfiles.portability_problems(clean, forbidden=["buildhost", "ab"]), [])
        self.assertEqual(list(runfiles.PORTABILITY_RULES), list(cases))

    def test_portability_passes_the_committed_run(self) -> None:
        forbidden = [str(ROOT), str(Path.home()), socket.gethostname(), getpass.getuser()]
        for name in runfiles.RUN_FILES:
            with self.subTest(file=name):
                data = (committed_dir() / name).read_bytes()
                self.assertEqual(runfiles.portability_problems(data, forbidden=forbidden), [])

    def test_resolve_handles_escapes_jsonl_lines_and_each_problem(self) -> None:
        docs = {"scorecard.json": {"a/b": {"m~n": 5}, "l": [1, {"x": None}], "f": 0.5},
                "ledger.jsonl": [{"k": "v"}, {"k": True}]}
        self.assertEqual(runfiles.resolve(docs, "scorecard.json#/a~1b/m~0n"), 5)
        self.assertEqual(runfiles.resolve(docs, "scorecard.json#/l/1/x"), None)
        self.assertEqual(runfiles.resolve(docs, "scorecard.json#/f"), 0.5)
        self.assertEqual(runfiles.resolve(docs, "ledger.jsonl#/1/k"), True)
        problems = {
            "screen.json#/run_id": "src_not_primary", "notes.json#/a": "src_not_primary",
            "scorecard.json#/nope": "src_unresolved", "scorecard.json": "src_unresolved",
            "scorecard.json#a": "src_unresolved", "scorecard.json#/a~2b": "src_unresolved",
            "scorecard.json#/l/2": "src_unresolved", "scorecard.json#/l/01": "src_unresolved",
            "ledger.jsonl#/x/k": "src_unresolved", "trace.json#/meta": "src_unresolved",
            "scorecard.json#/l": "src_not_scalar", "scorecard.json#/a~1b": "src_not_scalar",
            "scorecard.json#": "src_not_scalar",
        }
        for src, problem in problems.items():
            with self.subTest(src=src):
                with self.assertRaises(runfiles.RunFileError) as cm:
                    runfiles.resolve(docs, src)
                self.assertEqual(cm.exception.problem, problem)
                self.assertIn(problem, runfiles.SRC_PROBLEMS)

    def test_write_run_files_writes_nothing_when_any_file_has_a_problem(self) -> None:
        good = {"scorecard.json": {"kind": "k", "run_id": "r"}, "ledger.jsonl": [{"a": 1}, {"a": 2}]}
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({**good, "trace.json": {"p": "/home/u/x"}}, {**good, "trace.json": {"p": "secret-host"}},
                        {**good, "notes.json": {}}, {**good, "leakage.json": {"x": float("nan")}}, {}):
                target = Path(tmp) / "out"
                with self.subTest(bad=sorted(bad)):
                    with self.assertRaises(runfiles.RunFileError) as cm:
                        runfiles.write_run_files(target, bad, forbidden=["secret-host"])
                    self.assertFalse(target.exists())
                    self.assertNotIn("/home/u", str(cm.exception))
            target = Path(tmp) / "ok"
            order = runfiles.write_run_files(target, {**good, "trace.json": {"t": 1}}, forbidden=["secret-host"])
            self.assertEqual(order, ["trace.json", "ledger.jsonl", "scorecard.json"])
            self.assertEqual((target / "ledger.jsonl").read_bytes(), b'{"a":1}\n{"a":2}\n')
            self.assertEqual(runfiles.read_run_files(target, ["ledger.jsonl", "scorecard.json"]),
                             {"ledger.jsonl": [{"a": 1}, {"a": 2}], "scorecard.json": good["scorecard.json"]})
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["ledger.jsonl", "scorecard.json",
                                                                         "trace.json"])

    def test_read_run_files_refuses_missing_invalid_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "ledger.jsonl").write_bytes(b'{"a":1}\n\n{"a":2}\n')
            (d / "trace.json").write_bytes(b'{"a": NaN}')
            for name, problem in (("ledger.jsonl", "invalid JSON"), ("trace.json", "invalid JSON"),
                                  ("scorecard.json", "missing")):
                with self.subTest(name=name):
                    with self.assertRaises(runfiles.RunFileError) as cm:
                        runfiles.read_run_files(d, [name])
                    self.assertEqual((cm.exception.where, cm.exception.problem), (name, problem))

    def test_content_hash_ignores_excluded_keys_and_follows_every_other(self) -> None:
        doc = {"run_id": "a", "created_at": "t1", "hero": {"rank": 2}, "checks": [1, 2]}
        excludes = ("/run_id", "/created_at")
        h = runfiles.content_hash(doc, excludes)
        self.assertRegex(h, r"^[0-9a-f]{32}$")
        self.assertEqual(runfiles.content_hash({**doc, "run_id": "b", "created_at": "t2"}, excludes), h)
        for changed in ({**doc, "hero": {"rank": 3}}, {**doc, "checks": [1]}, {**doc, "extra": None}):
            self.assertNotEqual(runfiles.content_hash(changed, excludes), h)
        for bad in (["run_id"], ["/hero/rank"], [7]):
            with self.assertRaises(runfiles.RunFileError):
                runfiles.content_hash(doc, bad)


# --------------------------------------------------------------------------------------------------- one recording

class _HeroAssertions:
    """The hero-block assertions shared by the fixture recordings and the committed run."""

    assertEqual: Any
    assertTrue: Any
    assertFalse: Any
    assertIn: Any
    assertIsInstance: Any
    assertGreaterEqual: Any
    assertRegex: Any
    assertLessEqual: Any

    def assert_detection(self, docs: dict[str, Any]) -> None:
        hero = docs["scorecard.json"]["hero"]
        det = hero["detection"]
        self.assertIsInstance(det["X"]["rank"], int)
        self.assertTrue(det["X"]["caught"])
        for channel in ("S", "R_mf", "U", "single_site"):
            self.assertEqual(set(det[channel]), CHANNEL_KEYS, channel)
        self.assertEqual((det["S"]["caught"], det["R_mf"]["caught"]), (False, False))
        self.assertEqual((det["S"]["rank"], det["R_mf"]["rank"]), (None, None))
        self.assertEqual((det["s_caught"], det["r_caught"]), (det["S"]["caught"], det["R_mf"]["caught"]))
        self.assertEqual((det["by_construction"]["S"], det["by_construction"]["R_mf"]), (True, True))
        self.assertEqual(det["rule_candidates"], [])
        self.assertIn(hero["key"]["key"], hero["case_keys"])
        for channel in ("X", "S", "R_mf", "U", "single_site"):
            for rel in det[channel]["related"]:
                self.assertIn(rel["key"], hero["case_keys"])
        items = {i["id"]: i for i in docs["screen.json"]["items"]}
        self.assertEqual((items["s_rank"]["display"], items["r_rank"]["display"]), (scr.NOT_ALERTED, scr.NOT_ALERTED))

    def assert_pushdown(self, docs: dict[str, Any]) -> None:
        pd = docs["scorecard.json"]["hero"]["pushdown"]
        pack = scn.load_scenario().pack
        buckets = set(verdict_buckets(pack)) | {None}
        confirms = [v for v in pd["verdicts"] if v["role"] == "contributing" and v["verdict"] == "confirm"]
        refutes = [v for v in pd["verdicts"] if v["role"] == "sibling" and v["verdict"] == "refute"]
        self.assertGreaterEqual(len(confirms), 3)
        self.assertGreaterEqual(len(refutes), 1)
        self.assertGreaterEqual(pd["contributing_confirms"], 3)
        self.assertGreaterEqual(pd["sibling_refutes"], 1)
        self.assertEqual(pd["gate"]["status"], "supported")
        self.assertTrue(pd["resolvable"])
        self.assertTrue({v["role"] for v in pd["verdicts"]} >= {"contributing", "sibling"})
        for v in pd["verdicts"]:
            self.assertEqual(set(v), {"site", "role", "verdict", "quality", "reason", "support_bucket",
                                      "roots_bucket", "reporters_bucket", "entity_records_bucket", "evidence_ref",
                                      "newest_week", "truncated"})
            for field in ("support_bucket", "roots_bucket", "reporters_bucket", "entity_records_bucket"):
                self.assertIn(v[field], buckets)
            self.assertTrue(v["evidence_ref"] is None or HEX16.fullmatch(v["evidence_ref"]) is not None)
        for v in confirms:
            self.assertRegex(v["evidence_ref"], HEX16)

    def assert_followup(self, docs: dict[str, Any], *, approval: str, requests: int) -> None:
        sc = docs["scorecard.json"]
        fu = sc["hero"]["followup"]
        pack = scn.load_scenario().pack
        self.assertTrue(fu["proposed"])
        packet, draft = fu["packet"], fu["draft"]
        self.assertEqual(packet["result_status"], "complete")
        self.assertEqual(sorted(p["site"] for p in packet["packets"]), sorted(s["site"] for s in sc["hero"]["sites"]))
        self.assertTrue(all(p["status"] == "ok" for p in packet["packets"]))
        for part in (packet, draft):
            self.assertEqual((part["executor_calls"], part["execute_requests"], part["approval"], part["state"]),
                             (1, requests, approval, "executed"))
            self.assertEqual(part["owner_role_label"], pack.roles[part["owner_role"]].label)
            self.assertTrue(part["owner"])
        lines = docs["approvals.jsonl"]
        entries, head = lines[:-1], lines[-1]
        self.assertEqual(head["kind"], runfiles.HEAD_KIND)
        for prev, line in zip(entries, entries[1:]):
            self.assertEqual(line["prev_hash"], prev["hash"])
            self.assertEqual(line["seq"], prev["seq"] + 1)
        self.assertEqual(head["head_hash"], entries[-1]["hash"])
        self.assertEqual(head["head_hash"], fu["ledger_head"])
        self.assertEqual(head["entries"], len(entries))
        self.assertEqual(fu["ledger_entries"], len(entries))
        self.assertEqual(fu["outcome"], {"status": "not yet checked", "label": OUTCOME_LABEL})
        checks = {c["id"]: c["ok"] for c in sc["checks"]}
        self.assertTrue(checks["ledger_chain_ok"] and checks["executed_once"] and checks["packets_from_contributing"])
        approvals = [e for e in docs["trace.json"]["events"] if e["type"] == "approval"]
        self.assertEqual(sorted(e["data"]["key"] for e in approvals), sorted([packet["key"], draft["key"]]))
        self.assertEqual({e["data"]["mode"] for e in approvals}, {approval})

    def assert_no_baseline_numbers(self, docs: dict[str, Any]) -> None:
        for name in runfiles.PRIMARY_FILES:
            for path, value in walk(docs[name]):
                if not isinstance(value, dict):
                    continue
                for channel in BASELINE_CHANNELS:
                    if channel in value and isinstance(value[channel], dict):
                        block = value[channel]
                        self.assertLessEqual(set(block), CHANNEL_KEYS, f"{name} {path}.{channel}")
                        for rel in block.get("related", []):
                            self.assertLessEqual(set(rel), {"key", "week", "rank"}, f"{name} {path}")
                        inner = {k for _, v in walk(block) if isinstance(v, dict) for k in v}
                        self.assertEqual(inner & FEATURE_KEYS, set(), f"{name} {path}.{channel}")
        alerts = [e for e in docs["trace.json"]["events"] if e["type"] == "alert"]
        self.assertTrue(any(e["data"]["channel"] in BASELINE_CHANNELS for e in alerts))
        for e in alerts:
            self.assertLessEqual(set(e["data"]), {"rank", "caught", "related", "key", "week", "channel"})
        for item in docs["screen.json"]["items"]:
            if any(f"/{c}/" in item["src"] for c in BASELINE_CHANNELS):
                self.assertRegex(item["src"], r"/(rank|caught|week|key)$", item["id"])


class RecordTests(_HeroAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rec = FIXTURE["0"]
        cls.out = cls.rec["out"]
        cls.docs = load(cls.out) if (cls.out / "scorecard.json").exists() else {}

    def test_exit_time_and_six_files(self) -> None:
        r = self.rec["result"]
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(self.rec["elapsed"], 300)
        self.assertRegex(r.stdout, r"record: [0-9.]+ s")
        self.assertIn("gate=supported checks 12/12", r.stdout)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(runfiles.RUN_FILES))
        self.assertEqual(demo.validate_run(self.docs), [])
        mtimes = {p.name: p.stat().st_mtime_ns for p in self.out.iterdir()}
        self.assertEqual(max(mtimes, key=lambda n: (mtimes[n], n == runfiles.SCORECARD)), runfiles.SCORECARD)
        self.assertFalse(self.rec["work"].exists())
        self.assertFalse(any(p.name in ("manifest.json", "packets", "private") for p in self.out.rglob("*")))

    def test_stamps_and_providers(self) -> None:
        sc, trace = self.docs["scorecard.json"], self.docs["trace.json"]
        stamps = sc["stamps"]
        self.assertTrue(all(stamps[k] for k in ("fictional", "synthetic", "internal_only", "illustration")))
        self.assertFalse(stamps["measurement"])
        self.assertEqual((sc["mode"], stamps["approval"], trace["meta"]["approval"], trace["meta"]["mode"]),
                         ("record", "recorded", "recorded", "record"))
        self.assertFalse(stamps["shared_model"])
        self.assertIsNone(trace["meta"]["sites_note"])
        self.assertEqual({p["task"]: p["label"] for p in sc["providers"]},
                         {"extract": STUB, "judge": STUB, "draft": STUB})
        self.assertTrue(all(p["fake"] for p in sc["providers"]))

    def test_hero_detection_block(self) -> None:
        self.assert_detection(self.docs)

    def test_no_baseline_counts_or_features(self) -> None:
        self.assert_no_baseline_numbers(self.docs)

    def test_hero_pushdown_block(self) -> None:
        self.assert_pushdown(self.docs)
        questions = [e for e in self.docs["trace.json"]["events"] if e["type"] == "question"
                     and e["data"]["key"] == self.docs["scorecard.json"]["hero"]["key"]["key"]]
        self.assertEqual(len(questions), 1)

    def test_followup_block(self) -> None:
        self.assert_followup(self.docs, approval="recorded", requests=2)
        executions = [e for e in self.docs["trace.json"]["events"] if e["type"] == "execution"]
        self.assertEqual(len(executions), 4)
        self.assertEqual({e["data"]["executor_calls"] for e in executions}, {1})

    def test_decoys_and_leakage(self) -> None:
        sc, leak = self.docs["scorecard.json"], self.docs["leakage.json"]
        self.assertTrue(all(d["status"] != "supported" for d in sc["decoys"]))
        self.assertTrue(any(d["x_alerted"] and d["verified"] for d in sc["decoys"]))
        self.assertEqual([s["scan"] for s in leak["scans"]], ["after_pushdown", "final"])
        for s in leak["scans"]:
            self.assertEqual((s["hit_count"], s["hits"], s["shingle_overlap_bytes"], s["shingle_hits"]),
                             (0, [], 0, []))
        final = {c["class"]: c for c in leak["scans"][1]["artifact_classes"]}
        self.assertEqual(final["run_files"]["items"], 4)
        self.assertNotIn("run_files", {c["class"] for c in leak["scans"][0]["artifact_classes"]})
        self.assertGreater(leak["positive_control"]["canary_hits"], 0)
        self.assertGreater(leak["positive_control"]["shingle_overlap_bytes"], 0)

    def test_ledger_capped_and_clean(self) -> None:
        summary, rows = self.docs["scorecard.json"]["ledger"], self.docs["ledger.jsonl"]
        groups: dict[tuple[str, str], int] = {}
        for row in rows:
            self.assertEqual(set(row), set(runfiles.LEDGER_ROW_KEYS))
            groups[(row["site"], row["task"])] = groups.get((row["site"], row["task"]), 0) + 1
        self.assertTrue(all(n <= demo.LEDGER_PER_GROUP for n in groups.values()))
        self.assertEqual(summary["rows_written"], len(rows))
        self.assertLessEqual(summary["rows_written"], demo.LEDGER_PER_GROUP * len(groups))
        self.assertLess(summary["rows_written"], summary["rows_total"])
        self.assertEqual(sum(t["calls"] for t in summary["by_task"]), summary["rows_total"])
        text = (self.out / "ledger.jsonl").read_text(encoding="utf-8")
        for key in ("host", "ref", "ts", "model_served"):
            self.assertNotIn(f'"{key}"', text)

    def test_run_files_portable(self) -> None:
        forbidden = [str(self.rec["work"]), str(self.rec["work"].resolve()), str(self.out), str(self.out.resolve()),
                     str(ROOT), str(Path.home()), socket.gethostname(), getpass.getuser()]
        for name in runfiles.RUN_FILES:
            with self.subTest(file=name):
                data = (self.out / name).read_bytes()
                self.assertEqual(runfiles.portability_problems(data, forbidden=forbidden), [])
                self.assertEqual(secret_findings(data.decode("utf-8")), [])

    def test_cut_excludes_followup(self) -> None:
        cut = ["problem", "alert", "check", "real_data"]
        for name in ("trace.json", "screen.json"):
            doc = self.docs[name]
            self.assertEqual(doc["cut_60s"], cut)
            self.assertEqual({b["id"]: b["in_cut"] for b in doc["beats"]}["followup"], False)


# --------------------------------------------------------------------------------------------------- the lint

class LintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="collective-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "run"
        shutil.copytree(committed_dir(), self.run_dir)
        for name in ("console.html", "SCRIPT.md", "README.md"):
            shutil.copy(DEMO_DIR / name, self.tmp / name)

    def lint(self) -> tuple[int, str, str]:
        return lint_in_process(str(self.run_dir), "--console", str(self.tmp / "console.html"), "--script",
                               str(self.tmp / "SCRIPT.md"), "--readme", str(self.tmp / "README.md"))

    def edit(self, name: str, change) -> None:
        path = self.tmp / name
        path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")

    def edit_screen(self, change) -> None:
        path = self.run_dir / "screen.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def reset(self) -> None:
        shutil.rmtree(self.run_dir)
        shutil.copytree(committed_dir(), self.run_dir)
        for name in ("console.html", "SCRIPT.md", "README.md"):
            shutil.copy(DEMO_DIR / name, self.tmp / name)

    def assert_fails(self, *tokens: str) -> None:
        code, out, err = self.lint()
        self.assertEqual(code, 1, out + err)
        for token in tokens:
            self.assertIn(token, out)

    def test_committed_run_passes(self) -> None:
        r = run_lint(str(committed_dir()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("lint: ok ("), r.stdout)

    def test_each_injection_fails_naming_the_token(self) -> None:
        def console(snippet: str):
            return lambda text: text.replace("<!-- collective:screen -->", snippet + "\n<!-- collective:screen -->")

        def script(line: str):
            return lambda text: text.replace('"use strict";', '"use strict";\n  ' + line)

        def item(item_id: str, **fields: Any):
            def change(doc: dict[str, Any]) -> None:
                next(i for i in doc["items"] if i["id"] == item_id).update(fields)
            return change

        def first_text(suffix: str):
            def change(doc: dict[str, Any]) -> None:
                part = next(p for b in doc["blocks"] for p in b["parts"] if p["text"] is not None)
                part["text"] += suffix
            return change

        def add_item(item_id: str):
            def change(doc: dict[str, Any]) -> None:
                src = next(i for i in doc["items"] if i["id"] == "company")
                doc["items"].append({**src, "id": item_id})
            return change

        x_rank = next(i for i in load(committed_dir())["screen.json"]["items"] if i["id"] == "x_rank")
        other = "scorecard.json#/hero/detection/single_site/rank"
        self.assertNotEqual(runfiles.resolve(load(committed_dir()), other), int(x_rank["display"]))
        cases = [
            ("console", console("<p>Ranked 7 of all</p>"), ("digit_in_text", "7")),
            ("console", console('<span aria-label="step 3">x</span>'), ("digit_in_text", "3")),
            ("console", console('<span title="week 12">x</span>'), ("digit_in_text", "12")),
            ("console", script('var probe = "top 5 keys";'), ("digit_in_text", "5")),
            ("console", script('var probe = "rank \\u0033";'), ("digit_in_text", "3")),
            ("console", script('var probe = "t\\u0068ree plants";'), ("number_word", "three")),
            ("console", script('var tag = "\\u006fl";'), ("console_markup", "ol")),
            ("console", script("var probe = `rank ${x} of 8`;"), ("digit_in_text", "8")),
            ("script", lambda t: t + "\nThe detectors flag 4 keys.\n", ("digit_in_text", "4")),
            ("script", lambda t: t + "\nThree plants answer.\n", ("number_word", "Three")),
            ("screen", first_text(" seven"), ("number_word", "seven")),
            ("screen", first_text(" 9"), ("digit_in_text", "9")),
            ("screen", item("x_rank", label="Rank 2 of X"), ("digit_in_text", "2")),
            ("screen", item("x_rank", display="1"), ("display_mismatch", "x_rank=1")),
            ("screen", item("x_rank", src=other), ("display_mismatch", "x_rank=")),
            ("screen", item("x_rank", src="screen.json#/run_id"), ("src_not_primary", "x_rank")),
            ("screen", item("x_rank", src="scorecard.json#/hero/nope"), ("src_unresolved", "x_rank")),
            ("screen", item("x_rank", src="scorecard.json#/hero/detection"), ("src_not_scalar", "x_rank")),
            ("screen", add_item("recall"), ("aggregate_metric_key", "recall")),
            ("screen", add_item("x_recall"), ("aggregate_metric_key", "x_recall")),
            ("screen", add_item("x_minus_s_lift"), ("aggregate_metric_key", "x_minus_s_lift")),
            ("console", console("<ol><li>a</li></ol>"), ("console_markup", "<ol")),
            ("console", lambda t: t.replace("</style>", ".x::before { content: counter(item); }\n</style>"),
             ("console_markup", "counter(")),
            ("console", lambda t: t.replace("</style>", "ul.y { list-style: decimal; }\n</style>"),
             ("console_markup", "decimal")),
        ]
        phrases = [p.replace("(s)", "") for p in lint_numbers.DENYLIST] + ["any fields", "Collective  Intelligence"]
        self.assertIn("learns", phrases)
        for phrase in phrases:
            cases += [("console", console(f"<p>It {phrase} here</p>"), ("denylisted_phrase", phrase)),
                      ("screen", first_text(f" {phrase}"), ("denylisted_phrase", phrase)),
                      ("script", lambda t, p=phrase: t + f"\nIt {p} here.\n", ("denylisted_phrase", phrase)),
                      ("readme", lambda t, p=phrase: t + f"\nIt {p} here.\n", ("denylisted_phrase", phrase))]
        for figure in ("57/78", "78/57", "57% vs 78%", "75.1%", "0% centralised", "2.6×", "2.6x"):
            cases += [("script", lambda t, f=figure: t + f"\nA figure: {f} here.\n", ("benchmark_figure", figure)),
                      ("readme", lambda t, f=figure: t + f"\nA figure: {f} here.\n", ("benchmark_figure", figure))]
        self.assertEqual(self.lint()[0], 0)
        for where, change, tokens in cases:
            with self.subTest(where=where, tokens=tokens):
                self.reset()
                if where == "screen":
                    self.edit_screen(change)
                else:
                    self.edit({"console": "console.html", "script": "SCRIPT.md", "readme": "README.md"}[where],
                              change)
                self.assert_fails(*tokens)

    def test_allowed_tokens_pass(self) -> None:
        self.edit("console.html", lambda t: t.replace('"use strict";', '"use strict";\n  var css = ["12px", "#1a2b3c", '
                                                      '"250ms", "rgba(0,0,0,.5)", "translateX(4px)", "1.5rem"];'))
        self.edit("console.html", lambda t: t.replace("<!-- collective:screen -->",
                                                      "<p>X4 N1 Phase-1 60-second E2 T0</p>\n"
                                                      "<!-- collective:screen -->"))
        self.edit("console.html", lambda t: t.replace("</style>", ".z { width: 12px; color: #1a2b3c; "
                                                      "transition: opacity 250ms; }\n</style>"))
        self.edit("SCRIPT.md", lambda t: t + "\n| 1:05–1:12 | Extra | \"Say the X4 caption.\" | The caption. |\n")
        code, out, err = self.lint()
        self.assertEqual(code, 0, out + err)
        for literal in ("12px", "#1a2b3c", "250ms", "rgba(0,0,0,.5)", "translateX(4px)"):
            self.assertEqual(lint_numbers.text_problems(literal, literal=True), [])
        self.assertEqual(lint_numbers.text_problems("X4 N1 Phase-1 60-second"), [])
        self.assertTrue(lint_numbers.text_problems("12px"))            # outside a script literal a length is a digit

    def test_usage_errors_exit_2_one_line(self) -> None:
        missing_screen = self.tmp / "no-screen"
        shutil.copytree(committed_dir(), missing_screen)
        (missing_screen / "screen.json").unlink()
        invalid = self.tmp / "invalid"
        shutil.copytree(committed_dir(), invalid)
        (invalid / "trace.json").write_text("{", encoding="utf-8")
        old = self.tmp / "old"
        shutil.copytree(committed_dir(), old)
        for name in ("scorecard.json", "trace.json", "leakage.json", "screen.json"):
            doc = json.loads((old / name).read_text(encoding="utf-8"))
            doc["schema_version"] = 2
            (old / name).write_text(json.dumps(doc), encoding="utf-8")
        for directory in (self.tmp / "nowhere", missing_screen, invalid, old):
            with self.subTest(directory=directory.name):
                r = run_lint(str(directory))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                lines = r.stderr.strip().splitlines()
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertTrue(lines[0].startswith("error: "), r.stderr)
                self.assertNotIn("Traceback", r.stderr + r.stdout)

    def test_format_value_and_parse_display(self) -> None:
        cases = [(1234, "int", "1,234"), (0, "int", "0"), (-5, "int", "-5"), (0.256, "pct", "26%"), (1, "pct", "100%"),
                 (1.005, "dec2", format(1.005, ".2f")), (2.5, "dec2", "2.50"), (None, "rank", scr.NOT_ALERTED),
                 (3, "rank", "3"), ("ab" * 16, "digest12", "ab" * 6), (True, "yesno", "yes"), (False, "yesno", "no"),
                 ("2024-10-28T08:00:00.000Z", "datetime", "2024-10-28 08:00 UTC"),
                 ("2024-11-02T00:00:00Z", "datetime", "2024-11-02 00:00 UTC"),
                 ("Werk Dornhagen – Gerätemontage (fiktiv)", "text", "Werk Dornhagen – Gerätemontage (fiktiv)"),
                 ("record", "mode", "RECORDED"), ("live", "mode", "LIVE"),
                 ("recorded", "approval", scr.APPROVAL_DISPLAY["recorded"]),
                 ("live", "approval", scr.APPROVAL_DISPLAY["live"])]
        for value, fmt, display in cases:
            with self.subTest(value=value, fmt=fmt):
                self.assertEqual(scr.format_value(value, fmt), display)
                self.assertEqual(scr.parse_display(display, fmt), scr.comparable(value, fmt))
        refused = [(True, "int"), (1.0, "int"), ("1", "int"), (0, "rank"), (True, "rank"), (1.5, "pct"),
                   ("zz" * 16, "digest12"), ("ab" * 5, "digest12"), ("x", "yesno"), ("other", "mode"),
                   ("2024-10-28", "datetime"), (float("nan"), "dec2"), (1, "text"), (1, "unknown")]
        for value, fmt in refused:
            with self.subTest(refused=(value, fmt)):
                with self.assertRaises(scr.ScreenError):
                    scr.format_value(value, fmt)
        for display, fmt in (("1234", "int"), ("1,23", "int"), ("26", "pct"), ("2.5", "dec2"), ("01", "rank"),
                             ("ABCDEF123456", "digest12"), ("maybe", "yesno"), ("Recorded", "mode")):
            with self.assertRaises(scr.ScreenError):
                scr.parse_display(display, fmt)


# --------------------------------------------------------------------------------------------------- honesty

def _both(raw: dict[str, Any]) -> None:
    pack = scn.load_scenario().pack
    hero = raw["items"][0]
    specific = sorted(c for c, v in pack.codes.items() if v.predicate == hero["key"]["predicate"] and v.specific)
    hero["visibility"] = "both"
    hero["codes"] = specific[:1]
    for field in hero["structured"]:
        if field["entity_type"] == hero["key"]["entity_type"]:
            field["fill_rate"] = 1.0


def _short_hero(raw: dict[str, Any]) -> None:
    raw["items"][0]["weeks"] = 2


def _quiet_hero(raw: dict[str, Any]) -> None:
    hero = raw["items"][0]
    hero["weeks"] = 1
    for site in hero["sites"]:
        site["rate_per_week"] = 1


class HonestyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="collective-honesty-"))
        cls.variants = {}
        for name, change in (("both", _both), ("short", _short_hero), ("quiet", _quiet_hero)):
            path = write_variant(cls.tmp / name, change)
            out = cls.tmp / name / "run"
            r = run_demo("--record", str(out), "--scenario", str(path), "--run-id", f"variant-{name}")
            page = cls.tmp / name / "page.html"
            exported = run_demo("--export", str(page), "--run", str(out)) if (out / "scorecard.json").exists() \
                else None
            cls.variants[name] = {"result": r, "out": out, "page": page, "export": exported}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def committed(self) -> dict[str, Any]:
        return {k: v for k, v in load(committed_dir()).items() if k in runfiles.PRIMARY_FILES}

    def test_screen_sentences_follow_flags(self) -> None:
        base = self.committed()
        for r_caught in (False, True):
            for s_caught in (False, True):
                for related in (False, True):
                    for by_construction in (False, True):
                        docs = copy.deepcopy(base)
                        det = docs["scorecard.json"]["hero"]["detection"]
                        det["R_mf"]["caught"], det["S"]["caught"] = r_caught, s_caught
                        det["by_construction"]["S"] = by_construction
                        if not related:
                            det["R_mf"]["related"], det["S"]["related"] = [], []
                        screen = scr.build_screen(docs, mode="record", phase="complete")
                        texts = " ".join(screen_texts(screen))
                        with self.subTest(r=r_caught, s=s_caught, related=related, by_construction=by_construction):
                            self.assertEqual(scr.R_ALSO in texts, r_caught)
                            self.assertEqual(scr.S_ALSO in texts, s_caught)
                            self.assertEqual(scr.BY_CONSTRUCTION in texts, by_construction)
                            for sentence, channel in ((scr.R_RELATED, "R_mf"), (scr.S_RELATED, "S")):
                                blocks = blocks_with_text(screen, sentence)
                                self.assertEqual(len(blocks), len(det[channel]["related"]))
                                self.assertEqual(bool(blocks), related and bool(base["scorecard.json"]["hero"][
                                    "detection"][channel]["related"]))

    def test_related_keys_are_shown_with_their_own_items(self) -> None:
        docs = load(committed_dir())
        det = docs["scorecard.json"]["hero"]["detection"]
        screen = docs["screen.json"]
        items = {i["id"]: i for i in screen["items"]}
        for sentence, channel, prefix in ((scr.R_RELATED, "R_mf", "r"), (scr.S_RELATED, "S", "s")):
            self.assertTrue(det[channel]["related"])
            blocks = blocks_with_text(screen, sentence)
            self.assertEqual(len(blocks), len(det[channel]["related"]))
            for j, rel in enumerate(det[channel]["related"]):
                part_items = {p["item"] for p in blocks[j]["parts"] if p["item"] is not None}
                self.assertIn(f"{prefix}_related_{j}_rank", part_items)
                self.assertEqual(items[f"{prefix}_related_{j}_rank"]["display"], str(rel["rank"]))
                self.assertEqual(items[f"{prefix}_related_{j}_week"]["display"], rel["week"])
                entity = rel["key"].split(":")[1]
                self.assertEqual(items[f"{prefix}_related_{j}_id"]["display"], entity)
        texts = " ".join(screen_texts(screen)).lower()
        self.assertNotIn("miss", texts)
        self.assertIn(scr.BY_CONSTRUCTION, " ".join(screen_texts(screen)))

    def test_visibility_both_variant_catches(self) -> None:
        v = self.variants["both"]
        self.assertEqual(v["result"].returncode, 0, v["result"].stdout + v["result"].stderr)
        docs = load(v["out"])
        det = docs["scorecard.json"]["hero"]["detection"]
        self.assertEqual((det["R_mf"]["caught"], det["S"]["caught"], det["r_caught"], det["s_caught"]),
                         (True, True, True, True))
        self.assertEqual(docs["scorecard.json"]["scenario"]["visibility"], "both")
        texts = " ".join(screen_texts(docs["screen.json"]))
        self.assertIn(scr.R_ALSO, texts)
        self.assertIn(scr.S_ALSO, texts)
        self.assertNotIn(scr.BY_CONSTRUCTION, texts)
        self.assertEqual(v["export"].returncode, 0, v["export"].stderr)
        page = v["page"].read_text(encoding="utf-8")
        for sentence in ("The restricted central baseline also caught this case",
                         "The codes-only baseline also caught this case"):
            self.assertIn(sentence, page)
        self.assertNotIn(scr.BY_CONSTRUCTION, page)
        r = run_lint(str(v["out"]))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_not_supported_variant_is_shown(self) -> None:
        v = self.variants["short"]
        self.assertEqual(v["result"].returncode, 1, v["result"].stdout + v["result"].stderr)
        self.assertEqual(sorted(p.name for p in v["out"].iterdir()), sorted(runfiles.RUN_FILES))
        docs = load(v["out"])
        sc, screen = docs["scorecard.json"], docs["screen.json"]
        self.assertEqual(demo.validate_run(docs), [])
        status = sc["hero"]["pushdown"]["gate"]["status"]
        self.assertNotEqual(status, "supported")
        items = {i["id"]: i for i in screen["items"]}
        gate_items = [i for i in screen["items"] if i["src"] == "scorecard.json#/hero/pushdown/gate/status"]
        self.assertTrue(gate_items)
        self.assertTrue(all(i["display"] == status for i in gate_items))
        self.assertIn(scr.NO_FOLLOWUP, screen_texts(screen))
        fu = sc["hero"]["followup"]
        self.assertEqual((fu["proposed"], fu["packet"], fu["draft"], fu["ledger_head"]), (False, None, None, None))
        failed = [c["id"] for c in sc["checks"] if not c["ok"]]
        self.assertIn("gate_supported", failed)
        warning = [b for b in screen["blocks"] if b["id"] == "footer-failed"]
        self.assertEqual(len(warning), 1)
        shown = {items[p["item"]]["display"] for p in warning[0]["parts"] if p["item"] is not None}
        self.assertEqual(shown, set(failed))
        r = run_lint(str(v["out"]))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_x_not_alerting_variant_is_shown(self) -> None:
        v = self.variants["quiet"]
        self.assertEqual(v["result"].returncode, 1, v["result"].stdout + v["result"].stderr)
        docs = load(v["out"])
        sc, screen = docs["scorecard.json"], docs["screen.json"]
        self.assertEqual(demo.validate_run(docs), [])
        self.assertEqual((sc["hero"]["detection"]["X"]["rank"], sc["hero"]["detection"]["X"]["caught"]), (None, False))
        self.assertIsNone(sc["hero"]["pushdown"])
        self.assertEqual((sc["hero"]["followup"]["proposed"], sc["hero"]["followup"]["reason"]), (False, "not_alerted"))
        texts = screen_texts(screen)
        self.assertIn(scr.NOT_CHECKED, texts)
        self.assertIn(scr.NO_FOLLOWUP, texts)
        self.assertEqual({i["id"]: i for i in screen["items"]}["x_rank"]["display"], scr.NOT_ALERTED)
        self.assertIn("hero_alerted_by_x", [c["id"] for c in sc["checks"] if not c["ok"]])
        self.assertEqual(run_lint(str(v["out"])).returncode, 0)

    def test_labels_present(self) -> None:
        screen = load(committed_dir())["screen.json"]
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page.html"
            r = run_demo("--export", str(page_path))
            self.assertEqual(r.returncode, 0, r.stderr)
            page = page_path.read_text(encoding="utf-8")
        text = json.dumps(screen, ensure_ascii=False)
        for label in ("Fictional company", "synthetic data", "RECORDED", scr.R_DEFINITION, "Internal and YC use only",
                      STUB):
            with self.subTest(label=label):
                self.assertIn(label, text)
                self.assertIn(label, page)
        self.assertEqual(scr.R_DEFINITION, "R: the same detectors over the fields allowed to leave, record-level, "
                                           "no model")
        self.assertNotIn("LIVE", [i["display"] for i in screen["items"]])
        for sentence, beat in ((scr.X4_CAPTION, "followup"), (scr.REAL_DATA, "real_data")):
            blocks = blocks_with_text(screen, sentence)
            self.assertTrue(blocks, sentence)
            self.assertEqual({b["beat"] for b in blocks}, {beat})
            self.assertIn(sentence, page)
        self.assertEqual(scr.X4_CAPTION, "approval-routed follow-up — not measured (X4)")
        self.assertEqual(scr.REAL_DATA, "Real-data result: not yet measured (Phase-1 audit / public replay pending)")

    def test_committed_scenario_shape(self) -> None:
        sc = scn.load_scenario()
        self.assertIn("(fictional)", sc.company)
        sites = sc.raw["org"]["sites"]
        self.assertGreaterEqual(len(sites), 5)
        self.assertGreaterEqual(len({s["country"] for s in sites}), 2)
        self.assertTrue(any(not s["display_name"].isascii() for s in sites))
        self.assertFalse(any(re.search(r"[0-9]", s["display_name"]) for s in sites))
        hero = sc.hero
        self.assertEqual(hero.visibility, "narrative_only")
        self.assertGreaterEqual(len(hero.sites), 3)
        self.assertGreaterEqual(len({language for _, language, _ in hero.sites}), 2)
        siblings = [i for i in sc.items if i.role == "sibling"]
        self.assertTrue(any(hero.key.entity_id in str(i.slots) or hero.key.entity_id in str(i.structured)
                            for i in siblings))
        self.assertGreaterEqual(len([i for i in sc.items if i.role == "decoy"]), 3)
        docs = load(committed_dir())
        fills = docs["scorecard.json"]["scenario"]["structured_fill"]
        self.assertTrue(fills)
        items = {i["id"]: i for i in docs["screen.json"]["items"]}
        for j, fill in enumerate(fills):
            self.assertEqual(items[f"fill_{j}_filled"]["display"], f"{fill['filled']:,}")
            self.assertEqual(items[f"fill_{j}_records"]["display"], f"{fill['records']:,}")
            self.assertEqual(items[f"fill_{j}_label"]["display"], fill["entity_type_label"])

    def test_scenario_validation_errors(self) -> None:
        raw = json.loads((DEMO_DIR / "scenario.json").read_text(encoding="utf-8"))
        pack = scn.load_scenario().pack
        hero_predicate = raw["items"][0]["key"]["predicate"]
        mapped = sorted(c for c, v in pack.codes.items() if v.predicate == hero_predicate)[0]

        def unknown(d):
            d["surplus_key"] = 1

        def company(d):
            d["company"] = "Halvern Medical Plc"

        def coded(d):
            d["items"][0]["codes"] = [mapped]

        def slot(d):
            d["items"][0]["narratives"]["en"] = ["On units from lot {undefined_slot} the door came loose."]

        for name, change, secret in (("unknown", unknown, "surplus_key"), ("company", company, "Halvern Medical Plc"),
                                     ("coded", coded, mapped), ("slot", slot, "undefined_slot")):
            with self.subTest(case=name):
                d = copy.deepcopy(raw)
                change(d)
                with self.assertRaises(scn.ScenarioError) as cm:
                    scn.parse_scenario(d, digest="0" * 64)
                message = str(cm.exception)
                self.assertTrue(message.startswith("scenario: $"), message)
                if name != "unknown":
                    self.assertNotIn(secret, message)
                self.assertNotIn("\n", message)


# --------------------------------------------------------------------------------------------------- determinism

class DeterminismTests(unittest.TestCase):
    def test_content_hash_equal_across_hashseeds_workdirs_and_committed(self) -> None:
        hashes = []
        for directory in (FIXTURE["0"]["out"], FIXTURE["4242"]["out"], committed_dir()):
            sc = json.loads((directory / "scorecard.json").read_text(encoding="utf-8"))
            self.assertEqual(runfiles.content_hash(sc, sc["content_hash_excludes"]), sc["content_hash"])
            self.assertEqual(sc["content_hash_excludes"], list(demo.CONTENT_HASH_EXCLUDES))
            hashes.append(sc["content_hash"])
        self.assertEqual(FIXTURE["4242"]["result"].returncode, 0, FIXTURE["4242"]["result"].stderr)
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(hashes[0], hashes[2], "the committed run is stale: re-record it (demo/collective/README.md)")
        a, b = (load(FIXTURE[s]["out"]) for s in ("0", "4242"))
        for name in ("ledger.jsonl", "approvals.jsonl"):
            self.assertEqual(a[name], b[name])

    def test_build_world_is_hashseed_independent(self) -> None:
        code = ("import hashlib, json\nfrom demo.collective.scenario import load_scenario, build_world\n"
                "from mycelic.collective.jsonio import canonical_bytes\n"
                "w = build_world(load_scenario())\n"
                "print(json.dumps([hashlib.sha256(canonical_bytes(list(w.records))).hexdigest(), list(w.case_keys), "
                "sorted(c.id for c in w.manifest.canaries), list(w.weeks)]))\n")
        outs = []
        for seed in ("1", "977"):
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(json.loads(r.stdout))
        self.assertEqual(outs[0], outs[1])
        world = scn.build_world(scn.load_scenario())
        self.assertEqual(sha256_hex(canonical_bytes(list(world.records))), outs[0][0])


# --------------------------------------------------------------------------------------------------- export, replay

class ExportReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="collective-export-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def popen(self, *args: str) -> subprocess.Popen[str]:
        proc = subprocess.Popen([sys.executable, SCRIPT, *args], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})

        def stop() -> None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
        self.addCleanup(stop)
        return proc

    def routing_file(self, base_url: str, *, provider: str = "openai_compat", boundary: str = "any-simulated",
                     draft: bool = False) -> Path:
        spec: dict[str, Any] = {"provider": provider, "boundary": boundary}
        if provider == "openai_compat":
            spec.update(base_url=base_url, model="m-tag")
        routes = {TASK_NAME: {"endpoint": "sim"}, JUDGE_TASK: {"endpoint": "sim"}}
        if draft:
            routes[DRAFT_TASK] = {"endpoint": "sim"}
        path = self.tmp / f"routing-{provider}-{boundary.replace(':', '-')}.json"
        path.write_text(json.dumps({"schema_version": 1, "endpoints": {"sim": spec}, "routes": routes}),
                        encoding="utf-8")
        return path

    def test_export_page(self) -> None:
        page_path = self.tmp / "page.html"
        r = run_demo("--export", str(page_path))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = page_path.read_bytes()
        self.assertLess(len(data), 2 * 1024 * 1024)
        page = data.decode("utf-8")
        self.assertTrue(page.startswith("<!doctype html>"))
        head = page.split("</head>", 1)[0]
        for needle in ('<meta charset="utf-8">', '<meta name="viewport"', "<title>", "<style>"):
            self.assertIn(needle, head)
        m = re.search(r'<script id="collective-screen" type="application/json">(.*?)</script>', page, re.DOTALL)
        self.assertIsNotNone(m)
        self.assertNotIn("<", m.group(1))
        self.assertEqual(json.loads(m.group(1)), load(committed_dir())["screen.json"])
        self.assertEqual(secret_findings(page), [])
        console = (DEMO_DIR / "console.html").read_text(encoding="utf-8")
        for text in (page, console):
            self.assertIsNone(re.search(r"https?://|@import|url\(\s*['\"]?http", text, re.IGNORECASE))
        style = re.search(r"<style>(.*?)</style>", console, re.DOTALL).group(1)
        for prop, value in re.findall(r"(?<![\w-])(width|min-width|max-width|flex-basis)\s*:\s*([0-9.]+)px", style):
            self.assertLessEqual(float(value), 1280, prop)

    def test_secret_and_seeded_secret_absence(self) -> None:
        directory = committed_dir()
        sc = scn.load_scenario()
        secrets = seeded_secrets(sc.seed, [s["site_id"] for s in sc.raw["org"]["sites"]])
        page_path = self.tmp / "page.html"
        self.assertEqual(run_demo("--export", str(page_path)).returncode, 0)
        for path in [directory / n for n in runfiles.RUN_FILES] + [page_path]:
            with self.subTest(file=path.name):
                data = path.read_bytes()
                self.assertEqual(secret_findings(data.decode("utf-8")), [])
                for secret in secrets:
                    self.assertNotIn(secret, data)
                    self.assertNotIn(secret[:16], data)

    def test_replay_server(self) -> None:
        port = free_port()
        proc = self.popen("--replay", str(committed_dir()), "--port", str(port), "--no-browser", "--exit-after", "60")
        docs = load(committed_dir())
        wait_until(lambda: get_json(port, "/screen"), what="the replay server")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            self.assertEqual(r.status, 200)
            page = r.read().decode("utf-8")
        self.assertIn('id="collective-screen"', page)
        self.assertEqual(get_json(port, "/screen"), docs["screen.json"])
        self.assertEqual(get_json(port, "/trace"), docs["trace.json"])
        n = len(docs["trace.json"]["events"])
        snapshot, ids = read_events(port, last_event_id=None, want=3)
        self.assertTrue(snapshot)
        self.assertEqual(ids, [0, 1, 2])
        snapshot, ids = read_events(port, last_event_id="9999", want=2)
        self.assertEqual(ids, [0, 1])
        snapshot, ids = read_events(port, last_event_id=str(n - 3), want=2)
        self.assertEqual(ids, [n - 2, n - 1])
        self.assertEqual(post_control(port, {"action": "next"})[0], 409)
        second = run_demo("--replay", str(committed_dir()), "--port", str(port), "--no-browser", "--exit-after", "5",
                          timeout=60)
        self.assertEqual(second.returncode, 2)
        lines = second.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, second.stderr)
        self.assertIn(f"port {port}", lines[0])
        self.assertIn("--port", lines[0])
        self.assertIsNone(proc.poll())

    def test_serve_controls_are_idempotent(self) -> None:
        port, out = free_port(), self.tmp / "live"
        proc = self.popen("--serve", "--port", str(port), "--no-browser", "--out", str(out), "--exit-after", "240",
                          "--run-id", "live-test")

        def beat(s: dict[str, Any]) -> str | None:
            return next((c["beat"] for c in s["controls"] if c["action"] == "next"), None)

        def at(name: str, extra=lambda s: True):
            def check() -> Any:
                s = get_json(port, "/screen")
                return s if s["phase"] == "ready" and beat(s) == name and extra(s) else None
            return check

        s = wait_until(at("problem"), what="phase ready")
        self.assertEqual(post_control(port, {"action": "check"})[0], 409)
        self.assertEqual(post_control(port, None, raw=b"not json")[0], 400)
        self.assertEqual(post_control(port, {"action": "dance"})[0], 400)
        self.assertEqual(post_control(port, {"action": "next"})[0], 200)
        wait_until(at("alert"), what="the alert beat")
        self.assertEqual(post_control(port, {"action": "next"})[0], 200)
        wait_until(at("check"), what="the check beat")
        results: list[tuple[int, Any]] = []
        threads = [threading.Thread(target=lambda: results.append(post_control(port, {"action": "check"})))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(code for code, _ in results), [200, 200])
        self.assertEqual(sum(1 for _, body in results if body.get("queued") == "check"), 1, results)
        wait_until(at("check", lambda s: any(c["action"] == "next" and c["enabled"] for c in s["controls"])),
                   what="the check to finish")
        self.assertEqual(post_control(port, {"action": "check"}), (200, {"ok": True, "done_before": True}))
        self.assertEqual(post_control(port, {"action": "next"})[0], 200)
        s = wait_until(at("followup"), what="the follow-up beat")
        for _ in range(2):
            keys = [c["key"] for c in s["controls"] if c["action"] == "approve"]
            self.assertEqual(len(keys), 1)
            pair = []
            threads = [threading.Thread(target=lambda: pair.append(post_control(port, {"action": "approve",
                                                                                        "key": keys[0]})))
                       for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sum(1 for _, body in pair if body.get("queued") == "approve"), 1, pair)
            s = wait_until(at("followup", lambda s, k=keys[0]: all(c["key"] != k for c in s["controls"])),
                           what="the approval to run")
            self.assertEqual(post_control(port, {"action": "approve", "key": keys[0]}),
                             (200, {"ok": True, "done_before": True}))
        self.assertEqual(post_control(port, {"action": "approve", "key": "act:none"})[0], 409)
        self.assertEqual(post_control(port, {"action": "next"})[0], 200)
        wait_until(at("real_data"), what="the real-data beat")
        self.assertEqual(post_control(port, {"action": "next"})[0], 200)
        s = wait_until(lambda: (lambda x: x if x["phase"] == "complete" else None)(get_json(port, "/screen")),
                       what="completion")
        self.assertIn("LIVE", [i["display"] for i in s["items"]])
        self.assertEqual(post_control(port, {"action": "next"})[0], 409)
        proc.terminate()
        proc.communicate(timeout=30)
        docs = load(out)
        self.assertEqual(demo.validate_run(docs), [])
        sc, trace = docs["scorecard.json"], docs["trace.json"]
        self.assertEqual((sc["mode"], sc["stamps"]["approval"], trace["meta"]["approval"]), ("live", "live", "live"))
        self.assertEqual(docs["screen.json"]["mode"], "live")
        hero_key = sc["hero"]["key"]["key"]
        events = trace["events"]
        questions = [e for e in events if e["type"] == "question" and e["data"]["key"] == hero_key]
        self.assertEqual(len(questions), 1)
        qid = questions[0]["data"]["question_id"]
        verdicts = [e["data"]["site"] for e in events if e["type"] == "verdict" and e["data"]["question_id"] == qid]
        self.assertEqual(sorted(verdicts), sorted(r["site"] for r in questions[0]["data"]["routes"]))
        approvals = [e["data"]["key"] for e in events if e["type"] == "approval"]
        self.assertEqual(len(approvals), 2)
        self.assertEqual(len(set(approvals)), 2)
        executions = [e["data"] for e in events if e["type"] == "execution"]
        self.assertEqual(sorted(e["key"] for e in executions), sorted(approvals))
        self.assertEqual({e["executor_calls"] for e in executions}, {1})
        self.assertTrue(all(c["ok"] for c in sc["checks"]), [c for c in sc["checks"] if not c["ok"]])
        r = run_lint(str(out))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_routing_unreachable_endpoint_fails_visibly(self) -> None:
        port = free_port()
        routing = self.routing_file(f"http://127.0.0.1:{port}/v1")
        out = self.tmp / "unreachable"
        t0 = time.monotonic()
        r = run_demo("--record", str(out), "--routing", str(routing), timeout=120)
        self.assertLess(time.monotonic() - t0, 60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("sim", r.stderr)
        self.assertIn("--replay", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(out.exists() and any(out.iterdir()))

    def test_routing_schema_invalid_fails_visibly(self) -> None:
        srv = FakeOpenAIServer("always-invalid").start()
        self.addCleanup(srv.stop)
        out = self.tmp / "invalid"
        r = run_demo("--record", str(out), "--routing", str(self.routing_file(srv.base_url)), timeout=300)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("endpoint sim", r.stderr)
        self.assertIn("the demo never falls back silently", r.stderr)
        self.assertIn("--replay", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertLessEqual(len(r.stderr.strip().splitlines()), 2, r.stderr)
        self.assertFalse(out.exists() and any(out.iterdir()))

    def test_routing_fake_server_end_to_end(self) -> None:
        pack = scn.load_scenario().pack
        canonicaliser = Canonicaliser(pack)
        extract, judge, draft = lexical_handler(pack, canonicaliser), lexical_judge(pack, canonicaliser), \
            template_draft(pack)

        def respond(request: Any) -> Any:
            payload = request_payload(request)
            if "followup_type" in payload:
                return draft(payload)
            return judge(payload) if "question" in payload else extract(payload)

        srv = FakeOpenAIServer("valid", responder=respond).start()
        self.addCleanup(srv.stop)
        out = self.tmp / "routed"
        r = run_demo("--record", str(out), "--routing", str(self.routing_file(srv.base_url, draft=True)),
                     "--run-id", "routed-test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        docs = load(out)
        sc = docs["scorecard.json"]
        labels = {p["task"]: p["label"] for p in sc["providers"]}
        self.assertEqual((labels["extract"], labels["judge"], labels["draft"]), (demo.TEST_SERVER_LABEL,) * 3)
        self.assertTrue(sc["stamps"]["shared_model"])
        self.assertFalse(sc["stamps"]["measurement"])
        self.assertEqual(docs["trace.json"]["meta"]["sites_note"], "sites simulated in one process, one shared model")
        self.assertIn("sites simulated in one process, one shared model",
                      [i["display"] for i in docs["screen.json"]["items"]])
        self.assertGreater(len(srv.chat_requests), 0)
        self.assertEqual(run_lint(str(out)).returncode, 0)
        for provider, boundary in (("openai_compat", "site:plant-ashvale"), ("fake", "any-simulated")):
            with self.subTest(provider=provider, boundary=boundary):
                target = self.tmp / f"refused-{provider}"
                r = run_demo("--record", str(target), "--routing",
                             str(self.routing_file(srv.base_url, provider=provider, boundary=boundary)), timeout=120)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertFalse(target.exists())

    def test_old_schema_one_line_errors(self) -> None:
        old = self.tmp / "old"
        shutil.copytree(committed_dir(), old)
        for name in ("scorecard.json", "trace.json", "leakage.json", "screen.json"):
            doc = json.loads((old / name).read_text(encoding="utf-8"))
            doc["schema_version"] = 2
            (old / name).write_text(json.dumps(doc), encoding="utf-8")
        for argv in (("--replay", str(old), "--port", str(free_port()), "--no-browser", "--exit-after", "5"),
                     ("--export", str(self.tmp / "page.html"), "--run", str(old))):
            with self.subTest(mode=argv[0]):
                r = run_demo(*argv, timeout=60)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                lines = r.stderr.strip().splitlines()
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertTrue(lines[0].startswith("error: "))
                self.assertIn("schema version 2", lines[0])
                self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertFalse((self.tmp / "page.html").exists())
        r = run_lint(str(old))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_usage_errors_write_nothing(self) -> None:
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("x", encoding="utf-8")
        bad_scenario = write_variant(self.tmp / "bad", lambda d: d.update(surplus_key=1))
        cases = [("--record", str(busy)), ("--record", str(self.tmp / "a"), "--run-id", "bad id!"),
                 ("--record", str(self.tmp / "b"), "--scenario", str(bad_scenario)),
                 ("--record", str(self.tmp / "c"), "--scenario", str(self.tmp / "missing.json")),
                 ("--record", str(self.tmp / "d"), "--routing", str(self.tmp / "missing.json")),
                 ("--replay", str(self.tmp / "nowhere"), "--port", str(free_port()), "--no-browser"),
                 ("--export", str(self.tmp / "e.html"), "--run", str(self.tmp / "nowhere"))]
        for argv in cases:
            with self.subTest(argv=argv):
                before = sorted(p.name for p in self.tmp.iterdir())
                r = run_demo(*argv, timeout=60)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertTrue(r.stderr.startswith("error: "), r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)
        self.assertEqual(sorted(p.name for p in busy.iterdir()), ["keep.txt"])
        r = run_demo("--record", str(self.tmp / "f"), "--scenario", str(bad_scenario))
        self.assertEqual(r.stderr.strip(), "error: scenario: $: unknown key")

    def test_an_interrupted_record_writes_nothing(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                out, work = self.tmp / f"int-{sig.name}", self.tmp / f"work-{sig.name}"
                proc = self.popen("--record", str(out), "--workdir", str(work))
                wait_until(lambda: work.exists() and any(work.rglob("*.sqlite3")), what="the work directory")
                proc.send_signal(sig)
                _, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 130, err)
                self.assertFalse(out.exists() and any(out.iterdir()))
                self.assertFalse(work.exists())

    def test_dry_run_creates_nothing(self) -> None:
        before = sorted(str(p) for p in self.tmp.rglob("*"))
        for argv in (("--record", str(self.tmp / "dry")), ("--serve", "--out", str(self.tmp / "dry")),
                     ("--replay", str(committed_dir())), ("--export", str(self.tmp / "dry.html")),
                     ("--record", str(self.tmp / "dry"), "--routing", str(self.tmp / "missing.json"))):
            with self.subTest(argv=argv):
                r = run_demo(*argv, "--dry-run", timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(r.stdout.startswith("dry-run: "), r.stdout)
        self.assertEqual(sorted(str(p) for p in self.tmp.rglob("*")), before)


# --------------------------------------------------------------------------------------------------- committed run

class CommittedRunTests(_HeroAssertions, unittest.TestCase):
    def test_exactly_one_committed_run_with_six_small_files(self) -> None:
        dirs = [p for p in RECORDED.iterdir() if p.name != "__pycache__"]
        self.assertEqual(len(dirs), 1)
        directory = dirs[0]
        self.assertEqual(sorted(p.name for p in directory.iterdir()), sorted(runfiles.RUN_FILES))
        sizes = [(directory / n).stat().st_size for n in runfiles.RUN_FILES]
        self.assertTrue(all(s < 512 * 1024 for s in sizes))
        self.assertLess(sum(sizes), 1024 * 1024)
        docs = load(directory)
        self.assertEqual(demo.validate_run(docs), [])
        self.assertEqual(docs["scorecard.json"]["run_id"], directory.name)

    def test_lint_and_leakage_rescan(self) -> None:
        directory = committed_dir()
        self.assertEqual(run_lint(str(directory)).returncode, 0)
        sc = scn.load_scenario()
        world = scn.build_world(sc)
        artifacts = [Artifact("run_files", name, path=directory / name) for name in runfiles.RUN_FILES]
        report = scan(artifacts, world.manifest, world.narratives, sc.pack)
        self.assertEqual((report["hits"], report["shingle_overlap_bytes"], report["known_limitation"]), ([], 0, []))
        hero_ids = set(world.item_records[sc.hero.id])
        hero_text = next(r["narrative"] for r in world.records if r["record_ref"] in hero_ids)
        probe = [Artifact("run_files", "probe", data=json.dumps({"x": hero_text}, ensure_ascii=False).encode())]
        self.assertGreater(scan(probe, world.manifest, world.narratives, sc.pack)["shingle_overlap_bytes"], 0)

    def test_portability_and_stamps(self) -> None:
        directory = committed_dir()
        forbidden = [str(ROOT), str(Path.home()), socket.gethostname(), getpass.getuser()]
        for name in runfiles.RUN_FILES:
            data = (directory / name).read_bytes()
            self.assertEqual(runfiles.portability_problems(data, forbidden=forbidden), [], name)
            self.assertIsNone(re.search(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", data), name)
        docs = load(directory)
        sc = docs["scorecard.json"]
        self.assertFalse(sc["stamps"]["measurement"])
        self.assertTrue(all(sc["stamps"][k] for k in ("fictional", "synthetic", "internal_only", "illustration")))
        self.assertEqual((sc["mode"], sc["stamps"]["approval"]), ("record", "recorded"))
        self.assertTrue(all(p["fake"] and p["label"] == STUB for p in sc["providers"]))
        self.assertTrue(all(c["ok"] for c in sc["checks"]))
        self.assert_detection(docs)
        self.assert_pushdown(docs)
        self.assert_followup(docs, approval="recorded", requests=2)
        self.assert_no_baseline_numbers(docs)


if __name__ == "__main__":
    unittest.main()
