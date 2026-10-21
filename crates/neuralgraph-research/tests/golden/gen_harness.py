#!/usr/bin/env python3
"""Fixtures for the benchmark harnesses that drive the shipped pipeline or re-score answer files:
``research/benchmarks/chat_memory_locomo.py``, ``chat_memory_longmemeval.py``, ``rescore.py`` and the metric helpers
of ``NeuralGraph/tests/test_single_hop_regression.py``. Writes ``harness.json``.

* Deterministic helpers on the real datasets: LoCoMo message building, evidence ids, eligibility, gold parts;
  LongMemEval date parsing and the stratified sample (``--n 50 --seed 1`` is the published Track D sample).
* End to end, both chat_memory harnesses run their real ``main()`` on the reference ``NeuralGraph.chat_memory``
  package with its scripted fake model (``testing.py`` of the last Python release, restored next to the frozen
  package), the clock and ids pinned and ``time.perf_counter`` frozen at 0. stdout and the ``--out`` files are
  recorded with the temporary directory replaced by ``<TMP>``.
* rescore: J3, the Gemma column, the auto-pass counts and the ``count`` dry run on the committed answer files,
  then a full run with a deterministic stand-in judge (sha1 of the prompt picks one of a few raw replies) whose
  output, cache and log are pinned by sha1, plus a second run that must reuse the cache.

Run from the repository root: ``PYTHONHASHSEED=0 python crates/neuralgraph-research/tests/golden/gen_harness.py``.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import _reference as R

# testing.py (the scripted fake model) left the frozen package with the Rust port; the library modules are
# unchanged since that release, so its last version drops back in.
PRODUCT_RELEASE = "f431110221c009f61fc5973e6e62a86ec467cfc3"
_testing = R.REF / "NeuralGraph" / "chat_memory" / "testing.py"
if not _testing.exists():
    _testing.write_bytes(subprocess.run(
        ["git", "-C", str(R.ROOT), "show", f"{PRODUCT_RELEASE}:NeuralGraph/chat_memory/testing.py"],
        check=True, capture_output=True).stdout)

_SCRATCH = Path(tempfile.mkdtemp(prefix="ng-harness-n5-"))
os.environ["N5_SCRATCH_DIR"] = str(_SCRATCH) + "//"   # rescore strips trailing slashes and adds one

import NeuralGraph.chat_memory.extraction as extraction_mod  # noqa: E402
import NeuralGraph.chat_memory.models as models_mod  # noqa: E402
import NeuralGraph.chat_memory.retrieval as retrieval_mod  # noqa: E402
import NeuralGraph.chat_memory.service as service_mod  # noqa: E402
import NeuralGraph.chat_memory.store as store_mod  # noqa: E402
import NeuralGraph.chat_memory.worker as worker_mod  # noqa: E402
from NeuralGraph.chat_memory import ChatMemory  # noqa: E402
from NeuralGraph.chat_memory.testing import scripted_fake_llm  # noqa: E402

import chat_memory_locomo as L  # noqa: E402
import chat_memory_longmemeval as LME  # noqa: E402
import rescore as N5  # noqa: E402

L.DATASET = R.DATASETS / "locomo10.json"
LME_FILE = R.DATASETS / "longmemeval" / "longmemeval_oracle.json"
RESULTS = R.ROOT / "research" / "results"
LOCOMO = json.loads(L.DATASET.read_text())
LME_DATA = json.loads(LME_FILE.read_text())


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def err(fn, *a):
    try:
        return {"ok": fn(*a)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------------------------- determinism hooks
_clock = {"now": R.FIXED_NOW}
for _m in (models_mod, store_mod, retrieval_mod, service_mod, worker_mod):
    _m.utcnow = lambda: _clock["now"]

_ids: dict[str, int] = {}


def _new_id(prefix: str) -> str:
    _ids[prefix] = _ids.get(prefix, 0) + 1
    return f"{prefix}_{_ids[prefix]:020d}"


for _m in (models_mod, store_mod, extraction_mod):
    _m.new_id = _new_id



def _vector_rank_stable(self, qvec, mask, k, min_sim=0.0):
    """``_Index.vector_rank`` with ties in index order. numpy's default argsort (introsort) leaves equal
    similarities in an arbitrary order, and the scripted fake model's bag-of-words embeddings tie constantly; the
    Rust product breaks ties by index (documented in its ``vector_rank``), so the reference is pinned to the same
    order here. Everything else is the original method."""
    import numpy as np
    self._ensure()
    if not qvec or len(qvec) not in self.mats:
        return []
    idxs, M = self.mats[len(qvec)]
    q = np.asarray(qvec, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    sims = M @ q
    allowed = mask[idxs] & (sims >= min_sim)
    sims = np.where(allowed, sims, -np.inf)
    top = np.argsort(-sims, kind="stable")[:k]
    out = []
    for i in top:
        if not np.isfinite(sims[i]):
            break
        out.append((self.ids[idxs[i]], float(sims[i])))
    return out


retrieval_mod._Index.vector_rank = _vector_rank_stable

_frozen_time = types.SimpleNamespace(perf_counter=lambda: 0.0)
L.time = _frozen_time
LME.time = _frozen_time


def _fake_chat_memory(db, config=None):
    return ChatMemory(db, llm=scripted_fake_llm(), config=config)


L.ChatMemory = _fake_chat_memory
LME.ChatMemory = _fake_chat_memory


def run_main(module, argv: list[str], tmp: Path) -> str:
    """``module.main()`` with ``argv``; stdout with the temp dir replaced by <TMP>."""
    _ids.clear()
    old = sys.argv
    sys.argv = [module.__name__ + ".py"] + argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            module.main()
    finally:
        sys.argv = old
    return buf.getvalue().replace(str(tmp), "<TMP>")


def read_tmp(path: Path, tmp: Path):
    return json.loads(path.read_text().replace(str(tmp), "<TMP>"))


# ---------------------------------------------------------------------------------------------- LoCoMo helpers
def locomo_helpers():
    convs = []
    for i, c in enumerate(LOCOMO):
        conv, qa = c["conversation"], c["qa"]
        entry = {"conv": i}
        for captions in (True, False):
            msgs = L.messages_for(conv, i, 99, captions=captions)
            entry["all_sessions" + ("" if captions else "_no_captions")] = {
                "n": len(msgs), "sha1": sha1(json.dumps(msgs)),
                "words": sum(len(x["text"].split()) for x in msgs)}
        entry["evidence_ids"] = [L.evidence_ids(q) for q in qa]
        # some evidence ids are not "D<n>:<m>" (e.g. "D:11:26"): eligible_qa raises for those conversations
        entry["eligible"] = {str(s): err(lambda s: [qa.index(q) for q in L.eligible_qa(qa, s)], s)
                             for s in (0, 1, 3, 6, 8, 99)}
        entry["gold_parts"] = [L.gold_parts(q["answer"]) if "answer" in q else None for q in qa]
        convs.append(entry)
    conv0 = LOCOMO[0]["conversation"]
    return {
        "convs": convs,
        "conv0_sessions2": L.messages_for(conv0, 0, 2),
        "conv7_sessions1_no_captions": L.messages_for(LOCOMO[7]["conversation"], 7, 1, captions=False),
        "negative_index": L.messages_for(LOCOMO[-1]["conversation"], -1, 1)[:3],
        "sess_of": {e: err(L.sess_of, e) for e in ["D1:3", "D12:1", "D:4", "", "D 3:1", "D+4:2", "Dx:1", "D٣:1"]},
        "evidence_edge": [{"q": q, "out": L.evidence_ids(q)} for q in [
            {"evidence": ["D1:3; D2:4", "D3:1,D4:2"]}, {"evidence": []}, {}, {"evidence": None},
            {"evidence": ["D1:3\x1cD2:2"]}, {"evidence": ["  "]}]],
        "gold_edge": [{"a": a, "out": L.gold_parts(a)} for a in [
            "Paris, London and Rome", "2022", 2022, 3.5, "  .  ", "a; b ;c.", "Sweden", "", None, True,
            "Ünïcödé, ÉCOLE and İstanbul"]],
    }


# ---------------------------------------------------------------------------------------------- LongMemEval helpers
def lme_helpers():
    dates = sorted({d for q in LME_DATA for d in q["haystack_dates"]})
    edge = ["2023/04/10 (Mon) 17:50", "2023/04/10 17:50", "2023/04/10(Mon)7:05", "x 2023/04/10 (Mon) 17:50 y",
            "2023/02/30 (Thu) 10:00", "2023/13/01 (Mon) 10:00", "2023/01/01 (Mon) 24:00", "2023/01/01 (Mon) 23:60",
            "0000/01/01 (Mon) 10:00", "2023/04/10 (Monday) 17:50", "", None, "2023-04-10 17:50", "٢٠٢٣/٠٤/١٠ ١٧:٥٠"]
    samples = []
    for n, seed in [(50, 1), (50, 2), (10, 0), (100, 7), (500, 3), (600, 1), (1, 1), (-5, 1), (50, -1),
                    (50, 2**40 + 3)]:
        samples.append({"n": n, "seed": seed, "ids": [q["question_id"] for q in LME.stratified_sample(LME_DATA, n, seed)]})
    small = LME_DATA[:40]
    samples_small = [{"n": n, "seed": s, "min_per_type": m,
                      "ids": [q["question_id"] for q in LME.stratified_sample(small, n, s, m)]}
                     for n, s, m in [(10, 1, 3), (10, 1, 0), (5, 4, 1), (20, 9, 5)]]
    return {
        "dates": {d: LME.parse_lme_date(d) for d in dates},
        "date_edge": [{"s": s, **err(LME.parse_lme_date, s)} for s in edge],
        "samples": samples,
        "samples_small": samples_small,
    }


def synthetic_rows():
    """summarize() over hand-made rows: every branch (abstention-only types, empty, int/float mixes)."""
    def row(qid, t, abs_, turn, sess, ceil, empty, mem, sec, msgs):
        return {"question_id": qid, "type": t, "abstention": abs_, "ctx_turn_rank": turn, "ctx_session_rank": sess,
                "ceiling_turn": ceil, "ctx_empty": empty, "memories": mem, "extract_seconds": sec, "messages": msgs}
    rows = [
        row("a", "multi-session", False, 0, 0, True, False, 12, 3.4, 20),
        row("b", "multi-session", False, None, 2, True, False, 7, 1.25, 11),
        row("c_abs", "multi-session", True, None, None, False, True, 3, 0.1, 4),
        row("d", "single-session-user", False, None, None, False, False, 0, 0.0, 2),
        row("e_abs", "temporal-reasoning", True, None, None, False, False, 5, 2.05, 9),
        row("f", "knowledge-update", False, 3, 3, True, True, 22, 10.35, 40),
        row("g", "knowledge-update", False, 1, None, False, False, 9, 7, 12),
    ]
    return [{"rows": rows[:k], "summary": LME.summarize(rows[:k])} for k in (0, 1, 3, 5, 7)] + [
        {"rows": rows[4:5], "summary": LME.summarize(rows[4:5])}]


# ---------------------------------------------------------------------------------------------- end to end
def locomo_e2e():
    tmp = Path(tempfile.mkdtemp(prefix="ng-harness-locomo-"))
    out = []
    for name, ingest_args in [
        ("default", []),
        ("variant", ["--batch-size", "2", "--no-captions", "--no-relations", "--min-importance", "0.5",
                     "--reconcile-cosine", "0.7", "--max-memories", "10", "--context-messages", "3"]),
        ("words", ["--batch-words", "60", "--no-gate", "--sess", "1"]),
    ]:
        db = tmp / f"{name}.db"
        ingest_out, eval_out = tmp / f"{name}_ingest.json", tmp / f"{name}_eval.json"
        sessions = ["--sessions", "2"] if "--sess" not in ingest_args else []
        ingest_stdout = run_main(L, ["ingest", "--db", str(db), *sessions, "--out", str(ingest_out), *ingest_args], tmp)
        eval_sessions = sessions or ["--sessions", "1"]
        eval_stdout = run_main(L, ["eval", "--db", str(db), *eval_sessions, "--out", str(eval_out)], tmp)
        out.append({"name": name, "ingest_args": ingest_args, "sessions": sessions,
                    "eval_sessions": eval_sessions,
                    "ingest_stdout": ingest_stdout, "ingest_out": read_tmp(ingest_out, tmp),
                    "eval_stdout": eval_stdout, "eval_out": read_tmp(eval_out, tmp)})
    return out


def lme_e2e():
    tmp = Path(tempfile.mkdtemp(prefix="ng-harness-lme-"))
    def size(q):
        return sum(len(s) for s in q["haystack_sessions"])
    picks = []
    for t in ("single-session-user", "multi-session", "knowledge-update"):
        picks.append(min((q for q in LME_DATA if q["question_type"] == t and not q["question_id"].endswith("_abs")),
                         key=lambda q: (size(q), q["question_id"])))
    picks.append(min((q for q in LME_DATA if q["question_id"].endswith("_abs")), key=lambda q: (size(q), q["question_id"])))
    file = tmp / "questions.json"
    file.write_text(json.dumps(picks))
    ids = [q["question_id"] for q in picks]
    runs = []
    for name, argv in [
        ("all", ["--n", "0"]),
        ("sampled", ["--n", "3", "--seed", "5", "--extract-assistant", "--no-extract-assistant", "--batch-size", "3",
                     "--user-name", "Alex", "--concurrency", "1"]),
        ("typed", ["--n", "0", "--types", " multi-session, knowledge-update", "--extract-assistant"]),
    ]:
        out = tmp / f"{name}.json"
        stdout = run_main(LME, ["--file", str(file), "--db-dir", str(tmp / name), "--out", str(out), *argv], tmp)
        runs.append({"name": name, "argv": argv, "stdout": stdout, "out": read_tmp(out, tmp)})
    # resume: everything done already, so only the header line and the summary are printed
    stdout = run_main(LME, ["--file", str(file), "--db-dir", str(tmp / "all"), "--out", str(tmp / "all.json"),
                            "--n", "0", "--resume"], tmp)
    runs.append({"name": "resume", "argv": ["--n", "0", "--resume"], "stdout": stdout, "out": None})
    return {"question_ids": ids, "runs": runs}


# ---------------------------------------------------------------------------------------------- rescore
JUDGE_REPLIES = ['{"label": "CORRECT"}', 'The answer matches. {"label": "WRONG"}', 'verdict: "label" : "correct"',
                 "no idea", '```json\n{"label": "Correct "}\n```', '{"label": true}', '["CORRECT"]',
                 '{"label": "WRONG", "why": "x"} then {"label": "CORRECT"}']


def fake_judge(prompt: str) -> str:
    return JUDGE_REPLIES[int(sha1(prompt), 16) % len(JUDGE_REPLIES)]


def rescore():
    files = [("flat_single_hop", str(RESULTS / "flat_single_hop.json")),
             ("local_pairs_single_hop", str(RESULTS / "local_pairs_single_hop.json"))]
    per_file = []
    for name, path in files:
        rows = json.load(open(path))["results"]
        n = len(rows)
        gemma = sum(N5.norm_bool(r.get("correct")) for r in rows)
        j3 = sum(N5.j3_substring(str(r["gold_answer"]), str(r["generated_answer"])) for r in rows)
        j1_auto = j2_exact = nogold = 0
        for r in rows:
            gl, ge = str(r["gold_answer"]).lower().strip(), str(r["generated_answer"]).lower().strip()
            j1_auto += bool(gl and (ge == gl or gl in ge))
            j2_exact += bool(gl and ge == gl)
            nogold += not gl
        per_file.append({"name": name, "n": n, "gemma": gemma, "j3": j3, "gemma_pct": round(100 * gemma / n, 1),
                         "j3_pct": round(100 * j3 / n, 1), "j1_autopass": j1_auto, "j2_exact": j2_exact,
                         "nogold": nogold,
                         "j3_flags": [N5.j3_substring(str(r["gold_answer"]), str(r["generated_answer"])) for r in rows]})

    # the `count` dry run is the module's __main__ block: run that block with FILES = the committed files
    tree = ast.parse(Path(N5.__file__).read_text())
    main_block = next(n for n in tree.body if isinstance(n, ast.If))
    N5.FILES = files
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["rescore.py", "count"]
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(ast.Module(body=main_block.body, type_ignores=[]), "rescore.py", "exec"), vars(N5))
    finally:
        sys.argv = old
    count_stdout = buf.getvalue()

    prompts: list[str] = []

    def judge(prompt):
        prompts.append(prompt)
        return fake_judge(prompt)

    N5.llm = judge
    N5.time = types.SimpleNamespace(time=lambda: 1000.0, strftime=lambda fmt: "12:34:56", sleep=lambda s: None)
    runs = []
    for _ in range(2):
        prompts.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            N5.main()
        runs.append({"stdout": buf.getvalue(), "calls": len(prompts),
                     "prompts_sha1": sha1("\x00".join(prompts)),
                     "first_prompts": prompts[:2],
                     "out_sha1": hashlib.sha1(Path(N5.OUT_PATH).read_bytes()).hexdigest(),
                     "cache_sha1": hashlib.sha1(Path(N5.CACHE_PATH).read_bytes()).hexdigest(),
                     "log": Path(N5.LOG_PATH).read_text()})
    out_doc = json.loads(Path(N5.OUT_PATH).read_text())
    labels = ['{"label": "CORRECT"}', ' {"label":"correct"} ', '{"label": "WRONG"}', '{"label": null}', '{"x": 1}',
              'Reasoning first. {"label": "CORRECT"}', '{"label": "CORRECT", "n": NaN}', '"label": "Wrong"',
              "[1, 2]", "", "CORRECT", '{"label": "CORRECT}', '{"label": " correct\\n"}', '{"label": 1}',
              '{"a": {"label": "CORRECT"}}', '{"label": "ＣＯＲＲＥＣＴ"}']
    return {
        "judge_replies": JUDGE_REPLIES,
        "scratch_env": os.environ["N5_SCRATCH_DIR"], "ov_suffix": N5.OV[len(str(_SCRATCH)):],
        "files": per_file,
        "count_stdout": count_stdout,
        "runs": runs,
        "rescored_first_rows": {k: v[:3] for k, v in out_doc.items()},
        "keys": [{"args": a, "key": N5.key(*a)} for a in [("J1", "q", "g", "a"), ("J2", "Wer?", "Zürich", "„ja“"),
                                                         ("J1", "", "", "")]],
        "norm_bool": [{"v": v, "out": N5.norm_bool(v)} for v in [True, False, None, "True", " true ", "TRUE", "yes",
                                                                  1, 0, "1", 1.0]],
        "j3": [{"gold": g, "gen": a, "out": N5.j3_substring(g, a)} for g, a in [
            ("Oliver, Luna, Bailey", "Luna, Oliver"), ("2", "week of 14 August 2023"), ("", "anything"),
            ("ab", "xaby"), ("Sweden", "she moved from sweden"), ("a and b and cat", "the cat"),
            ("  ", "x"), ("Paris and London", "london, then paris"), ("İstanbul", "i̇stanbul")]],
        "parse_judge_label": [{"resp": r, "out": N5.parse_judge_label(r)} for r in labels + JUDGE_REPLIES],
        "prompt_templates": {"accuracy": N5.ACCURACY_PROMPT, "strict": N5.STRICT_PROMPT},
        "request_body": json.dumps({"model": N5.MODEL, "messages": [{"role": "user", "content": "Zürich {x}"}],
                                    "temperature": 0, "max_tokens": 150, "stream": False, "reasoning_effort": "none"}),
    }


# ---------------------------------------------------------------------------------------------- single-hop regression
def single_hop():
    spec = importlib.util.spec_from_file_location(
        "test_single_hop_regression", R.REF / "NeuralGraph" / "tests" / "test_single_hop_regression.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    qs = S.SINGLE_HOP_TEST_QUESTIONS
    results = [("The teacher Caroline", 0.9), ("New York, NY", 0.8), ("nothing", 0.5), ("Toyota corolla", 0.4),
               ("it is blue", 0.3), ("COMPUTER science", 0.2)]
    checks = []
    for exp in ["teacher", "New York", "blue", "computer science", "Spanish", "ab, Toyota", "  ", "x,yz,abc"]:
        for k in (0, 1, 2, 5, 10):
            checks.append({"expected": exp, "k": k, "out": S.check_answer_in_results(
                exp, [(types.SimpleNamespace(content=c), s) for c, s in results], k)})
    no_content = S.check_answer_in_results("42", [(42, 1.0)], 1)

    answers = {q.question: i for i, q in enumerate(qs)}

    async def retrieval(question):
        i = answers[question]
        if i % 7 == 6:
            raise RuntimeError(f"backend down for {i}")
        hit = (types.SimpleNamespace(content=f"answer: {qs[i].expected_answer}"), 1.0)
        filler = [(types.SimpleNamespace(content=f"filler {j}"), 0.5) for j in range(12)]
        pos = [0, 1, 4, 7, 11, 20][i % 6]
        return (filler[:pos] + [hit] + filler[pos:])[:12]

    res = S.run_regression_suite(qs, retrieval)
    d = res.to_dict()
    for k in ("avg_latency_ms", "p95_latency_ms"):
        d.pop(k)
    lat = S.RegressionResult(total_questions=5, latencies_ms=[10.0, 20.0, 30.0, 40.0, 100.0])
    lat2 = S.RegressionResult(total_questions=3, correct_at_1=2, latencies_ms=[0.1, 0.2, 0.7, 5.5, 3.25, 1e-3, 2.0])
    tmp = Path(tempfile.mkdtemp(prefix="ng-harness-shr-"))
    S.save_regression_results(lat2, tmp / "r.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(ast.Module(body=next(n for n in ast.parse(Path(spec.origin).read_text()).body
                                          if isinstance(n, ast.If)).body, type_ignores=[]), "shr", "exec"), vars(S))
    return {
        "questions": [vars(q) for q in qs],
        "checks": checks, "no_content": no_content,
        "suite": {**d, "failed_questions": res.failed_questions},
        "latency": lat.to_dict(), "latency2": lat2.to_dict(),
        "saved": (tmp / "r.json").read_text(),
        "compare": [{"p1": [c, t], "out": list(S.compare_to_baseline(S.RegressionResult(total_questions=t, correct_at_1=c)))}
                    for c, t in [(7, 10), (64, 100), (65, 100), (2, 3), (0, 0), (1, 1)]],
        "standalone": buf.getvalue(),
        "baseline": S.BASELINE_PRECISION_1,
    }


def main():
    out = {
        "locomo": locomo_helpers(),
        "lme": lme_helpers(),
        "lme_summaries": synthetic_rows(),
        "locomo_e2e": locomo_e2e(),
        "lme_e2e": lme_e2e(),
        "rescore": rescore(),
        "single_hop": single_hop(),
    }
    R.dump("harness.json", out)


if __name__ == "__main__":
    main()
