# Continuation — 2026-10-24, session 2 (planner / engineer / adversarial reviewer)

Status of everything below: executed in this session unless marked otherwise. Nothing here is a held-out result,
no freeze commit exists, and no model inference of any kind has been run.

## 1. Recovery: what existed, and where

| | State found | Now |
|---|---|---|
| NeuralGraph `claude/mycelic-gate-0-recovery-tjl82x` | `f440d10` on the remote | unchanged; this file is added to it |
| MycelicLabs `claude/mycelic-gate-0-recovery-tjl82x` | `a09283a` on the remote (5 commits on `64027cb`) | base for the new production patches |
| The EMERGENCE deployment-branch commits (`fa609166`, `22cbd0ed`, `7bbaad1d`, `b3025fb6`, `f830a57c`, `7eabc4c3`, `b4eeb7bb`, `28275759`, `494650c7`) | **on no remote**; only the 11 archived patches | re-applied in order with `git am` onto `e094bd8` |

The re-applied series is `anovruzov/NeuralGraph` branch **`research/emergence-next-continuation-20261024`**
(draft PR #17). `git am` accepted all 11 patches in sequence. The patch archive did **not** reproduce a green
tree: two tests failed on the series alone, before any new work (§3).

## 2. The seed guard (Execution order A.3)

`check_world_counts` demanded section 3.1's row exactly, and that row is **seed 0's draw**: the guard refused
every other seed, which is why `raw/e2e_ledger.jsonl` holds one `OFFICIAL-CLI` row and six
`DIAGNOSTIC-guard-bypass` rows.

Measured here, 12 seeds at 200 users: `users`, `patterns_real`, `gold_discoverable`, `decoys`, `entities` never
move; `sites` (7–8), `teams` (34–40), `records` (12,288–13,131), `gold_rare` (11–21) all do.

- invariant counts: still exact equality with the row.
- drawn counts: held to the benchmark's own definition — `MIN_SITES` (2) sites for the admission rule to compare,
  a team per site, `MIN_SITES * ADMISSION_K * gold_discoverable` = 240 records at 200 users (fewer and a planted
  pattern cannot be confirmable anywhere), `0 <= gold_rare <= gold_discoverable`.
- the table check survives as `table_row_diff()`, **reported, never enforced**.
- `c8` (`report.py`) was the other half and had been missed: it still required the whole row, so a run on seeds
  1–5 still failed its checks. It now requires the invariants to match and the draw to be *reported*
  (`table_row_diff` present, naming only drawn counts).

Commits `4402c23` (guard) and `ce0e357` (c8, floor, pins).

## 3. Adversarial review (independent agent, 13 findings)

Full text: `reviewer/FINDINGS.md` in the session scratchpad; probes beside it. Fixed in `ce0e357`:

| | Finding | Evidence |
|---|---|---|
| F1 | **The branch was red.** `9857556` edited `detect/detectors.py` and re-pinned the report's `SPIKE_CODE_HASH` but not the repo's `DETECT_SHA256`, so `test_detection_is_byte_identical_to_its_pin` failed from that commit on — no run on this branch could have had `checks_pass` true | pinned `9d23000e…`, file `d113c269…`; re-pinned deliberately |
| F2 | The guard repair did not reach its purpose: `c8` still pinned seed 0's row | fixed; see §2 |
| F9 | `records >= users` is not derivable from the benchmark | replaced by the admission rule's arithmetic; the docstring now states what the guard gives up |
| F11 | `unittest.main()` above `class N2Tests` silently skipped the four N2 tests from the file's own entry point | moved |

Confirmed and **open** (each would move a reported number):

- **F3** near-duplicate roots are feed-order dependent — the window is backward-only and the real feed is not
  date-sorted: 6,370 descending adjacent pairs at seed 0, 158 extra roots (12.4% of the merge) from order alone.
  The class-4 reductions (41→30, 47→26) are therefore not a stable property of the world.
- **F4** under N1 a sub-k **sibling** refute does not contest: `gate.py:274` builds `weak_from_sites` without a
  role filter, so a conclusion with live contrary evidence can publish as `supported`.
- **F5** nd merges genuinely different readings (equal value *sets*, polarity words outside a 7-word list,
  `vA`/`vB` not matching the value regex) and collapses three independent reporters of one incident to one root.
- **F6** N2 constrains roots only; `reporters_lb`/`support_lb` still sum `bucket_lower('<k') = 1` per site, and
  N2's −21 evaporates for every echo group nd misses.
- **F7** 134 of 484 rule-sourced snapshots carry `supporting_sites` from an earlier week than the snapshot's own.
- **F13** every site process receives `demo_seed`, and Gold is a pure function of `(target_users, seed)` — a site
  could regenerate the label set. No leak today; no tripwire either.

Not defects: direct label leakage (REFUTED — `world.labels` is read only in `Harness.discovery`), the
`hq_bridge` provenance guard (holds as written), "N1 discloses nothing new" (content claim supported; the
reviewer notes the *audience* differs, which the privacy review should be told).

## 4. Ceiling, re-derived on this checkout (Execution order, step 2)

| Evidence model | REPORT §3 | Re-derived here |
|---|---|---|
| own facet records, 6-week | 147 = 0.6125 | **0.6125** (`tools/ceiling.py 0 1 2 3 4 5`) |
| own facet records, 26-week | 156 = 0.65 | **0.65** (same command) |
| anchor-or-aux, 6-week (reviewer) | 153 = 0.6375 | **0.6375** (`tools/categories2.py 0 1 2 3 4 5`, R0 row) |
| anchor-or-aux, 26-week, late `as_of` | 179 = 0.746 | **0.746** (same command, 26w block) |

The category table re-derives exactly too: R0 153 patterns / 207 decoys, ONE_CORR 54 / 20, ONE_ALONE 1 / 2,
POOL2 11 / 20, WEAK 21 / 51; cumulative admission 0.637/0.690 → 0.863/0.757 → 0.912/0.830. All four bounds are
now reproduced on this tree, not only the own-facet one. Raw output: `verify/` in the session scratchpad.

## 5. Broker

`nats-server v2.10.29`, the official linux-amd64 release, sha256
`e314da74a83a1d3876db8814c27eb990fe729640fd6452025b441bcede8390da`, matching the release's own `SHA256SUMS`.
Run from the session scratchpad through `MYCELIC_NATS_SERVER_BIN`; no system-wide install. Official baselines on
seeds 0–5 with drills enabled are running at `ce0e357` as this is written; their reports and the ledger rows
follow. Historical `DIAGNOSTIC-guard-bypass` rows keep their labels.

## 6. MycelicLabs production fixes (read-only repo, prepared as patches)

Against pinned `a09283a`, in `patches/myceliclabs/`:

- **0006 — independence-aware slot selection.** Slot selection took the strongest claim per slot independently,
  so one agent strongest in two slots left the composition resting on two holders and a `min_agents 3 /
  min_teams 2` rule refused it, although the same candidate set contained a three-agent selection. A slot whose
  holder already fills another selected slot is now offered the best-ordered candidate whose holder is new; the
  swap is taken only when it raises the holder count. `tests/independence_selection.rs`: **0 compositions before,
  1 after** (support 3, teams 3, lineage carries the independent note), plus a control that an already
  independent composition keeps its strongest claims — passing before and after.
- **0007 — arriving derivations judged against local state.** A `memory.derived` event whose known direct parent
  is already inactive is kept with its lineage but retracted, its dependents retired; `supersedes` obeys producer
  time, so a stale version delivered late cannot retire the newer one standing.
  `tests/derived_freshness.rs`: 1 passed / 2 failed before, 4 passed after. (Prepared against `64027cb`.)

Reproduce either:

```sh
git clone https://github.com/PNurmanM/MycelicLabs mycelic && cd mycelic
git checkout a09283a                       # 0007: 64027cb
git am ../patches/myceliclabs/0006-*.patch
cargo test --locked -p mycelic --test independence_selection   # 2 passed
cargo test --locked -p mycelic                                  # whole crate
```

For the fail-before, apply only the patch's test file.

## 7. What is still owed

- The `rule_snapshots` row (REPORT §5) is still `_pending_`; it is a required freeze input (SPEC §1 lists the
  candidate source as the column still to be frozen).
- Every ablation ran `--skip-drills`; the freeze candidate owes a drills-on six-seed run.
- F3–F7 and F13 above.
- Class 2 (temporal scramble) and class 5 (stale chains) carry the decoy excess; the planner agent's offline
  probes put class 5 at a 53:60 vs 15:240 refutation asymmetry and argue class 2 is not separable through the
  permitted interface. **Those two numbers are an agent's measurement, not re-run here**; the class-2 argument
  rests on `pack_predicate()`, `vocabulary.json` and `VERDICT_KEYS`, which are checkable.

## 8. Type-2 (temporal scramble): measured, and the naive check does not ship

Dev seeds 0–5, offline over the generator's own records; no run, no labels used as input to the check.

**Separability of the evidence (holds).** 60/60 type-2 decoys have at least one site holding records from ≥2
distinct chain stages; real patterns 152/240 (63.3%). Stage identity is recoverable without the generator: the
pack lexicon lists three phrases per stage in causal order (`vocabulary.json` `predicates.<chain>.lexicon.en`;
positions [0,3,6,9,12] for 5 of 7 chains, the other two ending in the separate `revenue_miss` predicate), and
`PRED_SURFACE`'s 150 phrases are pairwise unambiguous, so text → stage is exact.

**Usability of the check (fails as specified).** Scope decides everything:

| Verifier's input | real flagged | decoy2 flagged |
|---|---|---|
| the pattern's own facet records, whole horizon (oracle view) | 1/240 = 0.4% | 60/60 |
| every record on (anchor, chain), whole horizon | 112/240 = 46.7% | 60/60 |
| **records on the key inside the 6-week question window, at the week the system asks** | **18/240 = 7.5%** | **2/60 = 3.3%** |
| the same, at the best week of the label window | 61/240 = 25.4% | 60/60 |

Requiring a majority of visible stage pairs to be inverted (rather than any pair) does not rescue it at the asked
week: at the first week it fires on **0/60** type-2 decoys at every threshold (100%, ≥75%, ≥50%) and on 4.6–5.8%
of real patterns. At the best week it reaches 78–100% on type 2 against 15–18% on real.

**Conclusion.** The inversion is not visible yet when the system asks. The check is only worth its false-reject
cost *paired with asking later* (v5 timer or v5b ask-when-ready); alone it is strictly harmful. Type 5 is a
different mechanism — the facets are retracted later (`corpus.py:637-640`), so what catches it is the negative
polarity records, not order: with positives only, detection on type 5 falls from 60/60 to 7/60.


## 9. Type-5 (stale chain) is not a freshness problem — and the "60/60" does not hold for the shipped config

An engineer traced the freshness path end to end and returned a clean negative. Findings, with the checks that
confirm them:

- **The gate does enforce the 42-day rule.** `gate.py:252-254` computes `age = as_of - week_sunday(newest)` over
  the counted confirms and `is_stale = age > freshness_days`; the status branch at `:265-267` sits before the
  support thresholds. A parity sweep of ages 14–55 days against the harness oracle's `expected_status`
  (`harness.py:331-334`) gave **42 cases, 0 disagreements** — 42 days supported on both sides, 43 stale on both.
  One latent divergence worth a guard: the oracle's threshold is the literal `FRESHNESS_DAYS = 42`
  (`harness.py:105`) while the gate reads it from the pack (`gate.py:91`), and no test asserts the two are equal.
- **A `stale` verdict is published, not suppressed** (`hq_bridge.py:246-250`, `:329-343` have no status filter);
  what it suppresses is acceptance, since only `supported` counts (`harness.py:1293`).
- **Type-5 decoys are fresh when they are asked, by construction.** The facets land in the first half of the
  horizon and the retraction follows **30–70 days later** (`corpus.py:674-690`, the offset at `:680`). At the first
  rule week the retraction has not happened. Measured over dev seeds 0–5: the 27 rule-sourced snapshots have ages
  20/27/34/41 days — **none stale**.
- **So a freshness change cannot move class 5.** What makes a stale chain acceptable is that a confirm ignores
  retractions (`verify.py:251-254`), the gate contests only on a *contributing* refute (`gate.py:250`), and each
  key is asked once. The fix is retraction-awareness in the verifier, and it is not free: the root-cause report's
  own count says that contest rule would also contest 6 of the 13 found real patterns.
- **The "type 5 saturated at 60/60" figure does not hold for the shipped candidate source.** The engineer's
  ceiling there is at most 30/60 (27 keys never asked), and **our official six-seed baseline accepts exactly
  30/300 type-5 decoys** (per seed 3, 5, 7, 6, 3, 6) — an independent confirmation. 60/60 is a *v4
  rule_snapshots* figure, where the same analysis puts the ceiling at 57/60.

### The two decoy classes fail for the same reason the rare patterns do

Type 2's inversion is not inside the 6-week window at the week the system asks (0/60 fire there, §8). Type 5's
retraction has not arrived at the week the system asks. Both become visible later in the window, and so does the
evidence for 37 of the 76 rare misses. **Asking once, early, is simultaneously the recall bottleneck and the
reason both decoy classes are invisible.** That makes ask-when-ready (the Gate-0 session's v5b) the lever that
both verifier checks depend on, rather than an alternative to them.
