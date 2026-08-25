# Open questions — what to fix, and what we'd need to know first

Written 2026-08-25 at `bdf255f`. Companion to `HANDOFF.md`.

Every question below is scoped so it can be *answered*, not just discussed. Each
names the evidence we already have, the specific unknown, the cheapest
experiment that settles it, and the decision it unblocks. Cost is marked
**FREE** (local Ollama / deterministic) or **PAID** (gpt-4o judge, currently
blocked — see §Budget).

Three attempted mechanisms have already been rejected on evidence: the v2
abstention gate, Candidate E replacement retrieval, and Candidate B
completeness. Do not re-attempt them in the same form. `HANDOFF.md` §6 says why.

---

## Correction that reframes everything below

An earlier note in this repo claimed `judge_disagreement` was 27% of addressable
failures and "the cheapest real gain". **That was wrong.** The label was assigned
by a heuristic (item recall ≥ 0.8), and inspecting the 7 rows individually shows
only about 2 are genuine judge errors:

| id | gold | answered | verdict |
|---|---|---|---|
| `q1018` | "making **his** first mobile game" | "making **my** first mobile game" | **judge error** — pronoun only |
| `q617` | "California **or** Florida" | "California" | **gold-format** — disjunctive gold |
| `q554` | "stuffed animal dog named Tilly" | `['Tilly', 'writing']` | model error — spurious extra item |
| `q952` | "November 5-6, 2022" | `['7 November, 2022', '3 October, 2022']` | model error — wrong dates |
| `q1081` | "May 2023" | `['1 February, 2023']` | model error — wrong date |
| `q1067` | "in summer 2022" | `['last summer']` | **unresolved relative date** |

So judge calibration is worth roughly **2 of 26** addressable failures (~8%), not
27%. Any label produced by a heuristic in this repo should be spot-checked
against rows before it drives a decision — this is the third time a heuristic
label has misdirected work here (see also the substring evidence measure,
`HANDOFF.md` §6).

---

## Q1 — Is the corpus gold itself a bounded source of loss?

**What we know.** `q617`'s gold is `"California or Florida"` — a disjunction, where
either answer should count. The judge is instructed that missing an item is
INCORRECT, so it penalises a correct single answer. `q1067`'s gold is
`"in summer 2022"` where the evidence says `"last summer"`; the answer is right
in substance and wrong in resolution.

**Unknown.** How many of the 60 validation golds are disjunctive, relative, or
otherwise not a single canonical string?

**Experiment (FREE).** Regex-classify all 120 frozen golds into: single literal,
disjunctive (`" or "`), range (`"5-6"`), relative (`"last "`, `"ago"`), and
list. Cross-tabulate against current correctness.

**Unblocks.** Whether to add a disjunction-aware judge rule (a grading-contract
change, which must be pre-registered and applied to baseline *and* candidate
alike, never retrofitted to one side).

---

## Q2 — Do the injected date annotations reach the answerer?

**What we know.** The recorded pipeline injects resolved dates into evidence text:
`"yesterday [= 7 May 2023]"`. This was discovered while fixing excerpt→turn
mapping (coverage 76.2% → 97.3%). Separately, `q1067` answered `"last summer"`
where gold wanted `"summer 2022"`.

**Unknown.** Is the `[= …]` annotation present in the excerpts the answerer
actually receives, and does its presence predict temporal correctness?

**Experiment (FREE).** Count annotated excerpts per question in
`demo/maximal.json`; split temporal questions by annotated / not; compare
current accuracy across the split.

**Unblocks.** If annotations are present and temporal accuracy is still poor, the
answerer ignores them and the fix is instructional. If absent, the fix is
upstream in evidence rendering. These have completely different owners.

---

## Q3 — Why is `wrong_selection` (10 of 26) the largest bucket, and is it one thing?

**What we know.** It is the biggest addressable bucket and has no proposed
mechanism. Inspected rows look heterogeneous:

- `q51` gold `"Liberal"`, answered `"LGBTQ rights"` — an *inference* question, not extraction
- `q242` gold `"Middle-class or wealthy"`, answered with a verbatim quote about money problems
- `q48` gold `"Her mentors, family, and friends"`, answered `"Melanie"`
- `q82` gold `"No; she's in the process of adopting children."`, answered `[]`

Several are `open_domain`, which the RCA already shows is the weakest category
(20% accuracy) and the most evidence-starved.

**Unknown.** Is `wrong_selection` a single mechanism or a dumping ground? It is
the *residual* bucket in the priority order, so it absorbs everything the labels
above it miss.

**Experiment (FREE).** Hand-classify all 10 into: requires inference beyond
extraction / wrong entity selected / abstained wrongly / answer-type wrong /
mislabelled. Report the split.

**Unblocks.** Everything. If most are inference questions, no
retrieval-or-completeness mechanism helps and the ceiling is lower than the
evidence-availability arithmetic suggests.

---

## Q4 — Can item-level support filtering deliver Candidate B's gain without its cost?

**What we know.** Candidate B raised item recall **+0.069** and precision
**+0.022** but failed its gate because unsupported items rose 17 → 24 (token-wise
7 → 11, item length unchanged, so real). The mechanism works; the self-policing
does not.

**Unknown.** If each emitted item is filtered against evidence support
*deterministically before output*, does the recall gain survive while
unsupported items return to baseline?

**Experiment (FREE).** The 30 generated Candidate B rows are already cached in
`evaluation/artifacts/overnight_80/candidate_b_cache.json`. Apply a support
filter post-hoc and recompute recall / precision / unsupported. No new
generation, no judging.

**Unblocks.** Whether a repaired Candidate B is worth a paid pilot. This is the
single cheapest live experiment in this document.

**Caveat.** Filtering on support risks dropping correct paraphrases — measure
recall loss, not just unsupported reduction.

---

## Q5 — Is the 7B answerer the binding constraint rather than any mechanism?

**What we know.** The recorded GPT-4o-era pipeline scored **66.7%** on the full
1540. The current local `qwen2.5:7b-instruct` scores **40.0%** on the frozen 60
and **0.379 exact-set-match** on the full 1540. Same evidence, same prompt.

**Unknown.** How much of the 26-question gap is model capability rather than any
addressable mechanism?

**Experiment (PAID, ~60 calls).** Run the frozen 60 with a stronger answerer,
everything else identical. The delta bounds what mechanism work can ever recover
on this answerer.

**Unblocks.** Whether to keep optimising prompts for a 7B model at all. If a
stronger answerer closes most of the gap with no mechanism change, the loop has
been optimising the wrong variable.

---

## Q6 — What is the honest ceiling, and is 80% reachable at all?

**What we know.** Evidence-present is 48/60 (80.0%) under frozen additive
retrieval; +2 answerable without retrieved evidence → **83.3% empirical
ceiling**. Current conversion is 22/48 = **45.8%**.

**Unknown.** What conversion rate is actually attainable? 80% judged accuracy
requires converting essentially every evidence-present question — conversion
would have to go 45.8% → ~96%.

**Experiment.** Q3 and Q5 together bound this. If `wrong_selection` is mostly
inference questions and the answerer is capability-bound, 80% is not reachable
and the target should be restated.

**Unblocks.** Whether "≥80%" remains the objective or is replaced by a defensible
one. **No score should be claimed as full-LoCoMo without a completed 1540-question
run**; `VALIDATION_80` ≠ `FULL_80`.

---

## Q7 — Should the 1986/5-category protocol replace the 1540/4-category subset?

**What we know.** `evaluation/locomo/locomo10.json` holds **1986 questions across
5 categories** with gold `dia_id` evidence. `demo/maximal.json` holds a
**1540-question, 4-category** subset. All 120 frozen questions join cleanly
between them.

**Unknown.** What is category 5, and why were 446 questions dropped? If they are
adversarial/unanswerable, scoring them changes every denominator.

**Experiment (FREE).** Tabulate category 5; sample 20; determine whether they are
unanswerable by construction.

**Unblocks.** Which protocol the paper reports. **A 1540 score and a 1986 score
must never be compared as though they were the same benchmark.**

---

## Q8 — Is the paired-comparison methodology sound enough to accept any round?

**What we know.** The judge flips ~**4.5%** on byte-identical answers at
`temperature: 0` (1 CORRECT in 22 identical calls). On 60 questions that is ~2.7
expected spurious flips. The v2 round's entire +0.083 was non-significant
(McNemar p=0.18, bootstrap CI [−0.033, +0.200]).

**Unknown.** What effect size is detectable at n=60? Roughly: a bootstrap CI of
±0.13 means nothing below ~8 net questions is distinguishable from noise.

**Experiment (FREE).** Power analysis by simulation over the existing paired
rows.

**Unblocks.** Whether the 60-question validation set can adjudicate *any*
single-mechanism fix, or whether the frozen set must be enlarged before more
rounds are run. **If it cannot, further rounds are theatre** and this is the most
important question in this document.

---

## Priority

| # | Question | Cost | Why |
|---|---|---|---|
| **Q8** | Is n=60 adequate to detect anything? | FREE | If no, every other question is unanswerable at this scale |
| **Q4** | Support-filtered Candidate B | FREE | Cheapest live experiment; data already cached |
| **Q3** | Is `wrong_selection` one thing? | FREE | Largest bucket, no mechanism proposed |
| **Q2** | Do date annotations reach the answerer? | FREE | Clear owner split either way |
| **Q1** | Is gold format costing us? | FREE | Bounded, and a grading-contract change |
| **Q6** | Honest ceiling | FREE | Follows from Q3 + Q5 |
| **Q7** | 1986 vs 1540 protocol | FREE | Must settle before any paper claim |
| **Q5** | Is the 7B the constraint? | PAID | Highest information, needs budget |

Seven of eight are free. Do those before spending anything.

---

## Budget

`evaluation/improvement_loop/budget.py` reserves before each request, counts
retries, and persists atomically. **48 paid requests already consumed against a
40-request ceiling** (75% stop = 30). `BUDGET_LEDGER.json` has
`paid_calls_permitted: false`. Only **Q5** needs paid calls, and it is blocked
until a human raises the ceiling.

## Standing constraints

Do not alter gold answers, labels, split membership, or the denominator. Do not
inspect the locked test. Do not drop operational errors. Do not touch
`stash@{0}`. Do not push.
