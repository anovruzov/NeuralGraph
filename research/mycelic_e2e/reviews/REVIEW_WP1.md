# REVIEW_WP1: commit `dafe1af` (hypergraph, ranked routing, min_independent_units)

Reviewer: REVIEWER-2 (model id `claude-opus-5-5`), 2026-10-09. I did not write this code.
Spec: PLAN_v1 §C, §D WP1, §F (O2, O3).

**Verdict: ACCEPT-WITH-FIXES.** The fixes marked BLOCKING must land before any run is reported as "hypergraph". F10 blocks
only the L (10k users) scale.

The commit is sound in several places. The suite is green. Authorization still runs first and is unchanged. Holder
envelopes do not carry `target_entities`. Commit, conflict, discovery and heartbeat edges are written inside the existing
transactions. Ranking is deterministic.

It also has three groups of defects:
- **Privacy:** entity ids from members-only sources are published to the coordinator, and the default publication threshold
  is 1 record (a deviation from the spec). Routed holder owners can read the blind-verification targets. Entity ids taken
  from purged claim text are kept.
- **Invariants:** I4 can fail in recompute, there are windows where I2/I4 do not hold, and `hypergraph-rebuild` erases the
  I3 history.
- **Cost:** each entity-index heartbeat costs O(H), so a cold start costs O(H²).

Every finding below has a failing probe and the probe's output.

## 0. How this was reproduced

```
SCR=/tmp/claude-0/-home-user-NeuralGraph/cb55ecbb-9144-58cb-a5af-0b006a90960a/scratchpad
mkdir -p $SCR/rv && git -C /home/user/ng-impl archive dafe1af | tar -x -C $SCR/rv
cd $SCR/rv && python -m pytest mycelic/tests -q -p no:warnings -x
→ 411 passed, 7 skipped in 96.14s
```
This matches the engineer's report (411 passed, 7 skipped).

I wrote adversarial probes in the scratch copy only: `$SCR/rv/mycelic/tests/test_review_wp1.py` (R1–R10) and
`$SCR/rv/mycelic/tests/test_review_perf.py`. Each probe asserts the *correct* behaviour, so a failure is a finding.
```
python -m pytest mycelic/tests/test_review_wp1.py -q -p no:warnings -s   → 10 failed (R1..R10), outputs quoted below
python -m pytest mycelic/tests/test_review_perf.py -q -p no:warnings -s  → 1 passed (measurement only)
```

## 1. The new tests: what each one actually asserts

| test | asserts | assessment |
|---|---|---|
| `test_hypergraph.py::test_i1_ids_are_stable_and_tenant_scoped` | edge_id = sha(tenant,kind,anchor,version); members carry tenant; a discovery edge naming another tenant's claim raises and the edge count is unchanged | meaningful |
| `::test_commit_claim_writes_edges_in_same_tx` | `_revision_sync` monkeypatched to raise → 0 claims, 0 edges, 0 members; then actor and reason recorded | meaningful (this is the §D crash test) |
| `::test_i8_replay_is_safe` | replay with the same key writes no row; the writer called twice adds nothing | meaningful but narrow |
| `::test_i3_i4_versions_supersession_and_legacy_consistency` | v1 superseded → v2…v4 through recompute, `on_evidence_event` and `supersede_changed_support`; retract keeps the members | meaningful. It relies on `verify_consistency`, which compares the edge with `claim_evidence`/`compute_support(stored refs)` and **never with `claims.support`**, so it cannot see R1 |
| `::test_i5_contradiction` | conflict edge sides; I5 detector on a hand-forced status | line 198 parses as `(A and B) or C`, which weakens the assertion; otherwise fine |
| `::test_i6_traversal_is_bounded` | hop bound, node bound, retracted not followed | meaningful; does not bound edge members (R9) |
| `::test_traverse_respects_authz` | other-department employee sees no edge; entity_index edges show only routable holders | meaningful |
| `::test_rebuild_matches_live` | rebuild fingerprint = live; idempotent; entity_index untouched | compares active and retracted edges only, so the loss of superseded history goes undetected (R10); the from-nothing case compares only `(status, independent_roots)` |
| `::test_entity_index_from_heartbeats` | disallowed kinds and freemail dropped; versioning; withdrawal; auto_entities off; counts-only audit | meaningful |
| `::test_holder_publishes_only_public_and_members_entities` | private and restricted records never publish; **members records do** | line 361 parses as `A and B and C or D` (weak). It writes the spec deviation (R8) into the test as expected behaviour |
| `::test_holder_entities_skip_personal_domains_and_suspicious_records` | personal domain excluded; suspicious excluded **only `if flagged:`** | conditional assertion. I checked it fires in this run (`FLAGGED 1`), but it is fragile |
| `::test_min_independent_units_needs_two_departments` | 3 roots in one department → hypothesis with reason; 2 departments → supported; a later-observed copy does not add a department; recompute applies the rule | meaningful; does not cover tied `observed_at` (R3) |
| `::test_min_independent_units_flows_from_goal_and_question` | goal → question inheritance, a stricter question wins, invalid level → ValueError, gate enforces | meaningful |
| `test_routing_rank.py::test_top_n_spans_departments` | with 3 slots, departments 1 and 4 (the holders of the entity) are both chosen; the order does not depend on input order; `target_entities` routes like text; domain-only mode spreads | meaningful (6 departments and 3 slots, against the spec's 2 departments and 4 slots; acceptable) |
| `::test_verification_routes_away_from_support` | the supporter ranks below independent holders; `root_sharing_holders` is populated | meaningful |
| `::test_rank_detail_in_audit_and_authorization_first` | a non-routable holder with weight 50 is not routed; `rank_method` and `scores` are in the audit | meaningful |
| `::test_verification_question_carries_the_findings_entities` | the blind question text lacks `lgx`; `target_entities` routes to department 2 | meaningful |

None of the tests is purely tautological. The weak spots are the two precedence-weakened assertions, the conditional
assertion, and a consistency checker that is blind to `claims.support`.

## 2. Invariants: violations in production paths the tests do not cover

**R1 (I4): edge `independent_roots` ≠ `claims.support.independent_roots` after recompute.** This is a BLOCKING fix.
- Cause: `recompute_status` computes support from the gate's *effective* refs, where a revoked or no-longer-routable holder
  is demoted to `context`. The edge is synced from the *stored* roles instead: `hypergraph.py:258-265` `sync_support_sync`
  → `claim_refs_sync`.
- Probe: commit a claim with 2 roots, revoke one holder (`org.update_holder(status="revoked")`), then call
  `recompute_status`. Output:

  `R1 claim.status hypothesis claims.support.independent_roots 1 edge.independent_roots 2 verify_consistency []`

- So the edge still says 2 roots, and still lists the revoked holder as `held_by`, while the claim says 1.
  `verify_consistency` (`hypergraph.py:659-663`) reports nothing. Spec C.2 says "evidence_ref with the **gate's effective
  role**", and I4 says the edge's `independent_roots` equals `claims.support.independent_roots`.

**R2 (I2/I3/I4): claim_evidence and evidence status change in transactions that do not touch the edge.** This is a BLOCKING fix.
- Where: `discovery/engine.py:755-759` (verification agrees) and `:1113-1117` (late response) insert `claim_evidence` in
  their own transaction. The edge is synced later, inside `recompute_status`.
- Probe (the engine's exact transaction):

  `R2 verify_consistency after the engine's own tx: ['I4: … evidence members differ from claim_evidence', 'I4: … source_root members differ …', 'I4: … independent_roots 1 != 2']`

- Crash consequence: a crash between the two transactions is never repaired. The replay finds the `der_verify_{qid}`
  marker (`engine.py:745-749`), sets `outcome="replayed"` and skips the recompute, so the edge stays inconsistent
  indefinitely. The `claims.support` staleness on that path predates this commit, but I4 now makes it a contract violation.
- Same class: `knowledge/service.py:565-613`. `on_evidence_event` changes `evidence_refs.status` in transaction 1. The
  edge follows only in later `revise_claim` / `_sync_support_edge` transactions.

**R10 (I3): `hypergraph-rebuild` on a live coordinator deletes the version history.** This is a BLOCKING fix.
- Where: `hypergraph.py:582-587` deletes every support/lineage/conflict/discovery edge and re-inserts version 1. The CLI
  (`__main__.py:141-166`) runs this in place on `coord.db`.
- Probe output:

  `R10 support versions before rebuild: [(1, 'superseded'), (2, 'active')]  after: [(1, 'active')]`

- This breaks I3 ("nothing is deleted"). C.8 intended the rebuild to run "into a scratch DB and diff against the live
  edges".

**R6 (retention after deletion): a purged claim keeps the entity ids extracted from its text.** This is a BLOCKING fix.
- Where: `knowledge/service.py:653` `_scrub_claim_text` replaces the text and cleans revisions, discoveries, events and
  audit, but leaves the support and discovery edge `entity/about` members alone. `rebuild_sync` then *carries them
  forward* (`hypergraph.py:578-581`).
- Probe output:

  `R6 claim text now: [removed: source deleted]  entities before: ['org:target-corp.example', 'service:project-falcon']  after purge: [same]`

**R7: a revoked holder stays in the entity index.**
- Neither `org.py:499` `update_holder(status="revoked")` nor turning `auto_entities` off at the coordinator touches the
  index. Withdrawal happens only on the holder's next heartbeat, which a revoked or offline holder never sends.
- Probe output: `R7 holders on symptom:timeout after revoke: {'hold_…': 3.0}`
- Routing is unaffected, because `can_route` filters revoked holders. Counts, the registry and `tenant_known_keys` still
  include the holder.

**R9 (I6): `traverse` caps nodes but returns every member of every reached edge.**
- Where: `hypergraph.py:528-537`.
- Probe output: `R9 nodes: 3  members returned inside edges: 19  truncated: True` (`max_nodes=3`).
- An entity edge held by 10k holders is returned in full. For a non-system principal, `_visible_index_members` also runs
  one `get_holder` plus one `can_route` per member. `traverse` is not exposed by any API route
  (`grep -rn traverse mycelic/api` → 0 hits), so this is low severity.

**I1:** `_assert_tenant` (`hypergraph.py:70-83`) is correct for the id types it checks. Ids that are not found pass
silently, and `source_root`/`entity`/`domain` ids are tenant-free by design. I found no cross-tenant path:
`incidence`, `support_context` and `traverse` all filter on `tenant_id`.

**I8:** the replay returns before any write (`service.py:174-176`), and commit edges share the claim's transaction, so the
"repair by replay" case cannot arise on the commit path. The idempotency lookup still runs outside the transaction, a race
that predates this commit.

**Latent:** `supersede_support_sync` with no *active* edge computes `version=1` (`hypergraph.py:249`). If the claim's v1
edge is `retracted`, `INSERT OR IGNORE` drops the new edge and adds members to the retracted one. Today this is guarded by
`sync_support_sync`'s retracted check (`:261`); no un-retract path exists yet.

## 3. Authorization and leakage

- **`can_route` is unchanged.** `authz.py` is not in the diff, and `candidate_holders` is untouched. `route` still calls
  it first (`inquiry/service.py:381`).
- **`rank_holders` never adds a holder that `candidate_holders` rejected.** The pool is a slice of `candidates`
  (`routing.py:93`), and every chosen holder comes from that pool (`:104-122`). The probe test
  `test_rank_detail_in_audit_and_authorization_first` also shows a weight-50 non-routable holder is not routed.
- **Router audit detail.** `/api/admin/audit` is admin-only (`routes_admin.py:155-158`).
  - `scores` covers only chosen, authorized holders.
  - `ranking.entity_holders` = `len(hits)` counts **all** holders in the tenant, including unauthorized ones
    (`routing.py:124`). Probe output: `R4 entity_holders in audit detail: 3  authorized holders with the entity: 2`. This
    is the "no inference from counts" rule of I7. Low severity because only admins can read it.
- **Blind verification (R5).** This is a BLOCKING fix.
  - The holder envelope correctly omits `target_entities` (`inquiry/service.py:457-461`).
  - However, `QuestionService.can_view` grants routed holder owners access (`:206-214`), and `view()` returns the raw row
    (`:119-120`), which now includes `target_entities`.
  - Probe output: `R5 full_view: False  target_entities seen by routed owner: ['issue:tracker:lgx-412']`.
  - The same probe shows an older leak on the same screen: the `motivating_lineage` labels carry the claim text
    (`'Deploys cause customer timeouts, tracked as issue:tracker:lgx-412'`, `view()` lines 137-139). File it separately.
    WP1 must not add a second channel.
- **Entity publication from members-only sources (R8).** This is a BLOCKING fix.
  - Spec C.5 allows `members` records only when the source's audience covers the owning unit ("members-with-audience-of-the-owning-unit").
    `evidence/service.py:982` publishes every `members` record, with no check on the member list.
  - Probe setup: a source with `member_ids=["usr_ana","usr_bo"]` mentions checkout-service once. The probe heartbeats that
    map to user u1b's holder. The dept-1 lead, who is not a member of that source, asks a question with
    `target_entities=["service:checkout"]` and `budget.holders=1`.
  - Probe output:

    `R8 published from members-only source: {'service:checkout': 1, 'symptom:timeout': 1}  lead in members: False  probe routes: ['hold_cdaa…']  control routes: ['hold_25c9…']  target holder: hold_cdaa…`

  - The asker's own question view (`routes`) is therefore an oracle for "which holder's members-only channel mentions X".
    The same works without `target_entities`, by naming `checkout-service` in the question text.
- **Engineer's deviation: entity threshold 1 instead of the domain threshold 5 (`evidence/service.py:963-971`). REJECT
  the deviation as a default.**
  - The spec says to reuse the domain threshold.
  - At 1, any single message's ids are published, including `org:<email domain>` and `issue:` keys. That gives the index
    no k-anonymity and makes the R8 oracle exact to one record.
  - The engineer's rationale ("an entity a department mentions twice routes there") is a benchmark-recall argument, not a
    product one. §D also forbids production defaults that weaken controls.
  - Acceptable fix: the default is `min_records_to_publish` (5). The owner may lower it with `entity_min_records`, which is
    already validated and audited. The benchmark declares any lowered value in BENCHMARK_CONTRACT.md. If O2(a) recall at
    5 turns out too low, that is a measured contract decision, not a code default.

## 4. O3 `min_independent_units`

- **Gate and recompute agree.** Both call `required_units` + `CommitGate.independent_units` + `units_shortfall` on effective
  refs (`gate.py:255-284`, `service.py:488-506`). In production the loop always sets `candidate.question_id = q` together
  with `question=q` (`engine.py:650-662`), so recompute reads the same stored policy.
- **Edge cases handled correctly:**
  - A later-observed forwarded copy in another department does not add a unit (engineer's test).
  - A unit holder resolves to its own unit's department ancestor.
  - A user resolves to their primary non-project, active, non-archived membership. The Python path
    (`org.memberships_for_user`, `org.py:302-305`) and the SQL path (`units.py:holder_units_sync`) filter identically.
  - Holders with no department, and project-only users, count toward no department.
- **R3: attribution of a root held in two departments depends on `ref_id` string order when `observed_at` ties.** This is a
  BLOCKING fix for O3.
  - Where: `gate.py:89`. A tie is normal when two holders sync the same provider object, because `observed_at` is the
    document time and `ref_id` comes from `new_ref_id()`.
  - Probe: same evidence, only the dept-2 copy's ref id differs. Output:

    `R3 ref 'z9' -> ('hypothesis', {'department': 1})   ref 'a0' -> ('supported', {'department': 2})`

  - A single-department decoy can therefore pass the cross-department rule by chance.
- **Fail-open:** `required_units` swallows `ValueError` and returns `{}` (`gate.py:66-74`). An invalid *tenant*
  `min_independent_units` therefore disables a valid *question* requirement:
  `required_units({'min_independent_units': {'dept': 2}}, {'policy': {'min_independent_units': {'department': 2}}}) → {}`.
- **Goal policy is not validated at goal creation** (`routes_goals.py:48-55`). An invalid goal policy makes every question
  under that goal fail at create time instead of being rejected with a 400 on the goal.

## 5. Routing

- **Deterministic.**
  - The greedy pick uses strict `>` over `sorted(holder_id)`, and the base sort is `(-score, holder_id)` (`routing.py:93-117`).
  - The engineer's test checks reversed input.
  - My perf probe gives 10 distinct department groups for 10 slots out of 1,500 candidates.
- **Prefers entity-incident holders across departments: yes.** Score: entity hit +3, then +1 for the first holder of each
  department.
- **Side effect:** holders with no resolvable unit get the bucket `holder:{id}` (`routing.py:102`). Each of them therefore
  earns the +1 diversity bonus, so unit-less and project-only holders are systematically preferred over department members
  when base scores tie. Minor. Recommend a single shared `"unassigned"` bucket.
- **Verification routes away from supporters:**
  - Upstream: `exclude_holder_ids` (`engine.py:204,557`).
  - In the ranker: −2 for supporters and −1 for holders sharing a root.
  - The penalty can be outweighed when supporters are not excluded upstream. A supporter with 2 entity hits scores
    6−2−1=3 (+1 diversity), and an independent holder with no hits scores 1+1. That is acceptable under the spec's weights.
- **Blind verification:**
  - `target_entities` never reaches holders, either through the envelope or through `_blind_leak` text, which checks text
    only.
  - It does reach routed holder owners through the API (R5).
  - Routing itself reveals only "you hold E", which is inherent to O2(b).

## 6. Performance (measured, `test_review_perf.py`; 1,500 user holders, 10 departments, 2 shared entities + 1 unique each)

```
PERF H=1500 heartbeat_ms_at_n={10: 2.5, 100: 5.7, 500: 37.3, 1000: 41.6, 1500: 106.1} total_s=66.8 member_rows=10542 entity_edges=4500 one_drop_change_ms=48.8
PERF candidates=1500 candidate_holders_s=0.12 rank_holders_s=0.029 method=hypergraph
```
- **Rank cost per question is fine.**
  - `incidence` runs one indexed query per 300 entities plus one member query per 400 edges.
  - `rank_holders` is O(C log C + max_holders·C): 29 ms at C=1,500.
  - The incidence edge payload carries every holder of each entity, so for a very common entity it is O(H) per question.
    That is acceptable for now.
- **`entity_index` rewrite cost is O(H) per (holder, entity) membership change** (`hypergraph.py:383-406`). The change
  re-reads all members, runs `holder_units_sync` over all H holders, re-inserts H+units+domains rows, and deletes an old
  version.
  - Per-heartbeat latency grows linearly: 2.5 → 106 ms from 10 to 1,500 holders for 2 shared entities. The total cold
    start is O(H²): 66.8 s of single-writer time at 1,500 holders.
  - Extrapolation (quadratic, not measured): ≈ 50 min at 10,000 holders. A heartbeat that changes k common entities holds
    the coordinator's write lock for ≈ k × 0.7 s.
  - `hyperedges` grows by one row per holder-set change, forever: 4,500 edge rows for 3 entities × 1,500 holders. Members
    of old versions are deleted (an I3 deviation the engineer documented for the index), but edge rows are not.
- **S/M: acceptable. L: BLOCKING (F10).**

## 7. Required fixes

1. **F1 (R1, BLOCKING).** Make the support edge follow the gate's effective refs.
   - `knowledge/service.py:497-516`: `recompute_status` passes its effective `refs` and `support` to `revise_claim`.
     `revise_claim` (`:264`) passes them to `hg.sync_support_sync(..., refs=, support=)`. `sync_support_sync`
     (`hypergraph.py:258-265`) then uses them instead of `claim_refs_sync`.
   - `_sync_support_edge` (`:519-523`) gets the same change.
   - `verify_consistency` (`hypergraph.py:662`) also compares `edge.independent_roots` with `claims.support.independent_roots`.
   - Add R1 as a test.
2. **F2 (R2, BLOCKING).** Write the edge in the same transaction as `claim_evidence` and evidence status.
   - In `discovery/engine.py:755-759` and `:1113-1117`, call `hg.sync_support_sync(c, claim_id=…)` inside the same
     `db.tx()` after the inserts. Better: route both through one `KnowledgeService.attach_evidence_sync`.
   - In `knowledge/service.py:565-613`, call `hg.sync_support_sync(c, …)` for each claim the changed refs support, inside
     that transaction.
3. **F3 (R10, BLOCKING).** Stop `hypergraph-rebuild` from destroying history.
   - The default mode writes edges only for anchors that have none: the migration case.
   - The full regeneration in `rebuild_sync` (`hypergraph.py:582-587`) runs into a scratch copy and diffs, per C.8, or
     only behind an explicit `--regenerate` flag that keeps superseded rows.
4. **F4 (R6, BLOCKING).** In `knowledge/service.py:653` `_scrub_claim_text`, delete the claim's `entity` members from all
   its support-edge versions and recompute the discovery edges' `about` members. In `rebuild_sync` (`hypergraph.py:594`),
   skip the carry-over for claims whose text is `DELETED_TEXT`.
5. **F5 (R8, BLOCKING).** In `evidence/service.py:982`, publish `members` records only when the source audience covers
   the owning unit, as C.5 requires. Otherwise, in v1, publish `public` records only.
6. **F6 (threshold, BLOCKING).** In `evidence/service.py:963-971`, the default `entity_min_records` becomes
   `min_records_to_publish` (5). Any lower value used by the benchmark is declared in BENCHMARK_CONTRACT.md.
7. **F7 (R5, BLOCKING).** In `inquiry/service.py:119` `view()`, drop `target_entities` (and `trigger.claim_id`) when the
   principal has no `_full_view`. Also open a separate ticket for the existing `motivating_lineage` label leak at
   `:137-139`.
8. **F8 (R3, BLOCKING for O3).** In `knowledge/gate.py:84-96`, attribute a root whose refs span several units at the
   level, with no unique earliest `observed_at`, conservatively. Prefer a unit already counted by other roots; otherwise
   take the smallest unit id. Never decide by `ref_id`. Add R3 as a test.
9. **F9.** In `knowledge/gate.py:66-74`, normalize each requirement separately, so an invalid tenant value cannot erase a
   valid question value; log the invalid tenant policy. Validate the goal `policy` at `routes_goals.py:48-55` with
   `normalize_requirement` → 400.
10. **F10 (blocks L only).** Make the `entity_index` update incremental (`hypergraph.py:383-406`).
    - Insert or delete only the changing holder's member row.
    - Adjust the unit and domain counts in place.
    - Version the edge on a time bucket instead of on every holder-set change.
    - Measure again at 10k holders.
11. **F11 (minor).**
    - `routing.py:124`: count `entity_holders` over candidates only.
    - `routing.py:102`: put unit-less holders in a single shared `unassigned` diversity bucket.
    - `org.py:499`: withdraw index edges on `status="revoked"` and on `auto_entities: false` (R7).
    - `hypergraph.py:528-537`: cap members per returned edge to the admitted nodes (R9).
    - `routing.py` and `inquiry/service.py:386`: on a ranking exception, fall back to first-N with
      `rank_method: "fallback"`, so that a ranking bug cannot block routing.

Not changed by this review: /home/user/ng-impl (except this file) and /home/user/NeuralGraph. All probes ran in the scratch archive copy.

---

## Re-review of the F1–F11 fixes (snapshot `snap_wp1fix`, ~06:45 UTC, uncommitted, base `82a7a4f`)

Reviewer: REVIEWER-2 (`claude-opus-5-5`). Scope: WP1's files only (`knowledge/{hypergraph,gate,service}.py`,
`inquiry/{routing,service}.py`, `discovery/engine.py` (the two transaction edits), `org.py:update_holder`,
`evidence/service.py`, `api/routes_goals.py`, `__main__.py`, `tests/test_hypergraph*.py`). ENGINEER-E's scale hunks in the
same diff were not reviewed. That includes `authz.py` (+175 lines: `can_route_many`, `tenant_index`, `pinned`), so this
re-review makes no claim about `can_route` semantics after those hunks.

**Verdict: ACCEPT WP1. One narrow item (S1) still blocks any run that uses `min_independent_units` (O3).** Everything else
is fixed, and the remaining notes are non-blocking.

### How it was checked
```
cp -r $SCR/snap_wp1fix/{mycelic,NeuralGraph,pytest.ini} $SCR/rv2      # my own copy; the snapshot is not modified
orchestrator log $SCR/snap_wp1fix_tests.log            → 483 passed, 7 skipped in 129.39s, EXIT 0
python -m pytest mycelic/tests/test_hypergraph_review.py mycelic/tests/test_hypergraph.py mycelic/tests/test_routing_rank.py -q → 34 passed
python -m pytest mycelic/tests/test_review_wp1.py -q -s   (my R1–R10 probes, unchanged, plus R3b, R8b) → see below
python -m pytest mycelic/tests/test_review_perf.py -q -s  → see F10
```

### R1–R10 and F1–F11, one by one

- **F1 / R1: fixed.**
  - Change: `KnowledgeService.sync_support_sync` (`knowledge/service.py:519`) syncs the edge from the gate's effective
    refs and refreshes `claims.support`. `verify_consistency` now compares the edge with `claims.support`
    (`hypergraph.py:765-767`).
  - Probe output: `R1 claim.status hypothesis claims.support.independent_roots 1 edge.independent_roots 1 verify_consistency []`.
- **F2 / R2: fixed.**
  - `engine.py`: both transactions now call `self.knowledge.sync_support_sync(c, …)` after the `claim_evidence` insert.
  - `on_evidence_event` syncs inside its first transaction. The engineer's monkeypatch test shows a failing edge write
    rolls back the status change.
  - `test_r2_whole_loop_leaves_a_consistent_graph` runs the demo loop to quiet and then checks `verify_consistency == []`.
  - My probe, updated to the new transaction shape, outputs `R2 verify_consistency after the engine's own tx: []`.
  - Residual (non-blocking): the verification replay path (`engine.py:771-773`, `outcome = "replayed"`) still skips
    `recompute_status`. After a crash, edge and `claims.support` agree (I4 holds), but `claims.status` can lag until the
    next recompute or sweep. Suggest calling `recompute_status` on replay; it is idempotent.
- **F3 / R10: fixed.**
  - `rebuild_sync` writes only missing edges, and `--regenerate` adds new versions without deleting anything.
  - Probe output: `R10 … before rebuild: [(1, 'superseded'), (2, 'active')]  after: [(1, 'superseded'), (2, 'active')]`.
- **F4 / R6: fixed.**
  - `hg.scrub_claim_entities_sync` runs inside `_scrub_claim_text`. Rebuild no longer derives entities from
    `DELETED_TEXT`.
  - Probe output: `R6 … after purge: []`.
- **F5 / R8: fixed.**
  - Members-only ids are no longer published, so my original oracle probe cannot even find a `service:` id to probe with
    (`IndexError`, as expected).
  - New probe R8b outputs `default: {'service:parcelrouter': 5, 'symptom:timeout': 5}` and
    `opt-in members + min 1: {… 'service:checkout': 1, 'service:dispatch': 1}`. So by default, members-only records are
    excluded and single-record ids are excluded.
- **F6: fixed.** `_entity_min_records(default=min_records_to_publish)` returns 5 unless the owner sets
  `entity_min_records` (R8b above). Nothing outside the store sets `entity_include_members` or `entity_min_records`
  (grep over the snapshot and `research/mycelic_e2e`: no hits outside the reviews).
- **F7 / R5: fixed for what WP1 added.**
  - `view()` empties `target_entities` and drops `trigger.claim_id` for principals without `_full_view`.
  - Probe output: `R5 full_view: False  target_entities seen by routed owner: []`.
  - **The older leak is still there:** the same view still returns `motivating_lineage` with the claim id *and* its text
    as the label (`inquiry/service.py:145`). Probe output: `lineage labels: ['Deploys cause customer timeouts, tracked as issue:tracker:lgx-412']`.
    So blind verification is still defeated for *human* responders through that channel. This was there before WP1 and is
    not WP1's to fix, but it needs an owner and a ticket before any human-responder evaluation of blind verification.
- **F8 / R3: fixed for the reported case; residual S1 below.**
  - Probe output: `R3 ref 'z9' -> ('hypothesis', {'department': 1})   ref 'a0' -> ('hypothesis', {'department': 1})`.
- **F9: fixed.**
  - `required_units` normalizes each part separately and logs the invalid one.
  - `routes_goals.py` returns 400 for an invalid `min_independent_units`, whether it comes from `policy` or
    `measurement_source.policy`.
  - Tested in `test_f9_*`.
- **F10: fixed.**
  - Change: an incremental index update costs O(this holder's entities). A version opens at most once per UTC day per
    entity, and only the last 3 versions are kept.
  - My unchanged perf probe (1,500 holders, 10 departments, 2 shared + 1 unique entity each):

    `heartbeat_ms_at_n={10: 0.5, 100: 1.5, 500: 0.5, 1000: 0.7, 1500: 0.6} total_s=1.5 entity_edges=1502 one_drop_change_ms=0.6`

    Before the fix: `{… 1500: 106.1} total_s=66.8 entity_edges=4500`. Latency no longer grows with H.
  - `rank_holders` takes 0.029 s at 1,500 candidates, unchanged.
  - Residual (non-blocking): unit and domain counts are adjusted with the holder's *current* unit
    (`hypergraph.py:465`). After a membership move, the old unit's count stays inflated and the new unit's decrement is a
    no-op. Only `traverse` reads those counts; routing reads live units. Acceptable for v1. Suggest recounting units when
    the daily version opens.
- **F11: fixed.**
  - `entity_holders` and incidence hits are restricted to candidates (probe output: `R4 entity_holders 2, authorized with
    the entity 2`).
  - A single shared `unassigned` diversity bucket.
  - Revoke and `auto_entities: false` withdraw in the same transaction (probe output: `R7 … after revoke: {}`).
  - Traverse members are bounded (probe output: `R9 nodes: 3 members returned inside edges: 3`).
  - First-N fallback with `rank_method: "fallback"` (`test_f11_ranking_failure_falls_back_to_first_n`).

### Still blocking for O3 runs

**S1: department attribution still depends on unit-id order when two roots are each shared with the same department.**
- Where: `knowledge/gate.py:116-118`. A multi-unit root goes to an already-counted unit if possible, otherwise to
  `min(unit_id)`. With roots processed in id order, the result depends on whether the shared department has the smallest
  id. Unit ids are random (`new_id`).
- Probe R3b: root `ra` is held at the same `observed_at` by the shared department S and by department X; root `rb` by S
  and by department Y. Requirement `{"department": 2}`. Output:

  `R3b shared dept smallest id -> ('hypothesis', {'department': 1})   shared dept largest id -> ('supported', {'department': 2})`

- So the same evidence structure gives opposite verdicts. All of this evidence is visible inside S, so the conservative
  answer is 1.
- Minimal patch: attribute the multi-unit roots to the *fewest* units, not greedily by id.
  - Process those roots in order of their candidate unit, choosing the unit that covers the most remaining multi-unit
    roots. Break ties first by "already counted", then by unit id.
  - Alternatively, an exact minimum by brute force when there are ≤ 8 multi-unit roots, and the greedy otherwise.
- Add R3b as a test.
- This blocks only runs that set `min_independent_units`. It does not block WP1's other uses.

### Ruling on the engineer's F5 deviation (public-only by default; members-only ids need `export_policy.entity_include_members=true`; `entity_min_records` defaults to 5)

**ACCEPT, with conditions.**

The defaults are now fail-closed and stricter than §C.5. C.5 would have published members records whose audience covers
the owning unit. Here no members record is published unless the owner opts in, and ids need 5 records. That removes both
the R8 oracle and the k=1 problem by default.

The engineer's justification holds. The holder sees the member list but not the coordinator's routing audience, so it
cannot evaluate C.5's condition itself. Doing that properly needs a coordinator-supplied audience, as `question_audience`
does.

Conditions:
1. **The opt-in is weaker than C.5.** It publishes ids from members-only sources regardless of whether the members cover
   the routing audience. The source's other members never consented; only the holder owner did. Treat it as a
   privacy-weakening configuration:
   - No benchmark, seed or default config may set it. None does today.
   - Any run that enables it, or lowers `entity_min_records` below 5, declares that in BENCHMARK_CONTRACT.md and in the
     run ledger.
   - The `holder.update` audit already records `export_policy` changes.
2. **The audience-covered variant of C.5 stays open as a follow-up.** It means publishing members ids only when the
   source's member set contains the owning unit's audience, which the coordinator sends to the holder.
3. **E1's O2(a) prediction must be re-derived with threshold 5 and public-only ids.** Recall from symptom, service and
   issue ids will be lower than under the original commit's defaults. If it is too low, the remedy is a declared, measured
   contract parameter, not a code default.

---

## Review of commit `1b74545` (integration): WP1 parts, H1 term index, H2, H5

Reviewer: REVIEWER-2 (`claude-opus-5-5`). Copy: `git archive 1b74545 | tar -x -C $SCR/rv3`.
Scope:
- S1 / `_attribute`, `evidence_freshness`, the `motivating_lineage` redaction;
- H1: `0007_term_index`, `entities.py`, publication in `evidence/service.py`, `org.holder_heartbeat`, `rank_holders`;
- H2;
- H5.

ENGINEER-E's scale hunks (`authz.py`, transport, `holder/embedded.py` lifecycle, migration 0006) are out of scope and were
not reviewed.

**Verdict: ACCEPT the WP1 fixes (S1, lineage redaction, `evidence_freshness`), H2 and H5. H1 is BLOCKED by one
publication defect (B1).** I accept the orchestrator's privacy ruling in principle, but the code does not implement
"public records only" as disclosure defines it.

### Reproduction
```
python -m pytest mycelic/tests -q -p no:warnings                  → 496 passed, 7 skipped in 142.59s (matches the engineers' run)
python -m pytest mycelic/tests/test_review_wp1.py -q -s           → R1–R10, R3b, R8b pass (R8 cannot find a members-only id: expected); T1 passes; T2 FAILS (B1)
```

### (a) S1, lineage redaction, evidence_freshness: ACCEPT

- **S1.** `CommitGate._attribute` (`knowledge/gate.py:117-154`):
  1. Roots with a single candidate unit fix that unit.
  2. Roots that touch a fixed unit join it.
  3. The remaining roots get a minimum unit cover: exhaustive search up to 8 roots, greedy beyond.

  The total is minimal, because the remaining roots never touch a fixed unit. My unit-id-order probe gave:

  `R3b shared dept smallest id -> ('hypothesis', {'department': 1})   shared dept largest id -> ('hypothesis', {'department': 1})`

  The original R3 also holds: `('hypothesis', {'department': 1})` for both ref ids.
  - Non-blocking: the exhaustive search has no bound on how many units it may consider (`gate.py:137-142`). Measured:
    8 roots with disjoint candidate sets of size 2/3/4 take 0.03 s / 1.02 s / 9.03 s. `KnowledgeService.sync_support_sync`
    recomputes `independent_units` *inside a write transaction*.
  - Fix: stop the search at `k = need`, which is enough to decide a shortfall, and fall back to the greedy cover when
    `C(|universe|, k)` exceeds about 10⁵.
- **`motivating_lineage`.** For a principal without full view, `view()` now replaces claim, conflict and evidence lineage
  items with `{"type", "label": "", "redacted": true}` (`inquiry/service.py:128,149`). Probe output: `R5 … lineage labels: ['']`.
  This closes the older leak I flagged.
  - Non-blocking: `detail()` still returns the `question_runs` row unfiltered (`inquiry/service.py:206`). After
    evaluation its state carries `verified.target_claim_id` (`engine.py:824`). That is after the routed owner has answered,
    so it does not break blindness, but it should be redacted the same way.
- **`evidence_freshness`.** It now reads `meta`, so undated references count as `unknown` (`knowledge/service.py:965-971`).
  This is an admin metric and is covered by `test_evidence_freshness_counts_undated_references_as_unknown`.

### (b) H1 term index: ruling and findings

**Authorization comes first: confirmed.**
- `term_hits` is restricted to the authorized `candidates` (`routing.py:94`, `in_scope = … if hid in allowed`).
- The pool is still a slice of the candidates.
- `term_holders` and `entity_holders` are intersected with `allowed` (`:152`).
- `ranked_out` (`:150-153`) lists only authorized candidates that were not chosen, with numeric score parts.

So term routing cannot route to a holder the asker could not route to.

**The audit detail holds hashed ids only.**
- `terms` holds the question's term ids, i.e. hashes of the words of a question an admin can already read.
- No route exposes `term_index`: `grep term_index mycelic/api` finds nothing, and `traverse` does not read it.
- The `/admin/audit` endpoint is admin-only.

**The keyed hash.**
- Term ids are HMAC-SHA256 of a stem, truncated to 64 bits, under a per-tenant key (`org.py:73-77`).
- The runtime always has a secret: it comes from `MYCELIC_SECRET_KEY`, or is generated and persisted to
  `data_dir/secret_key` (`runtime.py:112-127`). `build_runtime` passes it to `OrgService`. The bench (`run.py:57`) and
  perf (`perf_bench.py:104`) set fresh secrets.
- Backups do not include `secret_key` (`observability.backup_bundle`: `coord.db` and holder DBs only).

**Ruling on the key: ACCEPTABLE.** The "stable non-secret fallback" is reachable only through `OrgService(db)` without a
secret. That happens in tests, in `hypergraph.traverse`'s default authorizer (which never asks for the term key), and in
`bench/baseline_central.py:139` (which does not route). Required hardening (non-blocking for now): `term_key()` returns
`None` and logs when `secret_key` is empty, so a misconfigured path publishes nothing instead of dictionary-testable ids.

Two caveats:
- Every holder receives the tenant-wide key (`routes_org.py:1043`). Any holder owner can therefore hash a dictionary, but
  the index itself is never served to them.
- Rotating the server secret silently invalidates all term ids until holders send their next heartbeat.

**Ruling on the orchestrator's privacy ruling (public records only, threshold 1, `auto_terms` off-switch): ACCEPT in
principle.** Some corrections:
- A public record is, by the product's own ACL definition, disclosed to "anyone the holder's export policy answers"
  (`ingest/events.py:57`). That is not "every tenant member" as D18 says. The term signal is only consulted for holders
  the asker may route to, so the conclusion still holds: the router reveals nothing that holder would not disclose to that
  asker.
- Threshold 1 is fine *for records that really are disclosable*. The boilerplate filter (df > max(5, 10 %)) is
  irrelevant to privacy. It only removes non-discriminative words.
- T1 confirms the main filter works:

  `T1 published: 6 {'zanzibarquux': True, 'vorpalcorp': False, 'snarkhunter': False, 'quibblefrob': False}`

  Here `vorpalcorp` sits in a public-visibility record of a members-only source, `snarkhunter` in a private source, and
  `quibblefrob` in a public record with `sensitivity="restricted"`.

**B1 (BLOCKING H1, and it also affects F5 entity publication): publication uses the record's ingest-time visibility, not
the ACL that disclosure applies at use time.**
- Where: `_published_terms` (`evidence/service.py:1009-1016`) and `_entity_counts_sync` (`:1052-1060`) filter on
  `ingest_records.visibility='public'`.
- Disclosure (`_record_acl_sync`, `:841-865`) does more: it *narrows* by the source's current ACL (`connector_sources.visibility`
  and `member_ids`: "a channel turned members-only … takes effect at the next use without re-ingesting"). It also honours
  the owner's per-source opt-out (`connector_sources.exportable`), the per-source `disclosure` and `access_state`.
- Probe T2: a public source with 5 records. Output:

  `baseline term: True entities: [service:ledgergate, symptom:timeout] | narrowed (source set members-only): term True entities [same] | opted-out (exportable=0): term True entities [same] | record exportable at use time: False`

- A channel that became members-only, or that the owner withdrew from export, keeps routing questions by its words and
  entities. This is precisely the content oracle for restricted records that the ruling forbids.
- Fix:
  - Filter publication on the same effective ACL as disclosure. Join `connector_sources` (in a sharded store, read
    eligible `source_id`s from s0 and filter shard records by them) and require all of:
    - the source's *current* visibility is `public`, or no source row exists and `r.visibility = 'public'`;
    - `exportable` is not 0;
    - `disclosure` is not `none`;
    - `access_state = 'ok'`.
  - Publish nothing when the holder-level `export_policy.disclosure` is `none`.
  - Drop stems matching the owner's `deny_patterns` (`self._deny`); today they are redacted from answers but still hashed
    into terms.
  - Add the `connector_sources` state (e.g. `MAX(updated_at)`) and the deny list to the term-cache signature (`:1000`);
    today the cache ignores ACL changes for up to 600 s.
  - Add T2 as a test.

**Non-blocking.**
- `term_incidence` drops a term held by more holders *tenant-wide* than `max(TERM_COMMON_FLOOR, fraction × candidates)`
  (`hypergraph.py:502-519`, `routing.py:92`). Holders the asker cannot route to can thereby suppress a term that is rare
  among the asker's own candidates. This affects recall only; it leaks nothing.
- `term_stems` stems only the hyphen parts of a token, not the whole token (`entities.py`). This is consistent on both
  sides, so matching works, but the comment's claim of "light stemming" is inaccurate.

### (c) H2 entity-named blind verification: ACCEPT
- `LoopEngine._topic_names` (`engine.py:261-274`) admits `service` / `component` / `symptom` / `topic` names only, drops
  any name containing a digit, and keeps at most 3. Tracker keys, versions and organizations never appear.
- `_blind_leak` (numbers shared with the claim, a run of 6 or more of its words) still guards the result.
- Example: for the claim "Timeouts on the ledgergate-service are caused by the retry-storm in component payments v2.3
  since 2026-10-01 (LGX-412)", the fake model writes "What do your own notes show about ledgergate-service, timeout?
  Include dates." It contains no number, no key and not the asserted cause. The old fallback, kept when no entity is
  known, wrote "…about timeouts ledgergate service caused retry?", which leaks more.
- Notes (non-blocking):
  - Naming both service and symptom states the finding's subject-predicate pair. Consider naming only the
    service/component when one exists.
  - `topic:` ids can enter the claim text literally from holder responses, through `_LITERAL_ENTITY` and up to 80
    characters. Their display names then reach other holders' question text. Restrict topic names to ids present in
    `entity_registry` with `holder_count ≥ 1`.

### (d) H5 snapshot rule: ACCEPT, with one non-blocking bound
- Retraction requires `snapshot_complete`: nothing queued or leased, with dead items excluded (`evidence/service.py`
  `_snapshot_complete`; `org.py:518`, entity retract, term retract). An owner switching publication off, or a
  revocation, still withdraws immediately (`org.py` `update_holder` → `withdraw_holder_entities_sync`).
- **Can an honest holder never retract?** Only while its ingest queue never drains. Queued retries end as `dead`, and
  expired leases are re-leased, so normal operation retracts at the next idle beat. A permanently busy holder, or an
  offline but unrevoked one, keeps stale domains, entities and terms indefinitely. That includes ids derived from
  *deleted* records, which matters for deletion requests.
  - Recommended (non-blocking): a deletion processed by the holder reports its withdrawn ids explicitly. Those are
    authoritative regardless of the snapshot. Separately, cap the age of entries an unvouched holder has not re-reported
    (for example 24 h).
- **Can an attacker-holder keep stale incidence?** Yes, by never vouching. That adds no capability: a lying holder could
  always report arbitrary ids, and it can already have any word's term id because it holds the key. The impact is
  bounded by `can_route` (only holders the asker may route to are ranked) and by the caps (1,000 terms, 500 entities,
  64 domains). It can attract questions it was already authorized to receive. Accept.

### Blocking list for `1b74545`
1. **B1:** `evidence/service.py:1009-1016` and `:1052-1060`, plus the cache signature at `:1000`. Make term and entity
   publication use the disclosure-time effective ACL (current source visibility, `exportable`, `disclosure`,
   `access_state`, holder `disclosure`) and the owner's `deny_patterns`. Add probe T2
   (`$SCR/rv3/mycelic/tests/test_review_wp1.py::test_t2_narrowed_or_opted_out_source_still_publishes`) as a test.

Everything else in scope is accepted. The non-blocking items listed above are follow-ups.

---

## Re-review of commit `36a9951` (B1, heartbeat ordering, term_key, bounded cover)

Reviewer: REVIEWER-2 (`claude-opus-5-5`). Copy: `git archive 36a9951 | tar -x -C $SCR/rv4`.
Probes: `$SCR/rv4/mycelic/tests/test_review_wp1.py` (all earlier probes, plus T3 and HB).

**Verdict: ACCEPT the B1 ACL fix, the `term_key` change and the bounded unit-cover search. Two small items are BLOCKING:
HB-1 (holder-clock ordering can freeze retractions, including privacy withdrawals) and B1-d (multi-word deny patterns are
applied to single stems).** The fix for each is a few lines.

### Reproduction
```
python -m pytest mycelic/tests -q -p no:warnings   → 507 passed, 7 skipped in 186.51s (matches the commit message)
python -m pytest mycelic/tests/test_review_wp1.py -q -s   → all earlier probes pass (R8: expected, see above); T1, T2 pass; T3 and HB fail (below)
```

### (1) B1: publication follows the disclosure-time effective ACL. ACCEPT, except deny patterns (B1-d)
- `_blocked_sources` (`evidence/service.py`) excludes sources whose *current* visibility is outside the published set, or
  that have `exportable = 0`, `disclosure = 'none'`, a lost access state, or are not selected. Both `_published_terms`
  and `_entity_counts_sync` apply it.
- The holder-level `disclosure: none` publishes nothing.
- The blocked set and the deny list are part of the term-cache key.
- `test_publication_follows_the_effective_acl_not_the_ingest_time_visibility` is my T2.
- Probe outputs:
  - `T1 published: 6 {'zanzibarquux': True, 'vorpalcorp': False, 'snarkhunter': False, 'quibblefrob': False}`
  - `T2 baseline term: True entities: [service:ledgergate, symptom:timeout] | narrowed: term False entities [] | opted-out: term False entities [] | record exportable at use time: False`
- **B1-d (BLOCKING, one line): deny patterns are tested against single stems.**
  - Where: `evidence/service.py:1032` (`not any(p.search(st) …)`) and against entity ids in `_entity_counts_sync`. Answers
    instead redact the *text* (`_redact`).
  - A phrase pattern therefore never matches a stem.
  - Probe T3, with deny pattern `project\s+falcon`:

    `T3 redacted answer text: Status of [redacted]: the zanzibarquux migration slipped. | 'falcon' still a published term: True`

  - Fix: compute the stems from the redacted text, the same way answers do. At `:1022`, use
    `term_stems(redact(r["text"], self._deny))`. For entities, skip records whose text matches a deny pattern. Add T3 as a
    test.

### (2) Heartbeat ordering by holder-supplied `stats.at` (`org.py:506-512`). BLOCKING (HB-1) as implemented
- **The fix works for its target.** It addresses the wave-1 blackout correctly: the transport replays the first, empty
  start-up beat after fresher ones, and that replay is now ignored (`test_a_stale_heartbeat_never_rolls_the_registry_back`).
  Embedded holders share the coordinator's clock, so for them the comparison is sound.
- **The comparison trusts a value from another clock.** `at` is the holder process's wall clock
  (`holder/service.py:171`, `"at": now_iso()`), but it is compared with no bound against the last applied `at`.
- **Clock running ahead, later corrected.** Every beat until real time passes the bad timestamp is ignored. Retractions
  are ignored too, including the B1 withdrawals of a source turned members-only and the withdrawals after deletions.
- **Far-future `at`** (a bad RTC, or a malicious holder) freezes the holder's published domains, entities and terms
  indefinitely. Holders always send `at`, so nothing resets it. Only revocation or turning publication off clears it,
  because `update_holder` withdraws in its own transaction regardless.
- **`at` missing.** The check is skipped and the beat applies. Because `stats` is stored without `at`, the next beat's
  comparison is reset as well.
- **Probe HB** (entity `symptom:timeout`, vouched snapshots):

  `HB indexed after ahead-beat: True | after corrected vouched empty beat: True | after beat without at: False | far-future holder still indexed after empty beat: True`

- **Attacker-holder.** Freezing its *own* incidence gives a malicious holder nothing new: it could always report anything,
  or never vouch. A freeze cannot spread to other holders (the check is per holder row). The real problem is honest
  holders with bad clocks, whose retractions (including privacy withdrawals) are silently suppressed.
- **Required fix (minimal).**
  - Bound the holder clock against the coordinator's. If `at > now + MAX_SKEW` (for example 5 min), treat it as `now` for
    both the comparison and what is stored.
  - Store the coordinator's receive time with the applied `at`.
  - This limits any suppression to `MAX_SKEW` and makes a far-future `at` harmless.
  - Add HB as a test.
- **Recommended (proper) fix.** Order by a monotonic sequence assigned by the transport, not by any clock:
  - the SQLite transport's message row id (coordinator-assigned), or the NATS stream sequence for the holder subject;
  - or, if the holder must assign it, a `(boot_id, seq)` pair, accepting a new `boot_id` only when its first beat
    arrives after the last applied beat's receive time.

  Holder timestamps should then serve only as a sanity bound.

### (3) term_key and the bounded unit cover: ACCEPT
- **`term_key`.** It returns `None` and logs once when there is no server secret (`org.py:76-86`). Checked directly:
  `no secret -> None | with secret -> 32c1a441... | question terms without key -> []`. Routing then uses no term signal,
  the bootstrap hands `None` to external holders, and embedded holders publish nothing.
- **Bounded cover.** The exhaustive search now stops at `need − fixed units` and falls back to the greedy cover beyond
  `MAX_COMBINATIONS = 50,000` (`knowledge/gate.py:140-151`).
  - Measured on my pathological cases (8 roots, disjoint candidate sets of size 3/4/5): need=2 takes ≤ 0.001 s and
    need=None ≤ 0.044 s, against 9.03 s before.
  - The shared-department chain still attributes to one unit (`chain {'S': 2}`).
  - The decision stays correct. If no cover of size ≤ `need − fixed` exists, the true count already reaches `need`. The
    combination cap triggers only for universes of more than about 316 units at k = 2.

### Blocking list for `36a9951`
1. **HB-1:** `org.py:506-512`. Bound the holder-supplied `stats.at` by the coordinator's clock (clamp or ignore
   `at > now + MAX_SKEW`), so a clock running ahead cannot freeze retractions. Preferably order by a transport-assigned
   sequence. Add probe HB as a test.
2. **B1-d:** `evidence/service.py:1022,1032` and `_entity_counts_sync`. Apply deny patterns to the record text before
   computing stems and entities, as answers do (`_redact`). Add probe T3 as a test.

---

## Re-review of `fca60ea` (HB-1 heartbeat clock bound, B1-d deny phrases)

Reviewer: REVIEWER-2 (`claude-opus-5-5`). Copy: `git archive fca60ea | tar -x -C $SCR/rv5`.
Probes: `$SCR/rv5/mycelic/tests/test_review_wp1.py` (all earlier probes, plus HB2 and T4).

**Verdict: ACCEPT. HB-1 and B1-d are fixed.** One required follow-up is a one-line change (L1) and must land before this
code runs against an *existing* `coord.db`. It does not block benchmark runs on fresh databases.

### Reproduction
```
python -m pytest mycelic/tests -q -p no:warnings          → 509 passed, 7 skipped in 174.15s (matches the commit message)
python -m pytest mycelic/tests/test_review_wp1.py -q -s   → every earlier probe passes (R8: expected, as before); HB, T3 and the new T4 now pass
```

### HB-1: fixed (`org.py:19,509-534`)
- **The fix.** A holder `at` more than `HEARTBEAT_MAX_SKEW_SECONDS = 300` ahead of the coordinator counts as now. The
  stored value is clamped too, with `at_reported`, `at_clamped` and `received_at` kept beside it. A beat with no time
  keeps the previous ordering value.
- **The stale start-up replay is still ignored.** Covered by `test_a_stale_heartbeat_never_rolls_the_registry_back` and
  `test_a_holder_clock_ahead_or_far_future_cannot_freeze_retractions`.
- **HB probe output:**

  `HB indexed after ahead-beat: True | after corrected vouched empty beat: False | after beat without at: False | far-future holder still indexed after empty beat: False`

  A holder clock that runs ahead can now suppress its own later beats for at most 300 s. A far-future value no longer
  freezes anything.
- **L1 (required before upgrading an existing coordinator; one line).** The *stored* value `seen` is not clamped
  (`org.py:510`). A far-future `at` written by an earlier version (every version before `36a9951` stored `stats.at`
  unbounded) still freezes that holder after the upgrade.
  - Probe HB2 (stored `at` = 9999-12-31): `HB2 legacy far-future stored at still freezes: True`.
  - Fix: `if seen and seen > now_dt + timedelta(seconds=HEARTBEAT_MAX_SKEW_SECONDS): seen = now_dt`, and add HB2 as a test.
- **Residual (non-blocking, inherent to ordering by clock).** A holder clock that jumps *backwards* suppresses that
  holder's own beats until its clock passes the last applied time. Probe HB2 output: `backward clock jump (1 h)
  suppresses the vouched retraction: True`.
  - A clock-based check cannot tell this apart from the stale replay it exists to drop. The impact is limited to that
    holder's own entries, and it corrects itself when the clock does.
  - Owner opt-out and revocation still withdraw immediately (`update_holder`).
  - The durable fix remains ordering by a transport-assigned sequence (SQLite message row id or NATS stream sequence), as
    recommended in the 36a9951 re-review.

### B1-d: fixed (`evidence/service.py:1063-1080` and the two call sites)
- **The fix.** Terms are stemmed from the text after the owner's deny patterns are removed (`_strip_denied`, the same
  `pattern.sub` an answer's redaction applies). Records whose text matches a deny pattern contribute no entity ids
  (`_denied_records`, then `record_id NOT IN json_each(?)`).
- **Probe outputs:**
  - `T3 redacted answer text: Status of [redacted]: … | 'falcon' still a published term: False`
  - New probe T4 (5 records saying "Project Falcon: the ledgergate-service …", deny `project\s+falcon`):

    `T4 entities before deny: ['service:dispatch', 'service:ledgergate', 'symptom:timeout'] | after deny: ['service:dispatch', 'symptom:timeout']`

  - Covered by `test_phrase_deny_patterns_keep_every_word_of_the_phrase_out_of_the_published_ids`.
- **Non-blocking cost.** When deny patterns exist, `_denied_records` scans and regex-tests every active chunk of the
  holder on *every* stats call (every heartbeat, about 20 s), and nothing caches it. Terms are cached, entities are not.
  Cache the denied record ids under the same signature as terms (`change_seq`, count, deny tuple).

### Status
- No blocking items remain in my WP1 scope.
- Open follow-ups:
  - L1, the one-line `seen` clamp, before any upgrade of an existing coordinator;
  - transport-sequence heartbeat ordering;
  - the `_denied_records` cache;
  - the non-blocking items listed in the earlier sections.

---

## Review of C5 `92d3d7c` (D19: competing findings, contradiction follow-ups to both sides, department round-robin)

Reviewer: REVIEWER-2 (`claude-opus-5-5`). Copy: `git archive 92d3d7c | tar -x -C $SCR/rv6`.
Probes: `$SCR/rv6/mycelic/tests/test_review_c5.py` (P1–P5). Motivation read: `plan/DEV_ANALYSIS_C4.md`.

**Verdict: ACCEPT C2 and C3. C1 is accepted for the benchmark's canonical-name world, with two required changes before it
counts as a product rule. BLOCKING: K1, a disclosure regression through the question's run state.** Its fix is one line.

### Reproduction
```
python -m pytest mycelic/tests -q -p no:warnings -p no:cacheprovider       → 518 passed, 7 skipped in 129.99s (matches the commit message)
python -m pytest mycelic/tests/test_c4_followup.py -q -p no:warnings        → 7 passed
python -m pytest mycelic/tests/test_review_c5.py -q -s                      → probe outputs below
```
A first full-suite run in the same tree crashed during session teardown (pytest cache write). It overlapped with my probe
runs in the same directory. The rerun above uses no cache and its own basetemp.

### C1: "different single subjects → competing, not a contradiction" (`discovery/engine.py:261-279, 775-800, 826-833`, late-response path)
- **Correct for the analysed failure.** Two statements, each naming exactly one subject entity, with different subjects:
  no conflict is opened, both are committed, and the gate ranks them. The same subject still opens a conflict
  (`test_c1_same_service_with_different_numbers_still_opens_a_conflict`). Zero subjects or several subjects on either side
  keep the old behaviour.
- **Can BOTH become supported? Yes.** P1: the rival is also corroborated in 2 departments. Output:

  `P1 … {'ledgergate-service …': 'supported', 'parcelrouter-service …': 'supported'} conflicts: 0`

  For the product, this is acceptable when they really are different subjects: two services can each have their own
  value, and several causes can contribute. But the competing relation is recorded only in the question's run-state
  `gate_notes` (`engine.py:799`), which the asker view does not present as a relation.
  - Required (product, non-blocking for the benchmark): persist `competing` as a hypergraph relation between the two
    claims, or as a question-result field, so the asker sees two *competing* supported explanations. Otherwise they look
    like two unrelated facts.
  - For the benchmark, O1 extraction must keep its ambiguity rule: two supported claims naming different options abstain
    or count as ambiguous. That rule is in the scorer, which I did not re-review here.
- **Genuine contradictions that now slip through:**
  - **P2, alias or renamed service.** `ledgergate-service … 19` against `ledger-gateway-service … 50` (the same real
    service, renamed). Output:

    `P2 … {'ledgergate-service …': 'supported', 'ledger-gateway-service …': 'supported'} conflicts: 0`

    The extractor has no alias table, so any rename or alias turns a value clash into "competing". A holder (or colluding
    holders) can get a contradicted value accepted by phrasing it about an alias, as long as it reaches the gate's roots
    and departments.
  - **P3/P3b, issue against service.** `LGX-412 … 50` against `ledgergate-service … 19`.
    - While no holder has published an `LGX` issue (P3), "LGX-412" is not extracted: no subject on that side, so a
      conflict opens (`contested`, 1 conflict).
    - Once any holder of the tenant has published an `issue:tracker:lgx-*` id (P3b), the pair counts as competing:

      `P3b tracker issue (key known) vs service -> {… 'supported', … 'supported'} conflicts: 0`

    - Two problems follow:
      - Cross-kind pairs (issue / component / repo against service) are treated as different subjects, although a
        ticket or a component is very often *about* that service.
      - The verdict for the same pair depends on unrelated tenant state (`tenant_known_keys` from `entity_registry`).
  - **A service against a component of it.** Not reached in practice: my phrasing tests show components are rarely
    extracted (`'the ledgergate db component v2.1 …' -> []`), so that side has no subject and the conflict opens. It
    *would* slip through as soon as the extractor recognizes the component.
  - **A retraction phrased about a different service** ("not ledgergate, it was parcelrouter") names two services, so it is
    not competing and the conflict opens. Correct.
- **Is the subject-kind choice sound?** Service, component, issue and repo as subjects, with symptom, topic, version and
  org as context, is reasonable for "what is the statement about". Required before C1 is a product rule (not blocking for
  benchmark runs, whose worlds use canonical service names only):
  1. **Same kind only.** Competing requires `entity_kind(a) == entity_kind(b)`. Cross-kind pairs keep the conflict
     (`engine.py:279`).
  2. **Related subjects keep the conflict.** That covers subjects that co-occur in the same holders' records (entity
     index, the same `entity_index` holders) or whose display names overlap (one contains the other, or token Jaccard
     ≥ 0.5). This catches renames and aliases cheaply until an alias table exists.
  3. **Same answer in every tenant.** The verdict should not depend on whether a tracker key happens to be known in the
     tenant: pass the tenant `tracker_keys` policy as well, or treat an unrecognized key-like token as "unknown subject",
     which keeps the conflict.

### K1 (BLOCKING): the run state now carries other responders' content, and routed holder owners can read it
- **Where.** `QuestionService.detail()` returns the full `question_runs` row to everyone `can_view` admits, including a
  holder owner who was merely routed the question (`inquiry/service.py:206`). Such an owner otherwise sees only their own
  responses (`resp = [... if full or r["holder_id"] in mine]`).
- **What is new.** C1 writes response-derived text into that state:
  - `{"competing": {…, "summary": summary[:160]}}` (`engine.py:799`). The summary quotes both sides' values, e.g.
    "Numbers differ: 19 vs 50".
  - `{"verification_competing": [finding text[:120], …]}` (`engine.py:832`), i.e. other holders' finding statements on a
    *blind verification* question.
- **Probe P4.** The owner is on the rival side and routed only, and the gold side's value is 19. Output:

  `P4 full_view: False | responses visible: 1 | run.state mentions the other side's value 19: True | competing note: [{'competing': {… 'summary': 'Numbers differ: 19 vs 50'}}]`

- Before this commit the run state held only ids, statuses and reasons. I flagged it as non-blocking in the 1b74545 review
  because of `verified.target_claim_id`.
- **Fix (one line):** in `detail()`, return `run` only when `full` is true (or strip `state.gate_notes`, `state.verified`
  and the investigation notes otherwise). Add P4 as a test.

### C2: contradiction follow-ups reach both sides (`routing.py:110-113`). ACCEPT
- For `kind == 'contradiction'` the `already_supports` and `shares_root` penalties are off, and the audit says so.
  Verification questions keep routing away from supporters.
- **No regression in what the routed holders see.**
  - The contradiction question is still `blind_verification=True` (`engine.py` `_ask_contradiction`: `_child_policy(…,
    blind_verification=True)`).
  - Its text is composed from the anchor's topic entity names (H2 rules: no numbers, no tracker keys).
  - `target_entities` and lineage are redacted for routed-only viewers. Only the trigger's `conflict_id` remains, an id.
  - The holder envelope carries no claim text.
- The holders behind side A therefore do not receive side B's claim through the question. They were routable before too,
  only penalized. The one disclosure channel is K1, which affects contradiction questions as much as any other.

### C3: department round-robin. ACCEPT
- **The rule.** The bonus is `W_DIVERSITY / (1 + n_chosen_in_group)`, rounded to 1e-9 so equal sums tie exactly. Exact
  ties go to unit-owned holders, then `holder_id`. The order list is built from the pool sorted by that key
  (`routing.py:125-150`).
- **Authorization still comes first.** The pool is still a slice of the authorized candidates.
- **Probe P5.** Output:

  `P5 deterministic over 20 shuffles: True | chosen subset of candidates: True | with 5 candidates only those 5: True`

- Preferring unit holders on exact ties is a routing preference, not an authorization change: unit holders are
  candidates only if `can_route` admits them.

### Blocking list for `92d3d7c`
1. **K1:** `inquiry/service.py:206`. Do not return the run state (or at least its `gate_notes`, `verified` and
   investigation notes) to principals without `_full_view`. Add P4 as a test.

### Required before C1 is a product rule (not blocking benchmark runs)
- Same-kind subjects only, and related or alias subjects keep the conflict (`engine.py:263-279`).
- A tenant-independent verdict for tracker keys.
- A persisted, asker-visible `competing` relation.

---

## Review of K1 fix b145e21

Reviewer: REVIEWER-2, model id `claude-opus-5-5`. Copy: `git archive b145e21 | tar -x -C $SCR/rv7`.
Probes: `$SCR/rv7/mycelic/tests/test_review_c5.py` (P4 from the C5 review, plus new probes P6 and P7). Nothing under
`/root/sealed_holdout` was opened, and the repository was not modified.

**Verdict: BLOCK.** The fix itself is correct: `detail()["run"]` is redacted for viewers without full view. But the same
checkpoint content still reaches a routed-only holder owner through `detail()["question"]["result"]`, and P7 reproduces it
end to end. The fix is small and local (below).

### Commands and results
```
python -m pytest mycelic/tests -q -p no:warnings -p no:cacheprovider --basetemp $SCR/rv7_bt    → 521 passed, 7 skipped in 138.42s
python -m pytest mycelic/tests/test_review_c5.py -q -p no:warnings -p no:cacheprovider -s -k "p4 or p6 or p7"
```
`mycelic/tests/test_question_detail_k1.py` has three tests. They are meaningful; the stored state is asserted to really
hold "Numbers differ … 19":
- a routed-only owner gets `run` with exactly `{question_id, tenant_id, step, attempts, updated_at, state: {}}`;
- a full viewer gets the unredacted state and all 5 responses;
- the `verification_competing` text and `target_claim_id` are absent for a routed-only owner of a verification question.

### What the fix closes (confirmed)
- **P4, the C5 probe.** Before the fix it showed the other side's value in `run.state`. Now:

  `P4 full_view: False | responses visible: 1 | run.state mentions the other side's value 19: False | competing note: []`

- **P6, the whole `detail()` for a routed-only owner on the rival side, question stopped at step `verification_spawned`.**
  Output: `gold value/name present per detail part: {'question': False, 'claims': False, 'discoveries': False, 'followups': False, 'lineage': False, 'run': False}`.
  The run is `{'question_id': …, 'tenant_id': …, 'step': 'verification_spawned', 'attempts': 3, 'updated_at': …, 'state': {}}`.
- **No other route returns `question_runs` state.** `grep -rn "question_runs\|_checkpoint(" mycelic` outside tests and the
  engine finds only `inquiry/service.py:205` and the internal `seed/scenario.py`.
- **Full viewers are unchanged.** See `test_k1_full_viewer_still_gets_the_run_state`.
- **The benchmark asker view is unchanged.** `research/mycelic_e2e/bench/issue.py:collect_view` (lines 73-100) reads
  `question` (status, `result`, routes), `followups` (id, kind, status), `claims` and `discoveries` from
  `GET /api/questions/{id}`. It never reads `run`, and b145e21 changes no file under `research/`.

### Remaining disclosure path (BLOCKING): `question.result`
- **Where.** When the question resolves, the engine writes its result from the same checkpoint
  (`discovery/engine.py:1034-1038`): `gate_notes` (including the C1 `competing` summaries and `verification_competing`
  texts), `verified` (including `target_claim_id`), `deferred_followups` (model-written follow-up texts, e.g. "Which
  record is current: {a_text} or {b_text}?"), `investigated`, and the synthesis `summary` ("Supported: <claim texts>
  Hypotheses: <claim texts> Disagreement: …").
- **Who sees it.** `QuestionService.view()` returns `dict(q)` with `result` unredacted (`inquiry/service.py:119-131`; the
  K1 branch redacts `target_entities`, `trigger` and lineage only). `detail()` returns `view(q, principal)` to every
  principal that `can_view` admits, including routed-only owners, and `list(needs_input=True)` does the same.
- **Reproducer P7** (`test_p7_whole_loop_result_visible_to_routed_owner`):
  1. Run the demo loop to quiet (the same scenario as `test_r2_whole_loop_leaves_a_consistent_graph`).
  2. For each resolved question and each routed user-holder owner without `_full_view`, call `detail()`.
  3. Look for the text of claims the owner may **not** view (`can_view_scoped` is false), excluding anything that also
     appears in that owner's own response.

  Output:
  ```
  P7 routed-only owner views of resolved questions: 12
  P7 kind: gap | result keys: ['claim_ids', 'conflict_ids', 'deferred_followups', 'discovery_id', 'followup_ids', 'gate_notes', 'investigated', 'outcome', 'summary', 'synthesis_method', 'verification_deferred', 'verification_questions'] | gate_notes: True | … | claim text quoted in result: ['Recurring blocker: deploy approvals for hotfixes take two da'] | run.state redacted: True
  P7 any NOT-viewable claim text in a routed-only owner's question.result: True | cases: 2 of 12
  ```
  In 2 of the 12 views, the owner reads the text of a claim they are not allowed to view, which is not in their own
  answer, through `question.result.summary`. Every one of the 12 also carries `gate_notes`. This is the K1 content class
  through a second field, and it predates C1 for the synthesis summary.
- **Fix (small).** In `view()`, when `principal is not None and not self._full_view(principal, q)`, replace `d["result"]`
  with a content-free subset, e.g. `{"outcome": res.get("outcome")}`. That covers `detail()`, `list()` and
  `needs_input`. Askers and full viewers are unchanged, so `collect_view`'s `result` is unaffected for the asker, provided
  the asker is a full viewer. Add P7, or a unit version of it, as a test.

### Checked and clean
- **`followups`** (child questions' `question_id, text, kind, status, depth, created_at`).
  - Verification and contradiction follow-ups go through the blind builders (`_create_followup` → `_ask_verification` /
    `_ask_contradiction`). Their text is composed from topic names, and `create()` rejects it if it states a number or
    repeats 6 or more words of the target (`_blind_leak`).
  - Other kinds (gap, relationship, hypothesis, prediction) are asked as the model wrote them. They are questions meant
    for holders, so they are not secret from holders, but a real LLM may quote a finding. Non-blocking. The fake provider
    only writes contradiction and verification follow-ups, and those are blind.
  - P6 found no gold value or name in `followups`.
- **`claims` and `discoveries`.** Filtered by `can_view_scoped` (`inquiry/service.py:200-203`); empty for the probe owner.
- **`lineage`.** `lineage_graph(principal, …)` is authorized per node; P6 found no leak.
- **`question` view, apart from `result`.** `target_entities` is emptied; the trigger's `claim_id` / `ref_id` are dropped;
  claim, conflict and evidence lineage items are `{"type", "label": "", "redacted": true}`. `routes` lists the routed
  holders and their statuses, not content, as before.

---

## Review of K1b fix 2d00ca2

Reviewer: REVIEWER-2, model id `claude-opus-5-5`. Copy: `git archive 2d00ca2 | tar -x -C $SCR/rv8`.
Probes: `$SCR/rv8/mycelic/tests/test_review_c5.py` (P4, P6 and P7 as before, plus P8, new: the SSE event audience). I did
not open `/root/sealed_holdout` and wrote nothing in the repository.

**Verdict: ACCEPT.** I found no remaining path through which a viewer without full view receives `questions.result` or
`question_runs.state` content.

### Commands and results
```
python -m pytest mycelic/tests -q -p no:warnings -p no:cacheprovider --basetemp $SCR/rv8_bt     → 523 passed, 7 skipped in 157.69s
python -m pytest mycelic/tests/test_review_c5.py -q -p no:warnings -p no:cacheprovider -s -k "p4 or p6 or p7 or p8"   → 4 passed
```
```
P4 full_view: False | responses visible: 1 | run.state mentions the other side's value 19: False | competing note: []
P6 full_view: False | gold value/name present per detail part: {'question': False, 'claims': False, 'discoveries': False, 'followups': False, 'lineage': False, 'run': False}
P6 needs_input listing exposes result.summary: False | run state: {… 'step': 'verification_spawned', 'attempts': 3, … 'state': {}}
P7 routed-only owner views of resolved questions: 12
P7 any NOT-viewable claim text in a routed-only owner's question.result: False | cases: 0 of 12        (was 2 of 12 at b145e21)
P8 non-full-viewer principals admitted to question events carrying result.summary: 0 of 42
```

### The change
In `QuestionService.view()` (`inquiry/service.py`), a viewer without `_full_view` gets `result` reduced to
`RESULT_PUBLIC_KEYS = ("outcome", "timed_out_routes")`, scalars only. The `run` redaction from b145e21 stays. Outcome
values are labels (`committed`, `investigated`, `no_findings`, `cancelled`, `no_authorized_holders`, …). The free-text
fields (`note`, `reason`, `rejected`, `summary`, `gate_notes`, `verified`, `deferred_followups`) are dropped.

### Every channel checked
- **`view()`.** Redacts `result` for viewers without full view; `target_entities`, trigger and lineage are redacted as
  before.
- **`detail()`.** `question` comes from `view(q, principal)`; `run` is redacted (b145e21). `claims` and `discoveries` are
  filtered by `can_view_scoped`, and `lineage` is authorized per node. P6 and P7 are clean.
- **`list()` and `needs_input`.** Both build `self.view(q, principal=principal)` (`inquiry/service.py:199`). P6 checks
  `needs_input`.
- **API routes that serialize questions:**
  - `routes_questions` (create at `:70` returns `view(q, principal=p)`; list; detail at `:75`);
  - `routes_goals:119` (`questions.list(p, …)`);
  - `routes_org:265` (`questions.list(p, …)`);
  - `routes_workspaces:79,88,122` (`questions.list(p, …)`);
  - `routes_knowledge:184` (`view(q, principal=p)`).

  All pass the principal. `routes_knowledge:189` and `routes_org:543` call `questions.get()` internally, for an
  authorization or route re-check, and serialize nothing. No API route returns `question_runs`: outside tests and the
  engine, only `inquiry/service.py` (detail) and the internal `seed/scenario.py` read it.
- **SSE events.** `set_status` emits `question.*` events whose payload carries the full `result`, with audience
  `{unit_ids: [scope], user_ids: [asker]}`. The hub admits a user if they are in `user_ids`, or if `unit_ids` intersects
  `visible_unit_ids ∪ led_unit_ids` (`api/sse.py:165-191`).
  - P8 ran the demo loop to quiet and evaluated `EventHub.authorized` for every active user who is neither a full viewer
    nor the asker, against every question event whose payload has `result.summary`. Result: 0 of 42 admitted.
  - This channel is consistent with full view in the demo topology. It is a separate filter, not the same predicate. A
    future change to `visible_unit_ids` or to the event audience would have to keep them aligned.
  - Suggestion (non-blocking): emit only `{"status", "outcome"}` in the event payload; clients refetch through `detail()`.
- **`principal=None` defaults to `full = True`.** Every caller of `view()` passes a principal: `routes_questions:70`,
  `routes_knowledge:184`, `inquiry/service.py:199,220,663`, and the internal `seed/scenario.py:420`
  (`grep -rn "\.view(" mycelic`, excluding tests and the unrelated `reshard.view()`). No externally reachable path calls
  it with `None`.

### Unchanged for full viewers, askers and internal callers
- `test_k1b_full_viewers_and_internal_callers_still_get_the_whole_result` covers full viewers, `principal=None` and the
  asker. `can_view_scoped` treats `asker_id == p.id` as a viewer (`authz.py:342`), so an asker always gets the whole
  result.
- `bench/issue.py:collect_view` reads `question.result` as the asker, and `bench/score.py:328,367` reads
  `result.outcome`. Both are unaffected, and `git diff 4f54d88 2d00ca2 -- research/` is empty.
- Note: the shared working tree currently has uncommitted edits by others to `bench/issue.py`, `bench/run.py` and
  `EXPERIMENTS.jsonl`. They are not part of 2d00ca2, and I did not review them.

### Tests added
- `test_k1b_resolved_result_is_content_free_for_a_routed_only_owner`: a routed-only owner gets only the public keys.
- `test_k1b_full_viewers_and_internal_callers_still_get_the_whole_result`: full viewer, `principal=None` and asker each
  get the whole result.

Both are meaningful and pass in the full run.

---

## Review of observe() query fix 4c27744

Reviewer: REVIEWER-2, model id `claude-opus-5-5`. Copy: `git archive 4c27744 | tar -x -C $SCR/rv9`. Fuzz script:
`$SCR/fuzz_observe.py`. I wrote nothing in the repository and did not open `/root/sealed_holdout`.

**Verdict: ACCEPT.** The new query returns exactly the evidence refs that some response of the goal cites, with
`created_at >= since`. That is the old result minus the old query's false positives and duplicates. The LIMIT order does
not starve anything, because the loop uses only the *count* of `new_evidence`. The oracle test is not vacuous.

### Commands and results
```
python -m pytest mycelic/tests -q -p no:warnings -p no:cacheprovider --basetemp $SCR/rv9_bt        → 527 passed, 7 skipped in 160.86s
python -m pytest mycelic/tests/test_observe_new_refs.py -q -p no:warnings -p no:cacheprovider -s   → 4 passed ("new query alone: 1.3 ms vs old 265.0 ms")
python3 -I $SCR/fuzz_observe.py
  → FUZZ cases: 300 non-empty new: 296 | new == exact-citation set and new ⊆ old and no duplicates — mismatches: 0
    | old-only false positives by cause: {'substring': 187, 'underscore': 203, 'case': 245}
```
Query plan on the real schema (`EXPLAIN QUERY PLAN`, migrated `CoordDB`):
```
SEARCH q USING INDEX idx_questions_goal (goal_id=?)
SEARCH r USING INDEX idx_responses_question (question_id=?)
SCAN j VIRTUAL TABLE INDEX 1:
SEARCH e USING INDEX sqlite_autoindex_evidence_refs_1 (ref_id=?)
USE TEMP B-TREE FOR DISTINCT / FOR ORDER BY
```
The cost is bounded by the goal's responses × the array length; there is no per-response scan of `evidence_refs`.

### Set equivalence
- **The fuzz.** 300 random goals: ids with substrings of one another, `_` and mixed case, duplicate citations, 5 %
  empty or malformed `evidence_ref_ids`, random `since`.
  - In every case the new set equals {ids cited in a valid JSON array of a goal response} ∩ {`created_at >= since`}.
  - The new set is a subset of the old set, with no duplicate rows.
- **What the old query matched in addition** (`old − new`), beyond the declared substring case:
  - a `_` in a ref id, which LIKE treats as a single-character wildcard;
  - case-insensitive matches, since LIKE is case-insensitive for ASCII;
  - ids found inside malformed, non-array values.

  All of these were false positives, so their removal is a correction. The commit message lists only "substring"; the
  `_` wildcard and case-insensitivity are two more classes it removes.
- **The json_each guard.** `CASE WHEN json_valid(...) THEN ... ELSE '[]'` keeps empty or malformed values from raising.
  The writer stores arrays (`NOT NULL DEFAULT '[]'`).

### LIMIT and `since` (starvation)
- **Old order.** It depended on the plan: q (goal index) → r (question index) → SCAN `evidence_refs` per response. So the
  old "first 50" were the refs cited by the *earliest questions and responses in index order*, in `evidence_refs` rowid
  order inside each response. That is neither globally oldest nor newest.
- **New order.** Globally oldest by `created_at`, then `ref_id`; deterministic (`test_observe_new_refs_limit_is_stable`).
- **`since`.** `tick()` passes `loop.stats.observed_from`, which is `utcnow() − 1 s` taken at the start of the previous
  tick (`engine.py:390,414-415`). It advances every tick whatever `observe()` returned. So with more than 50 new refs in
  one window, both versions drop the rest permanently. That is unchanged behaviour, not a regression.
- **Consumers.** `new_evidence` is read nowhere except as `len(new_evidence)` in the gap question's `trigger.observed`
  metadata (`engine.py:608`; `grep -rn new_evidence` finds only the return at `:375`). It is not part of `model_obs`, of
  `needs_model` or of `_deterministic_gaps`. So which 50 are listed has no effect on loop decisions.
- **One observable change.** The count is now deduplicated: the old count double-counted a ref cited by several
  responses. It is still capped at 50.

### The oracle test is not vacuous (`mycelic/tests/test_observe_new_refs.py`)
- `test_observe_new_refs_matches_the_old_query`:
  - compares against the verbatim old SQL for three `since` values;
  - asserts the exact expected list `["ref_edge_eq", "ref_a_new1", "ref_a_new2", "ref_shared"]` (including the
    inclusive edge);
  - asserts that the old query returned `ref_shared` twice and 5 rows in total;
  - asserts goal isolation (`ref_b_only` and `ref_nobody` absent) and the json_each guard on NULL.
- `test_observe_new_refs_is_exact_where_like_matched_substrings`: old `{ref_1, ref_10}`, new `[ref_10]`.
- `test_observe_new_refs_limit_is_stable`: more than 50 new refs gives the 50 oldest.
- `test_observe_new_refs_micro_benchmark`: equal rows (`len == 20`) on 3k × 3k data, plus a timing assertion
  `t_query < t_old`.
  - Non-blocking: a timing assertion in the unit suite can flake under load. The margin is about 200×, so the risk is
    low; consider marking it or moving it to the perf scripts.

### Non-blocking notes
- `SELECT DISTINCT` with `ORDER BY` on `e.created_at`, which is not in the select list, is accepted by SQLite. Because
  `ref_id` is the primary key, DISTINCT on the selected columns equals DISTINCT on `ref_id`, so the ordering is
  well-defined.
- If `new_evidence` is ever acted on per ref, the cut-off of more than 50 refs per window must be revisited: page with
  `since = last created_at` instead of the tick time. That limitation predates this change.
