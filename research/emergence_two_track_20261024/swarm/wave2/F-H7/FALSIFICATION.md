# F-H7 — falsification of H7-1 (echo-decoy gate veto: do not count a confirm whose support bucket exceeds its roots bucket)

Read-only on every repository; dev seeds 0–5; no e2e, no pytest, no broker. Scripts `f_*.py`, outputs `run_*.txt`,
`results_*.json`, patch `gate_veto.patch`, test proposal `test_pushdown_h7_echo_veto.py`, all under this directory.
Recorded stores were copied to `db/` before reading; the `nd` rule was run with the shipped `RecordStore.ingest` on
scratch stores under this directory, never on a run directory.

## Summary (10 lines)

1. **The shipped `roots_bucket` does not track distinct roots.** `root_ref` is origin → exact folded-narrative match → self; echoes carry the author line, so each echo is its own root. In the official ce0e357 stores 2,568 of 2,574 seed-0 records are their own root and **support_bucket == roots_bucket in 899 of 904 recorded confirms** (the 5 exceptions are same-author exact-text self-copies).
2. Only the research-only v4 override `egress.near_duplicate_roots {0.65, 14}` makes roots_bucket mean roots. Under it H7's numbers transfer closely: type-4 TB_v4 passes **57 → 5** (H7 said 57 → 4), asked d4 **40 → 4**, gold 207 → 207, ceiling 205/199 unchanged. Under the shipped pack the veto is a **no-op: d4 60 → 60**.
3. **Replay on the recorded verdicts (341 questions, 904 confirms, six seeds): the replay reproduces every recorded status and the report's 93/118 exactly; with the veto, found 93 → 93, decoys 118 → 118, type 4 41 → 41.** One status changes (seed 4, a background `revenue_miss` key, supported → hypothesis). Against the recorded 118/93 the delta is zero on every seed and every type.
4. **Zero gold cost is a generator artefact.** Every gold facet record has its own event id (`corpus.py` `arange(next_event, next_event+k)`). Rewriting gold facet records so that a quarter of (pattern, facet, site) groups report one incident costs the veto **14–15 of 240 gold patterns beyond N2's own 12–13**; half the groups: **32–34**; two reporters per incident everywhere: **10**. The d4 benefit in those worlds is unchanged (57 → 4).
5. **At 2,000 users the gate-level effect collapses.** Seeds 0/1/2 (label-window protocol, same at both sizes): at 200 users d4 passes 10→1, 9→0, 10→1 (90–100 % removed); at 2,000 users **11→9, 11→7, 10→7 (event roots) and 11→9, 11→9, 10→9 (nd roots): 10–36 % removed.** H7-1's own claim "removes ≥ 90 % of type-4 passes" fails.
6. The separation statistic itself survives scale: AUC d4-vs-gold 0.964–0.978 at 2,000 (1.000 at 200). It is the bucket granularity that defeats the veto: with 9–13 sites an echo site holds 3–9 records on 1–3 roots ('3-9' over '3-9', invisible), and the N1 path always finds one non-vetoed k-site plus a weak site.
7. Gold also starts to carry the signature at scale: 8–10 of 40 gold keys show a one-bucket gap at 2,000 users (2–4 at 200), from background echoes on the key; no gold pass is lost yet, but the margin that made "zero cost" is gone.
8. `nd` itself is imperfect in both directions: 1.03–1.10 roots per echo group (not 1.00), 227 non-echo background records merged, 2 gold facet records merged (seed 3). None of this moves a gate verdict at 200 users.
9. `gate_veto.patch` (124 lines, gate.py + loader.py, opt-in `pushdown.echo_veto_bucket_gap`) applies cleanly on a copy and parses; applied it would move `SPIKE_CODE_HASH` 0e05bc00 → **8b2c6940** (142 files unchanged) and all five `LIVE_CODE_HASHES_R002` digests, `DETECT_SHA256` not.
10. **H7-1 is DEAD as a gate fix**: inert on the shipped pack and the recorded run, dependent on a research-only text-similarity rule, zero-cost only because this generator never lets two gold reporters share an incident, and reduced to a 10–36 % effect at 2,000 users. What survives is one narrow fact: at 200 users, under the v4 override, on this generator, the veto removes ~90 % of type-4 passes at no measured cost.

---

## Kill 1 — does the shipped `roots_bucket` track distinct roots? VERDICT: **KILLED for the shipped pack; SURVIVED only under the v4 `nd` override**

What the code does (`ng-deploy`, read-only): `verify.py:336` `roots = bucket_of(len({r.root_ref for r in yes}))`; `records.py:412-420` sets `root_ref` to `origin_ref` (None for every EMERGENCE record, `emergence_world.py:8`), else the root of an earlier record at the same site with the identical folded narrative (`_ROOT_BY_KEY`), else — **only when the pack sets `egress.near_duplicate_roots`** — `_near_duplicate_root` (same entities, received within `days` before, same values and negation words, Jaccard ≥ `jaccard` on folded word sets, joining own-root records only), else the record itself. The shipped emergence pack (`research/e2e_ingest/pack/emergence/egress.json`) has **no** `near_duplicate_roots`; the official run's store carries that pack (config_hash 7783d265…). The v4 override (PLATEAU_DIAGNOSIS.md:174) adds `{jaccard 0.65, days 14}`.

Every echo's narrative begins with `[dNNN] <author> ::` (`corpus.text`), and 35 % of records get a paraphrased last token, so the exact-text rule never joins two authors' echoes (REPORT.md:153 says the same).

Measured (`f_1_roots.py`, `run_1_roots.txt`; real `RecordStore.ingest` on scratch stores, ingest order = rid order as in `run_pipeline` and the recorded store, verified `seq` follows rid):

| root definition | records on pool keys (6 seeds) | own-root | echo (site,event) groups → roots per group | gold facet records merged | roots holding > 1 event |
|---|---|---|---|---|---|
| shipped default | 26,589 | 26,017 | 1,647 → **4.1–4.4** | 0 | 0 |
| nd (0.65, 14) | 26,589 | 20,096 | 1,647 → **1.03–1.10** | **2** (seed 3, one root, two events) | 2 |
| generator event id (H7's `events`) | — | — | 1.00 by definition | 0 | 0 |

Recorded official HQ stores (`f_2_replay_official.py`): **5 of 904 confirms** have `support_bucket` above `roots_bucket`, all from same-author exact-text copies; seed 0 has 0 of 148.

H7's A4/A5 recomputed under each root definition (whole pool, rule weeks, model B; `run_1_roots.txt` §1b–1c):

| root | gold raw→veto | d1 | d2 | d3 | **d4 raw→veto** | d5 | other raw→veto | ceiling B / K raw→veto |
|---|---|---|---|---|---|---|---|---|
| event (H7) | 207→207 | 98 | 60 | 111 | **57→4** | 60 | 178→178 | 205→205 / 199→199 |
| nd | 207→207 | 98 | 60 | 112 | **57→5** | 60 | 244→244 | 205→205 / 199→199 |
| shipped default | 207→207 | 98 | 60 | 113 | **60→60** | 60 | 274→274 | 205→205 / 199→199 |

Per seed under nd: d4 10→1, 9→1, 10→1, 9→1, 10→1, 9→0; asked (snapshot-state) d4 keys **40 → 4** (H7: 40 → 3). Under the shipped default every d4 key passes (60/60) and the veto removes none, because roots ≡ records.

How the shipped bucket differs from what H7 modelled: H7's `events` is the generator's event id — an oracle no site has. The closest exported quantity is nd's text-similarity root, which (a) exists only under the research override, (b) merges 1.03–1.10 roots per echo group and 227 non-echo background records per six seeds, (c) is feed-order dependent (CONTINUATION.md F3; backward window only). The numbers transfer because nd happens to approximate the oracle on *this* generator, whose echoes are near-duplicates by construction.

## Kill 2 — replay the veto on the recorded verdicts. VERDICT: **KILLED** (zero delta against 118/93)

`f_2_replay_official.py`, `run_2_replay_official.txt`. Inputs: copies of the six `world/hq/collective.sqlite3` stores (`pd_questions`, `pd_routes`, `pd_verdicts`, `pd_conclusions`) and the six reports' `fabric_conclusions`. The shipped `evaluate` reproduces the recorded status for **341 of 341** questions; the recount under the harness's own rules (`harness.py:1291-1324`: found = gold pattern with a supported, lineage-ok conclusion on its key in its window; a decoy accepted when such a conclusion sits on its facet-0 key in its window, counted as distinct patterns) reproduces **93 found and 118 decoys, every seed, every type**. The veto variant is `gate_veto.patch` applied to a copy of gate.py and exec'd as a module, gap 1.

| seed | questions | confirms | confirms with gap ≥ 1 | found rec → veto | decoys rec → veto | by type rec → veto |
|---|---|---|---|---|---|---|
| 0 | 55 | 148 | 0 | 13 → 13 | 17 → 17 | {2:5, 4:9, 5:3} → same |
| 1 | 53 | 137 | 0 | 12 → 12 | 18 → 18 | {1:2, 2:4, 4:7, 5:5} → same |
| 2 | 60 | 155 | 1 | 17 → 17 | 21 → 21 | {2:6, 3:1, 4:7, 5:7} → same |
| 3 | 53 | 140 | 0 | 17 → 17 | 22 → 22 | {1:3, 2:8, 3:1, 4:4, 5:6} → same |
| 4 | 60 | 157 | 3 | 19 → 19 | 18 → 18 | {1:1, 2:7, 3:1, 4:6, 5:3} → same |
| 5 | 60 | 167 | 1 | 15 → 15 | 22 → 22 | {2:7, 3:1, 4:8, 5:6} → same |
| **all** | 341 | 904 | **5** | **93 → 93** | **118 → 118** | t4 **41 → 41** |

Three questions carry a vetoed site; one changes status (seed 4, `entity:E-UMBRA-CORE:revenue_miss` W12, background, supported → hypothesis). Type 4 stays at 41 because, without nd, an echo site reports 9 echoes as 9 roots.

Model-level counterpart under the TB_v4 override (nd roots, rule weeks, H7's gate model): asked d4 **40 → 4**, gold unchanged (table in Kill 1). No TB_v4 run with recorded verdicts exists on this machine (`runs/v4repro-*` hold only suites; `R/raw/v4_s0_replay_reference.json` is a summary), and the brief's DIAGNOSTIC e2e is outside this agent's rules (no e2e runs), so the TB_v4 figure remains a model, not a replay.

## Kill 3 — gold cost on a world where gold shares roots. VERDICT: **KILLED** (zero cost is a generator artefact)

`f_3_gold_shared.py`, `run_3_gold_shared.txt`. In the generator a gold facet's k records get k event ids (`corpus.py:566`); only class 4 writes one id for all echoes (`:659`). The counterfactual rewrites the event id of gold **facet** records only, per (pattern, facet, site) group G — background records on the key untouched. Since an echo here is realised as near-identical text keyed on the event id, and Kill 1 shows nd collapses such groups to 1.03–1.10 roots, rewriting event ids is the faithful stand-in for what a site would export under the override. TB_v4 over label windows, 240 patterns:

| counterfactual | model | raw (N1+N2) | N2's own cost vs original | veto ≥ 1 | **veto's marginal cost** | rare / common |
|---|---|---|---|---|---|---|
| original | B / K | 205 / 199 | 0 / 0 | 205 / 199 | **0 / 0** | — |
| CF-C: one shared pair per group | B / K | 161 / 157 | 44 / 42 | 160 / 157 | **1 / 0** | 0/1 |
| CF-B: two reporters per incident | B / K | 161 / 157 | 44 / 42 | 151 / 147 | **10 / 10** | 0/10 |
| CF-A @ 25 % of groups one incident | B / K | 192 / 187 | 13 / 12 | 178 / 172 | **14 / 15** | 1/13 · 1/14 |
| CF-A @ 50 % | B / K | 176 / 174 | 29 / 25 | 144 / 140 | **32 / 34** | 1/31 · 1/33 |
| CF-A all groups | B / K | 57 / 43 | 148 / 156 | 29 / 24 | **28 / 19** | 0/28 · 0/19 |

Mechanism (`run_3_gold_shared.txt` §3b): gold k-sites' exported pairs move from {('3-9','3-9') 2,245; ('10-49','10-49') 331} to {('3-9','<k') 149; ('10-49','3-9') 60; …} — exactly the echo signature. The veto cannot tell three colleagues reporting one incident from three echoes of one post, because the exported contracts carry no such distinction. d4 is unaffected by the counterfactual (57 → 4 in every CF world). Two things against interest: N2 alone already costs 12–44 patterns in these worlds, so TB_v4's roots arithmetic, not only the veto, is tuned to this generator; and in the frozen generator the veto costs nothing, as H7 reported.

## Kill 4 — scale: 2,000 users. VERDICT: **KILLED** (≥ 90 % removal becomes 10–36 %)

`f_4_scale.py`, `run_4_scale.txt` (seed 0), `run_4_scale_s1.txt`, `run_4_scale_s2.txt`. No detector dumps exist at 2,000, so weeks are label windows (gold: pattern window; decoys: decoy windows; background: all weeks) at **both** sizes; roots = event id and nd (real ingest at 2,000: 67–68k records, 9–13 sites). `check_world_counts` is not involved (`ew.generate` only).

| size, seed | sites | root | AUC d4-vs-gold | d4 TB_v4 raw → veto | removed | gold raw → veto | gold keys with gap ≥ 1 |
|---|---|---|---|---|---|---|---|
| 200, s0 | 7 | event / nd | 1.000 / 1.000 | 10 → 1 / 10 → 1 | 90 % | 34 → 34 | 4 / 2 of 40 |
| 200, s1 | 7 | event / nd | 0.995 / 1.000 | 9 → 0 / 9 → 1 | 89–100 % | 33 → 33 | 2 / 0 |
| 200, s2 | 8 | event / nd | 1.000 / 1.000 | 10 → 1 / 10 → 1 | 90 % | 34 → 34 | 2 / 0 |
| **2000, s0** | 9 | event / nd | 0.964 / 0.978 | **11 → 9 / 11 → 9** | **18 %** | 40 → 40 | 8 / 5 |
| **2000, s1** | 11 | event / nd | 0.971 / 0.988 | **11 → 7 / 11 → 9** | **18–36 %** | 37 → 37 | 10 / 3 |
| **2000, s2** | 13 | event / nd | 0.972 / 0.984 | **10 → 7 / 10 → 9** | **10–30 %** | 38 → 38 | 9 / 5 |

The statistic still separates (0.96–0.99) but the gate no longer feels it. Per-site d4 histogram at 2,000 (`run_4_scale.txt`): the mass sits at 3–9 records on 1–3 roots and 10–14 on 2–4 — '3-9' over '3-9' or '10-49' over '3-9'; the first is invisible to a bucket-index gap, and with 9–13 sites a d4 key always has one such site plus a weak site, which N1 accepts. H7's own survivor mechanism ("two sites at (9 records, 3 roots)") is the rule at scale, not the exception. Background passes under TB_v4 at 2,000 are 603–694 of ~1,050 keys (the discrimination collapse EVIDENCE_CEILING §6 anticipated) and the veto removes at most one of them.

## Kill 5 — the proposal. VERDICT: **delivered (proposal only; nothing applied)**

`gate_veto.patch` (unified diff, `a/mycelic/collective/pushdown/gate.py` and `a/mycelic/collective/packs/loader.py`): opt-in `pushdown.echo_veto_bucket_gap` (0 = shipped gate), read into `GateParams` (gate.py:84, 92–93, 100–101); the `counted` test at gate.py:234 becomes `counted and not echoed`, with `echoed = labels.index(support_bucket) − labels.index(roots_bucket) ≥ gap`; a vetoed site is neither counted nor `weak` (:240), does not make "no evidence" (:262), cannot be an N1 corroborator (:274 already requires `'<k'`), gets the note "confirms with more records than roots (echo veto; not counted)" and the field `support.echo_vetoed_sites`; the shipped output is byte-identical when the key is absent. loader.py: `_PUSHDOWN_OPTIONAL` (:147), `PushdownConfig` (:326), `S_PUSHDOWN_OPTIONAL` (:584, `_int(0, 3)`). `patch -p1 --dry-run` applies cleanly on a copy; both files parse; the patched gate reproduced all 341 recorded statuses with the key off and flipped one background conclusion with it on (Kill 2). Test proposal: `test_pushdown_h7_echo_veto.py` (9 tests mirroring `test_pushdown_n1.py`: opt-in bytes/hash, veto counted/noted, equal buckets not vetoed, not an N1 corroborator, not "no evidence", gap 2, range refused, determinism).

Pins if applied (`run_5_pins.txt`, computed by hashing the pinned file sets with the two patched copies substituted; the ledger's current values reproduce exactly):

| pin | covers the change? | current | would read |
|---|---|---|---|
| `report.SPIKE_CODE_HASH` (`mycelic/collective/**/*.py`) | yes, both files | 0e05bc00… | **8b2c6940f0639cc7a1eec3c18ea98018b43ad8e50e9191ee92ffe732661c4a22**, files 142 unchanged |
| `LIVE_CODE_HASHES_R002` X1 (loader.py) | yes | 150ae5fa… (tree) | 381dd91d… |
| E1 (loader.py) | yes | 172231c8… | dac1a6c7… |
| E2 (loader.py, gate.py) | yes | f8e866ae… | 0457e20d… |
| openfda_replay (loader.py) | yes | 643011d7… | 381dd91d… |
| X5 (loader.py, gate.py) | yes | 9ee8fa5b… | cde2a431… |
| `DETECT_SHA256` (detect/ only) | no | — | unchanged |

Per PIN_LEDGER.md §5 the re-pin is a separate reviewed commit that names the change; `LIVE_CODE_HASHES_R002` is already stale by design and its five "would write" values in the ledger would all move again, so the ledger's §2 table must be rewritten in the same commit. The pack config hash of any pack that sets the key changes (as for N1/N2); the shipped packs' hashes do not.

---

**H7-1: DEAD after wave 2, because** the veto is a no-op on the shipped pack and the recorded run (93 → 93, 118 → 118, type 4 41 → 41: the shipped `roots_bucket` is the record count), works only through the research-only `nd` text-similarity override, costs zero gold only because this generator never lets two gold reporters share an incident (14–34 of 240 patterns lost when a quarter to a half do), and at 2,000 users removes 10–36 % of type-4 passes instead of the ≥ 90 % it claimed — a 200-user, one-generator property, not a gate fix.
