"""Run Mycelic with live LLM edge agents, paired with the simulated operators.

    python3 -m research.mycelic.live.run --scale 400 --seed 701 \
        --edge-url http://127.0.0.1:8081 --kernel-url http://127.0.0.1:8082 \
        --systems H_mycelic_full,H_mycelic_prev,B4_central_triage,A2_live \
        --max-agents 40 --out research/mycelic/artifacts/live/smoke701.jsonl

What is live and what is not (see live/README.md):
  * every user agent's extraction is ONE real model call over its own notes
    (``agents.EdgeAgent``); the claims replace the simulated extraction
    operator in the unchanged pipeline (``systems.user_extract(ex=...)``);
  * A2_live: the kernel model reads the observable-prefiltered notes in
    chunks with the same output format (``chunked_long_context(pre_ex=...)``);
  * routing, merging, triage, questions and descents are code by design;
    kernel synthesis is the existing code and its verification coins are
    still SIMULATED at the kernel tier.

Every system is also run on the same world with the simulated operators
(paired).  Calls go through a record/replay cache, so an interrupted run
resumes where it stopped and a rerun is bit-for-bit identical.

Seeds: LIVE seeds are 700-709 (development data; never sealed seeds).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import ops as _ops
from ..corpus import N_CAUSAL_PRED, PRED_SURFACE
from ..evalm import evaluate
from ..models import ANCHORS, allocation
from ..org import ENT, USER, build_org
from ..runner import ART, World, build_world, ranker_for, run_arch
from ..systems import _lexical_index, chunked_long_context, user_extract
from . import agents as A
from .backend import (MOCK_LABEL, LlamaServerBackend, MockBackend, ReplayCache,
                      model_fingerprint)
from .notes import NoteStore
from . import score as S

LIVE_SEEDS = range(700, 710)
LIVE_ART = os.path.join(ART, "live")
CACHE_DIR = os.path.join(LIVE_ART, "cache")
SIM_TWIN = {"A2_live": "A2_chunked_ctx"}
HEADLINE = ["found_anywhere_in_register", "found_supported", "rare_supported",
            "average_precision", "discovery_precision", "decoy_acceptance_all",
            "decoy_D1_entity_coincidence", "decoy_D2_temporal_scramble",
            "decoy_D3_near_miss_entity", "decoy_D5_stale_chain",
            "independent_evidence_accuracy", "lineage_accuracy",
            "hallucination_rate", "rare_signal_recall", "n_reported"]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=os.path.dirname(__file__)).stdout.strip()
    except OSError:
        return "?"


def mock_lexicon() -> Dict[Tuple[str, ...], str]:
    return {tuple(f.split()): p for p, forms in PRED_SURFACE.items() for f in forms}


def resolve_model_id(url: str, path: Optional[str]) -> str:
    """sha256 of the GGUF file the server runs (sidecar-cached)."""
    if path:
        return model_fingerprint(path)
    props = LlamaServerBackend.server_props(url)
    mp = str(props.get("model_path", ""))
    if mp and os.path.exists(mp):
        return model_fingerprint(mp)
    return "props:" + mp


def make_backend(kind: str, url: Optional[str], model_path: Optional[str],
                 concurrency: int, store: NoteStore, mock: bool,
                 replay_only: bool, timeout: float) -> object:
    if mock:
        return MockBackend(mock_lexicon(), store.catalog, cache=None, concurrency=1)
    if not url:
        raise SystemExit(f"--{kind}-url is required (or --mock)")
    mid = resolve_model_id(url, model_path)
    cache = ReplayCache(os.path.join(CACHE_DIR, f"{kind}-{mid[:16]}.jsonl"))
    return LlamaServerBackend(url, mid, cache=cache, concurrency=concurrency,
                              replay_only=replay_only, timeout=timeout,
                              label=f"{kind}:{os.path.basename(model_path or url)}")


class Progress:
    def __init__(self, what: str, every: int = 10):
        self.what = what
        self.every = max(1, every)
        self.t0 = time.time()
        self.live_tok = 0
        self.live_calls = 0

    def __call__(self, done: int, total: int, res) -> None:
        if not res.replayed:
            self.live_tok += res.completion_tokens
            self.live_calls += 1
        if done % self.every and done != total:
            return
        el = time.time() - self.t0
        rate = self.live_calls / el if el > 0 and self.live_calls else 0.0
        left = total - done
        eta = left / rate if rate > 0 else 0.0
        log(f"{self.what}: {done}/{total} calls ({done - self.live_calls} replayed) "
            f"elapsed {el/60:.1f} min, {rate*60:.1f} live calls/min, "
            f"{self.live_tok / el if el > 0 else 0:.1f} completion tok/s, "
            f"ETA {eta/60:.1f} min")


def usage_row(role: str, replies, wall_s: float, concurrency: int) -> Dict[str, object]:
    u = A.call_usage(replies)
    n = max(1, u["calls"])
    srv_s = (u["prompt_ms"] + u["decode_ms"]) / 1000.0
    return {"kind": "usage", "role": role, **u, "phase_wall_s": round(wall_s, 1),
            "concurrency": concurrency,
            "per_call_prompt_tokens": u["prompt_tokens"] / n,
            "per_call_uncached_prompt_tokens": (u["prompt_tokens"] - u["cached_tokens"]) / n,
            "per_call_completion_tokens": u["completion_tokens"] / n,
            "per_call_latency_s": u["latency_s"] / n,
            "est_wall_s_at_concurrency": u["latency_s"] / max(1, concurrency),
            "agg_completion_tok_s": (u["completion_tokens"] / (u["latency_s"] / concurrency)
                                     if u["latency_s"] > 0 else 0.0),
            "server_busy_s": srv_s}


def run_edge(store: NoteStore, agents: Sequence[str], backend, every: int):
    edge = A.EdgeAgent(backend)
    items = [(a, store.notes(a)) for a in agents]
    t0 = time.time()
    replies = edge.extract_many(items, progress=Progress("edge agents", every))
    return replies, time.time() - t0


def a2_candidates(world: World) -> np.ndarray:
    """A2's observable prefilter: notes whose rendered text contains a causal
    predicate's surface phrase (systems._lexical_index, observable mode)."""
    assert _ops.OBSERVABLE["lexical_index"], "A2-live needs the observable lexical index"
    idx = _lexical_index(world.corpus)
    return np.concatenate([idx[p] for p in range(N_CAUSAL_PRED) if p in idx])


def run_kernel_reader(world: World, store: NoteStore, backend, chunk: int,
                      seed: int, every: int, limit: int = 0):
    cand = a2_candidates(world).astype(np.int64)
    rng = np.random.default_rng(61_000 + seed)
    rng.shuffle(cand)
    chunks = [cand[i:i + chunk] for i in range(0, len(cand), chunk)]
    if limit:
        chunks = chunks[:limit]
    reader = A.EdgeAgent(backend, role="kernel-reader")
    items = [(f"chunk{c:05d}", [(i, store.text_of(int(r))) for i, r in enumerate(ch)])
             for c, ch in enumerate(chunks)]
    t0 = time.time()
    replies = reader.extract_many(items, progress=Progress("A2 kernel chunks", every))
    can = A.Canonicaliser(store)
    cl = A.Claims()
    for rep, ch in zip(replies, chunks):
        can.add(cl, rep, ch)
    read = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int64)
    return cl, replies, read, time.time() - t0, len(cand)


def system_rows(name: str, world: World, alloc, seed: int,
                live_world: Optional[World], pre_ex=None) -> List[Dict[str, object]]:
    rows = []
    twin = SIM_TWIN.get(name, name)
    # simulated-operator run (paired, same world)
    t0 = time.time()
    rs = run_arch(twin, world, alloc, seed)
    ms = evaluate(world.corpus, world.gold, rs)
    rows.append({"kind": "system", "system": name, "mode": "sim", "arch": twin,
                 "pipeline_s": round(time.time() - t0, 2), **ms})
    # live run
    t0 = time.time()
    if name == "A2_live":
        _ops.set_ranker(ranker_for(twin))
        rl = chunked_long_context(world.corpus, alloc, seed,
                                  near_miss=world.near_miss, pre_ex=pre_ex)
    else:
        rl = run_arch(name, live_world, alloc, seed)
    ml = evaluate(world.corpus, world.gold, rl)
    rows.append({"kind": "system", "system": name, "mode": "live", "arch": twin,
                 "pipeline_s": round(time.time() - t0, 2), **ml})
    return rows


def scale_users(scale: int, seed: int) -> int:
    return len(build_org(scale, seed=seed).user_ids)


def markdown(rows: List[Dict[str, object]], meta: Dict[str, object]) -> str:
    out = [f"# Live Mycelic run `{meta['tag']}`\n",
           f"scale {meta['scale']} (users {meta['n_users']}), seed {meta['seed']}, "
           f"agents run {meta['agents_run']}/{meta['n_users']}, notes {meta['notes_run']}.\n",
           f"edge backend: {meta['edge_backend']}; kernel backend: {meta.get('kernel_backend')}.\n"]
    if meta.get("mock"):
        out.append(f"\n**{MOCK_LABEL}: these numbers are NOT LLM results.**\n")
    for r in rows:
        if r["kind"] == "usage":
            out.append(f"\n**{r['role']} usage**: {r['calls']} calls ({r['replayed']} replayed), "
                       f"prompt {r['prompt_tokens']} tok ({r['cached_tokens']} from slot cache), "
                       f"completion {r['completion_tokens']} tok, phase wall {r['phase_wall_s']} s, "
                       f"recorded latency sum {r['latency_s']:.0f} s, "
                       f"aggregate {r['agg_completion_tok_s']:.1f} completion tok/s.\n")
        if r["kind"] == "diag":
            out.append(f"\n**{r['role']} parse diagnostics**: " +
                       ", ".join(f"{k} {v}" for k, v in r.items() if k not in ("kind", "role")) + "\n")
    sys_rows = [r for r in rows if r["kind"] == "system"]
    if sys_rows:
        names = sorted({r["system"] for r in sys_rows}, key=lambda x: [r["system"] for r in sys_rows].index(x))
        out.append("\n## Systems, live vs simulated operators (same world)\n\n")
        out.append("| metric | " + " | ".join(f"{n} sim | {n} live" for n in names) + " |\n")
        out.append("|---|" + "---|---|" * len(names) + "\n")
        for k in HEADLINE:
            cells = []
            for n in names:
                for mode in ("sim", "live"):
                    r = next((x for x in sys_rows if x["system"] == n and x["mode"] == mode), None)
                    v = r.get(k) if r else None
                    cells.append("-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v)))
            out.append(f"| {k} | " + " | ".join(cells) + " |\n")
    for r in rows:
        if r["kind"] == "gap":
            out.append(f"\n## Operator gap: {r['title']}\n\n{r['table']}")
    eta = [r for r in rows if r["kind"] == "eta"]
    if eta:
        out.append("\n## ETA at measured throughput (edge extraction only)\n\n")
        for r in eta:
            out.append(f"* {r['scale']} users ({r['n_users']} agents): {r['hours']:.1f} h "
                       f"({r['basis']})\n")
    return "".join(out)


def main(argv: Optional[Sequence[str]] = None) -> List[Dict[str, object]]:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=int, default=400)
    ap.add_argument("--seed", type=int, default=701)
    ap.add_argument("--edge-url")
    ap.add_argument("--kernel-url")
    ap.add_argument("--edge-model", help="local GGUF path (fingerprint for the cache key)")
    ap.add_argument("--kernel-model")
    ap.add_argument("--systems", default="H_mycelic_full,H_mycelic_prev,B4_central_triage")
    ap.add_argument("--max-agents", type=int, default=0, help="0 = every user agent")
    ap.add_argument("--kernel-chunks", type=int, default=0,
                    help="limit A2 kernel chunks (0 = all); a limit skips A2 system scoring")
    ap.add_argument("--chunk-notes", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=4, help="edge server slots (-np)")
    ap.add_argument("--kernel-concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--alloc", default="back-loaded")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mock", action="store_true",
                    help="LLM-free deterministic stub (tests / plumbing only)")
    ap.add_argument("--replay-only", action="store_true",
                    help="never call a server; every call must be in the cache")
    ap.add_argument("--log-every", type=int, default=10)
    ap.add_argument("--allow-any-seed", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--eta-scales", default="2000,10000")
    a = ap.parse_args(argv)

    if a.seed not in LIVE_SEEDS and not a.allow_any_seed:
        raise SystemExit(f"live runs use LIVE seeds 700-709 (got {a.seed})")
    tag = os.path.splitext(os.path.basename(a.out))[0] if a.out else \
        f"live_{a.scale}_{a.seed}{'_mock' if a.mock else ''}"
    out = a.out or os.path.join(LIVE_ART, tag + ".jsonl")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    systems = [s for s in a.systems.split(",") if s]

    log(f"building world scale={a.scale} seed={a.seed}")
    world = build_world(a.scale, a.seed)
    alloc = allocation(a.alloc)
    store = NoteStore(world.corpus)
    agents = store.agents()
    run_agents = agents[:a.max_agents] if a.max_agents else agents
    complete = len(run_agents) == len(agents)
    log(f"{len(agents)} user agents, {store.n_notes()} notes; running {len(run_agents)} agents "
        f"({store.n_notes(run_agents)} notes)")

    edge_be = make_backend("edge", a.edge_url, a.edge_model, a.concurrency, store,
                           a.mock, a.replay_only, a.timeout)
    rows: List[Dict[str, object]] = []
    meta = {"kind": "meta", "tag": tag, "scale": a.scale, "seed": a.seed,
            "n_users": len(agents), "agents_run": len(run_agents),
            "notes_run": store.n_notes(run_agents), "n_notes": store.n_notes(),
            "edge_backend": edge_be.label, "edge_model_id": edge_be.model_id,
            "edge_is_llm": edge_be.is_llm, "mock": bool(a.mock),
            "prompt_version": A.PROMPT_VERSION, "alloc": a.alloc,
            "git": git_rev(), "systems": systems,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # ---- edge agents (live) ----
    replies, wall = run_edge(store, run_agents, edge_be, a.log_every)
    claims = A.edge_claims(store, replies)
    live_ex = claims.to_extract_result()
    rows.append(usage_row("edge", replies, wall, a.concurrency))
    rows.append({"kind": "diag", "role": "edge", **claims.diag})

    # ---- extraction quality, live vs simulated operator on the same notes ----
    read = np.concatenate([store.rids_of(x) for x in run_agents])
    sim_ul = world.user_layer(alloc[USER], a.seed)
    q_live = S.extraction_quality(world.corpus, world.gold, live_ex, read)
    q_live.update(S.signature_accuracy(world.corpus, live_ex))
    sim_ex = sim_ul.ex.take(np.nonzero(np.isin(sim_ul.ex.rid, read))[0])
    q_sim = S.extraction_quality(world.corpus, world.gold, sim_ex, read)
    q_sim.update(S.signature_accuracy(world.corpus, sim_ex, drop_invented=True))
    q_txt = S.text_signature_intrinsic(world.corpus, read, store.signature)
    live_lbl = "MOCK" if a.mock else "live edge"
    rows.append({"kind": "extraction", "operator": f"live:{edge_be.label}", **q_live})
    rows.append({"kind": "extraction", "operator": f"sim:{alloc[USER].name}", **q_sim})
    rows.append({"kind": "signature_text_intrinsic", **q_txt})
    rows.append({"kind": "gap", "title": f"edge extraction ({alloc[USER].name} tier vs {live_lbl})",
                 "table": S.gap_table(ANCHORS[alloc[USER].name], q_sim, q_live,
                                      live_lbl, f"sim {alloc[USER].name}")})
    log(f"edge extraction: live recall {q_live['recall']:.3f} precision {q_live['precision']:.3f} "
        f"facet recall {q_live.get('facet_recall', float('nan')):.3f} | sim recall "
        f"{q_sim['recall']:.3f} precision {q_sim['precision']:.3f}")

    # ---- A2-live: kernel model reads prefiltered notes in chunks ----
    pre_ex = None
    kernel_ok = False
    if "A2_live" in systems:
        if a.kernel_url or a.mock:
            kbe = make_backend("kernel", a.kernel_url, a.kernel_model, a.kernel_concurrency,
                               store, a.mock, a.replay_only, a.timeout)
            meta["kernel_backend"] = kbe.label
            meta["kernel_model_id"] = kbe.model_id
            kcl, krep, kread, kwall, ncand = run_kernel_reader(
                world, store, kbe, a.chunk_notes, a.seed, a.log_every, a.kernel_chunks)
            pre_ex = kcl.to_extract_result()
            kernel_ok = len(kread) == ncand
            rows.append(usage_row("kernel-reader", krep, kwall, a.kernel_concurrency))
            rows.append({"kind": "diag", "role": "kernel-reader", **kcl.diag})
            kt = alloc[ENT]
            qk = S.extraction_quality(world.corpus, world.gold, pre_ex, kread)
            qk.update(S.signature_accuracy(world.corpus, pre_ex))
            sk = _ops.extract(world.corpus, kread, kt, np.random.default_rng(61_500 + a.seed),
                              near_miss=world.near_miss)
            qs = S.extraction_quality(world.corpus, world.gold, sk, kread)
            qs.update(S.signature_accuracy(world.corpus, sk, drop_invented=True))
            rows.append({"kind": "extraction", "operator": f"live:{kbe.label}", **qk})
            rows.append({"kind": "extraction", "operator": f"sim:{kt.name}", **qs})
            klbl = "MOCK" if a.mock else "live kernel reader"
            rows.append({"kind": "gap", "title": f"A2 kernel reading ({kt.name} tier vs {klbl})",
                         "table": S.gap_table(kt, qs, qk, klbl, f"sim {kt.name}")})
        else:
            log("A2_live skipped: no --kernel-url")

    # ---- systems: live claims through the unchanged pipeline, paired with sim ----
    if systems and complete:
        live_ul = user_extract(world.corpus, alloc[USER], np.random.default_rng(0),
                               ex=live_ex)
        live_world = dataclasses.replace(world, _ul_cache={
            f"{alloc[USER].name}:{a.seed}": live_ul})
        for name in systems:
            if name == "A2_live" and not kernel_ok:
                continue
            for r in system_rows(name, world, alloc, a.seed, live_world, pre_ex):
                rows.append(r)
                log(f"{name:18s} {r['mode']:4s} found {r['found_anywhere_in_register']:.3f} "
                    f"supported {r.get('found_supported', float('nan')):.3f} "
                    f"AP {r['average_precision']:.3f} prec {r['discovery_precision']:.3f} "
                    f"decoy {r['decoy_acceptance_all']:.3f}")
    elif systems:
        log("systems skipped: only part of the user agents ran (--max-agents)")

    # ---- ETA at the measured edge throughput ----
    ur = rows[0]
    if ur["calls"] and ur["latency_s"] > 0 and not a.mock:
        per_agent = ur["latency_s"] / ur["calls"] / max(1, a.concurrency)
        per_note = per_agent * ur["calls"] / max(1, meta["notes_run"])
        for sc in [int(x) for x in a.eta_scales.split(",") if x]:
            nu = scale_users(sc, a.seed)
            notes_est = store.n_notes() / len(agents) * nu
            rows.append({"kind": "eta", "scale": sc, "n_users": nu,
                         "hours": notes_est * per_note / 3600.0,
                         "basis": f"{per_note*1000:.1f} ms per note at concurrency "
                                  f"{a.concurrency} (recorded latencies)"})

    meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows.insert(0, meta)
    with open(out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=_jsonable) + "\n")
    with open(os.path.splitext(out)[0] + ".md", "w") as fh:
        fh.write(markdown(rows, meta))
    log(f"wrote {out} and {os.path.splitext(out)[0]}.md")
    return rows


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


if __name__ == "__main__":
    main()
