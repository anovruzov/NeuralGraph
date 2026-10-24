# ADVERSARIAL_REVIEW.md — every claim this mission made, attacked

Two sources. **Part A** is the swarm's own adversarial layer: four falsifiers, each a separate agent from the
author of its target, each handed the kills in advance and told that confirming was not its job. Their reports
are verbatim under `swarm/wave2/<id>/FALSIFICATION.md`. **Part B** covers the claims in this mission's earlier
documents (`EVIDENCE_CEILING.md`, `BENCHMARK_INTEGRITY.md`, `RECURSIVE_ACQUISITION.md`). An independent
reviewer was spawned for those at 15:03Z with five named claims (C1–C5) and had not reported by 16:45Z; it is
presumed lost. Part B is therefore what the wave-1 and wave-2 agents established *incidentally* about those
claims, plus the orchestrator's own re-attack — and it says which is which. A self-attack is weaker than an
independent one; where that matters, the gap is named rather than papered over.

## Part A — the swarm's claims (independent falsifiers)

| claim | attacked by | verdict | the decisive attack |
|---|---|---|---|
| H3-3: a non-monotone rule-week band ranks the pool | F-H3 | **FAILS** | beats the free line on 4/6 seeds; its margin over longest run (+0.036) survives no best-of discount; top-20 precision 0.14 against a measured p* of 0.39; its only novelty (the ≥16 demotion) is worth exactly the best-of-8 line |
| H5-2: co-activation separates gold from decoys (0.945) | F-H5 | **HOLDS at 200 users, does not transfer** | leak-free, selection-proof (empirical best-of-56 null max 0.619); 0.742 at 2,000 users, below 0.70 on the dominant decoy classes; the cause is record density, not region granularity as its author said |
| H5-3: the shape-gated count is a lever | F-H5 | **FAILS** | feasibility at the week it would ask is 0.616, not the ~0.75 quoted (that was the label-window flag, true of all 240 gold); p* 0.447 > P@20 0.393; at 2,000 users the gate admits 67% of background |
| H4-3: net expected utility over the cell class ranks the pool (0.794) | F-H4 | **FAILS** | fails its own per-seed kill; 80% of its AUC orders keys at classes where the gate never clears (0.657 among keys that can); inverts at 2,000 users (0.451); its 0.842 on the asked set was look-ahead (0.661 at the asked week); its mechanism sentence is false even at 200 (common gold, not rare) |
| H4-2: a label-free week choice lifts 1-in-30 to 1-in-18 | F-H4 | **FAILS as a claim** | P̂(clear) is 0 or 1 on 41/64 classes — a gate lookup, not a value of information; a parameter-free structural rule does better; ×4.5 optimistic; not replayable |
| H7-1: an echo-decoy veto removes type-4 passes at zero gold cost | F-H7 | **FAILS** | a no-op on the shipped pack (one root per record; buckets agree in 899/904 recorded confirms); replay of every recorded verdict: zero delta; costs 14–15 gold the moment gold shares roots; 10–36% at 2,000 users |
| H6: p* ≥ 0.47 (displacing) / 0.19 (additive) | applied by F-H3, F-H5, F-H4 | **HOLDS, and its caveat was decisive** | every ranker died on the feasibility term H6 flagged as dominant |
| H8: the null thresholds | applied by all four | **HOLDS** | nothing was promoted on a number inside the selection envelope; the seed-swap test caught what LOSO cannot |
| wave-1's structural finding: "gold is thin and staggered, decoys broad and synchronous" | F-H5, F-H4 | **HOLDS at 200 users as description; FAILS as mechanism** | real, measurable, and a property of record density at 200 users, not of the planting; it inverts at 2,000 |

**What Part A could not check.** The TB_v4 figures under the `nd` override (type-4 40 → 4 asked) remain gate-model
numbers: no v4 run with recorded verdicts exists on disk, and a diagnostic e2e was outside the falsifiers' rules.
The replay-level reorder (138 vs 128) is a replay, not an e2e run.

## Part B — the mission's earlier claims

| claim | source | what the agents established incidentally | orchestrator's re-attack | verdict |
|---|---|---|---|---|
| **C1** the gap decomposes exactly: 93 found + 5 asked-wrong + 142 never-asked, with 92 + 4 + 83 = 179 | `EVIDENCE_CEILING.md` §7 | F-H4's replay reproduces the official per-seed found 13/12/17/17/19/15 exactly; H6 found a 2-key accounting difference (100 gold *keys* asked vs 98 *patterns*: a key-vs-pattern unit mismatch, moves g_s from 0.275 to 0.273 and nothing else); D9 placed the 142's origin at `detectors.py:636` | the "never asked" class is read from `oracle_rows`, which holds only `candidates_true` — a gold pattern asked under a key that is not its own would be miscounted. H6's 100-vs-98 says this happened for ≤ 2 of 240 | **HOLDS WITH CORRECTION**: the decomposition is exact to the unit at the pattern level; the key-level count differs by 2 |
| **C2** 138 of 142 never-asked have a rule-layer candidate | `EVIDENCE_CEILING.md` §7 | H3 verified the dump's `rule_weeks` match the pool CSV on all 3,178 rows; D9 confirmed the code path (rule-only keys get `snapshot = None` and are dropped by `ordered_candidates`) | the join is by key string `entity:<anchor>:<chain>`; gold labels carry the same `key` field; no alternate spelling exists in the dumps | **HOLDS** |
| **C3** recursive acquisition at the gate: 5/42 targeted vs 4/42 untargeted, 111 vs 69 false positives | `RECURSIVE_ACQUISITION.md` | not re-run by any agent | the budget is per key (fixed from a per-week draft, disclosed); windows are matched across classes (fixed from an asymmetric draft, disclosed); the control's holder draw is one seeded shuffle, not an average; n = 42 makes 5 vs 4 one key. The precision gap (111 vs 69) is the only part with signal, and it points the wrong way | **HOLDS as a negative result; NOT INDEPENDENTLY VERIFIED** |
| **C4** no code was wrong: `detectors.py` byte-identical between `d88c150` and `ce0e357`; the defect is provenance | `BENCHMARK_INTEGRITY.md` | D9 audited the full path at `ce0e357` and found the three stale pins, the `--skip-drills`-without-`--suites` hole, and nothing behavioural; F-H7 confirmed the shipped pack has no `rule_snapshots` key | byte-identical `detectors.py` is necessary, not sufficient: `gate.py` and `loader.py` also moved between those commits. Whether v4's 163/240 reproduces was never settled, because the reproduction was stopped by the founders' decision not to promote it | **HOLDS as stated ("no code was wrong"); the implied "163 would reproduce" is UNVERIFIED and was withdrawn** |
| **C5** 74.6% (F) is the defensible ceiling, 85.4% (B) the permissive one; every infeasible pattern is rare | `EVIDENCE_CEILING.md` §1–3 | H7-3 attacked the ceiling as overcounted and lost: B 205/240 and K 199/240 identical per seed after root de-duplication. F-H5/F-H4 computed c_g from the same `ceiling_b.json` and reproduced its feasible weeks; H4 reproduced them exactly but for a full-window edge (1/205 B, 1/199 K). The other session explained the one found-but-F-infeasible pattern (seed 4 p022: background-assisted) | B counts a contradicted key as support; calling it "defensible" would be wrong and the document does not — it calls it permissive and reviewer-constructed. F is a ceiling on *evidence availability*, and the 93-vs-92 case shows it is not a strict bound on *score* | **HOLDS, with the §6 caveats as written** |
| the 142's origin is candidate generation, not judging or retrieval | `EVIDENCE_CEILING.md`, `NEXT_EXPERIMENTS.md` | D9: the code path; H2: judging supports 207 of 216 asked decoys — so "judging is near-perfect on gold" is true and "judging is near-perfect" is not | the 93-of-98 figure measured judging on *gold*; on decoys the judge is nearly blind. The document said the former and should not be read as the latter | **HOLDS WITH CORRECTION**: judging is near-perfect on asked gold and near-useless on asked decoys |
| the official baseline 93/240 with 118 decoys, all checks green | `raw/official_ce0e357/` | F-H7 replayed all 341 questions / 904 confirms through the shipped `evaluate` and reproduced every recorded status, 93 and 118 per seed and per type | the official runs' verdict is `pass: false` (no `--suites` supplied); "all checks green" is `checks_pass: true` with c10 unevaluated — exactly the reporting hole this mission then closed | **HOLDS as a baseline; its own verdict line is `pass: false`, which `BENCHMARK_INTEGRITY.md` says** |

**The weakest claim in these documents, not listed above:** `NEXT_EXPERIMENTS.md` §1 says the Gate-0 session's
rule-hit-first ordering is "+10 on the seeds the ordering was chosen on". F-H4's replay reproduces rule-first at
128 and the e2e at 127; neither is a held-out number, and both sessions have now said so. The honest figure is
"+8 to +10 over 118 on dev seeds, best-of-ten, no held-out run" — and the same discount applies to the 138 that
F-H4 left standing.

## What would change any verdict above

- An e2e run of the asked-set reorder that beats 128 with ≤ 113 decoys **after** a founders' ruling on fitted
  tables — the one open item.
- A sealed-seed run of anything — forbidden until the founders decide, and the only thing that would turn a dev-seed
  figure into a result.
- The independent C1–C5 reviewer reporting; its verdicts supersede Part B's self-attack where they differ.
