"""The routing spike's CLI: ``prereg``, ``run`` and ``score`` (``docs/collective/ROUTING-SPIKE.md`` 5.6).

    python -m research.routing_spike.run prereg [--out docs/collective/routing_spike/prereg.json]
    python -m research.routing_spike.run run --run-id ID [--jobs 4] [--mode in_process|process] [--runs-dir runs]
    python -m research.routing_spike.run score --run-dir runs/routing_spike/ID

``prereg`` writes the frozen settings (section 8), the code hash and the pack and plant hashes. ``run`` refuses a
missing, uncommitted or mismatched prereg and any uncommitted change under the spike's code paths, then runs every
pre-registered seed's planted and no-plant world (each in its own worker process; the worlds share nothing), and
scores them. ``score`` re-scores a finished run directory. Results are synthetic and stamped ``measurement: false``.

Per world (``run_world``):

1. the shipped pipeline and detection (``world.build_world``); the sites' stores are copied as they are before any
   question (``pristine/``);
2. one site process per site (in process or as a child process) behind ``wire``; HQ asks each for its descriptor;
3. the A pass: every candidate, in ``(as_of, key)`` order, asked of every eligible site in site order; if no site
   answered ``unknown`` with reason ``budget``, every other route label reads the same answers (a site re-sends a stored
   verdict); else each label re-runs with fresh site state (the pristine stores);
4. every route of every candidate is published to the world's fabric;
5. the harness-side checks: the leakage scan over every crossing byte, the crossing types, router blindness (rankings
   recomputed with no plant spec, the site narratives rewritten and the site graphs deleted), equal budget, answer
   identity, the shipped verifier's verdicts on the same questions (agreement, and on seed 1's planted world the
   byte equivalence of the site process with the shipped retrieval swapped in).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import sqlite3
import statistics as pystats
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.egress import check_artifact, read_log
from mycelic.collective.edge.records import RecordStore
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import SiteVerifier, retrieve
from mycelic.collective.evaluate.baselines import world_weeks
from mycelic.collective.evaluate.plant import labels_doc
from mycelic.collective.experiments.common import (code_commit, code_dirty, code_files, code_hash, utc_clock,
                                                   write_json_atomic)
from mycelic.collective.jsonio import canonical_bytes, sha256_hex, strict_load
from mycelic.collective.leakage import CANARY_PREFIX, Artifact, Manifest, scan
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import load_pack

from . import score as scoring
from . import wire
from NeuralGraph.research.coordination import TraceEventType

from .hq import Asked, Candidate, Hq, HqView, arm_labels, plan
from .publish import Fabric
from .site_process import SiteConfig, SiteProcess, write_config
from .world import ROOT, SETTINGS, World, backup_sqlite, build_world, candidate_label, load_spec, restore_store

KIND = "routing_spike"
PREREG_PATH = "docs/collective/routing_spike/prereg.json"
CODE_PATTERNS = ("research/routing_spike/*.py", "NeuralGraph/research/coordination/*.py",
                 "NeuralGraph/research/retrieval/*.py", "NeuralGraph/chat_memory/llm.py",
                 "NeuralGraph/chat_memory/textutil.py", "mycelic/collective/**/*.py", "mycelic/*.py")
DIRTY_PATHS = ("research/routing_spike", "NeuralGraph/research/coordination", "NeuralGraph/research/retrieval",
               "NeuralGraph/chat_memory", "mycelic/collective", "mycelic/service.py", "mycelic/lineage.py",
               "mycelic/store.py", PREREG_PATH)
MODES = ("in_process", "process")
FORBIDDEN_HQ = ("research.routing_spike.site_process", "NeuralGraph.research.retrieval", "NeuralGraph.llm_backend",
                "numpy", "mycelic.collective.edge.records", "mycelic.collective.edge.site",
                "mycelic.collective.edge.verify", "mycelic.collective.edge.extract", "mycelic.collective.evaluate",
                "mycelic.collective.packs.generator")
FORBIDDEN_PUBLISH = ("NeuralGraph.llm_backend", "NeuralGraph.research.retrieval", "research.routing_spike.site_process")


def spike_code_files() -> list[str]:
    return code_files(CODE_PATTERNS)


def spike_code_hash() -> str:
    return code_hash([ROOT / p for p in spike_code_files()])


# --------------------------------------------------------------------------------------------------- import picture

def imports_loaded(module: str, prefixes: Sequence[str]) -> list[str]:
    """In a fresh interpreter: which of ``prefixes`` (a module or a package prefix) importing ``module`` loads."""
    code = ("import importlib, json, sys; importlib.import_module(%r); "
            "print(json.dumps(sorted(m for m in sys.modules)))" % module)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=env,
                         timeout=300)
    if out.returncode != 0:
        return ["<import failed>"]
    loaded = json.loads(out.stdout)
    return sorted(p for p in prefixes if any(m == p or m.startswith(p + ".") for m in loaded))


def static_imports(path: Path) -> list[str]:
    """Every module an ``import`` or ``from ... import`` in ``path`` names (relative ones resolved in the package)."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = "research.routing_spike" + (f".{base}" if base else "")
                out += [f"{base}.{a.name}" if not node.module else base for a in node.names]
            else:
                out.append(base)
    return sorted(set(out))


def import_check() -> dict[str, Any]:
    """Integrity check 3: ``hq`` and ``wire`` import none of the forbidden modules, statically and in a fresh
    interpreter; ``publish`` loads neither the backend module nor the retrieval package."""
    here = ROOT / "research" / "routing_spike"
    static = {name: [m for m in static_imports(here / f"{name}.py")
                     if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_HQ)] for name in ("hq", "wire")}
    fresh = {name: imports_loaded(f"research.routing_spike.{name}", FORBIDDEN_HQ) for name in ("hq", "wire")}
    publish = imports_loaded("research.routing_spike.publish", FORBIDDEN_PUBLISH)
    ok = not any(static.values()) and not any(fresh.values()) and not publish
    return {"ok": ok, "static": static, "fresh_interpreter": fresh, "publish": publish}


# --------------------------------------------------------------------------------------------------- endpoints

def site_configs(world: World, *, retrieval: str = "tesseract", max_records: int | None = None,
                 edge: Path | None = None, hq_dir: Path | None = None,
                 settings: Mapping[str, Any] = SETTINGS) -> dict[str, SiteConfig]:
    edge = edge or world.workdir / "edge"
    hq_dir = hq_dir or world.workdir / "hq"
    cap = max_records if max_records is not None else settings["site_retrieval"]["max_records"]
    return {sid: SiteConfig(pack=world.pack.id, site_id=sid, edge_dir=str(edge), hq_dir=str(hq_dir),
                            master_data={t: list(v) for t, v in world.master_data[sid].items()},
                            demo_seed=world.seed, retrieval=retrieval, max_records=cap,
                            graph_path=str(edge / f"site-{sid}.graph.sqlite3"),
                            clock_start=f"{world.pipeline.as_of}T12:00:00Z",
                            embed_dim=settings["site_graph"]["dim"])
            for sid in world.site_ids}


def open_endpoints(configs: Mapping[str, SiteConfig], mode: str, wire_log: Path, config_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        for sid in sorted(configs):
            if mode == "process":
                path = write_config(configs[sid], config_dir / f"site-{sid}.json")
                out[sid] = wire.ChildProcessEndpoint(sid, path, log_path=wire_log)
            else:
                cfg = configs[sid]
                out[sid] = wire.InProcessEndpoint(sid, lambda cfg=cfg: SiteProcess(cfg), log_path=wire_log)
    except BaseException:
        close_endpoints(out)
        raise
    return out


def close_endpoints(endpoints: Mapping[str, Any]) -> None:
    for endpoint in endpoints.values():
        endpoint.close()


# --------------------------------------------------------------------------------------------------- checks

def leakage_check(world: World) -> dict[str, Any]:
    """Integrity check 1: narrative shingle overlap of every byte that crossed a site boundary (and every wire frame)."""
    w = world.workdir
    artifacts = []
    for path in [w / "hq" / "questions.jsonl", w / "hq" / "descriptors.jsonl", w / "hq" / "wire.jsonl",
                 w / "hq" / "intake.jsonl"]:
        if path.exists():
            artifacts.append(Artifact("crossing", f"{world.name}/hq/{path.name}", path=path))
    for number, row in enumerate(read_log(w / "hq" / "receive.jsonl"), start=1):
        if row["artifact_type"] == "verdict":
            artifacts.append(Artifact("crossing", f"{world.name}/hq/receive.jsonl#{number}",
                                      data=canonical_bytes(row["body"])))
    for sid in world.site_ids:
        for name in (f"site-{sid}.ingress.jsonl", f"site-{sid}.egress.jsonl", f"site-{sid}.descriptors.jsonl"):
            if (w / "edge" / name).exists():
                artifacts.append(Artifact("crossing", f"{world.name}/edge/{name}", path=w / "edge" / name))
    manifest = Manifest(pack_id=world.pack.id, config_hash=world.pack.config_hash, prefix=CANARY_PREFIX, canaries=())
    report = scan(artifacts, manifest, [r["narrative"] for r in world.records], world.pack)
    overlap = sum(hit["bytes"] for hit in report["shingle_hits"])
    entry = report["artifact_classes"].get("crossing", {"bytes": 0, "items": 0})
    return {"ok": overlap == 0 and not report["hits"], "overlap_bytes": overlap, "canary_hits": len(report["hits"]),
            "scanned_bytes": entry["bytes"], "scanned_items": entry["items"]}


def crossing_check(world: World) -> dict[str, Any]:
    """Integrity check 2: only questions in, verdicts and the descriptor out (cells as before); every question and
    verdict passes ``check_artifact`` on both sides; every descriptor passes the closed schema."""
    w, pack = world.workdir, world.pack
    problems: list[str] = []
    kinds: dict[str, int] = {}
    for row in wire.read_frames(w / "hq" / "wire.jsonl"):
        if row["direction"] == "request":
            try:
                kind, _ = wire.decode_request(row["frame"].encode("utf-8"))
                kinds[kind] = kinds.get(kind, 0) + 1
            except wire.WireError:
                problems.append("wire request")
    for row in read_log(w / "hq" / "questions.jsonl"):
        if row["artifact_type"] != "question" or check_artifact(pack, "", "question", row["body"]) is not None:
            problems.append("question log")
    types: dict[str, int] = {}
    for row in read_log(w / "hq" / "receive.jsonl"):
        types[row["artifact_type"]] = types.get(row["artifact_type"], 0) + 1
        if row["artifact_type"] not in ("cells_bundle", "verdict"):
            problems.append("receive type")
        elif row["artifact_type"] == "verdict" and check_artifact(pack, row["site"], "verdict", row["body"]):
            problems.append("receive verdict")
    for sid in world.site_ids:
        for row in read_log(w / "edge" / f"site-{sid}.ingress.jsonl"):
            if row["artifact_type"] != "question":
                problems.append("ingress type")
        for row in read_log(w / "edge" / f"site-{sid}.egress.jsonl"):
            if row["artifact_type"] not in ("cells_bundle", "verdict"):
                problems.append("egress type")
    descriptor_rows = [strict_load(line) for line in (w / "hq" / "descriptors.jsonl").read_bytes().split(b"\n")
                       if line] if (w / "hq" / "descriptors.jsonl").exists() else []
    for sid in world.site_ids:
        path = w / "edge" / f"site-{sid}.descriptors.jsonl"
        descriptor_rows += [strict_load(line) for line in path.read_bytes().split(b"\n") if line] \
            if path.exists() else []
    for row in descriptor_rows:
        if wire.descriptor_problem(row["body"], site_id=row["site"], pack_id=pack.id, config_hash=pack.config_hash,
                                   template_ids=sorted(pack.questions)) is not None:
            problems.append("descriptor")
    intake = [strict_load(line) for line in (w / "hq" / "intake.jsonl").read_bytes().split(b"\n") if line] \
        if (w / "hq" / "intake.jsonl").exists() else []
    refused = sum(1 for row in intake if row["problem"] is not None)
    if refused:
        problems.append("intake")
    return {"ok": not problems and set(kinds) <= set(wire.KINDS), "problems": sorted(set(problems)),
            "request_kinds": kinds, "receive_types": types, "hq_intake_refusals": refused,
            "descriptor_rows": len(descriptor_rows)}


def blindness_check(world: World, asked: Sequence[Asked], hq: Hq, eligible: Sequence[str],
                    settings: Mapping[str, Any]) -> dict[str, Any]:
    """Integrity check 4: every router's ranking is byte-identical with no plant spec, with the site narratives
    rewritten after the cells were sent, and with the site graphs deleted (O reads the plant spec by design and is
    left out)."""
    w = world.workdir
    for sid in world.site_ids:
        conn = sqlite3.connect(str(w / "edge" / f"site-{sid}.sqlite3"))
        try:
            conn.execute("UPDATE records SET narrative = 'rewritten after the cells were sent ' || seq")
            conn.commit()
        finally:
            conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(w / "edge" / f"site-{sid}.graph.sqlite3") + suffix).unlink(missing_ok=True)
    view = HqView(world.pipeline.store, world.pack)
    before = {a.question["question_id"]: {k: v for k, v in a.orders.items() if k != "O"} for a in asked}
    after = {}
    for a in asked:
        question, _, orders = plan(view, world.pack, a.candidate, eligible, tie_salt=settings["tie_salt"],
                                   m=settings["m"], seed=world.seed, oracle_sites=None)
        after[question["question_id"]] = {k: v for k, v in orders.items() if k != "O"}
    same = canonical_bytes(before) == canonical_bytes(after)
    return {"ok": same, "orders_sha256": sha256_hex(canonical_bytes(before)),
            "recomputed_sha256": sha256_hex(canonical_bytes(after))}


def shipped_compare(world: World, asked_in_order: Sequence[Asked], answers: Mapping[str, Mapping[str, Any]], *,
                    equivalence: bool, settings: Mapping[str, Any]) -> dict[str, Any]:
    """The shipped ``SiteVerifier.answer`` on the same questions, from pristine copies of the site stores: verdict
    agreement with the Tesseract site, and (``equivalence``) the byte equality of the site process with the shipped
    retrieval swapped in (cap ``verify_max_records``)."""
    w, pack = world.workdir, world.pack
    pristine = w / "pristine"
    shipped: dict[tuple[str, str], bytes] = {}
    base = w / "compare" / "shipped"
    clock_value = {"ts": f"{world.pipeline.as_of}T12:00:00Z"}

    def clock() -> str:
        return clock_value["ts"]

    for sid in world.site_ids:
        restore_store(sid, pristine, base / "edge")
        site = EdgeSite(pack, sid, base / "edge", runtime=None, clock=clock, master_data=world.master_data[sid],
                        hq_dir=base / "hq")
        try:
            verifier = SiteVerifier(site, runtime=None, clock=clock, demo_seed=world.seed)
            for a in asked_in_order:
                clock_value["ts"] = f"{a.question['as_of']}T12:00:00Z"
                shipped[(a.question["question_id"], sid)] = canonical_bytes(verifier.answer(a.question))
        finally:
            site.close()
    pairs = same = 0
    for (qid, sid), data in shipped.items():
        mine = answers[qid][sid]
        if mine["source"] != "site":
            continue
        pairs += 1
        same += int(strict_load(data)["verdict"] == mine["verdict"])
    out: dict[str, Any] = {"agreement": {"pairs": pairs, "same": same,
                                         "share": same / pairs if pairs else None}}
    # in-window records the shipped retrieval finds that fall outside what Tesseract kept (harness side)
    diag_refs: dict[tuple[str, str], list[str]] = {}
    for sid in world.site_ids:
        path = w / "edge" / f"site-{sid}.tesseract.jsonl"
        for line in path.read_bytes().split(b"\n") if path.exists() else []:
            if line:
                row = strict_load(line)
                diag_refs[(row["question_id"], sid)] = row["refs"]
    missed = found = 0
    for sid in world.site_ids:
        canon = Canonicaliser(pack, known=world.master_data[sid])
        store = RecordStore(base / "edge" / f"site-{sid}.sqlite3", site_id=sid, pack_id=pack.id,
                            config_hash=pack.config_hash)
        try:
            cache: dict[tuple[str, str], frozenset[str]] = {}
            for a in asked_in_order:
                key = (a.question["question_id"], sid)
                if key not in diag_refs:
                    continue
                params = a.question["params"]
                records, _ = retrieve(store, canon, entity_type=params["entity_type"],
                                      entity_id=params["entity_id"], window=a.question["window"],
                                      cap=pack.egress.verify_max_records, cache=cache)
                kept = set(diag_refs[key])
                found += len(records)
                missed += sum(1 for r in records if r.record_ref not in kept)
        finally:
            store.close()
    out["shipped_retrieval"] = {"questions": len(diag_refs), "records_found": found,
                                "outside_tesseract_kept": missed}
    if equivalence:
        base2 = w / "compare" / "equivalence"
        for sid in world.site_ids:
            restore_store(sid, pristine, base2 / "edge")
        configs = site_configs(world, retrieval="shipped", max_records=pack.egress.verify_max_records,
                               edge=base2 / "edge", hq_dir=base2 / "hq", settings=settings)
        differing = total = 0
        for sid in world.site_ids:
            process = SiteProcess(configs[sid])
            try:
                for a in asked_in_order:
                    total += 1
                    data = process.handle("question", a.question_bytes)
                    differing += int(data != shipped[(a.question["question_id"], sid)])
            finally:
                process.close()
        out["equivalence"] = {"ok": differing == 0 and total > 0, "pairs": total, "differing": differing}
    return out


# --------------------------------------------------------------------------------------------------- one world

def _site_diag(world: World) -> dict[str, Any]:
    seconds: list[float] = []
    for sid in world.site_ids:
        path = world.workdir / "edge" / f"site-{sid}.tesseract.jsonl"
        for line in path.read_bytes().split(b"\n") if path.exists() else []:
            if line:
                seconds.append(strict_load(line)["seconds"])
    return {"tesseract_queries": len(seconds),
            "tesseract_seconds_median": pystats.median(seconds) if seconds else None,
            "tesseract_seconds_p95": stats.percentile(seconds, 95) if seconds else None,
            "tesseract_seconds_total": sum(seconds), "tesseract_seconds": seconds}


def _bytes(world: World, results: Sequence[Asked]) -> dict[str, Any]:
    w = world.workdir

    def summary(values: Sequence[int]) -> dict[str, Any]:
        return {"n": len(values), "total": sum(values), "median": pystats.median(values) if values else None,
                "max": max(values) if values else None}

    questions = [row["bytes"] for row in read_log(w / "hq" / "questions.jsonl")]
    verdicts = [row["bytes"] for row in read_log(w / "hq" / "receive.jsonl") if row["artifact_type"] == "verdict"]
    descriptors = []
    if (w / "hq" / "descriptors.jsonl").exists():
        descriptors = [strict_load(line)["bytes"] for line in (w / "hq" / "descriptors.jsonl").read_bytes().split(b"\n")
                       if line]
    exported = [r.exported_bytes for a in results for r in a.results.values()]
    return {"question": summary(questions), "verdict": summary(verdicts), "descriptor": summary(descriptors),
            "hq_exported_per_query": summary(exported)}


async def _ask_all(hq: Hq, asked: Sequence[Asked], labels: Sequence[str]) -> None:
    for label in labels:
        for a in asked:
            await hq.ask(a, label)


async def run_world_async(world: World, *, settings: Mapping[str, Any] = SETTINGS, mode: str = "in_process",
                          equivalence: bool = False) -> dict[str, Any]:
    t_start = time.perf_counter()
    w, pack = world.workdir, world.pack
    edge, hq_dir = w / "edge", w / "hq"
    for site in world.pipeline.sites.values():
        site.close()
    for sid in world.site_ids:
        backup_sqlite(edge / f"site-{sid}.sqlite3", w / "pristine" / f"site-{sid}.sqlite3")
    configs = site_configs(world, settings=settings)
    wire_log = hq_dir / "wire.jsonl"
    t0 = time.perf_counter()
    endpoints = open_endpoints(configs, mode, wire_log, w / "configs")
    open_s = time.perf_counter() - t0
    try:
        hq = Hq(pack, world.pipeline.store, endpoints, hq_dir=hq_dir, tie_salt=settings["tie_salt"],
                m=settings["m"], seed=world.seed)
        descriptors = hq.describe_all()
        eligible = hq.eligible()
        cands = [Candidate.from_detector(c) for c in world.candidates]
        lab = [candidate_label(c.key, c.week, world.labels) for c in cands]
        asked = [hq.prepare(c, oracle_sites=lb["sites"] if lb["label"] == "true" else None)
                 for c, lb in zip(cands, lab)]
        in_order = [asked[i] for i in sorted(range(len(asked)), key=lambda i: (cands[i].as_of, cands[i].key))]
        labels = arm_labels(eligible, settings["m"])
        t0 = time.perf_counter()
        await _ask_all(hq, in_order, ["A"])
        a_pass_s = time.perf_counter() - t0
        budget = sum(1 for a in in_order for r in a.results["A"].received
                     if r.source == "site" and r.body.get("reason") == "budget")
        answers_mode = "shared" if budget == 0 else "per_arm"
        t0 = time.perf_counter()
        if answers_mode == "shared":
            await _ask_all(hq, in_order, labels[1:])
        else:
            for label in labels[1:]:
                close_endpoints(endpoints)
                for sid in world.site_ids:
                    restore_store(sid, w / "pristine", edge)
                endpoints = open_endpoints(configs, mode, wire_log, w / "configs")
                hq.replace_endpoints(endpoints)
                await _ask_all(hq, in_order, [label])
        arms_s = time.perf_counter() - t0
    finally:
        close_endpoints(endpoints)

    # the fabric
    t0 = time.perf_counter()
    fabric = Fabric(w / "fabric" / "mycelic.db", pack)
    await fabric.start(labels)
    try:
        for a in in_order:
            for label in labels:
                r = a.results[label]
                await fabric.publish(label, a.question, entity_type=a.candidate.entity_type,
                                     candidate_key=a.candidate.key, snapshot_week=a.candidate.week,
                                     as_of=a.candidate.as_of, route=r.route, received=r.received, status=r.status)
    finally:
        idle = await fabric.close()
    fabric_s = time.perf_counter() - t0

    # answers, routes and the per-world checks
    answers: dict[str, dict[str, Any]] = {}
    for a in in_order:
        qid = a.question["question_id"]
        answers[qid] = {r.site: {"verdict": r.body["verdict"], "reason": r.body.get("reason"), "sha256": r.sha256,
                                 "source": r.source} for r in a.results["A"].received}
    identity_bad = 0
    for a in in_order:
        qid = a.question["question_id"]
        for label in labels:
            for r in a.results[label].received:
                ref = answers[qid][r.site]
                budget_pair = "budget" in (r.body.get("reason"), ref["reason"])
                if answers_mode == "per_arm" and budget_pair:
                    continue
                if r.sha256 != ref["sha256"] or r.source != ref["source"]:
                    identity_bad += 1
    sizes_bad = [(a.question["question_id"], label) for a in in_order for label in labels
                 if len(a.results[label].route) != hq.size(label, eligible)]
    trace_bad = sum(1 for a in in_order for label in labels if not a.results[label].route_selected_first)
    covered = all(set(a.results) == set(labels) for a in in_order)
    checks: dict[str, Any] = {
        "leakage": leakage_check(world),
        "crossing": crossing_check(world),
        "shared_candidates": {"ok": covered and len(in_order) == len(world.candidates),
                              "candidate_sha256": world.candidate_sha256, "labels": len(labels)},
        "equal_budget": {"ok": not sizes_bad, "bad_routes": len(sizes_bad)},
        "answer_identity": {"ok": identity_bad == 0, "mode": answers_mode, "mismatches": identity_bad},
        "trace_route_selected_first": {"ok": trace_bad == 0, "bad": trace_bad},
        "fabric_idle": {"ok": bool(idle)},
    }
    t0 = time.perf_counter()
    compare = shipped_compare(world, in_order, answers, equivalence=equivalence, settings=settings)
    compare_s = time.perf_counter() - t0
    checks["blindness"] = blindness_check(world, in_order, hq, eligible, settings)
    if "equivalence" in compare:
        checks["equivalence"] = compare.pop("equivalence")
    site_index = {s: i for i, s in enumerate(world.site_ids)}
    candidates_out = []
    for c, lb, a in zip(cands, lab, asked):
        candidates_out.append({"key": c.key, "entity_type": c.entity_type, "entity_id": c.entity_id,
                               "predicate": c.predicate, "as_of": c.as_of, "week": c.week,
                               "question_id": a.question["question_id"], "label": lb["label"],
                               "pattern": lb["pattern"], "decoy_class": lb["decoy_class"]})
    routes = {label: {a.question["question_id"]: {"route": "".join(str(site_index[s]) for s in a.results[label].route),
                                                  "status": a.results[label].status}
                      for a in in_order} for label in labels}
    codes = {t.value: chr(ord("a") + i) for i, t in enumerate(TraceEventType)}
    traces = {label: {a.question["question_id"]: "".join(codes[e] for e in a.results[label].trace)
                      for a in in_order} for label in labels}
    out = {
        "seed": world.seed, "world": "planted" if world.planted else "noplant", "name": world.name, "mode": mode,
        "answers_mode": answers_mode, "budget_unknowns": budget, "detected": world.detected,
        "n_candidates": len(world.candidates), "candidate_sha256": world.candidate_sha256,
        "site_ids": world.site_ids, "eligible": list(eligible), "labels": labels,
        "descriptors": {sid: sha256_hex(data) for sid, data in descriptors.items()},
        "candidates": candidates_out, "answers": answers, "routes": routes, "traces": traces,
        "trace_codes": {code: name for name, code in codes.items()}, "checks": checks,
        "site_side": {**compare, **_site_diag(world),
                      "nodes": {sid: n for sid, n in _graph_nodes(world).items()}},
        "bytes": _bytes(world, in_order),
        "records": len(world.records),
        "timings": {"open_sites_s": open_s, "a_pass_s": a_pass_s, "other_arms_s": arms_s, "fabric_s": fabric_s,
                    "compare_s": compare_s, "world_total_s": time.perf_counter() - t_start},
    }
    return out


def _graph_nodes(world: World) -> dict[str, int]:
    out = {}
    for sid in world.site_ids:
        path = world.workdir / "edge" / f"site-{sid}.tesseract.jsonl"
        rows = [strict_load(line) for line in path.read_bytes().split(b"\n") if line] if path.exists() else []
        out[sid] = rows[0]["nodes"] if rows else 0
    return out


def run_world_job(seed: int, planted: bool, run_dir: str, mode: str, top_n: int | None = None) -> str:
    """One world in its own worker process; writes ``<world>/world.json`` and returns its path."""
    settings = SETTINGS
    t0 = time.perf_counter()
    workdir = Path(run_dir) / "worlds" / f"seed-{seed:02d}-{'planted' if planted else 'noplant'}"
    workdir.mkdir(parents=True, exist_ok=False)
    world = build_world(seed, planted, workdir, settings=settings, top_n=top_n)
    build_s = time.perf_counter() - t0
    try:
        doc = asyncio.run(run_world_async(world, settings=settings, mode=mode,
                                          equivalence=(seed == settings["seeds"][0] and planted)))
    finally:
        world.pipeline.store.close()
    doc["timings"]["build_s"] = build_s
    write_json_atomic(workdir / "world.json", doc)
    return str(workdir / "world.json")


# --------------------------------------------------------------------------------------------------- prereg

def prereg_doc(settings: Mapping[str, Any] = SETTINGS) -> dict[str, Any]:
    pack = load_pack(settings["pack"])
    return {"kind": "routing_spike_prereg", "schema_version": 1, "settings": dict(settings),
            "code_hash": spike_code_hash(), "code_files": spike_code_files(), "pack_hashes": pack.hashes(),
            "plant_sha256": settings["plant_sha256"], "synthetic": True, "measurement": False,
            "design": "docs/collective/ROUTING-SPIKE.md"}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=30)


def prereg_check(path: str | Path = PREREG_PATH) -> dict[str, Any]:
    """Integrity check 10: the prereg exists, is committed, matches the settings and the code hash, and no spike code
    path has an uncommitted change."""
    full = ROOT / path
    problems = []
    if not full.exists():
        return {"ok": False, "problems": ["missing prereg"]}
    doc = strict_load(full.read_bytes())
    current = prereg_doc()
    if canonical_bytes(doc.get("settings")) != canonical_bytes(current["settings"]):
        problems.append("settings differ")
    if doc.get("code_hash") != current["code_hash"]:
        problems.append("code hash differs")
    if doc.get("pack_hashes") != current["pack_hashes"]:
        problems.append("pack hashes differ")
    tracked = _git("ls-files", "--error-unmatch", str(path))
    if tracked.returncode != 0:
        problems.append("prereg not committed")
    dirty = code_dirty(list(DIRTY_PATHS))
    if dirty is not False:
        problems.append("uncommitted change under the spike's code paths")
    return {"ok": not problems, "problems": problems, "code_hash": current["code_hash"],
            "prereg_sha256": sha256_hex(full.read_bytes()), "code_commit": code_commit()}


# --------------------------------------------------------------------------------------------------- score

def score_run(run_dir: str | Path, *, settings: Mapping[str, Any] = SETTINGS,
              extra_checks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    pack = load_pack(settings["pack"])
    seeds = [s for s in settings["seeds"] if (run_dir / "worlds" / f"seed-{s:02d}-planted" / "world.json").exists()
             and (run_dir / "worlds" / f"seed-{s:02d}-noplant" / "world.json").exists()]
    worlds: dict[tuple[int, str], dict[str, Any]] = {}
    conclusions: dict[tuple[int, str], dict[str, list[dict[str, Any]]]] = {}
    for s in seeds:
        for kind in ("planted", "noplant"):
            wdir = run_dir / "worlds" / f"seed-{s:02d}-{kind}"
            doc = strict_load((wdir / "world.json").read_bytes())
            worlds[(s, kind)] = doc
            receive, egress = scoring.log_index(wdir, doc["site_ids"])
            conclusions[(s, kind)] = scoring.fabric_conclusions(
                wdir / "fabric" / "mycelic.db", doc["labels"],
                min_confirming_sites=pack.pushdown.min_confirming_sites, receive=receive, egress=egress)
    patterns = None
    per_seed: dict[int, dict[str, dict[str, Any]]] = {}
    fabric_bad = notes_bad = 0
    for s in seeds:
        planted, noplant = worlds[(s, "planted")], worlds[(s, "noplant")]
        if patterns is None:
            spec = load_spec(pack, settings)
            labels_full = labels_doc(spec, pack, weeks=world_weeks(settings["start"], settings["weeks"]),
                                     eval_from=settings["eval_from"], eval_to=settings["eval_to"],
                                     grace_weeks=settings["grace_weeks"])
            patterns = labels_full["patterns"]
        cand_labels = {c["question_id"]: c for c in planted["candidates"]}
        per_seed[s] = {}
        for label in planted["labels"]:
            pc, nc = conclusions[(s, "planted")][label], conclusions[(s, "noplant")][label]
            for kind, rows, doc in (("planted", pc, planted), ("noplant", nc, noplant)):
                if len(rows) != doc["n_candidates"]:
                    notes_bad += 1
                routes = doc["routes"][label]
                for row in rows:
                    if routes.get(row["question_id"], {}).get("status") != row["status"]:
                        fabric_bad += 1
                    if row["status"] == "supported" and not row["lineage_ok"]:
                        fabric_bad += 1
            per_seed[s][label] = scoring.label_counts(pc, nc, patterns, cand_labels, noplant["n_candidates"])
    b = settings["bootstrap"]
    summary = scoring.summarise(seeds, per_seed, B=b["B"], alpha=b["alpha"], bar=settings["noplant_bar"],
                                primary_seed=b["primary_seed"], noplant_seed=b["noplant_seed"])
    # integrity checks over every world
    docs = list(worlds.values())

    def every(name: str) -> dict[str, Any]:
        bad = sorted(d["name"] for d in docs if not d["checks"][name]["ok"])
        return {"ok": not bad and bool(docs), "failing_worlds": bad}

    desc = {}
    for d in docs:
        for site, sha in d["descriptors"].items():
            desc.setdefault(site, set()).add(sha)
    equivalence = [d["checks"]["equivalence"] for d in docs if "equivalence" in d["checks"]]
    leak_overlap = sum(d["checks"]["leakage"]["overlap_bytes"] for d in docs)
    checks: dict[str, Any] = {
        "1_leakage": {**every("leakage"), "overlap_bytes": leak_overlap,
                      "scanned_bytes": sum(d["checks"]["leakage"]["scanned_bytes"] for d in docs)},
        "2_crossing_types": {**every("crossing"), "descriptors_constant_per_site":
                             all(len(v) == 1 for v in desc.values()) and len(desc) == len(docs[0]["site_ids"])},
        "4_router_blindness": every("blindness"),
        "5_shared_candidates": every("shared_candidates"),
        "6_equal_budget": every("equal_budget"),
        "7_answer_identity": {**every("answer_identity"),
                              "modes": sorted({d["answers_mode"] for d in docs})},
        "8_fabric": {"ok": fabric_bad == 0 and notes_bad == 0 and all(d["checks"]["fabric_idle"]["ok"] for d in docs),
                     "status_or_lineage_mismatches": fabric_bad, "note_count_mismatches": notes_bad},
        "9_equivalence": {"ok": len(equivalence) == 1 and equivalence[0]["ok"],
                          **(equivalence[0] if equivalence else {})},
    }
    checks["2_crossing_types"]["ok"] = checks["2_crossing_types"]["ok"] and \
        checks["2_crossing_types"]["descriptors_constant_per_site"]
    for name, value in (extra_checks or {}).items():
        checks[name] = value
    checks = dict(sorted(checks.items(), key=lambda kv: int(kv[0].split("_")[0])))
    if len(seeds) != len(settings["seeds"]):
        checks["seeds_complete"] = {"ok": False, "seeds": seeds}
    result = scoring.verdict(checks, summary["primary"], summary["noplant"])
    agreement = [d["site_side"]["agreement"] for d in docs]
    shipped = [d["site_side"]["shipped_retrieval"] for d in docs]
    seconds = [x for d in docs for x in d["site_side"]["tesseract_seconds"]]
    return {
        "kind": "routing_spike_result", "schema_version": 1, "synthetic": True, "measurement": False,
        "settings": dict(settings), "seeds": seeds, "verdict": result, "checks": checks,
        "statistics": {k: summary[k] for k in ("primary", "noplant", "secondary", "totals")},
        "per_seed": summary["per_seed"],
        "planted_false_by_class": scoring.by_decoy_class(per_seed, seeds),
        "per_seed_label": {str(s): per_seed[s] for s in seeds},
        "site_side": {
            "agreement_pairs": sum(a["pairs"] for a in agreement), "agreement_same": sum(a["same"] for a in agreement),
            "shipped_found": sum(x["records_found"] for x in shipped),
            "shipped_outside_tesseract_kept": sum(x["outside_tesseract_kept"] for x in shipped),
            "tesseract_queries": sum(d["site_side"]["tesseract_queries"] for d in docs),
            "tesseract_seconds_median": stats.percentile(seconds, 50) if seconds else None,
            "tesseract_seconds_p95": stats.percentile(seconds, 95) if seconds else None,
            "tesseract_seconds_median_planted": stats.percentile(
                [x for d in docs if d["world"] == "planted" for x in d["site_side"]["tesseract_seconds"]], 50),
            "tesseract_seconds_median_noplant": stats.percentile(
                [x for d in docs if d["world"] == "noplant" for x in d["site_side"]["tesseract_seconds"]], 50),
            "nodes_per_site": {d["name"]: d["site_side"]["nodes"] for d in docs}},
        "bytes": {name: {"total": sum(d["bytes"][name]["total"] for d in docs),
                         "n": sum(d["bytes"][name]["n"] for d in docs),
                         "max": max((d["bytes"][name]["max"] or 0) for d in docs)}
                  for name in ("question", "verdict", "descriptor", "hq_exported_per_query")},
        "routes_and_answers": {d["name"]: {"site_ids": d["site_ids"], "candidates": d["candidates"],
                                           "answers": d["answers"], "routes": d["routes"]} for d in docs},
        "worlds": {d["name"]: {"records": d["records"], "detected": d["detected"], "candidates": d["n_candidates"],
                               "candidate_sha256": d["candidate_sha256"], "answers_mode": d["answers_mode"],
                               "budget_unknowns": d["budget_unknowns"], "timings": d["timings"]} for d in docs},
    }


# --------------------------------------------------------------------------------------------------- CLI

def cmd_prereg(args: argparse.Namespace) -> int:
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = write_json_atomic(out, prereg_doc())
    print(f"prereg: {args.out} sha256={sha} code_hash={spike_code_hash()}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    if args.mode not in MODES:
        print("error: --mode is in_process or process", file=sys.stderr)
        return 2
    run_dir = Path(args.runs_dir) / KIND / args.run_id
    if run_dir.exists():
        print(f"error: run directory exists: {run_dir}", file=sys.stderr)
        return 2
    pre = prereg_check()
    if args.top_n is not None:
        pre = {**pre, "ok": False, "problems": [*pre["problems"], "top_n override: not the pre-registered setting"]}
    if not pre["ok"] and not args.allow_dirty:
        print(f"error: prereg check failed: {', '.join(pre['problems'])}", file=sys.stderr)
        return 2
    imports = import_check()
    run_dir.mkdir(parents=True)
    write_json_atomic(run_dir / "run.json", {"run_id": args.run_id, "mode": args.mode, "jobs": args.jobs,
                                             "started_at": utc_clock(), "prereg": pre, "imports": imports,
                                             "command": " ".join(sys.argv)})
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")          # one thread per worker process; results do not depend on it
    jobs = [(s, planted) for s in SETTINGS["seeds"] for planted in (True, False)]
    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as pool:
        futures = [pool.submit(run_world_job, s, planted, str(run_dir), args.mode, args.top_n) for s, planted in jobs]
        for f in futures:
            print(f"world done: {f.result()}", flush=True)
    worlds_s = time.perf_counter() - t0
    extra = {"3_imports": imports, "10_code_and_settings": pre}
    doc = score_run(run_dir, extra_checks=extra)
    doc["run"] = {"run_id": args.run_id, "mode": args.mode, "jobs": args.jobs, "worlds_s": worlds_s,
                  "total_s": time.perf_counter() - started, "finished_at": utc_clock(),
                  "command": "python -m research.routing_spike.run " + " ".join(sys.argv[1:]),
                  "top_n_override": args.top_n, "allow_dirty": bool(args.allow_dirty)}
    sha = write_json_atomic(run_dir / "result.json", doc)
    print(_report(doc, run_dir / "result.json", sha))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    meta = strict_load((run_dir / "run.json").read_bytes())
    doc = score_run(run_dir, extra_checks={"3_imports": meta["imports"], "10_code_and_settings": meta["prereg"]})
    sha = write_json_atomic(run_dir / "rescore.json", doc)
    print(_report(doc, run_dir / "rescore.json", sha))
    return 0


def _report(doc: Mapping[str, Any], path: Path, sha: str) -> str:
    p, n = doc["statistics"]["primary"], doc["statistics"]["noplant"]
    return (f"routing spike: verdict={doc['verdict']['verdict']} primary mean R-U={p['mean_diff']:.4f} "
            f"[{p['ci_low']:.4f}, {p['ci_high']:.4f}] noplant R-U={n['mean_diff']:.4f} "
            f"[{n['ci_low']:.4f}, {n['ci_high']:.4f}] -> {path} sha256={sha} (synthetic)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.routing_spike.run",
                                     description="Routing spike (plan item 3.2): synthetic, offline, a spike.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prereg")
    p.add_argument("--out", default=PREREG_PATH)
    r = sub.add_parser("run")
    r.add_argument("--run-id", required=True)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--mode", default="in_process")
    r.add_argument("--runs-dir", default="runs")
    r.add_argument("--top-n", type=int, default=None, help="tests only: fewer candidates (the prereg says 60)")
    r.add_argument("--allow-dirty", action="store_true", help="tests only: run without a matching committed prereg")
    s = sub.add_parser("score")
    s.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "run" and (args.top_n is not None or args.allow_dirty):
        print("note: a run with --top-n or --allow-dirty is not the pre-registered run", file=sys.stderr)
    return {"prereg": cmd_prereg, "run": cmd_run, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
