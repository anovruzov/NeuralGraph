"""End-to-end throughput of a running llama-server on real agent prompts.

    python3 -m research.mycelic.live.bench --url http://127.0.0.1:8081 \
        --concurrency 4 --agents 12 --seed 700 [--role edge|kernel]

Builds the 400-user world of a LIVE seed, renders real edge-agent prompts
(or A2 kernel-reader chunks with ``--role kernel``), warms every server slot
with the shared system prompt, then sends ``--agents`` requests with at most
``--concurrency`` in flight, WITHOUT the replay cache (every call is real).
Prints one JSON line: wall time, prompt tokens actually prefilled, completion
tokens, aggregate completion tokens/s and seconds per agent / per note.
Seed 700 is the benchmark seed, so prompt work on seed 701 stays separate.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from ..runner import build_world
from . import agents as A
from .backend import LlamaServerBackend
from .notes import NoteStore


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--agents", type=int, default=12)
    ap.add_argument("--seed", type=int, default=700)
    ap.add_argument("--scale", type=int, default=400)
    ap.add_argument("--role", default="edge", choices=["edge", "kernel"])
    ap.add_argument("--chunk-notes", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--tag", default="")
    a = ap.parse_args(argv)
    w = build_world(a.scale, a.seed)
    st = NoteStore(w.corpus)
    if a.role == "edge":
        ags = st.agents()[a.offset:a.offset + a.agents]
        items = [(x, st.notes(x)) for x in ags]
    else:
        from .run import a2_candidates
        cand = a2_candidates(w)[a.offset * a.chunk_notes:]
        items = [(f"c{i}", [(j, st.text_of(int(r))) for j, r in
                            enumerate(cand[i * a.chunk_notes:(i + 1) * a.chunk_notes])])
                 for i in range(a.agents)]
    be = LlamaServerBackend(a.url, "bench", cache=None, concurrency=a.concurrency)
    ag = A.EdgeAgent(be)
    # warm every slot with the shared system prompt (one tiny request each)
    warm = [(f"warm{i}", st.notes(st.agents()[-1 - i])[:1]) for i in range(a.concurrency)]
    t0 = time.time()
    ag.extract_many(warm)
    t_warm = time.time() - t0
    t0 = time.time()
    reps = ag.extract_many(items)
    wall = time.time() - t0
    u = A.call_usage(reps)
    n_notes = sum(len(n) for _, n in items)
    out = {"tag": a.tag, "role": a.role, "concurrency": a.concurrency, "agents": len(items),
           "notes": n_notes, "wall_s": round(wall, 1), "warmup_s": round(t_warm, 1),
           "prompt_tokens": u["prompt_tokens"], "cached_tokens": u["cached_tokens"],
           "prefilled_tokens": u["prompt_tokens"] - u["cached_tokens"],
           "completion_tokens": u["completion_tokens"],
           "agg_completion_tok_s": round(u["completion_tokens"] / wall, 2),
           "agg_prefill_tok_s": round((u["prompt_tokens"] - u["cached_tokens"]) / wall, 2),
           "server_prefill_tok_s": round((u["prompt_tokens"] - u["cached_tokens"])
                                         / max(1e-9, u["prompt_ms"] / 1000), 2),
           "server_decode_tok_s_per_stream": round(u["completion_tokens"]
                                                   / max(1e-9, u["decode_ms"] / 1000), 2),
           "s_per_agent": round(wall / len(items), 2),
           "ms_per_note": round(1000 * wall / n_notes, 1),
           "lines_per_note": round(sum(len(r.lines) for r in reps) / n_notes, 3),
           "truncated": sum(r.call.finish_reason == "length" for r in reps)}
    print(json.dumps(out), flush=True)
    return out


if __name__ == "__main__":
    main()
