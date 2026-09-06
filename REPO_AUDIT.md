# REPO_AUDIT — anovruzov/NeuralGraph

Audit session 2026-09-06, local machine (macOS arm64, Darwin 24.6.0),
Python 3.11.14, numpy 2.4.6, pytest 9.1.1. This finishes the audit begun
in a prior cloud session whose workspace (`/workspace/scratch/…`) and
three cleanup commits (`e45c5c7a`, `35570724`, `130e82da`) did not
survive; those commits were recreated fresh here (§10, CLEANUP_LOG.md).
All work happened in an isolated worktree; the original dirty checkout
at `/Users/novruz/NeuralGraph` was never modified.

## 1. Repository Status

| Item | Value |
|---|---|
| Remote | `https://github.com/anovruzov/NeuralGraph` (public) |
| Remote main | `8785d691a95352eed618abca5b515aafaeab06dd` "Add project README" (observed 2026-09-06, ls-remote) |
| Audit branch | `audit/repository-readiness-2026-09-06` from `origin/main`, 3 cleanup commits: `999823f`, `f48da4a`, `9591a18` + deliverables commit |
| Local checkout | `/Users/novruz/NeuralGraph`, branch `locomo-six-fix-loop` @ `9125388`, dirty (4 modified tracked files + `demo/.cache/`, `docs/paper/capability_survival.pdf` untracked) — preserved untouched, SHA-256 fingerprinted at session start and re-verified at handoff |
| Worktrees | main checkout; `/Users/novruz/NeuralGraph-tesseract-operator-plugin-v1` @ `899cb4b` (ahead 1); one stale prunable scratchpad entry (left alone) |
| Stash | `stash@{0}` "On tesseract-coordination-v1: pre-replay 2026-08-21" — 8 files, +454/−107, includes edits to the six retrieval-track files and an untracked early `coordination/` prototype. Possibly sole record of pre-campaign experiments. Retained |
| Tags | none |
| PRs | #1 (draft, open), #2 (open), #3 (open, Nurman) — 3 PRs total in repo history, none merged/closed ever (full census via API, state=all) |
| CI | none at any commit (no `.github/`) |

Main is 93 files: the original 2025-12 bulk import + README. It has **no**
requirements.txt, pytest.ini, .gitignore, docs/, coordination module,
pinned artifacts, or CI, and tracked 22 stale `.pyc` files (removed on the
audit branch). All research since July 2026 lives on branches.

## 2. Ownership and Protected Contributor Work

**Nurman Mahammadov (`PNurmanM`)** — retrieval track. PR #3, branch
`research/retrieval-campaign-2026-09` (head `63aef96`; previously audited
head `eed4873` plus two newer commits `307ba2b`, `63aef96`, all authored
by Nurman). Per CLAUDE.md rule 3 his files are `answering.py`,
`llm_profile_extractor.py`, `prompts.py`, `reranker.py`, `service.py`,
`tesseract.py`, `demo/runner.py`. **Nothing of his was modified, pushed
to, rebased, merged, closed, or transplanted.** Findings on his PR are
reported (§4) — no fixes were applied to his branch.

One deliberate intersection, disclosed: audit-branch commit `9591a18`
edits `demo/runner.py` on **our** branch only, solely to remove a live
credential (one line). It recreates prior-session commit `e45c5c7a`,
mirrors the identical fix Nurman himself made in `eed4873`, and is
flagged for his review.

**anovruzov (Anar)** — everything else: main history, PRs #1–#2, the
coordination/lineage line, the LoCoMo closeout line, the manuscript.

`stash@{0}` touches Nurman-track files but predates his campaign
(2026-08-21) and its author is not recorded by git stash → ownership
UNKNOWN → BLOCKED — CONTRIBUTOR APPROVAL REQUIRED (retained untouched).

## 3. Pull Request Summary

| PR | Author | Head | Recommendation | Relevance | Risk | Sem. changed |
|---|---|---|---|---|---|---|
| #1 Harden LoCoMo retrieval and GPT evaluation (draft) | anovruzov | `e20215e` | NEEDS HUMAN REVIEW | HIGH | MEDIUM | **YES** |
| #2 Tesseract coordination v1 | anovruzov | `8258d13` | SUPERSEDED | CRITICAL | LOW | no |
| #3 Leakage-free harness, per-agent routing, LM Studio (Nurman) | PNurmanM | `63aef96` | KEEP OPEN | CRITICAL | MEDIUM | **YES** |

No CI checks or reviews exist on any PR. All three are fast-forwards from
the main tip (merge-base = `8785d69` for each).

## 4. Detailed PR Analysis

### PR #1 — `agent/locomo-integrity-gpt` @ `e20215e` (+1,329/−814, 16 files, 3 commits, created 2026-08-02)

Implements: OpenAI Responses API client (`openai_client.py`),
`benchmarking.py` (lexical ranking + RRF fusion + evidence-coverage
metrics), a rewritten category-blind `demo/runner.py`, deterministic
local feature-hash embeddings replacing Ollama embeddings, gold-answer
removal from the profile path (signature-level), batched reranking, the
tesseract list-regex fix, and the **corrected LoCoMo category mapping**
(`1: multi_hop, 4: single_hop, 5: adversarial` — "these were previously
swapped"), with 13 targeted tests (`test_benchmark_integrity.py`).

EXPERIMENTAL SEMANTICS CHANGED: embeddings (network model → local hash),
provider (Ollama → OpenAI), prompts, ranking, judging, category labels,
adversarial category included. Zero committed result artifacts — README
claims are not backed by committed data. No seeds recorded (unseeded
`random.random()` affects only diagnostic sampling, not scores).

Verdict basis: it fixes real, confirmed integrity bugs, but overlaps
PR #3 in the six Nurman-owned files with 26 textual conflict hunks, and
the choice between its retrieval approach and Nurman's is an authorship
decision. **NEEDS HUMAN REVIEW** (owner decision on the retrieval track).
Would change on: Nurman adopting the category fix and leakage guards
into #3, or an explicit owner decision on provider/judge harmonization.

### PR #2 — `tesseract-coordination-v1` @ `8258d13` (+5,311/−0, 13 files, 3 commits, created 2026-08-16)

Purely additive coordination package (`contracts.py`, `core.py`,
`adapters.py`, `storage_adapter.py`, `experiment.py`, `fixture.py`),
2,800 lines of tests, `state.md`, and a 3-line `sqlite_storage.py`
serialization addition (`source_memory_ids` — load-bearing for lineage).
`8258d13` is an **ancestor of `13a1729`**: the session-analysis branch
contains every byte of this PR plus the benchmark, scale, figures, docs,
artifacts, and 12 more test files. **SUPERSEDED** — close in favor of a
PR from `claude/session-analysis-continuation-sm1a1t`, or merge as a
stepping stone first (it merges clean; risk LOW). Would change on: a
decision not to publish the fuller branch.

### PR #3 — `research/retrieval-campaign-2026-09` @ `63aef96` (+368,476/−169, 74 files, 3 commits, created 2026-09-06) — Nurman

Implements: LM Studio/Ollama backend shim (`llm_backend.py`),
`mega_search.py` (vector + BM25 + personalized-PageRank channels, RRF),
`kg_extractor.py` (LLM entity graphs, cached), pair nodes, per-agent
memory routing, retrieval-only and two-agent eval harnesses, 18 research
docs (H1–H9, N1–N5), 20 result/cache JSONs, removal of the hardcoded
OpenAI key and of gold-answer/gold-category leakage from the committed
run path, `.gitignore` + bytecode removal. The self-audit
(`docs/research/AUDIT.md`) is unusually candid about dead code and known
integrity issues.

Delta since previously audited head (`eed4873..63aef96`): the KG
experiment (+ cached graphs + negative result), the conversation-5 run
(`capstone_rest.json`), the KG∪regex ablation, and the final-tally
report section. ~20 code lines; no tests, no seeds, no added run-config
metadata.

Confirmed issues (details §4a below): category labels swapped (inherited
from main; every "single_hop" number is actually multi-hop and vice
versa), judge metadata hardcoded `"GPT-4o (OpenAI)"` while the report
says the lenient local Gemma judge produced the final tally, caches not
keyed on model/prompt, provider errors scored as wrong answers, cohort
744/1,540 (conversations 6–10 unrun; category 5 skipped), ~a dozen
referenced ablation outputs absent from the PR (machine-local paths).
The headline "72.2% vs 66.9% (744 q)" **recomputes exactly** from the
two committed capstone files against main's `demo/maximal.json`, but no
script in the PR performs that computation.

EXPERIMENTAL SEMANTICS CHANGED: retrieval modes, judge, dataset
subsetting, caching. **KEEP OPEN**; findings reported to the author; no
fixes applied (contributor boundary). Tests: none — the headline path is
untested by automation. Would change on: label-swap correction, artifact
metadata carrying real run config + judge identity, cohort completion.

### 4a. Previously flagged issues — verdicts

| Issue | Verdict |
|---|---|
| Category-dependent profile routing when profiles enabled | **CONFIRMED in PR #3** (`demo/runner.py:858` gates on dataset category; off by default) — fixed in PR #1 |
| Reversed single-hop/multi-hop labels | **CONFIRMED** in base + PR #3; fixed in PR #1. Independently verified from the dataset: category 1 has mean 3.13 evidence ids (97.9% multi-evidence), category 4 has 1.07 — category 1 is multi-hop |
| Retrieval cache identity | **CONFIRMED in PR #3** (embedding cache keyed by filename only; KG cache by message count only; reranker `ScoreCache` md5(query‖text), no model/prompt) |
| Judge identity vs metadata | **CONFIRMED in PR #3** (hardcoded literal; silent local-judge fallback; committed artifacts say GPT-4o while the report says Gemma). PR #1 records the real variable but has a dead misleading `llm_base_url` default |
| Provider-error accounting | **CONFIRMED both PRs**: errors become wrong answers; no error counters. (Committed PR #3 artifacts contain 0 error strings — nothing failed, but nothing would be accounted) |
| Incomplete cohorts | **CONFIRMED PR #3**: 744/1,540; conv 6–10 unrun; adversarial (29%) skipped; `capstone_full.json` is not full. The 282-question A/B files are cohort-complete |
| Missing ablation outputs | **CONFIRMED PR #3**: H1/H5/H7/H8/N1/N4/N5/H9/N3 outputs referenced but not committed; several under absolute paths on the author's machine |

## 5. PR Dependency and Conflict Graph

```
origin/main 8785d69
 ├── PR #1  e20215e ──┐ 26 conflict hunks in 6 retrieval files ┌── PR #3 63aef96 (Nurman)
 │                    └───────────── CONFLICT ─────────────────┘
 ├── PR #2  8258d13 ── ancestor of ── 13a1729 (session-analysis…, +17 commits)
 │                                        └─ sibling fork @1c8245c: 7dab416..37103e5 (local, unpushed)
 │                                                                   └─ locomo-six-fix-loop (15 local commits)
 └── (disjoint) origin/master 46af258 — unrelated history (SporeOS import), shares no root with main
```

- PR #2 ⟂ PR #1, PR #2 ⟂ PR #3 (no file overlap, no conflicts).
- PR #1 ∩ PR #3 = `demo/runner.py`, `answering.py`,
  `llm_profile_extractor.py`, `llm_profile_query.py`, `reranker.py`,
  `tesseract.py` — all retrieval-track. Both fix the same tesseract
  regex bug and the same credential differently.
- Audit branch ∩ PR #3 = one line of `demo/runner.py` (same credential
  fix); trivial conflict, resolves to either side.
- Justified integration order: (1) coordination line — merge a PR cut at
  `13a1729` (or newer, see §6), superseding #2; conflict-free with
  everything. (2) Retrieval track — human decision between #1 and #3
  (or #3 with #1's correctness fixes adopted); do not auto-merge.
  (3) Audit branch any time; regenerate `.pyc`-free state merges clean.

## 6. Missing or Unpushed Work

**The previously "missing" commits `7dab416..37103e5` are FOUND** — they
are the 5 unpushed local commits on `claude/mycelic-gate-0-recovery-tjl82x`
in `/Users/novruz/NeuralGraph` (replay attribution, `.env` ignore, claim
audit, the manuscript, count corrections). On top of them sit **15 more
local-only commits** on `locomo-six-fix-loop` (LoCoMo corpus freeze,
balanced eval sets, overnight candidates B/E measured-and-rejected, RCA,
paper figures, closeout) ending at `9125388`, plus **1 commit** on
`tesseract-operator-plugin-v1` (`899cb4b`). Total 21 unpushed commits;
none introduces a secret (§11); `9125388` passes 346 tests and both
paper `--check` pipelines.

Still local-only and uncommitted: the 4 dirty files (closeout addendum:
renderer list-continuation bugfix with a deliberate, documented HTML
digest move `05f00de6…→93b3fb3f…`), `demo/.cache/` (2 JSONs),
`capability_survival.pdf`. Fingerprints recorded and re-verified.

Unpushed and at-risk records: `stash@{0}` (see §2). Lost for good:
the prior cloud session's evidence bundle and its three original cleanup
commits (recreated as new commits, §10).

Not recovered/not present anywhere reachable: the ~dozen PR #3 ablation
outputs on Nurman's machine (§4a) — request from Nurman if needed.

## 7. Research Provenance

Three research lines, all rooted at `8785d69`:

1. **Coordination/lineage** (Anar): PR #2 → `1c8245c` → `13a1729`
   (= `claude/session-analysis-continuation-sm1a1t`): the 8-strategy ×
   9-intervention survival benchmark, scale sweep, 5 figures, pinned
   artifacts + SHA256SUMS, docs/, 277 tests. **Fully reproduced this
   session (§13).**
2. **Manuscript/closeout** (Anar, local only): `1c8245c` → `7dab416..37103e5`
   → 15 locomo commits → `9125388`: claim audit, manuscript with
   generated tables, LoCoMo corpus freeze + judged runs, closeout. 346
   tests + paper checks pass at tip.
3. **Retrieval campaign** (Nurman): PR #3 (§4).

`plan/post-six-round-completion` = `1c8245c` + a 710-line plan doc +
.env ignore; documentation, not implementation.
`claude/mycelic-three-agent-orchestration-af8bmr` and
`claude/new-session-e99pju` are small hygiene/caching branches off main.
"Mycelic" appears only in branch names (per CLAUDE.md). `origin/master`
is an unrelated-history import kept for the record.

## 8. Claim-to-Code/Test/Artifact Matrix

| Claim | Code | Test | Artifact | Commit | Scope | Status |
|---|---|---|---|---|---|---|
| 8×9 survival table (isolated 0.000 … oracle 0.889) | `coordination/benchmark.py` | `test_artifact_reproducibility.py`, `test_benchmark*.py` | `benchmark_seed20260813.json`, `benchmark_sweep30_seed20260813.json` | `13a1729` | 5-node fixture, 30 seeds, deterministic simulator | **VERIFIED + REPRODUCIBLE** (bitwise, this machine) |
| Survival/forgetting/bytes summary incl. lineage_aware 0.778 | same | same | same + `RESULTS.md` | `13a1729` | same | **VERIFIED + REPRODUCIBLE** — regenerated table matches handoff numbers exactly |
| 3-strategy reconstruction experiment | `coordination/experiment.py` | restart tests | `experiment_seed20260813.json` | `13a1729` | 4-holder reconstruction | **VERIFIED + REPRODUCIBLE** |
| Scale invariance | `coordination/scale.py` | `test_scale.py` | `scale_seed20260813.json`, fig4 | `13a1729` | small arity sweep | **VERIFIED + REPRODUCIBLE** (small-N only) |
| 5 figures regenerate from artifacts | `coordination/figures.py` | `test_figures.py` | 5 SVGs | `13a1729` | — | **VERIFIED** (`--check` passes; SHA256SUMS OK) |
| Manuscript tables/figures regenerate byte-identically | `tools/build_paper_{tables,figures}.py` | 346-test suite | `docs/paper/*` | `9125388` (local) | — | **VERIFIED locally; unpublished** |
| PR #3: 72.2% vs 66.9% over 744 q | `demo/runner.py` (PR #3) | none | `capstone_full.json` + `capstone_rest.json` vs main's `maximal.json` | `63aef96` | conv 1–5, lenient judge | **REPRODUCIBLE (aggregate) / CONTRADICTED (metadata)** — category labels swapped, judge field wrong, no tally script |
| PR #3 per-category rows | — | — | same | `63aef96` | — | **CONTRADICTED** (single_hop↔multi_hop swap) |
| PR #3 H1–H9/N1–N5 ablations | partial | none | mostly absent | `63aef96` | — | **PARTIALLY IMPLEMENTED / UNKNOWN** (outputs on author's machine) |
| PR #1 README improvement claims | code present | 13 tests | none committed | `e20215e` | — | **IMPLEMENTED BUT NOT VALIDATED** |
| Self-interrogation / adaptive topology / autonomous discovery / hallucination elimination | — | — | — | — | — | **PROPOSED ONLY** — not established by any existing experiment |
| 1K–10K population capability | — | — | — | — | — | **PROPOSED ONLY** (gates 3–6 unmet, §15) |

Known distinctions preserved: "bytes" = serialized transfer volume, not
resident-memory savings; the summary's forgetting column is
**unrecovered** silent forgetting after repair (initial forgetting is a
different quantity); the partition row does **not** show every
distributed strategy failing — full_replication survives partition
(regenerated matrix confirms: partition column = 1.00 for
full_replication/centralized, 0.00 for the repair strategies); artifact
introduction commits are not proven generator commits (generator
metadata absent from the JSONs → generator commit = UNKNOWN, though
regeneration at `13a1729` is bitwise-identical); the real-adapter
"independent support available for unknown provenance" discrepancy is
reported, not repaired.

## 9. Verified vs Proposed

**Verified this session:** everything marked VERIFIED in §8; test counts
in §12; the 21 unpushed commits' content and cleanliness; PR #3's
aggregate tally arithmetic.
**Implemented but not validated:** PR #1's retrieval changes; PR #3's
mega/KG/pair-node machinery (no tests, partial cohorts).
**Proposed only:** the Agentic Web benchmark (contract in
`evaluation/agentic_web/README.md`); all capability claims about
adaptive topology, echo discrimination at scale, churn survival.

## 10. Dead/Duplicate/Useless Files

Executed (audit branch; full detail in CLEANUP_LOG.md):
- DELETE: 22 tracked `.pyc` (`999823f`), narrow `.gitignore` added.
- Security fix: hardcoded key → env lookup (`9591a18`).
- Addition: `evaluation/agentic_web/README.md` (`f48da4a`).

Classified KEEP/UNKNOWN, deliberately untouched: `demo/` result JSONs
(experiment records), `evaluation/locomo/locomo10.json` (dataset; its
"AIza" hit is a false positive inside base64), `archive/` +
`.tesseract-handoff/` on local branches (records), stash, `master`
branch, stale worktree entry, all dirty/untracked local files. Zero
uncertain files were removed. No repository-history size savings are
claimed.

## 11. Security Findings

Four distinct live-format OpenAI project keys (values never printed;
full scan of all 4,015 blobs, every ref tip, all 3 stash trees, working
tree):

| # | Where | Reachable from | At a public tip now? |
|---|---|---|---|
| 1 | history of `demo/runner.py` + `demo/run_benchmark.py` (2025-12-16) | every ref | **YES — `origin/master`** (`run_benchmark.py`) |
| 2 | `demo/runner.py:72` since `791dbe8` (2025-12-19) | every ref | **YES — `origin/main` + 14 other tips incl. `pr/2`**; fixed on audit branch (`9591a18`), on PR #1, and on PR #3 |
| 3 | `evaluation/locomo10_parallel_benchmark.py` (2025-12-07, deleted 2025-12-13) | `origin/master` history only | no |
| 4 | untracked `.env` (never committed, gitignored) | — | no |

**ROTATION OF ALL FOUR KEYS: REQUIRED, UNRESOLVED.** They are on a
public remote; removal/rewrite never revokes. History rewriting was not
performed (out of authorization; would touch every ref). The 21 unpushed
commits introduce no secret; 20 inherit key #2 in-tree (already public
— pushing discloses nothing new). False positives documented in the
scan report (guard tests, placeholders, base64 slugs).

## 12. Before/After Test Results

Environment: Python 3.11.14 (audit venv; system-site pytest 9.1.1,
pytest-asyncio 1.4.0, numpy 2.4.6, aiohttp 3.14.3), macOS arm64. Command:
`python -m pytest` from repo root. Subtest counts are not separately
reported by pytest 9.1.1 even with pytest-subtests force-loaded; prior
counts (2,960 @`13a1729`, 3,382 @`37103e5`) were not re-measured, but a
failing unittest `subTest` fails its parent, so green parents bound them.

| Commit | Result | Classification |
|---|---|---|
| `8785d69` (main, before cleanup) | collection INTERRUPTED: `demo/test_instruments_fix.py` TypeError (no pytest.ini at main → demo/ collected) | PRE-EXISTING |
| `8785d69` targeted `NeuralGraph/tests` | 1 skipped, 7 errors (setup errors) | PRE-EXISTING |
| audit branch after all 3 cleanup commits | identical collection error; identical targeted 1 skipped/7 errors | PRE-EXISTING (no CLEANUP-INTRODUCED failures) |
| `13a1729` (lineage snapshot) | **277 passed, 1 skipped** (7.5s) | matches prior audit |
| `37103e5` (manuscript tip, local) | **266 passed, 1 skipped** (8.0s) | matches CLAUDE.md |
| `9125388` (locomo tip, local) | **346 passed, 1 skipped** (8.4s) | matches dirty CLOSEOUT addendum |

Prior-audit NumPy 2.5.2 import failure: not reproduced here (2.4.6 in
use; 2.5.2 not installed) — recorded as environment-specific, UNKNOWN on
this machine.

## 13. Reproduction Instructions and Results

See REPRODUCIBILITY.md for the tested procedure. Results at `13a1729`,
this machine, audit venv:

- `shasum -a 256 -c SHA256SUMS`: **12/12 OK** (9 coordination + 3 evaluation).
- Regeneration to fresh temp dir (never over historical artifacts):
  `experiment`, `benchmark`, `scale`, `benchmark --sweep 30` → all four
  JSONs **BITWISE-MATCH** their pinned counterparts (1s each).
- `figures --check`: 5/5 match.
- `PYTHONHASHSEED` variation: byte-identical output (hash-seed
  independent) → **bitwise deterministic reproduction**, not merely
  tolerance-level.
- At `9125388`: 4/4 artifact sums OK, `tools.build_paper_tables --check`
  and `tools.build_paper_figures --check` PASS.

Not reproduced: PR #3's LLM-driven numbers (needs LM Studio + models +
an OpenAI judge key; live model calls are not exactly reproducible;
replay records absent for most ablations) — labeled honestly as
replay-unavailable. No paid APIs were consumed this session.

## 14. Recommended Repository Structure

Adopt the `13a1729` layout as the target (it already exists there):
`NeuralGraph/` core (retrieval track = Nurman's), 
`NeuralGraph/coordination/` (+ pinned `artifacts/` + SHA256SUMS),
`NeuralGraph/tests/`, `evaluation/` (diagnosis harnesses + 
`agentic_web/` boundary), `docs/` (+ `docs/research/` for campaign 
reports), `demo/` (runner + results), `tools/` (paper pipeline, from the
locomo line). Add `.github/workflows/` CI running `python -m pytest` and
`figures --check` (currently nothing enforces green). Keep `master`
frozen. Do not blanket-ignore result JSONs; keep narrow rules.

## 15. Agentic Web Readiness

Boundary reserved at `evaluation/agentic_web/` (contract + gates in its
README). Gates: 1 deterministic small-world correctness — **PROPOSED**
(the 5-node coordination fixture is adjacent evidence, not the gate);
2 provenance/evaluator-isolation tests — **PROPOSED**; 3 N=100 smoke —
**PROPOSED**; 4 N=1,000 — **PROPOSED**; 5 N=10,000+ — **PROPOSED**;
6 independent reproduction — **PROPOSED**. The area is ready for
**implementation** to begin against the contract; it is not ready for
execution, and no population-scale claim is currently supportable.

## 16. Remaining Blockers

**P0**
1. Rotate all four OpenAI keys (human action; unresolved).
2. Publish the 21 local-only commits (single-machine risk to the
   manuscript line) — requires GitHub auth.
3. Retrieval-track ownership decision (#1 vs #3 semantics) — Anar +
   Nurman; blocks any merge of either.

**P1**
4. PR #3 label swap + judge-metadata correction (Nurman; findings
   delivered) — blocks citing any per-category number.
5. Complete PR #3 cohorts (conv 6–10, adversarial) or scope claims down.
6. Merge path for the coordination line (supersede #2 with a PR at
   `13a1729`+).
7. CI (pytest + figures --check + secret scanning).

**P2**
8. Commit or formally archive the dirty closeout addendum (renderer fix).
9. Recover/commit PR #3 ablation outputs from Nurman's machine, or mark
   those hypotheses unevidenced.
10. Resolve `stash@{0}` ownership; keep until then.
11. Decide `origin/master`'s fate (frozen historical import).
