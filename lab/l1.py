"""Latency test L001 (lab experiment ``l1``): how long an alert takes to answer on CPU, through the product's own
pushdown path, and whether the answer is right.

    python -m lab.l1 prereg --plan PLAN [--raw DIR]
    python -m lab.l1 run --prereg F --raw DIR --routing-dir D --endpoint KEY --slot K --run-id ID --runs-dir D
        --budget-seconds N

``docs/collective/L001/CHOICE-L001.md`` is the rule, with its section "Amended before any run" (K1 to K13), and
``docs/collective/L001/BUILD-L001.md`` the build note. :mod:`lab.l1path` builds the data and runs one judge on one
question, :mod:`lab.l1score` scores, :mod:`lab.l1guard` scans what a run uploads (the workflow runs it, and gets the
file's cache key, through :mod:`lab.msha`). This module pins, preregisters and runs a unit. Nothing here changes a
pinned module.

**The file.** ``DIR`` holds MSHA's ``Accidents.zip`` as ``tools/onboard/fetch_msha.py download`` writes it; when it is
absent it is downloaded there (the workflow restores it from the cache the plan job saved first, K10). By default
``DIR`` is ``lab-msha`` beside the plan's directory (:func:`raw_dir`), which the workflow restores to.

**prereg** (the plan job, in a subprocess whose output ``lab.prereg`` captures, K9), in K's order: the file and its
sha256, beside the drafter demo's recorded input (``demo/onboard/recorded/record-001-demo.json``); the data
(:func:`lab.l1path.prepare`); when the file is the demo's, the demo's figures (records, mines, weeks evaluated, one X
alert, no S alert), else ``demo_figures``; exactly one X or S alert in the evaluated weeks (``no_alert``, ``alerts``)
and exactly four other specific predicates (``questions``, K11); per question a probe path (its id, window and routes),
the retrieval check (``retrieval``: the hidden store retrieves what the unchanged store does) and the strata (K2); the
key judge, the lexical judge, the record-blind control and the route-role baseline, each on its own fresh stores, each
giving the probe's id and routes (``routes``, K1, K4); their scores, by stratum, the construction counts, the
predicate-only bound, the gate's agreement and the constant status (K8); the share of empty draws (``draws``, K7); and
the warm-up payloads (K5). It writes ``prereg/l1/prereg.json`` (:data:`PREREG_KEYS`), ``scores.json`` and
``warmup.json``. A stop writes ``prereg-l1-stop.json`` beside the plan (``{"stop": <code>, "counts": {...}}``) and exits
2; a failed download exits 3. No model runs here.

**run** (one unit: one model and one question slot). Every check is made before the first model call, each a usage
error (exit 2) or, for the file, an infrastructure failure (exit 3: the file could not be restored, fetched or does not
have the plan's sha256; K10): the prereg's kind; the code hash; the judge task; the file's sha256; the drafted pack's
config hash, the alert, the audit's counts; the question's id and routes and every routed mine's retrieved records
(from a probe path); each mine's routing (``openai_compat`` at ``site:<mine>``, ``judge_record`` to the model with no
escalation, the preregistered pins). Then the timed path (:func:`lab.l1path.run_path`, judge ``model``, a ``Runtime``
per mine, ledger ``ledger-<mine>.jsonl``) on stores no other judge touched, after it the K4 unit check (calls equal
the preregistered retrieved records, fewer only for a degraded verdict, a breaker stop or a timed-out mine; a ledger
row for every call), the per-record replies, the counted-confirm shares and the crossing overlap, then the lexical
rerun on fresh stores, which must give the preregistered verdicts and status (rule 5). The budget: the timed path and
the late wait stop :data:`AFTER_PATH_S` before ``--budget-seconds`` (a timer, ``SIGALRM``, armed when the path starts
for the time the budget's clock says is left), so the rest and the final ``run.json`` are written in time; ``SIGTERM``
stops the run as an interrupt. Files under ``<runs dir>/l1/<run id>/``:
``run.json`` (``kind: lab_l1_run``, written with ``complete: false`` before the first call and again at the end),
``records.jsonl`` (per judged record: slot, mine, index, positive, the model's and the lexical judge's answers; no
record id, no narrative) and the ledgers. Exit 0 complete; 1 not finished (budget) or a check after the model ran
failed; 2 usage or pins; 3 infrastructure before any model call; 130 interrupted.

**Errors** (K9): every command catches every exception and prints its class name and code location only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.extract import BREAKER_AFTER, SERVER_DOWN_KINDS, truncate
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import JUDGE_TASK, judge_payload, judge_schema, judge_task
from mycelic.collective.experiments import e1_extract
from mycelic.collective.experiments.common import (ROOT, UsageError, check_run_id, code_commit, code_dirty, code_files,
                                                   code_hash as common_code_hash, measurement_flag, run_dir,
                                                   utc_clock, write_json_atomic)
from mycelic.collective.experiments.e3_latency import filler
from mycelic.collective.inference.client import list_models
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, key_problem, load_routing, parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load

from . import l1path as P
from . import l1score as SC
from .l1guard import error_line

CLI = "lab.l1"
KIND = "l1"
SCHEMA_VERSION = 1
DATA_LABEL = "public"
ENDPOINT_DEADLINE_S = 600
MAX_RETRIES = 1
AFTER_PATH_S = 240                  # the unit's time kept after the timed path: the lexical rerun, the files
STOP_FILE = "prereg-l1-stop.json"
RAW_DIRNAME = "lab-msha"
ZIP = "Accidents.zip"
STOPS = ("pack", "demo_figures", "no_alert", "alerts", "questions", "alert_run", "retrieval", "routes", "hidden",
         "draws")
CODE_FILES = ("lab/l1.py", "lab/l1path.py", "lab/l1score.py", "lab/l1guard.py", "demo/onboard/run_demo.py",
              "tools/onboard/fetch_msha.py", "mycelic/collective/onboard/*.py", "mycelic/collective/pilot/audit.py",
              "mycelic/collective/edge/*.py", "mycelic/collective/pushdown/*.py", "mycelic/collective/detect/*.py",
              "mycelic/collective/evaluate/baselines.py", "mycelic/collective/inference/*.py",
              "mycelic/collective/packs/*.py", "mycelic/collective/leakage.py", "mycelic/collective/stats.py",
              "mycelic/collective/jsonio.py", "mycelic/collective/schemacheck.py",
              "mycelic/collective/experiments/common.py", "mycelic/collective/experiments/e1_extract.py",
              "mycelic/collective/experiments/e3_latency.py", "mycelic/collective/experiments/openfda_replay.py",
              "docs/collective/onboard/D002-settings.json",
              "mycelic/collective/onboard/data/template/*.json")
PREREG_KEYS = ("schema_version", "kind", "experiment", "settings", "operator", "input", "pack", "audit",
               "demo_figures", "mines", "alert", "questions", "judges", "draws", "task", "code_hash", "code_files",
               "code_commit", "code_dirty", "endpoints", "data_label", "deadline_seconds", "endpoint_deadline_s",
               "max_retries", "demo_seed", "bootstrap_b", "bootstrap_seed", "withhold_share", "slots", "tie_salt",
               "enterprise")
RUN_KEYS = ("schema_version", "kind", "experiment", "run_id", "endpoint", "slot", "question_id", "prereg_sha256",
            "pinned", "complete", "finished", "stopped", "problem", "model_calls", "measurement", "models_listed",
            "listed_fake", "models_served", "checks", "timing", "derived", "time_to_final_s", "late_still_running",
            "verdicts", "mines", "lexical", "confirm_shares", "crossing", "budget_seconds", "boundary_prefix",
            "data_label", "code_commit", "started_at", "finished_at")
RECORD_KEYS = ("slot", "mine", "index", "positive", "model", "lexical")
EXIT_INFRA = 3
_clock = time.monotonic             # the budget's clock (tests replace it)


# --------------------------------------------------------------------------------------------------- pins

def raw_dir(plan_dir: Path) -> Path:
    """Where the file lives by default: ``lab-msha`` beside the plan's directory (the workflow's cache path)."""
    return Path(plan_dir).parent / RAW_DIRNAME


def endpoint(entry: Mapping[str, Any], base_url: str, mine: str) -> dict[str, Any]:
    """One mine's endpoint (rule 4): the model's alias and transport at ``site:<mine>``, deadline 600 s, 1 retry."""
    return {"provider": "openai_compat", "boundary": f"site:{mine}", "base_url": base_url, "model": entry["alias"],
            "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
            "connect_timeout_s": 5, "deadline_s": ENDPOINT_DEADLINE_S, "max_retries": MAX_RETRIES}


def routing_doc(models: Mapping[str, Mapping[str, Any]], key: str, base_url: str, mine: str) -> dict[str, Any]:
    """The only builder of a mine's L1 routing: the model's endpoint at ``site:<mine>``, ``judge_record`` routed to it
    with no escalation."""
    return {"schema_version": 1, "endpoints": {key: endpoint(models[key], base_url, mine)},
            "routes": {JUDGE_TASK: {"endpoint": key}}}


def endpoint_pins(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """Each model's endpoint pins (``e1_extract.endpoint_pins``, the boundary left out: it is each mine's own),
    at the placeholder address."""
    out = []
    for key in sorted(keys):
        config = parse_routing(routing_doc(models, key, "http://127.0.0.1:9/v1", "m01"), check_env=False)
        pins = e1_extract.endpoint_pins(config.endpoints[key])
        pins.pop("boundary")
        out.append({"name": key, **pins, "deadline_s": ENDPOINT_DEADLINE_S, "max_retries": MAX_RETRIES})
    return out


def code_files_list() -> list[str]:
    return code_files(CODE_FILES, ROOT)


def code_hash() -> str:
    """sha256 over :data:`CODE_FILES`, built as the harnesses build theirs (``experiments.common.code_hash``)."""
    return common_code_hash([ROOT / p for p in code_files_list()])


def task_pins() -> dict[str, Any]:
    task = judge_task()
    return {"name": task.name, "data_class": task.data_class, "instructions_sha256": sha256_hex(task.instructions),
            "schema_sha256": sha256_hex(canonical_bytes(judge_schema())), "max_tokens": task.max_tokens}


def file_sha(path: Path) -> str | None:
    return sha256_hex(path.read_bytes()) if path.is_file() else None


def demo_input() -> dict[str, Any] | None:
    """The drafter demo's recorded input (``file``, ``bytes``, ``sha256``) and its audit figures, from its recorded
    file; None when it cannot be read."""
    try:
        doc = strict_load((ROOT / P.DEMO_RECORD).read_bytes())
        d = next(s for s in doc["steps"] if s["id"] == "d")["data"]
        return {"file": doc["input"]["file"], "bytes": doc["input"]["bytes"], "sha256": doc["input"]["sha256"],
                "figures": {"records": d["records"], "mines": d["mines"], "weeks": d["weeks_evaluated"],
                            "alerts": {"X": d["alerts"]["X"], "S": d["alerts"]["S"]}}}
    except (OSError, StrictJsonError, KeyError, TypeError, StopIteration):
        return None


def ensure_file(raw: Path, fetcher: Any = None) -> Path:
    """The zip in ``raw``, downloaded there (``fetch_msha.py download``) when absent; :class:`P.L1Error`
    (``fetch``) when it cannot be."""
    path = raw / ZIP
    if path.is_file():
        return path
    try:
        fetch = P.load_script(P.FETCH)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            fetch.download(raw, **({"fetcher": fetcher} if fetcher is not None else {}))
    except Exception:              # noqa: BLE001 (an infrastructure failure, K10)
        raise P.L1Error("fetch") from None
    if not path.is_file():
        raise P.L1Error("fetch")
    return path


# --------------------------------------------------------------------------------------------------- prereg

def question_counts(q: Mapping[str, Any]) -> dict[str, int]:
    """K1's counts for one preregistered question: routed mines by role and by stratum, the siblings with a record in
    the window, and the retrieved records summed over the routed mines."""
    strata, records = q["strata"], q["records"]
    return {"contributing": sum(1 for r in q["routes"].values() if r == "contributing"),
            "sibling": sum(1 for r in q["routes"].values() if r == "sibling"),
            **{s: sum(1 for v in strata.values() if v == s) for s in SC.STRATA},
            "siblings_with_records": sum(1 for m, v in strata.items() if v == "sibling" and records[m] > 0),
            "records": sum(records[m] for m in q["routes"])}


def _judge_entry(result: P.PathResult) -> dict[str, Any]:
    return {"verdicts": {m: v["verdict"] for m, v in sorted(result.first.items())},
            "reasons": {m: v["reason"] for m, v in sorted(result.first.items())},
            "support": {m: v["support_bucket"] for m, v in sorted(result.first.items())},
            "status": result.status_first, "status_final": result.status_final}


def _stop(plan_dir: Path, code: str, counts: Mapping[str, Any] | None = None) -> int:
    write_json_atomic(plan_dir / STOP_FILE, {"schema_version": 1, "kind": "lab_l1_stop", "stop": code,
                                             "counts": dict(counts or {})})
    print(f"l1 prereg: stopped ({code})", flush=True)
    return 2


def warmup_payloads(data: P.Data) -> dict[str, Any]:
    """K5: judge payloads built from no MSHA record and about no question a unit asks: a generated record (seeded
    filler, ``e3_latency.filler``) asked about the drafted pack's other bucket, which no question asks; the worst
    case is the filler repeated past ``max_input_chars`` and cut as the verifier cuts it."""
    text = filler(120, "l1:warmup")
    cap = data.pack.extraction.max_input_chars
    worst = truncate(" ".join([text] * (cap // max(1, len(text)) + 2)), cap)[0]
    entity_type = sorted(data.pack.egress.egress_entity_types)[0]
    entity_id = sorted(data.pack.entity_types[entity_type].ids)[0]
    question = {"params": {"entity_type": entity_type, "entity_id": entity_id, "predicate": data.other_predicate}}

    def payload(narrative: str) -> dict[str, Any]:
        w = WindowRecord(record_ref="warmup", iso_week="", root_ref="warmup", reporter_id=None,
                         language=data.language, codes=[], structured={entity_type: [entity_id]}, narrative=narrative)
        return judge_payload(data.pack, question, w)

    return {"schema_version": 1, "kind": "lab_l1_warmup", "predicate": data.other_predicate,
            "typical": payload(text), "worst": payload(worst)}


def preregister(plan_path: Path, raw: Path, work: Path, *, fetcher: Any = None) -> int:
    """The plan job's L1 step (see the module docstring): 0 written, 2 stopped, 3 the file could not be fetched."""
    plan = strict_load(plan_path.read_bytes())
    plan_dir = plan_path.parent
    units = [u for u in plan["units"] if u["experiment"] == KIND]
    p = units[0]["params"]
    try:
        zip_path = ensure_file(raw, fetcher)
    except P.L1Error:
        print("l1 prereg: the file could not be fetched", flush=True)
        return EXIT_INFRA
    sha = file_sha(zip_path)
    size = zip_path.stat().st_size
    demo = demo_input()
    is_demo = demo is not None and demo["sha256"] == sha and demo["bytes"] == size
    try:
        data = P.prepare(raw, work / "data")
    except P.L1Error as err:
        return _stop(plan_dir, err.code)
    counts = P.audit_counts(data.audit)
    if is_demo and counts != demo["figures"]:
        return _stop(plan_dir, "demo_figures", counts)
    found = P.alerts(data.audit)
    if not found:
        return _stop(plan_dir, "no_alert", counts)
    if len(found) != 1:
        return _stop(plan_dir, "alerts", counts)
    try:
        specs = P.question_specs(data, found[0])
    except P.L1Error as err:
        return _stop(plan_dir, err.code, {"specific_predicates": len(P.specific_predicates(data))})
    try:
        probe, _ = P.run_path(data, specs[0], "probe", work / "probe-1")
        specs = P.with_window(specs, probe.window)
        questions, judges = [], {j: {} for j in P.JUDGES}
        for spec in specs:
            slot = spec["slot"]
            pr, world = P.run_path(data, spec, "probe", work / f"probe-{slot}", keep_world=True)
            try:
                check = P.retrieval_check(data, world, spec, pr.window, pr.routes)
                st = P.strata(world.pipeline.store, spec, pr.window, pr.routes)
            finally:
                world.close()
            if not check["same"]:
                return _stop(plan_dir, "retrieval")
            questions.append({**spec, "question_id": pr.question_id, "window": pr.window, "routes": pr.routes,
                              "strata": st["strata"], "codes_with_text_only": st["codes_with_text_only"],
                              "records": check["records"]})
            for judge in P.JUDGES:
                result, _ = P.run_path(data, spec, judge, work / f"{judge}-{slot}", routes=pr.routes)
                if result.question_id != pr.question_id or result.routes != pr.routes:
                    return _stop(plan_dir, "routes")
                judges[judge][str(slot)] = _judge_entry(result)
    except P.L1Error as err:
        return _stop(plan_dir, err.code)
    rows = SC.site_answers(questions, {j: {s: e["verdicts"] for s, e in judges[j].items()} for j in P.JUDGES},
                           {s: e["verdicts"] for s, e in judges["key"].items()})
    b, bseed = p["bootstrap_b"], p["bootstrap_seed"]
    empty = SC.empty_draw_share(rows, b, SC.seed_of(bseed))
    scores, _ = SC.score_judges(rows, list(P.JUDGES), b=b, seed=SC.seed_of(bseed))
    draw_mines = sorted({r["mine"] for r in SC.scored(rows)})
    statuses = {j: {s: e["status"] for s, e in judges[j].items()} for j in P.JUDGES}
    doc = {
        "schema_version": SCHEMA_VERSION, "kind": "lab_l1_prereg", "experiment": "L1",
        "settings": {"path": P.SETTINGS, "sha256": data.settings_sha256}, "operator": P.COMPANY,
        "input": {"file": ZIP, "bytes": size, "sha256": sha,
                  "demo": {"bytes": demo["bytes"], "sha256": demo["sha256"]} if demo is not None else None,
                  "is_demo": is_demo},
        "pack": {"id": data.pack.id, "version": data.pack.version, **data.pack.hashes()},
        "audit": counts, "demo_figures": demo["figures"] if is_demo else None, "mines": list(data.site_ids),
        "alert": found[0], "questions": questions, "judges": judges, "draws": empty,
        "task": task_pins(), "code_hash": code_hash(), "code_files": code_files_list(), "code_commit": code_commit(),
        "code_dirty": code_dirty(["mycelic/collective", "lab", "demo/onboard", "tools/onboard"]),
        "endpoints": endpoint_pins(plan["models"], sorted({u["model"] for u in units})), "data_label": DATA_LABEL,
        "deadline_seconds": P.DEADLINE_S, "endpoint_deadline_s": ENDPOINT_DEADLINE_S, "max_retries": MAX_RETRIES,
        "demo_seed": P.DEMO_SEED, "bootstrap_b": b, "bootstrap_seed": bseed, "withhold_share": SC.WITHHOLD_SHARE,
        "slots": P.SLOTS, "tie_salt": P.TIE_SALT, "enterprise": P.ENTERPRISE}
    score_doc = {
        "schema_version": SCHEMA_VERSION, "kind": "lab_l1_scores", "draw_mines": draw_mines, "judges": scores,
        "strata": SC.by_stratum(rows, list(P.JUDGES), b=b, seed=SC.seed_of(bseed), mines=draw_mines),
        "construction": SC.construction(rows, {str(q["slot"]): q["records"] for q in questions},
                                        {str(q["slot"]): q["codes_with_text_only"] for q in questions}),
        "predicate_bound": SC.predicate_bound(rows),
        "gate": SC.gate_agreement({j: statuses[j] for j in P.JUDGES if j != "key"}, statuses["key"]),
        "draws": empty,
        "routes": {"questions": len(questions),
                   "contributing": sum(1 for q in questions for r in q["routes"].values() if r == "contributing"),
                   "sibling": sum(1 for q in questions for r in q["routes"].values() if r == "sibling"),
                   "by_stratum": {s: sum(1 for q in questions for v in q["strata"].values() if v == s)
                                  for s in SC.STRATA},
                   "siblings_with_records": sum(1 for q in questions for m, v in q["strata"].items()
                                                if v == "sibling" and q["records"][m] > 0),
                   "siblings_without_records": sum(1 for q in questions for m, v in q["strata"].items()
                                                   if v == "sibling" and q["records"][m] == 0),
                   "retrieved_records": {str(q["slot"]): sum(q["records"].values()) for q in questions}}}
    if empty["stops"]:
        return _stop(plan_dir, "draws", {"share": empty["share"], "draws": empty["draws"], "empty": empty["empty"]})
    out = plan_dir / "prereg" / KIND
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "prereg.json", doc)
    write_json_atomic(out / "scores.json", score_doc)
    write_json_atomic(out / "warmup.json", warmup_payloads(data))
    print(f"l1 prereg: questions {len(questions)} mines {len(data.site_ids)} file_is_demo "
          f"{str(is_demo).lower()}", flush=True)
    return 0


def read_prereg(path: str | Path) -> tuple[dict[str, Any], str]:
    try:
        data = Path(path).read_bytes()
        doc = strict_load(data)
    except (OSError, StrictJsonError):
        raise UsageError("the preregistration cannot be read") from None
    if not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS) or doc["kind"] != "lab_l1_prereg" \
            or doc["schema_version"] != SCHEMA_VERSION:
        raise UsageError("the preregistration is not an L1 prereg.json") from None
    return doc, sha256_hex(data)


# --------------------------------------------------------------------------------------------------- the run

class _Stop(BaseException):
    """Raised by :class:`_Signals` while the timed path runs; ``reason`` is ``budget`` or ``interrupted``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Signals:
    """The run's budget timer (``SIGALRM``), ``SIGTERM`` and ``SIGINT``, in the main thread. The handlers are installed
    at once; the timer is armed when the timed path starts (:meth:`enter`), for the seconds ``left()`` gives then, so
    the checks before the path count against the budget without the timer running while they do (with no time left,
    the path stops at once). A signal that arrives while the timed path runs (``in_path``) raises :class:`_Stop`
    there; elsewhere it is kept in ``pending``. :meth:`close` cancels the timer and restores the handlers."""

    SIGNALS = ("SIGALRM", "SIGTERM", "SIGINT")

    def __init__(self, left: Callable[[], float]) -> None:
        self.pending: str | None = None
        self.in_path = False
        self._left = left
        self._old: dict[int, Any] = {}
        self._armed = (threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer")
                       and hasattr(signal, "SIGALRM"))
        if self._armed:
            for name in self.SIGNALS:
                number = getattr(signal, name)
                self._old[number] = signal.signal(number, self._on_signal)

    def _on_signal(self, signum: int, frame: Any) -> None:
        if self.pending is None:
            self.pending = "budget" if signum == getattr(signal, "SIGALRM", None) else "interrupted"
        if self.in_path:
            self.in_path = False
            raise _Stop(self.pending)

    def enter(self) -> None:
        """The timed path starts: a signal already kept stops it at once; else the timer is armed for the seconds
        left, and none left stops it at once."""
        self.in_path = True
        if self.pending is None:
            left = float(self._left())
            if left > 0:
                if self._armed:
                    signal.setitimer(signal.ITIMER_REAL, left)
                return
            self.pending = "budget"
        self.in_path = False
        raise _Stop(self.pending)

    def answered(self) -> None:
        """The first decision is in: the timer is cancelled and no signal stops the path any more (``SIGTERM`` is
        kept in ``pending``)."""
        self.in_path = False
        if self._armed:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def close(self) -> None:
        if not self._armed:
            return
        self._armed = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        for number, old in self._old.items():
            signal.signal(number, old if old is not None else signal.SIG_DFL)


class _Checked:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _pin(name: str, pinned: Any, now: Any) -> None:
    if pinned != now:
        raise UsageError(f"{name} differs from the preregistration") from None


def check_routing(directory: Path, prereg: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Each mine's routing file (rule 4): one ``openai_compat`` endpoint at ``site:<mine>``, ``judge_record`` routed
    to it with no escalation, its pins the preregistered ones. ``{mine: RoutingConfig}``."""
    pinned = {e["name"]: e for e in prereg["endpoints"]}
    if key not in pinned:
        raise UsageError("the endpoint is not in the preregistration") from None
    want = {k: v for k, v in pinned[key].items() if k not in ("name", "deadline_s", "max_retries")}
    out = {}
    for mine in prereg["mines"]:
        try:
            config = load_routing(directory / f"{mine}.json", tasks=(JUDGE_TASK,), check_env=False)
        except ConfigError as exc:
            raise UsageError(f"routing: {exc}") from None
        if key not in config.endpoints:
            raise UsageError("the routing does not name the endpoint") from None
        ep = config.endpoints[key]
        pins = e1_extract.endpoint_pins(ep)
        if pins.pop("boundary") != f"site:{mine}" or ep.provider != "openai_compat":
            raise UsageError("a mine's endpoint is not openai_compat at its own boundary") from None
        _pin("the endpoint's pins", want, pins)
        if (ep.deadline_s, ep.max_retries) != (ENDPOINT_DEADLINE_S, MAX_RETRIES):
            raise UsageError("the endpoint's deadline or retries differ from the rule") from None
        route = config.routes.get(JUDGE_TASK)
        if route is None or route.endpoint != key or route.escalate_to is not None:
            raise UsageError("the routing must route judge_record to the endpoint without escalation") from None
        problem = key_problem(ep, os.environ)
        if problem is not None:
            raise UsageError(f"routing: {problem}") from None
        out[mine] = config
    return out


def _check_args(args: argparse.Namespace) -> _Checked:
    check_run_id(args.run_id)
    if args.budget_seconds < 1:
        raise UsageError("--budget-seconds must be >= 1") from None
    prereg, prereg_sha = read_prereg(args.prereg)
    if not 1 <= args.slot <= prereg["slots"] or len(prereg["questions"]) != prereg["slots"]:
        raise UsageError("--slot is not one of the preregistered slots") from None
    _pin("the code hash", prereg["code_hash"], code_hash())
    _pin("the judge task", prereg["task"], task_pins())
    configs = check_routing(Path(args.routing_dir), prereg, args.endpoint)
    out_dir = run_dir(args.runs_dir, KIND, args.run_id)
    return _Checked(prereg=prereg, prereg_sha=prereg_sha, configs=configs, out_dir=out_dir,
                    question=prereg["questions"][args.slot - 1])


def _base(args: argparse.Namespace, c: _Checked) -> dict[str, Any]:
    q = c.question
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_l1_run", "experiment": "L1", "run_id": args.run_id,
            "endpoint": args.endpoint, "slot": args.slot, "question_id": q["question_id"],
            "prereg_sha256": c.prereg_sha,
            "pinned": {"input_sha256": c.prereg["input"]["sha256"], "config_hash": c.prereg["pack"]["config_hash"],
                       "code_hash": c.prereg["code_hash"], "task": c.prereg["task"],
                       "endpoint": next(e for e in c.prereg["endpoints"] if e["name"] == args.endpoint)},
            "budget_seconds": args.budget_seconds, "boundary_prefix": "site:", "data_label": DATA_LABEL,
            "code_commit": code_commit(), "started_at": utc_clock()}


def _empty_run(base: Mapping[str, Any]) -> dict[str, Any]:
    return {**base, "complete": False, "finished": False, "stopped": None, "problem": None, "model_calls": 0,
            "measurement": False, "models_listed": None, "listed_fake": None, "models_served": [], "checks": {},
            "timing": None, "derived": None, "time_to_final_s": None, "late_still_running": 0, "verdicts": None,
            "mines": {}, "lexical": None, "confirm_shares": {}, "crossing": None, "finished_at": None}


def _timing_doc(result: P.PathResult, slot: int, calls: int, finished: bool,
                elapsed_s: float | None = None) -> dict[str, Any]:
    t = result.timing if result is not None else None
    if t is None:
        return {"slot": slot, "finished": False, "elapsed_lower_bound_s": SC.r3(elapsed_s), "calls": calls,
                "time_to_answer_s": None, "question_build_s": None, "gate_s": None, "between_s": [], "mines": []}
    return {"slot": slot, "finished": finished, "elapsed_lower_bound_s": None, "calls": calls,
            "time_to_answer_s": SC.r3(t["time_to_answer_s"]), "question_build_s": SC.r3(t["question_build_s"]),
            "gate_s": SC.r3(t["gate_s"]), "between_s": [SC.r3(g) for g in t["between_s"]],
            "mines": [{"mine": m["mine"], "seconds": SC.r3(m["seconds"]), "timed_out": m["timed_out"],
                       "contended": m["contended"]} for m in t["mines"]]}


def _mine_docs(c: _Checked, result: P.PathResult, ledgers: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per routed mine: role, stratum, retrieved records, calls, HTTP attempts, failures by class, the model's
    seconds (the sum of its judge calls' latencies), the first call's and the median call's latency (K5), and the
    stage figures."""
    q = c.question
    stage = {m["mine"]: m for m in (result.timing or {}).get("mines", [])} if result is not None else {}
    out = {}
    for mine in sorted(q["routes"]):
        rows = [r for r in ledgers.get(mine, []) if r["task"] == JUDGE_TASK and r["attempt"] >= 1]
        ok = [r["latency_ms"] / 1000 for r in rows if r["ok"] and r["latency_ms"] is not None]
        first = next((r for r in rows if r["latency_ms"] is not None), None)
        rec = result.recorders.get(mine) if result is not None else None
        failures = {"model": 0, "transport": 0}
        for reply in (rec.replies.values() if rec is not None else ()):
            if reply.get("after_close"):
                continue                # ended after the unit stopped the path: no call of the unit's answer
            cls = e1_extract.failure_class(reply["error_kind"])
            if cls is not None:
                failures[cls] += 1
        s = stage.get(mine)
        out[mine] = {"role": q["routes"][mine], "stratum": q["strata"][mine], "records": q["records"][mine],
                     "calls": rec.calls if rec is not None else 0, "attempts": len(rows), "failures": failures,
                     "model_s": SC.r3(sum(r["latency_ms"] for r in rows if r["latency_ms"] is not None) / 1000),
                     "first_call_s": SC.r3(first["latency_ms"] / 1000) if first is not None else None,
                     "median_call_s": SC.r3(stats.percentile(ok, 50)) if ok else None,
                     "seconds": SC.r3(s["seconds"]) if s else None, "timed_out": s["timed_out"] if s else None,
                     "contended": s["contended"] if s else None,
                     "call_latencies_s": [SC.r3(r["latency_ms"] / 1000) for r in rows
                                          if r["ok"] and r["latency_ms"] is not None]}
    return out


def breaker_stopped(rows: Sequence[Mapping[str, Any]], endpoint: str) -> bool:
    """Whether a mine's ``judge_record`` ledger rows show the verifier's breaker stop (``edge.verify``): its last
    :data:`BREAKER_AFTER` calls, in record order (the index of the ledger ref ``j:<question>:<i>``), each ended in a
    failure that says the route's endpoint is down, as ``edge.extract.server_down`` reads one: the call's last attempt
    failed with a :data:`SERVER_DOWN_KINDS` kind on ``endpoint``. A failed call of another kind (a reply that failed
    validation and its repair) resets the count, as it does in the verifier."""
    final: dict[int, Mapping[str, Any]] = {}
    for r in rows:
        m = P.REF_RE.fullmatch(str(r.get("ref")))
        if m is None or r.get("attempt", 0) < 1:
            continue
        i = int(m.group(1))
        if i not in final or r["attempt"] >= final[i]["attempt"]:
            final[i] = r
    last = [final[i] for i in sorted(final)][-BREAKER_AFTER:]
    return len(last) == BREAKER_AFTER and all(
        not r.get("ok") and r.get("error_kind") in SERVER_DOWN_KINDS and r.get("endpoint") == endpoint for r in last)


def unit_check(c: _Checked, result: P.PathResult, ledgers: Mapping[str, list[dict[str, Any]]], *,
               endpoint: str) -> list[str]:
    """K4's unit check, one problem per failing mine (``<mine>: <check>``): calls equal the preregistered retrieved
    records (fewer only for a degraded verdict, the breaker's stop on ``endpoint`` (:func:`breaker_stopped`), or a
    mine that timed out while its late thread still ran when the unit ended); every call has a ``judge_record`` row in
    the mine's ledger, but for a call still in flight when the unit stopped the path (``after_close``: the ledger
    closed under it)."""
    problems = []
    stage = {m["mine"]: m for m in (result.timing or {}).get("mines", [])}
    for mine in sorted(c.question["routes"]):
        rec = result.recorders.get(mine)
        calls = rec.calls if rec is not None else 0
        want = c.question["records"][mine]
        verdict = result.first.get(mine, {})
        rows = [r for r in ledgers.get(mine, []) if r["task"] == JUDGE_TASK]
        refs = {r["ref"] for r in rows}
        down = breaker_stopped(rows, endpoint)
        allowed_fewer = (verdict.get("reason") == "degraded" or down
                         or ((stage.get(mine) or {}).get("timed_out") is True and result.late_still_running > 0))
        if calls > want or (calls < want and not allowed_fewer):
            problems.append(f"{mine}: calls")
        if rec is not None and any(entry["ref"] not in refs for entry in rec.replies.values()
                                   if not entry.get("after_close")):
            problems.append(f"{mine}: ledger")
    return problems


def _write_records(path: Path, c: _Checked, check: Mapping[str, Any], result: P.PathResult | None,
                   lexical: Mapping[str, list[dict[str, str]]]) -> None:
    """``records.jsonl``: per routed mine and retrieved record, by index, the model's answers (or its error kind)
    and the lexical judge's. No record id, no narrative."""
    lines = []
    for mine in sorted(c.question["routes"]):
        rec = result.recorders.get(mine) if result is not None else None
        for i, positive in enumerate(check["positives"][mine]):
            got = rec.replies.get(i) if rec is not None else None
            model = None
            if got is not None and not got.get("after_close"):
                reply = got["reply"] or {}
                model = {"mentions_entity": reply.get("mentions_entity"),
                         "describes_predicate": reply.get("describes_predicate"), "error_kind": got["error_kind"]}
            lex = lexical.get(mine, [])
            lines.append({"slot": c.question["slot"], "mine": mine, "index": i, "positive": bool(positive),
                          "model": model, "lexical": ({"mentions_entity": lex[i]["mentions_entity"],
                                                       "describes_predicate": lex[i]["describes_predicate"],
                                                       "error_kind": None} if i < len(lex) else None)})
    path.write_text("".join(canonical_dumps(line) + "\n" for line in lines), encoding="utf-8")


def _execute(args: argparse.Namespace, c: _Checked) -> int:
    t_start = _clock()
    out = c.out_dir
    out.mkdir(parents=True)
    base = _base(args, c)
    doc = _empty_run(base)
    write_json_atomic(out / "run.json", doc)

    def finish(code: int, **fields: Any) -> int:
        doc.update(fields, finished_at=utc_clock())
        write_json_atomic(out / "run.json", doc)
        return code

    raw = Path(args.raw)
    try:
        zip_path = ensure_file(raw)
    except P.L1Error:
        return finish(EXIT_INFRA, problem="fetch")
    if file_sha(zip_path) != c.prereg["input"]["sha256"]:
        return finish(EXIT_INFRA, problem="input_sha256")
    work = Path(args.runs_dir) / f"l1-work-{args.run_id}"
    signals = _Signals(lambda: args.budget_seconds - AFTER_PATH_S - (_clock() - t_start))
    try:
        return _unit(args, c, t_start, doc, finish, raw, work, signals)
    finally:
        signals.close()
        shutil.rmtree(work, ignore_errors=True)


def _unit(args: argparse.Namespace, c: _Checked, t_start: float, doc: dict[str, Any], finish: Any, raw: Path,
          work: Path, signals: "_Signals") -> int:
    out = c.out_dir
    q = c.question
    spec = {k: q[k] for k in ("slot", "kind", "predicate", "key", "entity_type", "entity_id", "channel", "as_of",
                              "alert_week", "window_start")}
    checks: dict[str, Any] = {}
    try:
        data = P.prepare(raw, work / "data")
        checks["config_hash"] = data.pack.config_hash == c.prereg["pack"]["config_hash"]
        checks["audit"] = P.audit_counts(data.audit) == c.prereg["audit"]
        checks["alert"] = P.alerts(data.audit) == [c.prereg["alert"]]
        checks["mines"] = list(data.site_ids) == c.prereg["mines"]
        if not all(checks.values()):
            return finish(2, checks=checks, problem="pins")
        probe, world = P.run_path(data, spec, "probe", work / "probe", keep_world=True)
        try:
            check = P.retrieval_check(data, world, spec, probe.window, probe.routes)
        finally:
            world.close()
        checks["question_id"] = probe.question_id == q["question_id"]
        checks["routes"] = probe.routes == q["routes"]
        checks["window"] = probe.window == q["window"]
        checks["retrieved"] = check["same"] and check["records"] == q["records"]
        if not all(checks.values()):
            return finish(2, checks=checks, problem="pins")
    except P.L1Error as err:
        return finish(2, checks=checks, problem=err.code)
    ledgers = {mine: out / f"ledger-{mine}.jsonl" for mine in c.prereg["mines"]}
    runtimes = {mine: Runtime(c.configs[mine], boundary=f"site:{mine}", ledger_path=ledgers[mine], run_id=args.run_id,
                              clock=utc_clock, data_label=DATA_LABEL) for mine in c.prereg["mines"]}
    listed = list_models(c.configs[c.prereg["mines"][0]].endpoints[args.endpoint])
    result: P.PathResult | None = None
    model_world: P.World | None = None
    stopped: str | None = None
    t_path = time.perf_counter()

    def answered() -> float:
        # the first decision is in: the timer no longer stops the path, and the late wait takes what is left
        signals.answered()
        return max(0.0, args.budget_seconds - AFTER_PATH_S - (_clock() - t_start))

    try:
        signals.enter()
        result, model_world = P.run_path(data, spec, "model", work / "model", runtimes=runtimes,
                                         on_answer=answered, keep_world=True)
    except _Stop as stop:
        stopped = stop.reason
    except KeyboardInterrupt:
        stopped = "interrupted"
    finally:
        signals.in_path = False
        for rec in (result.recorders.values() if result is not None else ()):
            rec.close()              # the unit ends here for K4: a late thread starts no further call
        for rt in runtimes.values():
            with_closed(rt)          # a call still in flight fails on its ledger, so every ledger ends on a whole line
    elapsed = time.perf_counter() - t_path
    if signals.pending == "interrupted":
        stopped = "interrupted"
    rows_by_mine = P.ledger_by_mine(ledgers)
    all_rows = [r for rows in rows_by_mine.values() for r in rows]
    model_calls = sum(1 for r in all_rows if r["attempt"] in (0, 1))
    endpoints = [cfg.endpoints[args.endpoint] for cfg in c.configs.values()]
    common = {"model_calls": model_calls, "measurement": measurement_flag(endpoints, all_rows,
                                                                          bool(listed.get("fake"))),
              "models_listed": listed["ids"] if listed.get("ok") else None, "listed_fake": listed.get("fake"),
              "models_served": sorted({r["model_served"] for r in all_rows if r["model_served"]})}
    if result is None or stopped == "interrupted":
        if model_world is not None:
            model_world.close()
        doc.update(common)
        _write_records(out / "records.jsonl", c, check, None, {})
        timing = _timing_doc(None, args.slot, model_calls, False, elapsed)
        code = 130 if stopped == "interrupted" else 1
        return finish(code, checks=checks, stopped=stopped, timing=timing,
                      mines=_mine_docs(c, None, rows_by_mine))
    try:
        problems = unit_check(c, result, rows_by_mine, endpoint=args.endpoint)
        checks["unit_check"] = problems
        lexical_lines = P.lexical_replies(data, model_world, spec, result.question, result.routes)
        shares = P.confirm_shares(data, model_world, result, spec["predicate"])
        crossing = P.crossing_overlap(data, model_world)
    finally:
        model_world.close()
    _write_records(out / "records.jsonl", c, check, result, lexical_lines)
    lexical, _ = P.run_path(data, spec, "lexical", work / "lexical")
    want = c.prereg["judges"]["lexical"][str(args.slot)]
    reproduces = ({m: v["verdict"] for m, v in lexical.first.items()} == want["verdicts"]
                  and lexical.status_first == want["status"])
    checks["lexical_reproduced"] = reproduces
    timing = _timing_doc(result, args.slot, sum(r.calls for r in result.recorders.values()), True)
    derived = {"at_once_s": SC.r3(P.at_once(result)),
               "default_deadline": {k: (SC.r3(v) if k == "seconds" else v)
                                    for k, v in P.default_deadline(data.pack, result, spec["as_of"]).items()},
               "no_model_s": SC.r3((lexical.timing or {}).get("time_to_answer_s"))}
    verdicts = {"first": {m: {k: v[k] for k in ("verdict", "reason", "support_bucket")}
                          for m, v in sorted(result.first.items())},
                "final": {m: {k: v[k] for k in ("verdict", "reason", "support_bucket")}
                          for m, v in sorted(result.final.items())},
                "status_first": result.status_first, "status_final": result.status_final}
    complete = (not problems and reproduces and result.question_id == q["question_id"]
                and result.routes == q["routes"] and signals.pending != "interrupted")
    code = 0 if complete else 130 if signals.pending == "interrupted" else 1
    return finish(code, **common, stopped="interrupted" if signals.pending == "interrupted" else None,
                  checks=checks, complete=complete, finished=True,
                  problem=None if complete else ("unit_check" if problems else "lexical" if not reproduces
                                                 else "routes"),
                  timing=timing, derived=derived, time_to_final_s=SC.r3(result.time_to_final_s),
                  late_still_running=result.late_still_running, verdicts=verdicts,
                  mines=_mine_docs(c, result, rows_by_mine),
                  lexical={"verdicts": {m: v["verdict"] for m, v in sorted(lexical.first.items())},
                           "status_first": lexical.status_first,
                           "time_to_answer_s": SC.r3((lexical.timing or {}).get("time_to_answer_s")),
                           "reproduces": reproduces},
                  confirm_shares={m: SC.r3(v) for m, v in shares.items()},
                  crossing={"overlap_bytes": crossing["overlap_bytes"], "kib": SC.r3(crossing["bytes"] / 1024),
                            "items": crossing["items"]})


def with_closed(runtime: Any) -> None:
    try:
        runtime.close()
    except Exception:              # noqa: BLE001 (a closed ledger is closed)
        pass


def cmd_run(args: argparse.Namespace) -> int:
    try:
        checked = _check_args(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _execute(args, checked)


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.l1", description="Latency test L001.")
    sub = p.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prereg", help="the plan job's L1 step")
    pre.add_argument("--plan", required=True)
    pre.add_argument("--raw")
    run = sub.add_parser("run", help="one model on one question slot")
    run.add_argument("--prereg", required=True)
    run.add_argument("--raw", required=True)
    run.add_argument("--routing-dir", required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--slot", type=int, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--runs-dir", default="runs")
    run.add_argument("--budget-seconds", type=int, required=True)
    return p


def plan_has_l1(plan_path: str) -> bool:
    """Whether the plan at ``plan_path`` (read leniently: an unreadable plan has none) holds an L1 unit."""
    try:
        plan = strict_load(Path(plan_path).read_bytes())
    except (OSError, StrictJsonError):
        return False
    return isinstance(plan, dict) and any(isinstance(u, dict) and u.get("experiment") == KIND
                                          for u in plan.get("units") or ())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prereg":
            plan_path = Path(os.path.abspath(args.plan))
            raw = Path(args.raw) if args.raw else raw_dir(plan_path.parent)
            with tempfile.TemporaryDirectory(prefix="lab-l1-prereg-") as tmp:
                return preregister(plan_path, raw, Path(tmp))
        return cmd_run(args)
    except Exception as err:       # noqa: BLE001 (K9: an error's class and location, never its text)
        print(error_line(f"l1 {args.command}", err), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
