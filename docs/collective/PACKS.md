# Domain packs

A **domain pack** is what makes the collective layer general: the vocabulary, policy and test world of one field,
held as strict JSON and frozen with four hashes, plus a small amount of generic code that reads any pack. The
generic code (connectors, the canonicaliser, the extractors, detector types and executors) holds no domain literal;
`tests/mycelic/test_collective_guards.py::DomainLiteralTests` enforces that.

STRATEGY section 4.4 is the reason for the split: data is cheap to change and to review; code (connectors with
their own authentication, detector types, action executors) carries a test burden. A new field should cost a pack
directory, not new modules.

## 1. What a pack holds

A pack is a directory `mycelic/collective/packs/data/<pack_id>/` (built-in) or any directory you pass as a path.
It contains exactly these files; names starting with `.` are ignored and anything else is refused (G5's plant
specs, below, are the one exception).

| File | What it holds |
|---|---|
| `pack.json` | id, semantic version, title, languages (the first is primary), `illustrative`, `disclaimer` (required when illustrative), `same_author_as_code` |
| `vocabulary.json` | entity types (an id format or a closed list of alias-only ids; case, separator, leading zeros, `exact_match_metric`, `egress`), predicates with a lexicon per language, negation cues per language (pre, post, terminators), the negation window, extraction limits and the confidence of exact, alias and variant matches |
| `codes.json` | structured codes: label, predicate, and whether the code is specific |
| `aliases.json` | per entity type, alias phrase to canonical id |
| `mapping.json` | how a vendor export maps to an internal record (paths with `[]` fan-out and a `where` filter, code value maps, entity paths, the primary entity type, narrative sources, person fields, the reporter, a forward's origin, required targets) |
| `mapping_openfda.json` | optional: the same format for openFDA device events (device pack only) |
| `egress.json` | k, count suppression and granularity, verdict count buckets, which entity types may leave a site, the fields that never leave (`never_fields`) and the fields a restricted central baseline may read, verification limits |
| `detectors.json` | the parameters of the HQ detectors (G4 set the shape; section 1.1): alert budget and cooldown, baseline, window and minimum history weeks, the burst test, co-occurrence lift, resolution, independence, decoy filters and the ranker's default weights |
| `rules.json` | hand-written rules, the second detection channel |
| `questions.json` | pushdown question templates; only `{window}`, `{predicate_label}`, `{entity_type_label}` and `{entity_id}` may appear, without conversions or format specs, so a template can embed no record content. Since G6 also the required `pushdown` block: the commit gate's thresholds and the sibling cap (section 1.2) |
| `followups.json` | roles and follow-up types: tier (T0 packet, T1 draft, T2 write; T3 is never an action type and an enabled T2 is refused), owner and escalation roles, daily cap, acknowledgement days, an argument DSL (entity id, predicate, conclusion id, enum, integer: no free text) and, for drafts, a JSON schema whose every string has a `maxLength`; since G7 the follow-up layer uses them, and every arg must lie inside its conclusion (section 1.3) |
| `generator.json` | the synthetic world spec: sites, rates, predicate weights, code rates, the id universe and its links, surface weights, narrative templates per language and predicate, entity sentences, filler, person and reporter generators, site master data |
| `fixtures/records.jsonl` | at least 40 hand-labelled records, each `{"record", "gold"}`; the same line format as E1's `labels.jsonl` |
| `fixtures/plant_<name>.json` | optional (G5): plant specs for the evaluation harness (`ARCHITECTURE.md` section 14.2), named `plant_` plus 1 to 40 of `[a-z0-9_]`. The listing accepts them, but the loader never reads them and no hash covers them, so adding or editing one changes none of the four hashes; only `evaluate/plant.py` reads them. Each built-in pack ships `plant_smoke.json`, a same-author construction smoke that is not blind and never a result |

The loader (`packs/loader.py`) documents every rule; the brief that defines them is the G2 build brief. A few that
matter when you extend a pack:

- ids are `^[a-z][a-z0-9_]{1,40}$` and may not be a reserved word (an enum value, a record or claim field, a
  pack-file key, a Python keyword or builtin), so no pack id can collide with a word the generic code uses;
- an id format's adjacent segments may not share a character class unless a required separator divides them, so
  an id splits into segments one way only, and the format may not admit the empty string;
- only a hard separator (hyphen, underscore, any hyphen-like character, or the type's own separator) followed by a
  letter or digit rejects a match outright, so `SD-9-B` is no id at all; any other punctuation ends an id, so
  `SD-9.5`, `SD-9/5` and `SD-9,5` all read as `SD-9`. If your ids carry decimal or slash suffixes, put the suffix in
  the id format (a literal or a segment after an optional separator) so it cannot be read as the shorter id;
- an alias must point at a canonical id of its type, may not look like an id of any type, and may not chain;
- every gold claim in the fixtures must resolve exactly to a vocabulary id with a vocabulary predicate.

Every failure is a `PackError` naming the pack-relative file and a JSON path, for example
`aliases.json: $.component: alias chain`.

**How `egress.json` is enforced (G3).** A site's Boundary (`edge/egress.py`) reads it directly: `k` is the
suppression threshold of every count that leaves (each is an int of at least `k` or the literal `'<k'`),
`close_lag_days` decides when a week is closed and may be sent, `egress_entity_types` are the only types a cell may
name, and `require_master_data` decides whether an id with an id format must be in the site's master data before it
leaves (alias-only types always pass). `never_fields` never leave by construction: a cell holds only a type, a
canonical id, a predicate, a week, a channel and counts. Because these values are part of `config_hash`, changing
one (G0's `--require-master-data` override included) means a new pack copy, a new hash and a new site store.
`LEAKAGE.md` describes what may cross and how G0 checks that text does not.

### 1.1 `detectors.json` (G4)

A closed object; every key is required and every number is a finite strict-JSON number. The loader checks the ranges
below, the open ends explicitly, and four cross-checks: `window_weeks` is at least `egress.min_window_weeks`,
`0 < p_min <= p_max < 1`, `lambda_floor > 0`, and `stale_days >= egress.close_lag_days + 7` (so the newest closed
week is never stale). There is deliberately no relation between `min_history_weeks` and `baseline_weeks`.
`ARCHITECTURE.md` section 13 gives every formula.

| Key | Range | Meaning |
|---|---|---|
| `alert_budget_per_week` | int 0..1000 | how many candidates may alert per week (rule hits never use it) |
| `cooldown_weeks` | int 0..52 | consecutive weeks a key must be absent from the candidates before it may alert again |
| `baseline_weeks` | int 4..104 | the baseline: the weeks ending at `W - window_weeks` |
| `window_weeks` | int 1..26, and >= `egress.min_window_weeks` | the current window: the weeks ending at W |
| `min_history_weeks` | int 1..104 | weeks of history a site needs before the window to be eligible |
| `burst.alpha_site` | (0, 0.5] | a site exceeds when `P(X >= c) < alpha_site` under its baseline rate |
| `burst.lambda_floor` | (0, 1] | the lowest weekly baseline rate, so a never-seen series has a finite surprise |
| `burst.p_min`, `burst.p_max` | `0 < p_min <= p_max < 1` | the clip of each site's base rate `p_s`; `p_max` when a site has no past window |
| `burst.min_sites` | int 2..1000 | certainly exceeding sites a D2 candidate needs |
| `cooccurrence.pmi_smoothing` | (0, 10] | the additive smoothing of the PMI |
| `cooccurrence.pmi_delta` | [0, 20] | the PMI rise over the baseline a site needs to count as rising |
| `cooccurrence.min_sites` | int 2..1000 | rising sites a D3 candidate needs |
| `resolution.res_conf_min` | (0, 1] | a lineage resolution confidence below this sets `low_res_conf` |
| `independence.echo_min_ratio` | (0, 1] | an upper-bound root ratio below this sets `echo` |
| `decoy.stale_days` | int 7..3660, and >= `close_lag_days + 7` | a candidate whose newest lineage week is older than this at the step's as_of is removed |
| `decoy.base_rate_site_fraction` | (0, 1] | the share of eligible sites where another series of the predicate exceeds above which `high_base_rate` is set |
| `ranker.bias` | number | the ranker's intercept |
| `ranker.weights` | a number for each of `burst_surprise`, `pmi_rise`, `log_independent_roots`, `supporting_sites`, `low_res_conf`, `echo`, `few_reporters_share`, `high_base_rate` | the ranker's default weights |

**The built-in packs use the same author's defaults, not fitted values** (`it_incidents`'s `detectors.json` is a
byte copy of `device_quality`'s, B4): budget 5 (4 for `claims_integrity`),
cooldown 4, baseline 26, window 8, minimum history 12; `alpha_site` 0.01, `lambda_floor` 0.01, `p_min` 0.01, `p_max`
0.25, burst and co-occurrence `min_sites` 2; `pmi_smoothing` 0.5, `pmi_delta` 1.0; `res_conf_min` 0.95;
`echo_min_ratio` 0.5; `stale_days` 42 (56 for `claims_integrity`, whose `close_lag_days` is 21);
`base_rate_site_fraction` 0.5; bias -4.0 and weights 0.35, 0.5, 0.4, 0.3, -1.0, -1.5, -1.0, -1.5 in the order above.
They are default weights; nothing learns them. Changing any of them changes `config_hash` and `detector_hash`, so
site stores made with the old pack refuse to reopen (`site_info mismatch`) and a new HQ store is needed.

### 1.2 Pushdown verification: `questions.json`'s `pushdown` block and the verdict buckets (G6)

`questions.json` holds `templates` and, since G6, a required `pushdown` object. It is closed and every key is
required; a missing, extra or out-of-range key is a `PackError` naming `questions.json` and its path. The keys are in
`config_hash` only (not `detector_hash`: detection does not read them) and are reserved words.

| Key | Range | What it controls (`pushdown/gate.py`, `pushdown/orchestrator.py`) |
|---|---|---|
| `min_confirming_sites` | int 1..100 | sites whose confirm counts (support of at least k records) a `supported` conclusion needs |
| `min_independent_roots` | int 1..100000 | the sum over counted confirms of the roots bucket's lower bound (`'<k'` counts 1) it needs |
| `min_independent_reporters` | int 1..100000 | the same for reporters (every unknown reporter at a site is one shared reporter) |
| `freshness_days` | int 1..3660, and >= `egress.close_lag_days + 7` | a conclusion is `stale` when the newest counted confirming week ended more than this many days before `as_of` (exactly this many is fresh) |
| `max_sibling_sites` | int 0..100 | how many sibling sites (sites that did not contribute cells to the key, ranked by the entity's and then the entity type's volume) a question also goes to, for negative evidence and extraction misses |

Two more load checks: `freshness_days` below `close_lag_days + 7` is refused (`must be at least
egress.close_lag_days + 7`), so the newest closed week is never stale; and every egress entity type needs a template
whose `predicates` is null (`egress entity type <t> has no template with predicates null`), so HQ can ask about any
key a cell can name. Templates are chosen in sorted id order: the first whose `entity_types` hold the type and whose
`predicates` are null or hold the predicate.

**Built-in values (the same author's defaults, not fitted):** `device_quality` 2, 3, 3, 42, 2; `claims_integrity`
2, 3, 3, 56, 2; `it_incidents` (B4, copied from the device pack) 2, 3, 3, 42, 2 (in the table's order).
`claims_integrity`'s one template also covers `damage_area` since G6.

**Verdict buckets** (`egress.json`'s `verdict_count_buckets`). The first edge must equal `k`. A verdict carries a
count only as a label: `'<k'` for 1 to `k - 1`, then `lo-hi` per edge up to the next edge minus 1 (or `lo` alone
when that is `lo`), then `last+`. G6 set the edges to `[k, 10, 50]` in both packs: `device_quality` `[3, 10, 50]`
(`'<k'`, `3-9`, `10-49`, `50+`) and `claims_integrity` `[5, 10, 50]` (`'<k'`, `5-9`, `10-49`, `50+`). Only
`config_hash` changed; the other three hashes are byte-identical to G5 (`INTEGRATION.md`, G6). `it_incidents` (B4)
has `[3, 10, 50]`. `egress.json`'s
`verify_max_records` caps the records a site judges for one question (newest first; the verdict says `truncated`)
and `question_budget_per_entity_per_day` caps the distinct questions a site answers about one entity per day of its
own clock (`LEAKAGE.md` section 9). Audit round 2 added `question_entities_per_site_per_day` (1 to 1,000; 50 in both
packs then, and in `it_incidents`), the distinct entities a site answers questions about per day, so guessing many ids is capped too; it changed
only `config_hash` (`device_quality` `ac59c4cb...`, `claims_integrity` `12d62cdf...`).

### 1.3 Follow-up types: `followups.json` (G7)

**Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.** `ARCHITECTURE.md` section 16 describes the layer; this section covers only the pack data.

G7 changed only the built-in packs' `args_schema` blocks (D2), so only `config_hash` changed (the table in
`INTEGRATION.md`, G7); roles, owner and escalation roles, daily caps, acknowledgement days and draft schemas are as
in G2:

| Pack | Type | Tier | `args_schema` since G7 |
|---|---|---|---|
| `device_quality` | `evidence_packet` | T0 | `{conclusion: conclusion_id}` (was also `product_id`, `failure_mode`, `max_records`) |
| `device_quality` | `capa_initiation_draft` | T1 | `{conclusion: conclusion_id, severity: enum [low, medium, high]}` (unchanged) |
| `device_quality` | `scar_draft` | T1 | `{conclusion: conclusion_id, supplier_id: entity_id supplier}` (was also `component_id`) |
| `claims_integrity` | `evidence_packet` | T0 | `{conclusion: conclusion_id}` (was also `shop_id`, `pattern`, `max_files`) |
| `claims_integrity` | `siu_referral_draft` | T1 | `{conclusion: conclusion_id, priority: enum [routine, urgent]}` (was also `area`) |
| `it_incidents` (B4) | `evidence_packet` | T0 | `{conclusion: conclusion_id}` |
| `it_incidents` (B4) | `problem_record_draft` | T1 | `{conclusion: conclusion_id, impact: enum [low, medium, high]}` |
| `it_incidents` (B4) | `vendor_escalation_draft` | T1 | `{conclusion: conclusion_id, vendor_id: entity_id vendor}` |

Why: a packet's target is the conclusion's own key, so a product, failure-mode or shop arg could only repeat it or
contradict it; and generic code cannot tell which integer arg is a record cap (the cap is `egress.json`'s
`verify_max_records`, which the verdict already enforces).

**The scope rule.** A follow-up may be proposed only by the system principal, only on a `supported` conclusion, and
only with args inside that conclusion: every `conclusion_id` arg must equal the conclusion's own id, every
`predicate` arg the conclusion's key predicate, and every `entity_id` arg's (type, value) must be the conclusion's key
entity or an entity of its lineage cells (which are cells of the same key). Anything else is refused
(`args_out_of_scope`). So **a type whose entity arg names another entity type than the conclusion's key can never be
proposed on that conclusion**: `scar_draft` (a `supplier` arg) is proposable only on supplier-keyed conclusions,
`it_incidents`'s `vendor_escalation_draft` (a `vendor` arg) only on vendor-keyed ones, and every other built-in type on
any supported conclusion. When you design a pack's follow-up types, give each type at
most one `entity_id` arg, of the entity type it is meant for; `enum` and `integer` args are free choices the system
fills (G0's harness takes an enum's first value and an integer's minimum, a harness choice, not a system one), and
there is still no free-text arg kind.

**The draft template (B1).** Every type carries the required key `template`, right after `draft_schema`: `null`
unless the executor is `draft`, and for a draft type an object with exactly one entry per required property of its
draft schema, naming where the template drafter (`followup/drafts.py`, used whenever no model writes the draft) takes
that property from. A string property takes `headline` (`'<type label>: <predicate label> on <entity type label>
<entity id>'`), `summary` (the conclusion in one sentence: supported, how many confirming sites, the weeks and the
conclusion id), `evidence` (one clause per site packet with status `ok`: its site, verdict and support bucket, its code
labels and its co-mentioned ids with their count labels) or `for_owner` (left for the named owner to write), or a list
of one to three distinct entries from `headline`, `summary` and `evidence`, joined with a space. An array of strings
takes `entity_ids:<type>` (the sorted ids of an egress entity type among the conclusion's entity and the ok packets'
co-mentions), `confirming_sites` or `for_owner` (an empty list). Strings are cut to `maxLength` (whole evidence clauses
first, then the evidence part, then at the last space) and arrays to `maxItems`. The loader refuses, each with a fixed
problem at the template's path: a template on a type that does not draft, or none on one that does (`template is set
exactly when executor is draft`); a missing or an extra property (`missing key`, `unknown key`); a word or form it does
not know (`unknown template source`); a list entry named twice (`duplicate template source`); a source on a property
of the wrong type, a list on an array, or an empty or too-long list (`template source does not fit the property's
type`); and `entity_ids:` with a type that may not leave a site (`not an egress entity type`). `template` is a reserved
word. The built-in templates:

| Pack | Type | `template` |
|---|---|---|
| `device_quality` | `evidence_packet` | `null` |
| `device_quality` | `capa_initiation_draft` | `title: headline`, `problem_statement: [summary, evidence]`, `containment: for_owner`, `affected_lots: entity_ids:lot` |
| `device_quality` | `scar_draft` | `title: headline`, `nonconformance: [summary, evidence]`, `requested_actions: for_owner` |
| `claims_integrity` | `evidence_packet` | `null` |
| `claims_integrity` | `siu_referral_draft` | `title: headline`, `pattern_summary: [summary, evidence]`, `requested_checks: for_owner` |
| `it_incidents` (B4) | `evidence_packet` | `null` |
| `it_incidents` (B4) | `problem_record_draft` | `title: headline`, `problem_statement: [summary, evidence]`, `workaround: for_owner`, `affected_releases: entity_ids:software_release`, `affected_services: entity_ids:it_service` |
| `it_incidents` (B4) | `vendor_escalation_draft` | `title: headline`, `issue_summary: [summary, evidence]`, `requested_actions: for_owner` |

Only `config_hash` changed (`INTEGRATION.md`, B1). Up to B1 the template drafter wrote `'<type label>: <summary>'` into
every required string and `[]` into every array, so a CAPA draft's title, problem statement and containment were the
same sentence and its affected lots were empty.

## 2. Freezing and the four hashes

`load_pack` validates the files, then their cross-references, then hashes the parsed files and freezes the
result into a `FrozenPack`: frozen dataclasses, read-only mappings built in sorted key order, tuples and
frozensets. Nothing in it can be changed after loading.

| Hash | Scope | Changes when |
|---|---|---|
| `config_hash` | every file except `generator.json` and the fixtures, plus the version | any policy, vocabulary, mapping, detector, rule, question or follow-up value changes |
| `vocabulary_hash` | `vocabulary.json`, `aliases.json`, `codes.json`, the mappings, plus the version | what extraction can see changes; **this is what E1 pins** |
| `detector_hash` | `detectors.json`, `rules.json`, plus the egress fields detectors read (`k`, `suppress_below_days`, `count_granularity`, `close_lag_days`, `min_window_weeks`) | a detector input changes |
| `fixtures_hash` | `generator.json` and every fixture line | the synthetic world or the labelled fixtures change |

Each hash is a sha256 over canonical JSON (sorted keys, no whitespace), so it does not change with key order,
indentation or CRLF line ends, and it is the same on every machine and under every `PYTHONHASHSEED`. Displays show
the first 12 hex characters; run files always carry all 64.

**Built-in vs path packs.** `load_pack("device_quality")` loads the built-in directory and requires the pack id to
equal the directory name (`source: builtin`). Any other reference is a directory path (`source: path`), which may
have any name; that is how you freeze an extended copy before labelling. Check a pack with:

```
python -m mycelic.collective.packs.loader check <pack-dir>
```

## 3. The built-in packs

**`device_quality`** is the device-quality pack: products, alias-only components, lots and suppliers; ten
failure-mode predicates and a generic one (English, with a German subset); placeholder codes; a fictional six-plant device maker as
its world; follow-ups `evidence_packet` (T0), `capa_initiation_draft` and `scar_draft` (T1). Its vocabulary is an
illustrative subset, and its `pack.json` says so:

> This pack is an illustrative subset written for testing, not the official FDA or IMDRF code list. Its ILL- codes are invented placeholders, and its products, lots, suppliers, plants and people are fictional.

Before E1 or any partner work, extend it from the published code lists and the id shapes in your own data, and
freeze the copy (RUNBOOK, "Freeze or extend a pack").

**`claims_integrity`** is a second pack in an unrelated field: repair shops, clinics, tow operators and damage
areas at a fictional insurer, with predicates such as `supplement_after_teardown` and `duplicate_invoice` and an
SIU referral draft. It runs through exactly the same generic code as the device pack, which shows that the code
holds no device literal. **It is not evidence of generality.** It was written by the author of the generic code, so
it cannot measure X3 (a second pack built by someone else, with the engineer-hours recorded; STRATEGY section 11.2).
Its disclaimer says so, and it is never used in external material.

**`it_incidents`** (B4) is a third pack in another unrelated field: multi-site IT operations incidents at the six
subsidiaries of a fictional group, in English and German. Its entity types are IT services (alias-only: mail, remote
access, sign-in, finance ERP, file share, backup, printing, the self-service portal), configuration items (host names;
the first entity type of a built-in pack that never leaves a site, `egress` false, so `entities.config_item` is a
never field), software releases (`REL/nnnn/nn`, with slashes), vendors and change requests; eleven failure-mode
predicates and a generic one; follow-ups `evidence_packet` (T0), `problem_record_draft` and `vendor_escalation_draft`
(T1). It was built as data only, from requirements written before any of its files existed
(`docs/collective/x3/REQUIREMENTS.md`), with zero lines of code changed; what the data could not express is recorded
as generality gaps in `docs/collective/x3/effort.json` (section 4.1). **It is not X3 either**: the same AI system wrote
it and the generic code, so it is internal evidence that a new field can be configuration, never a buyer claim. Its
`pack.json` says so:

> This pack is an illustrative vocabulary for the IT operations incidents of a fictional group, written for testing. Its ITC- codes are invented placeholders, and its services, vendors, releases, changes, hosts, subsidiaries and people are fictional. It was written by the same author as the generic code, as an internal generality measurement (B4); it is not X3 by a non-author and it is never used externally.

## 4. How to add a pack

These are the steps B4 followed for `it_incidents` (same author as the generic code; section 3). Each names what the
generic code expects of the data, so the next pack can follow them.

1. **Write the field's requirements first** and commit them alone, before any pack file exists
   (`docs/collective/x3/REQUIREMENTS.md`, B4a). A requirement found missing later is listed as post hoc, never added to
   that file.
2. **Copy the pack closest to your field** to `mycelic/collective/packs/data/<pack_id>/` (or any directory, loaded by
   path) and delete what has no counterpart (B4 copied `device_quality`, which has two languages, an alias-only type
   and linked types, and deleted `mapping_openfda.json` and `fixtures/plant_e2_smoke.json`). Rewrite `pack.json` (id,
   title, languages, version, `illustrative`, `disclaimer`, `same_author_as_code`).
3. **Write the vocabulary before you look at any outcome**: entity types and their id shapes, predicates and their
   lexicon per language with every inflected form listed, negation cues. Ids must avoid reserved words and every
   identifier of the generic code (`DomainLiteralTests`); an alias may not fold to its own target, so give an
   alias-only id a descriptive name and its acronym as an alias; an id format's separator is `-`, `/` or none.
4. **Write `mapping.json` against the export's field names**, with a `value_map` for tool labels and a `where` filter
   for the narrative parts that people wrote. Never-leave fields (narrative, reporter, every person field, and
   `entities.<type>` of a type with `egress` false) go into `egress.json`'s `never_fields`.
5. **Write `generator.json`** so the generic generator can build a world whose gold equals lexical extraction on every
   record: every universe id canonical and every alias-only id with an alias; templates single sentences with slots on
   word boundaries, each using a lexicon phrase exactly; a negated template with its cue within `negation_window`
   tokens before the term; no alias word inside another id (a host named after a service is read as that service);
   volumes and template variety enough for unique narratives over 104 weeks.
6. **Hand-label at least 40 fixtures** from the text, as a careful person would, covering every predicate twice,
   negation in each language, aliases, id variants, near-miss ids, attached and entity-only claims, zero-claim records,
   person names in text, HTML, codes that disagree with the narrative and a mixed-language record. Where the lexical
   extractor disagrees, record the disagreement; never relabel toward it.
7. **Write `fixtures/plant_smoke.json`** (three patterns, one decoy per class) so `check_plant` passes for a 52-week
   world and a 104-week world, both with `eval_from` 26.
8. **Run `python -m mycelic.collective.packs.loader check <pack-dir>`** until it prints the four hashes, pin them
   (`B4_HASHES` in `tests/mycelic/test_collective_pushdown.py`), and add the pack's row to every per-pack test table
   (`LoopCoverageTests` in `tests/mycelic/test_collective_guards.py` fails until every table covers every built-in
   pack).
9. **Run G0, the X1 construction smoke and a demo scenario** on the pack (`docs/collective/x3`).
10. **Anything only code could fix is a generality gap**: record it with its exact error line, fix it only if it
    blocks one of the runs above, generically and without naming the field.

### 4.1 As measured for it_incidents (B4)

Every effort figure of B4 is in `docs/collective/x3/effort.json`; this table only points at it. It is the same
author's effort with the generic code in mind, an AI agent's wall clock and file sizes, not engineer-hours by a
non-author (X3).

| Pointer | What it holds |
|---|---|
| `docs/collective/x3/effort.json#/totals` | files, lines and bytes of the pack directory, and its lines changed against the template pack |
| `docs/collective/x3/effort.json#/files` | the same per file |
| `docs/collective/x3/effort.json#/code_lines_changed` | lines changed under `mycelic/` and `demo/` outside the pack directory |
| `docs/collective/x3/effort.json#/code_files_changed` | those files |
| `docs/collective/x3/effort.json#/test_lines_added` | test lines added and deleted (generalised loops and the new module) |
| `docs/collective/x3/effort.json#/requirements` | per requirement: expressible, approximated or not expressible, and how |
| `docs/collective/x3/effort.json#/gaps` | the generality gaps, each with its exact error line or observed behaviour |
| `docs/collective/x3/effort.json#/term_renames` | pack terms renamed because a check refused them |
| `docs/collective/x3/effort.json#/fit_to_code_choices` | data choices made to fit the generic code |
| `docs/collective/x3/effort.json#/wall_clock` | the AI agent's wall clock per milestone, not engineer-hours |

## 5. Drafting a pack from an export

`python -m mycelic.collective.onboard` drafts a pack from one export: CSV, pipe- or tab-delimited text with a header,
or JSON lines. Its predicates are the export's own filed categories. Its terms are counted from the export's own
narratives. Everything else comes from a neutral template in `mycelic/collective/onboard/data/`. The code names no
field: the roles file says which column is which.

- `draft` writes the pack and `draft.json`; `export` writes the normalised records the drafted mapping reads; `check`
  checks the privacy floor and the loader; `score` and `report` run test D001.
- The rule is `docs/collective/onboard/CHOICE-D001.md`. The build is `docs/collective/onboard/BUILD-D001.md`.
- It is not yet measured. Test D001 measures it on public records; until its run, nothing here claims that a drafted
  pack reads a field well.
