"""Tesseract router coverage on LoCoMo: where questions route and whether their gold evidence is a candidate.

For each category 1-4 question that cites evidence (dia_ids, see retrieval_eval.evidence_ids):
- route: the highest-scoring store type of detect_query_type, or "none" when OPEN scores
  higher (OPEN has no store) or no marker fires. This is the rule of tesseract.routed_store_type.
- candidates: the nodes the four stores pass to fusion inside Tesseract.retrieve.
- coverage: how many gold dia_ids are among the candidates. Gold outside every store's list
  cannot be retrieved, whatever the fusion weights.

Gold ids are pooled per route (one id counts once per question).

Usage:
    EMBEDDER=hash python3 research/benchmarks/router_coverage.py
    EMBEDDER=hash ONLY_CONV=0 python3 research/benchmarks/router_coverage.py
"""
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling harness modules

import retrieval_eval  # noqa: E402
from NeuralGraph.research.retrieval.tesseract import Tesseract, detect_query_type  # noqa: E402

STORES = ("_temporal_store", "_entity_store", "_reasoning_store", "_adversarial_store")
STORE_TYPES = ("temporal", "entity", "multi_hop", "adversarial")


def route(scores: dict[str, float]) -> str:
    """Top store type; "none" when OPEN scores higher or nothing fires (store wins a tie with OPEN)."""
    best = max(STORE_TYPES, key=lambda t: scores.get(t, 0.0))
    if scores.get(best, 0.0) <= 0.0 or scores[best] < scores.get("open", 0.0):
        return "none"
    return best


def capture_candidates(tesseract: Tesseract) -> dict[str, list]:
    """Record what each store returns to Tesseract.retrieve (cleared by the caller per question)."""
    captured: dict[str, list] = {}
    for name in STORES:
        store = getattr(tesseract, name)

        async def wrapped(*args, _orig=store.retrieve, _name=name, **kwargs):
            captured[_name] = await _orig(*args, **kwargs)
            return captured[_name]

        store.retrieve = wrapped
    return captured


async def coverage(data: list[dict], only_conv=None, http=None, log=print) -> dict[str, Counter]:
    """{route: Counter(questions, gold, covered)} plus "ALL"."""
    only_conv = retrieval_eval.ONLY_CONV if only_conv is None else only_conv
    out: dict[str, Counter] = {}
    t0 = time.time()
    for conv_idx, conv in enumerate(data):
        if only_conv and conv_idx not in only_conv:
            continue
        storage, _linker, _nodes, _speakers = await retrieval_eval.ingest(http, conv_idx, conv)
        tesseract = Tesseract(storage)
        captured = capture_candidates(tesseract)
        qas = [qa for qa in conv.get("qa", []) if qa.get("category") in retrieval_eval.CATEGORIES
               and retrieval_eval.evidence_ids(qa)]
        qvecs = await retrieval_eval.cached_embeddings(http, f"conv{conv_idx}_questions_all",
                                                       [qa["question"] for qa in conv.get("qa", [])])
        qvec_by_text = {qa["question"]: v for qa, v in zip(conv.get("qa", []), qvecs)}
        for qa in qas:
            captured.clear()
            await tesseract.retrieve(qa["question"], qvec_by_text[qa["question"]], f"conv_{conv_idx}",
                                     limit=retrieval_eval.TOP_K)
            candidates = {(n.metadata or {}).get("dia_id") for results in captured.values() for n, _ in results}
            gold = retrieval_eval.evidence_ids(qa)
            for key in (route(detect_query_type(qa["question"])), "ALL"):
                c = out.setdefault(key, Counter())
                c["questions"] += 1
                c["gold"] += len(gold)
                c["covered"] += sum(1 for g in gold if g in candidates)
        log(f"conv {conv_idx}: {len(qas)} questions done ({time.time() - t0:.0f}s)")
    return out


def format_table(out: dict[str, Counter]) -> list[str]:
    lines = [f"{'route':12} {'questions':>9} {'gold ids':>9} {'candidate':>9} {'in no list':>11}"]
    order = [r for r in (*STORE_TYPES, "none") if r in out] + ["ALL"]
    for key in order:
        c = out.get(key, Counter())
        missing = c["gold"] - c["covered"]
        share = f"{100 * missing / c['gold']:.1f}%" if c["gold"] else "-"
        lines.append(f"{key:12} {c['questions']:9d} {c['gold']:9d} {c['covered']:9d} {missing:5d} {share:>5}")
    return lines


async def main():
    data = json.load(open(Path(__file__).resolve().parents[2] / "research" / "datasets" / "locomo10.json"))
    if retrieval_eval.EMBEDDER == "hash":
        print("EMBEDDER=hash: hash-embedder numbers are structural checks, not retrieval quality")
    async with aiohttp.ClientSession() as http:
        out = await coverage(data, http=http)
    print("\n" + "\n".join(format_table(out)))


if __name__ == "__main__":
    asyncio.run(main())
