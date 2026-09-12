# Retrieval accuracy: what is actually broken

The long-standing note was "single-hop is ~50% despite 77% recall@50", with no
committed artifact behind it and no way to run the QA benchmarks without a live
Ollama. This is that number, diagnosed — entirely offline, from what the
benchmark run already wrote down.

```bash
python3 -m evaluation.diagnose_accuracy demo/maximal.json
```

Source artifact: `demo/maximal.json` — 1540 questions, GPT-4o judge, recorded
2025-12-20. It stores the retrieved memories per question alongside the gold
answer and the verdict, which is what makes an offline diagnosis possible.

---

## The headline

**Single-hop failure is a generation problem, not a retrieval problem.**
About **76%** of single-hop failures had their supporting evidence in the
retrieved context. The memory system found the fact; the answerer did not use
it.

This corroborates the "77% recall@50" note from a completely independent
direction — that figure was measured during retrieval, this one is recovered
after the fact from recorded outputs, and they agree closely.

## Accuracy by category

| category | n | accuracy |
|---|---|---|
| `multi_hop` | 841 | 74.1% |
| `temporal` | 321 | 64.5% |
| **`single_hop`** | 282 | **52.1%** |
| `open_domain` | 96 | 52.1% |
| **total** | 1540 | 66.7% |

**Single-hop is the weakest conversational category — worse than multi-hop.**
That is backwards from expectation and is the strongest hint that the problem
is not memory depth.

## Where failures come from

Bias-corrected (see *Method* below), `partial` measure:

| category | failures | evidence was present | **generation's fault** | retrieval's fault |
|---|---|---|---|---|
| `single_hop` | 135 | 102.8 | **76.1%** | 23.9% |
| `temporal` | 114 | 89.6 | 78.6% | 21.4% |
| `multi_hop` | 218 | 149.2 | 68.5% | 31.5% |
| total | 513 | 357.3 | **69.6%** | 30.4% |

Roughly **70% of all wrong answers had the evidence retrieved.** Effort spent on
retrieval recall is going after the smaller half of the problem.

---

## The shape of the generation failure

Not "wrong facts" — **wrong sets**.

| | single_hop | multi_hop | temporal |
|---|---|---|---|
| gold answer is a list (≥2 items) | **66%** | 32% | 42% |
| accuracy on list questions | 50.0% | 74.4% | 74.6% |
| accuracy on single-item questions | 56.2% | 73.9% | 57.2% |
| generation failures | 86 | 143 | 87 |
| **omit ≥1 gold item** | **85 / 86** | 141 / 143 | 83 / 87 |
| complete but over-inclusive | **0** | 2 | 4 |
| no overlap with gold at all | 38 | 103 | 44 |

Two distinct sub-modes, needing different fixes:

**Incomplete coverage — 47 of 86 (55%).** The answer is partially right and
drops items. Gold `"Nothing is Impossible", "Charlotte's Web"` → produced
`"Charlotte's Web"`. Gold `Her mentors, family, and friends` → produced
`Friends and family`. The judge marks these wrong, correctly.

**Wrong selection — 38 of 86 (44%).** The answer shares *no* content token with
gold, despite at least half the gold tokens being present in the retrieved
context. Gold `dinosaurs, nature` → produced `camping, going to the beach,
playing in the park`. The answerer had the material and wrote about something
else.

A third of failures also hallucinate plausible items: gold `Horse, sunset,
sunrise` → produced `horses, landscapes, still life` — one right, two invented.

**Single-item single-hop questions are still only 56% accurate**, so list
handling explains much of the gap but not all of it. Do not treat list
extraction as the whole fix.

---

## What follows

~70% of failures are generation-side, so that is where the return is. The
ranked plan with sizes is in **Path to 85%** below; in short, corpus-wide the
buckets are selection (147), completeness (120) and abstention (60).

One caution about scope. Within single-hop, list handling dominates — 66% of
its questions are list-valued and 85 of 86 failures drop an item. Corpus-wide
it does not: list-valued and single-item questions differ by only 1.5pp
(65.8% vs 67.3%). Single-hop is 18% of the corpus, so a list-completeness fix
is necessary and nowhere near sufficient. An earlier draft of this document
called it "the single largest identified bucket", which was true only within
single-hop and is corrected above.

None of this is implemented. This document is the diagnosis; the fixes belong
to whoever owns the retrieval track.

---

## Path to 85%

Current: **66.7%** (1027/1540). Target 85% = 1309 correct, so **+282 answers**.

The ceiling with perfect generation is **90.7%**, so 85% is reachable — but only
just, and not by generation fixes alone.

### Every failure, bucketed

| bucket | n | pp of corpus | what it is |
|---|---|---|---|
| selection error | 147 | 9.5pp | had the evidence, answered something else |
| incomplete list | 120 | 7.8pp | named some gold items, omitted others |
| abstention | 60 | 3.9pp | said "not mentioned" when it *was* mentioned |
| retrieval miss | 139 | 9.0pp | answer is in the conversation, retrieval missed it |
| **unanswerable** | **47** | **3.1pp** | answer is nowhere in the conversation |
| **total wrong** | **513** | **33.3pp** | |

The last row is a hard ceiling. Verified against the full source conversations
in `evaluation/locomo/locomo10.json`:

```bash
python3 -m evaluation.retrieval_ceiling
```

47 gold answers appear nowhere in the conversation they belong to — 18 of them
`open_domain`, which is expected, since those questions are not about the
conversation. **No retrieval or generation fix can ever recover them.** Maximum
attainable accuracy on this benchmark is therefore **96.9%**, not 100%.

This corrects an earlier version of this document, which treated all 186
no-evidence failures as recoverable. Only 139 are.

### The three generation fixes, ranked by expected yield

**1. Selection — answer the question actually asked. 147 questions, 9.5pp.**
The largest bucket. Failures return adjacent-but-wrong content: *"What workshop
did Caroline attend?"* → `28 August 2023` (a date, for a "what" question).
49 failures are this exact answer-type mismatch. Fix: constrain the answer to
the question's expected type, and require it to answer the asked question rather
than summarise nearby material.

**2. Completeness — enumerate every supported item. 120 questions, 7.8pp.**
Answers are wrong *sets*: gold `"Nothing is Impossible", "Charlotte's Web"` →
produced `"Charlotte's Web"`. Note this is **not** mainly a list-question
problem corpus-wide — list-valued and single-item questions differ by only
1.5pp overall (65.8% vs 67.3%). It concentrates in single-hop, which is 18% of
the corpus.

**3. Abstention — stop refusing when the evidence is present. 60 questions, 3.9pp.**
Smallest bucket but the cheapest and highest-confidence fix. 146 answers abstain
("not mentioned in the memories"); **79% of those are wrong**, and 32 of the 60
recoverable ones are temporal. Fix: abstain only when the evidence genuinely
lacks the answer, and never hedge with "there is no mention… however".

### Arithmetic, at realistic conversion rates

| step | available | conversion | gain | running |
|---|---|---|---|---|
| baseline | | | | 66.7% |
| 1 selection | 147 | 50% | +73 | 71.4% |
| 2 completeness | 120 | 60% | +72 | 76.1% |
| 3 abstention | 60 | 80% | +48 | 79.2% |
| 4 retrieval recall | 139 | 40% | +55 | **82.8%** |
| unanswerable | 47 | — | never | |

**The three generation fixes reach ~79%.** Adding retrieval lands at **82.8%**,
which is short of the target. Put differently: **85% requires converting 60% of
every *recoverable* failure** — 282 of the 466 that are recoverable at all.

That is a harder bar than the 84.0% this table previously showed, and the
difference is entirely the 47 unanswerable questions that earlier arithmetic
counted as winnable. Treat any plan claiming 85% from prompt work alone as
optimistic; treat one claiming it without touching retrieval as impossible.

### Why this is one change, not three

All three generation fixes are requirements on the *same* answer-generation
contract, and together they address **327 of 513 failures (64%)**. They do not
need three separate projects — one prompt plus a structured output schema that
forces an explicit item list covers all three. That prompt is already written in
`evaluation/replay_generation.py`; it needs an API key and a measured run, not
more design.

Retrieval work should come **after** that run, because its remaining share is
only correctly sized once generation stops masking it.

## Method, and why the raw numbers are not quotable

There is no ground-truth label for "the supporting fact was retrieved", so the
gold answer's presence in the retrieved text is used as a proxy, under three
measures: `strict` (whole answer as substring), `lenient` (every content token),
`partial` (≥50% of content tokens).

**The proxy under-detects**, because it cannot see evidence that was
paraphrased, computed, or partially listed. Under-detection inflates apparent
*retrieval* failure — which is exactly the conclusion the uncorrected numbers
suggest, so they cannot be read directly. Uncorrected, `lenient` reports
single-hop as 78% retrieval failure. That is an artefact.

**Correct answers calibrate it.** When the system answered correctly it
demonstrably had what it needed, so the proxy's detection rate on correct
answers estimates its true-positive rate. Dividing observed detection on wrong
answers by that rate corrects the bias.

Detection rates under `partial`:

| category | detection on correct answers |
|---|---|
| `temporal` | 97.1% |
| `multi_hop` | 95.8% |
| `single_hop` | 83.7% |
| `open_domain` | **38.0%** |

For the conversational categories the correction is a modest adjustment, not an
extrapolation. **`open_domain` is not reliable** — its answers are not in the
conversation at all — and its row should not be quoted. That is pinned as a
test.

### A bug worth recording

The first implementation had `lenient` use exact token matching while `strict`
used substring, so `"sunset"` failed against `"sunsets"` and **lenient was
stricter than strict**. It inverted the two measures and pushed the headline
toward "retrieval failure" — the wrong conclusion. `test_diagnose_accuracy.py`
now asserts `strict ⟹ lenient ⟹ partial` over all 1540 records, so the
implication cannot silently break again.

---

## Limitations

- **Not a live run.** This re-analyses a recorded artifact. Rerunning the
  benchmark needs Ollama at `localhost:11434`, unavailable here.
- **The artifact records ~21 memories per question** (min 15, max 30), not 50.
  The "recall@50" phrasing does not describe this run.
- **The judge is GPT-4o** and its verdicts are taken as ground truth. Some
  "generation failures" may be judge strictness on partially-correct lists —
  though that would make the generation share *larger*, not smaller, since a
  lenient judge would move failures into the correct column.
- **`open_domain` is excluded** from the conclusions for the reason above.
- Categories come from the benchmark's own labels; they were not re-derived.

## Files

```
evaluation/diagnose_accuracy.py            the tool
evaluation/artifacts/accuracy_diagnosis.json   pinned per-measure report + calibration
evaluation/artifacts/failure_modes.json        pinned failure-mode breakdown
evaluation/artifacts/ACCURACY_DIAGNOSIS.txt    human-readable
NeuralGraph/tests/test_diagnose_accuracy.py    18 tests, 2440 subtests
```
