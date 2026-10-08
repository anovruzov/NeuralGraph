# G0: what may cross a site boundary, and the text-leakage scan

**G0 proves only that text did not leave.** It plants canaries in raw records and scans every byte that crossed a
site boundary for them and for narrative text. STRATEGY section 6.4 calls this the weakest leak mode: counts and
claims can reveal things that no text scan sees. X5 (attribute and membership inference by a red team holding an
HQ-level token) must run before anyone says "no leakage". Every `leakage.json` says `"scope": "text-only"` and lists
what it does not cover (section 7). Section 12 is X5 on synthetic worlds (B3): the published leakage figure, filled
from its run file whichever way it falls.

Every number in this document comes from runs in the worktree on **synthetic data** (the packs' seeded worlds, with
fictional sites, products, people and narratives) and a **fake model** (an in-process provider that replays the
lexical extractor). None of it measures a model, a partner's data or a real site.

## 1. What may cross, and why

A site keeps its records, claims and extraction stats in its own SQLite file (`site-<id>.sqlite3`, ARCHITECTURE
section 12). Six artifact types cross one `Boundary` (`edge/egress.py`), each in one direction. G3 let two leave;
G6 adds the verdict, which leaves, and the question, which comes in (section 9); G7 adds the packet request, which
comes in, and the packet summary, which leaves (section 10):

| Artifact | Direction | What it holds | What it never holds |
|---|---|---|---|
| `cells_bundle` | out | weekly count cells per (entity type, entity id, predicate, ISO week, channel): `n` records, `n_roots` distinct roots, `n_reporters` distinct reporters, and `res_conf_min` when `n` is exact | narrative, persons, reporters, record refs, dates finer than a week, totals or marginals, forwarded-in records |
| `usage_summary` | out | per (task, endpoint), extraction only: calls, ok, errors by kind, missing-token counts, a fake flag; token sums and latency p50/p95 only when calls >= k; with an int `calls` and any part below k, `ok`, every error kind and both missing-token counts as `'suppressed'`, with no token sum and no latency; an exact missing-token count only as a sum of whole parts, and a token sum only beside one | `ts`, `ref`, `host`, `run_id`, model names, per-call rows, the judge's usage, a `'<k'` part beside an int `calls`, any count from which a part below k can be worked out |
| `question` (G6) | in | one candidate key, a pack template id, the params `{entity_type, entity_id, predicate}`, a window of closed ISO weeks, `as_of`, the pack id and hash, the question id | any text (the template's display text stays at HQ), a record ref, a count |
| `verdict` (G6) | out | `confirm`, `refute` or `unknown`; four count buckets (`'<k'`, `k-9`, `10-49`, `50+`); the newest confirming week; one opaque 16-hex `evidence_ref`; `truncated`, `quality`, `secret_mode`; a reason only for `budget` or `no_secret` | text, an exact count, a record ref or any per-record handle, judged or failure counts, the site's own reason for an unknown |
| `packet_request` (G7) | in | one follow-up key, the question id, the candidate key, the question's window, `as_of`, the pack id and hash | any text, a record ref, a count |
| `packet` (G7) | out | a status; the stored verdict, its three buckets, its `evidence_ref` and `truncated`; per pack code `'suppressed'` or a bucket from k up (complementary suppression); the master-data ids named in the structured fields of at least k confirming records, as buckets | a total, an exact count, a record ref, any text, an id below k, an id named only in a narrative, persons, the reporter |

The rules, each enforced in code and tested:

- **k-suppression, per field.** `n`, `n_roots` and `n_reporters` are each either an int >= k or the literal `'<k'`.
  No count in 1..k-1 can pass the Boundary (`k` is 3 in `device_quality` and 5 in `claims_integrity`). A count of 0
  never appears: there are no zero cells. `res_conf_min` is sent only when `n` is exact.
- **Closed weeks only.** A week W is closed at `as_of` when its Sunday plus the pack's `close_lag_days` is on or
  before `as_of` (`device_quality` 14 days, `claims_integrity` 21). A bundle covers `(after, closed_through]`.
- **Never revised.** `after` must equal the last `closed_through` the site sent (the Boundary's `sequence` check),
  and every week at or before it is final, including weeks without a record. A record that arrives for a week
  already sent is *late*: it counts in its ingest week, or in the first week after the watermark when the site clock
  is behind, and gets a `late_records` row. The earlier bundle's bytes never change.
- **Forwarded-in records are excluded.** A record whose origin is another site never contributes to `n`, `n_roots`
  or `n_reporters` there; a forward within the same site counts, with its origin as its root.
- **Master data.** With the pack's `require_master_data` on, an id of a type with an id format leaves only when it
  is in the site's master data; a type the site has no master data for leaves nothing. Alias-only types (closed
  pack vocabularies, such as components) always pass. `device_quality` has it on; `claims_integrity` has it off.
- **A closed structure.** Unknown keys anywhere, booleans, floats or strings where a count belongs, `'<k '` with a
  space, non-canonical ids, unsorted or duplicate cells, weeks outside the window, a wrong `after`, NaN or non-JSON
  values are all refused. The error (`EgressError`) names the artifact type, a schema path and a keyword, never a
  value; a refused send writes nothing to either log.

**What the built-in volumes produce.** At the packs' own synthetic weekly volumes, every weekly cell is `'<k'`:

| Synthetic run (seed 11, 1,000 records, 6 sites) | cells | cells with `n` >= k |
|---|---|---|
| `device_quality`, master data on (its default) | 3,080 | 0 |
| `claims_integrity`, master data off (its default) | 3,374 | 0 |
| `claims_integrity`, master data on | 2,419 | 0 |

So each site's whole weekly series is suppressed. That is valid output, and G4 and G5 must expect it: a weekly
detector over these cells sees presence, not counts. On a high-volume copy of each pack (two sites at 120 to 160
records a week, seed 7, built by the tests), `CellSuppressionTests` printed 843 cells with `n` >= k out of 5,173 for
`device_quality` (262 of them with `n_reporters` still `'<k'`) and 443 out of 3,778 for `claims_integrity` (410 with
`n_reporters` `'<k'`); those are synthetic test numbers too.

## 2. Usage summaries

A cumulative usage summary sent every week would reveal small deltas (one call in a week), so usage follows the cells'
discipline: each emission covers the ledger rows whose timestamp falls in a newly closed week and that no earlier
summary covered (the store records how many rows each summary consumed), so every ledger row is summarised exactly
once. Counts are suppressed per field (`calls` and each error kind `'<k'` below k; `ok` and the missing-token counts
may also be 0); tokens and latency are withheld unless `calls` is at least k and no part is withheld (below). A row
whose week is still open waits for the next window. The per-call ledger never leaves the site, and the run files
(section 11) carry a site's usage only as these summaries. A site without a model sends no usage.

**Complementary suppression (audit round 2).** `calls` is `ok` plus the error counts, so a `'<k'` part beside an int
`calls` was not suppressed at all: `calls 7, ok 6, errors {http_5xx: '<k'}` gives exactly one failure. Now, when
`calls` is an int and any part (`ok` or an error kind) is below k and not 0, `ok` and every error kind go as
`'suppressed'`, a value of any size held back.

**The rest of the group follows the parts (review of audit round 2).** Round 2 still sent the missing-token counts,
the token sums and the latency beside a withheld split, and each gave the split back. A transport failure (timeout,
network, 5xx) carries no token counts, so `calls 8, ok 'suppressed', errors {timeout: 'suppressed'},
tokens_in_missing 6` read as 6 timeouts and `ok = 8 - 6 = 2`, below k. A token sum divided by the known prompt size
counts the rows it covers, and a failure's latency at the deadline moves the interpolated p95 by how many failures
there were. Now a withheld split withholds the whole group: HQ reads `calls`, which error kinds occurred and the fake
flag, with both missing-token counts `'suppressed'` and no token sum or latency. When no part is withheld, a
missing-token count goes exact only when, within each part, every row or none lacks the tokens, so it is a sum of
whole parts, each 0 or at least k; otherwise it goes as `'suppressed'` and its token sum stays home. Every row count
HQ can then work out (the parts, the rows with and without tokens, their differences) is 0, at least k, or a sum of
such parts. The Boundary enforces it independently of the site: with an int `calls` no part and no missing-token
count may be `'<k'`; `'suppressed'` parts come all together and take both missing-token counts with them, with no
token sum and no latency; otherwise `calls` must equal the sum of the parts, an int missing-token count must be a sum
of whole parts, and a token sum goes only beside an int missing-token count. `UsageTests` checks every mix of up to
three parts (with and without rows lacking tokens) against those rules.

**The judge's usage stays at the site (audit round 2).** The judge makes one call per retrieved record, so a week's
judge calls, `ok` and token sums are the exact counts of records retrieved and judged for that week's questions; HQ
decides when it asks, so it could ask one question in a week and read the counts the verdict's buckets hide (G6 to
G8 sent them). A usage summary now names only the extraction task (the Boundary's task list has nothing else), and
`emit_usage` passes over the judge's rows while still consuming them in the ledger position.

## 3. The canaries

`leakage.plant_canaries` works on deep copies of the records, from a `random.Random` the runner seeds with
`g0-canaries:<seed>`. The examples below are invented for this document.

| Class | Where | Example | What should happen |
|---|---|---|---|
| **a**, letter-only | appended to every narrative, every person field without a class-b token, and every distinct reporter (one token per reporter value, so reporter counts are unchanged) | `Qzxv` + 12 random lower-case letters, e.g. `Qzxvplmkrtwsbcjn` | never extracted, never leaves |
| **b**, id-shaped person data (25% of records) | a person field holds exactly an id of an egress type, mirrored into the narrative as `patient ref L483920K.` | `L483920K` (lot format) | extraction drops it as a person value |
| **c**, id-shaped, narrative only (25% of records) | only in the narrative, as `<field phrase> <id>.` | `IP-604` (product format) | extracted as a claim; the site drops it as not master data, or, with master data off, it leaves as a cell key (section 7) |

Draw rules (a re-draw on any failure; more than 50 re-draws stop the run):

- a class-a core is re-drawn while any of its 8-letter windows is in the pack's text (every config string, the world
  spec it was loaded with and the artifact schema words), is used by another canary, or consists only of `a` to `f`
  (which a hex digest could contain), or while the token reads as an entity mention or matches a lexicon term, a
  negation cue or an alias;
- an id canary is drawn from its type's own id format and re-drawn unless it is canonical, outside the world's ids,
  master data and alias targets, absent from the pack's text, at least 6 characters long, holds a letter `g` to `z`,
  is not a substring (either way) of another canary, and its sentence scans to exactly that one mention. Formats that
  cannot reach 6 characters are never used (the illustrative supplier format, `V` plus four digits).

The manifest (`private/manifest.json`) is the only file that holds tokens. It is written outside every scanned path,
and `scan` refuses an artifact that is the manifest, contains it, or holds a symlink to it.

## 4. The scan

Each artifact (a file, a directory walked without following directory links, or bytes in memory) is read as raw
bytes, so SQLite pages and `-wal` files are scanned too. Each file gives up to three views: the bytes decoded as UTF-8
with replacement, then JSON-unescaped once and, if that changes it, twice (`\uXXXX` in either hex case with surrogate
pairs, and the short escapes). Decoding the artifact catches every escaped form of a canary or narrative, including
`ensure_ascii` output, mixed escaping and double-encoded JSON.

- **Canaries**, case-insensitive: any 8 consecutive core letters of a class-a canary (`match: core_window`, or
  `token` when the whole token is there), or a whole id token. Hits are kept once per (artifact class, file, canary
  id), preferring `token` over `core_window` and the raw view over an unescaped one.
- **Narrative shingles**, exact: every 24-character window of every planted narrative. Windows that also occur in
  the pack's config text (the `config_hash` file set) are excluded and counted, so a phrase every site shares by
  configuration is not counted as leaked; the world spec's templates are *not* excluded, since they are not public
  configuration and excluding them would blind the scan. An artifact's overlap is the UTF-8 byte length of the union
  of matched positions, the maximum over its views, so escaping does not double count.

**What is scanned.** Everything that crossed: each site's egress log, the whole HQ directory (so later `-wal` files
are covered), and each `cells_bundle` and `usage_summary` body from the HQ log, separately. Since G6 also every
question (each line of `hq/questions.jsonl`), every verdict body of the HQ log, each site's ingress log, and HQ's
collective store with its `-wal` (section 9). **What is never scanned as crossing:** the site databases and the
manifest. The site ledgers stay at their site; they are scanned as a
separate hygiene class (`site_ledger_hygiene`), which must also be clean. **The positive control** scans the first
site's database: the scanner must find canaries and narrative text there, or the run fails, because a scanner that
finds nothing anywhere proves nothing.

Measured on the runs of section 1 (G3, the edge stage only; synthetic, fake model): every run exits 0 with `hits`
empty and `shingle_overlap_bytes` 0. The positive control found 880 canary hits and 40,318 bytes of narrative overlap
in `edge/site-plant-ashvale.sqlite3` (`device_quality`), and 1,180 hits and 43,524 bytes in
`edge/site-motor-north.sqlite3` (`claims_integrity`). The `device_quality` run planted 4,297 canaries (a 3,789,
b 240, c 268) and scanned 494,528 bytes of cell bodies, 499,664 bytes of HQ log, the same of site egress logs and
2,671 bytes of usage summaries; its ledgers (563,827 bytes) were clean. Section 9 has the same runs with G6's
pushdown stage.

## 5. `leakage.json`

| Key | Meaning |
|---|---|
| `kind`, `schema_version`, `synthetic`, `data_label` | `g0_leakage`, 1, true, `synthetic` |
| `pack`, `pack_version`, `illustrative`, `config_hash`, `base_config_hash` | the pack the run used; `base_config_hash` is the pack named on the command line |
| `require_master_data`, `require_master_data_overridden` | the setting used; true when `--require-master-data` differed from the pack, which writes a pack copy with its own `config_hash` |
| `seed`, `records`, `sites`, `weeks`, `world_digest` | the synthetic world |
| `mode`, `models_fake` | `fake`, `lexical` or `routing`; true for the fake provider, false for routing, null for lexical (no model) |
| `as_of`, `clock` | the simulated clock: ingest the day after the last record, emit when every record week and the ingest week are closed |
| `stages` | the stages that ran (G3: `edge`; G6: `edge`, `pushdown`) |
| `pushdown_totals` | G6: the judge (`fake`, `lexical` or `routing`), the secret mode, detector and constructed candidates verified, questions, routes, verdicts by kind and conclusions by status |
| `scope`, `not_covered` | `text-only`, and section 7's list |
| `canaries_planted`, `canaries_by_class` | totals per class |
| `artifact_classes`, `scanned` | bytes and items per crossing class; each scanned artifact with its bytes |
| `hits`, `hit_count` | at most 1,000 hits (artifact class, file, canary id, class, match, view), and their number |
| `known_limitation`, `known_limitation_note` | class-c id hits when master data is off, by canary id; the note is set when the list is not empty |
| `shingle_overlap_bytes`, `shingle_hits`, `excluded_vocabulary_windows` | narrative bytes found, per file; distinct config-text windows seen in crossing artifacts |
| `shingles` | window size, narratives, short narratives, windows searched, windows excluded as vocabulary |
| `site_ledger_hygiene` | the hygiene class: bytes, items, hits, overlap |
| `positive_control` | the database scanned, its canary hits and narrative overlap |
| `edge_totals` | totals across sites only: records ingested, rejected, duplicates, forwarded in, late, extracted, claims, cells, cells with `n` >= k, suppressed fields, not master data, non-egress type, usage groups |
| `model_path` | null in `lexical` mode; else (audit round 3) the extraction records by extractor and error kind, the judge's attempts, the ones a model answered and the failed ones by kind, the degraded verdicts, and `problems`: every way the run did not go through the models it names (counts only) |
| `passed` | no hit, no overlap, clean ledgers, a positive control that found both and, with a model, no `model_path` problem |
| `code_commit`, `code_dirty`, `code_hash`, `created_at` | code stamps; `created_at` is the only wall-clock value |

The report never holds a token, and the run's one-line summary prints only counts.

## 6. Exit codes

0 when `passed`; 1 when a canary or narrative text crossed, a ledger is not clean, the positive control found
nothing, or (with a model) `model_path` lists a problem, so a run whose records never reached the model cannot pass
as a check of the path through it; 2 on a usage or configuration error (a non-empty `--out`, a bad pack or routing
file). Known-limitation entries do not fail a run.

## 7. What G0 does not cover (verbatim from `leakage.NOT_COVERED`)

- Attribute inference on counts and claims (X5).
- Membership inference (X5).
- Differencing between verdict buckets and weekly cells (G6, X5).
- Cross-site duplicates without an origin marker, which each site counts as independent (X5).
- A '<k' cell still reveals that an entity had at least one record with that predicate in that week, and entity ids
  are emitted in clear by design (STRATEGY section 6.4).
- Usage summaries reveal weekly extraction-call volume, token sums and latency per site, with counts of at least k
  (the judge's usage stays at the site).
- Encoded or transformed text (hashes, base64, translation, paraphrase).
- Fragments shorter than 8 canary-core characters or 24 narrative characters.
- Strings split across SQLite pages.
- Presence or absence of an entity at a site in a question window, revealed by a refute versus an unknown; limited,
  not prevented, by the per-entity and per-site daily question budgets (G6, X5); with require_master_data only for
  master-data ids, and never through a person value or the reporter.
- Bucket transitions between overlapping question windows for one key, which can narrow a count inside its bucket
  (G6, X5).
- A packet discloses, for one window, which pack codes and which master-data ids co-occur with the key in at least k
  confirmed records at a site, as count buckets (G7, X5).
- A model drafting at HQ sees only structured inputs; the text scan cannot show that its prose does not restate a
  count or an id in another form (G7).

**Known limitation (verbatim from `leakage.KNOWN_LIMITATION_NOTE`):** With require_master_data off, ids found only in
narratives leave as cell keys (their counts suppressed), so id-shaped person data written into a narrative crosses.
Turn require_master_data on, or keep person identifiers out of id formats.

Measured: `claims_integrity` at its own setting (off) lists 495 known-limitation entries for 165 distinct class-c
canaries (each found in a cell body, the HQ log and the site's egress log); `device_quality` with
`--require-master-data off` lists 552 entries for 184 canaries. Both runs have no hit of class a or b and exit 0.
With master data on, both packs list none.

## 8. How to run it

RUNBOOK section 9 has the commands. Send back `leakage.json` only, never `private/manifest.json`.

## 9. Questions and verdicts (G6)

Pushdown verification (ARCHITECTURE section 15) is the one place HQ talks back to a site. HQ asks a narrow question
about one candidate; the site answers from its own raw records with its in-boundary model (or the lexical judge) and
sends a verdict. Both cross the same Boundary as the cells, through the same validator, and G0 scans both.

**The question (in).** Exactly nine keys: `schema_version`, `pack`, `pack_hash`, `question_id`, `candidate_key`,
`template_id`, `params {entity_type, entity_id, predicate}`, `window {start_week, end_week}` and `as_of`. The params
are an egress entity type, a canonical id (an alias-only type's id is one of its ids) and a pack predicate, checked
before anything is built. The template's display text ("In the last 6 weeks, how many of your records describe ...")
is rendered for HQ's screens and never sent. The site refuses a window that ends after the last week closed by its own
clock (`range`), so a question cannot ask about a week the site has not closed. A question tells the site which key
HQ is looking at; that is what it is for.

**The verdict (out).** Every leaf is an id (the pack, the site), a 64-hex id, a fixed enum string, an ISO week, a
bucket label, the 16-hex `evidence_ref`, a bool or `schema_version` 1. The spec is closed: an int count anywhere, a
count string such as `'7'`, a list of record refs, judged or failure counts and free-text reasons are all refused
structurally, and every cross-field rule is checked (a confirm carries support, roots and reporters buckets, the
newest week inside the window and an `evidence_ref`; a refute carries only the entity-records bucket and a reference;
an unknown carries no bucket, week or reference; the roots and reporters buckets never exceed the support bucket;
`secret_mode` is `none` exactly when the reason is `no_secret`; the `verdict_id` is the sha256 of the body without it
and the reference).

**Buckets.** A count leaves only as a label of `verdict_buckets(pack)`, from the pack's `verdict_count_buckets`
(`[3, 10, 50]` for `device_quality`, `[5, 10, 50]` for `claims_integrity`): `'<k'` for 1 to k-1, then `3-9` (or
`5-9`), `10-49` and `50+`. A count of 0 is never bucketed: a site with no matching record answers `unknown`. HQ uses
each label's lower bound (`'<k'` counts 1).

**evidence_ref.** STRATEGY section 6.3 has a verdict carry `record_ids_local[]`. G6 sends one opaque reference per
verdict instead: `HMAC-SHA256(site secret, verdict_id)`, first 16 hex characters. A list's length is an exact count,
and per-record handles let HQ link records across questions; one keyed reference per verdict lets the site's auditor
resolve it (`SiteVerifier.resolve`) to the local record refs behind it, and lets nobody else. The refs, the judged,
failure and unclear counts, the extraction misses and the site's own reason for an unknown stay in the site's
`verdict_log`.

**The question budget.** A site answers at most `question_budget_per_entity_per_day` distinct questions about one
entity per day of its own clock (5 in `device_quality`, 3 in `claims_integrity`); further ones get `unknown` with the
wire reason `budget`, are not stored, and are answered on a later day. Re-delivering a question answered before
returns the stored bytes and uses no budget. The budget limits how fast HQ can probe one entity with overlapping
windows; it does not prevent it. Since audit round 2 a second budget caps the distinct entities a site answers about
per day (`question_entities_per_site_per_day`, 50 in both packs): a question about one more entity gets `budget` too.
Up to round 2 nothing limited guessing many ids, since the per-entity budget is keyed by the id guessed.

**Questions about ids the cells would withhold (audit round 2).** The cells withhold an id that is not master data
(`require_master_data`, on in `device_quality`) and the extractor drops a claim whose entity is written as a person
value, so id-shaped person data (a patient reference in the lot format, say) never leaves as a cell. The question
path applied neither: a question about such an id read the narratives that mention it and came back `refute` or
`confirm` (with a bucket and a week), while a wrong guess came back `unknown`, a per-guess membership oracle on
person data. Now, with `require_master_data`, a site answers `unknown` for an id outside its master data without
reading a single record (the same body as an id with no records; the local reason `not_master_data` stays in
`verdict_log`), and retrieval never takes a record because its narrative names the id as one of the record's person
values or its reporter. Without `require_master_data` (`claims_integrity`), an id-shaped value that is not written
in a person field is still an id to the site: keep person identifiers out of id formats, as the known-limitation note
says.

**Unknown discloses little.** Only two reasons cross: `budget` and `no_secret`. A site's own reason (no record, unclear
judgements, failures on more than half the records) stays at the site; only `quality: degraded` crosses for the last.
`timeout` and `error` are HQ's own records of a route that did not answer.

**Secrets and rotation.** A site's secret is a file of exactly 64 lowercase hex characters (one trailing newline
allowed); a malformed file stops the verifier at construction. A missing file fails closed: every question gets
`unknown` with `no_secret`, before any record is read or the budget is checked. Demos use `seeded-demo` secrets
(`sha256("mycelic-seeded-demo-secret:<seed>:<site id>")`), stamped in every verdict's `secret_mode` and in E2's
stamps; they are not secrets. Rotating the file makes every earlier `evidence_ref` unresolvable (resolve recomputes
the HMAC with the current secret) unless the old file is kept.

**Residual risks (X5), not hidden.** Three remain and are listed in section 7: a refute versus an unknown reveals
whether a site holds the entity in the window (the two budgets limit it); bucket transitions between overlapping windows
for one key can narrow a count inside its bucket; and verdict buckets can be differenced against weekly cells.
Verdicts also reveal, by design, which sites hold supporting evidence for a candidate.

**What G0 scans for G6.** The pushdown stage opens HQ's collective store at `hqdb/collective.sqlite3` (outside `hq/`,
so the edge stage's whole-directory artifact still covers only the transport logs: `receive.jsonl` with its cell,
usage and verdict rows, and `questions.jsonl`), ingests the receive log, detects (run X), verifies up to 20 detector
candidates and tops them up to 5 with constructed ones, every site answering with a seeded-demo secret. It adds four
crossing classes: `questions`, `verdicts`, `collective_sqlite3` (the HQ database and its `-wal`, read while the store
is open) and `site_ingress_log`.

Measured on the G0 runs of section 1 with the pushdown stage (seed 11, 1,000 records, 6 sites; synthetic, fake
model, which replays the lexical judge): both exit 0 with `hits` empty and `shingle_overlap_bytes` 0.

| | `device_quality` | `claims_integrity` |
|---|---|---|
| questions (candidates verified) | 9 | 7 |
| routes, verdicts | 50: 40 confirm, 10 refute | 41: 35 confirm, 6 refute |
| conclusions | 4 supported, 3 stale, 2 hypothesis | 1 supported, 1 stale, 5 hypothesis |
| bytes scanned: questions, verdicts | 22,398, 30,734 | 19,531, 25,145 |
| bytes scanned: HQ database and `-wal`, site ingress logs | 2,278,368, 32,335 | 2,220,688, 27,555 |

`claims_integrity` at its own setting (master data off) now lists 660 known-limitation entries for the same 165
class-c canaries as before: the fourth artifact holding each is HQ's database, which stores the cells. No question
and no verdict carries one. These are synthetic numbers from fake models, not measurements.

## 10. Follow-up: packets, drafts, the ledger and the outbox (G7)

**Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.** ARCHITECTURE section 16 describes the layer. What it adds to what crosses a boundary:

**Packet requests (in).** HQ asks each target site for one follow-up's evidence packet: the follow-up key (whose
conclusion id must be `c-` plus the first 32 hex of the question id), the question id, the candidate key (an egress
type, a canonical id, a pack predicate), the question's window and an `as_of` that closes it. The site refuses a
window its own clock has not closed. A request is logged in HQ's `packet_requests.jsonl` and the site's ingress log,
never in `questions.jsonl` (D7). A request tells the site which follow-up HQ is running on which key; that is what it
is for.

**Packets (out).** The site answers only from the verdict it stored for the question, and only for its confirming
records. The summary that crosses holds no total, no exact count, no record handle and no text field:

- **codes**: per pack code, the number of confirming records carrying it, sent as a bucket from k up
  (`packet_labels(pack)`: `'suppressed'`, then `3-9`, `10-49`, `50+` for the device pack) or `'suppressed'` below k.
  **Complementary suppression**: when exactly one code is suppressed among two or more, the unsuppressed code with
  the smallest count is suppressed too, so a suppressed count cannot be recovered from the others and the support
  bucket. The validator refuses a packet with exactly one `'suppressed'` code among two or more (keyword
  `suppression`).
- **co-mentions** (D6): an id is listed only when at least k confirming records name it, as a bucket label; an id
  below k is omitted entirely, because listing it would itself disclose it. Co-mentions come only from records'
  STRUCTURED entity fields, resolved exactly, and an id of a type with an id format must be in the site's master data
  whatever the pack's `require_master_data` says. The narrative is never read for a packet, so text in a record (an
  injection included) cannot reach one. The key's own entity is never a co-mention.
- the stored verdict's buckets, `evidence_ref` and `truncated`, copied (a refute or an unknown sends no code and no
  co-mention).

The full packet (the crossing summary plus each confirming record's ref, week, codes, structured fields and
narrative; never persons or the reporter) is written to `<site workdir>/packets/site-<id>/<key digest>.json` and never
leaves the site. G0 never scans those directories as crossing.

**Drafts (HQ).** A T1 draft is written at HQ from structured inputs only: the follow-up type, its args, the
conclusion's key, labels, window, sites, decision unit and support lower bounds, and each packet's status, verdict,
support bucket, codes and co-mentions (no reasons text, no narrative). A draft must then pass the id-scope scan: no
exact or variant mention of an id (of a type with an id format) outside the conclusion's scope and the packets'
co-mentions, whether written as the id or by one of its pack aliases (a product or business named by its name), and
no unresolved lookalike (D8). The scan is conservative where the canonicaliser recognises a form: in
the device pack two letters, a space and a number read as a space-separated product id, so prose such as "at 2" is
refused; the template drafter avoids it. It sees only those forms, though. It catches an out-of-scope id in its
canonical form, in another case, with a homoglyph or a non-ASCII digit, and, for a format with a separator, with a
space for it (device `SD 10`, claims `RS 12345`). It does not see an id restated with a separator its format lacks or
without the one it has: device suppliers `V-1001` and `V 1001`, device products `SD10`, claims repair shops `RS12345`
pass the scan, and so does a business or product named by a name that is not in the pack's alias table. This is the
second G7 residual risk below.

**The template draft restates only what already crossed (B1).** Since B1 the template drafter fills each field from
its pack template (`PACKS.md` section 1.3): the headline and the summary from the conclusion, and the evidence from the
packets with status `ok`, clause by clause: each such packet's site, verdict and support bucket, its codes by label with
their count labels, and its co-mentioned ids with their bucket labels; an affected-lots list holds the conclusion's
entity and the ok packets' co-mentioned ids of that type. Every one of these values is in the conclusion or in a packet
summary that has already crossed to HQ; the draft adds no count, no id and no text that HQ did not hold, and it still
reads structured inputs only (no reasons text, no narrative) and still passes the id-scope scan. What changes is where
they sit: the drafts and the outbox lines now restate the packets' buckets and co-mentioned ids, so a reader of the
outbox learns what a reader of HQ's receive log already could. A field whose template source is `for_owner` is filled
with a fixed sentence (or an empty list) and is left for the named owner to write.

**The ledger and the outbox.** The follow-up ledger (`followups.sqlite3`) holds every proposal, assignment, draft,
decision, execution result (the T0 packets' crossing summaries and the T1 outbox receipts) and outcome; refusals hold
a code, a schema path and keyword or an arg name, never an arg value. The outbox holds one canonical JSON line per
executed T1 draft. Both are HQ files; G0 scans both.

**Residual risks, listed in section 7:** a packet discloses which pack codes and which master-data ids co-occur with
the key in at least k confirmed records at a site, as buckets; and a model drafting at HQ sees only structured inputs,
but the text scan cannot show that its prose does not restate a count or an id in another form.

**What G0 scans for G7.** The follow-up stage (ARCHITECTURE section 16) adds six crossing classes: `packets` (every
packet row of HQ's receive log), `packet_requests` (every row of `hq/packet_requests.jsonl`), `drafts` (every drafted
or edited draft in the ledger), `approvals_ledger` (the ledger file), `outbox` and `hq_draft_ledger` (the central
draft runtime's usage ledger, fake mode). The site egress and ingress logs and HQ's `hq/` directory, already scanned,
now also carry the packet and packet-request lines.

Measured on the G0 runs of section 1 with the follow-up stage (seed 11, 1,000 records, 6 sites; synthetic, a fake
model replaying the lexical extractor, judge and template drafter, simulated approvals; same-author, **not a
measurement**): both exit 0 with `hits` empty and `shingle_overlap_bytes` 0.

| | `device_quality` | `claims_integrity` |
|---|---|---|
| supported conclusions, follow-ups proposed, approved and executed | 4, 8, 8, 8 | 1, 2, 2, 2 |
| types skipped (an entity arg of another type: `scar_draft` on product keys) | 4 | 0 |
| packets (all `ok`), packet codes (of them `'suppressed'`), co-mentions | 21, 43 (42), 5 | 6, 8 (8), 0 |
| drafts, outbox lines, ledger entries | 4, 4, 44 | 1, 1, 11 |
| bytes scanned: packets, packet requests, drafts | 14,339, 8,616, 5,292 | 3,971, 2,598, 908 |
| bytes scanned: ledger file, outbox, central draft ledger | 94,208, 7,409, 2,272 | 61,440, 1,426, 568 |

`claims_integrity` at its own setting (master data off) still lists the same 660 known-limitation entries as in
section 9: no packet, draft, ledger entry or outbox line carries a class-c canary. These are synthetic numbers from
fakes, not measurements. The draft, ledger-file, outbox and central-draft-ledger bytes are re-measured at B1 (the same
runs with B1's template drafter, whose drafts restate the packets' evidence; up to B1 they were 2,611, 90,112, 4,728
and 2,216 for the device pack and 464, 61,440, 982 and 554 for the claims pack); every other cell is unchanged.

## 11. Run files (G8)

G8 writes run files (ARCHITECTURE section 17): the six files of a collective demo run, and a `run/` directory in every
G0 run. Run files are meant to be sent back and committed, so they are scanned like anything that crosses a boundary,
and they must also be portable.

**What a run file may hold.** Counts, buckets, ranks, labels, ids that already crossed (entity ids, codes, question and
conclusion ids, follow-up keys, evidence refs), the follow-up ledger's entries with their payloads, HQ's own usage
ledger rows projected to `runfiles.LEDGER_ROW_KEYS` (at most twelve per task; never a timestamp, run id, record ref,
host or served model), and a site's usage only as the usage summaries that crossed its Boundary (`site_usage`,
k-suppressed as sent, section 2). Never narrative text, a record ref, a person or reporter value, the canary manifest, a
site database, a site's per-call ledger or a site `packets/` directory. The baselines R, U and each site alone appear
only as `{rank, caught, related}`: none of their counts, features or scores is written.

**Why a site's ledger rows never enter a run file.** A site's per-call ledger is site data: the judge makes one call per
judged record and extraction one per record, so a site's rows for one question or one week are its exact record counts,
below k as often as not. Up to G8 the run files copied up to twelve rows per site and task, which put those counts (and,
for a question, the site's exact judged count) into a file meant to be committed and sent. `runfiles.project_ledger` now
refuses any `site:<id>` row, and a site's usage appears only as `crossed_usage` reads it from HQ's receive log: every
group of every usage summary, a `'<k'` count written as null and named in `suppressed`. The site ledgers are still read
inside the run, for one thing only: each site task's endpoint flags (any call, every call to an in-process fake, every
call fake-marked), which label the providers; no count or row leaves that read.

**Hygiene of digests and paths.** Every sha256 in a run file is its first 32 hex characters (`runfiles.shorten`); a
64-hex token is refused, because it reads as a credential (`secret_findings` of the live-demo tests), and a string
that holds one inside longer text is refused rather than kept. `runfiles.portability_problems` refuses an absolute
path, the work and output directories, the repository root, the home directory, the host and user names (whole
tokens), a credential-named key and the agent-key prefix; `write_run_files` checks every file before writing any, so
a problem writes nothing. The full digests stay in the stores of the work directory, which the demo removes at exit.

**G0's `run_files` stage** runs after the follow-up stage. It writes `run/ledger.jsonl` (HQ's central draft ledger only,
projected and capped; the sites' usage is the scorecard's `site_usage`), `run/approvals.jsonl` (the follow-up ledger's
entries and its head), `run/trace.json` (the stages, every question's rendered text and every conclusion with its
verdicts' buckets) and `run/scorecard.json` (digests, the stage totals, the ledger summary and a content hash), and
scans the four files as the crossing class `run_files`. A stage that writes narrative text into `run/` fails the run
with a shingle hit of that class (`G0RunnerTests`). `leakage.json` gains `run_files_totals` (`files`, `bytes`,
`ledger_rows_total`, `ledger_rows_written`).

Measured on the G0 runs of section 1 with the run-files stage, re-run after the change above (seed 11, 1,000
records, 6 sites; synthetic, a fake model, simulated approvals; same-author, **not a measurement**): both exit 0 with
stages `edge`, `pushdown`, `followup`, `run_files`, `hits` empty and `shingle_overlap_bytes` 0.

| | `device_quality` | `claims_integrity` |
|---|---|---|
| run files scanned, bytes | 4, 57,466 | 4, 26,224 |
| HQ usage ledger rows: total, written (at most twelve per task) | 4, 4 | 1, 1 |
| crossed usage groups in `site_usage` | 6 | 6 |
| follow-up ledger entries in `approvals.jsonl` | 44 | 11 |

Up to G8 the same runs wrote 1,485 and 1,538 usage rows in total (148 and 145 written), nearly all of them the
sites' own. The run-file bytes are re-measured at B1 (the template drafter's longer drafts in `approvals.jsonl`; they
were 54,784 and 25,780 before).

**The demo's two scans.** A collective demo run scans twice, with the scenario's canaries planted at every plant
(classes a, b and c) and its narratives as the shingle source:

- `after_pushdown`, when the check finishes: HQ's cells, usage summaries, questions and verdicts (each receive-log row
  by artifact type), every plant's egress and ingress log, and HQ's database (and its WAL);
- `final`, at the end: all of that plus the packet requests, packets, the follow-up ledger, the outbox, the central
  draft ledger, every draft, and the four primary run files that exist by then (`scorecard.json`, `trace.json`,
  `ledger.jsonl`, `approvals.jsonl`; class `run_files`).

The plants' usage ledgers are scanned apart as hygiene (`site_ledger_hygiene`). A positive control scans the first
plant's own database and must find canaries and narrative text. After `screen.json` is built, the demo's self-check
scans all six final files and runs the portability check; any hit stops the run with nothing written. Both scans and
the positive control are in `leakage.json`, and the check beat shows the first one.

On the committed demo run (B1a: `collective-halvern-b1a`; synthetic, same-author, the deterministic stand-in model,
**not a measurement**): both scans have `hits` empty and `shingle_overlap_bytes` 0; the final scan covers fourteen
classes, `run_files` among them (four files, 50,493 bytes; HQ's draft ledger only, the plants' usage as
`site_usage`); the positive control finds 952 canary hits and 44,050 overlapping bytes in the first plant's
database. The committed run's six files also pass a re-scan
against a rebuilt world in the tests (`CommittedRunTests`).

**What the run-file checks do not cover.** They are text checks, as everything here: they cannot show that a run
file's counts, buckets, ranks or co-mentions reveal nothing (section 7, X5). A run with `--routing` names the endpoint
and the model tag the founder configured; that is a label, not a secret.

## 12. X5: leakage beyond text (the published figure)

X5 here is synthetic, same-author and internal only: the worlds, the red team and the defences were written by the
same AI system on the packs' synthetic generators, so these figures describe these artifacts on these worlds and are
never a buyer claim.

Sections 1 to 11 show that text did not leave. X5 (STRATEGY 6.4 and 11.2) asks what the things that do leave (counts,
buckets, verdicts, packets, ids, ranks) reveal about individual records, against a pass bar of at chance or near it.
The bar was fixed in B3a's code before the B3b prereg, but after one full rehearsal whose outcome existed (the
rehearsal disclosure below). `experiments/x5_inference.py` runs it (RUNBOOK section 18, ARCHITECTURE section 18) and
`experiments/x5_attacks.py` holds the attacks as pure functions. Whatever the result, it is published here; nothing
in this section is a "no leakage" claim unless the stored labels below say so.

**What the red team holds.** Exactly what HQ holds after G0's four stages (edge, pushdown, follow-up, run files) on a
world's member records, each as its own artifact type: the cells by channel (`cells_codes`, `cells_text`), the usage
summaries (`usage_summary`), HQ's own questions and verdicts (`verdicts_passive`), the packets (`packets`), the drafts,
outbox, approvals ledger and central draft ledger (`followup`), HQ's store results (the detection result, recomputed
from the store, and the conclusions, `hq_results`) and the run files (`run_files`); `all` is their union. It knows
the packs, the lexical extractor and every site's master data. It never holds a site database, a narrative, a
person or reporter value, a site's packet directory or the generator's truth.

**What it may do.** Read those artifacts, and ask questions of its own through the real, budgeted site verifier
(`verdicts_active`, attack A6): a few sampled entities per site, every window of the pack's minimum length, starting
the day after the pipeline's `as_of`, never more new windows per entity per day than the pack's budget. A `budget`
answer is asked again on a later day and never read as absence.

**The worlds.** Each world is a pack's seeded synthetic world of 2n records; an original record is a member by a
seeded coin, a forwarded copy follows its origin, and only members go through the pipeline, so non-members are the
matched outsiders for membership. Shadow worlds (other seeds) never run a pipeline: they give every prior and the A1
threshold, never target-world truth. Variants: `default` (the pack), `k2` and `k10` (egress k and the verdict
buckets), `rmd_flipped` (`require_master_data` negated), `minus_type` (the pack's primary entity type no longer
leaves) and `volume` (every site's weekly volume times the pre-registered factor); `k1_reference` replaces the default
worlds' cells by exact counts (reference, not deployable) and `a5_injected` adds a cell naming each A5 target's
surname after the Boundary (a positive control; the Boundary would refuse it).

**The attacks** (per-target truth never reaches an attack function; each has a matched chance baseline that uses the
same targets and the attacker's knowledge minus the artifact):

| Attack | Target | Truth | Matched baseline |
|---|---|---|---|
| A1 membership (`A1_calibrated` primary, `A1_fixed` exploratory) | original records of a (site, week) stratum, balanced | member or not | 0.5 exactly |
| A2 predicate attribute inference | member records with a structured entity and an affirmed predicate | the record's affirmed predicates | the shadow prior chain (entity and site, entity, type, overall) |
| A3 count inference inside the protected range | every true cell with 1 <= n < k | n | the shadow mode over [1, k-1] |
| A4 reporter linkage inside the protected range | every pair of records in a true cell with 2 <= n < k | same reporter or not | the shadow majority |
| A5 person-name negative control | member records with a name field | the surname | the shadow mode per field |
| A6 presence oracle | sampled (site, entity, window) | a member record at the site names the entity in the window | the shadow presence share |

**A1 per artifact type.** A record's A1 score counts only the keys of the channels the attacked type can carry
(`cells_codes` and the allowed-fields reference: the codes channel; `cells_text`: the text channel; every other type:
both), and `A1_calibrated` uses the threshold calibrated on that type's own shadow facts: the two cell types, `all`
(from the shadow cells of both channels, its only part with a shadow analogue), the allowed-fields reference (R
(model-free)'s exact cells of the shadow members) and `allowed_plus_all` (both together). Shadow worlds run no pipeline,
so the verdict, packet, follow-up, HQ-results and run-file types have no shadow facts: `A1_calibrated` is not applicable
to them, and only `A1_fixed` (every key present) reads them, which can understate their membership leakage. Up to audit
round 4 every type borrowed the threshold of the cells' union and divided by keys it could not carry, which understated
the per-type and reference entries and overstated the incremental entry (`INTEGRATION.md`, audit round 4); the `all`
entries, the primary family and the bar did not depend on it.

**Labels** (pre-registered; read only from the cluster-bootstrap interval of the advantage over the matched
baseline, clusters (seed, site, week), for A6 (seed, site, window start); Wilson intervals of the accuracy are shown,
never used for a label):

- `leak`: `ci_low > 0`
- `at_chance`: `-0.05 <= ci_low and ci_high <= 0.05`
- `inconclusive`: `otherwise`, and every entry with no target

Only the eight primary tests (A1_calibrated, A2, A3 and A4 on `all`, default variant, both packs) also carry a
Bonferroni label, at alpha 0.05 / 8 from the same bootstrap replicates (so its interval contains the 95% one); every
other entry is exploratory.

**The primary sentences** (`x5_attacks.SENTENCES`, keyed by the 95% label and the Bonferroni label; one per primary
test, in pack order, then A1_calibrated, A2, A3, A4). Since the Bonferroni interval contains the 95% one, six pairs
can occur:

- (leak, leak): On {pack}, {attack_name} leaks: advantage {estimate} over chance (95% CI {ci_low} to {ci_high}),
  above 0 at the 95% and at the Bonferroni level for {family_size} tests; this fails STRATEGY 11.2's X5 bar (at chance
  or near it).
- (leak, at_chance): On {pack}, {attack_name} shows a leak at the 95% level (advantage {estimate}, 95% CI {ci_low} to
  {ci_high}) that lies within 0.05 of chance at the Bonferroni level for {family_size} tests; this still fails
  STRATEGY 11.2's X5 bar (at chance or near it), since the pre-registered bar reads the 95% label.
- (leak, inconclusive): On {pack}, {attack_name} shows a leak at the 95% level (advantage {estimate}, 95% CI {ci_low}
  to {ci_high}) that does not stay above 0 at the Bonferroni level for {family_size} tests; this still fails STRATEGY
  11.2's X5 bar (at chance or near it), since the attack is not shown to be at chance.
- (at_chance, at_chance): On {pack}, {attack_name} is at chance: advantage {estimate} (95% CI {ci_low} to {ci_high}),
  within 0.05 of chance at the 95% and at the Bonferroni level for {family_size} tests; this meets STRATEGY 11.2's X5
  bar for this attack on synthetic, same-author worlds.
- (at_chance, inconclusive): On {pack}, {attack_name} is within 0.05 of chance at the 95% level only (advantage
  {estimate}, 95% CI {ci_low} to {ci_high}), not at the Bonferroni level for {family_size} tests; inconclusive against
  STRATEGY 11.2's X5 bar.
- (inconclusive, inconclusive): On {pack}, {attack_name} is inconclusive: advantage {estimate} (95% CI {ci_low} to
  {ci_high}); this run does not decide STRATEGY 11.2's X5 bar for this attack.

**The bar sentence** (`x5_attacks.BAR_SENTENCES`; exactly one opens the result: `fails` when any primary 95% label is
`leak`, else `met` when every primary Bonferroni label is `at_chance`, else `undecided`):

- fails: STRATEGY 11.2's X5 bar fails on these synthetic worlds: at least one primary attack leaks at the 95% level;
  the claim that nothing leaks beyond text may not be made.
- met: STRATEGY 11.2's X5 bar is met on these synthetic, same-author worlds only: all {family_size} primary attacks
  are at chance at the Bonferroni level; this describes these artifacts on these worlds and is never a buyer claim.
- undecided: STRATEGY 11.2's X5 bar is undecided on these synthetic worlds: no primary attack leaks at the 95% level,
  but not every primary attack is at chance at the Bonferroni level; the claim that nothing leaks beyond text may not
  be made.

**Controls.** Positive controls, in real artifact formats and read by the run's own extractors, must be labelled
`leak` on `all`: `k1_reference` for A1_calibrated, A2, A3 and A4, and `a5_injected` for A5; otherwise the run exits 2
("harness cannot detect a known leak") and nothing is published. The negative control is A5 on `all` for every pack
and measured variant (nothing that crosses names a person): it must be `at_chance` or `inconclusive` with an interval
that holds 0, otherwise the run exits 1 and nothing is published until it is investigated.

**Designed disclosures** (STRATEGY 6.4; separate rows, never labelled `leak`, never mixed into A3 or A4): a `'<k'`
cell says an entity had at least one record with a predicate at a site in a week, and a cell of at least k is an exact
count. A1 and A2 do read the `'<k'` presence cells, because that is the X5 question; if they leak, presence cells are
the cause and the simulated transforms below show what removing or coarsening them would buy.

**Mitigations.** The measured knobs are the variants (`k2`, `k10`, `rmd_flipped`, `minus_type`, `volume`), each run
through the whole pipeline. The simulated transforms (`drop_lt_k`: cells below k removed; `four_week`: cells rebuilt
over four-week periods at the pack's k) are labelled exactly "simulated: not implemented in the Boundary; implementing
it is a code change": they replace only the cells, keep the rest of the unmitigated run's artifacts and do not re-run
detection.

**Rehearsal disclosure.** A full rehearsal of this experiment ran before its code froze. On the uncommitted tree that
became B3a (commit 1cee431), the B3b prereg command was run with `--allow-dirty` (seeds derived from 202dd531, not from
B3a), and a one-seed run of both packs with every variant, attack, artifact type and simulated transform at n = 1000 and
the default bootstrap sizes, on a prereg with explicit seeds 101 and shadow 102, exited 0 after 632 s. A run exits 0
only after writing `x5.json` and `leakage_section.md`, which hold every label and the bar outcome, and B3a's code prints
the bar outcome on its last stdout line (`bar <outcome>`). Every control holding follows from the exit code alone;
whether the uncommitted code printed the bar line too, and whether anyone read it, was not recorded, so the outcome
counts as seen. Both files were deleted without a sha256; the outcome was not recorded and cannot be recovered; whether
any code, label rule or sentence changed after it was not recorded either. So B3a's code, label rules, primary family
and bar were fixed after one outcome-bearing run of the same experiment on other seeds: the result below is
pre-registered only with respect to the B3b prereg's own seeds. The B3a commit message's "No X5 prereg was made and no
x5.json written" is wrong on this point (`INTEGRATION.md`, B3a and audit round 4). Audit round 4 changed the X5 code
again after that rehearsal (A1 per artifact type, above); on the test fixture that change leaves every `all` entry, the
primary entries, the controls and the bar byte-identical. The test suite's fixture (one device world, n = 400, B = 100)
also computes a bar outcome every time the suite runs; it is a fixture and is never read as a result.

### The result

<!-- x5:begin -->
Filled at B3c from docs/collective/x5/x5.json; nothing here until then.
<!-- x5:end -->
