# Offline retrieval evaluation and the 95% question

Written 2026-09-18. Context: a request to "bring accuracy up to 95% in one hour", worked in an
environment with no model server (no Ollama / LM Studio), no GPU, and no model downloads. This note
records what was possible there, what was measured, and what the 95% target would actually require.

## 1. What "accuracy" means in this repo

The headline number is LoCoMo end-to-end accuracy: **72.2% (744 questions, conversations 1–5, Gemma
lenient judge)**, 74.1% on the 590-question `capstone_full` file under the GPT-4o lenient judge
(`docs/BENCHMARKS.md`). Every stage that produces that number calls an LLM: reranking, answering and
judging. Nothing in the answer path can run, let alone improve, without a model server. The same holds
for the call-center benchmark and the chat-memory QA path.

## 2. What the recorded results say about the remaining errors

Offline analysis of `demo/results/capstone_full.json` (590 questions, 153 wrong):

| category | n | accuracy | wrong: abstained ("Not found") | wrong: wrong content |
|---|---:|---:|---:|---:|
| single_hop | 115 | 76.5% | 13 | 14 |
| multi_hop | 311 | 72.3% | 39 | 47 |
| temporal | 130 | 78.5% | 1 | 27 |
| open_domain | 34 | 64.7% | 5 | 7 |

The gold answer is a substring of the 15–30 memories handed to the answer model for only 31–49% of
single/multi-hop questions (the substring check is crude: "two" vs "2", paraphrased gold), but a
clear pattern is visible in the wrong answers on `local_pairs_single_hop.json`: counting questions
("How many turtles does Nate have?" → "Not found" with the turtle messages in context), set questions
("What states has Maria vacationed at?" → lists four, gold has two) and two-speaker intersection
questions ("Which city have both Jean and John visited?" → "Not found"). These are answer-synthesis
failures with the evidence present, and they need a stronger answer model or an aggregation step, not
retrieval work.

## 3. Why 95% is not a one-hour target

- Published LoCoMo results for full memory systems cluster in the 60–80% band under lenient LLM
  judges; the current shipped system is inside that band. 95% would be well beyond any reported result
  on this benchmark, with a 4B-class local model doing the answering.
- The campaign already tried 18 interventions (`docs/BENCHMARKS.md`, "All 18 experiments"). The
  largest single gain was +8.9 points (per-agent routing); most others were null or negative.
- The judge alone moves the same answers by up to 51 points (N5). A 95% figure obtained by loosening
  the judge would be a grading artifact, not an accuracy gain, and this note does not do that.
- Each end-to-end single-hop run is ~40 minutes on a laptop GPU; the full benchmark is ~3.5 hours.
  One hour buys at most one experiment, and none was possible here.

## 4. What was done instead

1. **pytest runs repo-wide.** The stale root `__init__.py` (a MemMachine scaffold importing a package
   that is not installed) made pytest fail at collection, so the single-hop regression suite could not
   run in CI. It is removed; `python -m pytest NeuralGraph/tests` gives 222 passed, 1 skipped.
2. **An LLM-free retrieval evaluator.** `demo/retrieval_eval.py` gained `EMB_BACKEND=local`: a
   deterministic hashed TF-IDF embedding (word unigrams + character 3–5-grams, IDF fitted per
   conversation) so the real `Tesseract` retrieval stack can be scored with no server and no network.
   It also reports evidence recall against LoCoMo's gold `evidence` dialogue ids (`ev@10`: every gold
   message in the top 10; `ev_any@10`: at least one), which is stricter and less noisy than the
   substring check.

```bash
cd demo && EMB_BACKEND=local ONLY_CAT=single_hop \
  VARIANTS=embed,tesseract,tesseract+speaker+embed_union python3 retrieval_eval.py
```

## 5. Offline baseline (local TF-IDF embeddings, single_hop as labelled in the harness, n=282)

Run 2026-09-18, 4-CPU container, 18 minutes wall time, no network.

| variant | recall@10 | recall@50 | recall@all | ev@10 | ev_any@10 |
|---|---:|---:|---:|---:|---:|
| embed (flat cosine) | 30.9% | 52.8% | 52.8% | 9.2% | 46.8% |
| tesseract | 35.8% | 55.7% | 55.7% | 10.6% | 53.2% |
| tesseract + speaker boost + embed back-fill | 39.0% | 55.7% | 57.4% | 13.1% | 58.5% |

For comparison, the same three variants with nomic-embed (`docs/BENCHMARKS.md`) score 39.4 / 44.7 /
46.8 recall@10. The local embedding costs about 8 points of recall@10 but preserves the ranking.

These numbers are a **retrieval-mechanics baseline with weak embeddings**, not comparable with the
nomic-embed numbers in `docs/BENCHMARKS.md`. Their use is relative: a change to speaker routing,
temporal scoring, keyword matching or pair back-fill should move `ev@10` / `ev_any@10` here before it
is paid for with a 40-minute end-to-end run. The ordering matches the campaign's finding (flat
embedding < Tesseract < Tesseract + speaker boost + embed back-fill).

## 6. The shortest credible path upward

In order of expected return, all requiring a model server:

1. Answer-synthesis failures with evidence present (section 2): route counting / set / intersection
   questions to an aggregation prompt that first lists candidate facts, then answers. The capstone
   has 58 abstentions and 95 wrong-content answers; most of the abstentions are of this kind.
2. Temporal wrong-content (27 of 28 temporal errors): compare the resolved date carried in node
   metadata with what the answer model wrote; when the memory has an explicit `resolved_date`, answer
   from metadata rather than from the model's arithmetic.
3. Larger answer model only where strict judges are used (H8: −7 lenient, +4 strict).

None of these has been run; each is one end-to-end experiment under the harness in `demo/runner.py`.
