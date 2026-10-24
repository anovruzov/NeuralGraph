# Frontier research: shared context (read first; every fact here has a source)

Folder: `research/emergence_two_track_20261024/` on branch `claude/mycelic-gate-0-recovery-tjl82x` of
`anovruzov/NeuralGraph`. Code of the e2e benchmark: the deployment worktree `$EMERGENCE_RULE` (read-only for
research agents). Design doc of the benchmark: `$EMERGENCE_RULE/docs/collective/E2E-INGEST.md` (§1 path,
§3.5 questions, §4 Tesseract). Architecture figure: `figures/benchmark_arch.png`.

## The task, precisely

EMERGENCE end-to-end: a synthetic organisation (generator `research/e2e_ingest/emergence_world.py`) of users in sites.
Planted **patterns**: a chain of facets about one (entity, chain predicate) key, spread across users and sites over
time. **Discovery** = a supported conclusion in the real fabric (Mycelic service over NATS JetStream) whose key and
snapshot week match the pattern's key and label window. Score = found / gold-discoverable, pooled over dev seeds 0–5.
Decoys (5 types, 50 per world) are look-alikes; accepting them is the false-discovery cost.

Pipeline (E2E-INGEST.md §1): sites ingest own records → weekly **k-suppressed cells** (counts, roots, reporters;
values < k = 3 shown as `<k`) cross the Boundary to HQ → HQ detectors → candidates → one question per candidate →
TesseractCoordinator routes to m = 3 sites → each site retrieves over its own NeuralGraph and a lexical judge returns
a bucketed verdict → HQ gate (supported / hypothesis) → bridge publishes events → fabric applies → scorer.
**Raw records and graph nodes never cross the Boundary** (privacy invariant). Logical site ids never authorize.

## Numbers (dev seeds 0–5, 240 gold patterns = 137 common + 103 rare; world size 200 → 309 users, 7 sites)

| Config | Status | Recall | Rare | Decoys (bar 118) | Questions | Background-supported |
|---|---|---|---|---|---|---|
| baseline (frozen R0) | **OFFICIAL**, checks green, `raw/official_ce0e357/` | 93/240 = 0.3875 | 5/103 | 118 | 341 | 3 |
| v2 h1n+nd+N1 | DIAGNOSTIC | 151/240 = 0.629 | 17 | 174 | 518 | 22 |
| v4 rule_snapshots+nd+N1+N2 | DIAGNOSTIC (best) | 163/240 = 0.679 | 27 | 181 | 3,181 | 96 |

"~63%" in older notes = v2/v3, a diagnostic, not a baseline.

## Where the evidence is lost (PLATEAU_DIAGNOSIS.md, final; scripts tools/plateau/)

- Detection loses nothing: 0 records lost, every gold pattern produces a detector candidate.
- v2: 81 of 86 missed rare patterns had a rule-layer candidate that was **never asked**.
- v4 (asks rule candidates once, at the first fitting week): of 76 rare misses, **37 asked before their evidence could
  pass the gate**, **34 can never pass at any week**, 2 failed at a feasible week. Verdict/gate are not the bottleneck:
  an offline evidence check predicts the system's supported/not on 98.6% of 3,181 questions.
- A supported note only counts if its snapshot week is inside the pattern's label window; early support is scored as
  background (HQ cannot see the window).
- Site NeuralGraphs: one MESSAGE node per own record, hash embedding, **no edges** (`research/routing_spike/
  site_process.py:249-259`, `save_nodes_batch` only); retrieval requests every node (`limit=self.nodes`). Retrieval
  depth is therefore not a loss point at this size.

## Bounds (Gate 1, already measured at world size 200)

- Under the harness's evidence semantics (v4 gate): ceiling **179/240 = 0.746** (rare ≤ 42/103).
- Under the shipped judge's polarity-blind model: **205/240 = 0.854** (rare ≤ 68/103).
- **35 rare patterns are undiscoverable even then; 32 never have k = 3 records at any one site.** Only pooling sub-k
  counts across sites could recover them — a change to the privacy invariant (founder decision).
- **Open: these bounds were measured at 309 users. The goal is stated at 10,000 agents.** Per-site counts grow with
  world size, so the k-floor binds less; the bound at 10K is unmeasured (the orchestrator is measuring it now).

## Offline policy estimates (tools/plateau/v5b_offline.py, estimates not runs)

Ask once at first week (v4 model) 165/240; ask once when HQ's own cells say ready 173/240 (same budget); timer re-ask
every 2nd week 199/240 at 4.2× questions. All timing policies lift echo decoys (type 4) 24 → 53–56. Types 2 and 5 are
saturated (59/60, 60/60). The other session measured: a type-2 temporal-order check is harmful at v4's asked week
(fires on 3% of type-2 decoys, 7.5% of true patterns) and only works when asking later; type 5 is caught by later
retractions, not ordering. The offline model under-predicts background-supported cost by ~1/3 (61 vs 96 measured).

## Rules for every research agent

- Dev seeds 0–5 only; never generate, read or use seeds 94000–94009. No paid/hosted model calls. Do not edit code,
  goldens, labels, scorer or metrics; do not commit.
- Never fabricate a paper, author, arXiv id, date or result. Every citation must be fetched and verified; mark
  anything unverified as UNVERIFIED. Distinguish demonstrated results from speculative transfer.
- Never present a hypothetical gain as measured. Every proposed improvement needs a control and a falsification test.
- A proposal that does not act on a measured loss point above (timing, k-floor, decoys, label-window scoring) must
  say which loss point it acts on and how, or it is out of scope for this benchmark.
