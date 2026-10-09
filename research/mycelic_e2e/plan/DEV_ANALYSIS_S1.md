# DEV run S1 — failure analysis (size S, seed 1, dev split)

Run dir: `scratchpad/runs/S1` (run_id `run_ad994c5f75c6`, provider `deterministic-provider`, fake.py sha `2ea34d23…`).
Score reproduced with `python -m research.mycelic_e2e.bench.score <run dir>`: **78/120 = 0.650**, Wilson [0.561, 0.729],
disclosures 0, errors `{'tie': 1}`. Per class: cross_domain 22/40, contradiction 4/10, temporal 5/10, common_origin_pos
1/8, fault 4/10; coincidence 10/10, common_origin_copies 7/7, single_domain 10/10, denied 10/10, cross_tenant 5/5.
Architecture gate G1–G10 all pass (`arch_gate.json`). Everything below was read from `coord.db` / holder `evidence.db`
(`mode=ro`), the asker views, the public task file and the dev gold.

Stage codes (from the brief): (a) ingestion/root, (b) holder not routed, (c) routed but no_evidence / wrong chunk, (d)
evaluate clustering, (e) gate, (f) extraction, (g) tie, (h) timeouts/budget/race, (i) generator/gold defect.

## 0. Headline

All 41 failed **question** tasks diverge first at the same place: **(b) the first question is routed to 10 of 15–17
authorized holders by a ranking that has no signal for the question's topic**, so one or more gold holders are ranked
out. `rank_method` was `domains` for every asker question (837 questions: 522 `domains`, 315 `hypergraph`, the latter all
loop children). The question text names the symptom phrase ("lunar ladder") and two department names, never a service,
and `hypergraph.entities_in_text` knows only services / tracker keys / "<x> regression" symptoms, so `entities=[]`,
`entity_hits=0` for every candidate. After the authorization filter every candidate has `domain_overlap=1` (or 0 with no
published domains) and the greedy loop in `inquiry/routing.py:rank_holders` breaks ties by `holder_id` (strict `>` over
`order = sorted(remaining)`), i.e. **first-N by holder id** — the behaviour the ranker was built to replace. Gold holders
with high ids (`hold_ca…`, `hold_d6…`, `hold_db…`, `hold_e7…`, `hold_ec…`, `hold_f7…` — the department memories) are the
ones that lose.

The recovery path exists and routes correctly — the blind verification question carries `target_entities` and is
ranked `hypergraph`; gold holders in the other departments received it in 31/34 cases and answered with the gold
record in 24/34 — but it is wasted at **(c)+(d)**: `fake.compose_verification_question` turns the finding into its
first five content words ("customer case summary ledgergate", "going through last week s", "team confirms X service")
and prefixes "Independently of any other **team** … your own **records** show …", so every holder's lexical answer rule
(≥2 shared tokens) returns unrelated records that use the same template or contain "team", and `evaluate_responses`
puts every response into a numeric disagreement (≥3 shared boilerplate tokens, different `{n}`) → **no findings** →
`verified.outcome = no_evidence` (53 of 71 verification questions run-wide; 6 confirmed, 11 contradicted, 1 mixed).
The two tasks that were rescued (dev-066, dev-067) are exactly the ones whose first five words happened to contain the
context phrase ("retro item gilded harbor comes").

The one remaining failure (dev-040, goal-only) is a **generator + answer-concatenation** defect, not a routing one.

## 1. Per-class table — failed tasks

Legend for the evidence column: `cands` = authorized candidates in the `question.route` audit, `gold routed k/n` = gold
holders among the 10 chosen, `U`/`u` = unit/user holder, `verif` = the child verification question's `verified.outcome`
and whether gold holders in other departments answered with the gold record.

### cross_domain (18 failed of 40; 10 goal-only tasks: 9 ok, 1 tie)

| task | first stage | evidence (one line) |
|---|---|---|
| dev-001 | (b) + (h) wave-1 blackout | cands=31 (domains wiped at 06:10:54, see D1), chosen incl. 4 holders with `base=0`; gold routed 0/3 → `no_evidence`, no claim, no verification |
| dev-006 | (b)→(e) | gold routed 2/3 but both in Release Engineering (U+u) → gate "span 1 department unit(s); the question requires 2" → hypothesis; verif no_evidence (1 gold routed, junk answers) |
| dev-008 | (b) | gold routed 1/3 (Treasury Planning memory) → 1 root → hypothesis; verif: 2 gold holders in other depts answered the gold record (16) but 8 answers → all in numeric disagreement → `no_evidence` |
| dev-010 | (b) | cands=17, gold routed 0/3 → `no_evidence` |
| dev-014 | (b)→(e) | gold 2/3 same dept (Platform Reliability U+u) → units rule → hypothesis; verif 0 gold routed |
| dev-018 | (b) | gold 1/3 → 1 root; verif: 2 gold answered correctly, `no_evidence` (9 junk answers) |
| dev-030 | (b) | gold 1/3; verif 2 gold answered correctly, `no_evidence` |
| dev-034 | (b) | gold 1/3; verif 2 gold answered correctly, `no_evidence` |
| dev-040 | (g)/(i) goal-only | 3 supported claims whose text is 3 concatenated excerpts naming batchrouter **and** cargoforge (both options, equal roots) → scorer tie; cargoforge is another genuine pattern of the same project |
| dev-041 | (b) | gold 1/3 (user holder); verif 1 gold answered correctly, `no_evidence` |
| dev-043 | (b)→(e) | gold 2/3 same dept → units rule; verif 1 gold routed, `no_evidence` |
| dev-079 | (b)→(e) | gold 2/3 same dept → units rule → hypothesis, then `contested` by a junk verification disagreement |
| dev-092 | (b)→(e) | gold 2/3 same dept; verif `contradicted` (junk) → claim `contested` |
| dev-103 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-112 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-114 | (b) | gold 1/3; verif 2 gold answered correctly, `no_evidence` |
| dev-118 | (b) | gold 1/3 (user, diversity slot); verif `contradicted` by junk → claim `contested` |
| dev-119 | (b)→(e) | gold 2/3 same dept (Rapid Response Cell U+u) → units rule; verif 1 gold routed, `no_evidence` |

### contradiction (6 failed of 10)

| task | first stage | evidence |
|---|---|---|
| dev-028 | (b) | gold 1/3 (user) → 1 root; verif 2 gold answered the surviving value, `no_evidence` |
| dev-061 | (b) | gold 1/3 → 1 root; verif 2 gold answered correctly, `no_evidence` |
| dev-083 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-104 | (b)→(e) | gold 1/3 + a copy from a non-gold holder: "2 roots, 1 copied reference(s) not counted, span 1 department" → hypothesis; verif `no_evidence` |
| dev-109 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-113 | (b)→(e) | gold 2/3 same dept (Treasury Planning U+u) → units rule; verif 1 gold routed, `no_evidence` |

The retraction/edit records themselves were ingested correctly (surviving value present as `active`, the retracted side
`revised`/absent in the holder DBs); the contradiction mechanics were never reached.

### temporal (5 failed of 10)

| task | first stage | evidence |
|---|---|---|
| dev-020 | (b) | gold 1/3 (user) → 1 root; verif 2 gold answered the current value, `no_evidence` |
| dev-037 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-042 | (b) | gold 1/3 (user); verif 2 gold answered correctly, `no_evidence` |
| dev-085 | (b) | gold 1/3 (user, diversity slot); verif `contradicted` by junk → `contested` |
| dev-093 | (b) | gold 1/3 (user); verif 2 gold routed, 1 answered correctly, `no_evidence` |

`valid_from` (120 days) was applied; superseded values did not reach the gate. Temporal gold is `U u u` (one unit
holder), so these tasks are the most exposed to the user-holder tie-break.

### common_origin_pos (7 failed of 8)

| task | first stage | evidence |
|---|---|---|
| dev-004 | (b) | gold 1/2 → 1 root; verif 1 gold routed, junk → `no_evidence` |
| dev-012 | (b) | gold 1/2; verif 1 gold answered correctly, `no_evidence` |
| dev-016 | (b) | gold 0/2 → `no_evidence` |
| dev-050 | (b) | gold 0/2 → `no_evidence` |
| dev-051 | (b) | gold 0/2 → `no_evidence` |
| dev-062 | (b) | gold 1/2; verif `contradicted` by junk → `contested` |
| dev-081 | (b) | gold 0/2 → `no_evidence` |

Both genuine roots live in the two department memories (`U U`); copies are elsewhere. The root-dedupe rule itself works
where it was exercised (dev-104: "1 copied reference(s) not counted").

### fault (6 failed of 10)

| task | first stage | evidence |
|---|---|---|
| dev-003 (restart_mid_ingest) | (b) + (h) blackout | all 4 gold records ingested and `active`; cands=30 (wave 1), gold 0/4 → `no_evidence` |
| dev-013 (delete) | (b)→(e) | 4 gold records active; gold 2/4 same dept → units rule; verif 1 gold answered correctly, `no_evidence` |
| dev-021 (out_of_order) | (b) | gold 1/4; one record correctly `revised v2`; verif 3 gold routed, junk → `no_evidence` |
| dev-031 (edit) | (b)→(e) | gold 2/4 same dept (edited record `revised v2`, fine); verif `no_evidence` |
| dev-090 (malformed) | (b) | gold 1/4; verif 3 gold answered correctly, `no_evidence` |
| dev-094 (duplicate) | (b)→(e) | gold 2/4 same dept; dedupe correct (17 duplicates run-wide); verif `no_evidence` |

No fault task failed at (a): every gold record is present with the right root and status in its holder DB (`G10` pass).

## 2. Counts per first-divergence stage (42 failures)

| stage | count | detail |
|---|---|---|
| (b) gold holder authorized but ranked out by `max_holders=10` | **41** | 0 gold routed → `no_evidence`: 7 (dev-001, 003, 010, 016, 050, 051, 081); 1 gold routed → 1 root: 24; 2 gold routed in one department → (e) units rule: 10 |
| ↳ of which (h) wave-1 domain blackout contributed | 2 | dev-001, dev-003 (cands 30–31, base-0 holders chosen) — see D1 |
| (g)/(i) goal-only tie | 1 | dev-040 — see D2 |
| (a) ingestion / root | 0 | all gold records present, roots and statuses as expected |
| (c)+(d) as the *second* loss (verification wasted) | 34 of 34 tasks that reached a verification | gold in other departments routed 31/34, answered correctly 24/34; findings=0 because of boilerplate matches — see H2 / D3 |
| (f) extraction wrong | 0 | every abstain in a failed task reflects "no supported claim"; extraction matched correctly where claims existed |

Passing positives, for calibration: 11 of the 22 passing cross_domain tasks got exactly two gold holders from two
departments on the first question (roots=2, units=2); 2 passed through a confirmed verification (dev-066, dev-067);
dev-002 passed in the blackout by chance (a `base=0` gold holder was chosen).

## 3. Expected-abstain classes — right answer, right reason?

| class | n | abstained for the intended rule | abstained because nothing relevant was routed | note |
|---|---|---|---|---|
| single_domain | 10 | 8 — gate reason "independent roots span 1 department unit(s); the question requires 2" (O3) | 2 (dev-019, dev-086: 1/3 gold routed → "1 root") | intended rule is load-bearing |
| coincidence | 10 | 5 — the gold holder answered, 1 root, no cluster with the unrelated statements → hypothesis | 5 (dev-058, 063, 071, 080, 084: 0/1 routed) | half are correct for the wrong reason |
| common_origin_copies | 7 | 0 — the "copied reference(s)" reason never appears; with ≤1 holder routed the dedupe rule was not exercised | 7 (0/1 or 1/1 routed, copies' holders never routed) | **correct for the wrong reason**; under H1/H5 these tasks will route to the copy holders, and the A2 ablation must then break them |
| denied | 10 | 10 — the forbidden source's holder *was* routed in 10/10 (holder-level authz allows it) and answered `no_evidence`: the record-level ACL (`EvidenceStore._allowed_memory_ids`) withheld the record; 0 foreign refs, raw checks all refused | — | intended mechanism, no leak |
| cross_tenant | 5 | 5 — tenant-B holders never candidates ("tenant mismatch" is checked first); 2 tasks committed unrelated tenant-A claims (hypothesis/contested) | — | intended |

Any routing fix must be re-checked against coincidence and copies-only: today they abstain largely because the gold
holder was not reached, not because the decoy rule fired.

## 4. Hypotheses, ranked by expected gain on the primary metric

### H1 — Route the first question by the symptom phrase: a content-free term index next to the entity index (largest gain)

* **Mechanism today**: `inquiry/routing.py:question_entities` → `knowledge/hypergraph.py:entities_in_text` (services,
  tracker keys, "<x> regression") → `entity_hits=0` for every asker question; `rank_holders` falls back to
  `domain_overlap + unit_diversity` with `holder_id` tie-break. Holder side: `evidence/service.py:_ingest_stats_sync`
  publishes `ingest.entities` (ids with ≥ `entity_min_records` records, cap 500) via `org.py:holder_heartbeat` into
  `entity_index` hyperedges.
* **One-factor change**: publish, through the same heartbeat/entity-index path, hashed rare content terms of the holder's
  public/members records (`term:<sha1[:12] of stemmed token>`, document frequency within the holder ≤ 3, cap 500 by
  rarity, same `entity_min_records`/ACL rules as entities — private sources excluded as today); accept the `term:` kind
  in `entities.PUBLISHABLE_ENTITY_KINDS` / `hg.is_publishable_entity`; in `question_entities` add `term:<hash>` for each
  stemmed content token of the question text (reuse `models/fake.content_tokens`-style stemming; drop department names
  listed in `candidate_domains`' display names). No change to weights: "lunar"+"ladder" give the 3 gold holders
  `entity_hits=2` (+6) against 1 for domain overlap.
* **Expected movement**: every question class. cross_domain 22→~36/40, contradiction 4→~9, temporal 5→~9,
  common_origin_pos 1→~7, fault 4→~9 (the first question then reaches all gold holders; the units rule is satisfied on
  the first commit without verification). Roughly +28 tasks (→ ~0.88). Implements O2(a) for arbitrary symptom wording,
  so it is the one change that also scales to M/L (where `budget.holders=10` of hundreds).
* **Must not break**: authz is untouched (`candidate_holders` runs first; `rank_holders` filters incidence to `allowed`);
  no asker-visible surface changes (term hashes appear only in the `question.route` audit `entities` list — keep that
  list hashed, never the token). Denied/cross-tenant: unaffected (holder set is the same, only its order changes).
  coincidence: the gold holder will now be routed in 10/10 — its single statement stays 1 root → still abstain, but
  verify. copies-only: the copy holders will now be routed → the claim gets refs from 2 holders of 1 root → the gate
  must say "copied reference(s) not counted" → still abstain; this is the first time A2 is actually exercised.
  Keep G8's "entity coverage" counting entities only, or extend it to terms explicitly.

### H2 — Blind verification question names the finding's entity instead of its first five words

* **Mechanism today**: `discovery/engine.py:_ask_verification` → `models/fake.py:compose_verification_question`:
  `content_words(finding_text, drop_numbers=True)[:5]` + preamble "Independently of any other team: what do your own
  records show about … ? Include dates." Holder answers need ≥2 shared tokens (`fake.answer_from_evidence`); every
  dev record with the same template ("Customer case summary: … explains …", "Our team confirms …") qualifies, and
  `evaluate_responses` (≥3 shared tokens → compare numbers) marks all of them as disagreements → `findings=[]` →
  `verified.outcome=no_evidence` → `revise_claim(... "returned no independent evidence")`.
* **One-factor change**: `_ask_verification` already computes `target_entities` via `_claim_entities`; pass their display
  names (`ledgergate-service`) as `entities` to the task, and let `compose_verification_question` use
  `", ".join(entities)` as the topic when present (fallback to today's words); shorten the preamble to words that no
  observation template uses: "What do your own notes show about {topic}? Include dates." (drop "team", "records",
  "independently"). Blindness is preserved (no number, no polarity; the entity is already in the text today whenever it
  is among the first five words).
* **Expected movement**: without H1, rescues the 24 "1 gold routed" + 10 "2 gold, 1 department" tasks whose
  verification reached gold holders in the other department (31/34): ≈ +25 tasks. With H1 in place: +3–5 (tasks where
  a user gold holder is still ranked out). Also the mechanism that keeps M/L viable.
* **Must not break**: verification must still exclude supporters (`exclude_holder_ids`) and must not name the number.
  Watch `_agrees` in `engine.py`: a response that concatenates two excerpts with different numbers (temporal: old+new
  value in one holder) would read as "disagree" and open a conflict on a correct claim — pair with H3 or limit
  `answer_from_evidence` to one excerpt for verification questions. Check contradiction tasks still produce the conflict
  on the retracted side (they do today at the first question only when both sides are routed).

### H3 — One excerpt per holder answer (or evaluate per reference), not three concatenated

* **Mechanism today**: `models/fake.py:answer_from_evidence` joins up to 3 matching excerpts into one `answer`; the
  claim text is the longest member of a cluster, so a claim can name several entities, and `numbers()` of a
  concatenation is a set of several numbers → spurious numeric disagreement in `evaluate_responses` and in
  `engine._agrees`.
* **One-factor change**: `kept` limited to the single best excerpt (highest shared-token count, ties by observed_at
  desc), or the coordinator splits a response into one evaluation unit per `evidence_ref_id` (`handle_response` keeps the
  refs; `evaluate` builds `responses` per ref). The provider sha changes → new provider label in the ledger.
* **Expected movement**: +1 (dev-040) directly; removes the main false-disagreement source for H2 and for goal-only
  tasks. Independent-support correctness (6/36 today, goal-only claims report 8–12 roots because three patterns are
  merged into one claim) becomes measurable.
* **Must not break**: goal-only positives that currently pass with roots 8–12 (dev-005, 007, 009, 026, 033, 036, 060,
  064, 102) must still pass: each pattern becomes its own supported claim; the scorer then picks the option with the most
  roots — that is only unambiguous if D2 is fixed (decoys must not be other genuine patterns).

### H4 — Tie-break in `rank_holders` by evidence mass, not by holder id (cheap partial substitute for H1)

* **Mechanism today**: `routing.py:rank_holders` greedy loop: `adj > best_adj` strict, iteration over `sorted(remaining)`
  → lowest `holder_id` wins every tie; a department memory with 40 records in the domain ranks equal to a user with 5.
* **One-factor change**: add `W_RECORDS * min(1.0, records_in_wanted_domains / 20)` to `base`, taking the per-domain
  counts the heartbeat already reports (`stats.ingest.domains` — expose them in `org.routing_holders` as
  `published_domain_counts` so the routing view stays stats-free), or at minimum `+0.5` for `owner_type == "unit"`.
* **Expected movement**: gold is `U U u` (cross_domain, contradiction, fault `U U u u`, common_origin `U U`): both
  department memories get routed → first-question support from 2 departments. +15–20 tasks at S (temporal `U u u` only
  partially). Does not scale to M/L on its own (every department has many unit holders), so it is a fallback if H1
  slips, and a sane tie-break to keep under H1.
* **Must not break**: single_domain still abstains by the units rule; denied/cross-tenant unchanged.

### H5 — Stop treating an empty heartbeat snapshot as "all domains purged" (see D1; a correctness fix, not an accuracy gain)

* `org.py:holder_heartbeat`: `published = … if auto else []` then "replaced, not accumulated": an `ingest.domains == {}`
  report with `records > 0` (or a `dormant` beat) removes every published domain and, in the entity branch, every
  entity-index edge, and republishes them on the next beat. Guard: when the snapshot reports records but no domains
  (or is marked `dormant`), keep the previous publication. Runner: after `worker.start()`, wait until two consecutive
  beats per holder report the same non-empty set before issuing wave 1.

## 5. Harness / generator / scorer defects — fix before the contract freezes (not accuracy gains)

**D1 — Domain/entity publication blackout during wave 1 (harness + runtime race).** `audit_log`: 168
`holder.domains_published` rows; publication at 06:10:49–53, then at **06:10:54 all 56 holders** get
`{"removed": [<their domain>]}` and `holder.entities_published {"published": 0}`, re-published 06:10:55–57. The runner
logged "routable domains published by 56/56" at 06:10:54 and started the worker; wave-1 questions were routed
06:10:54–06:11:01 (`question.route` audit) with 30–31 candidates (holders with no domains pass `_can_route`) and
`base=0` holders chosen (dev-001: 4 of 10 chosen had `domain_overlap=0` although their current `published_domains`
match). G4's "99 replay denials (domain drift)" are the same event. Affected: dev-000…dev-014 (9/12 question tasks in
wave 1 failed vs 2–7/15 in later waves). Fix as H5 plus a runner stability check; re-run before any ledger row is
compared.

**D2 — Goal-only tasks are ambiguous by construction (dev-040).** `world.py:cross_domain(goal_only=True)` places the
pattern in a project's members and the loop asks "What recurring operational blockers related to <family> …"; the
answer legitimately contains every goal-only pattern of that family/project, and `options_for` draws decoys from the
tenant's services, which include those patterns' gold entities (dev-040 options: ledgergate, dockforge,
**batchrouter**, **cargoforge** — cargoforge is dev-064's gold, same project). With H3 the extraction becomes a roots
comparison between two genuine patterns (ties → wrong). Fix in the generator: decoy options of a goal-only task must
exclude entities of any pattern whose records are reachable from the same goal scope; add a test.

**D3 — Lexical contract has a gap.** `templates_dev.py` guarantees ≥2 shared tokens question↔record and ≥3 between
agreeing records, but observation templates share 5–8 boilerplate tokens *across different patterns* ("customer case
summary … explains … counted … affected accounts"), so any two records of the same template with different `{n}` are a
numeric disagreement for `fake.evaluate_responses`; "Our team confirms …" and "… for the team …" share "team" with the
verification preamble. This is invisible as long as only the asker question is answered (its tokens are the ctx words),
and fatal for every loop-composed question. Either the template bank must keep cross-pattern overlap < 3 content tokens
(and the holdout bank must be checked by the same predicate — do not open it, extend the existing predicate test), or
the deterministic evaluator must compare numbers only between responses that mention the same entity id. Decide and
record in BENCHMARK_CONTRACT §2 before freeze; H2/H3 reduce the exposure but do not remove it.

**D4 — Routing audit is not self-explanatory.** `question.route.detail.scores` lists only the chosen holders; the
ranked-out candidates' parts had to be reconstructed. Add the top `2 × max_holders` candidates with their parts, and
the tie-break rule, to the detail (G4 can then assert "no candidate with a strictly higher base was left out").

**D5 — Scorer / view nits (no score impact in S1).** `latency_s` is computed from second-resolution timestamps (all
1.0); the asker view carries only the task question's claims, which is right for O1 but means a loop follow-up that
commits the answer under a child question is invisible to extraction — acceptable, but write it into the contract.
`independent_support_correctness` (6/36) is dominated by concatenated goal-only claims (H3/D2) and by 2-holder claims
against `genuine_roots=3`; keep it as a supporting metric only.

## 6. Suggested order for the overnight

1. D1/H5 (race) and D2 (generator) — otherwise every later comparison carries wave-1 noise and a built-in tie.
2. H1 (term index) — one factor, re-run S1; expect ≥0.85. Record the `question.route` `rank_method` distribution: asker
   questions must now show `hypergraph`.
3. H2 (+H3 for the concatenation) — one factor on top; expect the remaining 1-gold-routed cases to close and
   `verified.outcome` to flip from `no_evidence` to `confirmed` for most children.
4. H4 only if H1 is not ready; D3 decision written into the contract either way.
5. Re-run the abstain classes after each step and look at *reasons* (single_domain "span 1 department", copies-only
   "copied reference(s) not counted"), not just the abstain count.
