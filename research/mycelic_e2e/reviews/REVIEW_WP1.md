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
