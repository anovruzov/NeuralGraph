# REVIEW_E: commit 1b74545, ENGINEER-E scale repairs

Reviewer: REVIEWER-3, model `claude-opus-5-5`. I did not write this code. I reviewed it in a fresh `git archive 1b74545` copy at
`$SCR/re`, where `SCR=/tmp/claude-0/-home-user-NeuralGraph/cb55ecbb-9144-58cb-a5af-0b006a90960a/scratchpad`. My two probe
files exist only in that copy: `mycelic/tests/test_zz_review_e_probe.py` and `test_zz_review_e_watch.py`.

Scope:
- authz caches and migration 0006;
- `candidate_holders` and `question_audience`;
- bounded open holders in `holder/embedded.py`;
- subject-indexed wake-ups in `transport/sqlite_transport.py`.

The WP1, H1, H2 and H5 parts of the integration commit are out of my scope.

## Verdict: ACCEPT. Nothing blocks the merge.

I found no path where a stale cache **authorizes** something that should be denied. Fixes F1-F4 below are small,
recommended, and not blocking.

## Reproduction

- **Full suite** (`python -m pytest mycelic/tests -q -p no:warnings`): **496 passed, 7 skipped in 141.31s**, matching the
  commit message.
- **Probe A: another connection, raw SQL, warm caches.** `test_zz_review_e_probe.py` builds the randomized org from
  `test_scale_equivalence.build` (seed 5, 24 users) and warms every cache with `check_all`. A second `sqlite3`
  connection then makes 12 writes with **raw SQL** (no OrgService), and `check_all` compares `question_audience`,
  `candidate_holders` (ids and reasons), `can_route`, `domains_for_goal` and `_scopes` against the legacy uncached code
  after each one. The writes:
  - membership role → `department_lead`, then membership revoked;
  - holder owner disabled, then re-enabled;
  - holder `owner_id` changed to another user;
  - holder `export_policy` changed, then holder revoked;
  - unit archived;
  - project scope deleted;
  - goal grant inserted, then revoked.

  Result: **passed**.
- **Probe A is sensitive.** With `DROP TRIGGER trg_rev_memberships_u` added first, it fails with
  `AssertionError: candidate_holders differs`.
- **Probe B: cross-process watcher.** `test_zz_review_e_watch.py`: transport 2 on a second `CoordDB` of the same file
  publishes a burst of 4,101 messages, more than the poller's 4,000-row page. A watcher on transport 1 sees every holder
  subject, including the last one. **Passed.**

## 1. Authz caches (`authz.py`, migration 0006)

**What each cache reads, and which trigger guards it:**

| Cache | Reads | Guarded by |
|---|---|---|
| `_scopes` | memberships, org_units, project_scopes | memberships (all columns), units (`path, parent_id, type, tenant_id, archived_at`), pscopes (all) |
| `_closure_cache`, `_descendants_cache` | org_units, project_scopes | the same unit and pscope triggers |
| `_owner_cache` | `users.status`, `users.tenant_id`, memberships | users (`status, tenant_id`), memberships |
| `tenant_index` | active users, memberships joined to non-archived units, active grants | users, memberships, units, grants (all columns), plus time expiry (below) |
| `tenant_taxonomy` | domain_taxonomy, domain_aliases | taxonomy triggers |
| `routing_holders` (org.py:440) | holder routing columns | holder triggers on owner, tenant, revoked transition, domains, published_domains, export_policy |
| `domains_for_goal` (engine.py:295) | the above | `(org, holders)` counters; `candidate_domains=[]`, so no taxonomy dependency |

- **Every column a cached function reads is guarded by a trigger.**
- The columns that are not guarded are not read by any authorization decision:
  - users: `name`, `email`, `is_demo`, `settings`. `is_demo` only tags created artifacts; grep finds no authorization use.
  - org_units: `name`, `depth`, `settings`, `is_demo`.
  - holders: `status` online/offline, `mode`, `name`, `stats`, `last_heartbeat_at`. `can_route` reads only `revoked`.
    The status snapshot in `routing_holders` is not used downstream: route() and rank_holders read only ids, owner and
    domains.
- **Raw SQL is covered too.** Triggers fire for every writer: raw SQL, another process, upsert (UPDATE triggers) and
  `INSERT OR REPLACE` (the INSERT trigger fires).

**Time-based grant expiry:**
- `tenant_index` stores `valid_until = min(expires_at)` over the active grants (authz.py:209) and rebuilds once
  `now_iso() >= valid_until`. The SQL filter uses the same string comparison (`expires_at > now`), and `expires_at` is
  written by `plus_seconds` in the same format as `now_iso`.
- `question_audience` keys on `idx.stamp`, so a rebuild invalidates it.
- No other cache depends on grants: `_scopes`, `_closure`, `_descendants` and `_owner` read no grants, and
  `principal_for_user` is uncached.
- The equivalence test covers a grant that expires while the caches are warm (sleep 1.2 s).

**Policies and roles:**
- Roles are `memberships.role`, which is guarded.
- `tenant_policies` and holder `answer_scopes` are read live on each call, or come from the holder row's `export_policy`,
  which is guarded.

**Audience cache key** (inquiry/service.py, `question_audience`):
- The key holds (tenant, visibility, scope, asker id and type, goal id), `goal_fp` and the tenant-index stamp.
- `goal_fp` covers every goal field read by `can_view_scoped` and `_is_mine`: visibility, scope, owner type and id,
  `created_by`, assignees, tenant. Goal grants live in the index.
- The question id joins the key only when the question itself has a grant. `create_grant` cannot create question grants
  anyway (its `res_table` has no `question`).

**Multi-process:**
- Counters are read from the file on every `_sync_caches`. They are not read inside a `pinned()` block, which is fully
  synchronous.
- The coordinator is single-threaded. The only executor use is `extract_text` in routes_org.py:360, so `_hold` cannot leak
  across threads.
- `test_another_connection_sees_counter_changes` and probe A both confirm this.

**Residual risks (low):**
- **F1, `db/coord.py:102`:** `counter()` returns a constant `0` when the counter row is missing. A deleted row would turn
  every cache into a never-invalidated cache. Its other fallback, `self.revision` when the table is missing, is
  in-process only. Fix: return `-1 - self.data_version()` (or raise) instead of `0`, so a missing row never stays equal.
- **Theoretical rollback race:** a cache built from uncommitted rows read on the shared connection inside an open
  transaction that later rolls back could survive. It survives only if the next commit brings the counter back to the
  same value before any read. This class already existed with the old `(revision, data_version)` key. Fix if wanted:
  skip cache population while `conn.in_transaction`.

## 2. `candidate_holders` and `question_audience` semantics and the equivalence test

- **Semantics are unchanged.** `candidate_holders` runs the same predicate (`_can_route`) with per-question memoization
  (closure, descendants, asker visibility, domain overlap per distinct domain set), in the same order (`ORDER BY name`).
- `question_audience` tests the same predicates over principals built in bulk. `memberships_by_user` and `active_grants`
  match `memberships_for_user` and `grants_for`: same non-archived-unit filter, user grants plus grants to the user's
  membership units.
- **`test_scale_equivalence.py` is adequate:**
  - 3 random orgs;
  - the legacy code kept as a reference;
  - comparison of ids **and** rejection reasons, audience, `domains_for_goal` and `_scopes`;
  - re-checked after membership, user, tree, project, grant (incl. expiry), holder and goal-assignee changes;
  - `update_goal` exists, so that step really runs.
- **Gaps in the test (non-blocking):**
  - cross-connection changes are checked only for `visible_unit_ids`;
  - holder owner changes and raw-SQL writers are not checked.

  Probe A closes all three gaps and passes. Adding it to the suite would be worthwhile (F4).
- **F3 (robustness), org.py:440-455:** `routing_holders` returns the cached list of dicts itself, and `candidate_holders`
  hands those dicts to callers. Today no caller mutates them: grep finds no `h[...] =` in routing.py or route(). One
  mutation would still poison routing for the whole tenant until the next counter move. Fix: return shallow copies, or
  freeze the rows (`MappingProxyType`).

## 3. `holder/embedded.py`: bounded open holders

**Loss analysis.** Every path below either delivers the message or leaves it waiting in the durable queue, from which it
is redelivered:

| Path | Outcome |
|---|---|
| Message in-process to a dormant holder | `publish` → `_notify` → watcher `_on_message` → `_wake` → activator → `ensure` → the durable consumer reads from its cursor |
| Message from another process | shared poller within `poll_interval` (probe B) |
| Watcher missed it | `_scan_pending` at start and every max(30 s, heartbeat) |
| Message during a close | `_on_message` sees `_closing` and queues a wake; `ensure` waits on the lifecycle lock; `_close_dormant` re-checks `pending()` after closing |
| Before the process started | `reconcile` → `_scan_pending` (`test_message_published_before_the_process_started_is_not_lost`) |

Messages are durable (transport cursor plus `AUTOINCREMENT` ids, so no id reuse after prune).

**Lifecycle lock (remove/ensure fix):**
- `ensure` takes the fast path only when the holder is not closing and its lock is free. Otherwise it waits for the lock
  and re-reads `_services`.
- `remove` holds the lock.
- `SqliteTransport.subscribe` no longer hands back a subscription that is closing; it waits for the closing task and
  starts a fresh one.
- `test_remove_and_ensure_racing_leave_a_working_consumer` covers it.

**Dormant holders stay routable:**
- The registry status is untouched while the process runs, and the final heartbeat (`_close_dormant`) carries
  `dormant: true`.
- `can_route` reads only `revoked`.
- On `stop()`, the dormant holders are marked offline in one write, which is correct: nobody can open them any more.

**F2 (low, not demonstrated), embedded.py:491-492:**
- The evictability check, including `svc.inflight == 0`, runs before `stats = await svc.stats()`.
- The consumer is still live during that await (`stop()` comes after), so it can start a handler.
- `svc.stop()` then cancels the in-flight handler through `Subscription.close`. The envelope is not acked and is
  redelivered after reopen, and the post-close `pending()` check wakes the holder. So this is a **duplicate delivery, not
  a loss**.
- Fix: call `await svc.stop()` before `svc.stats()` (stats do not need the consumer). Alternatively, re-check
  `svc.inflight` after `stats()` and abort the close if it rose.
- I did not reproduce the window. Whether `store.stats()` actually yields depends on the store's async wrappers.

## 4. `transport/sqlite_transport.py`: subject-indexed wake-ups and the shared poller

- **No lost in-process wake-up while a consumer registers or runs:**
  - the listener is registered before the first `_deliver_batch`;
  - `wake.clear()` happens *before* each read (line ~329);
  - `asyncio.Event` is sticky, so a publish between the read and the wait makes the wait return at once.
- **Index lookup:** `_notify` checks every literal prefix of the subject (`tokens[:k]` for k = 0..n), so patterns ending
  in `*` or `>` at any depth are found. Each candidate is still confirmed with `subject_matches`.
- **Cross-process:**
  - the poller starts at `MAX(id)` when the first listener registers. Earlier messages are read by the consumers from
    their own cursors;
  - ids are `AUTOINCREMENT` and writers are serialized, so `id > _seen` cannot skip a later commit;
  - pages of 4,000 rows are drained in a loop (probe B);
  - the consumers' own safety timer, `max(20 × poll_interval, 5 s)`, backs up a missed poller wake.
- **Note:** watchers have no safety timer of their own. For embedded holders, the periodic `_scan_pending` plays that
  role.

## NATS

`nats_transport.py` `watch` (a core subscription) and `pending` (`consumer_info`, else `get_last_msg` on a wildcard
subject) are **not exercised against a live server**; the docstring says so. It is unverified whether `get_last_msg`
accepts a `>` wildcard on the deployed server version. If it does not, a never-opened holder's waiting messages would be
noticed only by the core watcher, which does not survive a restart. Note only; not reviewed further.

## Fixes (none blocking)

- **F1:** `db/coord.py:102`: a missing counter row must not return a constant (return a non-repeating value or raise).
- **F2:** `holder/embedded.py:491-492`: stop the consumers before taking the final stats, or re-check `inflight` after
  `stats()`, so a handler that started meanwhile is not cancelled.
- **F3:** `org.py:440-455`: return copies, or frozen rows, from `routing_holders`.
- **F4 (test):** add probe A (raw-SQL writes from a second connection, with a trigger-drop sensitivity check) to
  `test_scale_equivalence.py`.
