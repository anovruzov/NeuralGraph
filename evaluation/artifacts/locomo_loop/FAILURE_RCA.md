# LoCoMo failure RCA, per domain

Generated 2026-08-25 by `python3.11 -m evaluation.improvement_loop.rca`.
Data: `demo/maximal.json` — 1540 questions, real NeuralGraph pipeline, GPT-4o
judge, overall accuracy **66.7%**, **513 failures**.

Analysed on the recorded full-pipeline results rather than the local 7B replay,
because the replay measures a much weaker answerer and would diagnose the model
instead of the system.

Every failing question receives exactly one label, assigned in a fixed priority
order (evidence-side causes first, since no prompt change recovers evidence that
was never retrieved), so the counts partition the failures.

---

## The headline finding: the dominant cause depends on the evidence measure

This is the result, not a caveat. "Was the evidence retrieved?" has two
defensible definitions in `diagnose_accuracy.py`, and they disagree about the
root cause for half the domains:

- **lenient** — every content token of the gold answer appears in the retrieved text
- **partial** — at least half of them do (this is what `docs/ACCURACY.md` headlines)

| domain | dominant under `lenient` | dominant under `partial` |
|---|---|---|
| open_domain | retrieval_miss (96%) | retrieval_miss (76%) |
| single_hop | retrieval_miss (77%) | retrieval_miss (36%) |
| **temporal** | **retrieval_miss** (51%) | **wrong_selection** (37%) |
| **multi_hop** | **retrieval_miss** (59%) | **wrong_selection** (62%) |

Overall failure mix moves just as much:

| label | lenient | partial |
|---|---|---|
| retrieval_miss | **335 (65%)** | 186 (36%) |
| wrong_selection | 135 (26%) | **235 (46%)** |
| incomplete_list | 18 (4%) | 47 (9%) |
| false_abstention | 19 (4%) | 39 (8%) |
| answer_type_mismatch | 6 (1%) | 6 (1%) |

**Consequence.** `docs/ACCURACY.md` concludes the corpus is generation-bound
("327 of 513 had the evidence"). That conclusion holds only under `partial`.
Under `lenient` the same corpus is retrieval-bound, 335/513. Any fix plan must
state which measure it targets, and a plan built on `partial` will spend its
effort on generation for exactly the two domains where the measures disagree.

Both measures agree on one thing: **open_domain and single_hop are
retrieval-bound under either definition.** That is the safe ground.

---

## Per-domain RCA

### open_domain — n=96, accuracy 52.1%, 46 failures

**Dominant: retrieval_miss (96% lenient / 76% partial).** The most
retrieval-bound domain by a wide margin, and it is not close under either
measure.

Evidence was present for only **12%** (lenient) / **31%** (partial) of
questions. Recoverable failures: **2** of 46 (lenient), **11** of 46 (partial).

**Root cause.** The retriever essentially does not surface evidence for
open-domain questions. Generation is near-irrelevant here — even a perfect
answerer recovers at most 11 of 46 failures. 35% of these questions are
list-valued, and they are 37% of the failures, so list handling is a
second-order effect at best.

**Implication.** No prompt work helps this domain. It needs retrieval, or the
questions are genuinely unanswerable from the corpus. `docs/ACCURACY.md`
already records 47 corpus-wide questions whose gold answer appears nowhere in
the source conversation; open_domain is where they concentrate.

### single_hop — n=282, accuracy 52.1%, 135 failures

**Dominant: retrieval_miss (77% lenient / 36% partial)** — retrieval-bound
under both, though far less decisively under `partial`.

Evidence present: **36%** (lenient) / **74%** (partial). Recoverable failures:
**31** (lenient) / **86** (partial) — a 2.8× spread that entirely determines
whether this domain is worth a generation fix.

**Root cause.** This is the domain where the measure choice matters most in
absolute terms. **66% of single-hop questions are list-valued** and lists are
**69% of its failures** — strongly over-represented. Under `partial`, 86
failures had at least half the gold tokens retrieved, meaning the model saw
part of a list and returned an incomplete one. Under `lenient`, the full list
was rarely all present, meaning retrieval never surfaced the whole answer.

**Implication.** The list mechanism is the lever either way, but the *layer*
differs: under `partial` it is a generation problem (assemble the complete list
from what was retrieved); under `lenient` it is a retrieval problem (retrieve
all the list members in the first place). Worth resolving before spending a
round here — this is the largest single pool of ambiguous failures.

### temporal — n=321, accuracy 64.5%, 114 failures

**Dominant: FLIPS — retrieval_miss (51%) under lenient, wrong_selection (37%)
under partial.**

Evidence present: **72%** (lenient) / **90%** (partial). Recoverable failures:
**56** / **87**.

**Root cause.** Under `partial`, 90% of temporal questions had their evidence
retrieved and the model still answered unrelated content 37% of the time — a
selection defect inside generation, not a retrieval gap. This domain also
carries the corpus's highest false-abstention count (**15 lenient / 39
corpus-wide partial**): the model declining despite holding the evidence.

**Implication.** The clearest generation-side target in the corpus. High
evidence availability plus high wrong-selection is exactly the signature a
prompt or routing fix can move, and false abstention is the cheapest sub-class
to fix.

### multi_hop — n=841, accuracy 74.1%, 218 failures

**Dominant: FLIPS — retrieval_miss (59%) under lenient, wrong_selection (62%)
under partial.**

Evidence present: **70%** (lenient) / **88%** (partial). Recoverable failures:
**89** / **143** — the largest recoverable pool in absolute terms.

**Root cause.** The best-performing domain, and under `partial` its failures are
overwhelmingly wrong_selection (62%) with negligible abstention (0.9%) and
negligible incomplete lists (0.9%). Only 32% of its questions are list-valued.
So multi-hop failure is not about omission or invention — it is about picking
the wrong evidence from a set that contained the right evidence.

**Implication.** Highest-volume target (841 questions, 143 recoverable under
`partial`), but the mechanism is evidence selection/attention, which is harder
to move with a prompt edit than abstention or list completion.

---

## Ranking, if a fix budget had to be spent

Under `partial` (the measure `docs/ACCURACY.md` uses), by recoverable failures:

| rank | domain | recoverable | dominant mechanism | tractability |
|---|---|---|---|---|
| 1 | multi_hop | 143 | wrong_selection | hard — attention/selection |
| 2 | temporal | 87 | wrong_selection + false_abstention | **easiest** — abstention is cheap |
| 3 | single_hop | 86 | incomplete lists | medium — depends on measure |
| 4 | open_domain | 11 | retrieval_miss | not fixable in generation |

**Best first target: temporal.** Not the largest pool, but it combines high
evidence availability (90%) with the corpus's concentration of false
abstention — the one failure class where the model demonstrably holds the
answer and declines to give it.

**Do not start with open_domain.** At 11 recoverable failures out of 46, it is
retrieval-bound under every measure and cannot be moved from the generation
side.

---

## Caveats

- Labels are heuristic (substring/token matching against retrieved text and the
  predicted answer), not human-adjudicated. They are consistent across domains,
  so the *relative* ranking is more trustworthy than any single share.
- `wrong_selection` is the residual bucket in the priority order and will absorb
  misclassifications from the labels above it. Its absolute size is the least
  reliable number here.
- The 47 corpus-wide unanswerable questions (`docs/ACCURACY.md`) are counted as
  `retrieval_miss`; they are not recoverable by any means and inflate that bucket.
- All of this describes the **recorded pipeline at 66.7%**, not the local
  `qwen2.5:7b-instruct` replay, which scores far lower and would give a
  different (model-bound) diagnosis.

## Artifacts

- `evaluation/artifacts/locomo_loop/failure_rca.json` — both measures, per domain, machine-readable
- `evaluation/improvement_loop/rca.py` — generator
