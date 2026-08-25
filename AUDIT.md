# Claim-to-evidence audit

Executed 2026-08-25 against branch `claude/mycelic-gate-0-recovery-tjl82x`
at commit `7dab416`, Python 3.11.14. Every status below was checked by
running code or reading a committed file, not inferred from prose.

**Status vocabulary**

| Status | Meaning |
|---|---|
| VERIFIED | Committed code + passing test + pinned artifact |
| PARTIAL | Real, but narrower than stated, or missing one leg of evidence |
| PROPOSED | Design intent. No implementation. Honestly labelled as such |
| UNSUPPORTED | Asserted somewhere, with no code, test, or artifact |
| CONTRADICTED | Asserted, and the measured evidence says otherwise |

---

## 0. Which manuscript is authoritative

**There is no manuscript file in this repository.** `git ls-files` returns
118 tracked files and zero `.tex`, `.pdf`, `.docx`, or `.bib`.

Three documents have been mistaken for the manuscript:

| Document | What it actually is | Disposition |
|---|---|---|
| `docs/PAPER.md` | Claims-to-evidence map and submission checklist. Not prose. | **Authoritative plan.** Keep. |
| The "Mycelic" preprint (pasted into session, never committed) | Architecture and experimental-design preprint for a much larger system | **Not authoritative.** Its own closing note says it is a design preprint. |
| `NeuralGraph_System_Architecture.md` | Design doc for the local memory engine (Nurman's track) | Keep. Accurate and scoped; does not discuss coordination at all. |

The authoritative paper is the one `docs/PAPER.md` scopes:

> **Capability Survival and Collective Forgetting in Distributed Agent Memory**

The corrected manuscript is now written at `docs/paper/capability_survival.md`.

---

## 1. Supported contributions — the defensible seven

| # | Claim | Code | Test | Artifact | Status |
|---|---|---|---|---|---|
| C1 | Distributed reconstruction: the collective answers what no node holds | `coordination/core.py`, `storage_adapter.py` | `test_no_single_real_store_holds_the_full_answer` | `experiment_seed20260813.json` | **VERIFIED** |
| C2 | Capability lost while confidence stays high (collective forgetting) | `coordination/benchmark.py` | `test_confidence_exceeds_coverage_when_the_capability_is_lost` | `benchmark_sweep30…json` | **VERIFIED** |
| C3 | Replica count does not buy survival | `benchmark.py:_full_replication` | `test_full_replication_does_not_survive_a_root_failure_either` | sweep: `full_replication::lineage_root_failure` = 0.00 (0/30) | **VERIFIED** |
| C4 | Lineage-aware repair survives at half the storage | `benchmark.py:_lineage_aware_repair` | `test_lineage_awareness_beats_full_replication_on_both_axes` | 0.778 @ 5,448 B vs 0.667 @ 9,877 B | **VERIFIED** |
| C5 | Min-cut predicts survival | `benchmark.py:_worst_single_domain`, `structural_diversity` | `test_worst_single_domain_failure_is_survived_only_by_min_cut_two` | only `oracle_min_cut` = 1.00 (30/30) | **VERIFIED** |
| C6 | Holds at scale, K ∈ {2,3,5,8}, H ∈ {2,3} | `coordination/scale.py` | `test_survival_is_invariant_across_scale_in_every_cell` | `scale_seed…json`: `invariant_across_scale` true in all 12 cells | **VERIFIED** |
| C7 | Generalizes beyond pairs, arity 2–8 | `scale.py` | `test_reconstruction_succeeds_for_every_capability_arity` | `cost_by_arity`, all baselines correct | **VERIFIED** |
| C8 | Holds on real SQLite, lineage derived not declared | `storage_adapter.py`, `sqlite_storage.py` | `test_min_cut_of_two_is_reproduced_against_real_sqlite`, `test_lineage_roots_are_derived_from_storage_not_declared` | 4 real SQLite files in-test | **VERIFIED** |
| C9 | Privacy: no node holds the answer, denials payload-free | `contracts.py` | `test_denied_responses_never_carry_payload`, `test_redaction_does_not_leak_content_or_trace_metadata` | — | **VERIFIED** |

### Counter-results, verified and to be preserved

| # | Claim | Evidence | Status |
|---|---|---|---|
| N1 | No distributed strategy survives a partition isolating one holder | sweep `network_partition`: 0.00 for all 6 distributed; only `centralized`/`full_replication` = 1.00 | **VERIFIED** |
| N2 | Lineage-aware repair does not dominate | `scale`: `lineage_aware::node_failure::I1` = 0.00 vs `source_count` = 1.00 | **VERIFIED (see §2)** |
| N3 | Failure domains are absent by default, never synthesized | `test_absent_domains_stay_absent`, `test_domain_key_is_opt_in` | **VERIFIED** |
| N4 | Partial provenance is pessimistic, never optimistic | `test_partial_provenance_is_pessimistic_never_optimistic` | **VERIFIED** |
| N5 | `random_path_diversification` matches its closed-form 2/3 | sweep `lineage_root_failure` = 0.667 (20/30) | **VERIFIED** |

---

## 2. Imprecision found in existing docs (must be corrected)

**N2 is stated too broadly.** `CLAUDE.md` and `state.md` say lineage-aware
repair "loses to `source_count` under plain node failure (0.00 vs 1.00) …
at every K and H."

Measured: it loses in **exactly 1 of 6** policy×intervention cells —
`lineage_aware::node_failure::I1`. At **I2** it ties at 1.00. The controlling
variable is the number of **independent roots (I)**, not K or H.

The honest statement: *with a single independent root there is no independent
holder to find, so refusing every correlated holder discards a replica that
would have served.* Corrected in `state.md`, `CLAUDE.md`, and the manuscript.

This is a narrowing, not a retraction. N2 survives and is still reported
against interest.

---

## 3. Specifically audited constructs

Grepped across all 118 tracked files.

| Construct | Preprint § | Files in code | Test | Artifact | Status |
|---|---|---|---|---|---|
| **Mycelic Fabric** | §4.3 | **0** | none | none | **PROPOSED** — branch names only |
| **NATS / JetStream** | §4.2 | **0** | none | none | **PROPOSED** — everything runs in-process |
| **KnowledgeArtifact** (Eq 3) | §2.1 | **0** | none | none | **PROPOSED** |
| **Four-way learned router** `{r1hop, rmulti, rtemp, ropen}` | Eq 8, 14 | **0** | none | none | **PROPOSED** |
| **Hyperedges / cross-cutting scopes** | §3 | **0** | none | none | **PROPOSED** |
| **Hyperbolic embeddings** `h_a ∈ H^p` | §5.1 | **0** | none | none | **PROPOSED** |
| **Active lineage diversification** (Eq 17) | §6.2 | **0** | none | none | **PROPOSED** — preprint itself says "proposed" |
| **8 architecture families** (C-RAW … MYC-LEARNED) | §7.1 | **0** | none | none | **UNSUPPORTED** — see below |
| **Min-cut κ(k)** (Eq 15) | §6 | `benchmark.py`, `scale.py` | 4 tests | sweep + scale | **VERIFIED** |
| **Failure domains** | §6 | `storage_adapter.py` (13 files) | `test_failure_domains_real_storage.py`, 8 tests | real SQLite | **VERIFIED** |
| **Independent vs copied support** (Eq 16) | §6.1 | `_independent_support` | `test_verification_distinguishes_correlated_from_independent_support` | — | **VERIFIED** |

### The "eight" collision — the most dangerous item in the audit

The preprint's §7.1 lists **8 architecture families**: C-RAW, C-GRAPH,
R-CENTRAL, FED, FLAT, HIER, MYC-FIXED, MYC-LEARNED. **Zero are implemented.**

The repository has **8 placement/repair strategies**: `isolated_local`,
`centralized`, `full_replication`, `fixed_distributed_replication`,
`source_count_repair`, `random_path_diversification`, `lineage_aware_repair`,
`oracle_min_cut`.

Both are "eight things," and a reader skimming would take the filled
strategy table as the empty architecture table. **They are different axes**:
the strategies vary *where records are placed and how repair chooses
candidates*; the families vary *the memory architecture itself*. The
manuscript must never present the strategy table as an architecture
comparison.

### `tesseract.py` — naming trap

`NeuralGraph/tesseract.py` (2,212 lines) is **intra-node 4D retrieval
fusion**. It is **not** the distributed four-way router. `docs/ARCHITECTURE.md`
already records this correctly. The manuscript must not cite it as the router.

---

## 4. Tables and figures

| Object | Source | Status |
|---|---|---|
| Preprint Table 1 (8 architectures × 7 metrics) | none | **UNSUPPORTED — 100% empty, ~56 cells** |
| Preprint Table 2 (hierarchy board) | none | **UNSUPPORTED — empty** |
| Preprint Table 3 (scenario coverage) | none | **PARTIAL — checkmarks present, headline column empty** |
| Preprint Table 4 (8 ablations) | none | **UNSUPPORTED — empty** |
| Preprint Fig 1, 2 (fabric, system boundary) | schematic | **PROPOSED — depicts unimplemented components** |
| Manuscript Tables 1–5 | `tools/build_paper_tables.py` from pinned artifacts | **VERIFIED — generated, not typed** |

No invented values were found in any *committed* file. All emptiness and
overstatement is in the uncommitted preprint.

**Stale numbers found in a committed file:** see §5.

---

## 5. `QUERY_ROUTING_IMPROVEMENTS.md` — CONTRADICTED

The only committed file carrying numbers that the measured evidence refutes.
`state.md` already flagged line 120 as unsupported; the audit finds the
problem is larger.

| Line | Claim | Measured (`evaluation/artifacts/accuracy_diagnosis.json`) | Status |
|---|---|---|---|
| 120 | single_hop "Before: 50% (39/78)" | **52.1%**, n = **282** | **CONTRADICTED** (n off by 3.6×) |
| 120 | "with 77% recall@50" | no committed artifact | **UNSUPPORTED** |
| 121 | "**Expected**: 70-80%" | never measured | **UNSUPPORTED** (projection in a results-shaped table) |
| 126 | Temporal "78.4%" | **64.5%** | **CONTRADICTED** |
| 127 | Multi_hop "81.5%" | **74.1%** | **CONTRADICTED** |
| 128 | Open_domain "72%" | **52.1%** | **CONTRADICTED** |
| 5 | "**2,000+ patterns**" | its own §Pattern Statistics sums to **5,100+** | **CONTRADICTED — internally inconsistent** |
| 134-136 | 800+ / 2,500+ / 1,800+ patterns | 3,599 string literals total across all four routing modules — an *upper bound* well below 5,100 | **UNSUPPORTED** |
| 141 | references `temporal_utils.py` | exists | ok |

Disposition: **ARCHIVE**, not delete. It records genuine design intent for the
marker system and has historical value. Superseded by `docs/ACCURACY.md`,
which is measured.

---

## 6. Citation integrity

Checked against the pasted preprint's bibliography (23 entries).

| Check | Result |
|---|---|
| Every body citation exists in the bibliography | **PASS** — all of [3]–[23] resolve |
| Every bibliography entry is cited in the body | **FAIL** — **[1] HiddenBench** and **[2] Silo-Bench** are never cited |
| arXiv ID dates internally consistent with an Aug 2026 preprint | PASS — 25xx/26xx series are plausible for the stated dates |

Not applicable to the corrected manuscript, which carries its own short
bibliography.

---

## 7. Executed verification

```
$ /opt/homebrew/bin/python3.11 --version
Python 3.11.14

$ /opt/homebrew/bin/python3.11 -m pytest -q
266 passed, 1 skipped, 3382 subtests passed in 13.39s

$ cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS
benchmark_seed20260813.json: OK
benchmark_sweep30_seed20260813.json: OK
experiment_seed20260813.json: OK
scale_seed20260813.json: OK

$ python3.11 -m tools.build_paper_tables --check
all pinned artifact digests match
```

**Artifact regeneration** — all four regenerate byte-identically from their
canonical commands under Python 3.11:

| Artifact | sha256 (first 16) | Regenerated |
|---|---|---|
| `experiment_seed20260813.json` | `f712e12d85f8b63a` | MATCH |
| `benchmark_seed20260813.json` | `2d8564e7cd717a23` | MATCH |
| `benchmark_sweep30_seed20260813.json` | `3e45ebc05d653507` | MATCH |
| `scale_seed20260813.json` | `61be1f24d6ef4808` | MATCH |

### Test-count discrepancy, resolved

`CLAUDE.md` and `state.md` both say **"185 passed, 1 skipped"**. Actual: **266
passed, 1 skipped**. Not a changed result — tests were added since those docs
were written (`test_diagnose_accuracy`, `test_retrieval_ceiling`,
`test_replay_generation`, and 13 added this session). Corrected in both files.

### Interpreter trap

`python3` on this machine resolves to `/usr/bin/python3` (**3.9.6**), which
**fails to collect the entire suite** — `TypeError: unsupported operand type(s)`
on `X | Y` annotations in all 13 test files. `pytest` is not on `PATH` at all.
Every canonical command in `CLAUDE.md`, `state.md`, and `docs/REPRODUCE.md`
used bare `python3`/`pytest`. **Corrected to `python3.11 -m …` throughout.**

---

## 8. Security

| Item | Finding |
|---|---|
| `.env` (holds `OPENAI_API_KEY`) | **Never committed to any branch.** `git log --all -- .env` is empty. Now ignored via `.gitignore`. **No rotation or history rewrite required.** |
| `.gitignore` gap | `env/` (a virtualenv dir) did **not** match `.env`. One `git add -A` would have committed the key. Fixed. |
| Tracked secrets elsewhere | none found |
| Tracked `__pycache__` / `.pyc` / editor junk | **none** — already clean |
| Duplicate PDFs / base64 payloads | **none exist** |
| Portfolio / public-site references | **none in repo.** Only `https://github.com/anovruzov` (README:183) and `https://ollama.com/` (README:132), both valid |

The cleanup brief anticipated a messier repository than this one is. Several
categories in the DELETE list have no members.

---

## 9. Untouched by this audit, per instruction

- `stash@{0}` — "pre-replay 2026-08-21", 8 files, 454 insertions. Contains
  retrieval-track work (`answering.py`, `reranker.py`, `prompts.py`,
  `service.py`, `tesseract.py`, `demo/runner.py`). **Read metadata only.
  Not applied, not dropped.**
- All remote branches. Nothing pushed, nothing deleted.
- Git history. No rewrite.
- Retrieval-track files, per `CLAUDE.md` rule 3 (Nurman's ownership).
