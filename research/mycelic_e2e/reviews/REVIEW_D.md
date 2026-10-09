# REVIEW_D: commit 93ae752 (ingestion dispositions D1/D2/D3)

Reviewer: REVIEWER-3, model `claude-opus-5-5`. I did not write this code. I reviewed the commit (not the working tree) in a
`git archive 93ae752` copy at `$SCR/rv3`. For comparison I used the parent `9b426a3` (`$SCR/rv3_parent`) and `f96f263`
(`$SCR/rv3_old`), where `SCR=/tmp/claude-0/-home-user-NeuralGraph/cb55ecbb-9144-58cb-a5af-0b006a90960a/scratchpad`. The probe
scripts are in `$SCR/scripts/` (`mig_test.py`, `d3_probe.py`, `d2_d1_probe.py`). Every number below comes from those runs.

## Verdict: ACCEPT-WITH-FIXES. F1 and F2 block the merge: do not merge as-is.

D1 (rejection ledger) is sound and well built. There is one leak path (F3). D2 stores the flag correctly, but the coordinator
never reads it, and two of the "source time" fallbacks bring the probe-e defect back (F2). D3 fixes probe f1b. As written,
however, it **replaces** the content root with an identity root. In five probe shapes this makes the count less safe
than the parent: copies that counted as one root now count as two or three. That contradicts I4 ("copies cannot raise
support") and §4.5 rule 6 (F1).

## 1. Reproduction and test value

- Full suite in the archive copy: `python -m pytest mycelic/tests -q -p no:warnings` gives **434 passed, 7 skipped in 96.69s**.
  This matches the commit message.
- New file: `mycelic/tests/test_ingest_dispositions.py` gives **23 passed in 3.08s**.
- Run against the parent code, the new file errors at import (`origin_root` does not exist). That proves nothing, so I
  mutated the commit code itself (`$SCR/rv3_mut`):

| mutation | result |
|---|---|
| M1 remove the origin-root override (events.py:370-377) | 3 failed |
| M2 export the record's own `object_key` instead of `origin_key` (evidence/service.py:1164) | 4 failed |
| M3 `changed = ev.updated_at or ev.created_at or ev.observed_at` (old evidence time) | 2 failed |
| M4 write the ledger in a separate transaction after `commit_page` (not atomic with the cursor) | **23 passed (survives)** |
| M5 store `str(exc)` for any exception | 1 failed |
| M6 drop the future-`content_time` guard (events.py:389) | 1 failed |

So the tests are meaningful for D2, D3 and the content-free property. Gap: no test checks that the *pipeline* writes the
ledger inside the cursor transaction. `test_rejections_commit_with_the_cursor_or_not_at_all` calls `commit_page` directly,
which is why M4 survives.

## 2. D1: rejected input

Traced paths in `_prepare_page` (pipeline.py:455-510):

- Every `continue` that is not an owner choice now writes a row: foreign source, normalize exception, validate problems,
  and admit `quota`/`foreign_source`.
- Owner exclusions, tombstones, `already_deleted`/`unknown_record`/`still_in_source` are counted in `excluded` only. This
  matches the doc's split.
- `local_export` turns unparsable and non-object JSONL lines into `__invalid__` rows with the hash of the line, so they reach
  the ledger as `line:N` (verified: `line:3`, `line:6` rows in the migration run).

Remaining gaps:

- **Leak (F3):** `pipeline.py:478` uses `getattr(exc, "code", None) or type(exc).__name__` for *any* exception. The class
  filter protects only `detail`. If a non-`ConnectorError` exception carries an input-derived `.code`, that value is stored
  verbatim in `ingest_rejections.reason` (no length cap) and in `connectors.health.rejected_by_reason`. Reproduced
  (`d2_d1_probe.py`, connector raising `CodeErr(code="SECRETSTRING-in-code " + text)`): the ledger row
  `reason = 'SECRETSTRING-in-code the quarterly password is hunter2'`, and the same string appears in health. The INFO log
  line already did this before the commit; durable storage is new.
- A `normalize` that returns `[]` (Slack non-kept subtype or empty body, slack.py:657/661; Gmail empty message,
  gmail.py:495) is not counted anywhere: no `normalize_errors`, no `excluded`, no ledger row. This predates the commit and
  is an intentional skip, but `raw_items = enqueued + duplicates + excluded + normalize_errors` no longer adds up for those
  connectors. Low.
- For JSON (non-JSONL) exports, a non-object entry is `{"__invalid__": i}` with no `__sha256__` (local_export.py:140). Its
  `raw_sha256` is the hash of `{"__invalid__": i}`, i.e. of its position, not of the item, unlike what the doc says. Low.
- `_locator` falls back to the payload `id`/`ts`/... as `object:<id>` when the id matches `[A-Za-z0-9._:@/#=+\-]{1,120}`.
  That admits email addresses and token-like strings unmasked. The same value is already stored as `source_object_id` for
  accepted records, so this is not new exposure. Low.
- **Atomicity:** correct. `extra_sync` runs after the fence check inside `commit_page`'s transaction (queue.py:151-155), and
  report counters are bumped only after the commit. A crash between the two cannot lose or duplicate rows, but only the
  direct test covers it (M4 above).
- **5000-row cap** (store.py:392-395): it is safe in the sense that storage is bounded and nothing content-bearing is lost.
  Two weaknesses:
  - Eviction is silent: there is no evicted counter, and `rejections_on_record` saturates at 5000.
  - Within one page every row has the same `now`, so in a flood the tie-break by `rejection_id` (a hash) decides which rows
    survive. That is arbitrary, not "most recent". A flood of junk evicts earlier diagnostics.

  Acceptable. An `evicted` counter in health would be a small, worthwhile addition (optional).
- Small accounting nits: `report.time_inferred` is counted before the commit (a fenced page counts twice). Two invalid
  events from one raw item with the same reason collapse into one row with `count=2`.

## 3. D2: undated records

Ingest side is correct:
- Records get `time_inferred` (+ `time_ingest_fallback`).
- `documents.observed_at` gets the source time.
- The future-time guard works (`content_time > observed` is dropped; M6 proves the test covers it).
- A later dated edit clears the mark.
- A malicious header `content_time` adds no capability: an exporter can set `created_at` on any record anyway.

Coordinator side does **not** honour the flag:
- `grep time_inferred|time_basis` outside `mycelic/ingest` finds only the export (evidence/service.py:1165-1168).
- `knowledge/support.py:107 evidence_time` / `freshness()` read `observed_at` only.
- `knowledge/gate.py:269` and `knowledge/service.py:498` gate on `fresh["stale"]`.

Reproduced (`d3_probe.py`, `d2_d1_probe.py`, `freshness(refs, freshness_days=30)` on the exported refs):

| case | exported `observed_at` / `time_basis` | `freshness()` |
|---|---|---|
| file with no dates at all | ingest time, `ingest` | `age_days 0.0, stale False` |
| undated 2019 note + one record dated today in the same file | `2026-10-09T05:00` (the other record's time), `source` | `age_days 0.1, stale False` |
| undated page, header `exported_at: 2026-10-09` | `2026-10-09T00:00`, `source` | `age_days 0.3, stale False` |

So the commit message's "never presenting as fresh" does not hold at the gate. There are two causes:
- The gate ignores `time_basis: ingest`.
- `exported_at` and "newest dated record of the file" are not content times. local_export.py:153 and :157 make them the
  evidence time, and the doc itself calls them only an upper bound (INGESTION.md:124-126).

Other connectors: Slack always has `ts`. Gmail without `internalDate` falls to `time_ingest_fallback`, so it is fresh at
the gate. Revisions behave the same as first ingest.

## 4. D3: explicit forwards

Probe results, compiled by `d3_probe.py`: (apparent refs, independent roots, unknown), parent 9b426a3 vs this commit.

| probe | parent | 93ae752 |
|---|---|---|
| S1 verbatim copy without link + verbatim forward with link (original held nowhere) | 2, **1**, 0 | 2, **2**, 0 |
| S1b pasted forward block without link + same block with link | 2, **1**, 0 | 2, **2**, 0 |
| S2 original held under app `slack`, verbatim forward whose link says `slack-export` | 2, **1**, 0 | 2, **2**, 0 |
| S3 original held as a mail record, pasted forward carrying the original's Message-ID | 2, **1**, 0 | 2, **2**, 0 |
| S4 same text, 3 distinct (fabricated) origins, 3 holders | 3, **1**, 0 | 3, **3**, 0 |
| S4b same text, 3 distinct origins, one holder | 3, **1**, 0 | 3, **3**, 0 |
| S5 `is_bot` record with a `bot_relay` link to an object held nowhere | 1, 0, **1 (unknown)** | 1, **1**, 0 |
| S6 A original + B independent + attacker forwards A with B's text verbatim | 3, 2, 0 | 3, 2, 0 |
| S7 (pre-existing) attacker's local_export uses A's namespace and id with B's text | 3, **1**, 0 | 3, **1**, 0 |

Findings:

- **Over-count regressions (S1-S5, unsafe direction).** The cause is `events.py:370-377`: when a link names an object, the
  content root is *replaced* by `origin_root(origin_key)`. A copy that carries a link therefore stops colliding with copies
  of the same text that lack one, or whose link uses another identity space.
  - The doc's limits paragraph (INGESTION.md:137) says such pairs "**still** count as two roots". That is false: in the
    parent they were one root. The regression is new.
  - S4 lets anyone turn one text into N independent roots by attaching N distinct link ids. A malicious member could
    already inflate by paraphrasing, so the new damage is mainly to honest data (S1-S3).
  - S5 contradicts §4.5 rule 6 / D11: an unresolvable bot relay used to be `root_known = False` and is now counted. The
    override condition `root.method != "explicit"` also fires for `unknown`.
- **Suppression by a forged link: not introduced by this commit.** In S6 the forged forward only folds the attacker's own
  record into A's group. A and B stay 2. Suppressing others' genuine roots still requires forging one's *own* identity
  (S7). local_export allows that, since `source_app` and `account_id` are free config, but it predates this commit.
- **Merging with unrelated objects.**
  - A reply that quotes someone carries no explicit relation: `quote` is excluded (test line 537), and local_export takes
    `relation` from the file with default `forward`.
  - A share link to a *different* object folds the sharer's own observation into that object's root. That is the declared
    I4 direction (under-count): acceptable.
- **Origin key and identity.** `object_key` hashes `tenant_id` in, including the rfc822 form (`events.py:_origin_identity`
  then `object_key(tenant_id, ...)`), so there is no cross-tenant linkage. Within a tenant the coordinator newly learns
  "holder B holds a forward of object K". It is an unsalted SHA-256 over guessable fields (Slack ts, Message-ID), so it can
  be confirmed by guessing. This is the same exposure class as the existing `object_key` export: acceptable, worth one
  sentence in §10.
  - Side effect: the forward's own `object_key` is no longer exported (service.py:1164). The same forward held by a pre-0004
    holder (no `origin_key`) and a post-0004 holder therefore does not merge until it is re-ingested. Low; the doc mentions
    pre-0004 records.

**Trade-off for the fix.** I prototyped the minimal alternative in `$SCR/rv3_fix`: delete the override block
events.py:370-377, keep the content root, and keep `meta.object_key = origin_key` so `support.py`'s object-key union does
the merge.
- Results: S1 = 1, S1b = 1, S2 = 1, S3 = 1, S4 = 1, S4b = 1, S5 = (0 independent, 1 unknown), and f1b still collapses.
  In the new test file, 3 tests fail, only on root-shape assertions (`source_root_id == origin_root(...)`,
  `root_method == 'explicit'`). Their support-count assertions (lines 427 and 442) pass.
- Cost: S6 becomes **1**. A member who forwards A while pasting B's verbatim text merges A and B. That is
  suppression-by-forged-link, the same power S7 already gives any local_export user, and in the direction I4 declares safe
  (under-count).

The structural reason no small patch has both properties: S1 (an honest link plus a verbatim copy) and S6 (a forged link
plus a verbatim copy of someone else) look the same to the counter. The owner has to choose. I recommend the content-root
variant, because I4/D11 rank over-counting as the worse failure and the suppression path is not new in kind.

## 5. Migration on a pre-0004 holder

`mig_test.py`: I built a holder with f96f263 code (good line, bad line, forward with `forwarded_from`; sync gave
`raw 3, enqueued 2, normalize_errors 1`), then opened it with 93ae752 code:
- Migrations `[(1, baseline), (2, ingest), (3, shards), (4, dispositions)]` were applied in order.
- The `origin_key` column is present, and old rows have `origin_key NULL`, `root_method content`.
- `stats().ingest_rejections == 0`, and export answers normally.
- The next sync gave `raw_items 5, enqueued 1, rejected 2` (the old bad line `line:3` plus a new one, `line:6`),
  `time_inferred 1`, health `degraded / rejected_items`.
- An edit of the old forward gave it `origin_key` and `root_method explicit`.

No breakage. Data shards get migrations at provisioning (shards.py:616).

Observed side effect: the first sync after the upgrade re-read the whole JSONL file (5 raw items, not 2). The invalid-line
placeholder now includes `__sha256__`, which changes the cursor's prefix digest whenever a file contains an invalid line.
Event-key dedupe absorbed it (enqueued 1), so this is harmless. It is a one-time re-read per such file.

## Fixes

- **F1 (blocking, D3), `mycelic/ingest/events.py:370-377`.** Remove the `origin_root` override and keep the content root.
  The merge then happens at the coordinator through `meta.object_key = origin_key` (evidence/service.py:1164, unchanged).
  `_link_origin` (pipeline.py:512) may stay, since it is spec rule 1 for a local original.
  - Update the three shape assertions in `test_ingest_dispositions.py` (lines 434, 447-448, 536) to assert
    `origin_key`/`meta.object_key` instead of `origin_root`.
  - Add S1/S4/S5 as regression tests.
  - Rewrite INGESTION.md:127-139 (drop "still count as two roots"; document S6/S7 suppression as a known limit).

  If the owner instead prefers suppression resistance, the *minimum* change is to make the override condition
  `root.method == "content" and root.root_known`. I did not run this variant. By reading the code, it restores S1b, S3 and S5 (pure_copy and unknown roots are no
  longer overridden) but not S1, S2 or S4, which must then be documented as regressions, not as "still".
- **F2 (blocking, D2), coordinator.** In `mycelic/knowledge/support.py:107 evidence_time`, ignore `observed_at` when
  `meta.time_inferred` is true (use `meta.reconfirmed_at`, else `None`). An undated ref can then never make a claim fresh;
  all-undated support becomes `unknown`.
  - Also remove `exported_at` from `local_export.py:153`, since export time is not content time.
  - Either drop the "newest dated record of the file" fallback (local_export.py:157) or keep it documented as an upper
    bound only. With the support.py change it no longer reaches the gate.
  - Add a test: `freshness()` over refs from a no-date file reports `unknown`, not `stale False / age 0`.
- **F3 (D1 leak), `mycelic/ingest/pipeline.py:478`.** Change it to
  `code = exc.code if isinstance(exc, ConnectorError) else type(exc).__name__`, and cap `reason` (e.g. `[:80]`) in
  `IngestStore.add_rejections_sync` (store.py:390, next to the existing `detail[:200]`).
- **F4 (test gap, optional).** Add a pipeline-level test: inject `StaleCheckpoint` on the page commit and assert there are
  no ledger rows and `report.rejected == 0` (kills M4).
- **F5 (optional).** Add an `evicted` counter to health when the 5000 cap drops rows, and give JSON-file invalid entries
  a real item hash (local_export.py:140).

## Re-review: fix commit 82a7a4f (HEAD), REVIEWER-3 (`claude-opus-5-5`)

I reviewed a fresh `git archive HEAD` (82a7a4f) at `$SCR/rr`, so uncommitted edits from other engineers are not included.
I used the same probe scripts as above. **Verdict: ACCEPT. Nothing is still blocking.**

### Reproduction

- **Full suite:** `python -m pytest mycelic/tests -q -p no:warnings` gave **445 passed, 7 skipped in 99.37s**. The commit
  message says 479 passed "on the shared tree"; the difference is uncommitted tests in that tree, not in HEAD.
- **New test file:** 34 passed. It adds an S1-S6 regression test for each probe, the F4 atomicity tests at lines 251 and
  268, the F3 tests, and the coordinator freshness tests.

**Probes, independent roots (HEAD vs 93ae752):**

| Probe | HEAD | 93ae752 |
|---|---|---|
| S1 | 1 | 2 |
| S1b | 1 | 2 |
| S2 | 1 | 2 |
| S3 | 1 | 2 |
| S4 | 1 | 3 |
| S4b | 1 | 3 |
| S5 | 0 + 1 unknown | 1 |
| S6 | **1** | 2 |
| S6 baseline | 2 | 2 |
| S7 | 1 | 1 |

- S1-S5 match the parent 9b426a3 again.
- f1b still collapses (`test_original_and_three_forwards_with_commentary_are_one_independent_root` passes).
- S6 = 1 is the documented cost of the content-root variant I recommended. INGESTION.md:139-143 now names it, together
  with the S7 equivalent that already existed. The S6 test asserts only `roots <= 2`, i.e. "never inflates".

**D2 at the coordinator** (`freshness(refs, freshness_days=30)`):

| Case | Result |
|---|---|
| File with no dates | `unknown: True, freshness_at None, undated_refs 1` |
| Undated record next to a record dated today | `unknown` (the neighbour's date is no longer used) |
| `exported_at` header | `time_basis ingest` and `unknown` |

Before the fix, all three were `stale False, age 0-0.3`.

**D1 leak (F3):** the `CodeErr(code=<input text>)` probe now stores `reason 'CodeErr'`, and health shows
`normalize:CodeErr`. No input text reaches either.

**Migration:** I reran the f96f263 to HEAD upgrade probe (`mig_test.py`):
- Migrations 1-4 applied in order and the old rows are intact.
- Ledger rows `line:3` and `line:6` were written, health is `degraded`.
- An edit of the old forward gets an `origin_key` and keeps `root_method content`.

**Mutations** of HEAD (`$SCR/rr_mut`), number of failing tests out of 34:

| Mutation | Failing tests |
|---|---|
| M2 export own `object_key` | 4 |
| M3 old evidence time | 1 |
| **M4 ledger outside the page transaction** | **1 (killed; it survived before)** |
| M5 `str(exc)` for any exception | 2 |
| M6 no future-time guard | 1 |
| M7 origin-root override reinstated | 7 (incl. the S1, S2/S3, S4 and S5 tests) |
| M8 `support.evidence_time` ignores `time_inferred` | 2 |
| M9 any `.code` reaches the ledger | 1 |
| M10 `exported_at` back in `_content_time` | 1 |
| M11 `reason` uncapped | 1 |

Every fix is pinned by at least one test.

### F5

- Done: JSON-file invalid entries now carry a hash of the item (local_export.py:141-142).
- Not done: the eviction counter (no `evicted` anywhere in `mycelic/ingest`). It was optional and remains a follow-up.
- Side note: the new `__sha256__` also changes the cursor digest for JSON files that contain non-object entries. The
  effect is the same as for JSONL: a one-time full re-read after the upgrade, absorbed by event-key dedupe.

### Rulings on the engineer's open questions

**(a) All-undated support: freshness `unknown` (stale False) and the gate can still say `supported`.**
**Acceptable; not blocking.**
- It matches the existing semantics for references that carry no time at all (support.py `freshness`: no stamps means
  `unknown`).
- It strictly improves on the parent, which showed this case as fresh with age 0.
- Mixed support is handled correctly: the newest *dated* reference decides, so undated references cannot rescue stale
  evidence (M8 kills 2 tests).
- Forcing `stale` would misstate the facts ("old" is not known). It would also block honestly undated sources forever and
  add no security: whoever can strip dates from an export can just as easily write `created_at = now`.

Recommended follow-up, not blocking: make it visible. Today `gate.py:268-285` adds no reason when `fresh["unknown"]`, so a
`supported` claim with no dated evidence looks the same as a dated one. Add a reason such as "no dated evidence:
freshness unknown (N undated reference(s))" there and in the recompute path (knowledge/service.py:496-500). An org
policy knob (`require_dated_support`) could optionally demote such claims to `hypothesis`.

**(b) `knowledge/service.py:939 evidence_freshness` does not select `meta`.** **Not blocking.**
- Its only consumers are the admin overview (`api/routes_admin.py:104/116`) and the Prometheus gauge
  `mycelic_evidence_stale` (`api/app.py:99`). Neither feeds the gate.
- Claim status uses `freshness(supporting, ...)` on full rows (gate.py:253, knowledge/service.py:342/486), and those
  already carry `meta`.
- Effect today: undated references are counted as "fresh" in the dashboard, not as "unknown".
- Fix in WP1's file: `SELECT freshness_at, observed_at, meta FROM evidence_refs ...` (knowledge/service.py:939). It should
  go in with WP1's next change, with a one-line test.

### Not blocking, noted

- `test_s6_a_forged_link_can_only_merge_never_inflate` asserts `roots <= 2`. It does not pin the S6 = 1 suppression as
  documented behaviour. That is fine, but if the owner later chooses suppression resistance, this test will not flag the
  change.
- As INGESTION.md:144 now states, consumers that group by `source_root_id` alone do not see origin merges. The
  cross-department unit rule (`gate.independent_units`, gate.py:88) does use `root_groups`, so it is merge-aware. I
  checked; no gap there.
