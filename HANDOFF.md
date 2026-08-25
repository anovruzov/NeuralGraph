# Handoff — NeuralGraph / coordination track

Self-contained cold-start bundle. Written 2026-08-25. Assumes you have the
repository and nothing else — no prior conversation.

Repo: `/Users/novruz/NeuralGraph`
Branch: `claude/mycelic-gate-0-recovery-tjl82x`
HEAD: `37103e5` · 5 commits ahead of origin · **nothing pushed**

---

## 1. Read these first, in this order

Do not grep the source to reconstruct facts these already state. They are kept
accurate and are far cheaper than re-deriving from ~5,000 lines.

| Need | File |
|---|---|
| **What every claim rests on, and what it does not** | `AUDIT.md` |
| Executed tests, digests, regeneration | `VERIFICATION.md` |
| KEEP / REWRITE / ARCHIVE / DELETE decisions | `CLEANUP_PLAN.md` |
| The paper | `docs/paper/capability_survival.md` |
| Tables (generated, never hand-typed) | `docs/paper/tables.md` |
| Design, invariants, why they hold | `docs/ARCHITECTURE.md` |
| What the numbers say | `docs/RESULTS.md` |
| How to regenerate every number | `docs/REPRODUCE.md` |
| Claims → evidence → test | `docs/PAPER.md` |
| Why retrieval accuracy is low | `docs/ACCURACY.md` |
| Status, gates, open decisions | `state.md` |
| Rules, naming traps, pinned results | `CLAUDE.md` |

---

## 2. Environment — this bites everyone

**Use Python 3.11 explicitly.** On this machine `python3` is `/usr/bin/python3`
(3.9.6) and **cannot collect the test suite at all** — it dies with
`TypeError: unsupported operand type(s)` on `X | Y` annotations in all 13 test
files. Bare `pytest` is **not on PATH**.

```bash
/opt/homebrew/bin/python3.11 --version          # 3.11.14
/opt/homebrew/bin/python3.11 -m pytest -q       # 266 passed, 1 skipped, 3382 subtests
```

Canonical commands (all from repo root, no venv activation step):

```bash
pip install -r requirements.txt
python3.11 -m pytest
python3.11 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3.11 -m NeuralGraph.coordination.benchmark --output benchmark.json
python3.11 -m NeuralGraph.coordination.experiment --output experiment.json
python3.11 -m NeuralGraph.coordination.scale --format markdown
python3.11 -m tools.build_paper_tables --check
python3.11 -m tools.build_paper_tables --output docs/paper/tables.md

cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS
# macOS has no sha256sum; use shasum -a 256
```

---

## 3. Naming traps — get these wrong and you lose an hour

- `NeuralGraph/coordination/` is the cross-node coordinator.
- `NeuralGraph/tesseract.py` (2,212 lines) is **intra-node retrieval fusion**.
  **Not** the coordinator, **not** a distributed router. Unrelated. If a task
  says "Tesseract", establish which one is meant.
- **"Mycelic" appears in no committed file.** Branch names only.
- **NATS / JetStream are not implemented.** Design intent. Everything is
  in-process. There is no transport layer and no wall-clock latency anywhere.
- Commit `b5b9c3e271` **does not exist**. Do not look for it.
- **The two "eights" are different axes.** The repo has 8 *placement/repair
  strategies*. An older architecture writeup has 8 *architecture families*
  (C-RAW, C-GRAPH, R-CENTRAL, FED, FLAT, HIER, MYC-FIXED, MYC-LEARNED) of
  which **zero are implemented**. Never present the strategy table as an
  architecture comparison.

---

## 4. Rules that are not negotiable

1. **Never refresh a pinned artifact to get a test green.** Diff it, find out
   why it moved, re-pin deliberately with the reason in the commit message.
2. **Never skip, disable, xfail or quarantine a test** to get green.
3. **Do not touch the retrieval track** without Anar's say-so: `answering.py`,
   `llm_profile_extractor.py`, `prompts.py`, `reranker.py`, `service.py`,
   `tesseract.py`, `demo/runner.py` belong to Nurman.
4. **Never synthesize a failure domain.** Domains are read from recorded root
   metadata or reported absent. Inferring one from storage identity, node id,
   file path, or value equality makes every domain metric fiction.
5. **Coordination stays domain-neutral.** `contracts.py` and `core.py` import
   no NeuralGraph storage types. Only `storage_adapter.py` bridges.
6. **Lineage direction is load-bearing.** Derivation parents are the *targets*
   of outgoing HIERARCHY edges plus `source_memory_ids`. `parent_id` and
   `get_edges_to` point the wrong way.
7. **Repair policies see exported contracts only**, never `Placement`
   internals. A policy that reads private state is an oracle, not a policy.

### Git safety
- Preserve unrelated dirty work. **Do not touch `stash@{0}`** ("pre-replay
  2026-08-21", 8 files, retrieval-track work).
- No `git reset --hard`, `git clean -fd`, blanket restore/checkout, history
  rewriting, force-push, or destructive worktree deletion.
- **Do not push.**

---

## 5. Results that must not silently change

If any of these moves, a claim about the world has changed — stop and say so.

- Replica count survives a lineage-root failure: **0.00** (3 replicas, and
  8-record full replication)
- `lineage_aware_repair`: **0.778** · `oracle_min_cut`: **0.889**
- Only min-cut-2 survives `worst_single_domain_failure`
- Every scale verdict invariant across K ∈ {2,3,5,8}, H ∈ {2,3}
- No distributed strategy survives `network_partition` (0.00) — against interest
- `lineage_aware` loses to `source_count` in **exactly one of six cells**:
  `node_failure` at **I=1** (0.00 vs 1.00). At I=2 they tie at 1.00. The
  controlling variable is independent roots, not K or H — against interest

Full headline (30 seeds, 8 strategies × 9 interventions = 72 cells):

| strategy | survival | silent forgetting | mean bytes |
|---|---|---|---|
| `isolated_local` | 0.000 | 0.000 | 1,371 |
| `centralized` | 0.111 | 0.667 | 3,020 |
| `fixed_distributed_replication` | 0.556 | 0.444 | 4,943 |
| `source_count_repair` | 0.556 | 0.444 | 4,941 |
| `full_replication` | 0.667 | 0.333 | 9,877 |
| `random_path_diversification` | 0.704 | 0.296 | 5,327 |
| `lineage_aware_repair` | **0.778** | 0.222 | 5,448 |
| `oracle_min_cut` | **0.889** | 0.111 | 6,461 |

---

## 6. What was done in this session

Five commits, `7dab416..37103e5`, 18 files, +1,522/−31. **Nothing pushed.**

| Commit | What |
|---|---|
| `7dab416` | Replay harness: split wrong answers into generation vs retrieval failure via `evidence_lenient`; 13 tests added, mutation-verified |
| `d981878` | **Security.** `.gitignore` had `env/` (a virtualenv dir) which does not match `.env`. `.env` holds `OPENAI_API_KEY`. Verified `git log --all -- .env` is empty — **never committed, no rotation needed** |
| `d05f10c` | `AUDIT.md`, `CLEANUP_PLAN.md`, `VERIFICATION.md` — claim-to-evidence audit |
| `4656aed` | Manuscript + `tools/build_paper_tables.py` |
| `37103e5` | Doc corrections; archived one contradicted file |

### Three defects found and fixed
1. **Test count** documented as 185; actually **266**. Tests were added since —
   nothing regressed.
2. **Every canonical command was unrunnable** (bare `python3` = 3.9). Fixed to
   `python3.11 -m …` throughout.
3. **N2 overstated.** Narrowed from "under plain node failure at every K and H"
   to the one cell it actually holds in. See `AUDIT.md` §2.

### Archived, not deleted
`QUERY_ROUTING_IMPROVEMENTS.md` → `archive/`. Four accuracy baselines are
contradicted by `evaluation/artifacts/accuracy_diagnosis.json` (single-hop 50%
vs **52.1%** at n=**282** not 78; temporal 78.4% vs **64.5%**; multi-hop 81.5%
vs **74.1%**; open-domain 72% vs **52.1%**), "Expected: 70-80%" is a projection
formatted as a result, and pattern counts contradict themselves. Header on the
file says so.

**Nothing was deleted.** No history rewrite. No credential rotation.

---

## 7. Verified state as of `37103e5`

```
Python 3.11.14 · 266 passed, 1 skipped, 3382 subtests
7/7 pinned artifacts verify against SHA256SUMS
4/4 coordination artifacts regenerate byte-identically
docs/paper/tables.md regenerates identically
```

| Artifact | sha256 (first 16) |
|---|---|
| `experiment_seed20260813.json` | `f712e12d85f8b63a` |
| `benchmark_seed20260813.json` | `2d8564e7cd717a23` |
| `benchmark_sweep30_seed20260813.json` | `3e45ebc05d653507` |
| `scale_seed20260813.json` | `61be1f24d6ef4808` |

Working tree clean except untracked `demo/.cache/` (generated embeddings).

---

## 8. VERIFIED vs PROPOSED — the line not to cross

**VERIFIED** (code + test + pinned artifact): lineage-aware reconstruction;
failure-domain min-cut; 8×9 strategy/intervention matrix; 30-seed survival;
real-SQLite reproduction with lineage *derived* not declared; scale and arity
generalization; the negative results.

**PROPOSED — zero files, zero tests, zero artifacts.** Do not describe any of
these as built: Mycelic Fabric · NATS/JetStream · KnowledgeArtifact · four-way
learned router · hyperedges / cross-cutting scopes · hyperbolic embeddings ·
active lineage diversification · all 8 architecture families.

---

## 9. Open items

1. **Figures 1 and 3.** Not drawn. `docs/PAPER.md` calls Figure 3 (survival vs
   storage Pareto) "the one that carries the paper"; Figure 1 (the A/B/C/D
   fixture — why C is not redundancy) is what makes the rest legible. Data for
   both is in `docs/paper/tables.md` and the pinned artifacts.
2. **Authorship / ownership** unresolved — on the `docs/PAPER.md`
   pre-submission checklist, flagged open in `state.md`.
3. **Retrieval accuracy: diagnosed, not fixed.** Single-hop 52.1%, *worse* than
   multi-hop 74.1%. Of 513 wrong answers: 327 generation, 139 recoverable
   retrieval misses, 47 **unanswerable** (gold appears nowhere in the source),
   so the hard ceiling is **96.9%**, not 100%. Fixes land in Nurman's files —
   see rule 3. Full analysis in `docs/ACCURACY.md`.
4. **Deadline.** `docs/PAPER.md` targets NeurIPS Agentic Web: lock **Aug 26**,
   submit **Aug 29 AoE**.

---

## 10. Ownership

- **Nurman** — NeuralGraph: memory formation, graph dynamics, retrieval.
- **Anar** — coordination, lineage semantics, failure experiments, paper.
- Interface: `NeuralGraph/coordination/contracts.py`. Do not widen it casually.
