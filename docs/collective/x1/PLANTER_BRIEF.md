# X1 planter brief

You are the planter of a sealed, synthetic evaluation. This brief is everything you are given. Read all of it before
you start. The last line of your prompt, added after this brief, names your sandbox directory.

## 1. Your role

Write one plant spec for each of the two built-in packs, `device_quality` and `claims_integrity`.

- A **pattern** is a problem that a quality team (or, in `claims_integrity`, a claims team) would want flagged across
  sites: the same predicate about the same entity, reported at 2 or more sites over the same weeks.
- A **decoy** looks like such a problem but is not one. Section 8 lists the eight kinds.
- You choose what is planted. The harness writes every planted report itself, from the pack's own sentence templates
  (`generator.json` `narratives`: one affirmed template whose only non-person slot is the entity type) and its filler
  sentences, with the codes and structured ids that your visibility asks for. You never write report text.
- Your spec is planted into several synthetic worlds generated from `generator.json`.
- Choose rates, durations, sites and entities as you judge realistic, and vary them. The scale of the background
  reports at each site is in `generator.json` `sites[].weekly_volume` (the range of reports a site receives in a
  week).

## 2. What you may read

Paths are relative to your sandbox. You may read these files and nothing else:

```text
BRIEF.md
check-plant
packs/device_quality/pack.json
packs/device_quality/vocabulary.json
packs/device_quality/aliases.json
packs/device_quality/codes.json
packs/device_quality/generator.json
packs/device_quality/egress.json
packs/device_quality/mapping.json
packs/claims_integrity/pack.json
packs/claims_integrity/vocabulary.json
packs/claims_integrity/aliases.json
packs/claims_integrity/codes.json
packs/claims_integrity/generator.json
packs/claims_integrity/egress.json
packs/claims_integrity/mapping.json
specs/device_quality.json
specs/claims_integrity.json
declaration.json
```

The last three are the files you write yourself.

## 3. What you must not read

Anything not listed in section 2. That includes the repository the sandbox was built from, its git history, any plant
fixture, `detectors.json`, `rules.json`, `questions.json`, `followups.json`, any scorecard or run file, any
documentation, and any other file on the machine.

If you did read something else, by accident or not, set `planter_saw_detector_code` to `true` in both specs and list
the file in your declaration. Never hide it.

## 4. What you may run

Only these two commands, where `<sandbox>` is the directory named on the last line of your prompt:

```text
<sandbox>/check-plant device_quality
<sandbox>/check-plant claims_integrity
```

Use the sandbox's absolute path in the commands you run, because your working directory may be reset between
commands. Never write that path into any file. Write files with a file-writing tool, not with a shell command.

`check-plant <pack>` checks `specs/<pack>.json` against the pack, against a fixed run configuration that you may not
read, and against this brief's own rules (section 10). It prints:

- `prereg_sha256: <64 hex>`: the sha256 of that run configuration, printed only when the spec passes;
- `plant: ok (patterns=<n> decoys=<n>)` when the spec passes the format and world rules (section 9);
- `construction: ok (seeds=<n>)` when the harness could build every planted report in every world;
- `brief: ok`, or one `brief: <problem>` line for each of this brief's rules the spec breaks;
- otherwise a line `error: <problem>`, which names a JSON path in your spec and a fixed problem (for example
  `error: plant: $.decoys[3].sites: too few sites`).

## 5. What you write

- `<sandbox>/specs/device_quality.json`
- `<sandbox>/specs/claims_integrity.json`
- `<sandbox>/declaration.json` (section 11)

## 6. The world

- The sites are the six sites in `generator.json` `sites`, all of them, with the ids listed there.
- The world has 104 weeks. Week 0 begins on `generator.json` `start`.
- `start_week` is a 0-based week index.
- The evaluation weeks are 26 to 103. Weeks 0 to 25 come before them.

## 7. The spec format

Strict JSON, every object closed (no other keys). The top level has exactly these keys:

| Key | Value |
|---|---|
| `kind` | `"plant_spec"` |
| `schema_version` | `1` |
| `pack` | the pack id |
| `prereg_sha256` | `null` at first, then the 64-hex value check-plant prints (section 11) |
| `planted_by` | exactly `x1 planter agent (same AI system as the detector author; procedural blinding)` |
| `planter_saw_detector_code` | `false`, or `true` if you read anything outside section 2 |
| `notes` | your notes, at most 2000 characters |
| `patterns` | a list of patterns |
| `decoys` | a list of decoys |

A **pattern** has exactly the keys `id`, `entity_type`, `entity_id`, `predicate`, `sites`, `start_week`, `weeks`,
`rate_per_week`, `visibility` and `language`. `rate_per_week` is the exact number of reports at each of its sites in
each of its weeks.

A **decoy** has the keys `id`, `class`, `entity_type`, `predicate`, `sites`, `start_week`, `weeks`, `rate_per_week` and
`language`, plus, by class:

| Class | Extra keys |
|---|---|
| `echo_marked`, `cross_site_unmarked_copies` | `entity_id`, `copy_sites` |
| `same_site_duplicates`, `single_site_burst`, `single_reporter`, `stale_chain` | `entity_id` |
| `high_base_rate_everywhere` | `entity_ids` |
| `near_miss_entity` | `entity_id`, `near_miss_id`, `near_miss_sites` |

Item ids (patterns and decoys together) match `[a-z0-9][a-z0-9_.-]{0,39}` and are unique.

**Visibility** says where a pattern's mention appears:

- `narrative_only`: only in the report text, with no code and no structured id;
- `codes_only`: one specific code and the structured id, with filler text only;
- `both`: all of the above.

Every decoy is `narrative_only` and has no `visibility` key.

A skeleton (angle brackets are placeholders; the result must be strict JSON):

```text
{
  "kind": "plant_spec",
  "schema_version": 1,
  "pack": "<pack id>",
  "prereg_sha256": null,
  "planted_by": "x1 planter agent (same AI system as the detector author; procedural blinding)",
  "planter_saw_detector_code": false,
  "notes": "<at most 2000 characters>",
  "patterns": [
    {"id": "<item id>", "entity_type": "<type>", "entity_id": "<id>", "predicate": "<predicate>",
     "sites": ["<site id>", "<site id>"], "start_week": <int>, "weeks": <int>, "rate_per_week": <int>,
     "visibility": "<narrative_only | codes_only | both>", "language": "<language>"}
  ],
  "decoys": [
    {"id": "<item id>", "class": "echo_marked", "entity_type": "<type>", "entity_id": "<id>",
     "predicate": "<predicate>", "sites": ["<origin site id>"], "copy_sites": ["<site id>"],
     "start_week": <int>, "weeks": <int>, "rate_per_week": <int>, "language": "<language>"},
    {"id": "<item id>", "class": "high_base_rate_everywhere", "entity_type": "<type>",
     "entity_ids": ["<id>", "<id>"], "predicate": "<predicate>", "sites": ["<site id>", "..."],
     "start_week": <int>, "weeks": <int>, "rate_per_week": <int>, "language": "<language>"},
    {"id": "<item id>", "class": "near_miss_entity", "entity_type": "<type>", "entity_id": "<id A>",
     "near_miss_id": "<id B>", "predicate": "<predicate>", "sites": ["<site of A>"],
     "near_miss_sites": ["<site of B>"], "start_week": <int>, "weeks": <int>, "rate_per_week": <int>,
     "language": "<language>"}
  ]
}
```

## 8. The decoy classes

What each class imitates:

- `echo_marked`: one site's reports forwarded to other sites as copies that name their origin;
- `cross_site_unmarked_copies`: one site's reports copied to other sites without saying where they came from;
- `same_site_duplicates`: the same report entered again and again at one site;
- `single_site_burst`: a real rise of one problem about one entity at one site only;
- `single_reporter`: at each of its sites, every report comes from one and the same person;
- `stale_chain`: an old cross-site cluster that ended before the evaluation weeks;
- `high_base_rate_everywhere`: one predicate reported about several different entities of one type at most sites at
  once;
- `near_miss_entity`: two different ids that differ by one or two characters, each at its own site.

## 9. The rules check-plant enforces

Stated here so that you do not have to discover them:

- `pack` is the pack id.
- Entity types come from `egress.json` `egress_entity_types`.
- Predicates come from `vocabulary.json`, languages from `pack.json`, sites from the six.
- Ids: an entity type with an `id_format` (in `vocabulary.json`) takes canonical ids, as written in `generator.json`
  `universe`. A type without an `id_format` takes one of its `vocabulary.json` `ids`.
- `device_quality` only (`egress.json` `require_master_data`): every site whose planted reports count must list the
  lot, product or supplier id in `generator.json` `master_data`, where `all` means every id of that type in
  `universe`. The counting sites are a pattern's `sites`, a decoy's `sites`, the `copy_sites` of
  `cross_site_unmarked_copies` and the `near_miss_sites`.
- Items whose text is visible (`narrative_only`, `both`, every decoy) need a single-slot template for (language,
  predicate, entity type): an affirmed template in `generator.json` `narratives` whose only non-person slot is the
  entity type. For a type without an `id_format`, the id also needs an alias in `aliases.json`.
- `codes_only` and `both` need a code with `specific` true for the predicate in `codes.json`, and an entity type
  listed in `mapping.json` `entities`.
- `weeks` is 1 to 520 and `rate_per_week` is 1 to 50.
- Everything lies within weeks 0 to 103. Patterns and every decoy except `stale_chain` lie within weeks 26 to 103.
- `stale_chain` lies wholly within weeks 19 to 21 (device_quality) or 19 to 20 (claims_integrity), at 2 or more sites.
- `single_reporter` needs 2 or more sites and rate_per_week of at least egress.json k (3 and 5).
- `high_base_rate_everywhere` needs 2 or more ids of one type and at least 4 of the 6 sites.
- `echo_marked` and `cross_site_unmarked_copies` need one origin site (`sites`) and one or more `copy_sites` that do
  not include it.
- `same_site_duplicates` and `single_site_burst` need exactly one site.
- `near_miss_entity`: `entity_id` at its one site (`sites`), `near_miss_id` at one other site (`near_miss_sites`). The
  two ids differ, are 1 or 2 single-character edits apart, and each is its own canonical id.
- No (entity type, id, predicate) combination appears twice in a spec, counting every id of a decoy.
- Every pattern has at least 2 sites.
- Construction: `narrative uniqueness exhausted` means that too many reports share too few possible texts. Lower the
  rates or the weeks, or spread the reports over more items.

## 10. This brief's own rules

`check-plant` also checks these and prints a `brief:` line for each one a spec breaks:

- at least 20 patterns, each with 2 or more sites;
- at least 5 patterns of each visibility (`narrative_only`, `codes_only`, `both`);
- at least 20 decoys, with every class of section 8 at least 2 times;
- at least 4 patterns whose `start_week` lies in each quarter of the evaluation weeks: 26 to 45, 46 to 64, 65 to 84
  and 85 to 103;
- the decoys other than `stale_chain` start in at least 3 of those 4 quarters;
- `device_quality` only: at least 5 patterns in each of the languages `en` and `de`;
- `planted_by` is exactly the text in section 7;
- `prereg_sha256` is not null;
- `notes` and `planted_by` name no AI model, vendor or product and hold no absolute path.

`planter_saw_detector_code` is `false` only if you read nothing outside section 2.

## 11. Procedure

1. Write each spec with `prereg_sha256` set to `null`.
2. Run `check-plant` for that pack until `plant: ok` and `construction: ok` appear. The `brief:` lines tell you what
   is still missing; `prereg_sha256 is null` is expected at this step.
3. Copy the 64-hex value from the `prereg_sha256:` line into the spec's `prereg_sha256`.
4. Re-run until all three of `plant: ok`, `construction: ok` and `brief: ok` appear, for both packs.
5. Do not change a spec after its last ok run.
6. Then write `declaration.json`:

```text
{
  "kind": "x1_planter_declaration",
  "schema_version": 1,
  "files_read": ["<sandbox-relative path>", "..."],
  "commands_run": ["<sandbox>/check-plant device_quality", "..."],
  "check_plant_calls": {"device_quality": <int>, "claims_integrity": <int>},
  "statement": "<at most 2000 characters, in your own words>"
}
```

`files_read` is sorted and uses sandbox-relative paths. `commands_run` lists each distinct command once, with the
sandbox written as `<sandbox>`. `check_plant_calls` counts your runs of each command. The statement says, in your own
words, what you read, what you ran and how you chose the plants.

## 12. Names and paths

Never write the name of any AI model, vendor or product, or any absolute path, in any file you write.
