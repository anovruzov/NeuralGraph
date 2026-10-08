"""Shared fixtures for the lab tests: paths, request builders, an in-process plan runner, throwaway git worlds, stub
worlds, copies of dry runs to mutate and re-seal (:class:`DryTree`), and the summary checks (code spans, sources).

Every git call passes ``-c user.name=lab -c user.email=lab@invalid`` and every repository is created with ``-b
main``, so the tests run with an empty ``HOME``, ``GIT_CONFIG_NOSYSTEM=1`` and ``GIT_CONFIG_GLOBAL=/dev/null``.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "lab" / "data"
MANIFEST_TEST = DATA / "manifest-test.json"
PLUMBING_MIN = DATA / "requests" / "plumbing-min.json"
PLUMBING_001 = ROOT / "lab" / "requests" / "plumbing-001.json"
LAB_MANIFEST = ROOT / "lab" / "models.json"
GIT_IDENTITY = ("-c", "user.name=lab", "-c", "user.email=lab@invalid")


def plumbing_min() -> dict[str, Any]:
    return json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))


def request_obj(**changes: Any) -> dict[str, Any]:
    """plumbing-min with top-level keys replaced (a value of ``None`` deletes the key)."""
    obj = copy.deepcopy(plumbing_min())
    for key, value in changes.items():
        if value is None:
            obj.pop(key, None)
        else:
            obj[key] = value
    return obj


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


def run_plan(argv: list[str]) -> tuple[int, str, str]:
    """``lab.plan.main`` in-process with stdout and stderr captured."""
    from lab import plan

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = plan.main(argv)
    return code, out.getvalue(), err.getvalue()


def make_plan(directory: Path, request: dict[str, Any], manifest: Path = MANIFEST_TEST,
              name: str = "lab-test") -> tuple[dict[str, Any], Path]:
    """Plan ``request`` (written to ``directory/<name>.json``) into ``directory/plan``; returns (plan, plan path)."""
    path = write_json(directory / f"{name}.json", request)
    code, out, err = run_plan(["--request", str(path), "--manifest", str(manifest), "--out", str(directory / "plan")])
    if code != 0:
        raise AssertionError(f"plan failed: {err}")
    plan_path = directory / "plan" / "plan.json"
    return json.loads(plan_path.read_text(encoding="utf-8")), plan_path


def lab_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def lab_cli(*args: str, cwd: Path = ROOT, timeout: float = 300, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd, env=lab_env(), capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL, **kwargs)


def pids_mentioning(text: str) -> list[int]:
    """Live processes whose command line contains ``text`` (this process excluded)."""
    found = []
    needle = text.encode("utf-8")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if needle in cmdline and state != "Z":
            found.append(int(entry.name))
    return found


def kill_mentioning(text: str) -> None:
    for pid in pids_mentioning(text):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def wait_until(predicate: Any, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# --------------------------------------------------------------------------------------------------- git worlds

def git_run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = git_run(*args, cwd=cwd)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


class GitWorld:
    """A bare ``origin`` with ``main``, a ``work`` clone to commit and push from, and fresh ``--no-local`` clones that
    play the workflow checkout (full history, every branch as ``origin/<branch>``, HEAD detached at the pushed tip;
    unreachable objects are not copied, as with a real fetch)."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        self._clones = 0
        self._events = 0
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        git("init", "-q", "-b", "main", str(self.work))
        self.git("remote", "add", "origin", str(self.origin))
        self.base = self.commit({"README.md": "base\n"}, message="base")
        self.push("main")

    def git(self, *args: str, check: bool = True) -> str:
        return git(*args, cwd=self.work, check=check)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def commit(self, files: dict[str, str] | None = None, delete: tuple[str, ...] = (), message: str = "change",
               rename: tuple[str, str] | None = None) -> str:
        for rel, content in (files or {}).items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.git("add", "--", rel)
        for rel in delete:
            self.git("rm", "-q", "--", rel)
        if rename is not None:
            self.git("mv", "--", *rename)
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def push(self, branch: str, force: bool = False) -> str:
        self.git("push", "-q", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{branch}")
        return self.head()

    def clone(self, sha: str) -> Path:
        self._clones += 1
        path = self.tmp / f"plan-{self._clones}"
        git("clone", "-q", "--no-local", str(self.origin), str(path))
        git("checkout", "-q", "--detach", sha, cwd=path)
        return path

    def event(self, **fields: Any) -> Path:
        self._events += 1
        path = self.tmp / f"event-{self._events}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path

    def push_event(self, *, before: str, after: str, branch: str, deleted: bool = False,
                   default_branch: str = "main", ref: str | None = None) -> Path:
        return self.event(before=before, after=after, ref=ref or f"refs/heads/{branch}", deleted=deleted,
                          repository={"default_branch": default_branch})


# --------------------------------------------------------------------------------------------------- stub worlds

COMMIT = "0123456789abcdef0123456789abcdef01234567"
HUB_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"
RELEASE_OWNER, RELEASE_REPO = "example-org", "lab-test-server"
ARCHIVE_ROOT = "lab-test-server-b0000"
BINARY = "bin/server-stub"
MODEL_KEY = "tiny-gguf"


def gguf_blob(size: int, seed: int = 1) -> bytes:
    """``GGUF`` followed by deterministic filler, ``size`` bytes in all."""
    import hashlib
    body = b"".join(hashlib.sha256(f"{seed}:{i}".encode()).digest() for i in range(size // 32 + 1))
    return (b"GGUF" + body)[:size]


def stub_manifest(directory: Path, *, revision: str = COMMIT, lock: dict[str, Any] | None = None,
                  model: dict[str, Any] | None = None, server: dict[str, Any] | None = None) -> Path:
    """The test manifest with the stub server (non-empty archive root) and one gguf model, plus its lock."""
    manifest = json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))
    base = manifest["server"]
    manifest["server"] = {**base, "archive_root": ARCHIVE_ROOT, "binary": BINARY, **(server or {})}
    entry = manifest["models"][MODEL_KEY]
    entry["gguf"]["revision"] = revision
    entry.update(model or {})
    manifest["models"] = {MODEL_KEY: entry}
    path = write_json(directory / "manifest.json", manifest)
    write_json(directory / "manifest.lock.json", lock or {"schema_version": 1, "server": None, "models": {}})
    return path


def gguf_request(*, e3: bool = True, g0: bool = True, program: str | None = None) -> dict[str, Any]:
    obj = plumbing_min()
    obj["provider"] = program or json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))["server"]["program"]
    obj["models"] = [MODEL_KEY]
    blocks = {}
    if g0:
        blocks["g0"] = {**obj["experiments"]["g0"], "models": [MODEL_KEY]}
    if e3:
        blocks["e3"] = {**obj["experiments"]["e3"]}
    obj["experiments"] = blocks
    return obj


def call_main(module: Any, argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
    """``module.main(argv, **kwargs)`` in-process with stdout and stderr captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(argv, **kwargs)
    return code, out.getvalue(), err.getvalue()


class StubWorld:
    """A loopback hub and release CDN serving a stub server tarball and a GGUF blob, a manifest pinning them and a
    plan for one gguf model; provision, prepare and run happen in process through the hub's ``url_map``. ``lock`` is
    the lock to write, or a function of the world (its tarball and blob exist by then) that returns it."""

    def __init__(self, tmp: Path, *, server_config: dict[str, Any] | None = None, revision: str = COMMIT,
                 hub_commit: str | None = None, blob_size: int = 65536, request: dict[str, Any] | None = None,
                 lock: Any = None, model: dict[str, Any] | None = None, server: dict[str, Any] | None = None,
                 release: dict[str, Any] | None = None, unsafe: str | None = None) -> None:
        from tests.lab.stubs.fake_server_stub import write_launcher
        from tests.lab.stubs.http_stub import HubStub, make_tarball

        self.tmp = Path(tmp)
        self.hub = HubStub().start()
        self.record_dir = self.tmp / "stub-records"
        self.manifest_path = stub_manifest(self.tmp / "m", revision=revision, model=model, server=server)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        server = self.manifest["server"]
        self.tag, self.asset = server["tag"], server["asset"]
        self.launcher = write_launcher(self.tmp / "launcher" / "server-stub",
                                       {"record_dir": str(self.record_dir), "tag": self.tag, **(server_config or {})})
        self.tarball = make_tarball(self.tmp / "asset.tar.gz", archive_root=ARCHIVE_ROOT, binary=BINARY,
                                    launcher=self.launcher, unsafe=unsafe)
        self.hub.add_release(RELEASE_OWNER, RELEASE_REPO, self.tag, self.asset, self.tarball, **(release or {}))
        self.blob = gguf_blob(blob_size)
        entry = self.manifest["models"][MODEL_KEY]
        self.commit = hub_commit or (revision if revision == COMMIT else HUB_COMMIT)
        self.hub.add_model(entry["gguf"]["repo"], commit=self.commit, file=entry["gguf"]["file"], blob=self.blob,
                           revision=revision)
        if lock is not None:
            write_json(self.manifest_path.with_name("manifest.lock.json"), lock(self) if callable(lock) else lock)
        self.plan, self.plan_path = make_plan(self.tmp / "p", request or gguf_request(), self.manifest_path)
        self.cache = self.tmp / "cache"
        self.records = self.tmp / "records"
        self.sleeps: list[float] = []

    def close(self) -> None:
        self.hub.stop()

    def provision(self, target: str, *extra: str, out: Path | None = None) -> tuple[int, str, str, dict[str, Any]]:
        from lab import provision

        out = out or self.records / (target if target == "server" else f"gguf-{MODEL_KEY}")
        argv = [target, *(["--model", MODEL_KEY] if target == "gguf" else []), "--plan", str(self.plan_path),
                "--manifest", str(self.manifest_path), "--cache-root", str(self.cache), "--out", str(out), *extra]
        code, stdout, stderr = call_main(provision, argv, url_map=self.hub.url_map, sleep=self.sleeps.append)
        record_path = out / "provision.json"
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
        return code, stdout, stderr, record

    def provision_all(self) -> None:
        for target in ("server", "gguf"):
            code, stdout, stderr, record = self.provision(target)
            if code != 0:
                raise AssertionError(f"provision {target} failed: {stderr}")

    def prepare(self, out: Path, shard: str | None = None, *extra: str) -> tuple[int, str, str]:
        from lab import shard as lab_shard

        shard = shard or next(s["shard"] for s in self.plan["shards"] if s["kind"] == "gguf")
        return call_main(lab_shard, ["prepare", "--plan", str(self.plan_path), "--shard", shard,
                                     "--provision-records", str(self.records), "--cache-root", str(self.cache),
                                     "--out", str(out), *extra], url_map=self.hub.url_map, sleep=self.sleeps.append)

    def run(self, out: Path, shard: str | None = None, *extra: str) -> tuple[int, str, str]:
        from lab import shard as lab_shard

        shard = shard or next(s["shard"] for s in self.plan["shards"] if s["kind"] == "gguf")
        return call_main(lab_shard, ["run", "--plan", str(self.plan_path), "--shard", shard, "--out", str(out),
                                     *extra])

    def stub_files(self, kind: str) -> list[Any]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.record_dir.glob(f"{kind}-*.json"), key=lambda p: int(p.stem.split("-")[1]))]


# --------------------------------------------------------------------------------------------------- summaries

NUMBER_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def split_code_spans(md: str) -> tuple[str, list[str]]:
    """(the text outside code spans, each span's content) under CommonMark's rule: a run of n backticks opens a span
    that the next run of exactly n backticks closes; a run that is never closed is literal text. A span becomes one
    space in the outside text. Contents are returned raw (no padding stripped, no table escapes undone)."""
    outside: list[str] = []
    spans: list[str] = []
    i, n = 0, len(md)
    while i < n:
        if md[i] != "`":
            outside.append(md[i])
            i += 1
            continue
        j = i
        while j < n and md[j] == "`":
            j += 1
        run, k, close = j - i, j, -1
        while k < n:
            if md[k] == "`":
                m = k
                while m < n and md[m] == "`":
                    m += 1
                if m - k == run:
                    close = k
                    break
                k = m
            else:
                k += 1
        if close < 0:
            outside.append(md[i:j])
            i = j
            continue
        spans.append(md[j:close])
        outside.append(" ")
        i = close + run
    return "".join(outside), spans


def span_text(content: str, table: bool = False) -> str:
    """What a reader sees of a span's raw content: one space stripped from each side when both are spaces and the
    content is not all spaces; in a table cell ``\\|`` is ``|``."""
    if table:
        content = content.replace("\\|", "|")
    if len(content) >= 2 and content[0] == " " and content[-1] == " " and content.strip(" "):
        content = content[1:-1]
    return content


def resolve(doc: Any, pointer: str) -> Any:
    """The tests' own RFC 6901 resolver."""
    if pointer == "":
        return doc
    assert pointer.startswith("/"), pointer
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, list):
            assert re.fullmatch(r"0|[1-9][0-9]*", token), pointer
            doc = doc[int(token)]
        else:
            doc = doc[token]
    return doc


def check_sources(test: Any, md: str, sources: list[dict[str, Any]], root: Path) -> int:
    """Every number outside code spans is, in order, the text of a source entry; no other digit is outside a code
    span; each entry resolves in its file to a value that renders as its text. Returns the number of entries."""
    from lab.summary import STYLES

    outside, _ = split_code_spans(md)
    tokens = [m.group(0) for m in NUMBER_RE.finditer(outside)]
    test.assertEqual(tokens, [e["text"] for e in sources])
    test.assertIsNone(re.search(r"[0-9]", NUMBER_RE.sub("", outside)))
    docs: dict[str, Any] = {}
    for entry in sources:
        if entry["file"] not in docs:
            docs[entry["file"]] = json.loads((Path(root) / entry["file"]).read_text(encoding="utf-8"))
        value = resolve(docs[entry["file"]], entry["pointer"])
        test.assertNotIsInstance(value, bool)
        test.assertEqual(STYLES[entry["style"]](value), entry["text"], entry)
    return len(sources)


# --------------------------------------------------------------------------------------------------- dry-run trees

def canonical_json(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def dry_run(out: Path, request: Path, manifest: Path | None = None) -> subprocess.CompletedProcess[str]:
    return lab_cli("lab.dryrun", "--request", str(request), "--out", str(out),
                   *(["--manifest", str(manifest)] if manifest is not None else []))


class DryTree:
    """A copy of a dry run's plan and shards (summaries left out) that a test mutates and re-seals the way the
    workflow would have sealed it."""

    def __init__(self, source: Path, dest: Path, manifest: Path = MANIFEST_TEST) -> None:
        self.root = Path(dest)
        self.manifest = manifest
        shutil.copytree(source / "plan", self.root / "plan", ignore=shutil.ignore_patterns("summary*"))
        shutil.copytree(source / "shards", self.root / "shards", ignore=shutil.ignore_patterns("summary"))
        self.plan_path = self.root / "plan" / "plan.json"
        self.shards = self.root / "shards"

    @property
    def plan(self) -> dict[str, Any]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def plan_sha256(self) -> str:
        return hashlib.sha256(self.plan_path.read_bytes()).hexdigest()

    def path(self, shard: str, rel: str = "") -> Path:
        return self.shards / shard / rel if rel else self.shards / shard

    def read(self, shard: str, rel: str) -> Any:
        return json.loads(self.path(shard, rel).read_text(encoding="utf-8"))

    def write(self, shard: str, rel: str, obj: Any) -> None:
        self.path(shard, rel).write_bytes(canonical_json(obj))

    def edit(self, shard: str, rel: str, change: Callable[[Any], None]) -> None:
        obj = self.read(shard, rel)
        change(obj)
        self.write(shard, rel, obj)

    def replace_unit_file(self, shard: str, unit: str, rel: str, data: bytes) -> None:
        """Rewrite a file a unit record lists, and the record's hash of it, as if the harness had written it so."""
        self.path(shard, rel).write_bytes(data)
        self.edit(shard, f"units/{unit}/unit.json",
                  lambda r: r["files"].update({rel: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}))

    def reseal(self, shard: str, *, attempt: int | None = None, plan: bool = True,
               steps: dict[str, str] | None = None, path: Path | None = None) -> None:
        from lab import shard as lab_shard

        env = {"GITHUB_RUN_ATTEMPT": str(attempt) if attempt is not None else ""}
        with mock.patch.dict(os.environ, env):
            code = lab_shard.seal(path or self.path(shard), shard, steps or {"run": "success"},
                                  str(self.plan_path) if plan else None)
        assert code == 0

    def set_plan(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Change the plan, point every shard's provenance at the new plan and re-seal every shard."""
        plan = self.plan
        change(plan)
        self.plan_path.write_bytes(canonical_json(plan))
        sha = self.plan_sha256()
        for shard in sorted(p.name for p in self.shards.iterdir()):
            self.edit(shard, "provenance.json", lambda prov: prov["plan"].update(sha256=sha))
            self.reseal(shard)

    def make_real(self, shard: str, *, units: tuple[str, ...] = (), model_units: tuple[str, ...] = ()) -> None:
        """A shard as a verified model server would have left it: provenance ``real``, the given units of kind gguf
        without fake rows (``model_units`` with every class check true, the others ``unverified``); re-sealed."""
        from lab.units import CHECK_KEYS

        self.edit(shard, "provenance.json", lambda p: p.update(result_class="real", provider="llama-server",
                                                               banner=None))
        for unit in (*units, *model_units):
            model = unit in model_units
            self.edit(shard, f"units/{unit}/unit.json", lambda r: r.update(
                kind="gguf", provider="llama-server", fake_rows=0,
                measurement_class="model" if model else "unverified",
                class_reason="verified" if model else "model_path",
                class_checks={k: model or k != "model_path" for k in CHECK_KEYS}))
        self.reseal(shard)

    def aggregate(self, out: Path, *, shards: Path | None = None, provision: Path | None = None,
                  manifest: Path | None = None, plan: Path | None = None) -> tuple[int, str, str, Any]:
        from lab import aggregate as lab_aggregate

        code, stdout, stderr = call_main(lab_aggregate, [
            "--plan", str(plan or self.plan_path), "--provision", str(provision or self.root / "provision"),
            "--shards", str(shards or self.shards), "--manifest", str(manifest or self.manifest), "--out", str(out)])
        report_path = Path(out) / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        return code, stdout, stderr, report
