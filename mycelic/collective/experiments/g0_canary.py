"""G0 runner v1: plant canaries in a synthetic world, run every stage, and scan every byte that crossed a boundary.

    python -m mycelic.collective.experiments.g0_canary --pack P --records N --seed S --out DIR
        [--mode fake|lexical|routing] [--routing FILE] [--require-master-data on|off] [--dry-run]

Flow: load the pack (``--require-master-data`` that differs from the pack's frozen setting writes a pack copy to
``DIR/pack`` with that one field changed, so the override has its own ``config_hash``); generate a world of at least
N records and keep the first N; plant canaries (``leakage.plant_canaries``) and write the manifest to
``DIR/private/manifest.json``, outside every scanned path; run each stage of :data:`STAGES` under a simulated clock
(ingest the day after the last record, emit when every record week and the ingest week are closed); scan what the
stages say crossed, with the site ledgers as a separate hygiene class; scan the first site's database as a positive
control (the scanner must find canaries and narrative text there, or the run fails); write ``DIR/leakage.json``.

Stages: ``edge`` (each site ingests, extracts and emits its cells and usage), ``pushdown`` (G6), ``followup`` (G7)
and ``run_files`` (G8). ``pushdown``: HQ's collective
store at ``DIR/hqdb/collective.sqlite3`` (outside ``hq/``, so the edge stage's ``hq`` artifact still covers only the
transport logs) ingests the receive log, detects (run X, tie salt ``g0``) and verifies up to
:data:`G0_MAX_CANDIDATES` detector candidates by ``(-score, key)``, topped up to :data:`G0_MIN_CANDIDATES` with
constructed candidates for the keys with cells at the most sites, all at the run's ``as_of`` (every site emits once,
there, so no cell is visible earlier); every site answers with a ``SiteVerifier`` under a
seeded demo secret. Its crossing artifacts are every question (``hq/questions.jsonl``), every verdict row of the
receive log, the HQ database and its ``-wal`` (read while the store is open) and each site's ingress log;
``leakage.json`` gains ``pushdown_totals``.

``followup`` (G7; built ahead of E2 and X4, unvalidated): under ``DIR/followup/`` it writes ``approvers.json`` (one
simulated owner ``g0-<role>`` per pack role, at the enterprise unit), ``kill.json`` (off), the follow-up ledger
``followups.sqlite3`` and ``outbox.jsonl``. For each conclusion the pushdown stage verified whose latest status is
``supported``, and each enabled T0 or T1 type in ``(tier, id)`` order (so drafts see the packets), it fills the args
generically (a harness choice, not a system one: the conclusion id, the key's entity id when the arg's type is the
key's type, else the type is skipped, the key's predicate, an enum's first value, an integer's minimum), proposes as
the system principal, approves as the simulated owner at the latest version (``simulated_approvals: true``) and
executes: T0 asks every target site's ``PacketAssembler`` for its packet, T1 drafts at HQ (``--mode fake``: a central
fake runtime replaying the template drafter, with its usage ledger at ``DIR/followup/central.ledger.jsonl``; other
modes: the template drafter) and appends the approved draft to the outbox. Its crossing artifacts are every packet row
of the receive log, every packet request (``hq/packet_requests.jsonl``), every drafted or edited draft, the ledger file,
the outbox and the central draft ledger; each site's ``packets/`` directory (the full packets, narratives included)
stays inside the site and is never scanned as crossing. ``leakage.json`` gains ``followup_totals``.

``run_files`` (G8) writes ``DIR/run/`` with ``runfiles.write_run_files`` (refused, writing nothing, when a file holds
an absolute path, the output directory, the repository root, the home directory, the host or user name, a 64-hex
token, a credential key or an agent-key prefix): ``ledger.jsonl`` (``runfiles.project_ledger`` over every site ledger
and the central draft ledger, 12 rows per site and task), ``approvals.jsonl`` (``runfiles.project_entries`` over the
follow-up ledger), ``trace.json`` (the stages, every verified question with its display text, and every conclusion
with its used verdicts, read through ``HqReader`` and, for the verdict bodies, the receive log) and ``scorecard.json``
(the pack digests, the stage totals and the ledger summary; every digest 32 hex). Each of the four files crosses as
class ``run_files``; ``leakage.json`` gains ``run_files_totals``.

Modes: ``fake`` (default) extracts and judges through an in-process fake model at each site, so a ledger and a usage
summary exist; ``lexical`` uses no model (no ledger); ``routing`` uses a routing file whose extraction and judge
endpoints are all ``any-simulated`` (one machine plays every simulated site). Everything is synthetic, and nothing
here measures a model.

Exit 0 when no canary and no narrative shingle crossed, the site ledgers are clean and the positive control found
both; 1 otherwise; 2 on a usage or configuration error. ``known_limitation`` entries (class-c ids leaving as cell
keys with ``require_master_data`` off) do not fail the run. The one wall-clock value is ``created_at``.
"""
from __future__ import annotations

import argparse
import getpass
import math
import os
import random
import shutil
import socket
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import runfiles
from ..detect.detectors import detect
from ..detect.store import CollectiveStore, HqReader
from ..edge.egress import read_log
from ..edge.extract import TASK_NAME, lexical_handler
from ..edge.packets import PacketAssembler
from ..edge.site import EdgeSite
from ..edge.verify import JUDGE_TASK, SiteVerifier, lexical_judge
from ..evaluate.baselines import org_for_sites
from ..followup.drafts import DRAFT_TASK, DraftWriter, template_draft
from ..followup.executors import OutboxExecutor, PacketExecutor
from ..followup.ledger import LEDGER_FILE, FollowupLedger
from ..followup.policy import BUILT_AHEAD_LABEL, SYSTEM, KillSwitch, human
from ..followup.service import ConclusionView, FollowupRefused, FollowupService
from ..inference.fake import FakeProvider
from ..inference.ledger import read_ledger
from ..inference.routing import ConfigError, RoutingConfig, load_routing, missing_env, parse_routing
from ..inference.runtime import Runtime
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, strict_load
from ..leakage import Artifact, LeakageError, plant_canaries, scan, write_manifest
from ..packs.canonical import Canonicaliser
from ..packs.generator import GeneratorError, generate, world_digest
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack
from ..pushdown.gate import STATUSES
from ..pushdown.orchestrator import Orchestrator, constructed_candidate
from ..pushdown.questions import render_text
from .common import ROOT, DryRun, UsageError, code_stamps, fail, utc_clock, write_json_atomic

CLI = "experiments.g0_canary"
MODES = ("fake", "lexical", "routing")
MAX_RECORDS = 100000
MAX_SEED = 10 ** 12
MAX_WEEKS = 520
DAY_TIME = "T08:00:00.000Z"
CROSSING_CLASSES = ("cells", "hq_receive_log", "site_egress_log", "usage_summary", "questions", "verdicts",
                    "collective_sqlite3", "site_ingress_log", "packets", "packet_requests", "drafts",
                    "approvals_ledger", "outbox", "hq_draft_ledger", "run_files")
ROUTED_TASKS = (TASK_NAME, JUDGE_TASK)
G0_MAX_CANDIDATES = 20
G0_MIN_CANDIDATES = 5
G0_TIE_SALT = "g0"
G0_ENTERPRISE = "g0"
TOTAL_KEYS = ("records_ingested", "rejected", "duplicates", "forwarded_in", "late", "extracted", "claims", "cells",
              "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type", "usage_groups")


class SimClock:
    """The simulated clock every stage receives; never the wall clock."""

    def __init__(self, value: str) -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@dataclass
class G0Context:
    pack: FrozenPack
    records: tuple[dict[str, Any], ...]
    master_data: Mapping[str, Mapping[str, tuple[str, ...]]]
    out: Path
    clock: SimClock
    as_of: str
    emit_at: str
    mode: str
    seed: int
    routing: RoutingConfig | None
    sites: tuple[str, ...]
    runtimes: dict[str, Runtime] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOTAL_KEYS, 0))
    pushdown_totals: dict[str, Any] = field(default_factory=dict)
    conclusion_ids: list[str] = field(default_factory=list)
    followup_totals: dict[str, Any] = field(default_factory=dict)
    run_files_totals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage:
    """``run`` returns (artifacts that crossed a boundary, hygiene artifacts that must stay clean)."""

    name: str
    run: Callable[[G0Context], tuple[list[Artifact], list[Artifact]]]


def _runtime(ctx: G0Context, site_id: str) -> Runtime | None:
    ledger = ctx.out / "edge" / f"site-{site_id}.ledger.jsonl"
    boundary = f"site:{site_id}"
    if ctx.mode == "lexical":
        return None
    if ctx.mode == "routing":
        return Runtime(ctx.routing, boundary=boundary, ledger_path=ledger, run_id=f"g0-{ctx.seed}", clock=ctx.clock,
                       data_label="synthetic", simulation=True, environ=os.environ)
    config = parse_routing({"schema_version": 1,
                            "endpoints": {"site-fake": {"provider": "fake", "boundary": boundary}},
                            "routes": {task: {"endpoint": "site-fake"} for task in ROUTED_TASKS}}, allow_fake=True)
    provider = FakeProvider()
    canonicaliser = Canonicaliser(ctx.pack, known=ctx.master_data[site_id])
    provider.register(TASK_NAME, lexical_handler(ctx.pack, canonicaliser))
    provider.register(JUDGE_TASK, lexical_judge(ctx.pack, canonicaliser))
    return Runtime(config, boundary=boundary, ledger_path=ledger, run_id=f"g0-{ctx.seed}", clock=ctx.clock,
                   data_label="synthetic", allow_fake=True, fake=provider, sleep=lambda s: None, environ={})


def edge_stage(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """Each site ingests and extracts its own records, then every site emits its cells and usage."""
    edge, hq = ctx.out / "edge", ctx.out / "hq"
    sites: list[EdgeSite] = []
    t = ctx.totals
    try:
        for site_id in ctx.sites:
            runtime = _runtime(ctx, site_id)
            if runtime is not None:
                ctx.runtimes[site_id] = runtime
            site = EdgeSite(ctx.pack, site_id, edge, runtime=runtime, clock=ctx.clock,
                            master_data=ctx.master_data[site_id], hq_dir=hq)
            sites.append(site)
            got = site.ingest([r for r in ctx.records if r["site"] == site_id])
            t["records_ingested"] += got.ingested
            t["rejected"] += sum(got.rejected.values())
            t["duplicates"] += got.duplicates
            t["forwarded_in"] += got.forwarded_in
            t["late"] += got.late
            summary = site.extract("lexical" if runtime is None else "model")
            t["extracted"] += summary.records
            t["claims"] += summary.claims
        ctx.clock.set(ctx.emit_at)
        for site in sites:
            cells = site.emit_cells(ctx.as_of)
            if cells is not None:
                for key in ("cells", "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type"):
                    t[key] += cells.stats[key]
            usage = site.emit_usage(ctx.as_of)
            if usage is not None:
                t["usage_groups"] += len(usage.body["groups"])
    finally:
        for site in sites:
            site.close()
        for runtime in ctx.runtimes.values():
            runtime.close()
    crossing = [Artifact("site_egress_log", f"edge/site-{s}.egress.jsonl", path=edge / f"site-{s}.egress.jsonl")
                for s in ctx.sites]
    crossing.append(Artifact("hq_receive_log", "hq", path=hq))
    for number, row in enumerate(read_log(hq / "receive.jsonl"), start=1):
        artifact_class = "cells" if row["artifact_type"] == "cells_bundle" else "usage_summary"
        crossing.append(Artifact(artifact_class, f"hq/receive.jsonl#{number}", data=canonical_bytes(row["body"])))
    hygiene = [Artifact("site_ledger_hygiene", f"edge/site-{s}.ledger.jsonl", path=edge / f"site-{s}.ledger.jsonl")
               for s in ctx.sites if (edge / f"site-{s}.ledger.jsonl").exists()]
    return crossing, hygiene


def _constructed_keys(store: CollectiveStore, as_of: str, skip: set[str]) -> list[tuple[str, str, str]]:
    """Keys with visible X cells, by (-sites, -lower-bound volume, key), the verified ones left out."""
    _, cells = store.detection_inputs(as_of, "X")
    sites: dict[tuple[str, str, str], set[str]] = {}
    volume: dict[tuple[str, str, str], int] = {}
    for c in cells:
        key = (c.entity_type, c.entity_id, c.predicate)
        sites.setdefault(key, set()).add(c.site)
        volume[key] = volume.get(key, 0) + (c.n if c.n is not None else 1)
    ranked = sorted(sites, key=lambda key: (-len(sites[key]), -volume[key], ":".join(key)))
    return [key for key in ranked if ":".join(key) not in skip]


def pushdown_stage(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """HQ detects over what the edge stage sent, then asks the sites about its candidates (G6)."""
    edge, hq, hqdb = ctx.out / "edge", ctx.out / "hq", ctx.out / "hqdb"
    hqdb.mkdir(parents=True, exist_ok=True)
    store = CollectiveStore(hqdb / "collective.sqlite3", ctx.pack, org_for_sites(ctx.sites, G0_ENTERPRISE),
                            clock=ctx.clock)
    sites: list[EdgeSite] = []
    runtimes: list[Runtime] = []
    crossing: list[Artifact] = []
    try:
        store.ingest_log(hq / "receive.jsonl")
        result = detect(store, as_of=ctx.as_of, run_channel="X", tie_salt=G0_TIE_SALT)
        store.save_run(result)
        handlers = {}
        for site_id in ctx.sites:
            runtime = _runtime(ctx, site_id)
            if runtime is not None:
                runtimes.append(runtime)
            site = EdgeSite(ctx.pack, site_id, edge, runtime=None, clock=ctx.clock,
                            master_data=ctx.master_data[site_id], hq_dir=hq)
            sites.append(site)
            handlers[site_id] = SiteVerifier(site, runtime=runtime, clock=ctx.clock, demo_seed=ctx.seed).answer
        orchestrator = Orchestrator(store, handlers=handlers, clock=ctx.clock)
        detected = sorted((c for c in result["candidates"] if c["snapshot"] is not None),
                          key=lambda c: (-c["snapshot"]["score"], c["key"]))[:G0_MAX_CANDIDATES]
        # every site emits once, at ctx.as_of, so a cell is visible from there on: verify at the run's as_of
        conclusions = [orchestrator.verify_stored(result["run_id"], c["key"], as_of=ctx.as_of) for c in detected]
        constructed = 0
        for t, eid, predicate in _constructed_keys(store, ctx.as_of, {c["key"] for c in detected}):
            if len(conclusions) >= G0_MIN_CANDIDATES:
                break
            candidate = constructed_candidate(store, entity_type=t, entity_id=eid, predicate=predicate,
                                              as_of=ctx.as_of)
            conclusions.append(orchestrator.verify_candidate(candidate, as_of=ctx.as_of))
            constructed += 1
        ctx.conclusion_ids.extend(c.conclusion_id for c in conclusions)
        verdict_rows = [row for row in read_log(hq / "receive.jsonl") if row["artifact_type"] == "verdict"]
        question_rows = [row for row in read_log(hq / "questions.jsonl") if row["artifact_type"] == "question"]
        question_ids = sorted({c.question_id for c in conclusions})
        verdicts = dict.fromkeys(("confirm", "refute", "unknown"), 0)
        for row in verdict_rows:
            verdicts[row["body"]["verdict"]] += 1
        statuses = dict.fromkeys(STATUSES, 0)
        for c in conclusions:
            statuses[c.status] += 1
        ctx.pushdown_totals.update({
            "judge": ctx.mode, "secret_mode": "seeded-demo", "candidates": len(detected),
            "constructed_candidates": constructed, "questions": len(question_ids),
            "routes": sum(len(store.routes(q)) for q in question_ids), "verdicts": verdicts, "statuses": statuses})
        crossing += [Artifact("questions", f"hq/questions.jsonl#{number}", data=canonical_bytes(row["body"]))
                     for number, row in enumerate(question_rows, start=1)]
        crossing += [Artifact("verdicts", f"hq/receive.jsonl#verdict-{number}", data=canonical_bytes(row["body"]))
                     for number, row in enumerate(verdict_rows, start=1)]
        db = hqdb / "collective.sqlite3"
        for path in (db, db.with_name(db.name + "-wal")):
            if path.exists():
                crossing.append(Artifact("collective_sqlite3", f"hqdb/{path.name}", data=path.read_bytes()))
    finally:
        for site in sites:
            site.close()
        for runtime in runtimes:
            runtime.close()
        store.close()
    crossing += [Artifact("site_ingress_log", f"edge/site-{s}.ingress.jsonl", path=edge / f"site-{s}.ingress.jsonl")
                 for s in ctx.sites if (edge / f"site-{s}.ingress.jsonl").exists()]
    return crossing, []


def _harness_args(ft: Any, view: ConclusionView) -> dict[str, Any] | None:
    """The G0 harness's generic args (a harness choice, not a system one); None skips a type whose entity arg names
    another type than the key's."""
    args: dict[str, Any] = {}
    for name in sorted(ft.args):
        spec = ft.args[name]
        if spec["kind"] == "conclusion_id":
            args[name] = view.conclusion_id
        elif spec["kind"] == "entity_id":
            if spec["entity_type"] != view.entity_type:
                return None
            args[name] = view.entity_id
        elif spec["kind"] == "predicate":
            args[name] = view.predicate
        elif spec["kind"] == "enum":
            args[name] = spec["values"][0]
        else:
            args[name] = spec["minimum"]
    return args


def _drafter(ctx: G0Context, ledger: Path) -> tuple[DraftWriter, Runtime | None]:
    if ctx.mode != "fake":
        return DraftWriter(ctx.pack, runtime=None), None
    config = parse_routing({"schema_version": 1, "endpoints": {"hq-fake": {"provider": "fake", "boundary": "central"}},
                            "routes": {DRAFT_TASK: {"endpoint": "hq-fake"}}}, allow_fake=True)
    provider = FakeProvider()
    provider.register(DRAFT_TASK, template_draft(ctx.pack))
    runtime = Runtime(config, boundary="central", ledger_path=ledger, run_id=f"g0-{ctx.seed}", clock=ctx.clock,
                      data_label="synthetic", allow_fake=True, fake=provider, sleep=lambda s: None, environ={})
    return DraftWriter(ctx.pack, runtime=runtime), runtime


def followup_stage(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """Approval-routed follow-up over the supported conclusions (G7; built ahead of E2 and X4, unvalidated), with a
    simulated owner approving every proposal."""
    edge, hq, base = ctx.out / "edge", ctx.out / "hq", ctx.out / "followup"
    base.mkdir(parents=True, exist_ok=True)
    org = org_for_sites(ctx.sites, G0_ENTERPRISE)
    approvers, kill = base / "approvers.json", base / "kill.json"
    write_json_atomic(approvers, {"schema_version": 1, "enterprise": G0_ENTERPRISE, "approvers": [
        {"person_label": f"g0-{role}", "role": role, "unit_path": G0_ENTERPRISE} for role in sorted(ctx.pack.roles)]})
    write_json_atomic(kill, {"schema_version": 1, "global": "off", "types": {}})
    outbox, central = base / "outbox.jsonl", base / "central.ledger.jsonl"
    outbox.touch()
    ledger = FollowupLedger.create(base / LEDGER_FILE, pack=ctx.pack, enterprise=G0_ENTERPRISE, clock=ctx.clock)
    sites: list[EdgeSite] = []
    runtime: Runtime | None = None
    reader = HqReader(ctx.out / "hqdb" / "collective.sqlite3")
    supported = skipped = 0
    packets = dict.fromkeys(("complete", "partial", "failed"), 0)
    try:
        handlers = {}
        for site_id in ctx.sites:
            site = EdgeSite(ctx.pack, site_id, edge, runtime=None, clock=ctx.clock,
                            master_data=ctx.master_data[site_id], hq_dir=hq)
            sites.append(site)
            handlers[site_id] = PacketAssembler(site, clock=ctx.clock).handle
        drafter, runtime = _drafter(ctx, central)
        service = FollowupService(ledger, pack=ctx.pack, org=org, hq=reader, approvers_path=approvers,
                                  kill_switch=KillSwitch(kill, ctx.pack, environ={}), drafter=drafter,
                                  executors={"packet": PacketExecutor(ctx.pack, handlers),
                                             "draft": OutboxExecutor(outbox)})
        types = sorted((ft for ft in ctx.pack.followups.values() if ft.enabled and ft.tier in ("T0", "T1")),
                       key=lambda ft: (ft.tier, ft.id))
        for conclusion_id in sorted(set(ctx.conclusion_ids)):
            view = service.view(conclusion_id)
            if view is None or view.status != "supported":
                continue
            supported += 1
            for ft in types:
                args = _harness_args(ft, view)
                if args is None:
                    skipped += 1
                    continue
                try:
                    key = service.propose(conclusion_id, ft.id, args, principal=SYSTEM, as_of=ctx.as_of)
                    state = service.state(key)
                    service.approve(key, state.latest_version, principal=human(state.owner), as_of=ctx.as_of)
                    done = service.execute(key, as_of=ctx.as_of)
                except FollowupRefused:
                    continue
                if done.status == "executed" and ft.tier == "T0":
                    packets[done.result["status"]] += 1
        summary = service.summary()
        entries = ledger.entries()
    finally:
        for site in sites:
            site.close()
        if runtime is not None:
            runtime.close()
        ledger.close()
    lines = [line for line in outbox.read_bytes().split(b"\n") if line]
    kinds = summary["kinds"]
    ctx.followup_totals.update({
        "approved": kinds["approved"], "conclusions_supported": supported, "drafts": kinds["drafted"],
        "executed": kinds["executed"], "label": BUILT_AHEAD_LABEL, "ledger_entries": summary["ledger_entries"],
        "ledger_head_hash": summary["ledger_head_hash"], "outbox_lines": len(lines), "packets": packets,
        "proposed": kinds["proposed"], "refused": kinds["refused"], "simulated_approvals": True,
        "skipped_types": skipped, "statuses": summary["statuses"]})
    crossing = [Artifact("packets", f"hq/receive.jsonl#packet-{n}", data=canonical_bytes(row["body"]))
                for n, row in enumerate((r for r in read_log(hq / "receive.jsonl") if r["artifact_type"] == "packet"),
                                        start=1)]
    crossing += [Artifact("packet_requests", f"hq/packet_requests.jsonl#{n}", data=canonical_bytes(row["body"]))
                 for n, row in enumerate(read_log(hq / "packet_requests.jsonl"), start=1)]
    crossing += [Artifact("drafts", f"followup/{LEDGER_FILE}#seq-{e.seq}", data=canonical_bytes(e.payload["draft"]))
                 for e in entries if e.kind in ("drafted", "edited")]
    crossing.append(Artifact("approvals_ledger", f"followup/{LEDGER_FILE}", path=base / LEDGER_FILE))
    crossing.append(Artifact("outbox", "followup/outbox.jsonl", path=outbox))
    if central.exists():
        crossing.append(Artifact("hq_draft_ledger", "followup/central.ledger.jsonl", path=central))
    return crossing, []


RUN_LEDGER_PER_GROUP = 12


def _forbidden(out: Path) -> list[str]:
    names = [str(out.resolve()), str(ROOT.resolve()), str(Path.home())]
    for get in (socket.gethostname, getpass.getuser):
        try:
            names.append(get())
        except (OSError, KeyError):
            pass
    return names


def run_files_stage(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """The run files G8 defines (``runfiles.py``), written to ``DIR/run/``: everything in them crossed, so each is
    scanned as class ``run_files``."""
    edge, base, run = ctx.out / "edge", ctx.out / "followup", ctx.out / "run"
    rows: list[dict[str, Any]] = []
    for path in sorted(edge.glob("site-*.ledger.jsonl")):
        rows += read_ledger(path)
    if (base / "central.ledger.jsonl").exists():
        rows += read_ledger(base / "central.ledger.jsonl")
    ledger_rows, summary = runfiles.project_ledger(rows, per_group=RUN_LEDGER_PER_GROUP)
    ledger = FollowupLedger.open(base / LEDGER_FILE, pack=ctx.pack, enterprise=G0_ENTERPRISE, clock=ctx.clock)
    try:
        entries = ledger.entries()
    finally:
        ledger.close()
    bodies = {}
    for row in read_log(ctx.out / "hq" / "receive.jsonl"):
        if row["artifact_type"] == "verdict":
            bodies[row["sha256"]] = row["body"]
    reader = HqReader(ctx.out / "hqdb" / "collective.sqlite3")
    questions, conclusions = [], []
    for conclusion_id in sorted(set(ctx.conclusion_ids)):
        latest = reader.conclusions(conclusion_id)[-1]
        body = strict_load(latest.body)
        question = strict_load(reader.question(latest.question_id).body)
        questions.append({"question_id": runfiles.digest(question["question_id"]),
                          "candidate_key": question["candidate_key"], "window": question["window"],
                          "text": render_text(ctx.pack, question)})
        verdicts = []
        for used in body["gate"]["used"]:
            v = bodies.get(used["sha256"], {})
            verdicts.append({"site": used["site"], "verdict": used["verdict"], "reason": v.get("reason"),
                             **{k: v.get(k) for k in ("support_bucket", "roots_bucket", "reporters_bucket",
                                                      "entity_records_bucket")},
                             "evidence_ref": v.get("evidence_ref")})
        conclusions.append({"conclusion_id": conclusion_id, "status": latest.status, "reasons": body["reasons"],
                            "verdicts": verdicts})
    pack = ctx.pack
    scorecard = {"kind": "g0_scorecard", "schema_version": 1, "pack": pack.id,
                 "digests": {name: runfiles.digest(value) for name, value in pack.hashes().items()},
                 "edge_totals": dict(ctx.totals), "pushdown_totals": dict(ctx.pushdown_totals),
                 "followup_totals": runfiles.shorten(ctx.followup_totals), "ledger": summary}
    scorecard["content_hash"] = runfiles.content_hash(scorecard, ["/content_hash"])
    docs = {"scorecard.json": scorecard,
            "trace.json": {"kind": "g0_trace", "schema_version": 1, "stages": [stage.name for stage in STAGES],
                           "questions": questions, "conclusions": conclusions},
            "ledger.jsonl": ledger_rows,
            "approvals.jsonl": runfiles.project_entries(entries, label=BUILT_AHEAD_LABEL)}
    written = runfiles.write_run_files(run, docs, forbidden=_forbidden(ctx.out))
    crossing = [Artifact("run_files", f"run/{name}", path=run / name) for name in written]
    ctx.run_files_totals.update({"files": len(written), "bytes": sum((run / n).stat().st_size for n in written),
                                 "ledger_rows_total": summary["rows_total"],
                                 "ledger_rows_written": summary["rows_written"]})
    return crossing, []


STAGES: tuple[Stage, ...] = (Stage("edge", edge_stage), Stage("pushdown", pushdown_stage),
                             Stage("followup", followup_stage), Stage("run_files", run_files_stage))


# --------------------------------------------------------------------------------------------------- arguments

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.g0_canary",
                                description="G0: plant canaries in a synthetic world and scan what crossed a "
                                            "boundary (text only).")
    p.add_argument("--pack", required=True, help="a built-in pack id or a pack directory")
    p.add_argument("--records", type=int, required=True, help=f"number of records, 1..{MAX_RECORDS}")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", required=True, help="output directory; absent or empty")
    p.add_argument("--mode", choices=MODES, default="fake")
    p.add_argument("--routing", help="routing file (only with --mode routing)")
    p.add_argument("--require-master-data", choices=("on", "off"), default=None,
                   help="default: the pack's frozen setting; a different value runs a copy of the pack")
    p.add_argument("--dry-run", action="store_true")
    return p


def _check_args(args: argparse.Namespace) -> None:
    if not 1 <= args.records <= MAX_RECORDS:
        raise UsageError(f"--records must be in [1, {MAX_RECORDS}]") from None
    if not 0 <= args.seed <= MAX_SEED:
        raise UsageError(f"--seed must be in [0, {MAX_SEED}]") from None
    if (args.mode == "routing") != (args.routing is not None):
        raise UsageError("--routing is required with --mode routing, and only then") from None
    out = Path(args.out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError(f"--out must be absent or an empty directory: {out}") from None


def _routed_names(config: RoutingConfig) -> list[str]:
    """Every endpoint the extraction and judge routes name (escalations included), sorted."""
    names = set()
    for task in ROUTED_TASKS:
        route = config.routes[task]
        names.update(n for n in (route.endpoint, route.escalate_to) if n is not None)
    return sorted(names)


def _routing(path: str, *, check_env: bool) -> RoutingConfig:
    config = load_routing(path, tasks=list(ROUTED_TASKS), check_env=check_env)
    for name in _routed_names(config):
        endpoint = config.endpoints[name]
        if endpoint.boundary != "any-simulated":
            raise ConfigError(f"$.endpoints.{name}.boundary",
                              "must be any-simulated: one machine plays every simulated site") from None
        if endpoint.provider == "fake":
            raise ConfigError(f"$.endpoints.{name}.provider", "the fake provider runs with --mode fake") from None
    return config


def _dry_run(args: argparse.Namespace) -> int:
    dry = DryRun(CLI)
    if is_builtin_ref(args.pack) or Path(args.pack).exists():
        load_pack(args.pack)
    else:
        dry.need(f"pack {args.pack}")
    if args.mode == "routing":
        if not Path(args.routing).is_file():
            dry.need(f"routing file {args.routing}")
        else:
            config = _routing(args.routing, check_env=False)
            names = _routed_names(config)
            for var in missing_env(config, names):
                dry.need(f"env {var}")
            for name in names:
                dry.need(f"network {config.endpoints[name].host_label} (endpoint {name}, a running server)")
    out = Path(args.out)
    for line in ("leakage.json", "private/manifest.json",
                 "edge/site-<id>.{sqlite3,egress.jsonl,ingress.jsonl,ledger.jsonl}",
                 "edge/packets/site-<id>/<key digest>.json (site-local, never scanned as crossing)",
                 "hq/receive.jsonl", "hq/questions.jsonl", "hq/packet_requests.jsonl", "hqdb/collective.sqlite3",
                 "followup/{approvers.json,kill.json,followups.sqlite3,outbox.jsonl,central.ledger.jsonl}",
                 "run/{scorecard.json,trace.json,ledger.jsonl,approvals.jsonl}"):
        dry.write(str(out / line))
    if args.require_master_data is not None:
        dry.write(f"{out / 'pack'} (only when --require-master-data differs from the pack)")
    return dry.emit()


# --------------------------------------------------------------------------------------------------- the run

def _override(base: FrozenPack, args: argparse.Namespace, out: Path) -> tuple[FrozenPack, bool]:
    """The pack the run uses: ``base``, or a copy in ``out/pack`` whose ``egress.json`` carries the requested
    ``require_master_data`` (so it has its own config_hash)."""
    if args.require_master_data is None or (args.require_master_data == "on") == base.egress.require_master_data:
        return base, False
    copy_dir = out / "pack"
    shutil.copytree(base.directory, copy_dir)
    egress = strict_load((copy_dir / "egress.json").read_bytes())
    egress["require_master_data"] = args.require_master_data == "on"
    (copy_dir / "egress.json").write_bytes((canonical_dumps(egress) + "\n").encode("utf-8"))
    return load_pack(copy_dir), True


def make_world(pack: FrozenPack, seed: int, n: int) -> tuple[Any, int]:
    """The pack's seeded world with at least ``n`` records (the weeks grow until it has them), and its weeks."""
    sites = len(pack.generator["sites"])
    weeks = pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]
    while True:
        world = generate(pack, seed, sites, weeks)
        if len(world.records) >= n:
            return world, weeks
        weeks = math.ceil(weeks * n / len(world.records)) + 1
        if weeks > MAX_WEEKS:
            raise UsageError(f"{n} records need more than {MAX_WEEKS} weeks of this world") from None


def _positive_control(ctx: G0Context, manifest: Any, narratives: list[str]) -> dict[str, Any]:
    db = ctx.out / "edge" / f"site-{ctx.sites[0]}.sqlite3"
    label = f"edge/site-{ctx.sites[0]}.sqlite3"
    if not db.exists():
        return {"label": label, "canary_hits": 0, "shingle_overlap_bytes": 0}
    artifacts = [Artifact("positive_control", label, path=db)]
    wal = db.with_name(db.name + "-wal")
    if wal.exists():
        artifacts.append(Artifact("positive_control", label + "-wal", path=wal))
    report = scan(artifacts, manifest, narratives, ctx.pack)
    return {"label": label, "canary_hits": report["hit_count"] + len(report["known_limitation"]),
            "shingle_overlap_bytes": report["shingle_overlap_bytes"]}


def _ingest_day(records: tuple[dict[str, Any], ...]) -> date:
    return date.fromisoformat(max(r["received_date"] for r in records)) + timedelta(days=1)


def build_context(pack: FrozenPack, records: tuple[dict[str, Any], ...],
                  master_data: Mapping[str, Mapping[str, tuple[str, ...]]], out: Path, *, mode: str, seed: int,
                  routing: RoutingConfig | None, sites: tuple[str, ...]) -> G0Context:
    """The context every stage runs in: the simulated clock starts the day after the last record (ingest) and the
    run's ``as_of`` closes every record week and the ingest week."""
    ingest_day = _ingest_day(records)
    as_of = ingest_day + timedelta(days=7 + pack.egress.close_lag_days)
    return G0Context(pack=pack, records=records, master_data=master_data, out=out,
                     clock=SimClock(ingest_day.isoformat() + DAY_TIME), as_of=as_of.isoformat(),
                     emit_at=as_of.isoformat() + DAY_TIME, mode=mode, seed=seed, routing=routing, sites=sites)


def run_stages(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """Every stage of :data:`STAGES` in order: (artifacts that crossed a boundary, hygiene artifacts)."""
    crossing: list[Artifact] = []
    hygiene: list[Artifact] = []
    for stage in STAGES:
        c, h = stage.run(ctx)
        crossing += c
        hygiene += h
    return crossing, hygiene


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    base = load_pack(args.pack)
    routing = _routing(args.routing, check_env=True) if args.mode == "routing" else None
    world, weeks = make_world(base, args.seed, args.records)      # generation reads no egress field
    out.mkdir(parents=True, exist_ok=True)
    pack, overridden = _override(base, args, out)
    records = world.records[:args.records]
    planted, manifest = plant_canaries(records, random.Random(f"g0-canaries:{args.seed}"), pack)
    manifest = write_manifest(manifest, out / "private" / "manifest.json")
    ctx = build_context(pack, planted, world.master_data, out, mode=args.mode, seed=args.seed, routing=routing,
                        sites=tuple(world.params["site_ids"]))
    crossing, hygiene = run_stages(ctx)
    ingest_day = _ingest_day(planted)
    narratives = [r["narrative"] for r in planted]
    report = scan(crossing, manifest, narratives, pack, hygiene=hygiene)
    for name in CROSSING_CLASSES:
        report["artifact_classes"].setdefault(name, {"bytes": 0, "items": 0})
    report["artifact_classes"] = dict(sorted(report["artifact_classes"].items()))
    positive = _positive_control(ctx, manifest, narratives)
    hygiene_report = report["site_ledger_hygiene"]
    passed = (report["hit_count"] == 0 and report["shingle_overlap_bytes"] == 0 and not hygiene_report["hits"]
              and hygiene_report["shingle_overlap_bytes"] == 0 and positive["canary_hits"] > 0
              and positive["shingle_overlap_bytes"] > 0)
    result = {
        "kind": "g0_leakage", "schema_version": 1, "synthetic": True, "data_label": "synthetic",
        "pack": pack.id, "pack_version": pack.version, "illustrative": pack.illustrative,
        "base_config_hash": base.config_hash, "require_master_data_overridden": overridden,
        "seed": args.seed, "records": len(planted), "sites": len(ctx.sites), "weeks": weeks,
        "world_digest": world_digest(world), "mode": args.mode,
        "models_fake": None if args.mode == "lexical" else args.mode == "fake",
        "as_of": ctx.as_of, "clock": {"ingest": ingest_day.isoformat() + DAY_TIME, "emit": ctx.emit_at},
        "stages": [stage.name for stage in STAGES], "positive_control": positive,
        "edge_totals": dict(ctx.totals), "pushdown_totals": dict(ctx.pushdown_totals),
        "followup_totals": dict(ctx.followup_totals), "run_files_totals": dict(ctx.run_files_totals),
        "passed": passed,
        **report, **code_stamps(), "created_at": utc_clock(),
    }
    write_json_atomic(out / "leakage.json", result)
    print(f"g0: pack={result['pack']} canaries={result['canaries_planted']} hits={result['hit_count']} "
          f"shingle_overlap_bytes={result['shingle_overlap_bytes']} "
          f"known_limitation={len(result['known_limitation'])} -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _check_args(args)
        if args.dry_run:
            return _dry_run(args)
        return run(args)
    except (UsageError, ConfigError, PackError, GeneratorError, LeakageError, runfiles.RunFileError) as exc:
        return fail(str(exc))
    except (OSError, StrictJsonError) as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")


if __name__ == "__main__":
    sys.exit(main())
