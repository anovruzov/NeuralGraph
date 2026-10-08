"""Run Mycelic with live LLM edge agents, paired with the simulated operators.

One command per stage (each resumable; servers from live/scripts/serve.sh on
Linux or live/mac_setup.sh on a Mac; endpoints and paths from flags or env):

    python3 -m research.mycelic.live.run --stage smoke   # 400 users, seed 701, 40 agents
    python3 -m research.mycelic.live.run --stage 400     # seed 702, every agent + systems
    python3 -m research.mycelic.live.run --stage 2000    # seed 703
    python3 -m research.mycelic.live.run --stage 10000   # seed 704

or fully explicit:

    python3 -m research.mycelic.live.run --scale 400 --seed 701 \
        --edge-url http://127.0.0.1:8081 --kernel-url http://127.0.0.1:8082 \
        --systems H_mycelic_full,H_mycelic_prev,B4_central_triage,A2_live \
        --max-agents 40 --out research/mycelic/artifacts/live/smoke701.jsonl

Environment (each also a flag): MYCELIC_EDGE_URL, MYCELIC_KERNEL_URL,
MYCELIC_EDGE_MODEL / MYCELIC_KERNEL_MODEL (local GGUF paths, for the sha256
fingerprint; read from the server's /props when unset), MYCELIC_LIVE_OUT_DIR,
MYCELIC_LIVE_CACHE_DIR, MYCELIC_EDGE_NP / MYCELIC_KERNEL_NP (requests in
flight; default: the server's slot count).

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
resumes where it stopped and a rerun is bit-for-bit identical.  After the
first 50 live agent calls (20 kernel chunks) the run prints the projected
wall time of the rest of this stage and of every later stage.

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
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import ops as _ops
from ..corpus import N_CAUSAL_PRED, PRED_SURFACE
from ..evalm import evaluate
from ..models import ANCHORS, allocation
from ..org import ENT, USER, build_org
from ..runner import ART, World, build_world, ranker_for, run_arch
from ..systems import _lexical_index, chunked_long_context, user_extract
from . import agents as A
from . import envinfo
from .backend import (MOCK_LABEL, LlamaServerBackend, MockBackend, ReplayCache,
                      model_fingerprint)
from .notes import NoteStore
from . import score as S

LIVE_SEEDS = range(700, 710)
MACHINE = envinfo.machine_info()
LIVE_ART = os.path.join(ART, "live")
CACHE_DIR = os.path.join(LIVE_ART, "cache")
SIM_TWIN = {"A2_live": "A2_chunked_ctx"}
ALL_SYSTEMS = "H_mycelic_full,H_mycelic_prev,B4_central_triage,A2_live"
# one command per stage; a stage's out file is <out-dir>/stage_<name>_s<seed>.jsonl
STAGES: Dict[str, Dict[str, object]] = {
    "smoke": {"scale": 400, "seed": 701, "max_agents": 40, "systems": ""},
    "400": {"scale": 400, "seed": 702, "max_agents": 0, "systems": ALL_SYSTEMS},
    "2000": {"scale": 2000, "seed": 703, "max_agents": 0, "systems": ALL_SYSTEMS},
    "10000": {"scale": 10_000, "seed": 704, "max_agents": 0, "systems": ALL_SYSTEMS},
}
STAGE_ORDER = ["smoke", "400", "2000", "10000"]
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


def resolve_model_id(url: str, path: Optional[str], props_path: str = "") -> str:
    """sha256 of the GGUF file the server runs (sidecar-cached): the given
    path, else the server's /props model_path when that file is local."""
    if path:
        return model_fingerprint(path)
    if props_path and os.path.exists(props_path):
        return model_fingerprint(props_path)
    return "props:" + props_path


def make_backend(kind: str, url: Optional[str], model_path: Optional[str],
                 concurrency: int, store: NoteStore, mock: bool,
                 replay_only: bool, timeout: float, cache_dir: str = CACHE_DIR
                 ) -> Tuple[object, Dict[str, object]]:
    """The backend for one role, and the role's run_env entry."""
    if mock:
        be = MockBackend(mock_lexicon(), store.catalog, cache=None, concurrency=1)
        return be, {"backend_kind": "mock", "url": None, "url_kind": "none",
                    "model_sha256": None, "llama_cpp": None, "concurrency": 1}
    if not url:
        raise SystemExit(f"--{kind}-url (or MYCELIC_{kind.upper()}_URL) is required (or --mock)")
    if replay_only:
        info = {"url": url, "url_kind": envinfo.url_kind(url), "backend_kind": "llama-server",
                "llama_cpp": None, "model_path": model_path or "", "total_slots": 0}
    else:
        info = envinfo.preflight(url, kind)
    mid = resolve_model_id(url, model_path, str(info.get("model_path", "")))
    conc = int(concurrency or info.get("total_slots") or 4)
    if info.get("total_slots") and conc != info["total_slots"]:
        log(f"note: {kind} concurrency {conc} != server slots {info['total_slots']}")
    cache = ReplayCache(os.path.join(cache_dir, f"{kind}-{mid[:16]}.jsonl"))
    be = LlamaServerBackend(url, mid, cache=cache, concurrency=conc,
                            replay_only=replay_only, timeout=timeout,
                            label=f"{kind}:{os.path.basename(model_path or info.get('model_path') or url)}")
    env = {"backend_kind": info["backend_kind"], "url": url, "url_kind": info["url_kind"],
           "model_sha256": mid, "model_file": os.path.basename(str(model_path or info.get("model_path") or "")),
           "llama_cpp": info.get("llama_cpp"), "server_slots": info.get("total_slots"),
           "concurrency": conc, "replay_only": bool(replay_only)}
    # stored with every new call in the cache (CallResult.recorded)
    be.record_info = {"machine": MACHINE, "llama_cpp": info.get("llama_cpp"),
                      "server_slots": info.get("total_slots"), "url_kind": info["url_kind"],
                      "model_sha256": mid}
    return be, env


def recorded_summary(replies) -> List[Dict[str, object]]:
    """Where the calls behind a result were made (from the cache entries):
    distinct (machine, llama.cpp build, slots) with call counts."""
    groups: Dict[str, Dict[str, object]] = {}
    for r in replies:
        rec = r.call.recorded
        k = json.dumps(rec, sort_keys=True, default=str)
        g = groups.setdefault(k, {"recorded": rec, "calls": 0, "replayed_here": 0})
        g["calls"] += 1
        g["replayed_here"] += int(r.call.replayed)
    return list(groups.values())


class Progress:
    """Progress / ETA logging; after ``check_after`` live calls it calls
    ``project(seconds_per_call, done, total)`` once (throughput self-check)."""

    def __init__(self, what: str, every: int = 10, check_after: int = 0,
                 project: Optional[Callable[[float, int, int], None]] = None):
        self.what = what
        self.every = max(1, every)
        self.t0 = time.time()
        self.live_tok = 0
        self.live_calls = 0
        self.check_after = check_after
        self.project = project
        self.checked = False
        self.sec_per_call: Optional[float] = None

    def __call__(self, done: int, total: int, res) -> None:
        if not res.replayed:
            self.live_tok += res.completion_tokens
            self.live_calls += 1
        el = time.time() - self.t0
        if self.live_calls:
            self.sec_per_call = el / self.live_calls
        if (self.project and not self.checked and self.check_after
                and self.live_calls >= min(self.check_after, total)):
            self.checked = True
            self.project(el / self.live_calls, done, total)
        if done % self.every and done != total:
            return
        rate = self.live_calls / el if el > 0 and self.live_calls else 0.0
        left = total - done
        eta = left / rate if rate > 0 else 0.0
        log(f"{self.what}: {done}/{total} calls ({done - self.live_calls} replayed) "
            f"elapsed {el/60:.1f} min, {rate*60:.1f} live calls/min, "
            f"{self.live_tok / el if el > 0 else 0:.1f} completion tok/s, "
            f"ETA {eta/60:.1f} min")


def fmt_h(sec: float) -> str:
    return f"{sec/3600:.1f} h" if sec >= 5400 else f"{sec/60:.0f} min"


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


def run_edge(store: NoteStore, agents: Sequence[str], backend, prog: Progress):
    edge = A.EdgeAgent(backend)
    items = [(a, store.notes(a)) for a in agents]
    t0 = time.time()
    replies = edge.extract_many(items, progress=prog)
    return replies, time.time() - t0


def a2_candidates(world: World) -> np.ndarray:
    """A2's observable prefilter: notes whose rendered text contains a causal
    predicate's surface phrase (systems._lexical_index, observable mode)."""
    assert _ops.OBSERVABLE["lexical_index"], "A2-live needs the observable lexical index"
    idx = _lexical_index(world.corpus)
    return np.concatenate([idx[p] for p in range(N_CAUSAL_PRED) if p in idx])


def kernel_chunks(world: World, chunk: int, seed: int) -> Tuple[List[np.ndarray], int]:
    cand = a2_candidates(world).astype(np.int64)
    rng = np.random.default_rng(61_000 + seed)
    rng.shuffle(cand)
    return [cand[i:i + chunk] for i in range(0, len(cand), chunk)], len(cand)


def run_kernel_reader(store: NoteStore, backend, chunks: List[np.ndarray], prog: Progress):
    reader = A.EdgeAgent(backend, role="kernel-reader")
    items = [(f"chunk{c:05d}", [(i, store.text_of(int(r))) for i, r in enumerate(ch)])
             for c, ch in enumerate(chunks)]
    t0 = time.time()
    replies = reader.extract_many(items, progress=prog)
    can = A.Canonicaliser(store)
    cl = A.Claims()
    for rep, ch in zip(replies, chunks):
        can.add(cl, rep, ch)
    read = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int64)
    return cl, replies, read, time.time() - t0


def system_rows(name: str, world: World, alloc, seed: int,
                live_world: Optional[World], pre_ex=None,
                llm: Optional[Dict[str, Dict[str, object]]] = None) -> List[Dict[str, object]]:
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
    # the real model calls this live run consumed (its sim twin consumed none)
    role = "kernel-reader" if name == "A2_live" else "edge"
    u = (llm or {}).get(role, {})
    real = {"llm_role": role, "llm_calls": u.get("calls"),
            "llm_prompt_tokens": u.get("prompt_tokens"),
            "llm_cached_prompt_tokens": u.get("cached_tokens"),
            "llm_completion_tokens": u.get("completion_tokens"),
            "llm_wall_s_est": u.get("est_wall_s_at_concurrency"),
            "llm_phase_wall_s": u.get("phase_wall_s")}
    rows.append({"kind": "system", "system": name, "mode": "live", "arch": twin,
                 "pipeline_s": round(time.time() - t0, 2), **real, **ml})
    return rows


def scale_users(scale: int, seed: int) -> int:
    return len(build_org(scale, seed=seed).user_ids)


def markdown(rows: List[Dict[str, object]], meta: Dict[str, object]) -> str:
    out = [f"# Live Mycelic run `{meta['tag']}`\n",
           f"scale {meta['scale']} (users {meta['n_users']}), seed {meta['seed']}, "
           f"agents run {meta['agents_run']}/{meta['n_users']}, notes {meta['notes_run']}.\n",
           f"edge backend: {meta['edge_backend']}; kernel backend: {meta.get('kernel_backend')}; "
           f"prompt {meta.get('prompt_version')}.\n"]
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
        for k in ("llm_calls", "llm_prompt_tokens", "llm_completion_tokens", "llm_wall_s_est",
                  "compute_units", "wall_seconds"):
            cells = []
            for n in names:
                for mode in ("sim", "live"):
                    r = next((x for x in sys_rows if x["system"] == n and x["mode"] == mode), None)
                    v = r.get(k) if r else None
                    cells.append("-" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v)))
            out.append(f"| {k} | " + " | ".join(cells) + " |\n")
        out.append("\n_`llm_*`: real calls / tokens / estimated wall time of the live model calls; "
                   "`compute_units` and `wall_seconds` are the simulator's meter (modelled). Kernel "
                   "synthesis checks are simulated coins in both columns._\n")
    for r in rows:
        if r["kind"] == "gap":
            out.append(f"\n## Operator gap: {r['title']}\n\n{r['table']}")
    proj = [r for r in rows if r["kind"] == "projection"]
    if proj:
        out.append("\n## Projected wall time at the measured throughput (this machine)\n\n")
        for r in proj:
            out.append(f"* [{r['role']}, measured {r['basis']}] {r['text']}\n")
    env = meta.get("run_env", {})
    if env:
        m = env.get("machine", {})
        out.append(f"\n_Run environment (this invocation): {m.get('cpu')} ({m.get('machine')}, "
                   f"{m.get('n_cpus')} cpus, {m.get('mem_gb')} GB), {m.get('platform')}; git "
                   f"{env.get('git')}; prompt {env.get('prompt_version')}._\n")
        for role in ("edge", "kernel"):
            e = env.get(role) or {}
            if not e:
                continue
            out.append(f"\n_{role}: {e.get('backend_kind')} at {e.get('url')} ({e.get('url_kind')}), "
                       f"model sha256 {str(e.get('model_sha256'))[:16]}…; calls recorded on:_\n")
            for g in e.get("calls_recorded_on") or []:
                rec = g.get("recorded") or {}
                mm = rec.get("machine") or {}
                out.append(f"  * {g['calls']} calls ({g['replayed_here']} replayed here): "
                           f"{mm.get('cpu', 'unrecorded')} {mm.get('platform', '')}, llama.cpp "
                           f"{rec.get('llama_cpp')}, {rec.get('server_slots')} slots\n")
    return "".join(out)


def stage_agents(stage: str, seed: int) -> int:
    st = STAGES[stage]
    n = scale_users(int(st["scale"]), seed)
    return min(n, int(st["max_agents"])) if st["max_agents"] else n


def make_projection(role: str, stage: Optional[str], seed: int, per_unit_of: Callable[[str], float],
                    unit: str, rows: List[Dict[str, object]]) -> Callable[[float, int, int], None]:
    """Throughput self-check: seconds per call measured so far -> projected
    wall time for the rest of this stage and for every later stage."""
    def project(sec_per_call: float, done: int, total: int) -> None:
        rem = (total - done) * sec_per_call
        parts = [f"rest of this run: {total - done} {unit}s -> {fmt_h(rem)}"]
        later = STAGE_ORDER[STAGE_ORDER.index(stage) + 1:] if stage in STAGE_ORDER else \
            [x for x in STAGE_ORDER if x != "smoke"]
        for st in later:
            n = per_unit_of(st)
            parts.append(f"stage {st}: ~{n:.0f} {unit}s -> {fmt_h(n * sec_per_call)}")
        text = "; ".join(parts)
        log(f"THROUGHPUT SELF-CHECK ({role}, {sec_per_call:.1f} s per {unit} wall at this "
            f"concurrency): {text}")
        rows.append({"kind": "projection", "role": role, "sec_per_call": sec_per_call,
                     "basis": f"{sec_per_call:.1f} s per {unit} after {done} calls",
                     "text": text})
    return project


def main(argv: Optional[Sequence[str]] = None) -> List[Dict[str, object]]:
    E = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=STAGE_ORDER,
                    help="preset scale / seed / agents / systems / out (see STAGES)")
    ap.add_argument("--scale", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--edge-url", default=E("MYCELIC_EDGE_URL"))
    ap.add_argument("--kernel-url", default=E("MYCELIC_KERNEL_URL"))
    ap.add_argument("--edge-model", default=E("MYCELIC_EDGE_MODEL"),
                    help="local GGUF path (sha256 fingerprint for the cache key)")
    ap.add_argument("--kernel-model", default=E("MYCELIC_KERNEL_MODEL"))
    ap.add_argument("--systems", default=None,
                    help=f"comma list (default H_mycelic_full,H_mycelic_prev,B4_central_triage; "
                         f"stages: {ALL_SYSTEMS})")
    ap.add_argument("--max-agents", type=int, default=None, help="0 = every user agent")
    ap.add_argument("--kernel-chunks", type=int, default=0,
                    help="limit A2 kernel chunks (0 = all); a limit skips A2 system scoring")
    ap.add_argument("--chunk-notes", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=int(E("MYCELIC_EDGE_NP", "0")),
                    help="edge requests in flight (default: the server's -np slots)")
    ap.add_argument("--kernel-concurrency", type=int, default=int(E("MYCELIC_KERNEL_NP", "0")))
    ap.add_argument("--timeout", type=float, default=3600.0,
                    help="per-request read timeout, s (a long agent on a busy CPU server can "
                         "take over 15 min; a timeout cancels the server task and restarts it)")
    ap.add_argument("--alloc", default="back-loaded")
    ap.add_argument("--out", default=None)
    ap.add_argument("--out-dir", default=E("MYCELIC_LIVE_OUT_DIR", LIVE_ART))
    ap.add_argument("--cache-dir", default=E("MYCELIC_LIVE_CACHE_DIR"))
    ap.add_argument("--mock", action="store_true",
                    help="LLM-free deterministic stub (tests / plumbing only)")
    ap.add_argument("--replay-only", action="store_true",
                    help="never call a server; every call must be in the cache")
    ap.add_argument("--edge-replay-only", action="store_true",
                    help="edge calls from the cache only (e.g. to add A2_live after the "
                         "edge server was stopped)")
    ap.add_argument("--log-every", type=int, default=10)
    ap.add_argument("--check-after", type=int, default=50,
                    help="throughput self-check after this many live agent calls")
    ap.add_argument("--allow-any-seed", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    st = STAGES.get(a.stage or "", {})
    scale = a.scale if a.scale is not None else int(st.get("scale", 400))
    seed = a.seed if a.seed is not None else int(st.get("seed", 701))
    max_agents = a.max_agents if a.max_agents is not None else int(st.get("max_agents", 0))
    sys_spec = a.systems if a.systems is not None else \
        str(st.get("systems", "H_mycelic_full,H_mycelic_prev,B4_central_triage"))
    systems = [x for x in sys_spec.split(",") if x]
    if seed not in LIVE_SEEDS and not a.allow_any_seed:
        raise SystemExit(f"live runs use LIVE seeds 700-709 (got {seed})")
    if a.out:
        out = a.out
    else:
        base = f"stage_{a.stage}_s{seed}" if a.stage else f"live_{scale}_{seed}"
        out = os.path.join(a.out_dir, base + ("_mock" if a.mock else "") + ".jsonl")
    tag = os.path.splitext(os.path.basename(out))[0]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cache_dir = a.cache_dir or os.path.join(a.out_dir, "cache")

    log(f"building world scale={scale} seed={seed}")
    world = build_world(scale, seed)
    alloc = allocation(a.alloc)
    store = NoteStore(world.corpus)
    agents = store.agents()
    run_agents = agents[:max_agents] if max_agents else agents
    complete = len(run_agents) == len(agents)
    log(f"{len(agents)} user agents, {store.n_notes()} notes; running {len(run_agents)} agents "
        f"({store.n_notes(run_agents)} notes)")

    edge_be, edge_env = make_backend("edge", a.edge_url, a.edge_model, a.concurrency, store,
                                     a.mock, a.replay_only or a.edge_replay_only, a.timeout,
                                     cache_dir)
    rows: List[Dict[str, object]] = []
    run_env: Dict[str, object] = {"machine": MACHINE, "edge": edge_env,
                                  "kernel": None, "git": git_rev(),
                                  "prompt_version": A.PROMPT_VERSION, "harness": "mycelic-live/2"}
    meta = {"kind": "meta", "tag": tag, "stage": a.stage, "scale": scale, "seed": seed,
            "n_users": len(agents), "agents_run": len(run_agents),
            "notes_run": store.n_notes(run_agents), "n_notes": store.n_notes(),
            "edge_backend": edge_be.label, "edge_model_id": edge_be.model_id,
            "edge_is_llm": edge_be.is_llm, "mock": bool(a.mock),
            "prompt_version": A.PROMPT_VERSION, "alloc": a.alloc,
            "git": run_env["git"], "systems": systems, "run_env": run_env,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # ---- edge agents (live), with the throughput self-check ----
    edge_proj = make_projection("edge agents", a.stage, seed,
                                lambda x: stage_agents(x, seed), "agent", rows)
    prog = Progress("edge agents", a.log_every,
                    check_after=0 if a.mock else a.check_after, project=edge_proj)
    replies, wall = run_edge(store, run_agents, edge_be, prog)
    claims = A.edge_claims(store, replies)
    live_ex = claims.to_extract_result()
    rows.append(usage_row("edge", replies, wall, edge_be.concurrency))
    edge_env["calls_recorded_on"] = recorded_summary(replies)
    rows.append({"kind": "diag", "role": "edge", **claims.diag})

    # ---- extraction quality, live vs simulated operator on the same notes ----
    read = np.concatenate([store.rids_of(x) for x in run_agents])
    sim_ul = world.user_layer(alloc[USER], seed)
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
            kbe, kenv = make_backend("kernel", a.kernel_url, a.kernel_model,
                                     a.kernel_concurrency, store, a.mock, a.replay_only,
                                     a.timeout, cache_dir)
            run_env["kernel"] = kenv
            meta["kernel_backend"] = kbe.label
            meta["kernel_model_id"] = kbe.model_id
            chunks, ncand = kernel_chunks(world, a.chunk_notes, seed)
            if a.kernel_chunks:
                chunks = chunks[:a.kernel_chunks]
            frac = ncand / max(1, store.n_notes())
            per_user = store.n_notes() / max(1, len(agents))
            kproj = make_projection(
                "A2 kernel reader", a.stage, seed,
                lambda x: scale_users(int(STAGES[x]["scale"]), seed) * per_user * frac
                / a.chunk_notes if STAGES[x]["systems"] else 0.0, "chunk", rows)
            kprog = Progress("A2 kernel chunks", a.log_every,
                             check_after=0 if a.mock else 20, project=kproj)
            kcl, krep, kread, kwall = run_kernel_reader(store, kbe, chunks, kprog)
            pre_ex = kcl.to_extract_result()
            kernel_ok = len(kread) == ncand
            rows.append(usage_row("kernel-reader", krep, kwall, kbe.concurrency))
            kenv["calls_recorded_on"] = recorded_summary(krep)
            rows.append({"kind": "diag", "role": "kernel-reader", **kcl.diag})
            kt = alloc[ENT]
            qk = S.extraction_quality(world.corpus, world.gold, pre_ex, kread)
            qk.update(S.signature_accuracy(world.corpus, pre_ex))
            sk = _ops.extract(world.corpus, kread, kt, np.random.default_rng(61_500 + seed),
                              near_miss=world.near_miss)
            qs = S.extraction_quality(world.corpus, world.gold, sk, kread)
            qs.update(S.signature_accuracy(world.corpus, sk, drop_invented=True))
            rows.append({"kind": "extraction", "operator": f"live:{kbe.label}", **qk})
            rows.append({"kind": "extraction", "operator": f"sim:{kt.name}", **qs})
            klbl = "MOCK" if a.mock else "live kernel reader"
            rows.append({"kind": "gap", "title": f"A2 kernel reading ({kt.name} tier vs {klbl})",
                         "table": S.gap_table(kt, qs, qk, klbl, f"sim {kt.name}")})
        else:
            log("A2_live skipped: no --kernel-url / MYCELIC_KERNEL_URL")

    # ---- systems: live claims through the unchanged pipeline, paired with sim ----
    if systems and complete:
        live_ul = user_extract(world.corpus, alloc[USER], np.random.default_rng(0),
                               ex=live_ex)
        live_world = dataclasses.replace(world, _ul_cache={
            f"{alloc[USER].name}:{seed}": live_ul})
        for name in systems:
            if name == "A2_live" and not kernel_ok:
                continue
            usage = {r["role"]: r for r in rows if r["kind"] == "usage"}
            for r in system_rows(name, world, alloc, seed, live_world, pre_ex, usage):
                rows.append(r)
                log(f"{name:18s} {r['mode']:4s} found {r['found_anywhere_in_register']:.3f} "
                    f"supported {r.get('found_supported', float('nan')):.3f} "
                    f"AP {r['average_precision']:.3f} prec {r['discovery_precision']:.3f} "
                    f"decoy {r['decoy_acceptance_all']:.3f}")
    elif systems:
        log("systems skipped: only part of the user agents ran (--max-agents)")

    # ---- end-of-run projection from the whole phase (if anything ran live) ----
    if prog.live_calls and not a.mock and not prog.checked:
        edge_proj(wall / prog.live_calls, len(run_agents), len(run_agents))

    meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows.insert(0, meta)
    with open(out, "w") as fh:
        for r in rows:
            r.setdefault("run_env", run_env)
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
