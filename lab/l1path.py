"""Latency test L001's path (lab experiment ``l1``): the drafter demo's pipeline on MSHA's accident file, and one
judge answering one question through the product's own pushdown path, on fresh stores, timed.

``docs/collective/L001/CHOICE-L001.md`` is the rule (with its section "Amended before any run", K1 to K13) and
``BUILD-L001.md`` the build note. Everything here imports the pinned modules read-only and changes none of them.

**The data** (:func:`prepare`; rule 1): ``fetch_msha.py split`` with D002's settings, c1's export read by the
drafter's reader, the pack drafted from c1's training years (``draft_export``) and passed through the loader and the
privacy floor, all as the drafter demo's steps (a) and (b) run them (``demo/onboard/run_demo.py``, imported read-only);
c1's held-out years normalised and each mine id replaced by its label, m01 onwards in the order of the ids, as the
demo's step (d) does; then the pilot audit (``pilot.audit.audit``, no outcomes, every setting at its default). The
audit's X and S alerts in the evaluated weeks are the alert list. The records, weeks, sites and master data every path
uses are the audit's own (:func:`audit_inputs`, the audit's preamble), so every path's pipeline is the audit's
pipeline (``evaluate.baselines.run_pipeline``, enterprise ``pilot``).

**A path** (:func:`run_path`) is one judge on one question, on fresh stores (K4): a new pipeline in a new directory,
so a new HQ store and new mine stores. For the alert question, HQ's detection runs at the alert's available date in
the alert's channel (tie salt ``pilot``) and is saved; the run must list the alert in its week, and
``Orchestrator.verify_stored`` asks it at the candidate's own ``as_of``. A control question is
``constructed_candidate`` at the same ``as_of`` and channel with the alert question's window start, then
``verify_candidate``. Every mine's verifier is ``SiteVerifier`` with the seeded secret (``demo_seed`` 1) and the
verifier's clock at the question's ``as_of``:

* the **key judge** reads the pipeline's own mine stores (codes as filed); every other judge reads a copy of each
  mine's records with ``codes`` emptied (:func:`hidden_sites`: the same records, ingested and extracted lexically as the
  pipeline does, in a store of its own);
* the key judge, the **record-blind control** and the **route-role baseline** are lab runtimes with the runtime's
  surface (:class:`FakeRuntime`: ``boundary`` ``site:<mine>``, no exemption, ``run``), since the pinned fake provider
  is simulated-only and would refuse real records; the **lexical judge** is ``SiteVerifier`` with no runtime; a
  **model** is one ``Runtime`` per mine at boundary ``site:<mine>`` (mode ``own``, data label ``public``) whose
  ``run`` is wrapped by :class:`Recorder`, which passes each call through unchanged and keeps its reply;
* a **probe** path's handlers answer nothing (each mine is an HQ ``error`` record), so its routes and question id are
  known before any judge runs.

Delivery is the orchestrator's own (one mine after another, sorted, ``deadline_seconds`` 3600). Each handler is
wrapped by a timer (:class:`Timing`) that records when the orchestrator called it and when it returned
(``time.perf_counter``). The verdicts and the gate's status are read when ``verify_stored`` (or ``verify_candidate``)
returns, the first decision (K6); late verdicts are then waited for up to ``late_wait_s`` and taken in at the
question's ``as_of`` (``join_late``, ``collect_late``), and the final status is read again.

Nothing here writes a record value: results name mines by label and records by their index in their mine's retrieved
list.
"""
from __future__ import annotations

import contextlib
import functools
import importlib.util
import io
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.detect.detectors import detect
from mycelic.collective.detect.store import RUN_CHANNELS
from mycelic.collective.edge.egress import read_log
from mycelic.collective.edge.records import RecordStore
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import (JUDGE_TASK, SiteVerifier, judge_payload, lexical_judge, retrieve)
from mycelic.collective.evaluate.baselines import run_pipeline
from mycelic.collective.experiments.openfda_replay import replay_weeks
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import canonical_bytes, strict_load
from mycelic.collective.leakage import CANARY_PREFIX, Artifact, Manifest, scan
from mycelic.collective.onboard import draft as D
from mycelic.collective.onboard import score as S
from mycelic.collective.packs.connector import map_rows
from mycelic.collective.packs.loader import FrozenPack
from mycelic.collective.pilot import audit as A
from mycelic.collective.pushdown.gate import VerdictRecord, evaluate, hq_record_body
from mycelic.collective.pushdown.orchestrator import Orchestrator, constructed_candidate

from . import ROOT

ARM = "msha"
COMPANY = "c1"
SETTINGS = "docs/collective/onboard/D002-settings.json"
DEMO_SCRIPT = "demo/onboard/run_demo.py"
DEMO_RECORD = "demo/onboard/recorded/record-001-demo.json"
FETCH = "tools/onboard/fetch_msha.py"
ENTERPRISE = A.ENTERPRISE
TIE_SALT = "pilot"
DEADLINE_S = 3600.0                  # the orchestrator's most (rule 4)
DEFAULT_DEADLINE_S = 600.0           # the product's default, derived only (rule 6)
DEMO_SEED = 1
COUNT_CHANNELS = ("X", "S")
CONTROLS = 4
SLOTS = 1 + CONTROLS
JUDGES = ("key", "lexical", "record_blind", "route_role")       # the plan job's judges (rule 5, K2)
STRATA = ("codes", "text_only", "sibling")
REF_RE = re.compile(r"j:[0-9a-f]{12}:([0-9]+)", re.ASCII)       # SiteVerifier's ledger ref for record i


class L1Error(Exception):
    """A stop or a refusal of L001's path: ``code`` is a fixed word (``lab.notes.L1_STOPS``), never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------------------------------- the data

@functools.lru_cache(maxsize=None)
def load_script(rel: str) -> Any:
    """A repository script (the demo, the MSHA fetch) as a module, imported once, read-only."""
    name = "lab_l1_" + Path(rel).stem
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@dataclass
class Data:
    """c1's drafted pack, its labelled held-out records and the audit, as the demo makes them. ``records`` are the
    audit's mapped records (each mine id replaced by its label); nothing of a record leaves this object except as
    counts and labels."""

    settings: dict[str, Any]
    settings_sha256: str
    input: dict[str, Any]
    split: dict[str, Any]
    pack: FrozenPack
    pack_dir: Path
    draft: dict[str, Any]
    audit: dict[str, Any]
    records: list[dict[str, Any]]
    weeks: tuple[str, ...]
    site_ids: list[str]
    master: dict[str, dict[str, tuple[str, ...]]]
    other_predicate: str
    language: Any


def audit_inputs(pack: FrozenPack, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], tuple[str, ...],
                                                                            list[str], dict[str, Any]]:
    """(records, weeks, site ids, master data) exactly as ``pilot.audit.audit`` builds them before its pipeline."""
    mapped = map_rows(rows, pack)
    records = list(mapped.records)
    days = sorted(r["received_date"][:10] for r in records)
    first, last = days[0], days[-1]
    records = [r for r in records if first <= r["received_date"][:10] <= last]
    weeks = tuple(replay_weeks(first, last))
    site_ids = sorted({r["site"] for r in records})
    return records, weeks, site_ids, A._master_data(pack, records, site_ids)


def prepare(raw: Path, work: Path, settings_path: Path | None = None, company: str = COMPANY) -> Data:
    """Rule 1: the split, the draft, the labelled held-out records and the audit (see the module docstring). Raises
    :class:`L1Error` (``pack``) when the drafted pack does not load or fails its privacy floor."""
    settings_path = settings_path or ROOT / SETTINGS
    settings, sha = S.load_settings(settings_path)
    demo = load_script(DEMO_SCRIPT)
    with contextlib.redirect_stdout(io.StringIO()):
        export, a = demo.step_export(settings_path, settings, raw, work, company)
    b = demo.step_draft(settings, export, company, work)
    if b["pack"] is None or not b["check"]["passed"]:
        raise L1Error("pack")
    pack = b["pack"]
    spec = settings["arms"][ARM]
    test = D.parse_window(*spec["test"])
    rows, _ = D.normalised_rows(export, b["roles"], b["draft"].dated, test, b["template"], b["draft"].etypes)
    key = b["template"]["ids.json"]["normalised"]["site"]
    labels = demo.mine_labels(r[key] for r in rows)
    named = [{**r, key: labels[r[key]]} for r in rows]
    doc = A.audit(pack, named, [])
    records, weeks, site_ids, master = audit_inputs(pack, named)
    languages = sorted({str(r.get("language")) for r in records})
    return Data(settings=settings, settings_sha256=sha, input=dict(a["input"]),
                split={"companies": a["companies"], "dir": a["split_dir"], "source": a["data"]["file"]},
                pack=pack, pack_dir=b["pack_dir"], draft=b, audit=doc, records=records, weeks=weeks,
                site_ids=site_ids, master=master,
                other_predicate=b["template"]["ids.json"]["other_predicate"],
                language=records[0].get("language") if len(languages) == 1 else None)


def alerts(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every X and S alert of the audit in its evaluated weeks, sorted by available date, channel and key (rule 2):
    ``{"channel", "week", "available_date", "key", "predicate"}``."""
    out = []
    for channel in COUNT_CHANNELS:
        ch = doc["channels"][channel]
        for a in ch.get("alert_timeline") or ():
            key = next(k for k in a["keys"] if k.count(":") == 2)
            out.append({"channel": channel, "week": a["week"], "available_date": a["available_date"], "key": key,
                        "predicate": key.split(":", 2)[2]})
    return sorted(out, key=lambda x: (x["available_date"], x["channel"], x["key"]))


def audit_counts(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The audit's counts every unit must reproduce (rule 1): records, mines, weeks evaluated and alerts per
    channel."""
    timeline = {ch: len(doc["channels"][ch].get("alert_timeline") or ()) for ch in COUNT_CHANNELS}
    return {"records": doc["export"]["records"], "mines": len(doc["export"]["sites"]),
            "weeks": doc["weeks"]["evaluated_weeks"], "alerts": timeline}


def specific_predicates(data: Data) -> list[str]:
    """The drafted pack's specific predicates (every predicate but the other bucket), sorted."""
    return sorted(p for p in data.pack.predicates if p != data.other_predicate)


def question_specs(data: Data, alert: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rule 3 and K11: slot 1 is the alert question; slots 2 to 5 are the other specific predicates, sorted. Each
    ``{"slot", "kind", "predicate", "key", "entity_type", "entity_id", "channel", "as_of"}``; the controls' window start
    comes from the alert question's window (:func:`with_window`)."""
    t, eid, predicate = alert["key"].split(":", 2)
    others = [p for p in specific_predicates(data) if p != predicate]
    if len(others) != CONTROLS:
        raise L1Error("questions")
    out = []
    for slot, (kind, p) in enumerate([("alert", predicate), *(("control", p) for p in others)], start=1):
        out.append({"slot": slot, "kind": kind, "predicate": p, "key": f"{t}:{eid}:{p}", "entity_type": t,
                    "entity_id": eid, "channel": alert["channel"], "as_of": alert["available_date"],
                    "alert_week": alert["week"], "window_start": None})
    return out


def with_window(specs: Sequence[Mapping[str, Any]], window: Mapping[str, str]) -> list[dict[str, Any]]:
    """The question specs with every question's window start set to the alert question's (rule 3)."""
    return [{**s, "window_start": window["start_week"]} for s in specs]


# --------------------------------------------------------------------------------------------------- runtimes

class FakeRuntime:
    """A lab judge with the surface ``SiteVerifier`` uses of a runtime: ``boundary`` (``site:<mine>``), ``exemption``
    (none: the call never leaves the mine), ``config.routes`` and ``run``. ``answer(payload)`` gives the reply; it
    never raises, so no failure path is taken. ``calls`` counts the records it judged."""

    class _Config:
        routes: dict[str, Any] = {}
        endpoints: dict[str, Any] = {}

    def __init__(self, site_id: str, answer: Callable[[Mapping[str, Any]], dict[str, str]]) -> None:
        self.boundary = f"site:{site_id}"
        self.config = self._Config()
        self._answer = answer
        self.calls = 0

    def exemption(self, task: Any, *, endpoint: str | None = None) -> None:
        return None

    def run(self, task: Any, payload: dict[str, Any], schema: Any, *, ref: str,
            endpoint: str | None = None) -> dict[str, str]:
        self.calls += 1
        return dict(self._answer(payload))


def key_answer(pack: FrozenPack) -> Callable[[Mapping[str, Any]], dict[str, str]]:
    """The key judge (rule 5): ``mentions_entity`` yes; ``describes_predicate`` yes exactly when one of the record's
    filed codes maps to the question's predicate in the pack's ``codes.json``."""
    def answer(payload: Mapping[str, Any]) -> dict[str, str]:
        predicate = payload["question"]["predicate"]
        filed = {pack.codes[c].predicate for c in payload["record"]["codes"] if c in pack.codes}
        return {"mentions_entity": "yes", "describes_predicate": "yes" if predicate in filed else "no"}
    return answer


def record_blind_answer(alert_question: bool) -> Callable[[Mapping[str, Any]], dict[str, str]]:
    """The record-blind control (rule 5, K1): yes and yes on every record of the alert question, yes and no on every
    record of a control question. It never reads the record."""
    reply = {"mentions_entity": "yes", "describes_predicate": "yes" if alert_question else "no"}
    return lambda payload: dict(reply)


def route_role_answer(role: str) -> Callable[[Mapping[str, Any]], dict[str, str]]:
    """The route-role baseline (K2): yes and yes on every record at a mine routed as contributing, yes and no at a
    sibling. It never reads the record."""
    reply = {"mentions_entity": "yes", "describes_predicate": "yes" if role == "contributing" else "no"}
    return lambda payload: dict(reply)


class Recorder:
    """Wraps one mine's ``Runtime.run`` (rule 10, K4): the call goes through unchanged and its reply, or its error
    kind, is kept by the record's index in the mine's retrieved list (from the verifier's ledger ref
    ``j:<question>:<i>``). ``calls`` counts the calls. Once the unit has stopped the path (:meth:`close`, before the
    runtimes close: K4's calls are counted when the unit ends), a late thread starts no call, and a call that was in
    flight and ends after it is kept with ``after_close`` (its ledger row may be missing: the ledger closed under it);
    such a call is no judgement of the unit."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.calls = 0
        self.closed = False
        self.replies: dict[int, dict[str, Any]] = {}
        self._run = runtime.run
        self._lock = threading.Lock()
        runtime.run = self.run

    def close(self) -> None:
        with self._lock:
            self.closed = True

    def run(self, task: Any, payload: dict[str, Any], schema: Any, *, ref: str, endpoint: str | None = None) -> Any:
        with self._lock:
            if self.closed:
                raise RuntimeError("the unit stopped the path")
            self.calls += 1
        m = REF_RE.fullmatch(ref)
        index = int(m.group(1)) if m is not None else -1
        try:
            reply = self._run(task, payload, schema, ref=ref, endpoint=endpoint)
        except Exception as err:
            self.replies[index] = {"reply": None, "error_kind": getattr(err, "kind", "error"), "ref": ref,
                                   "after_close": self.closed}
            raise
        self.replies[index] = {"reply": dict(reply), "error_kind": None, "ref": ref, "after_close": self.closed}
        return reply


# --------------------------------------------------------------------------------------------------- timing

class SimClock:
    """The clock every verifier and the orchestrator read: the question's ``as_of`` at noon."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


class Timing:
    """When the orchestrator called each mine's handler and when it returned (``time.perf_counter``), in call
    order. :meth:`wrap` gives the handler that records it; the handler is otherwise the verifier's own."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def wrap(self, site: str, handler: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        def timed(body: dict[str, Any]) -> Any:
            entry = {"site": site, "start": time.perf_counter(), "end": None}
            with self._lock:
                self.calls.append(entry)
            try:
                return handler(body)
            finally:
                entry["end"] = time.perf_counter()
        return timed


def stages(t0: float, t1: float, calls: Sequence[Mapping[str, Any]], deadline_s: float) -> dict[str, Any]:
    """Rule 6's stages of one call that returned at ``t1`` (K6): the question build (the call's start to the first
    mine's start), each mine (its handler's start to its return; a mine still running at its deadline, or whose return
    came after the call returned, ends at its start plus the deadline), each gap between one mine's end and the next
    mine's start, and the gate (the last mine's end to the call's return). A mine is ``contended`` when its handler ran
    while an earlier mine's handler of the same question was still running. The parts sum to the time to answer."""
    mines = []
    for i, c in enumerate(calls):
        late = c["end"] is None or c["end"] > t1 or c["end"] - c["start"] > deadline_s
        end = c["start"] + deadline_s if late else c["end"]
        contended = any(e["end"] is None or e["end"] > c["start"] for e in calls[:i])
        mines.append({"mine": c["site"], "start": c["start"], "end": end, "timed_out": late, "contended": contended,
                      "seconds": end - c["start"]})
    if not mines:
        return {"question_build_s": t1 - t0, "mines": [], "between_s": [], "gate_s": 0.0, "time_to_answer_s": t1 - t0}
    between = [mines[i + 1]["start"] - mines[i]["end"] for i in range(len(mines) - 1)]
    return {"question_build_s": mines[0]["start"] - t0, "mines": mines, "between_s": between,
            "gate_s": t1 - mines[-1]["end"], "time_to_answer_s": t1 - t0}


# --------------------------------------------------------------------------------------------------- one path

@dataclass
class World:
    """One path's fresh pipeline (HQ's store and the mines' stores) and, for every judge but the key, the mines'
    stores with the codes hidden."""

    pipeline: Any
    hidden: dict[str, EdgeSite]
    clock: SimClock
    workdir: Path

    def close(self) -> None:
        for site in self.hidden.values():
            site.close()
        self.pipeline.close()


def hidden_sites(data: Data, workdir: Path, clock: SimClock) -> dict[str, EdgeSite]:
    """Rule 4: each mine's records as ingested, with ``codes`` emptied, in a store of its own, extracted lexically as
    the pipeline extracts them. Raises :class:`L1Error` (``hidden``) on any rejection."""
    out: dict[str, EdgeSite] = {}
    try:
        for sid in data.site_ids:
            site = out[sid] = EdgeSite(data.pack, sid, workdir / "verify", runtime=None, clock=clock,
                                       master_data=data.master[sid], hq_dir=workdir / "verify-hq")
            got = site.ingest([{**r, "codes": []} for r in data.records if r["site"] == sid])
            if got.duplicates or sum(got.rejected.values()):
                raise L1Error("hidden")
            site.extract("lexical")
    except BaseException:
        for site in out.values():
            site.close()
        raise
    return out


def build_world(data: Data, workdir: Path, as_of: str) -> World:
    """A fresh pipeline in ``workdir`` (rule 1's pipeline) and the hidden mine stores, the clock at ``as_of``."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    clock = SimClock(f"{as_of}T12:00:00Z")
    pipeline = run_pipeline(data.pack, data.records, site_ids=data.site_ids, master_data=data.master,
                            weeks=data.weeks, workdir=workdir / "pipeline", enterprise=ENTERPRISE)
    try:
        hidden = hidden_sites(data, workdir, clock)
    except BaseException:
        pipeline.close()
        raise
    return World(pipeline=pipeline, hidden=hidden, clock=clock, workdir=workdir)


def alert_run(world: World, spec: Mapping[str, Any]) -> str:
    """Rule 2: HQ's detection on the alert's available date, in the alert's channel, saved in HQ's store; the run must
    list the alert in its week and store its candidate with the snapshot at that week (else ``alert_run``)."""
    store = world.pipeline.store
    result = detect(store, as_of=spec["as_of"], run_channel=spec["channel"], tie_salt=TIE_SALT)
    if not any(a["week"] == spec["alert_week"] and a["key"] == spec["key"] for a in result["alerts"]):
        raise L1Error("alert_run")
    store.save_run(result)
    candidate = store.candidate(result["run_id"], spec["key"])
    snapshot = candidate.get("snapshot") if candidate is not None else None
    if snapshot is None or snapshot["week"] != spec["alert_week"] or snapshot["as_of"] != spec["as_of"]:
        raise L1Error("alert_run")
    return result["run_id"]


def ask(world: World, orchestrator: Orchestrator, spec: Mapping[str, Any], run_id: str | None) -> Any:
    """The question's call: ``verify_stored`` for the alert question, ``verify_candidate`` of a constructed candidate
    for a control (rule 3)."""
    if spec["kind"] == "alert":
        return orchestrator.verify_stored(run_id, spec["key"])
    candidate = constructed_candidate(world.pipeline.store, entity_type=spec["entity_type"],
                                      entity_id=spec["entity_id"], predicate=spec["predicate"], as_of=spec["as_of"],
                                      run_channel=spec["channel"], window_start=spec["window_start"])
    return orchestrator.verify_candidate(candidate, as_of=spec["as_of"])


def site_verdicts(store: Any, question_id: str, as_of: str) -> dict[str, dict[str, Any]]:
    """Each routed mine's latest verdict at HQ (the one the gate uses): ``{"verdict", "reason", "source", "quality",
    "support_bucket", "seq"}``; the reason is the wire reason, ``degraded``, ``unclear`` (a site's unknown with
    neither), ``timeout`` or ``error``."""
    out: dict[str, dict[str, Any]] = {}
    for v in store.pd_verdicts(question_id):
        if v.received_as_of > as_of:
            continue
        body = strict_load(v.body)
        if v.source == "hq":
            entry = {"verdict": "unknown", "reason": body["reason"], "source": "hq", "quality": None,
                     "support_bucket": None}
        else:
            reason = body.get("reason")
            if body["verdict"] == "unknown" and reason is None:
                reason = "degraded" if body.get("quality") == "degraded" else "unclear"
            entry = {"verdict": body["verdict"], "reason": reason, "source": "site", "quality": body.get("quality"),
                     "support_bucket": body.get("support_bucket")}
        if v.site not in out or v.seq >= out[v.site]["seq"]:
            out[v.site] = {**entry, "seq": v.seq}
    return out


def strata(store: Any, spec: Mapping[str, Any], window: Mapping[str, str], routes: Mapping[str, str]) -> dict[str, Any]:
    """K2's strata, fixed by HQ's cells: each routed mine is ``codes`` (contributing with a ``codes`` cell of the key in
    the window), ``text_only`` (contributing with only ``text_only`` cells of it) or ``sibling``; and which codes
    contributors also sent a ``text_only`` cell."""
    cells = store.key_cells(spec["entity_type"], spec["entity_id"], spec["predicate"], window["start_week"],
                            window["end_week"], spec["as_of"], RUN_CHANNELS[spec["channel"]])
    channels: dict[str, set[str]] = {}
    for c in cells:
        channels.setdefault(c.site, set()).add(c.channel)
    out, both = {}, []
    for site, role in sorted(routes.items()):
        if role == "sibling":
            out[site] = "sibling"
        elif "codes" in channels.get(site, ()):
            out[site] = "codes"
            if "text_only" in channels[site]:
                both.append(site)
        else:
            out[site] = "text_only"
    return {"strata": out, "codes_with_text_only": both}


@dataclass
class PathResult:
    """One judge's path on one question (see the module docstring)."""

    question_id: str
    window: dict[str, str]
    routes: dict[str, str]
    first: dict[str, dict[str, Any]]
    status_first: str
    final: dict[str, dict[str, Any]]
    status_final: str
    timing: dict[str, Any] | None
    time_to_final_s: float | None
    late_still_running: int
    calls: dict[str, int]
    question: dict[str, Any]
    gate_records: list[Any] = field(default_factory=list)
    recorders: dict[str, Recorder] = field(default_factory=dict)
    verifiers: dict[str, SiteVerifier] = field(default_factory=dict)
    finished: bool = True


def _probe_handler(body: dict[str, Any]) -> Any:
    raise RuntimeError("probe")


def run_path(data: Data, spec: Mapping[str, Any], judge: str, workdir: Path, *,
             routes: Mapping[str, str] | None = None, runtimes: Mapping[str, Any] | None = None,
             late_wait_s: float = 0.0, on_answer: Callable[[], float] | None = None, keep_world: bool = False,
             deadline_s: float = DEADLINE_S) -> tuple[PathResult, World | None]:
    """One judge (``probe``, one of :data:`JUDGES`, or ``model`` with ``runtimes``: a Runtime per mine) on one
    question, on a fresh world. ``routes`` (the probe's) are needed by the route-role baseline. ``on_answer``, called
    as soon as the call returned (the first decision), gives the seconds left for the late wait (else
    ``late_wait_s``). Returns the result and, with ``keep_world``, the open world (the caller closes it); else the world
    is closed and its directory removed."""
    world = build_world(data, workdir, spec["as_of"])
    try:
        run_id = alert_run(world, spec) if spec["kind"] == "alert" else None
        timing = Timing()
        recorders: dict[str, Recorder] = {}
        verifiers: dict[str, SiteVerifier] = {}
        fakes: dict[str, FakeRuntime] = {}
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        for sid in data.site_ids:
            if judge == "probe":
                handlers[sid] = _probe_handler
                continue
            site = world.pipeline.sites[sid] if judge == "key" else world.hidden[sid]
            runtime: Any = None
            if judge == "key":
                runtime = fakes[sid] = FakeRuntime(sid, key_answer(data.pack))
            elif judge == "record_blind":
                runtime = fakes[sid] = FakeRuntime(sid, record_blind_answer(spec["kind"] == "alert"))
            elif judge == "route_role":
                role = (routes or {}).get(sid, "sibling")
                runtime = fakes[sid] = FakeRuntime(sid, route_role_answer(role))
            elif judge == "model":
                runtime = runtimes[sid]
                recorders[sid] = Recorder(runtime)
            elif judge != "lexical":
                raise ValueError("unknown judge")
            verifier = verifiers[sid] = SiteVerifier(site, runtime=runtime, clock=world.clock, demo_seed=DEMO_SEED)
            handlers[sid] = timing.wrap(sid, verifier.answer)
        orchestrator = Orchestrator(world.pipeline.store, handlers=handlers, clock=world.clock,
                                    deadline_seconds=deadline_s)
        t0 = time.perf_counter()
        conclusion = ask(world, orchestrator, spec, run_id)
        t1 = time.perf_counter()
        if on_answer is not None:
            late_wait_s = on_answer()
        store = world.pipeline.store
        qid = conclusion.question_id
        question = strict_load(store.question(qid).body)
        window = dict(question["window"])
        path_routes = {r.site: r.role for r in store.routes(qid)}
        first = site_verdicts(store, qid, spec["as_of"])
        status_first = conclusion.status
        gate_records = [VerdictRecord(site=v.site, seq=v.seq, source=v.source, body=strict_load(v.body),
                                      received_as_of=v.received_as_of) for v in store.pd_verdicts(qid)]
        stage = stages(t0, t1, list(timing.calls), deadline_s) if judge != "probe" else None
        late_still, t2 = 0, None
        status_final, final = status_first, first
        if orchestrator.late_deliveries:
            late_still = orchestrator.join_late(max(0.0, late_wait_s))
            orchestrator.collect_late(as_of=spec["as_of"])
            latest = orchestrator.conclusion(conclusion.conclusion_id)
            status_final = latest.status if latest is not None else status_first
            final = site_verdicts(store, qid, spec["as_of"])
            t2 = time.perf_counter()
        calls = {sid: (recorders[sid].calls if sid in recorders else fakes[sid].calls if sid in fakes else 0)
                 for sid in sorted(path_routes)}
        result = PathResult(question_id=qid, window=window, routes=path_routes, first=first,
                            status_first=status_first, final=final, status_final=status_final, timing=stage,
                            time_to_final_s=(t2 - t0) if t2 is not None else None, late_still_running=late_still,
                            calls=calls, question=question, gate_records=gate_records, recorders=recorders,
                            verifiers=verifiers)
    except BaseException:
        world.close()
        raise
    if keep_world:
        return result, world
    world.close()
    shutil.rmtree(workdir, ignore_errors=True)
    return result, None


def default_deadline(pack: FrozenPack, result: PathResult, as_of: str,
                     deadline_s: float = DEFAULT_DEADLINE_S) -> dict[str, Any]:
    """Rule 6, derived and deciding nothing: had the deadline been 600 s, each mine over it counts as 600 s and as an
    HQ timeout record, and the pinned gate (``gate.evaluate``) decides on the rest."""
    timing = result.timing or {"mines": [], "question_build_s": 0.0, "between_s": [], "gate_s": 0.0}
    over = {m["mine"] for m in timing["mines"] if m["seconds"] > deadline_s}
    records = []
    for r in result.gate_records:
        if r.received_as_of > as_of:
            continue
        if r.site in over:
            continue
        records.append(r)
    for site in sorted(over):
        records.append(VerdictRecord(site=site, seq=1 + max((r.seq for r in result.gate_records if r.site == site),
                                                            default=0),
                                     source="hq", body=hq_record_body(result.question_id, site, "timeout"),
                                     received_as_of=as_of))
    status = evaluate(pack, result.question, result.routes, records, as_of=as_of).status
    seconds = (timing["question_build_s"] + sum(min(m["seconds"], deadline_s) for m in timing["mines"])
               + sum(timing["between_s"]) + timing["gate_s"])
    return {"seconds": seconds, "status": status, "timeouts": len(over)}


def at_once(result: PathResult) -> float | None:
    """Rule 6, derived and deciding nothing: the question build, plus the slowest mine, plus the gate."""
    t = result.timing
    if t is None:
        return None
    return t["question_build_s"] + max((m["seconds"] for m in t["mines"]), default=0.0) + t["gate_s"]


# --------------------------------------------------------------------------------------------------- retrieval

def retrieved(site: EdgeSite, spec: Mapping[str, Any], window: Mapping[str, str], pack: FrozenPack) -> list[Any]:
    """The records the mine's verifier retrieves for the question, newest first, cut at ``verify_max_records``
    (``edge.verify.retrieve`` on a connection of the mine's store)."""
    store = RecordStore(site.store.path, site_id=site.site_id, pack_id=pack.id, config_hash=pack.config_hash)
    try:
        records, _ = retrieve(store, site.canonicaliser, entity_type=spec["entity_type"], entity_id=spec["entity_id"],
                              window=window, cap=pack.egress.verify_max_records)
    finally:
        store.close()
    return records


def retrieval_check(data: Data, world: World, spec: Mapping[str, Any], window: Mapping[str, str],
                    routes: Mapping[str, str]) -> dict[str, Any]:
    """Rule 4 and K4: at every routed mine, the hidden store retrieves the same records, in the same order, as the
    unchanged store; per mine, the count, and per record (by index) whether it was filed under the question's
    predicate (``positive``). ``same`` is False when any mine differs."""
    counts: dict[str, int] = {}
    positives: dict[str, list[bool]] = {}
    same = True
    for sid in sorted(routes):
        mine = retrieved(world.pipeline.sites[sid], spec, window, data.pack)
        hidden = retrieved(world.hidden[sid], spec, window, data.pack)
        if [r.record_ref for r in mine] != [r.record_ref for r in hidden] or any(r.codes for r in hidden):
            same = False
        counts[sid] = len(hidden)
        positives[sid] = [any(c in data.pack.codes and data.pack.codes[c].predicate == spec["predicate"]
                              for c in r.codes) for r in mine]
    return {"same": same, "records": counts, "positives": positives}


def lexical_replies(data: Data, world: World, spec: Mapping[str, Any], question: Mapping[str, Any],
                    routes: Mapping[str, str]) -> dict[str, list[dict[str, str]]]:
    """Rule 10: the lexical judge's reply on every record each routed mine retrieves, from the same payloads a model
    is sent (the hidden store's records), by index."""
    handlers = {sid: lexical_judge(data.pack, world.hidden[sid].canonicaliser) for sid in routes}
    out = {}
    for sid in sorted(routes):
        out[sid] = [handlers[sid](judge_payload(data.pack, question, rec))
                    for rec in retrieved(world.hidden[sid], spec, question["window"], data.pack)]
    return out


def confirm_shares(data: Data, world: World, result: PathResult, predicate: str) -> dict[str, float | None]:
    """Rule 10: for each counted confirm, the share of its confirming records, as its mine's own ``resolve`` lists
    them, that were filed under the predicate (the codes from the mine's unchanged store)."""
    out: dict[str, float | None] = {}
    for sid, v in sorted(result.first.items()):
        if v["verdict"] != "confirm" or v["support_bucket"] in (None, "<k") or sid not in result.verifiers:
            continue
        body = next((strict_load(r.body) if not isinstance(r.body, dict) else r.body for r in result.gate_records
                     if r.site == sid and r.source == "site"), None)
        resolution = result.verifiers[sid].resolve(body["evidence_ref"]) if body is not None else None
        if resolution is None or not resolution.record_refs:
            out[sid] = None
            continue
        codes = {r.record_ref: r.codes for r in world.pipeline.sites[sid].store.window_records(
            resolution.window[0], resolution.window[1])}
        filed = sum(1 for ref in resolution.record_refs
                    if any(c in data.pack.codes and data.pack.codes[c].predicate == predicate
                           for c in codes.get(ref, ())))
        out[sid] = filed / len(resolution.record_refs)
    return out


def crossing_overlap(data: Data, world: World) -> dict[str, int]:
    """Rule 10: the narrative overlap of every artifact that crossed between HQ and the mines on this path (the
    questions and the verdicts), scanned as E2 scans pushdown's artifacts (``leakage.scan``, narrative shingles,
    an empty canary manifest)."""
    hq = world.workdir / "verify-hq"
    artifacts = []
    if (hq / "questions.jsonl").exists():
        artifacts.append(Artifact("pushdown", "verify-hq/questions.jsonl", path=hq / "questions.jsonl"))
    for number, row in enumerate(read_log(hq / "receive.jsonl"), start=1):
        if row["artifact_type"] == "verdict":
            artifacts.append(Artifact("pushdown", f"verify-hq/receive.jsonl#{number}",
                                      data=canonical_bytes(row["body"])))
    edge = world.workdir / "verify"
    for sid in data.site_ids:
        for name in (f"site-{sid}.ingress.jsonl", f"site-{sid}.egress.jsonl"):
            if (edge / name).exists():
                artifacts.append(Artifact("pushdown", f"verify/{name}", path=edge / name))
    if not artifacts:
        return {"overlap_bytes": 0, "bytes": 0, "items": 0}
    manifest = Manifest(pack_id=data.pack.id, config_hash=data.pack.config_hash, prefix=CANARY_PREFIX, canaries=())
    report = scan(artifacts, manifest, [r["narrative"] for r in data.records], data.pack)
    overlap = sum(hit["bytes"] for hit in report["shingle_hits"])
    entry = report["artifact_classes"].get("pushdown", {"bytes": 0, "items": 0})
    return {"overlap_bytes": overlap, "bytes": entry["bytes"], "items": entry["items"]}


def read_rows(path: Path) -> list[dict[str, Any]]:
    """A ledger's complete rows (``read_ledger``'s check), leaving out a last line still being written."""
    if not path.exists():
        return []
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data = data[:data.rfind(b"\n") + 1]
    tmp = path.with_name(path.name + ".complete")
    tmp.write_bytes(data)
    try:
        return read_ledger(tmp)
    finally:
        tmp.unlink()


def ledger_by_mine(paths: Mapping[str, Path]) -> dict[str, list[dict[str, Any]]]:
    """Each mine's ledger rows (an absent ledger is none)."""
    return {sid: read_rows(p) for sid, p in sorted(paths.items())}
