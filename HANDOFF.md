# Handoff — NeuralGraph / coordination + LoCoMo

Self-contained cold-start bundle. Updated 2026-08-25. Assumes you have the
repository and nothing else — no prior conversation.

Repo: `/Users/novruz/NeuralGraph`
Branch: `locomo-six-fix-loop` · HEAD `bdf255f4a048b85efdbbdff8022ba516bf440b91`
**8 commits ahead of origin · nothing pushed · working tree clean** (only the
gitignored `demo/.cache/` is untracked)

---

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
| **LoCoMo loop: current state** | `evaluation/artifacts/overnight_80/FINAL_REPORT.md` |
| Rules, naming traps, pinned results | `CLAUDE.md` |
| Status, gates, open decisions | `state.md` |

---

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
```

LoCoMo evaluation (local answerer, unpaid; judge is paid and currently blocked):

```bash
python3.11 -m evaluation.improvement_loop.batch validation --size 5 --concurrency 2
ollama serve            # answerer + embeddings; must be running
```

---

## 3. Naming traps

- `NeuralGraph/coordination/` is the cross-node coordinator.
- `NeuralGraph/tesseract.py` (2,212 lines) is **intra-node retrieval fusion**.
  Not the coordinator, not a distributed router.
- **"Mycelic" appears in no committed file.** Branch names only.
- **NATS / JetStream are not implemented.** Everything is in-process. No
  transport, no wall-clock latency anywhere.
- Commit `b5b9c3e271` **does not exist**.
- **Two different "eights".** The repo has 8 *placement/repair strategies*. An
  older writeup has 8 *architecture families* of which **zero** are implemented.
  Never present the strategy table as an architecture comparison.

---

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

---

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

---

## 6. LoCoMo track — current state

### Frozen evaluation setup

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

### The four findings that matter most

**1. The substring evidence heuristic is unreliable.** The raw corpus carries
gold evidence turn ids and all 120 frozen questions join to it on
(conversation, question). Against that ground truth the heuristic disagrees on
**30 of 60** questions — 22 called "recoverable" with no gold evidence present.
Prefer `dia_id` ground truth over `diagnose_accuracy` measures for anything
load-bearing.

**2. Retrieval is not the bottleneck; generation is.** Corrected, recorded
retrieval already holds gold evidence for **46/60 (76.7%)**, complete evidence
for 36/60. Only 22 of the 48 evidence-present questions are answered correctly —
**26 generation-recoverable failures**.

**3. The judge is nondeterministic at ~4.5%.** Measured on a byte-identical
candidate: 1 CORRECT in 22 identical calls at `temperature: 0`. That is ~2.7
expected spurious flips per 60 questions. **Never adjudicate a round on the raw
delta alone** — classify identical/equivalent outputs and hold their verdict
fixed.

**4. Current failure mix** (26 evidence-present wrong answers):
wrong_selection **10**, judge_disagreement **7**, incomplete_list **4**,
unsupported_item **4**, temporal_error **1**.

### Ceiling

48/60 = 80.0% evidence-present, +2 answered correctly without retrieved evidence
→ **83.3% empirical ceiling**. Conversion is 22/48 = 45.8%. 80% judged accuracy
would require converting essentially every evidence-present question.

### Rejected experiments — preserved, not deleted

| experiment | where | why rejected |
|---|---|---|
| **v2 abstention gate** | `evaluation/improvement_loop/rejected_v2/` | Stated mechanism never fired (0 temporal false abstentions recovered; Qwen abstains on 0/15 temporal). Gained +0.083 judged but from an *unintended* global chronological re-ordering; McNemar p=0.18, CI spans zero, item recall −0.029. Full adjudication in `evaluation/artifacts/locomo_loop/V2_ROOT_CAUSE.md`. |
| **Candidate E (replacement)** | `evaluation/improvement_loop/candidate_e/` | recall@20 0.331 vs baseline 0.668; net **−22** questions. |
| **Candidate E (additive)** | same | Recovers **+2**, below the +5 gate. Kept as the frozen retrieval config because it is harmless and lifts evidence availability to 48/60. |
| **Candidate B (completeness)** | `evaluation/improvement_loop/candidate_b/` | Failed the unpaid gate: unsupported items 17→24 (token-wise 7→11, item length unchanged). Recall +0.069 and precision +0.022, so the mechanism works but buys completeness with invention. |

### Budget — currently blocked

`evaluation/improvement_loop/budget.py` reserves **before** each request, counts
retries as requests, persists atomically, and carries prior spend forward.

**48 paid judge requests already consumed** against a 40-request default ceiling
(75% stop point = 30). `BUDGET_LEDGER.json` has `paid_calls_permitted: false`.
**Any new judged evaluation is blocked until a human raises the ceiling.**

---

## 7. What to do next

Ranked by expected value:

1. **Judge calibration.** `judge_disagreement` is 7 of 26 addressable failures
   (27%) — e.g. `q753` gold `"brave, selfless, down-to-earth attitude"`,
   answered `"he's brave, selfless, down-to-earth"`, judged wrong. This is a
   grading question, not a model change, and is the cheapest real gain.
2. **Repaired Candidate B**: filter each emitted item against evidence support
   *before output*, rather than asking the model to self-police. Tests the same
   mechanism without the invention cost. Propose as a new mechanism, do not tune
   the rejected one.
3. **wrong_selection (10)** is the largest bucket and has no proposed mechanism.
4. Do **not** re-attempt global re-ordering or abstention instructions.

### Paper (separate track, near-done)

`docs/PAPER.md` targets NeurIPS Agentic Web: lock **Aug 26**, submit **Aug 29
AoE**. Open: Figures 1 and 3 (data is in `docs/paper/tables.md`), and the
authorship/ownership decision flagged in `state.md`.

---

## 8. Ownership

- **Nurman** — NeuralGraph: memory formation, graph dynamics, retrieval.
- **Anar** — coordination, lineage semantics, failure experiments, paper.
- Interface: `NeuralGraph/coordination/contracts.py`. Do not widen it casually.
