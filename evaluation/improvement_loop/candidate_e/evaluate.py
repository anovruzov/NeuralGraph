"""Retrieval-only evaluation against gold evidence turn ids. No paid calls.

The recorded pipeline's evidence is judged by a substring heuristic because its
excerpts carry no turn ids. The raw corpus does carry them (`dia_id`), and every
frozen question joins to its raw entry on (conversation, question). So Candidate
E can be scored against *actual* gold evidence rather than a lexical proxy --
Recall@k and MRR mean what they normally mean here.

The recorded baseline is scored the same way by mapping its excerpt texts back
to turn ids, so both sides are measured on one definition.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evaluation.improvement_loop.candidate_e.index import build_index, load_turns
from evaluation.improvement_loop.candidate_e.retrieve import UnionRetriever
from evaluation.improvement_loop.corpus import load_corpus
from evaluation.improvement_loop.rounds import load_eval_sets

RAW = Path("evaluation/locomo/locomo10.json")
LOOP = Path("evaluation/artifacts/locomo_loop")
OUT = Path("evaluation/artifacts/overnight_80/candidate_e")


# The recorded pipeline injects resolved dates into excerpt text, e.g.
# "yesterday [= 7 May 2023]". Stripping the annotation is required for a fair
# comparison: without it 24% of recorded excerpts fail to map back to a turn and
# the baseline's recall is understated against a Candidate E that knows turn ids
# exactly.
_ANNOTATION = re.compile(r"\[=[^\]]*\]")


def _norm(text: str) -> str:
    text = _ANNOTATION.sub(" ", text or "")
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def gold_map() -> dict[int, dict[str, Any]]:
    """question id -> {conversation, gold_uids, question, gold_answer}."""
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for ci, sample in enumerate(raw, start=1):
        for qa in sample.get("qa", ()):
            key = (str(ci), str(qa.get("question", "")).strip())
            lookup[key] = qa

    out: dict[int, dict[str, Any]] = {}
    for record in load_corpus():
        conv = str(record.get("conversation"))
        qa = lookup.get((conv, str(record.get("question", "")).strip()))
        if qa is None:
            continue
        evidence = qa.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [evidence]
        out[int(record["id"])] = {
            "conversation": conv,
            "question": str(record.get("question", "")),
            "gold_answer": record.get("gold_answer"),
            "category": str(record.get("category")),
            "gold_uids": [f"{conv}:{e}" for e in evidence if e],
        }
    return out


def baseline_uids(record: dict[str, Any], text_to_uid: dict[str, str],
                  prefix_index: list[tuple[str, str]] | None = None) -> list[str]:
    """Map the recorded excerpts back to turn ids, preserving their order.

    Exact normalised match first; then a prefix match, because excerpts are
    truncated as well as annotated. Unmapped excerpts are dropped rather than
    guessed -- a wrong mapping would credit the baseline with evidence it never
    retrieved.
    """
    uids: list[str] = []
    for memory in (record.get("retrieved_memories") or ()):
        if not isinstance(memory, dict):
            continue
        key = _norm(memory.get("text", ""))
        if not key:
            continue
        uid = text_to_uid.get(key)
        if uid is None and prefix_index is not None and len(key) > 24:
            head = key[:64]
            for turn_norm, turn_uid in prefix_index:
                if turn_norm.startswith(head) or key.startswith(turn_norm[:64]):
                    uid = turn_uid
                    break
        if uid and uid not in uids:
            uids.append(uid)
    return uids


def recall_at(ranked: list[str], gold: list[str], k: int) -> float:
    if not gold:
        return 0.0
    return len(set(ranked[:k]) & set(gold)) / len(gold)


def complete_at(ranked: list[str], gold: list[str], k: int) -> bool:
    """Every gold turn present in the top k -- the measure that matters for
    multi-evidence questions, where partial recall still fails the question."""
    return bool(gold) and set(gold) <= set(ranked[:k])


def mrr(ranked: list[str], gold: list[str]) -> float:
    for i, uid in enumerate(ranked, start=1):
        if uid in gold:
            return 1.0 / i
    return 0.0


def evaluate(question_ids: list[int], top_k: int = 20,
             embedder: Any | None = None) -> dict[str, Any]:
    index = build_index(load_turns())
    retriever = UnionRetriever(index, embedder=embedder)
    text_to_uid = {_norm(t.indexed_text): t.uid for t in index.turns}
    text_to_uid.update({_norm(t.text): t.uid for t in index.turns})
    prefix_index = [(_norm(t.text), t.uid) for t in index.turns]

    by_id = {int(r["id"]): r for r in load_corpus()}
    gold = gold_map()

    rows: list[dict[str, Any]] = []
    for qid in question_ids:
        g = gold[qid]
        cands = retriever.retrieve(g["question"], g["conversation"], top_k=max(top_k, 50))
        ranked = [c.uid for c in cands]
        base = baseline_uids(by_id[qid], text_to_uid, prefix_index)
        rows.append({
            "id": qid,
            "category": g["category"],
            "conversation": g["conversation"],
            "question": g["question"],
            "gold_uids": g["gold_uids"],
            "n_gold": len(g["gold_uids"]),
            "candidate_e": {
                "ranked": ranked[:top_k],
                "recall@1": recall_at(ranked, g["gold_uids"], 1),
                "recall@5": recall_at(ranked, g["gold_uids"], 5),
                "recall@10": recall_at(ranked, g["gold_uids"], 10),
                "recall@20": recall_at(ranked, g["gold_uids"], 20),
                "recall@50": recall_at(ranked, g["gold_uids"], 50),
                "complete@20": complete_at(ranked, g["gold_uids"], 20),
                "mrr": round(mrr(ranked, g["gold_uids"]), 4),
                "sources": {c.uid: c.sources for c in cands[:top_k]},
                "stage_scores": {c.uid: c.stage_scores for c in cands[:top_k]},
                "context_turns": len(ranked[:top_k]),
            },
            "baseline": {
                "n_retrieved": len(base),
                "recall@20": recall_at(base, g["gold_uids"], 20),
                "recall@50": recall_at(base, g["gold_uids"], 50),
                "complete@20": complete_at(base, g["gold_uids"], 20),
                "mrr": round(mrr(base, g["gold_uids"]), 4),
                "mapped_fraction": round(
                    len(base) / max(len(by_id[qid].get("retrieved_memories") or []), 1), 3),
            },
        })
    return {"top_k": top_k, "index_digest": index.digest, "rows": rows}


def summarise(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["rows"]
    n = len(rows)
    def mean(path, key):
        return round(sum(r[path][key] for r in rows) / n, 4) if n else 0.0
    e_hit = [r for r in rows if r["candidate_e"]["recall@20"] > 0]
    b_hit = [r for r in rows if r["baseline"]["recall@20"] > 0]
    recovered = [r["id"] for r in rows
                 if r["candidate_e"]["recall@20"] > 0 and r["baseline"]["recall@20"] == 0]
    lost = [r["id"] for r in rows
            if r["candidate_e"]["recall@20"] == 0 and r["baseline"]["recall@20"] > 0]
    return {
        "n": n,
        "candidate_e": {
            f"recall@{k}": mean("candidate_e", f"recall@{k}") for k in (1, 5, 10, 20, 50)
        } | {
            "mrr": mean("candidate_e", "mrr"),
            "any_gold@20": round(len(e_hit) / n, 4) if n else 0.0,
            "complete@20": round(
                sum(1 for r in rows if r["candidate_e"]["complete@20"]) / n, 4) if n else 0.0,
        },
        "baseline": {
            "recall@20": mean("baseline", "recall@20"),
            "recall@50": mean("baseline", "recall@50"),
            "mrr": mean("baseline", "mrr"),
            "any_gold@20": round(len(b_hit) / n, 4) if n else 0.0,
            "complete@20": round(
                sum(1 for r in rows if r["baseline"]["complete@20"]) / n, 4) if n else 0.0,
        },
        "recovered_ids": recovered,
        "lost_ids": lost,
        "net_recovered": len(recovered) - len(lost),
    }
