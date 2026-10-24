# EMERGENCE: two tracks, measured

Date: 2026-10-24. Session: Planner → Engineer → Adversarial Reviewer loop.
Status: **in progress**. Every figure below was executed in this session unless it is marked otherwise.
Cells that read _pending_ are runs still in flight; they are filled from `raw/e2e_ledger.jsonl` when they land.

## 0. Answer first

**Current best (dev seeds 0–5, real run): Track B h1n+nd+N1 = 0.629 recall, 174 decoys (baseline 0.388 / 118).
It fails the frozen decoy bar; the excess is class 2 (temporal scramble, inseparable from true patterns by design),
class 5 (stale chains) and cross-site echoes (addressed by N2, being measured).**


- **Track A (frozen EMERGENCE R0): 0.80 is not reachable under the frozen invariants, and this is now independently
  verified, not argued.** An evidence-level ceiling, computed from the generator's own records and attacked by an
  independent reviewer, caps `recall_all` at **0.6375** with the shipped 6-week window. The most permissive legal
  reading the reviewer could construct (aux-entity mentions, 26-week windows, late `as_of`) caps it at **0.746**. No
  development world reaches 0.80 even at the ceiling (best world 0.75). Detection, retrieval, Tesseract and model
  quality cannot raise a cap set by the k = 3 × two-site admission rule.
- **The limit is admission, not missing evidence.** Every Gold pattern on dev seeds 0–5 has at least k records
  somewhere (class NONE = 0 of 240). 54 patterns are lost only because their second corroborating site holds 1–2
  records, below k. That class carries 20 of 300 decoys: it is discriminative.
- **Track B (EMERGENCE-Next, separately versioned)**: protocol **N1** admits such a sub-k site as a corroborator
  beside at least one k-record site, using the `<k` bucket every site already sends. It is implemented, unit-tested
  and opt-in (absent, the gate is byte-identical R0). End-to-end results: _pending_. Track B scores are never
  compared with Track A as if the task were the same.
- **Live Qwen: wired, verified by a plumbing test, not yet run.** The real `run` job now sends every site's record
  judgments through a per-site model runtime (`--routing`). A fake judge served over HTTP reproduced the baseline's
  found set exactly through 5,247 calls. Live inference needs the user's machine (this cloud container has no local
  model endpoint and cloud inference is forbidden). Nothing in this report is live model output.

## 1. What counts, and the seed rules followed

- Metric: `recall_all` exactly as `research/e2e_ingest/harness.py` `discovery()` computes it, on the real process-mode
  `run` job (one NeuralGraph per site, Tesseract retrieval, real nats-server 2.10.29 with JetStream, site verifiers,
  the fabric). Generator, labels and scorer untouched (no file under `research/mycelic/corpus.py`, `org.py`,
  `emergence_world.generate/gold_labels`, `Harness.discovery`, `candidate_label` changed).
- Seeds: development seeds 0–5 only. **No preliminary or held-out seed was generated, inspected or run.** No freeze
  commit was made, so no held-out set exists yet (SPEC §3: seeds derive from a freeze commit).
- Run kinds, labelled in every run id and in the ledger:
  - `OFFICIAL-CLI`: unmodified `python -m research.e2e_ingest run` on an immutable checkout. Possible for seed 0 only:
    `check_world_counts` refuses every other seed (root-cause report rank 2).
  - `DIAGNOSTIC-guard-bypass`: seeds 1–5 through `tools/run_seed.py`, which replaces `check_world_counts` in-process
    and changes nothing else. Disclosed; never an official baseline.
  - `ABLATION`: `tools/ablate.py`, the same plus a pack override and/or candidate expansion; dev seeds only.
  - `PLUMBING-fake-judge`: the routed-judge path exercised with a fake server. Never inference.

## 2. Baselines reproduced (Track A)

Clean checkout `e094bd82`, process mode, drills skipped, real broker.

| Seed | 0 | 1 | 2 | 3 | 4 | 5 | Total |
|---|---|---|---|---|---|---|---|
| found / 40 | 13 | 12 | 17 | 17 | 19 | 15 | **93/240 = 0.3875** |
| decoys accepted | 17 | 18 | 21 | 22 | 18 | 22 | **118** |

Identical to the recorded figures (root-cause report: 13, 12, 17, 17, 19, 15; 118 of 300). **Seed 0 official CLI run**
(`OFFICIAL-s0-e094bd82`): unmodified `python -m research.e2e_ingest run`, immutable checkout `e094bd82`,
`code_dirty_start/end = false`, `checks_pass = true`; 13/40, the same 13 patterns, decoys 5/9/3. Its only non-pass is
`suites_not_green` (CI's junit suites are not supplied locally). Seeds 1–5 can only run as diagnostics (guard
bypass). Wall time ~5–7 min per world on 4 shared CPUs.

## 3. The ceiling (Track A feasibility), independently reviewed

Method (`tools/ceiling.py`, `tools/categories*.py`): from the generator's records, per Gold pattern, does any window
ending inside its scoring window hold ≥ 2 sites with ≥ k = 3 records on its key? Perfect detection, perfect judging,
every site asked. This over-states what the system can do; it is an upper bound.

| Evidence model (dev seeds 0–5, 240 patterns) | R0 cap |
|---|---|
| own facet records, 6-week window | 147 = 0.6125 |
| every record on the key, 6-week | 150 = 0.625 |
| anchor-or-aux mentions, 6-week (reviewer) | 153 = 0.6375 |
| own facet records, 26-week | 156 = 0.65 |
| anchor-or-aux, 26-week, late as_of allowed (reviewer, most permissive) | 179 = **0.746** |

Reviewer verdict: **WEAKENED, conclusion stands.** The first "every-claim" set was not a true upper bound (the
verifier also confirms through aux-entity mentions; seed 4 p023); the corrected bound is 0.746, still below 0.80. The
real seed-0 found set (13 patterns) lies inside every feasible set (no falsification). The claim "regardless of LLM
quality" was dropped: a judge that accepts aux mentions confirms beyond the narrow set, as the shipped lexical judge
already does.

**Category breakdown** (6-week window, anchor-or-aux model):

| Class | Patterns | Decoys (of 300) | Meaning |
|---|---|---|---|
| R0 | 153 (16 rare) | 207 | ≥ 2 sites with ≥ k: today's gate can confirm |
| ONE_CORR | 54 (all rare) | 20 | one site ≥ k, another site holds 1–2 records |
| ONE_ALONE | 1 | 2 | one site ≥ k, nothing elsewhere |
| POOL2 | 11 | 20 | no site ≥ k; ≥ 2 sites pooled ≥ 2k (fragmented) |
| WEAK | 21 | 51 | pooled ≥ k at best |
| NONE | **0** | – | no evidence anywhere |

Cumulative admission: R0 0.637 recall / 0.690 decoy acceptance (evidence level); + ONE_CORR 0.863 / 0.757;
+ POOL2 0.912 / 0.830. The decoy column shows R0's own decoy rejection does not come from counts (classes 2, 4, 5 are
all R0-feasible at the evidence level); it has to come from the verifier.

## 4. Where the real system loses R0-feasible patterns (seed 0 funnel)

Seed 0: 22–23 patterns are R0-feasible, 13 are found. The 10 missed (`tools/funnel.py`):
- 9 had a detector candidate but **no snapshot** (rule-layer only, or before the W12 warm-up): p000, p005, p009,
  p012, p013, p014, p023, p032, p039. Across the world, 485 of 539 detector candidates are rule-only and are dropped
  by the run (`emergence_world.py:289`).
- 1 was asked once, too early (p028: hypothesis at W13, would be supported at W15).

## 5. Track A ablations (dev seeds; all inside k = 3 and the two-site gate)

| Arm | What changes | Recall | Decoys | Questions/world |
|---|---|---|---|---|
| baseline | – | 93/240 = 0.3875 | 118 | 53–60 |
| h1 | `min_history_weeks` 6 → 1 (legal minimum), cap 60 | 89/240 = 0.371 | 148 | 60 |
| h1n | h1 + no candidate cap | **140/240 = 0.583** | 182 (fails decoy bar) | 84–90 |
| persist | re-ask while a candidate persists | 14/40 at s0 (+1) | 17 (+0) | 199 |
| persist+rule | + ask rule-only candidates, one question per 6-week window | **refused by the HQ bridge** (no detector snapshot week: provenance guard, correct) | – | 723 |
| nd (b3025fb6) | near-duplicate roots (echo = one root), first version | 94/240 = 0.392 | **108** (class 4: 41 → 30) | 53–60 |
| N1 (b3025fb6) | weak-site corroboration (Track B lever) | 96/240 = 0.400 | 119 | 53–60 |
| nd v2 (f830a57c) | hardened near-duplicate roots alone | 94/240 = 0.392 | 110 | 53–60 |
| **h1n+nd+N1 (f830a57c, Track B)** | combined | **151/240 = 0.629**; rare recall 0.09–0.24 per seed (baseline 0–0.11) | 174 (fails decoy bar) | 84–90 |

Notes:
- Every ablation ran with `--skip-drills`: the run's privacy and leakage drills (c1–c6) did not execute; only c0,
  c7, c8, c9 were evaluated (c8 fails on seeds 1–5 solely because those worlds are not the seed-0 world table the
  guard checks). A drills-on N1 run and a drills-on plumbing run are reported in §6.
- `persist+rule` cannot be tested by expanding candidates at HQ: the bridge publishes a conclusion only under the
  detection run's recorded snapshot week for that key (`hq_bridge.py:269-277`). Rule-layer routing therefore needs the
  detector itself to record rule hits as candidates: a candidate-source change (N, founder-gated), not attempted.
- Rare patterns are the bottleneck at the system level: on seeds 0–2 all 29 rare ONE_CORR patterns produce a detector
  candidate, but 25 are rule-only and never asked. N1 and nd act only on asked candidates.

Findings so far:
- **h1 alone is negative.** It surfaces the same patterns earlier (some before their label window, e.g. p036 at W08)
  and the cap of 60 cuts others (84 candidates for 60 slots). It rescues 6 and loses 6 at seed 0.
- **h1n is a large recall gain that fails the degeneracy bar.** SPEC §2.3: decoy acceptance must not exceed the
  baseline's. Its extra decoys are spread over class 2 (temporal scramble, +14), 5 (stale chain, +9), 4 (echo, +6)
  and 3 (near-miss entity, +5) on seeds 0–2.
- **Echo decoys are the copied-root defect.** The narrative begins with the author line, so the shipped exact-text
  root rule never fires on echoes: every echo of one event counts as an independent root. `nd` is the fix (§6.2).
- Labels per the root-cause report: detector settings and `top_n` are S (settings SPEC does not freeze yet);
  routing rule-layer hits is N (founder's decision on the candidate source). None of these touches k, the two-site
  rule, raw-record locality or the Boundary.

## 6. Changes made (each opt-in; default run unchanged), with regression tests

Deployment-branch commits (patches in `patches/neuralgraph-deployment-branch/`):

1. `fa609166` + `6fd8db9f`: **`--routing DIR`**. Each site process loads `<site_id>.routing.json`; the runtime is
   bound to exactly `site:<id>` (a wildcard or foreign boundary is refused); every call leaves a receipt (payload,
   validated reply or error kind) and a ledger row (model served, tokens, latency) in the site's own directory. The
   second commit fixed a defect the plumbing test found: the class the run actually starts (`E2ESiteProcess`) never
   built the runtime, and its inherited `close()` would have crashed every run.
2. `22cbd0ed`: **N1** weak-site corroboration (Track B). `tests/mycelic/test_pushdown_n1.py` (8 tests): R0 unchanged
   without the key; supports 1 k-site + 1 weak; never all-weak; weak adds no roots; contributing refute still
   contests; cap respected; out-of-range refused.
   **Disclosure analysis.** Today every routed site's verdict is already published to the fabric as a
   `collective.verdict` event carrying `verdict` and `support_bucket` (including `"<k"`), for every question and
   whatever the conclusion's status (`research/routing_spike/publish.py:80-87`); the gate result already lists
   `weak_confirming_sites` under R0 (`gate.py:308`). N1 changes only the conclusion's `status`, a deterministic
   function of those already-published events. It therefore publishes no new information and adds no differencing
   surface; what it changes is the corroboration decision (whether a sub-k independent site may count), which is a
   policy question for the founder, not a disclosure one. k = 3 per counted count, raw-record locality and the
   Boundary's artifacts are unchanged.
3. `7bbaad1d`: **near-duplicate roots** `egress.near_duplicate_roots {jaccard 0.65, days 14}`. Offline against ground
   truth on dev seeds 0–5: every echo (site, event) group collapses to one root; 2 of 3,912 true-pattern records are
   merged with another record. At 0.6 the false merges are 4/1,250 (seeds 0–1), at 0.5 107/1,250. Tuned on dev seeds
   against a generator whose echoes are near-duplicates by construction; the effect on real text is not measured.
   `tests/mycelic/test_near_duplicate_roots.py` (6 tests).
4. `b3025fb6`: deliberate re-pin of `SPIKE_CODE_HASH` (22dc1aba → 40f2d713), listing every pinned file changed.

**Plumbing equivalence (PASS)**: seed 0, fake lexical judge over HTTP through `--routing`: 5,247 judge calls, 5,247
receipts, 0 errors, every ledger row fake-marked, boundary mode `own`; found set and decoys identical to the baseline
(13/40, 5/9/3). The judge path is wired correctly; the plumbing run is not inference.

## 6b. Review round 2 on the deployment-branch changes (independent reviewer) and what was done

| Finding | Status | Action |
|---|---|---|
| near-dup roots chain (A~B, B~C merged C with A) and depend on order | CONFIRMED | fixed in 494650c7: join only own-root records |
| templated alerts with other values merged; negated report joins positive root | CONFIRMED | fixed: values and negation words must match |
| earlier-dated late record joins a later root | CONFIRMED | fixed: backward window only |
| quadratic ingest (81.6 s at 4k/site) | CONFIRMED | fixed: index on (structured, received_date); 50k/site: 16.9 s on vs 3.5 s off |
| N1 corroborator absent from `confirming_sites`; reason wording | CONFIRMED | `corroborating_sites` added when N1 is on; wording fixed |
| plumbing evidence from an intermediate code state, drills skipped | CONFIRMED | re-run at f830a57c **with drills**: c1 signed, c2 lineage, c3 idempotent, c4 recovery all ok; 10,882 fake-judge calls (reference + main passes) |
| N1 discloses nothing new | CONFIRMED by reviewer | – |
| v2 runs executed while N2 code sat uncommitted (inactive) in the worktree | self-found (c7 `spike_paths_clean`) | labelled dirty-tree; N2 committed 7eabc4c3 |

N1 with drills (seed 0): c1–c4 ok. 4 new near-dup regression tests fail on 7bbaad1d and pass after.

**N2 (Track B, 7eabc4c3)**: a site's roots count toward `min_independent_roots` only if its own roots bucket reaches
k. Reason: under R0 `bucket_lower('<k') = 1`, so one event echoed to three sites (one root each after nd) sums to
three "independent" roots. Offline re-gate of the actual verdicts of the h1n+nd+N1 run at seed 0: 0 of 22 true
patterns lost, 5 of 9 supported echo-decoy conclusions dropped. End-to-end: _pending_.

## 7. MycelicLabs (Rust production workspace) — review lane and fix lane

- Baseline main `64027cb` with a real broker: 184 passed, 0 failed, 1 ignored (9 real JetStream tests).
- Process-level 3-owner fixture (`crates/mycelic-harness/tests/ingestion_fixture_process.rs`) passed first run; the
  adversarial review then showed several assertions pass for the wrong reason:
  1. the query is not entity-bound (the API has an `entity` filter the test did not use), so abstention is vacuous
     while no other entity is complete; a free-text "SD-9" query returns another entity's conclusion (quality defect);
  2. **copies count as independent sources** (CONFIRMED, fixed, below);
  3. a verbatim re-post of a retracted note restores the conclusion — **conflicts with the golden scenario
     `follows_evidence` (steps 36–41), which specifies a recount by another agent as evidence**; left as an owners'
     decision;
  4. the similar-entity and canary checks are too narrow; 404 bodies do not disclose existence (checked: identical
     for real and made-up ids).
- Fix (`3daed66`, then `8e17b46`): active raw notes with the same slot and normalised text are one source. Round 2
  showed the first version trusted the client's `observed_at` (a backdated or offset copy displaced the original);
  the group is now credited to the member whose producer already contributes other evidence, so copying cannot add an
  agent. `tests/copied_roots.rs` fails before and passes after, including the backdated and offset attacks. Pushed:
  PNurmanM/MycelicLabs branch `claude/mycelic-gate-0-recovery-tjl82x` (`a09283a`).
- Found while writing the test, not fixed: **slot selection is not independence-aware** — an agent holding two
  slots can block composition even when an independent alternative exists (recall, not safety).
- Health startup race: `api.rs:273` returned 503 vs 200 once under heavy CPU load; `golden_api` passed 3/3 when
  re-run. Recorded, not a root cause.

## 8. Simulator track (10,000 and 50,000 users) — `simulated: true`

| Scale | Seeds | Users | Records/world | `found` mean | rare recall | run s (p50) |
|---|---|---|---|---|---|---|
| 10,000 | 0–9 | 9,999 | 327,805 | **0.5975** | 0.361 | 8.65 |
| 50,000 | 0–4 | 49,999 | 1,635,240 | **0.5660** | 0.315 | 38.2 |

Exactly the committed rows (`research/mycelic/artifacts/e1_baselines.jsonl`). Different metric and different worlds
from `recall_all`; no model calls (0 inference); agents are simulated. Never an end-to-end figure.

## 9. Tesseract, in each codebase

| Codebase | Retrieval Tesseract on a production request path? | TesseractCoordinator? |
|---|---|---|
| NeuralGraph `origin/main` 038fb63 (Rust) | No: benchmarks only (`runner.rs:1442`, `call_center.rs:262`, …); `neuralgraph-memory` has 0 references | No: benchmark/experiment/tests only |
| MycelicLabs main 64027cb | No: "tesseract" appears nowhere; BM25 over stored memories | No router/registry/coordinator |
| EMERGENCE run (deployment branch) | **Yes**: every site retrieves through Tesseract in the process-mode run | No |

On the EMERGENCE path Tesseract is present but **not decision-relevant**: the root-cause report's counterfactual
changed 0 of 165 verdicts at seed 0. Integrating it more deeply is not where recall is lost (§3–4).

## 10. Live Qwen: what exists and what is blocked

- `tools/qwen_live/run_live_qwen.sh` (deployment branch): discovers Ollama / llama.cpp / vLLM / LM Studio on loopback
  only; proves one inference and saves the request/response; writes one routing file per site (exact `site:<id>`);
  runs the job; summarises calls, tokens, latency p50/p95, receipts and the found set. Seeds ≠ 0 are labelled
  DIAGNOSTIC.
- Expected load: ~5,250 judge calls per world at 200 users (measured in the plumbing run). At 0.5–2 s per call on a
  local 7B model that is ~45 min – 3 h per world.
- Blocked here: this container has no local model server, and cloud substitution is forbidden. To run: on the
  machine that serves Qwen, `claude remote-control` (or the desktop app) in the repo folder, then the script.

## 11. Scale budget for Track B (measured footprint)

NeuralGraph site graphs at seed 0: 12,964 nodes, **0 edges**, 54.7 MB → **4,222 bytes per node** (text + 256-dim
embedding, SQLite).

| Workers × memories | Nodes | Storage (nodes only) |
|---|---|---|
| 10k × 1,000 | 10M | ~42 GB |
| 10k × 5,000 | 50M | ~211 GB |
| 50k × 1,000 | 50M | ~211 GB |
| 50k × 5,000 | 250M | ~1.06 TB |

This container has ~20 GB free. Full materialisation needs a ≥ 250 GB machine or disclosed lazy materialisation.

## 12. Open decisions (not settled by this work)

1. Track B protocol N1 (weak-site corroboration) and its privacy review before any use beyond research.
2. The candidate source (rule-layer hits, re-asks): founder-gated (N) per the root-cause report.
3. MycelicLabs: should a verbatim recount of a retracted note count? The golden says yes; the reviewer's revival
   finding says it is exploitable.
4. Track B storage: lazy materialisation vs a larger machine.

## 13. Reproduce

See `tools/` (scratch paths are hard-coded as `S=`; set it to any directory). Baselines: `tools/baselines.sh 0 1 2 3 4 5`
on a checkout of `e094bd82`. Ablations: `tools/arms.sh <arm> <seeds>` on the deployment-branch patches applied to
`e094bd82`. Ceiling: `python3.11 tools/ceiling.py 0 1 2 3 4 5`, `tools/categories2.py`. Simulator:
`tools/sim_track.py 10000 0,...,9 out.jsonl`. Ledger: `tools/ledger.py <runs dir> raw/e2e_ledger.jsonl`.
