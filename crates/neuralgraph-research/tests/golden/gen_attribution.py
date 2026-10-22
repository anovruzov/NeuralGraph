#!/usr/bin/env python3
"""Fixtures for ``retrieval/attribution.py``: per-query attribution records (stages with breakdowns and
metadata, router, evidence detection, rerank deltas, gold-answer ranking, summaries and ``to_json``) driven by
LoCoMo questions and turns, and the aggregating ``AttributionLogger`` (precision@k, latency percentiles on both
sides of the 20/100-sample thresholds, strategy effectiveness, both rerank-gain shapes, the JSONL file it
appends in batches, the disabled logger, the global logger). ``time.perf_counter`` is replaced by a counter
(start 1000.0, step 0.0125) before the module is imported, because the dataclass bound it as a default factory
at import time; the Rust test pins ``attribution::pin_perf_counter`` the same way. Writes ``attribution.json``."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import _reference as R


class _Counter:
    def __init__(self, start, step):
        self.n, self.step = start, step

    def __call__(self):
        v = self.n
        self.n += self.step
        return v


time.perf_counter = _Counter(1000.0, 0.0125)

import NeuralGraph.research.retrieval.attribution as A  # noqa: E402
from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer  # noqa: E402

R.freeze_now([A])

DATA = json.loads((R.DATASETS / "locomo10.json").read_text(encoding="utf-8"))


def turns(conv_index, session):
    return DATA[conv_index]["conversation"][f"session_{session}"]


def make_nodes(conv_index, session, count):
    """Node specs: id, content and metadata (speaker under "speaker", "producer_id", both, a non-string, none)."""
    specs = []
    for i, t in enumerate(turns(conv_index, session)[:count]):
        kind = i % 5
        meta = {"dia_id": t["dia_id"]}
        if kind == 0:
            meta["speaker"] = t["speaker"]
        elif kind == 1:
            meta["producer_id"] = t["speaker"]
        elif kind == 2:
            meta = {"producer_id": t["speaker"].upper(), "speaker": t["speaker"]}
        elif kind == 3:
            meta["producer_id"] = 7
        else:
            meta = {}
        specs.append({"node_id": f"locomo-{conv_index}-{session}-{i:03d}-{t['dia_id']}", "content": t["text"],
                      "metadata": meta})
    return specs


def nodes_of(specs):
    return [NeuralNode(node_id=s["node_id"], layer=NodeLayer.MESSAGE, content=s["content"],
                       metadata=dict(s["metadata"])) for s in specs]


def breakdowns(specs, n):
    out = []
    for i in range(n):
        s = specs[i % len(specs)]
        out.append({"node_id": s["node_id"], "content_preview": s["content"][:100], "speaker": "Caroline",
                    "rank": i + 1, "base_score": 0.9 / (i + 1), "semantic_score": 0.123456789 * (i % 7),
                    "entity_score": 0.00005 * i, "keyword_score": 1 / 3, "temporal_score": 0.33335,
                    "speaker_boost": 0.35 if i % 2 else 0.0, "specificity_bonus": 0.012345, "rerank_delta": -0.04567,
                    "final_score": 2.5 - 0.01 * i})
    return out


def bd_objs(specs):
    return [A.ScoreBreakdown(**b) for b in specs]


def scenario(conv_index, session, qa_slice):
    """One attribution per LoCoMo question of the slice, all methods driven."""
    specs = make_nodes(conv_index, session, 24)
    nodes = nodes_of(specs)
    texts = [s["content"] for s in specs]
    records = []
    for qi, qa in enumerate(DATA[conv_index]["qa"][qa_slice]):
        question, answer = qa["question"], str(qa.get("answer", qa.get("adversarial_answer", "")))
        attr = A.create_attribution(f"conv-{conv_index}", question * (1 + qi % 2))
        bds = breakdowns(specs, 60 if qi % 3 == 0 else 4)
        attr.log_stage("vector_search", candidates_in=0, candidates_out=50, top_score=0.81 + qi / 100,
                       latency_ms=12.5 + qi)
        attr.log_stage("entity_filter", 50, 45, 0.79, 2.25, breakdowns=bd_objs(bds), category=qa["category"],
                       evidence=qa["evidence"], note="ok")
        if qi % 2 == 0:
            attr.log_stage("rerank", 45, 10, 0.92 - qi / 50, 45.0, breakdowns=[])
        if qi % 4 == 1:
            attr.log_stage("vector_search", 10, 10, 0.5, 1.0)
        entities = [w for w in question.replace("?", "").split() if w[:1].isupper()][:3]
        attr.log_router(["semantic", "entity_filter", "temporal_filter"][qi % 3], 0.25 * (qi % 4), entities,
                        ["when"] if question.lower().startswith("when") else [], ["temporal", "single_hop",
                                                                                   "multi_hop"][qi % 3])
        order = list(range(len(nodes)))
        before = [(nodes[i], 1.0 - 0.03 * i) for i in order]
        rot = order[qi % 5:] + order[:qi % 5]
        after = [(nodes[i], 1.0 - 0.03 * k + (0.005 if k % 3 == 0 else 0.02)) for k, i in enumerate(rot)]
        attr.log_rerank(before, after)
        # the answer quotes one turn (a used snippet) and mentions the gold answer
        quoted = texts[(qi * 3) % len(texts)]
        generated = f"Based on the conversation: {quoted[:60]} ... so the answer is {answer}."
        attr.log_evidence(after[:22], generated if qi % 3 else "")
        top_k = [10, 3, 25][qi % 3]
        retrieved = texts[qi % 4:] + [f"The answer is {answer}."]
        attr.log_gold_position(answer if qi % 5 else answer.upper(), retrieved, top_k=top_k)
        attr.generated_answer = generated * 4
        attr.gold_answer = answer
        attr.gate_profile = ["default", "single_hop"][qi % 2]
        attr.is_list_question = "," in answer
        before_final = attr.get_summary()
        attr.finalize(final_candidates=len(after) - qi, answer_correct=qi % 3 != 1)
        records.append({"question": question, "answer": answer, "category": qa["category"],
                        "evidence": qa["evidence"], "breakdowns": bds, "entities": entities,
                        "summary_before_finalize": before_final if qi == 0 else None, "summary": attr.get_summary(),
                        "json": attr.to_json() if qi < 2 else None, "latency": attr.total_latency_ms,
                        "stage_latencies": attr.stage_latencies})
    return {"specs": specs, "records": records}, nodes


def gold_cases():
    out = []
    texts = ["Caroline went to the LGBTQ support group on 7 May 2023.", "Melanie painted a sunrise in 2022.",
             "Psychology and counseling certification", "Nothing relevant here", "an adoption agency visit"]
    for gold in ["7 May 2023", "2022", "Psychology, counseling certification", "Adoption agencies",
                 "a, b and c", "  ", "", "Sunrise and LGBTQ", "nothing relevant", "ab, cd and ef"]:
        for top_k in [0, 1, 3, 10]:
            attr = A.RetrievalAttribution(session_key="s", query_text="q", start_time=5.0)
            attr.log_gold_position(gold, texts, top_k=top_k)
            out.append({"gold": gold, "top_k": top_k, "rank": attr.gold_rank,
                        "flags": [attr.gold_in_top_1, attr.gold_in_top_5, attr.gold_in_top_10]})
    return {"texts": texts, "cases": out}


def logger_runs(tmp: Path):
    out = {}
    path = tmp / "nested" / "dir" / "attr.jsonl"
    logger = A.AttributionLogger(log_path=str(path), buffer_size=3)
    out["empty_summary"] = logger.get_summary()
    flushed_sizes = []
    for i in range(8):
        attr = A.create_attribution(f"sess-{i % 2}", f"Question number {i} about Caroline")
        attr.query_type = ["temporal", "single_hop", "multi_hop"][i % 3]
        attr.router_strategy = ["semantic", "entity_filter"][i % 2]
        for s in range(25 if i < 4 else 2):
            attr.log_stage(["vector_search", "rerank", "flash_retrieve"][s % 3], 10, 5,
                           top_score=0.0 if (i == 5 and s % 3 == 0) else 0.5 + 0.01 * s + 0.03 * i,
                           latency_ms=float((s * 37 + i * 11) % 23) + 0.5)
        attr.gold_rank = i
        attr.gold_in_top_1, attr.gold_in_top_5, attr.gold_in_top_10 = i == 1, 0 < i <= 5, 0 < i <= 10
        attr.answer_correct = i % 3 == 0
        if i != 6:
            attr.finalize(final_candidates=i, answer_correct=attr.answer_correct)
        logger.add(attr)
        flushed_sizes.append(len(logger._buffer))
    out["buffer_sizes"] = flushed_sizes
    out["file_after_adds"] = path.read_text(encoding="utf-8").splitlines()
    logger.close()
    out["file_after_close"] = path.read_text(encoding="utf-8").splitlines()
    logger.close()
    out["precision"] = [logger.get_precision_at_k(k) for k in [1, 5, 10, 3]]
    out["latency_stats"] = logger.get_latency_stats()
    out["strategy"] = logger.get_strategy_effectiveness()
    out["rerank_gain"] = logger.get_rerank_gain()
    out["summary"] = logger.get_summary()

    # 120 stage samples for the p99 branch
    big = A.AttributionLogger()
    for i in range(6):
        attr = A.create_attribution("big", "q")
        for s in range(20):
            attr.log_stage("vector_search", latency_ms=float((s * 53 + i * 7) % 101) / 4)
        big.add(attr)
    out["big_latency"] = big.get_latency_stats()
    out["big_rerank_gain"] = big.get_rerank_gain()
    big._append_to_log(A.create_attribution("x", "y"))

    disabled = A.AttributionLogger(log_path=str(tmp / "disabled.jsonl"), enabled=False)
    disabled.add(A.create_attribution("x", "y"))
    out["disabled"] = [len(disabled.attributions), disabled.get_summary(), (tmp / "disabled.jsonl").exists()]

    single = A.AttributionLogger(log_path=str(tmp / "single.jsonl"), buffer_size=100)
    a = A.create_attribution("one", "Wörld “quotes” ü")
    a.finalize(1, True)
    single._append_to_log(a)
    single.add(a)
    out["single_before_flush"] = (tmp / "single.jsonl").read_text(encoding="utf-8").splitlines()
    single.flush()
    out["single_after_flush"] = (tmp / "single.jsonl").read_text(encoding="utf-8").splitlines()

    g1 = A.get_attribution_logger()
    g1.add(A.create_attribution("g", "q"))
    g2 = A.get_attribution_logger()
    same = g1 is g2
    A.reset_attribution_logger()
    g3 = A.get_attribution_logger()
    out["global"] = [same, g3 is g1, len(g2.attributions), len(g3.attributions), g3.log_path is None]
    A.reset_attribution_logger()
    return out


def main():
    scenarios = []
    for conv_index, session, sl in [(0, 1, slice(0, 8)), (1, 2, slice(10, 16)), (3, 1, slice(40, 44))]:
        sc, _ = scenario(conv_index, session, sl)
        sc["conv_index"], sc["session"], sc["qa"] = conv_index, session, [sl.start, sl.stop]
        scenarios.append(sc)
    out = {"scenarios": scenarios, "gold": gold_cases()}
    with tempfile.TemporaryDirectory() as tmp:
        out["logger"] = logger_runs(Path(tmp))
    out["perf_counter_next"] = time.perf_counter()
    R.dump("attribution.json", out)


main()
