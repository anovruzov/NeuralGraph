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
| `questions.json` | pushdown question templates; only `{window}`, `{predicate_label}`, `{entity_type_label}` and `{entity_id}` may appear, without conversions or format specs, so a template can embed no record content |
| `followups.json` | roles and follow-up types: tier (T0 packet, T1 draft, T2 write; T3 is never an action type and an enabled T2 is refused), owner and escalation roles, daily cap, acknowledgement days, an argument DSL (entity id, predicate, conclusion id, enum, integer: no free text) and, for drafts, a JSON schema whose every string has a `maxLength` |
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

**Both built-in packs use the same author's defaults, not fitted values:** budget 5 (4 for `claims_integrity`),
cooldown 4, baseline 26, window 8, minimum history 12; `alpha_site` 0.01, `lambda_floor` 0.01, `p_min` 0.01, `p_max`
0.25, burst and co-occurrence `min_sites` 2; `pmi_smoothing` 0.5, `pmi_delta` 1.0; `res_conf_min` 0.95;
`echo_min_ratio` 0.5; `stale_days` 42 (56 for `claims_integrity`, whose `close_lag_days` is 21);
`base_rate_site_fraction` 0.5; bias -4.0 and weights 0.35, 0.5, 0.4, 0.3, -1.0, -1.5, -1.0, -1.5 in the order above.
They are default weights; nothing learns them. Changing any of them changes `config_hash` and `detector_hash`, so
site stores made with the old pack refuse to reopen (`site_info mismatch`) and a new HQ store is needed.

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

## 3. The two built-in packs

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

## 4. How to add a pack

1. Copy the pack closest to your field to a new directory and change `pack.json` (id, title, languages, version,
   `illustrative`, `disclaimer`).
2. Write the vocabulary before you look at any outcome: entity types and their id shapes, predicates and their
   lexicon per language, negation cues. Freeze it (step 6) before anyone names an incident.
3. Write `mapping.json` against a real export's field names; never-leave fields (narrative, reporter, every person
   field) go into `egress.json`'s `never_fields`.
4. Write `generator.json` so the generic generator can build a world: every universe id must be canonical, every
   alias-only id needs an alias, templates must be single sentences with slots on word boundaries.
5. Hand-label at least 40 fixtures that cover every predicate twice, negation, aliases, id variants, near-miss ids,
   attached and entity-only claims, zero-claim records, person names in text, HTML and codes that disagree with the
   narrative.
6. Run `python -m mycelic.collective.packs.loader check <pack-dir>` until it prints the four hashes, and record
   them. If a test or the domain-literal guard fails because the generic code needs a new literal, the pack has
   found a gap in the generic code: fix the code generically, never by naming the field.
