# REVIEW-4: benchmark integrity of the Mycelic-E2E v1 harness (pre-freeze)

Reviewer: REVIEW-4, model `claude-opus-5-5`. Scope: `research/mycelic_e2e/bench/` at 737b01c (WP2 c254a33 + WP3), contract
`BENCHMARK_CONTRACT.md`, `plan/PLAN_v1.md` §B/§F, dev run artifacts `runs/S1`, `runs/S1-baseline-single`, `runs/S1-baseline-source`
(seed 1, size S). Read-only: no code or run directory was modified. All mutation tests ran on copies in the reviewer's scratch directory.
Nothing under `/root/sealed_holdout` or any holdout file was opened.

Reproducible scripts (reviewer scratch, `rev4/`): `rescore.py` (independent scorer), `derive.py` (gold derivability from raw
records), `accretion.py` (baseline support analysis), `determinism.py` + `srcdet.py`, `gaterun.py` (gate on mutated copies).

## Verdict summary

| # | item | verdict |
|---|---|---|
| 1a | goal-only tasks: scope = a project whose members are exactly the observers; at S, 3 projects share members, so 3 golds answer one question | **MUST-FIX-BEFORE-FREEZE** |
| 1b | cross-tenant tasks: the "foreign" context also exists in the asker's own tenant (4/5 tasks); gold `abstain` wrong for 2 | **MUST-FIX-BEFORE-FREEZE** |
| 1c | record ids (`o`/`d`/`c`/`f`/`n` prefixes) and titles (`Housekeeping` = coincidence decoy) carry the record's role into the runtime | **MUST-FIX-BEFORE-FREEZE** (cheap; fatal for a live-model row) |
| 1d | temporal class is identifiable from public fields; its validity window was chosen with knowledge of the gold ages | DISCLOSE |
| 1e | question/goal text never names the gold or any option (checked 120/120); task ids shuffled | OK |
| 2a | scorer arithmetic: independent re-score equals `score.json` for every task (78 / 111 / 107) | OK |
| 2b | forbidden-holder check is holder-level (over-broad); tenant check reads a field the system views never carry | **MUST-FIX-BEFORE-FREEZE** |
| 2c | raw access checked for the first 40 refs only; other refs count as disclosures | MUST-FIX (latent at S, real at M/L) |
| 2d | extraction credits claims that name several entities when the distractors happen not to collide | **MUST-FIX** (with 1a) |
| 2e | scope = the task question only (loop questions under the same goal ignored); denominator; errors; `raw_authority.json` | OK, DISCLOSE the scope |
| 3 | class mix 40/10/10/8+7/10/10/10/5/10 = 120; the other 105 question tasks have derivable, unambiguous gold | OK (apart from 1a, 1b) |
| 4 | baseline fairness: `single` beats `source` only on the broken goal-only tasks; `source` is the fair primary | DISCLOSE + report `source` as primary |
| 5 | architecture gate: G3 is real; G7 harness-write, G4 authorization replay and G10 fault evidence are vacuous or circular | **MUST-FIX-BEFORE-FREEZE** (G7, G4, G10); DISCLOSE (G2, G8) |
| 6 | determinism: same seed gives byte-identical tasks and sources | OK; DISCLOSE that results depend on wave order |

Sensitivity, for information only and not a replacement metric: removing the 15 defective tasks (10 goal-only and 5
cross-tenant) gives system 64/105 (61.0 %), baseline-single 99/105, baseline-source 99/105. On the rest, the two baseline
variants are identical.

---

## 1. Leakage of gold, labels or roles into the system under test

**1a. Goal-only tasks (MUST-FIX).** `world.py:547` sets the goal scope to the project, and `world.py:554` sets `policy={}`
(no two-department requirement). The project's members are exactly the four holders that carry the observations, and the
asker (`proj.members[0]`) holds one of them, so the scope alone identifies the gold holders. Worse, at size S
`world.py:231-232` gives projects p0, p2 and p4 the same members (`t0-u00008, -09, -12, -13`), and p1 and p3 the same
members too. That holds for both tenants (checked with `generate(1,'S')`; at M the five member sets are distinct). The
goal-only observation templates (`GOAL_OBS_TEMPLATES`) are generic ("recurring … blocker … caused by"), so tasks dev-009,
dev-026 and dev-060 are the same question from the same person over the same holders, with three different golds. Evidence:

- dev-040 (gold `batchrouter`) also lists `cargoforge`, the gold of dev-064, among its options. All three runs score it as a tie, so wrong.
- Each of the 9 goal-only tasks the system got right, and each of the 9 that baseline-single got right, was credited from
  supported claims naming 2 to 5 services: every goal-only pattern of the project, or of the tenant for the baseline. They
  scored only because the other services were not among the 4 options (`rev4/accretion.py`, and the listing in §4).

The goal-only class (10 of the 40 positives) is therefore an option lottery. Fix:
1. Make project member sets disjoint (assert it at every size).
2. Give each goal-only pattern a distinguishing topic that the goal objective names (for example its context phrase).
3. With 2d, treat a claim that names more than one world entity as ambiguous.

**1b. Cross-tenant tasks (MUST-FIX).** `world.py:396-397` gives both tenants the same reserved context range
(`reserved_next = [60, 60]`). Tenant 0 hosts the cross-tenant patterns asked from tenant 1, and the reverse, so a tenant-0
asker who asks about context 60 or 61 (whose pattern lives in tenant 1) finds a real cross-department pattern with that
same context in their own tenant. `derive.py` over the final raw records shows:

| task | asker tenant | in-tenant pattern with the asked context | in-tenant service among the options? |
|---|---|---|---|
| dev-046 | t0 | none | — |
| dev-059 | t1 | `ledgersync`, 2 departments, 3 roots | no |
| dev-068 | t0 | `yardhub`, 2 departments, 3 roots | **yes** |
| dev-075 | t0 | `manifestgate`, 2 departments, 3 roots | no |
| dev-082 | t1 | `dockforge`, 2 departments, 3 roots | **yes** |

For dev-068 and dev-082 the gold `abstain` is wrong. Both baselines answered exactly `yardhub` and `dockforge` there and
were marked wrong (cross-tenant 3/5). The system abstained and was marked right, even though the answer is derivable in
its own tenant. For dev-059 and dev-075, `abstain` scores as correct only because of the option draw.

The options also come from the other tenant's entity list (`world.py:760`), and both tenants draw service names from one
140-name pool. As a result the forbidden markers are not unique: `manifestgate-service` is dev-082's forbidden marker and
also a real tenant-1 service (the gold of dev-036). The goal loop of dev-082 produced a supported claim naming it, from
tenant-1 data, with 0 forbidden-root references.

Fix:
1. Use disjoint reserved ranges per tenant, and assert that no context used by a cross-tenant pattern occurs in the asker's tenant.
2. Make the service names disjoint across tenants, or draw cross-tenant distractors from the asker's tenant.

**1c. Role carried by ids and titles (MUST-FIX, cheap).** `events.py:40` writes `"id": rec.rid`, and `Planner.rid`
(`world.py:429`) prefixes the id with the record's role:

| prefix | role | records in S1 sources |
|---|---|---|
| `o` | observation | 416 |
| `d` | coincidence decoy | 30 |
| `c` | forwarded copy | 22 |
| `f` | filler | 141 |
| `n` | private note | 20 |

Titles do the same:

| title | role | source line |
|---|---|---|
| "Field note" | observation | `world.py:506` |
| "Housekeeping" | coincidence decoy (30 records) | `world.py:706` |
| "Fwd: field note" | forwarded copy | `world.py:674` |
| "Notice" | filler | |
| "Personal note" | private note | |

Titles reach the asker's view: the baseline's dev-005 view lists 20 references titled "Field note". The deterministic
provider ignores these cues, but a live model sees them. Fix: hash the ids (for example `sha256(seed|rid)[:12]`) and draw
titles from one shared pool for every role.

**1d. Temporal class (DISCLOSE).** Only temporal tasks use the "currently / As of now" templates and carry
`valid_from_days=120` (`world.py:531`). From public fields the class separates perfectly (10/10, 0 false positives). The
superseded observations are 200–260 days old and the new ones 8–40 days old (`world.py:temporal`), so the 120-day window
does the supersession for both system and baseline. The class measures whether the window filter is honoured, not
temporal reasoning.

**1e. OK.** No question, goal title or objective contains the gold or any option name (120/120). Task ids are shuffled.
Options are not sent to the system (`issue.py:issue_task`). The gold file is written by `run.py` (through `GoldSink`) into
the run directory before the runtime starts, in the same process. The runtime never reads it, but this is part of the
§6 limitation; also, the `gold.py` docstring's claim that "nothing else imports this module" is false (`run.py` and
`baseline_central.py` import it).

## 2. Scorer (`score.py`)

**2a. Independent re-score (OK).** `rev4/rescore.py` does not import `score.py`. It applies the contract rule to the views,
the gold and `raw_authority.json`, and matches the stored per-task result exactly:

| run | correct | per-task differences |
|---|---:|---:|
| S1 | 78 | 0 |
| S1-baseline-single | 111 | 0 |
| S1-baseline-source | 107 | 0 |

The denominator is all 120 tasks. A missing view, error, timeout or unresolved question scores wrong, and `score_run`
raises on any mismatch between the task set and the gold.

**2b. Disclosure checks: one over-broad, one vacuous (MUST-FIX).**
- *Over-broad:* `score.py:365` marks a reference as foreign when its holder appears in `forbidden_holder_ids`. Those
  holders, such as department holders, also hold public records the asker may legitimately see. Evidence: the goal-level
  claims of dev-044 and dev-099 (denied) cite 3 references from forbidden holders each, with 0 forbidden roots. Had those
  claims been in the question view, both tasks would have been scored as leaks and the run would have failed compliance.
  Fix: count a reference as foreign only when its root is forbidden or its tenant differs, or when its holder is forbidden
  and it comes from the restricted source.
- *Vacuous:* `score.py:362` reads `view["tenant_id"]`, which `issue.collect_view` (`issue.py:73`) never writes. The
  cross-tenant reference check therefore never runs for system views; the baseline views do carry the field. Fix: write
  `tenant_id` in `collect_view`.

**2c. Raw-access checks capped at 40 references (MUST-FIX, latent).** `issue.py:119` checks raw access for the first 40
references only. In `score.py`, every other reference whose holder is not in the allowed set becomes `raw_unchecked` and
counts as a disclosure (`raw_checks_enabled=True`). The S1 maximum is 24 references per view, so nothing was affected, but
at M or L sizes this produces false leaks. Fix: check every reference, or exclude the unchecked ones.

**2d. Multi-entity claims (MUST-FIX with 1a).** Matching only against the K=4 options means a claim naming five services
is credited whenever the other four are not options. See 1a: 18 credited goal-only answers across the system and
baseline-single. Fix: the public task carries the world's entity vocabulary (not gold), and a supported claim naming ≥2
vocabulary entities counts as ambiguous, which the existing rule then scores as a tie.

**2e. OK, with disclosures.**
- `raw_authority.json` (from `baseline_central._authority`) is answer-free. It is computed from the post-run organization
  tables and the public asker only, using `can_view_raw_evidence`.
- Extraction scope is the task question's claims only. I checked `coord.db` (a copy) for every wrong question task: in
  none of them did a supported claim from another question under the same goal name the gold, so no correct answer was
  hidden. The scope does help the system on expected-abstain classes, though: in 17 question tasks, supported goal-loop
  claims name a non-gold option. Six of those tasks are expected-abstain (2 coincidence, 1 single-domain, 1 denied,
  2 cross-tenant), and all six would turn wrong if goal output were counted. Disclose this.
- Latent mismatch: `issue.RESOLVED` includes `investigated`, but `score.RESOLVED_STATUSES` (`score.py:45`) does not, so an
  `investigated` question would score as unresolved, hence wrong. S1 had none. Align the two lists.

## 3. Generator defects and gold correctness

- **Class mix: OK.** `TASK_MIX` and the per-class n in S1 give exactly 40/10/10/15 (8+7)/10/10/10/5/10 = 120. Goal-only = 10 of the 40.
- **Gold derivability (`rev4/derive.py`).** Method: take the final state of the records (edits applied, deletions removed,
  stale out-of-order versions dropped), count public records plus members-only records whose members include the asker,
  apply the validity window, and collapse forwarded copies onto the original's root. Result: all 105 question tasks other
  than the 5 cross-tenant ones have derivable, unambiguous gold.
  - Every positive has its gold service stated with the task's context in ≥2 departments with ≥2 roots, and covering the asked departments.
  - No other option is derivable.
  - No expected-abstain task (coincidence, single-domain, denied, common-origin copies) has a derivable option.
- **Defective tasks:** dev-068 and dev-082 have wrong gold; dev-059 and dev-075 are ambiguous (1b). All 10 goal-only
  tasks are ambiguous at S (1a). dev-040 is unwinnable for every run.

## 4. Baseline fairness

- **`single` is structurally different.** It sends one response per task (`baseline_central.py`, `variant == "single"`).
  `fake.evaluate_responses` therefore has no pair to compare: no disagreement is ever detected, the finding's references are
  all retained chunks, and the claim text is all excerpts joined together. The system and the `source` variant get
  pairwise agreement and disagreement checks.
- **Quantified on S1 correct positives:**

  | run | supported claims naming the gold | refs not stating the gold pattern | claims naming >1 service | claims with root count inflated by non-pattern refs |
  |---|---:|---:|---:|---:|
  | baseline-single | 71 | 38 of 261 | 9 | 9 |
  | baseline-source | 67 | 22 of 237 | 5 | 5 |

  Every case is a goal-only task. For question tasks the retrieval rule (≥2 shared tokens) keeps only chunks of the task's
  context, so no accretion occurred: 0 of the 62 question-task claims named a second service. Accretion inflated root
  counts (up to 12) but never flipped the 2-root minimum. The system accretes the same way inside each holder's 3-chunk
  response; all 27 of its supported multi-service claims naming the gold are in goal-only tasks.
- **The 111 vs 107 gap between the variants is exactly four goal-only tasks** (dev-026, dev-036, dev-060, dev-064), that
  is, the 1a defect.
- **Recommendation:** report `source` as the primary baseline. It caps each origin at 3 chunks and 10 origins, mirroring
  the system's 10 holders × 3, and runs the same pairwise evaluator. Report `single` as secondary.
- **Apples-to-apples?** Partly. The same in both:
  - raw files (byte copies)
  - `local_export` connector and pipeline
  - fake provider
  - commit gate and policy, including `min_independent_units`, with true origin holders
  - validity window
  - task set and ask order
  - evidence budget of 30

  Differences to disclose:
  - The baseline does not simulate replay or restart faults, so two fault tasks are easier for it.
  - The baseline answers each task statelessly. The system runs the 120 tasks in waves of 15 on shared state (S1: 358
    contested claims, 560 conflict edges by the end), so its results depend on wave composition and order.
  - Goal-only queries differ: the baseline uses the goal title plus objective, the system its loop's generic question.
  - Baseline raw checks are computed in-process with the API's rule, not through the API.

## 5. Architecture gate (`arch_gate.py`): mutation tests on scratch copies of S1's `coord.db` and holder stores

| copy | mutation | gate result | meaning |
|---|---|---|---|
| base | none | valid (reproduces `arch_gate.json`) | — |
| M1 | supported claim `support.independent_roots` set to 1 | **G3 fails** ("1 < policy 2", "1 != recomputed 8") | G3 is a real check |
| M2 | supported claim text rewritten to "TAMPERED by harness: zzzfake-service is the cause." | **valid** | harness writes go undetected |
| M3 | a routed holder's domains and published domains set to `legal.contracts`, outside its question's domains | **valid** | the authorization replay tolerates it |
| M4 | duplicate traces removed from every holder store, feed totals set to 0 | G10 fails, but only because of the `replay` flag in `sources_manifest` | no per-injection evidence check |

- **G7 (MUST-FIX).** The "harness never wrote" check rests on `harness_db_opens`, which `run.py` never writes. S1 reports
  `harness_opens_declared: 0`, so the check is vacuous, as M2 shows. The scorer reads views written by the harness, and
  nothing cross-checks those views against `coord.db`. (I checked by hand: the per-question claim counts in S1 views equal
  the database counts.) Fix: the gate compares every view claim's id, status, text and support with the `coord.db` row
  and its revision, and `run.py` records its database opens.
- **G4 (MUST-FIX).** Every replay denial with reason "no matching evidence domain" is tolerated as "domain drift"
  (`arch_gate.py:498`) without checking that drift happened. S1 has 99 such denials, all accepted, and M3 passes. Fix:
  compare against the domains recorded at route time in the audit detail, or run scored runs with `strict_domains`.
  DISCLOSE: the ranker assertion passes on any route ranked by the hypergraph (315 of 837 were; 522 were ranked by `domains`).
- **G10 (MUST-FIX).** The contract asks for "duplicates recorded as `applied_events.outcome='duplicate'`" and "an
  `ingest_queue` row leased twice". S1 meets neither yet passes. Evidence from the copies:
  - applied_events: 0 duplicate rows; queue rows leased more than once: 0.
  - Duplicate evidence comes from `ingest_stage_metrics` `enqueue/duplicate = 603`, which any re-sync produces.
  - The declared fault counts come from the system's own feed report, so the check is circular. With dedupe ablated, the
    feed reports 0 duplicates and the duplicate check never fires.
  - The restart check passes on `max_incr_checkpoint_version >= 2` (`arch_gate.py:841`), which `page_size=3` produces
    with or without a restart.
  - `deleted_markers_checked = 0`, because `run.py` writes no `fault_plan.json`.

  Fix: `run.py` writes a non-gold `fault_plan.json` (holder to fault kind, injected duplicate record ids, deleted texts as
  markers), and G10 checks each injected record.
- **G2 (DISCLOSE / cheap fix).** G2 checks distinct files, private records only with their owner, and no shared
  connector. It does not check that each source landed in the holder named in `sources_manifest`.
- **G8 (DISCLOSE).** Entity coverage is measured against the holders' own extracted entities, which is circular (104/104),
  not against gold entities as §B.7 states.
- **G1, G5, G6 and G9 look sound.** They were not mutation-tested.

## 6. Determinism and reproducibility (OK)

- Two independent builds with seed 1 at size S produce the same public-task sha256 (`262476928adf5f74…`, equal to S1's
  file), the same record-list hash and the same world fingerprint (`50dc7750…`). Seed 2 differs.
- Regenerating the sources with S1's `started_utc` gives 119/119 byte-identical files. The sources depend on the
  run-start clock only through absolute timestamps; ages are fixed.
- The task and world set is built before materialization and does not depend on runtime state; ids are a key map only.
- DISCLOSE: the system's score depends on wave size and order (shared state across tasks), which the frozen contract
  should fix (`--wave`, task order).

## Minimal pre-freeze fix list

1. `world.py:396-397`: disjoint reserved context ranges per tenant, plus an assertion that a cross-tenant context does not
   occur in the asker's tenant. Disjoint service-name pools per tenant, or cross-tenant distractors drawn from the asker's
   tenant (`world.py:760`).
2. `world.py:231-232, 547, 554`: disjoint project member sets, and goal-only objectives that name the pattern's topic.
3. `score.py` extraction: count a claim naming ≥2 world entities as ambiguous, with the entity vocabulary in the public
   file. `score.py:365`: foreign = forbidden root, or other tenant.
4. `issue.py:73,119`: write `tenant_id` and check every reference's raw access. Align the resolved-status lists.
5. `events.py:40` and `world.py` titles: opaque ids and role-independent titles.
6. `arch_gate.py`: view↔database cross-check (G7), verified domain drift (G4), and per-injection fault evidence from a
   `fault_plan.json` written by `run.py` (G10).
7. Re-run S1 (system plus both baseline variants) after 1–5, then freeze. Report `source` as the primary baseline.
