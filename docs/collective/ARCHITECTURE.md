# Mycelic collective: architecture (gates G1 to G3)

This document describes what gates G1 to G3 build under `mycelic/collective/`. It also places them in the loop
that later gates complete (STRATEGY section 4.1). Sections 1 to 10 describe G1; section 11 describes G2 (domain
packs and the sense step); section 12 describes G3 (the site boundary and the G0 text-leakage scan).

**No real-model number is produced in this sandbox.** Model weights and the openFDA API cannot be reached from it, so
every test runs against a deterministic in-process fake or a local fake HTTP server. Every harness output says so in
its `measurement` flag. The figures STRATEGY needs come from the founder's runs (`RUNBOOK.md`).

## 1. What G1 builds

| Part | Module | Purpose |
|---|---|---|
| Strict JSON | `jsonio.py` | Refuses byte-order marks, invalid UTF-8, NaN/Infinity (and `1e999`), duplicate keys and deep nesting; canonical serialisation for hashes, ledgers and run files |
| Schema subset | `schemacheck.py` | Local validation of every model reply; problems are `(json_path, keyword)` and never hold a value |
| Statistics | `stats.py` | Percentile, Wilson interval, exact sign test, paired bootstrap; seeded only |
| Model runtime | `inference/runtime.py` | The only way collective code calls a model; bound to one boundary; repair once, escalate at most once |
| HTTP client | `inference/client.py` | OpenAI-compatible chat over `http.client`; a deadline on every receive (status line, headers and body), size cap, no redirects, no stored text |
| Routing | `inference/routing.py` | Routing file: endpoints with their boundary, routes with optional single-hop escalation; no defaults ship |
| Tasks and prompts | `inference/tasks.py` | Task specs, the `<data>` block, the repair message |
| Reply parsing | `inference/jsonparse.py` | One JSON object out of a reply (reasoning blocks, fences, prose), with a depth cap |
| Usage ledger | `inference/ledger.py` | One JSON line per logical attempt; numbers and labels only |
| Fakes | `inference/fake.py`, `inference/fakeserver.py` | Deterministic in-process provider; local OpenAI-compatible server with 33 personas |
| Founder tools | `experiments/e3_latency.py`, `connectors/openfda.py`, `experiments/n1_narratives.py` | E3, the openFDA cache, N1 sample and score |

Everything is standard library only and runs under `python -S`. No fabric file changed (`INTEGRATION.md`). After
G3 the layer has 34 modules on the stdlib-only list and eleven CLIs (section 7); sections 11 and 12 list what G2 and
G3 added.

## 2. The boundary guard

A `Runtime` is built for one boundary, either `site:<id>` or `central`, and keeps it for its lifetime. `run` and
`single` have no boundary parameter. Every endpoint in the routing file declares its own boundary: `site:<id>`,
`central`, `external` or `any-simulated`. Every task declares its data class: `raw` means the payload holds record
text; `structured` means it holds only fields policy allows to leave a site.

`boundary_mode()` is the single source of truth. Rules apply top to bottom:

| Endpoint boundary | Condition | Mode |
|---|---|---|
| `any-simulated` | provider is `fake`, or the runtime was built with `simulation=True` | `simulated` |
| `any-simulated` | otherwise (raw and structured alike) | refused |
| same as the runtime's | | `own` |
| any other | task is `structured` | `structured_egress` |
| any other | runtime has `allow_external_raw` (only `public` or `synthetic`, equal to its `data_label`) | `external_raw_exempt` |
| any other | otherwise | refused |

Partner data can never be exempted: `allow_external_raw` must equal `data_label`, and only `public` and `synthetic`
are accepted. The guard runs before any I/O, on the primary endpoint and then on the escalation endpoint. A refusal
does three things:

- writes one ledger row (attempt 0, mode `refused`, error `boundary`);
- raises `InferenceBoundaryError`;
- sends no request to any endpoint, even when only the escalation endpoint is out of bounds.

## 3. One call: attempts, repair, escalation

Pre-flight checks run in this order, before any I/O:

1. the `ref` handle (opaque, ASCII, at most 96 characters; never a record id);
2. the payload (a dict that canonical JSON accepts);
3. the schema (compiles in the subset, with a top-level object);
4. the route, or the `endpoint=` override;
5. the api-key environment variables of every endpoint that may be contacted;
6. the boundary guard.

| Attempt | Endpoint | Messages | When |
|---|---|---|---|
| 1 | primary | system (task, schema) + user `<data>` block | always |
| 2 | primary | the same + one repair message (`### REPAIR`, problems as `path: keyword`, a 300-character excerpt of the bad reply as data, a truncation hint when `finish_reason` was `length`) | attempt 1 was `json_invalid` or `schema_invalid` |
| 3 | `escalate_to` | the attempt-1 messages only: no repair marker, no excerpt | attempt 2 was also `json_invalid` or `schema_invalid`, and the route names `escalate_to` (no `endpoint=` override) |

**Escalation matrix.** Only `json_invalid` and `schema_invalid` lead to a repair or an escalation. These never reach
the escalation endpoint:

- `http_4xx`: a 400 for `json_schema` or for strict keywords, 401 or 403, or 429 after its retries;
- `http_5xx` after its retries;
- `timeout`, `network` and `too_large`;
- `boundary`, configuration errors and `no_handler`.

Transport retries (429, 5xx and connection failures, never timeouts or TLS-verification failures) happen inside one
logical attempt. They are counted in that attempt's row, not written as rows of their own.

Errors are `InferenceError(task, endpoint, kind, http_status)`. Each has exactly those four values and no other
argument. Every raise uses `from None`, so no reason phrase, server body or socket text reaches a message, a
traceback or a log. Logging is at most one WARNING per failed attempt, naming the task, endpoint, attempt, kind and
status.

## 4. The usage ledger

Each runtime appends one canonical JSON line per logical attempt to its own ledger file. The row has exactly 27 keys:

| Key | Meaning |
|---|---|
| `ts` | from the runtime's injected clock |
| `run_id` | the run this call belongs to |
| `task` | task name |
| `ref` | the caller's opaque handle (never a record id) |
| `attempt` | 0 refused before any I/O, 1 primary, 2 repair, 3 escalation |
| `escalated_from` | the primary's name on attempt 3, else null |
| `endpoint`, `provider` | which endpoint served the attempt |
| `model_requested`, `model_served` | the configured tag, and the id the server reported (kept only if it is a short plain identifier) |
| `host` | `host:port`, or `in-process` for the fake |
| `boundary`, `endpoint_boundary` | the runtime's boundary and the endpoint's |
| `boundary_mode` | `own`, `simulated`, `external_raw_exempt`, `structured_egress` or `refused` |
| `data_label` | `synthetic`, `public` or `partner` |
| `ok`, `error_kind` | outcome; the kind is null when ok |
| `http_status`, `finish_reason`, `transport_retries` | transport facts of the final HTTP try |
| `tokens_in`, `tokens_out` | as reported by the server; null when not reported, never 0 |
| `latency_ms`, `ttft_ms` | the final try, measured with a monotonic timer |
| `cost_usd`, `cost_basis` | `per_token` or `per_hour` from a configured price; `unpriced` (null cost) without one; `fake` for the fake provider |
| `fake_marker` | the response carried `X-Mycelic-Fake: 1`, or the provider is the fake |

A row never holds a prompt, a completion, an error body, a header, a record id or record text.

**Only `usage_summary` crosses a boundary.** A site's ledger stays at the site. `usage_summary()` reduces it to
counts per (task, endpoint): calls, ok, errors by kind, token sums with missing counts, latency p50/p95 and a fake
flag. It has no `ts`, `ref`, `host`, `run_id` or model name. It is the only ledger-derived artifact meant to leave a
site. G3 sends it windowed and k-suppressed rather than whole (section 12.6).

## 5. Fakes and the measurement flag

- `FakeProvider` is in-process and deterministic. Each task has a handler; `fail_next` scripts the next replies to
  exercise repair and failure paths; it opens no socket. Its rows are priced `fake`.
- `FakeOpenAIServer` listens on 127.0.0.1 and implements 33 personas: valid, slow, trickling the status line, the
  headers or the body, oversized, deeply nested, echoing the prompt into its error, rejecting `json_schema`,
  streaming, stalling, resetting mid-body, and others. Every response carries `X-Mycelic-Fake: 1`, and its model
  listing says `"fake": true`.
- Every harness output carries `measurement`. It is false whenever a fake was involved: a fake provider in the
  routing, a ledger row with `fake_marker`, or a model listing that said fake. It is never inferred from the host
  name. A run with `measurement: false` is a rehearsal and is never quoted.

## 6. The schema subset

Replies are validated locally against the full schema, even when the wire carries a reduced one.

- **Allowed keywords:** `type`, `properties`, `required`, `additionalProperties`, `enum`, `const`, `pattern`,
  `minLength`, `maxLength`, `minimum`, `maximum`, `items`, `minItems` and `maxItems`. Anything else fails at compile
  time.
- **Objects:** every object sets `additionalProperties: false` and lists all its properties in `required`; an
  optional field is `[T, "null"]`.
- **Strict typing:** `integer` excludes `bool` and `3.0`; `number` excludes `bool`, NaN and Infinity; `enum` and
  `const` compare type-strictly.
- **Patterns:** matched with `re.fullmatch` under `re.ASCII`, so a trailing newline or a non-ASCII digit cannot
  satisfy `[0-9]`.
- **No values in problems:** problems name JSON paths and keywords from the schema only. An extra key is reported
  without its name.
- **Reduced transport schema:** `transport_schema: reduced` drops the seven range and length keywords from the wire
  for servers that reject them.

## 7. The guards (`tests/mycelic/test_collective_guards.py`)

| Guard | What it enforces |
|---|---|
| Import guard | `mycelic/{service,aggregation,store,transport,lineage}.py` import no model client and nothing from `mycelic.collective`. An AST check covers plain, relative and dynamic imports; a fresh-interpreter check confirms it. A missing core file fails loudly. |
| Stdlib only | All 34 collective modules import, and the eleven CLIs (G2 adds the five E1 subcommands and `packs.loader check`; G3 adds `experiments.g0_canary`) answer `--help`, under `python -S` |
| No model names | No model-family name in collective code, docs or tests. The matcher holds sha256 digests only. Example tags live only in `docs/collective/examples/`. |
| Determinism | No wall clock or unseeded randomness in `jsonio`, `schemacheck`, `stats`, the runtime modules, the pack modules, `edge/{extract,weeks,records,egress,site}.py` and `leakage.py` |
| Domain literals (G2) | No pack term (entity type, predicate, code, rule, template, follow-up type or role id of either built-in pack) is an identifier or a whole string constant in generic collective code, no string constant there contains `ILL-`, and no openFDA field name is a string constant in the pack, extraction or E1 code (one documented exemption: `text`, the payload key the brief fixes) |
| Runbook | Every RUNBOOK command runs with `--dry-run`, with the network blocked, and creates nothing |

Each later gate extends the lists at the top of that module.

## 8. Where G1 sits in the loop (STRATEGY section 4.1)

| Stage | What it needs | Status after G3 |
|---|---|---|
| Sense | records become typed claims and per-site counts; structured codes (no model) and in-boundary extraction | **G1:** runtime (boundary-bound model calls, schema validation, ledger). **G2:** packs, the canonicaliser, the record connector, claim extraction from codes (S) and narratives (X), and the E1 harness. **G3:** each site's own record store, k-suppressed weekly count cells and windowed usage summaries that leave only through the Boundary, and the G0 text-leakage scan. The HQ counts store is G4 |
| Detect | statistical detectors over counts; rules as a second channel | Later (no model is involved) |
| Decide | candidate decided at the lowest unit spanning the evidence | Exists in the fabric for rule conclusions; candidates are later |
| Verify (pushdown) | narrow questions answered by each site's in-boundary model from its own records | Later; it will call `Runtime.run` at each site, where the guard keeps raw text inside |
| Follow up | approval-routed T0/T1 tasks | Later |
| Check the outcome | did the failure mode recur | Later |

No stage "learns"; nothing in G1 claims learning.

## 9. Routing defaults as policy (STRATEGY section 5.6)

No routing file ships. Each site writes its own (`examples/routing.example.json` is a template, not a default). The
policy a site's file should express, until a measurement says otherwise:

- **Extract claims from one record:** a model inside the boundary, sized to the hardware. Escalate after a
  schema-invalid reply survives one repair (`escalate_to`, also inside the boundary). Gate: E1.
- **Canonicalise entities:** deterministic code (patterns, alias tables). Never a generative model by default.
- **Detect cross-site patterns:** no model.
- **Rank candidates:** a logistic over explicit features. A model reads raw text only at the evidence holder
  (pushdown). Gate: E2.
- **Verify downward:** the holder's in-boundary model over its own records, escalating to a larger in-boundary model.
  Gate: E2.
- **Draft a follow-up:** an in-boundary model, or a larger model on structured claims only (a `structured` task,
  which the guard lets leave the site). Gate: X4.

## 10. What G1 does not do

G1 built no pack and no extractor; G2 adds them (section 11). Edge stores, counts, detectors, verification,
follow-up and the demo are later gates, and the E2 harness comes later. E1 (G2) calls the model through
`Runtime.run` with an `endpoint=` override, so the repair rule is the one production extraction uses.

## 11. G2: packs and the sense step

G2 makes generality a matter of data plus a small amount of generic code, and builds the sense step with the
experiment that measures it (E1). **No real-model number is produced here either**: E1 was rehearsed against local
fake servers, and its outputs say `measurement: false`.

| Part | Module | Purpose |
|---|---|---|
| Pack loader | `packs/loader.py` | Strict-JSON pack directory to a frozen `FrozenPack` with four sha256 hashes; every failure a `PackError(file, path, problem)`; `python -m mycelic.collective.packs.loader check` |
| Canonicaliser | `packs/canonical.py` | Folds, id formats compiled to ASCII patterns, deterministic resolution of entity mentions (never a model), sentence spans |
| Connector | `packs/connector.py` | Vendor rows (and openFDA events) to internal records with exactly the record keys; per-reason rejections |
| Generator | `packs/generator.py` | A seeded synthetic world (records, gold claims, master data) from a pack's world spec |
| Built-in packs | `packs/data/device_quality`, `packs/data/claims_integrity` | An illustrative device-quality pack and a fictional claims-integrity pack, both same-author (`PACKS.md`) |
| Extraction | `edge/extract.py` | The codes channel, the lexical and model extractors, post-processing, pairing into claims |
| E1 | `experiments/e1_extract.py` | prepare, label-check, prereg, run, compare; pinned values, paired statistics |
| Additions to G1 | `stats.py`, `inference/fakeserver.py` | `f1_from_counts` and `bootstrap_f1`; the fake server's `responder` hook and `request_payload` |

Import graph (no cycles): `packs` imports neither `edge` nor `experiments`; `edge` imports `packs.canonical`, the
inference errors and `TaskSpec` (the runtime and the pack type only for type hints); only `experiments` imports
`experiments`; the fabric core imports none of this (the import guard).

### 11.1 Two channels

| Channel | Source | Model | Claims |
|---|---|---|---|
| **S** (codes) | structured codes and structured entity fields | none | structured entities x code predicates |
| **X** = S + `text_only` | S plus the narrative, read inside the site's boundary | lexical, or a model behind the runtime with deterministic post-processing | S's claims plus the pairs only the narrative supports |

`codes_channel` maps each code to its predicate (unknown codes are counted) and resolves each structured value
with `resolve_exact` (failures are counted); the primary entity is the first resolved value of the mapping's
`primary_entity_type`. An extractor returns text claims `(entity, predicate, negated)` or entity-only mentions.

**Pairing** (`pair`, amendment A2). For a record with structured entities E_s, code predicates P_c and text claims:

| Pair | Channel | Example (codes ILL-0101 crack; product SD-9, lot L10001; "Battery door cracked. No leak observed.") |
|---|---|---|
| E_s x P_c | `codes` | (SD-9, crack), (L10001, crack) |
| the extractor's own non-negated pairs | `text_only` unless already a codes pair | (BATTERY-DOOR, crack) |
| non-negated text predicates x E_s | `text_only` unless already a codes pair | (SD-9, crack) is already a codes pair |
| P_c x entities named only in the text | `text_only` | (BATTERY-DOOR, crack) |
| a pair the text states only as negated | never output | (SD-9, leak) is dropped |

There is no record-level cross product of text predicates with every text entity, so a predicate is not attached to
an unrelated component named elsewhere in the record. Each (entity, predicate) appears once; `res_conf` is the
best evidence for the entity (text or structured). S is `pair(codes, None)`; X is `pair(codes, text)`.

### 11.2 The canonicaliser

Rules, in order: full-width fold; separators at separator positions (hyphen, underscore, every hyphen-like
character, space or NBSP, the type's separator); zero-width deletion; ASCII-only case (so `ß`, `İ` and `ı` are
never folded); leftmost-longest matches bounded by non-alphanumerics and never followed by a hard separator plus an
alphanumeric (`SD-9-B` is rejected, `SD-90` is never `SD-9`); a lookalike with a non-ASCII letter or digit is
unresolved and counted (`homoglyph`, `non_ascii_digit`); a space-separated form resolves only to an id the site
knows (alias targets plus its master data), else it is counted as `space_unknown`; then alias phrases. Every span
indexes the original text. Exact matches carry confidence 1.0, aliases 0.95 and variants 0.9 in both packs.

### 11.3 Extraction and its post-processing

The model receives exactly `{language, text}`: the narrative cut at a whitespace boundary to the pack's input cap.
Persons, the reporter, the record ref and structured values are never sent. A reply item is dropped, in order, as
`empty` (all null), `not_canonical` (only one of entity type and text), `ungrounded` (its text is not in what was
sent), `person_value` (its text is a person value or the reporter), `not_canonical` (it does not resolve),
`ungrounded` again (the canonicaliser does not read that id anywhere the text occurs: a model that trims `SD-9-B` to
`SD-9` names an id the scanner rejects outright, so it is dropped, exactly as the lexical channel and label-check
find nothing there), `no_entity` (a predicate with no entity and no primary) or `duplicate`. A kept entity's
`res_conf` is that of the text's own occurrence, not of the model's spelling. An out-of-enum type or predicate is a
schema failure (one repair, then the lexical fallback, or an empty result in E1), not a drop. A boundary refusal
propagates. The fake provider's `lexical_handler` reproduces the lexical extractor's claims byte for byte, which
the tests check on every fixture of both packs.

The lexical extractor pairs per distinct entity and distinct (predicate, negated) in a sentence, carrying their
multiplicities, so its work is linear in the narrative with a factor the pack bounds (at most two per predicate) and
its duplicate counts equal those of pairing every mention. The remaining cost is the output itself: one sentence
that names thousands of distinct ids next to every predicate yields that many claims (a 200,000-character worst
case for `device_quality` gives about 371,000 claims in about 1.2 s). The handler stops at `max_claims` items.

### 11.4 E1

| Step | Writes | Refuses |
|---|---|---|
| `prepare` | `runs/e1/<id>/records.jsonl`, `sheet.csv`, `prepare.json` (seeded sample; shortfall recorded) | a missing mapping, an unreadable source |
| `label-check` | `labels.jsonl` (canonical, sorted) | unlabelled cells, unknown types or predicates, ids that do not canonicalise, a predicate-only item without a primary entity, duplicate or missing rows |
| `prereg` | `runs/e1/<id>/prereg.json` | fewer than 3 runs or 2 endpoints, a reference not evaluated, the fake provider, raw records to an external endpoint without a matching public or synthetic exemption, a data label the records contradict (`public` needs records from a public source, site `public`), dirty code without `--allow-dirty` |
| `run` | `run.json` (with the sha256 of `predictions.jsonl` and `ledger.jsonl`), `predictions.jsonl`, `ledger.jsonl` | a malformed prereg, any change to the labels, the vocabulary hash, the scoring code hash or a pinned endpoint setting, a data label the labels contradict |
| `compare` | `runs/e1/<id>/e1.json` | a tampered prereg, predictions or ledger that differ from their run's stamped sha256, a run that never finished writing `run.json`, duplicate or missing repeats, a pre-registered endpoint without runs or incomplete runs without `--allow-incomplete` (stamped; e1.json lists the endpoints without runs), a changed reference or margin |

The scored output is the extractor's text claims (A3). Field-level micro F1 is primary; claim, entity and predicate
F1, JSON validity and lot and supplier exact match (the share of records matched in every run, with a Wilson
interval over records) are secondary. Intervals bootstrap records; the paired
comparison bootstraps per-record differences against the reference and adds an exact sign test. With
`measurement: false` the verdicts are withheld (null).

## 12. G3: the site boundary

G3 stores each site's raw records and claims inside the site and lets exactly two artifact types out, both through
one `Boundary`: weekly count cells and usage summaries, each structurally closed, k-suppressed and never revised.
G0 (`LEAKAGE.md`) plants canaries and scans every byte that crossed. **Scope: text only.** Nothing in the fabric
changes, and no real-model number is produced: every run here is synthetic, with a fake model or none.

| Part | Module | Purpose |
|---|---|---|
| Weeks | `edge/weeks.py` | ISO weeks (`YYYY-Www`, 52 or 53 a year), closed weeks, the recorded local date of a timestamp |
| Record store | `edge/records.py` | One SQLite file per site (WAL); records, claims, extraction stats, late records and the emission log; the only SQL in the collective layer |
| Boundary | `edge/egress.py` | The only path out of a site: a closed spec per artifact type, cross-field checks, the `after` sequence, two append-only logs |
| EdgeSite | `edge/site.py` | Ingest, extract, `emit_cells`, `emit_usage`; `build_cells` |
| Leakage | `leakage.py` | Canary planting, the manifest, the scan |
| G0 runner | `experiments/g0_canary.py` | `python -m mycelic.collective.experiments.g0_canary`; writes `leakage.json` |
| Addition to G1 | `inference/ledger.py` | `summarise(rows)`, the reduction `usage_summary(path)` now calls |

Import graph (no cycles): `weeks` imports only `packs.connector`; `records` imports `weeks`, `jsonio` and
`packs.canonical`; `egress` imports `weeks`, `jsonio`, `packs.connector` and the inference error kinds; `site`
imports `records`, `egress`, `weeks`, `extract`, `packs.canonical`, `packs.connector` and the ledger (the runtime and
the pack type only for type hints); `leakage` imports `jsonio`, `packs.canonical`, `packs.loader` and
`egress.schema_words`; only `g0_canary` imports `experiments.common`.

### 12.1 Data flow

```
 SITE <id> (inside its boundary)                                        HQ (simulated in G0)
 +-------------------------------------------------------------+
 | records ---ingest---> site-<id>.sqlite3                      |
 |   (date check,        records | claims | extraction_stats    |
 |    record check,      late_records | emitted_weeks           |
 |    own site only)        |   ^                              |
 |                  extract |   | claims (valid only)          |
 |    lexical or model -----+---+   model calls -> ledger.jsonl |
 |    (runtime bound to site:<id>)          (never leaves)      |
 |                                                              |
 | emit_cells / emit_usage: closed weeks, k-suppressed,         |
 |   stored in emitted_weeks (exact bytes) before sending       |
 |                          |                                   |
 |                     Boundary.send ---------------------------+--> hq/receive.jsonl   (1st append)
 |                          +--> site-<id>.egress.jsonl         |    (2nd append, same line)
 +-------------------------------------------------------------+
```

### 12.2 The site store (`site-<id>.sqlite3`)

Opened with `isolation_level=None`, `journal_mode=WAL` (refused when the file system cannot do WAL),
`synchronous=FULL`, foreign keys on and a 5 s busy timeout. Every write runs in one `BEGIN IMMEDIATE ... COMMIT`
and is rolled back on any exception; every `SELECT` carries `ORDER BY`. No table has a fabric table name.

| Table | Columns |
|---|---|
| `site_info` | `key`, `value`: `schema_version`, `site_id`, `pack_id`, `config_hash`; reopening with another value raises `StoreError('site_info mismatch: <key>')` |
| `records` | `record_ref` (key), `seq`, `received_date`, `iso_week`, `ingested_at`, `ingest_week`, `count_week`, `language`, `codes`, `structured`, `narrative`, `narrative_key`, `person`, `reporter_id`, `origin_ref`, `origin_site`, `root_ref`, `forwarded_in`, `synthetic` |
| `claims` | `record_ref`, `entity_type`, `entity_id`, `predicate` (together the key), `channel`, `extractor`, `res_conf` |
| `extraction_stats` | `record_ref` (key), `mode`, `extractor`, `error_kind`, `truncated`, `language_supported`, `drops`, `unresolved`, `unknown_codes`, `structured_unresolved`, `invalid_claims`, `extracted_at` |
| `late_records` | `record_ref` (key), `received_week`, `count_week`, `watermark`, `ingested_at` |
| `emitted_weeks` | `artifact_type`, `closed_through` (together the key), `as_of`, `after_week`, `ledger_rows`, `body` (the bytes sent), `sha256`, `cells`, `created_at`, `sent_at` |

Ingest: a known `record_ref` (stored, or earlier in the batch) is a duplicate and the first wins. `root_ref` is the
origin's ref when the record names one; else the root of the earliest record at this site with the same folded
narrative (`narrative_key`); else the record's own ref. An empty narrative has no key and never shares a root, and a
copy that reached another site without an origin marker counts as independent there (a documented limitation).
`forwarded_in` is 1 only when the origin is another site. A record whose received week is at or before the cells
watermark is late: it counts in `max(ingest week, the week after the watermark)` and gets a `late_records` row.

Extraction runs over records without stats, 100 per transaction; the first extraction of a record wins. A claim is
stored only when its type and predicate are the pack's, its id is canonical, its channel is `codes` or `text_only`
and its `res_conf` is one of the pack's three confidences; others are counted as `invalid_claims`. A simulated
runtime refuses to start while any pending record is not synthetic; a boundary refusal propagates and saves nothing
of the batch.

### 12.3 The cell format

One cell per (entity type, entity id, predicate, ISO week, channel) with at least one of the site's own records:

```
{"channel":"text_only","entity_id":"ALARM-SPEAKER","entity_type":"component","iso_week":"2024-W02","n":3,
 "n_reporters":"<k","n_roots":3,"predicate":"detachment","res_conf_min":0.95}
{"channel":"codes","entity_id":"ALARM-SPEAKER","entity_type":"component","iso_week":"2024-W02","n":"<k",
 "n_reporters":"<k","n_roots":"<k","predicate":"alarm_failure"}
```

(Both are taken from a high-volume synthetic copy of `device_quality`, seed 7, site `plant-ashvale`; at the
pack's own volumes every cell looks like the second one, `LEAKAGE.md` section 1.)

- `n` counts distinct records, `n_roots` distinct roots and `n_reporters` distinct reporters (all unknown reporters
  are one shared reporter, so an unknown never inflates the count). Each is the int when it is at least k, else
  `'<k'`, independently. `res_conf_min` (the lowest resolution confidence) is present exactly when `n` is an int.
- Forwarded-in records are excluded; a record counts once per (entity, predicate), however many codes, structured
  values or mentions name the pair. The codes and text-only cells of a key count disjoint record sets.
- A row of a type outside `egress_entity_types` is counted as `non_egress_type`. With `require_master_data`, an id
  of a type with an id format outside the site's master data is counted as `not_master_data` (alias-only types
  always pass; a type without master data at the site drops all its ids).
- The bundle is `{schema_version, pack, config_hash, site, as_of, after, closed_through, k, cells}`, cells sorted by
  their key. It has no totals, no marginals, no forwarded count and no per-record rows; those stats stay at the site
  (`Emission.stats`).

### 12.4 The Boundary

`Boundary(pack, site_id, egress_log=..., receive_log=..., clock=..., tasks=..., endpoints=...)` is a site's only
way out (G6 adds the path in). `validate` and `send` check, in order, and raise the first problem as `EgressError`:

1. the direction (`out`) and the artifact type (`cells_bundle` or `usage_summary`);
2. canonical JSON (NaN, sets and other non-JSON values fail as `json`);
3. the closed structure: unknown keys first (reported at the parent object as `additionalProperties`, never named),
   then required keys in sorted order, then optional keys present; arrays by index. Counts are an int in
   [k, 10^9] or exactly `'<k'` (booleans, floats, `'3'` and `'<k '` fail); ids match a coarse ASCII pattern;
   enums compare type-strictly;
4. cross-field checks: `range` (`after < closed_through`, every cell week in `(after, closed_through]`),
   `id_format` (the type's canonical form, or a listed alias-only id), `consistency` (`res_conf_min` iff `n` is an
   int; `n_roots` and `n_reporters` are `'<k'` when `n` is, and never above `n`; no token or latency keys in a
   usage group whose `calls` is `'<k'`), `order` (cells, or usage groups, strictly ascending);
5. `sequence`: `after` equals the last `closed_through` sent for that type (None before the first), unless the body
   is byte-identical to the last one sent, which is an idempotent re-send.

An `EgressError` has `artifact_type`, `path` and `keyword` only; its `args` are empty and its text never holds a
value. A refused artifact writes nothing and `egress.py` logs nothing. `send` then appends one canonical line
`{artifact_type, body, bytes, direction, sha256, site, ts}` (with `ts` from the injected clock) to the HQ receive log
first and the site's egress log second, so after a crash between the two a re-send makes them agree; HQ may then hold
a duplicate line with the same `sha256`, which HQ (G4) drops. `read_log` refuses any line that is not exactly such a
row with a matching `sha256` and `bytes`.

### 12.5 Weeks

A week is closed at `as_of` when its Sunday plus `close_lag_days` is on or before `as_of`, so with a lag of 7 days
and `as_of` on a Wednesday, the current and previous weeks are still open. `emit_cells(as_of)` refuses an `as_of`
that is not a date, is later than the site clock or earlier than the last emission's; a repeated `as_of`, or one that
closes no new week, sends nothing. Before computing a new bundle it re-sends, byte for byte, any stored bundle a
failed send left unsent, and it refuses to emit while records counted in the window are not extracted. A window
without cells still sends a bundle: it advances the watermark. Received dates are read in the record's own calendar
(`2026-03-01T23:30:00-05:00` is 2026-03-01); a record dated in the future waits until its week closes.

### 12.6 Usage windowing

`emit_usage(as_of)` follows the same `as_of` rules and sends one `usage_summary` per newly closed span. It reads the
site's ledger (every row must belong to `site:<id>`), takes the rows after those already summarised (the
`ledger_rows` count of the last emission) up to the first row whose week is still open, and reduces them with
`ledger.summarise`. Per group, `calls` and each error kind are `'<k'` below k, `ok` and the missing-token counts may
also be 0, and tokens and latency percentiles are sent only when `calls` is at least k. Each ledger row is summarised
exactly once. A site without a runtime sends no usage.
