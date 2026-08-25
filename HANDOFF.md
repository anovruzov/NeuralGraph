# Handoff — NeuralGraph / coordination + LoCoMo

Self-contained cold-start bundle: current state, what's proven, what's rejected,
and the open questions worth answering next. Assumes you have the repository and
nothing else — no prior conversation.

Updated 2026-08-25.
Repo `/Users/novruz/NeuralGraph` · branch `locomo-six-fix-loop` · HEAD `ee4528e`
**10 commits ahead of origin · nothing pushed · tree clean** (only the gitignored
`demo/.cache/` is untracked)

---

# PART I — ORIENTATION

## 1. Read in this order

| Need | File |
|---|---|
| **What every claim rests on** | `AUDIT.md` |
| Executed tests, digests, regeneration | `VERIFICATION.md` |
| KEEP / REWRITE / ARCHIVE / DELETE decisions | `CLEANUP_PLAN.md` |
| The paper | `docs/paper/capability_survival.md` |
| Tables (generated, never hand-typed) | `docs/paper/tables.md` |
| Design, invariants | `docs/ARCHITECTURE.md` |
| Coordination numbers | `docs/RESULTS.md` |
| Regenerate every number | `docs/REPRODUCE.md` |
| Claims → evidence → test | `docs/PAPER.md` |
| Retrieval accuracy diagnosis | `docs/ACCURACY.md` |
| LoCoMo loop final state | `evaluation/artifacts/overnight_80/FINAL_REPORT.md` |
| Rules, naming traps, pinned results | `CLAUDE.md` |
| Status, gates, open decisions | `state.md` |

## 2. Environment — this bites everyone

**Use Python 3.11 explicitly.** `python3` here is `/usr/bin/python3` (3.9.6) and
**cannot collect the test suite at all** — `TypeError: unsupported operand
type(s)` on `X | Y` annotations in every test file. Bare `pytest` is **not on
PATH**.

```bash
/opt/homebrew/bin/python3.11 --version          # 3.11.14
/opt/homebrew/bin/python3.11 -m pytest -q       # 303 passed, 1 skipped, 3382 subtests
```

Canonical commands, all from repo root, no venv step:

```bash
pip install -r requirements.txt
python3.11 -m pytest
python3.11 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3.11 -m NeuralGraph.coordination.experiment --output experiment.json
python3.11 -m NeuralGraph.coordination.scale --format markdown
python3.11 -m tools.build_paper_tables --check
cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS
# macOS has no sha256sum; use shasum -a 256

# LoCoMo evaluation (local answerer unpaid; judge is paid and currently blocked)
ollama serve
python3.11 -m evaluation.improvement_loop.batch validation --size 5 --concurrency 2
```

## 3. Naming traps

- `NeuralGraph/coordination/` is the cross-node coordinator.
- `NeuralGraph/tesseract.py` (2,212 lines) is **intra-node retrieval fusion**.
  Not the coordinator, not a distributed router.
- **"Mycelic" appears in no committed file.** Branch names only.
- **NATS / JetStream are not implemented.** Everything is in-process. No
  transport, no wall-clock latency anywhere.
- Commit `b5b9c3e271` **does not exist**.
- **Two different "eights".** The repo has 8 *placement/repair strategies*. An
  older writeup has 8 *architecture families*, of which **zero** are implemented.
  Never present the strategy table as an architecture comparison.

## 4. Non-negotiable rules

1. Never refresh a pinned artifact to make a test green.
2. Never skip, xfail or quarantine a test.
3. Retrieval-track files belong to Nurman: `answering.py`,
   `llm_profile_extractor.py`, `prompts.py`, `reranker.py`, `service.py`,
   `tesseract.py`, `demo/runner.py`.
4. Never synthesize a failure domain.
5. Coordination stays domain-neutral; only `storage_adapter.py` bridges.
6. Lineage direction is load-bearing: parents are *targets* of outgoing
   HIERARCHY edges plus `source_memory_ids`.
7. Repair policies see exported contracts only, never `Placement` internals.
8. Never alter gold answers, labels, split membership, or the denominator.
9. Never drop provider/parse/timeout errors from the denominator.
10. **Do not touch `stash@{0}`** ("pre-replay 2026-08-21", retrieval-track work).
11. Do not push, force-push, rewrite history, or delete branches.
12. Do not inspect the locked test.

---

# PART II — WHAT IS PROVEN

## 5. Coordination track — DONE, results pinned

If any of these moves, a claim about the world changed. Stop and say so.

| strategy | survival | silent forgetting | bytes |
|---|---|---|---|
| `isolated_local` | 0.000 | 0.000 | 1,371 |
| `centralized` | 0.111 | 0.667 | 3,020 |
| `fixed_distributed_replication` | 0.556 | 0.444 | 4,943 |
| `source_count_repair` | 0.556 | 0.444 | 4,941 |
| `full_replication` | 0.667 | 0.333 | 9,877 |
| `random_path_diversification` | 0.704 | 0.296 | 5,327 |
| `lineage_aware_repair` | **0.778** | 0.222 | 5,448 |
| `oracle_min_cut` | **0.889** | 0.111 | 6,461 |

30 seeds × 8 strategies × 9 interventions = 72 cells. All 4 artifacts regenerate
byte-identically.

Reported against interest: no distributed strategy survives `network_partition`
(0.00); `lineage_aware` loses to `source_count` in exactly **one of six** cells —
`node_failure` at **I=1** — and ties at I=2. The controlling variable is
independent roots, not K or H.

## 6. LoCoMo track — frozen setup

| | |
|---|---|
| corpus | `demo/maximal.json`, digest `916ca308d5e41cba…` (1540 q, 4 categories) |
| raw corpus | `evaluation/locomo/locomo10.json` (1986 q, 5 categories, **gold `dia_id` evidence**) |
| splits | 40/20/40, seed 20260825 → dev 624 / val 305 / locked 611 |
| validation eval set | 60 q, 15/category, digest `8077c40ba2e7cff2` |
| locked eval set | 60 q, digest `c04acf27b0e939d7` — **never inspected** |
| answerer | `qwen2.5:7b-instruct` (local Ollama, unpaid) |
| judge | `gpt-4o`, prompt hash `5e923dc613887b80`, config `cd9098dfbdd0ad06` |
| retrieval | recorded excerpts ∪ entity/BM25 top-5, relevance-first (frozen additive) |

**Baseline: 24/60 = 40.0%**, 0 operational errors.

## 7. The four findings that matter most

**1. The substring evidence heuristic is unreliable.** The raw corpus carries
gold evidence turn ids and all 120 frozen questions join to it on
(conversation, question). Against that ground truth the heuristic disagrees on
**30 of 60** questions — 22 called "recoverable" with no gold evidence present.
Prefer `dia_id` ground truth for anything load-bearing.

**2. Retrieval is not the bottleneck; generation is.** Corrected, recorded
retrieval already holds gold evidence for **46/60 (76.7%)**, complete evidence
for 36/60. Only 22 of 48 evidence-present questions are answered correctly —
**26 generation-recoverable failures**.

**3. The judge is nondeterministic at ~4.5%.** Measured on a byte-identical
candidate: 1 CORRECT in 22 identical calls at `temperature: 0` — ~2.7 expected
spurious flips per 60 questions. **Never adjudicate a round on the raw delta.**

**4. Current failure mix** (26 evidence-present wrong answers):
`wrong_selection` **10**, `judge_disagreement` **7** (but see §9),
`incomplete_list` **4**, `unsupported_item` **4**, `temporal_error` **1**.

**Ceiling.** 48/60 = 80.0% evidence-present, +2 answered correctly without
retrieved evidence → **83.3% empirical**. Conversion is 22/48 = **45.8%**.

## 8. Rejected experiments — preserved, not deleted

| experiment | where | why rejected |
|---|---|---|
| **v2 abstention gate** | `evaluation/improvement_loop/rejected_v2/` | Stated mechanism never fired (0 temporal false abstentions recovered; Qwen abstains on 0/15 temporal). Gained +0.083 judged but from an *unintended* global chronological re-ordering; McNemar p=0.18, CI spans zero, item recall −0.029. Adjudication in `evaluation/artifacts/locomo_loop/V2_ROOT_CAUSE.md`. |
| **Candidate E (replacement)** | `evaluation/improvement_loop/candidate_e/` | recall@20 0.331 vs baseline 0.668; net **−22** questions. |
| **Candidate E (additive)** | same | Recovers **+2**, below the +5 gate. Kept as the frozen retrieval config: harmless, lifts evidence availability to 48/60. |
| **Candidate B (completeness)** | `evaluation/improvement_loop/candidate_b/` | Failed the unpaid gate: unsupported items 17→24 (token-wise 7→11, item length unchanged). Recall +0.069, precision +0.022 — the mechanism works but buys completeness with invention. |

Do not re-attempt these in the same form.

---

# PART III — WHAT TO FIX NEXT

## 9. Correction that reframes the priorities

An earlier note claimed `judge_disagreement` was 27% of addressable failures and
"the cheapest real gain". **That was wrong.** The label came from a heuristic
(item recall ≥ 0.8). Inspecting the 7 rows individually, only ~2 are genuine
judge errors:

| id | gold | answered | verdict |
|---|---|---|---|
| `q1018` | "making **his** first mobile game" | "making **my** first mobile game" | **judge error** — pronoun only |
| `q617` | "California **or** Florida" | "California" | **gold-format** — disjunctive gold |
| `q554` | "stuffed animal dog named Tilly" | `['Tilly', 'writing']` | model error — spurious item |
| `q952` | "November 5-6, 2022" | `['7 November, 2022', '3 October, 2022']` | model error — wrong dates |
| `q1081` | "May 2023" | `['1 February, 2023']` | model error — wrong date |
| `q1067` | "in summer 2022" | `['last summer']` | **unresolved relative date** |

Judge calibration is worth ~**2 of 26** (~8%), not 27%. This is the **third**
time a heuristic label has misdirected work here (see also the substring
evidence measure, §7.1). **Spot-check rows before any label drives a decision.**

## 10. Open questions

Each is scoped to be *answered*. **FREE** = local Ollama / deterministic.
**PAID** = gpt-4o judge, currently blocked (§11).

### Q1 — Is the corpus gold itself a bounded source of loss?
*Known.* `q617` gold is a disjunction (`"California or Florida"`); the judge
penalises a correct single answer. `q1067` gold is relative-resolved.
*Unknown.* How many of the 120 golds are disjunctive, relative, ranged, or list?
*Experiment (FREE).* Regex-classify all 120 golds; cross-tab against correctness.
*Unblocks.* Whether to add a disjunction-aware judge rule — a grading-contract
change, which must be pre-registered and applied to baseline **and** candidate
alike, never retrofitted to one side.

### Q2 — Do the injected date annotations reach the answerer?
*Known.* The recorded pipeline injects `"yesterday [= 7 May 2023]"` into evidence
text (found while fixing excerpt→turn mapping, 76.2% → 97.3%). Yet `q1067`
answered `"last summer"`.
*Unknown.* Are annotations present in what the answerer receives, and do they
predict temporal correctness?
*Experiment (FREE).* Count annotated excerpts per question; split temporal
questions by annotated/not; compare accuracy.
*Unblocks.* Present-but-ignored → instructional fix. Absent → upstream rendering
fix. Different owners.

### Q3 — Is `wrong_selection` (10 of 26) one thing?
*Known.* Largest addressable bucket, no proposed mechanism, and it is the
*residual* label so it absorbs whatever the labels above it miss. Inspected rows
look heterogeneous: `q51` gold `"Liberal"` answered `"LGBTQ rights"` (inference,
not extraction); `q242` gold `"Middle-class or wealthy"` answered with a verbatim
money quote; `q82` gold `"No; she's in the process of adopting children."`
answered `[]`.
*Unknown.* One mechanism or a dumping ground?
*Experiment (FREE).* Hand-classify all 10: requires-inference / wrong-entity /
wrong-abstention / answer-type / mislabelled.
*Unblocks.* Everything. If most need inference beyond extraction, no
retrieval-or-completeness mechanism helps and the ceiling is below what
evidence-availability arithmetic suggests.

### Q4 — Can support filtering deliver Candidate B's gain without its cost?
*Known.* Candidate B raised recall +0.069 and precision +0.022 but failed on
unsupported items 17 → 24 (token-wise 7 → 11; item length unchanged, so real).
The mechanism works; the self-policing does not.
*Unknown.* Does deterministic per-item support filtering *before output* keep the
recall gain while returning unsupported items to baseline?
*Experiment (FREE).* The 30 generated rows are already cached in
`evaluation/artifacts/overnight_80/candidate_b_cache.json`. Filter post-hoc and
recompute. No new generation, no judging.
*Unblocks.* Whether a repaired Candidate B earns a paid pilot. **Cheapest live
experiment here.**
*Caveat.* Filtering risks dropping correct paraphrases — measure recall loss too.

### Q5 — Is the 7B answerer the binding constraint? (PAID, ~60 calls)
*Known.* The recorded GPT-4o-era pipeline scored **66.7%** on the full 1540.
Current local `qwen2.5:7b-instruct` scores **40.0%** on the frozen 60 and
**0.379 exact-set-match** on the full 1540 — same evidence, same prompt.
*Unknown.* How much of the gap is model capability rather than any addressable
mechanism?
*Experiment.* Run the frozen 60 with a stronger answerer, everything else
identical.
*Unblocks.* Whether to keep optimising prompts for a 7B at all. If a stronger
answerer closes most of the gap unaided, the loop has been optimising the wrong
variable.

### Q6 — What is the honest ceiling, and is 80% reachable?
*Known.* 48/60 evidence-present, 83.3% empirical ceiling, conversion 45.8%.
80% would require conversion of ~96%.
*Experiment.* Q3 + Q5 bound this together.
*Unblocks.* Whether "≥80%" survives as an objective. **No score is full-LoCoMo
without a completed 1540-question run**; `VALIDATION_80` ≠ `FULL_80`.

### Q7 — 1986/5-category protocol or 1540/4-category subset?
*Known.* The raw corpus holds **1986 questions across 5 categories** with gold
evidence; `demo/maximal.json` holds a **1540/4-category** subset. All 120 frozen
questions join cleanly.
*Unknown.* What is category 5, and why were 446 questions dropped?
*Experiment (FREE).* Tabulate category 5; sample 20; check whether unanswerable
by construction.
*Unblocks.* Which protocol the paper reports. **A 1540 score and a 1986 score
must never be compared as the same benchmark.**

### Q8 — Can n=60 adjudicate any single-mechanism fix?
*Known.* Judge flips ~4.5% on byte-identical answers → ~2.7 spurious flips per
60. The v2 round's +0.083 was non-significant (McNemar p=0.18, bootstrap CI
[−0.033, +0.200]). A ±0.13 CI means nothing below ~8 net questions is
distinguishable from noise.
*Experiment (FREE).* Power analysis by simulation over existing paired rows.
*Unblocks.* Whether the frozen set can adjudicate *anything*, or must be enlarged
first. **If it cannot, further rounds are theatre** — which makes this the most
important question here.

## 11. Priority and budget

| # | Question | Cost |
|---|---|---|
| **Q8** | Is n=60 adequate to detect anything? | FREE |
| **Q4** | Support-filtered Candidate B | FREE |
| **Q3** | Is `wrong_selection` one thing? | FREE |
| **Q2** | Do date annotations reach the answerer? | FREE |
| **Q1** | Is gold format costing us? | FREE |
| **Q6** | Honest ceiling | FREE |
| **Q7** | 1986 vs 1540 protocol | FREE |
| **Q5** | Is the 7B the constraint? | PAID |

Seven of eight are free. Do those before spending anything.

`evaluation/improvement_loop/budget.py` reserves before each request, counts
retries, persists atomically. **48 paid requests consumed against a 40-request
ceiling** (75% stop = 30). `BUDGET_LEDGER.json` has
`paid_calls_permitted: false`. Only **Q5** needs paid calls; blocked until a
human raises the ceiling.

## 12. Paper track (separate, near-done)

`docs/PAPER.md` targets NeurIPS Agentic Web: lock **Aug 26**, submit **Aug 29
AoE**. Open: Figures 1 and 3 (data in `docs/paper/tables.md`), and the
authorship/ownership decision flagged in `state.md`.

## 13. Ownership

- **Nurman** — NeuralGraph: memory formation, graph dynamics, retrieval.
- **Anar** — coordination, lineage semantics, failure experiments, paper.
- Interface: `NeuralGraph/coordination/contracts.py`. Do not widen it casually.
