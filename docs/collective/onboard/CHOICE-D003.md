# Drafting test D003: a pack drafted from one device maker's own FDA reports

D002 passed all six of its criteria (`CHOICE-D002.md`, "Runs";
[run 38024536763](https://github.com/anovruzov/NeuralGraph/actions/runs/38024536763)). Packs drafted by
`mycelic/collective/onboard/` from one company's own export, with no code per field, read the held-out later records
of five mine operators better than record-blind and label-blind controls, and six vehicle makes' complaints better
than the hand-built vehicle pack.

Medical devices are the beachhead (`docs/strategy/MARKET.md`, sections 1 and 3). D003 asks D002's question there:

> Does a pack drafted by the same onboard code from one device maker's own FDA adverse-event reports (openFDA
> `device/event`, which publishes FDA's MAUDE reports) read that maker's later reports better than D002's controls,
> and better than the hand-built `device_quality` pack in a label space both name?

**D003 is a reading test only.** No field of these reports stands for a site that a company runs (section 2.5). So
D003 makes no claim about sites, cross-site detection or early warning, and D002's M4 (the pilot audit) does not
apply.

This file is committed on 2026-10-10, alone, before any of D003's code. No one has seen any value of a D003 company's
reports: no narrative, no problem-code distribution, no per-manufacturer category count. How it is built goes in
`BUILD-D003.md`. Every rule of D002, which is D001's rule as amended with D002's changes, stands for D003 except where
this file says otherwise. `CHOICE-D001.md`, `CHOICE-D002.md`, `D001-settings.json`, `D002-settings.json`,
`run-001.json`, `run-002.json` and `docs/collective/replay/vehicles/pack/` do not change.

**Amended before any run, on 2026-10-10.** The section of that name, just before "Runs", changes the export, the
hand pack's entity, C2's matched space and controls, the deciding intervals, the privacy floor's unit, C1's controls,
the fetch and the list of onboard changes, and qualifies the sentence above and the declaration. It was written
before any of D003's code. Where it disagrees with the text above it, it wins.

## Why openFDA now, when D001 left it out

`CHOICE-D001.md` section 2.3 gave three reasons. D003 answers each:

| D001's reason | D003 |
|---|---|
| None of the probe's fields (`mdr_text`, `product_problems`, `device`, `date_received`, `manufacturer_name`) is a site that a company runs. | Still true (section 2.5). D003 gives each company one site and reads only. |
| The reports come through a paged API, so a fixed export needs a sampling rule the probe did not test. | Section 2.3 fixes one: whole days, in a seeded order, under a stated request budget, stopping with a stated failure when it cannot. |
| The run's one job already held two arms. | D003 runs alone, in its own workflow, with one arm. |

## The declaration

- The rule was written by the AI system that wrote this repository's code, the `device_quality` pack, D001, D002 and
  the two public device replays. It has seen D002's result.
- **What it has seen of openFDA device events.** Everything below was in this repository before this file:
  - `SOURCES.md`: the endpoint answered the runner; 2,627,151 reports were received in 2024; the field names above.
  - MARKET 3.5 and `docs/strategy/data/field-coverage.json`: the ten product codes with the most 2024 reports, their
    report counts and field shares, and each code's most frequent coded problem in a 200-report sample. The file also
    lists up to eight coded problems per code with their counts in that sample, the nine generic problem terms and
    the placeholder lot values.
  - MARKET 3.6 (`docs/strategy/data/site-split.json`): device manufacturer names with their 2024 report counts for
    eight groups, and how each group's ten largest product codes split by name.
  - Public replays 001 and 002 (`docs/collective/replay/CHOICE-001.md`, `CHOICE-002.md`, `docs/lab/RESULTS.md` runs 3
    and 4): Becton Dickinson's names and four product codes' report counts; the 16 problem terms that replay 002's
    rule added to `device_quality`'s six, and that the mapped terms carry 7,154 of the 22,113 reports of that probe's
    top terms. The author read those 22 names, without counts, in `lab/packs/device_quality_bd/mapping_openfda.json`.
    `docs/collective/replay/inputs-002.json` holds Becton Dickinson's 100 most reported problem terms per
    code, with counts, for reports received in 2021 and 2022, which are D003's training years. The author did not open
    `inputs-001.json`, `inputs-002.json` or `site-split.json` for this rule.
  - No narrative of any device report.
- **So D003 is not blind.** A company whose name appears in MARKET 3.6 has its 2024 report count in the repository.
  If a Becton Dickinson name is chosen, part of its training-year problem distribution is in the repository too, and
  the comparator's 16 added names come from that distribution (section 6.2). The rule's choices that such knowledge
  could shape are the windows, the text type, the comparator map, the generic terms and the thresholds. Each is
  justified below from a recorded fact or file. C1's threshold is D002's; C2's are new.
- **What is new and chosen after D002's run:** C2 as superiority with a 100-record minimum (section 8), the site
  count of the floor (section 2.5) and the generic problem terms (section 3). D002's C2 difference was 0.184.
- **No model is used.** Every reader is the pinned lexical extractor (`mycelic/collective/edge/extract.py`) with a
  different pack.
- **The answer key is the problems each report was filed under,** not labels anyone checked. A narrative can describe
  a problem it was not filed under, and the reverse. No reader can reach F1 1.0.

## 1. What D003 keeps, and what it changes

**Kept as D002 ran it.** Rule 1, the drafter: reading an export (1.1 as D002 changed it), roles, dates, predicates,
lexicons, the template and the privacy floor's check, with D001's amendments A1 to A14 and D002's B2. Rule 3, the
held-out sample. Rule 4, the controls, with A13. Rule 5, the metrics. Rule 8, what is printed, with the last guard per
company (A10) and the backstop (A11). Rule 9 as D002 changed it: the run starts after the data is read and split.

**Changed, each in its own section:**

- the source, the companies, the windows and the export (section 2);
- the floor's site count, S = 1, since a company has one site (section 2.5);
- FDA's generic problem terms are non-specific labels (section 3);
- the hand pack and its comparator mapping (section 6.2);
- the criteria: C1 as D002's; C2 as superiority on at least 100 records; M1 to M3; M4 does not apply (section 8);
- six changes to the onboard package, P1 to P6, each off for D002's settings (section 11);
- the seeds `d003:boot`, `d003:sample`, `d003:permute` and `d003:days`; D003's own run file and workflow
  (section 12).

## 2. The source, the companies and the files

### 2.1 Access: openFDA's paged API, no bulk file

- **Only the GitHub Actions runner reads device data.** The build's tests and dry run use invented records and a
  local server. A refusal anywhere is recorded and never worked around, as `SOURCES.md` requires.
- **The API:** `https://api.fda.gov/device/event.json`, without a key. What is recorded of it:
  - a page of 1000 records without a key is refused with HTTP 403 `API_KEY_MISSING`, and pages of 5 and 100 are
    served (`CHOICE-001.md`, run 1, diagnosed in run 37861797336);
  - pages of 100 fetched in full: replay 001's run 2 read 9,039, 9,204, 9,010 and 1,633 reports for four product
    codes, none truncated, so the deepest skip recorded is 9,200 (`CHOICE-001.md`, run 2);
  - `count=device.manufacturer_d_name.exact&limit=100` lists the 100 names with the most reports, with their counts
    (`tools/market/openfda_sites.py`, run 37854721760, MARKET 3.6);
  - `meta.results.total` gives a query's report count (`tools/market/openfda_coverage.py`);
  - openFDA allows a client without a key 1,000 requests a day, and the lab caps its own keyless fetch at 800
    (`lab/request.py`, `docs/lab/README.md`).
- **No bulk file.** The only openFDA bulk input this repository has read is `device/registrationlisting`, through
  openFDA's download index (MARKET 3.4). No device-event bulk file, partition, size or layout is recorded, so D003 uses
  none.
- **The HTTP layer is the connector's**, imported and never modified (`mycelic/collective/connectors/openfda.py`,
  hash-pinned): `build_opener` (the environment's proxy, no redirects) and `_get` (429 and 5xx retried up to 6 times,
  waiting `Retry-After` up to 60 s or else 1 to 32 s; an unreachable host retried twice, then an error). A query is
  encoded as the connector's `page_url` encodes it (`quote_plus`), the form replay 001 fetched with. Each request
  carries D002's User-Agent, `mycelic-onboard (pack-drafting test on public data)`.
- **An error is printed by its HTTP status and openFDA's `error.code` only,** never `error.message`, which can echo
  the query and so a manufacturer's name.

### 2.2 The company

- **The field:** `device[].manufacturer_d_name`. MARKET 3.6 records that it names the plant or legal entity that made
  the device, and counts reports by it. Both public replays matched it exactly (`CHOICE-001.md`, rules 2 and 5). The
  top-level `manufacturer_name` is recorded only as a field name (`SOURCES.md`): no value, count or meaning of it is
  recorded, so D003 does not use it.
- **A company is one exact value of that field.** A report belongs to it when a device entry names it. A report whose
  other device entries name another manufacturer is dropped from the export and counted.
- **Choosing the companies, by report counts only.** Two count requests, one per window (section 2.3), each
  `search=date_received:[<first> TO <last>]&count=device.manufacturer_d_name.exact&limit=100`. 100 is the deepest
  count list recorded without a key. A name is a candidate when it is in both lists. Candidates are excluded, in this
  order:
  1. **a placeholder:** its folded form equals a value of `placeholder_lots` in `field-coverage.json` (such as `unk`,
     `ni`, `n/a`, `unknown`), or it holds no letter;
  2. **not searchable:** it holds a double quote, a backslash or a control character, or has more than 120
     characters;
  3. **a variant spelling of a name already chosen:** its normalised firm name (MARKET 3.4's rule,
     `tools/market/openfda_establishments.normalise_name`: lower case, punctuation and legal-form suffixes removed)
     equals that of a name chosen before it;
  4. **too few reports:** fewer than 2,000 in the training list, or fewer than 1,000 in the test list.
- **The order:** most training-window reports first, ties by the name's UTF-8 bytes. The first five are the
  companies, labelled d1 to d5 in that order. If fewer than five qualify, every qualifying one is used and the report
  says so. If fewer than three qualify, the fetch stops before any day is fetched, and that is not a run.
- **Name variants are not merged.** Exclusion 3 only keeps two spellings that differ by case, punctuation or a legal
  suffix from being two companies. Spellings that differ otherwise, such as Becton Dickinson's in MARKET 3.6, are
  different companies here. The replays did the same: they matched names exactly and merged nothing.
- **Why five, why the largest:** D002's MSHA arm took five companies by training rows, and section 2.3's budget holds
  five. Report counts are the one thing D003 may look at before its run, as D002 chose its companies by row counts.
- **No name is ever printed or uploaded.** The count lists stay in the fetch's memory. The chosen names are written
  only into the exports (section 2.4), which are never printed or uploaded.

### 2.3 The windows and the day sample

- **Windows on `date_received`:** training 2021-01-01 to 2022-12-31; test 2023-01-01 to 2024-12-31.
  - `product_problems` is recorded as present in reports of all four years. The 2021-2022 probe read problem terms
    (`CHOICE-002.md`); 34.4% of the replayed 2023-2024 reports carried a mapped problem code (`CHOICE-002.md`, the
    result); the ten codes' 2024 samples had a coded problem in 98% to 100% of reports (`field-coverage.json`,
    `coded_share`).
  - No fact about reports received in 2025 or later is recorded, so D003 does not use them.
  - Two training years and two later test years, as D002's NHTSA arm read two later years.
- **The exact-name check.** Per company and window, one request,
  `search=device.manufacturer_d_name.exact:"<name>" AND date_received:[<first> TO <last>]&limit=1`. Its total must
  equal the name's count in that window's list. Otherwise the fetch stops. The `.exact` search is not recorded in
  this repository, so this check shows on the runner that it selects what the count counted. The one record it
  returns is discarded unread.
- **Why a day sample.** The largest makers have far more reports than a keyless fetch can read. MARKET 3.6 records
  173,163 for one Medtronic name in 2024 alone. The order of results within a query is not recorded as stable, so the
  first pages of a large query are no fixed sample. A result set fetched whole does not depend on that order. A day is
  the smallest set the recorded date-range search gives.
- **The walk,** per company and window:
  1. The window's calendar days are put in the order of `random.Random("d003:days:<window>:<label>").shuffle` on the
     days sorted by date, where `<window>` is `train` or `test`.
  2. The window's budget is 70 page requests for training and 50 for test.
  3. For each day in that order, while budget is left: request the day's first page (`skip` 0, `limit` 100) for the
     exact name. HTTP 404 `NOT_FOUND` means no report that day. Otherwise the total `T` gives `P = ceil(T / 100)`
     pages. If the `P - 1` further pages exceed the budget left, the day is not kept, its first page is discarded
     unread, and the walk goes on. Otherwise the further pages are fetched (`skip` 100, 200 and so on).
  4. **A kept day must be complete:** its distinct `mdr_report_key` values number exactly `T`, each appears once,
     every record was received that day, and every record names the company in a device entry. Otherwise the fetch
     stops.
- **The request budget,** computed: 2 count requests, then per company 2 exact-name checks and at most 70 + 50 page
  requests, so at most 2 + 5 x 122 = 612 requests. That is under the lab's keyless cap of 800 and openFDA's 1,000 a
  day, leaving at least 388 for the connector's retries. The fetch counts its calls of `_get` and stops before the
  701st. No page is deeper than `skip` 6,900, below the 9,200 recorded.
- **The minimum per company,** checked after its fetch: at least 1,000 kept training reports and 500 kept test reports
  with a narrative (section 4). A company below either is dropped before the run starts and is not replaced. Its
  label stays unused, and the report says so. If fewer than three companies remain, the fetch stops. The test minimum
  leaves room for the 200 drawn records after reports without a specific filed problem and repeated narratives are
  set aside (rule 3).

### 2.4 The export

One JSON-lines file per company, `d<i>.jsonl`, with one object per kept report, sorted by `mdr_report_key`. Each
object has these keys, in this order:

| Column | Role | Value |
|---|---|---|
| `mdr_report_key` | `record_id` | the report's key, as returned |
| `date_received` | `date` | as returned (`YYYYMMDD`, rule 1.3's second format) |
| `product_problems[]` | `category` | the report's `product_problems` list as returned; strings only |
| `mdr_text` | `narrative` | the report's description entries, joined (section 4); absent when there is none |
| `maker_label` | `site` | the company's label, such as `d1`, in every row (section 2.5) |
| `makers[]` | `forbidden` | the exact names of every chosen company, in every row |

- **Nothing else is written:** no patient field, report number, lot, UDI, model, catalog, brand or generic name,
  product code, reporter, facility or distributor field, event type, or text of any other type.
- **Why JSON lines.** A narrative keeps its line breaks inside one JSON string, so no record spans lines, and D002's
  line-break pieces (B2) cannot arise. A list stays a list: rule 1.1 takes a JSON list under a `[]` key as it is,
  without splitting at `;`. Rule 1.1 has read JSON lines since D001, and D002's change (a) concerns delimited files
  only.
- **Why these names.** `mdr_report_key`, `date_received`, `product_problems` and `mdr_text` are openFDA's own names.
  The other two are new. None of the six equals an identifier, string constant or JSON string of the onboard package
  (M1). The package's own guard (`check.package_hits`) was run on the names alone before this commit.
- **The refused values (amendment A2).** The record id and the site refuse by their roles. `makers[]` holds all five
  names, so every company's drafter, check and last guard refuse every chosen maker's name. That covers a string equal
  to a name, a string holding a name as whole words, and a word of four letters or more of a name of two or more
  words. A10 makes the guard read each company against its own export. Through `makers[]` that export carries every
  chosen name, so a company's printed term can never be another chosen maker's name.
- **`companies.json`** maps each label to its file and to counts only (section 2.6). It never holds a name.

### 2.5 Sites: none that a company runs

- **The candidates recorded:**
  - `device[].manufacturer_d_name` is one value within one company.
  - Across one firm's names, MARKET 3.6 records names that are plants for some groups and spellings of one company
    for others. One name holds 87.0% of Boston Scientific's reports, 71.2% of Baxter's and 63.2% of Medtronic's. Per
    product code the split mostly disappears: in 45 of 80 group-and-code pairs, one name holds at least 90%.
  - `device[].manufacturer_d_country` is blank on 14,088 of Baxter's 14,421 reports and `*` on 96,297 of Medtronic's.
  - No other field holding a place is recorded.
- **So no field is a credible site, and D003 invents none.** Each company is one site: `maker_label` holds its label
  in every row. What follows from that:
  - **The privacy floor counts records only.** N stays 10, and S is 1 (`floor_sites`). A count of distinct sites
    would always be 1, so S = 3 would leave every category under the floor. Rule 1.7 and the last guard otherwise run
    as in D002.
  - **The site value is two characters,** so it refuses nothing a term could be, and the backstop ignores it.
  - **M4 does not apply.** The pilot audit's channels compare sites, and D003 runs no audit.
  - **D003 says nothing** about sites, about counting a pattern across a company's sites, or about detection.

### 2.6 What the fetch prints, and when it stops

- **It prints counts only:**
  - the requests sent;
  - for each count list, how many names it returned;
  - the candidates in both lists, the exclusions by reason (1 to 4), and how many qualify;
  - per label, its training and test counts from the lists, and whether its exact-name check passed;
  - per label and window: the days walked, kept, too large and empty; the pages fetched; the reports kept, those with
    a narrative, and those dropped for naming another manufacturer;
  - per label, its export's bytes and sha256, and whether the company is used.
- **It never prints:** a name, a record key, a URL or query, any value of a record, or openFDA's error message.
- **It stops with a stated failure, before the run starts, when:**
  - an HTTP error remains after the connector's retries, or the host cannot be reached;
  - openFDA answers 400 for a skip;
  - a window's total differs from its count;
  - a day's pages do not give its total exactly once;
  - a record names neither its company nor its day;
  - the 701st request would be needed;
  - fewer than three companies qualify, or fewer than three remain.

## 3. The filed category: a list, read as D002 read NHTSA's

`product_problems` holds one or more problems per report. D002's NHTSA arm had the same shape (`components[]`), and the
onboard code handles it as follows:

- a `[]` column is a list column (`exports.py`);
- rule 1.4 counts a row once per category it carries (`draft.group_categories`);
- rule 1.5's `df(t, c)` counts a record under each of its categories;
- the permuted-labels control moves each row's whole list (`draft.permuted_labels`);
- the gold is every filed specific predicate, and a reader's prediction is a set;
- micro F1 counts (record, predicate) pairs, and the majority prior predicts one predicate;
- a record is eligible when at least one filed problem is a specific predicate (`score.draw_sample`).

**D003 uses this as it is: no change to the onboard package for lists.**

**FDA's generic problem terms are non-specific labels.** The other bucket takes the nine strings that
`tools/market/openfda_coverage.py` (`GENERIC_PROBLEMS`) and `field-coverage.json` (`generic_problems`) record:

- `adverse event without identified device or use problem`;
- `appropriate device problem term/code not available`;
- `appropriate term/code not available`;
- `insufficient device problem information`;
- `insufficient information`;
- `no apparent adverse event`;
- `no device problem`;
- `unknown (for use when the device problem is not known)`;
- `no known impact or consequence to patient`.

- **What it does:** a category whose folded label equals one of them goes to the other bucket (rule 1.4), like
  `other` and `unknown`.
- **Why:** MARKET 3.5 defines these as the terms that say no specific problem was identified or no code applies. The
  onboard language file lists none of them.
- **Where the list lives:** in D003's settings (`params.non_specific_labels`, change P4), not in `en.json`, so that
  D002's drafter never reads it.

## 4. The narrative, and the leak of the coded wording

- **Which text:** only `mdr_text` entries whose `text_type_code` is exactly `Description of Event or Problem`. It is
  the one type this repository names and reads: `device_quality`'s `mapping_openfda.json` filters on it, N1 reads it
  by default (`mycelic/collective/experiments/n1_narratives.py`), and the replay tests' stub writes it. No other type
  is recorded here, so reading one would be a guess about content no one has described.
- **How entries are joined:** the non-blank entries of that type, each stripped, ordered by `mdr_text_key` (numeric
  keys first by value, then the rest as text), joined by a blank line. That is `n1_narratives.narrative` with that one
  type. A report with no such entry has no narrative: it is not in the corpus and cannot be sampled.
- **The leak.** A report's description and its coded problems come from the same filing, so the description can
  repeat the coded problem's words. In D002, 33.8% of the sampled NHTSA narratives held their own filed label, and
  0.2% of MSHA's.
- **The guards:**
  1. **The label-names control** is among C1's controls, and C1's margin is over the best control (rule 4, with A13).
     A reader that only finds the label's own words scores like it.
  2. **The label-in-text share,** per company and pooled, is reported beside C1 and C2 (rule 5).
  3. **The echo-free comparison** (change P6, reported only): the same readers on the sampled records whose narrative
     holds none of their own filed specific labels, folded and word-bounded (the test of the label-in-text share).
  4. **The drafter reads training rows only,** and a test record whose folded narrative equals a training one is not
     eligible (rule 3).
  5. **On the comparator's side,** most of section 6.2's names hold a word of the hand lexicon (replay 002's rule
     chose 16 of them that way). On those names the hand pack partly reads the label too.

## 5. The held-out sample, and what is read

Rule 3 as D002 ran it:

- **Eligible:** the test-window records with a narrative and at least one filed specific predicate, less narratives
  repeated from training or within the test window.
- **The draw:** 200 per company (all when fewer are eligible), by `random.Random("d003:sample:maude:<label>").sample`
  from the eligible records sorted by record id. The arm is called `maude`.
- **The gold** is the filed specific predicates.
- **A reader reads the narrative only.** The record goes through the reader's mapping with its category column
  removed and its codes emptied.
- **Its one structured entity** is the drafted pack's `export_scope`, or for a hand pack the entity of section 6.2.
- **A prediction** is the predicates of the affirmed claims, less the other bucket for a drafted pack, or less the
  catch-all for a hand pack.

## 6. Readers and controls

### 6.1 The drafted pack and D002's controls

- **Drafted:** the company's drafted pack.
- **The controls of rule 4:**
  - the majority prior;
  - permuted labels, shuffled by `random.Random("d003:permute:maude:<label>")`;
  - label names only, through the refusal (A13).

### 6.2 The hand-built pack and the comparator mapping

- **The reader is `device_quality`'s vocabulary, unchanged:** its eleven predicates, their English lexicons, its
  negation cues, its negation window of 3 and its extraction settings
  (`mycelic/collective/packs/data/device_quality/vocabulary.json`).
- **Two data copies** of `device_quality`'s files, `docs/collective/onboard/d003-hand/device_quality/` and
  `.../device_quality_own/`. No code is involved, and each differs from `device_quality` only by:
  - **(a) `mapping.json` reads D003's export:**
    - `record_ref` `mdr_report_key`, site `maker_label`, received date `date_received` (`yyyymmdd`);
    - codes from `product_problems[]` through the comparator value map below;
    - narrative `mdr_text`, language null, no persons;
    - the primary entity of (b), read from `maker_label`.
  - **(b) One alias-only entity type is added to `vocabulary.json`,** whose ids are the labels `D1` to `D5`. Every
    record then carries one entity, as the drafted pack's records carry `export_scope`.
    - **Why it is needed.** The lexical extractor attaches a sentence's predicates to an entity named in it, or else
      to the record's primary entity, and otherwise drops them (`LexicalExtractor.extract`).
    - The pack's own id formats resolve few real identifiers (replay 001's `low_resolution`). D003's export holds no
      model or lot number in any case.
    - Without (b) the hand pack would lose its claims for want of an entity. It would then score low for a reason
      that is not its vocabulary.
  - **(c) Only what the loader needs** for (a) and (b) to load, recorded in `BUILD-D003.md`.
  - **Never changed:** a predicate, a lexicon, a negation cue, the window or an extraction setting. A test checks these
    against `device_quality`'s.
- **The comparator mapping, fixed now.** The value map in (a) says which filed problem names each hand predicate
  stands for. The codes channel is emptied, so the map is used only to score.
  - **`device_quality` (decides C2):** the value map of `lab/packs/device_quality_bd/mapping_openfda.json`, 22 names.
    - Six of them are `device_quality`'s own (`mycelic/collective/packs/data/device_quality/mapping_openfda.json`).
    - The other 16 come from replay 002's term rule (`CHOICE-002.md`, "Problem terms"; `tools/market/replay_pack.py`).
      A term maps to a predicate when its words hold, as consecutive whole words, that predicate's id or one of its
      English lexicon entries, and no other predicate's. It takes that predicate's first specific code.
    - 21 of the 22 names map to a specific code. `Adverse Event Without Identified Device or Use Problem` maps to
      `ILL-9002`, which is not specific, so it never enters the matched space.
  - **`device_quality_own` (reported, deciding nothing):** the six names only, five of them specific.
  - **Names match by exact spelling,** as D002's `matched_space` does. Neither map is recomputed from D003's data.
  - **Why the 22-name map decides.** The reader is the same under either map. Replay 002's rule adds only names whose
    wording holds a lexicon entry of exactly one predicate: names the hand vocabulary claims to read. A larger matched
    space gives C2 more records. The six-name result is printed beside it, so a reader can see whether the decision
    rests on the 16 added names.
  - **The 16 added names come from Becton Dickinson's terms** in four product codes in 2021 and 2022. A chosen Becton
    Dickinson name would find the map fitted to its vocabulary. That can only favour the hand pack.
- **The matched space** is D002's rule 2.2 with amendment A7:
  - per company, C is the set of names that are specific predicates of its drafted pack and that the map sends to a
    specific code;
  - scoring is in the hand predicates that C reaches;
  - a record's gold is the hand predicate of each filed name the map sends to a specific code whose predicate C
    reaches;
  - the hand pack predicts its affirmed predicates in that set;
  - the drafted pack predicts the hand predicate of each name it predicts that is in C;
  - C2 uses the pooled sampled records with a gold in that space.

## 7. Metrics

- **As D002:** micro precision, recall and F1, pooled over the companies; macro F1 over (company, predicate) pairs;
  coverage.
- **Intervals:** percentile bootstrap over records, B = 10,000, seed `d003:boot:maude`, the same draws for every
  reader; differences by `stats.paired_bootstrap_f1`.
- **Reported, deciding nothing:**
  - per company, every metric above;
  - the label-in-text share;
  - the echo-free comparison (P6);
  - the drafted packs' sizes, and what the refusal removed (A14);
  - each hand pack in its own space;
  - C2's comparison as non-inferiority (lower end above -0.05);
  - the same comparison in the six-name space;
  - the matched names per company, and the matched records.

## 8. Pass and fail, fixed now

| Id | Criterion | Passes when |
|---|---|---|
| C1 | Reading against D002's controls | The drafted micro F1 minus the best control's micro F1, pooled over the companies, is at least 0.10, and the lower end of the paired 95% interval of that difference is above 0. The best control is the control with the highest micro F1 on the whole sample. D002's C1, unchanged. |
| C2 | Against `device_quality` | In the matched space of section 6.2 (the 22-name map), on at least 100 pooled records, the lower end of the paired 95% interval of drafted minus `device_quality` micro F1 is above 0: superiority. Fewer than 100 matched records fails C2. |
| M1 | No code per field | One code commit runs the arm. In `mycelic/collective/onboard/`, code and data, no identifier, string constant or JSON string equals (case-sensitive) a declared column name of D001, D002 or D003; words the loader reserves are exempt. No drafted category label equals one. The per-field code is the download code of section 12, whose line counts the report gives. |
| M2 | The packs load | `python -m mycelic.collective.packs.loader check` exits 0 on every used company's drafted pack, with at least three companies. |
| M3 | The privacy floor holds | Rule 1.7 passes for every drafted pack with D003's parameters (N = 10, S = 1), and the last guard and the backstop find nothing. |
| M4 | The pilot audit | Does not apply: D003 has one site per company (section 2.5). It is neither computed nor printed as a criterion. |

- **D003 passes only if C1, C2, M1, M2 and M3 all pass.** Each is reported on its own.
- **A criterion that cannot be computed fails.** That includes a company that cannot be drafted, a control or hand
  pack that cannot be built or loaded, and a matched space under 100 records.
- **Why C2 is superiority.** D003 asks whether the drafted pack reads better than the hand-built pack. D002's C2 asked
  whether it read no worse.
- **Why at least 100 records.** An interval on fewer would rest on a handful of predicates. D002's C2 had 1,198
  records.

## 9. How it is read

- **If D003 passes:** on the field closest to the beachhead, five device makers' own filed problems and event
  descriptions were enough to draft, with no code per field, a pack whose lexical reader finds the filed problems in
  later years:
  - better than record-blind and label-blind controls by the margin;
  - better than the hand-built device pack, in the filed names that pack's map says it reads.

  That supports "a new field costs a pack directory", for reading, in medical devices, on public reports. It is not a
  detection claim, and not a site claim.
- **If C1 fails:** the drafted packs load but read these reports no better than the controls by the margin. The claim
  does not hold for reading in this field.
- **If C2 fails,** the report says which way:
  - **on its interval:** the illustrative hand vocabulary reads the filed problems it names as well as the drafted
    pack does;
  - **on its record minimum:** too few of the problems these makers filed often enough to be drafted are names the
    hand pack's map reaches. That says how little the two vocabularies share, and nothing about which reads better.
- **If M1 to M3 fail:** the mechanical claim fails as stated: code per field, a pack that does not load, or a pack
  that leaks.
- **The echo-free comparison** is read beside C1 and C2. A pass whose margin disappears on the records that do not
  repeat their own label says that the drafted pack mostly read the label back.

## 10. What the run prints, and what it never prints

- **Prints:**
  - the fetch's counts (section 2.6);
  - the drafted packs' sizes and hashes;
  - category labels with their corpus counts;
  - for each predicate, its first 10 terms in the lexicon's own order;
  - every metric and interval, and the criteria;
  - the download code's line counts, the code hash and the settings file's sha256.

  The labels are FDA's device problem terms.
- **Never prints or uploads:**
  - a manufacturer's name (companies are d1 to d5 only);
  - a record key or report number, a narrative or any part of one;
  - a patient field, a lot, a UDI, a model or catalog number;
  - an export, a page, a URL, a query, or openFDA's error message.
- **The last guard and the backstop** run as D002's, per company, with `makers[]` among the refused columns
  (section 2.4).

## 11. Changes to the onboard package, and D002 kept reproducible

D003 needs six changes to `mycelic/collective/onboard/`. Each is off unless the settings turn it on, and
`D002-settings.json` turns none on:

- **P1. Several criteria from one arm.** An arm's `criterion` may be a list. Each item is computed as D002 computes
  its one, so `maude` decides both C1 and C2 on one sample.
- **P2. A record minimum for C2.** A C2 item may name `min_records`. Below it, C2 fails with that reason.
- **P3. The criteria the verdict takes.** The settings may list them. D003 lists C1, C2, M1, M2 and M3, so its report
  reads no audit file and prints no audit section. Without a list, the six.
- **P4. Extra non-specific labels.** `params.non_specific_labels` is folded and added to the language file's list,
  for that run only (section 3).
- **P5. The report's source-specific sentences come from the settings when the settings give them.** These are the
  sentence under the criteria table about `pack/`, the roles-right note that names MSHA, "matched names per make",
  and the audit section. D003's sentences say what section 6.2 and section 2.5 say. Without them, D002's text,
  byte for byte.
- **P6. The echo-free comparison** of section 4, reported, deciding nothing. It is printed only when the arm's
  settings ask for it.

**D002 stays reproducible, and a test proves it.** A fixed synthetic input in D002's two layouts goes through split,
score, audit and report with `D002-settings.json`:

- **on the code D002 ran:** commit `9070788`, whose onboard package is unchanged at `d428243`;
- **on D003's code:** the same input and settings.

`arm.json`, `report.json` and `report.md` must be identical on both, except the values that name the code
(`code_commit`, `code_files`, `files`, `code_hash`), the report line that prints them, and the test's own working
paths. The expected bytes are recorded from the D002 code before any onboard change and committed with the test.

Every existing D001 and D002 test passes. A test that changes only because a D003 file now exists is listed in
`BUILD-D003.md` with the reason, and none of its assertions about D001 or D002 is weakened. The language file, the
template and every pinned file stay as they are.

## 12. The settings and the run

- **The settings:** `docs/collective/onboard/D003-settings.json`, committed with the build. A test checks it against
  this file:
  - experiment `D003`, kind `onboard_d001_settings` (the schema D001 defined), choice `CHOICE-D003.md`, language `en`;
  - one arm, `maude`: its six columns and roles (section 2.4), its windows, 200 per company, the company and fetch
    values of sections 2.2 and 2.3, the two criteria, the two hand packs, and the echo-free switch;
  - D002's parameters, except `floor_sites` 1 and the nine non-specific labels;
  - D002's bootstrap values with D003's seed prefixes;
  - the criteria list and the report's sentences (P3, P5);
  - a time limit of 180 minutes.
- **The download code** (M1's per-field code) is `tools/onboard/fetch_openfda.py` and every module it imports beyond
  the standard library and the onboard package, the connector among them. The report gives each file's line count.
- **The workflow:** `.github/workflows/onboard-d003.yml`, started by a push that changes
  `docs/collective/onboard/run-003.json`. It reads that file only and refuses it, before any request, when:
  - its experiment is not `D003`;
  - the settings file's sha256 differs from the one it names;
  - the two name different experiments, or the id is not a plain name.
- **Its steps:**
  1. the offline tests, including section 11's;
  2. the settings check;
  3. the fetch, which selects, fetches and splits (a failure here is not a run);
  4. `=== D003 RUN-START ===`;
  5. score `maude`;
  6. the report;
  7. collect and upload `onboard-D003`: the report, `arm.json` and the drafted packs that passed their check.
     Never the exports or the fetch's working files.
- **`onboard-run.yml` does not change.** A push of `run-003.json` also starts it. Its B1 check then refuses the newest
  run file before any download (`not a D002 run file`), so nothing of D002 runs again.
- **The trigger:** the owner adds `run-003.json` after review:

  ```json
  {"experiment": "D003", "settings": "docs/collective/onboard/D003-settings.json", "settings_sha256": "<64 hex>"}
  ```

- **The first run is the result.**
- **What counts as a run:**
  - The run starts when the fetch has finished: both count lists read, the companies chosen, their exact-name checks
    passed, every day fetched and checked, every export written, and the fetch's counts printed. The workflow then
    prints `=== D003 RUN-START ===`.
  - A failure before that is not a run (section 2.6). The same run file may be pushed again unchanged. openFDA's data
    can change between two attempts, and the run reports the exports it fetched by their sha256.
  - From the marker on, whatever happens is the result, a crash or a timeout included.
- **Changes:** a change before any run is an amendment, recorded in this file as such. If the fetch stops because of
  this rule (for example, the exact-name search does not select what the count counted), the amendment's author will
  have seen the fetch's printed counts and nothing else. Any change after a run has started is a new choice file.

## 13. What D003 does not show

- **The answer key is the filed problem,** not a checked label. Every reader pays for its errors, and the controls
  pay the same.
- **The reader is the lexical extractor,** not a model.
- **These are reports to a regulator,** published by FDA. They are not a company's own complaint file, which holds
  more, coded in the company's own scheme. MARKET 3.5 notes that whether internal codes are coarser is open.
- **There are no sites:**
  - no claim about a company's sites, cross-site counting, detection or early warning;
  - no pilot audit.
  - The public device replays found nothing earlier than chance (MARKET 5.1), and D003 does not revisit that.
- **The sample is days, taken whole:**
  - The reports of one day can be alike, while the intervals resample records, not days or companies.
  - Days too large for the budget are skipped, so very large filing days are under-read.
- **The companies are the five largest names by 2021-2022 reports** among the 100 the API lists. They are not small
  makers, and not MARKET 3.1's 344 groups.
- **`device_quality` is illustrative** (its `pack.json` says so), its vocabulary written for synthetic data. Beating it
  means beating that vocabulary, in the filed names its map reaches.
- **The intervals cover these companies' sampled records only.**

## 14. What these choices risk

- **The fetch:**
  - **The exact-name search is not recorded.** If it does not select what the count counted, the fetch stops before
    the run, and the rule must be amended.
  - **openFDA limits a keyless client per day,** and a shared runner can share that limit. A run of 429s after the
    connector's retries stops the fetch, which is not a run. The same run file may be pushed again.
  - **The count lists hold 100 names,** so a company with enough reports but outside either list is never chosen. If
    fewer than three qualify, the fetch stops.
  - **The walk skips days too large for the budget left.** For a maker that files in large batches, the kept days
    favour quiet ones, and a company can fall under its minimum and be dropped.
- **The privacy floor and the refusal:**
  - **S = 1.** A word from at least 10 reports is printable even if one hospital or one person is named in all of
    them. The 8-token scan and the refusal do not catch a single word. D002's floor asked for 3 sites.
  - **`makers[]` refuses ordinary words** that appear in makers' names (four letters or more) for every company. That
    weakens the drafted, permuted and label-names lexicons alike, and never the hand pack's. A14 counts it.
  - **Brand and product words are not refused.** A printed term can name a company's product and so make dN easy to
    recognise. The choice is also reproducible from openFDA's public counts. No report prints a name.
- **The comparison:**
  - **The matched space can be small.** C2 then fails on its record minimum, and the report says so.
  - **The generic list is the same author's,** written for MARKET 3.5. A generic term missing from it becomes a
    specific predicate.
- **The code:**
  - **P1 to P6 change shared code.** A mistake there could change D002. Section 11's test guards against it.

## Amended before any run, 2026-10-10

No D003 code exists, no D003 run file exists, and no one has fetched any D003 data. A review of this rule found three
gaps that would let a criterion pass for the wrong reason, and nineteen smaller ones. The changes are E1 to E18,
numbered apart from D001's A1 to A14 and D002's B1 and B2 (C and D are taken by the criteria and the experiments).
Each says what was, what is now, and why. Where the text above disagrees with this section, this section wins.

### E1. The hand copies' primary entity resolves exactly (section 2.4; section 6.2 (a) to (c))

- **Was:** `maker_label` held `d1` to `d5`. It was every reader's site, and section 6.2 (a) read the hand copies'
  primary entity from it. Section 6.2 (b) gave the added entity type the ids `D1` to `D5`.
- **Why that fails:**
  - One column cannot hold both values. The loader allows an alias-only id only in upper case (`ALIAS_ONLY_ID_RE`,
    `packs/loader.py`). The connector rejects a site that is not lower case (`SITE_ID_RE`, `packs/connector.py`, as
    `bad_site`).
  - `Canonicaliser.resolve_exact` compares an alias-only id exactly, so `d1` never resolves to `D1`. The record then
    has no primary entity (`codes_channel`).
  - `LexicalExtractor.extract` drops, as `no_entity`, the predicates of every sentence that names none of the pack's
    own entities. The hand pack would read almost nothing, and C2 would pass for that reason alone.
- **Now:**
  - A new column, `maker_entity`, holds the company's label in upper case, `D1` to `D5`, in every row (E5). It has no
    role in the drafted arm, so the drafter never reads it. D002's NHTSA export also carried the hand pack's entity
    in a column of its own (`vehicle`).
  - Both hand copies read their primary entity from `maker_entity`, as an exact id of the added type. Their site is
    `received_day` (E4).
  - **(c) is made exact.** Besides (a) and (b), the hand copies change only what the loader requires before the new
    mapping and the added type will load. For the added type, that is its ids in `generator.json`'s universe, a fill
    rate, and one alias for each id listed there: the loader requires an alias for every alias-only id in the
    universe. The aliases are `d003 maker d1` to `d003 maker d5`, strings that no narrative is expected to hold. A
    mention of one could change which entity a claim names. It cannot add a predicate. `BUILD-D003.md` records every
    such change.
- **A test in the build.** It builds a row in D003's layout: a narrative of one invented sentence holding a term of a
  `device_quality` lexicon, `maker_entity` `D1` and a day in `received_day`. Read through each hand copy by the
  score's own reading function, the row yields that term's predicate. The same row with `maker_entity` `d1` yields
  no primary entity, and the run guard below counts it.
- **A guard in the run** (change P10). For each company, each hand copy and the drafted reader, the run prints the
  rows the reader's mapping rejected, the records left without a primary entity, `structured_unresolved` and the
  `no_entity` drops. It prints counts only. If the deciding copy's mapping rejects any sampled record, or leaves one
  without a primary entity, C2 cannot be computed and fails, and the report says why.

### E2. C2 scores every record it can score, against record-blind controls too (sections 6.2 and 8)

- **Was:** C2 kept only the sampled records whose gold fell in the matched space.
- **Why that fails:**
  - When C reaches a single hand predicate, every kept record is filed under it, and a false positive is impossible.
    Micro F1 is then 2R / (1 + R), where R is recall, so it measures recall only.
  - A reader that predicts the reached predicate on every record scores 1.0.
  - Superiority would then reward the reader with the broader lexicon, not the one that reads better. Small reaches
    are likely: the 22-name map gives `device_quality`'s eleven predicates about two names each, and the review found
    little overlap with the largest codes' most frequent problems ("The declaration, added").
  - No record-blind reader was scored in this space.
- **Now:**
  - **The records scored:** every sampled record of each company whose C is not empty.
  - **The gold:** the record's filed names, by A7's rule, restricted to the predicates C reaches. It may be empty. A
    reached predicate predicted on a record not filed under it is then a false positive, for every reader.
  - **The readers in that space:** the drafted pack and `device_quality`, as before, and two record-blind controls:
    - **all reached:** every predicate C reaches, predicted on every record of the company;
    - **most frequent reached:** the one reached predicate that the most training-corpus records of the company carry
      by A7's rule (ties: the smaller id), predicted on every record of the company.
  - **C2 passes** when the pooled records with a nonempty gold number at least 100 and the lower end of the 95%
    interval (E3) of the drafted micro F1 minus each of these three is above 0: `device_quality`, all reached, and
    most frequent reached. These are three comparisons, all required.
  - **Reported, deciding nothing:** drafted minus `device_quality` as non-inferiority (lower end above -0.05), under a
    label that says it is reported only. Also printed: the same space under the six-name map; the reached hand
    predicates per company, by id; the scored records and those with a nonempty gold.
- **The 100-record minimum** counts records with a nonempty gold, as before.

### E3. The deciding intervals resample received days (section 7; section 8, C1 and C2; sections 9 and 13)

- **Was:** a percentile bootstrap over records decided C1's and C2's lower ends.
- **Why that fails:**
  - Each company's test records come from a few whole days. A large maker keeps about 10 test days under a budget of
    50 pages (MARKET 3.6's 173,163 reports a year for one name is about 474 a day, or 5 pages).
  - Reports filed on the same day share products, problems and templates. A record bootstrap treats them as
    independent and understates the variance, so a lower end can lie above 0 wrongly.
- **Now:**
  - **The clusters:** the (company, received day) pairs of the scored records. The day is the record's
    `date_received`.
  - **The interval:** per reader, a cluster's (tp, fp, fn) is the sum over its records. `stats.paired_bootstrap_f1`
    runs on these cluster triples, ordered by (company label, day), with B = 10,000 and seed `d003:boot:maude`. With
    K clusters, each replicate therefore draws K of them with replacement by `randrange(K)` and keeps every record
    of each drawn cluster. The point difference is the same as over records. `stats` is not changed.
  - **What it decides:** C1's lower end (C1's 0.10 margin stays on the exact difference) and C2's three lower ends
    (E2). Every comparison of one criterion uses the same seed, so the same draws.
  - **Reported beside it:** the record bootstrap of D002 for the same differences, deciding nothing, and the clusters
    per company and in total. Each pooled reader's F1 gets a cluster interval beside its record interval.
    `stats.bootstrap_f1` runs on the same cluster triples.
- **Section 9's claim.** "Finds the filed problems in later years" now reads: finds the filed problems in reports
  received in the two later years, on days drawn at random from them, leaving aside days too large for the budget
  (E11). The deciding intervals resample those days.
- **Section 13.** "The intervals resample records, not days or companies" now reads: the deciding intervals resample
  days, and treat them as independent. Days near each other can still be alike, for example a run of reports about
  one event. The five companies are fixed, not sampled. "The intervals cover these companies' sampled records only"
  now reads "these companies' sampled days only".

### E4. The privacy floor counts received days (sections 2.5, 10 and 14)

- **Was:** S = 1, since a company had one site. A term or a category then needed only 10 reports, which could all come
  from one batch filed on one day, or from one narrative filed in 10 reports. Section 14 named the risk and offered
  nothing against it.
- **Now:**
  - A new column, `received_day`, holds the report's `date_received`, the day the walk kept. It takes the site role in
    the drafted arm, and S is 3 (`floor_sites` 3, D002's value).
  - So a category, a term or a value-map spelling passes the floor only when it appears in at least 10 training
    records received on at least 3 distinct days. One filing day cannot meet that, and neither can one narrative
    filed in one batch.
  - The day is the floor's spread unit and nothing else. It is not a site a company runs. Section 2.5 stands
    otherwise: D003 makes no claim about sites, and M4 does not apply.
- **What follows from the site role:**
  - Rule 1.7, the last guard and the backstop read the day as D002 read a site. An 8-digit day fits the site pattern.
  - The refusal refuses a string equal to a day, or holding one as a whole word. A term is letters only, so this
    can remove no term. A category label that held a day would be refused, and A14 counts it.
  - The backstop scans the report for every kept day as a whole token. So nothing the report prints may hold a date
    written `YYYYMMDD`. `companies.json`, and any `source.json` or `definitions.json` in the exports directory (the
    score copies these into `arm.json`), hold counts, and windows written `YYYY-MM-DD`, only. E11's list of kept days
    goes to the fetch's log, never into the report.
- **Reported, deciding nothing** (P11): per company, how many of the printed terms (the first 10 per predicate), and
  how many lexicon terms, occur in only one distinct folded narrative of the training corpus.
- **Numeric identifiers.** Report keys, lots and UDIs carry digits. Rule 1.5 takes only letters-only tokens as terms,
  so none of them can become a term. That rule keeps them out of the lexicons, not the refusal, since no column holds
  them. Category labels come only from `product_problems`. This holds for sections 2.5 and 10.
- **The declaration** no longer lists "the site count of the floor" among the choices new after D002. S = 3 is
  D002's value, and the received day as its unit is E4's choice.

### E5. The export, as amended (section 2.4)

One JSON-lines file per company, `d<i>.jsonl`, one object per kept report, sorted by `mdr_report_key`. Each object has
these keys, in this order:

| Column | Role in the drafted arm | Value |
|---|---|---|
| `mdr_report_key` | `record_id` | the report's key, as returned |
| `date_received` | `date` | as returned (`YYYYMMDD`) |
| `product_problems[]` | `category` | the report's `product_problems` list as returned; strings only |
| `mdr_text` | `narrative` | the report's description entries, joined (section 4); absent when there is none |
| `received_day` | `site` | the day the walk kept, `YYYYMMDD`, equal to `date_received` (E4) |
| `maker_entity` | none | the company's label in upper case, `D1` to `D5`, in every row (E1) |
| `makers[]` | `forbidden` | the exact names of every company chosen in section 2.2, dropped ones included (E12) |

- **`maker_label` is gone.** Its two jobs moved to `received_day` (site) and `maker_entity` (the hand entity).
- **Each hand copy reads:** record ref `mdr_report_key`, site `received_day`, received date `date_received`
  (`yyyymmdd`), codes from `product_problems[]` through its value map, narrative `mdr_text` (language null, no
  persons), primary entity from `maker_entity`.
- **M1's names** are these seven, with D001's and D002's (P12).
  - Before this commit, the package's own guard (`check.package_hits`) was run on the seven names, and on
    `maker_label`. It found nothing.
  - None of them is a word the loader reserves.
- **`date_received` and `received_day` hold the same value.** The onboard code gives a column at most one role, so the
  date and the floor's unit take two columns.
- Section 2.4's other rules stand: nothing else is written, and `companies.json` holds no name.

### E6. Two more controls in C1 (section 6.1; section 8, C1)

- **Added controls**, each read like the others (the drafted pack's mapping, its other bucket dropped):
  - **Label words.** Each specific predicate's lexicon is the words (runs of letters and digits) of its folded label
    that are letters only, have at least 4 letters, and are neither a stop word nor a negation word of the language
    file. A word the refusal's term rule refuses is left out (A13).
    - A word several labels share goes to the predicate with the most corpus rows (ties: the smaller id), as in the
      label-names control.
    - A predicate left with no word gets a placeholder.
  - **Set prior.** Take the company's training-corpus records that carry at least one specific predicate. The set of
    specific predicates most of them carry is predicted for every sampled record. Ties go to the smaller set, then to
    the set whose sorted ids come first as text.
- **C1's best control** is now the one with the highest micro F1 among five: majority prior, set prior, permuted
  labels, label names and label words. C1's margin (0.10) and its other condition stay as they were, the latter
  decided by E3's interval.
- **Why label words.** FDA's problem terms are long phrases, and `label_parts` splits a label only at `/`, `,`, `;`,
  brackets and the joining words. A narrative rarely repeats a whole phrase, so the label-names control is weaker
  here than on NHTSA's short component names, where it reached 0.405 in D002. A narrative that says "leak" for a
  code ending in "Leak" is what the word-level control reads.
- **Why the set prior.** The gold holds every filed specific problem, and micro F1 counts (record, predicate) pairs.
  When reports often carry two common codes, a record-blind set beats the single-label prior. "Better than
  record-blind controls" should mean the stronger of the two.

### E7. Reported measures of label echo and templates (sections 4, 5 and 7; P6, P11)

All of these are reported and decide nothing:

- **The word-level echo share.** Per company and pooled: the share of sampled records whose folded narrative holds,
  word-bounded, a word of one of its own filed specific labels. A word is taken as E6 takes label words.
- **The echo-free comparison (P6)** under both definitions, the whole label (section 4) and the word level. C1's
  comparison and C2's comparisons are repeated on the records that hold no echo.
- **Digit-masked repeats.**
  - The narrative is folded and every run of digits is replaced by `0`.
  - Per company and pooled, the run reports the share of sampled records whose masked narrative equals that of a
    training-corpus record, or that of another test-window record of the same company.
  - C1's and C2's comparisons are repeated on the sampled records that have no such repeat, with E3's intervals.
- **Rule 3 is not changed** (the reviewer's optional proposal is declined).
  - Masked dedup would change which records are eligible, by a measure no recorded fact sizes.
  - Templated narratives are part of what a company's own file holds.
  - Reporting both results shows whether template repeats carry the result.
- **How it reads** (section 9's last bullet, extended): the C1 or C2 margin may vanish on the records with no echo
  under either definition, or on the records that survive the masked dedup. Such a pass says that the drafted pack
  mostly read the label back, or recognised the company's templates.

### E8. When C2 is not decided (sections 8 and 9)

- **C2 fails without deciding** in two cases: it has fewer than 100 records with a nonempty gold, or E1's guard fails.
  The report then prints C2 as "not decided", with the reason and the count, beside its failure.
- **The verdict is still "fail".** D003 passes only if C1, C2, M1, M2 and M3 all pass.
- **How it reads.** "C1 and M1 to M3 pass; C2 not decided" means that the drafted packs read these reports better than
  the controls, and that D003 says nothing about the hand pack. D003 can therefore fail with no result against the
  hand pack at all.
- **Why.** The 16 added names come from Becton Dickinson's terms in four product codes. The chosen companies are the
  largest names, and the review counted little overlap between the 22 names and the largest codes' most frequent
  problems (see "The declaration, added"). A shortfall says how little the two vocabularies share, which section 9
  already says decides nothing about reading.

### E9. HTTP attempts, counted and capped (section 2.3, "The request budget"; section 2.6)

- **Was:** the fetch counted its calls of `_get` and stopped before the 701st.
- **Why that fails:**
  - `_get` retries inside each call: up to 6 times on 429 and 5xx, and twice on a network error. One call can
    therefore make up to 9 HTTP attempts.
  - So counting calls bounds nothing that reaches openFDA. The "388 for retries" was not enforced.
  - The walk's design maximum is 612 calls, so a stop at 701 could never fire, and 700 had no source.
- **Now:**
  - **The wrapper.** The fetch passes `_get` a wrapper around the connector's `build_opener()`. Its `open()` counts
    every HTTP attempt, retries included, before it delegates.
  - **The cap.** Before the 801st attempt, the wrapper raises the fetch's own exception. That exception is not an
    `OSError` or an `http.client.HTTPException`, so `_get` does not retry it.
  - **Why 800:** it is the lab's keyless cap (`lab/request.py`, `OPENFDA_CAP`), below openFDA's 1,000 requests a day
    for a client without a key. The connector is not modified.
- **The bound on calls:** 2 count requests, then per company 2 exact-name checks, at most 2 repeats of them (E10) and
  at most 70 + 50 page requests. That is at most 2 + 5 x (2 + 2 + 70 + 50) = 622 calls of `_get`, which leaves at
  least 178 attempts for retries.
- **The fetch prints** its attempts and its calls.
- **Not a run:** reaching the cap ends the attempt before the run starts. So does a run of 429s that outlasts the
  connector's retries.
- **No pacing.** No per-minute limit is recorded, so the fetch adds none beyond the connector's back-off.

### E10. The exact-name check, incomplete days and the order of pages (section 2.3; sections 2.6 and 14)

- **The exact-name check tolerates no difference.**
  - When a total differs from the count, the search is made once more.
  - If it still differs, the company is not used. It is dropped before its days are walked and counted as
    `exact_check_failed`. It is not replaced, and its name stays in `makers[]`.
  - If fewer than three companies remain, the fetch stops. That is not a run, and the author of any amendment that
    follows will have seen the fetch's counts and nothing else.
- **An incomplete day is fetched once more, then left out.** Step 4 fails when any of these holds:
  - the day's pages give a number of distinct `mdr_report_key` values other than `T`;
  - a key appears twice;
  - a record was received on another day;
  - a record names the company in no device entry.

  Such a day is fetched once more, all its pages, if the budget left allows. If the second fetch passes, the day is
  kept with that fetch's records. Otherwise the day is not kept, and it is counted as `incomplete` with its reason.
  A refetch takes its pages from the window's budget. Step 4 no longer stops the fetch.
- **Paging order.** Section 2.3 said that "a result set fetched whole does not depend on that order". That is false
  for a day of more than 100 reports.
  - Its pages are separate requests (`skip` 0, 100, 200 and so on). If openFDA's unsorted order changes between them,
    the pages overlap or miss records.
  - No recorded fact says keyless paging is stable. Replays 001 and 002 recorded totals only.
  - The fetch now prints, per label and window, the duplicate keys and the missing keys (`T` minus the distinct keys),
    summed over first fetches and over refetches, and the days refetched and incomplete. All are counts.
- **Section 14 adds:**
  - If paging is unstable, multi-page days are lost. The largest makers' days are mostly multi-page, so they can fall
    under their minimum. With fewer than three companies left, the fetch stops, and that is not a run.
  - The counts show which happened. Duplicates and misses point to unstable paging. A failed exact-name check, or
    records naming no device entry of the company, point to the `.exact` search.

### E11. The walk: a cap per day, a minimum of kept days, and the list of kept days (section 2.3)

- **A day is too large** when its `P` exceeds a fifth of the window's budget: 14 pages in training (1,400 reports) and
  10 in test (1,000). It is also too large, as before, when its `P - 1` further pages exceed the budget left. Its
  first page is discarded unread, as before. No page is deeper than `skip` 1,300, below the 9,200 recorded.
- **Minimum kept days.** A company needs at least 5 kept days in each window. This is checked with the report
  minimums. A company below it is dropped before the run starts and is not replaced.
- **The fetch prints the kept days** per label and window: each kept day as its date and its total `T`, sorted by
  date, then the sha256 of that list. The list is written one line per day, `YYYYMMDD T`, joined by `\n`, in UTF-8.
  - These dates are the walk's own days, which every kept record equals by step 4. The totals are search totals.
  - No other date, and no key, is printed.
  - A later fetch can then show whether openFDA's data changed, or the code did.
- **The number of kept days** per company goes into `companies.json`, and the report prints it beside C1 and C2. The
  dates never go into the report (E4).
- **Why.** Otherwise one very large day could take a whole window, and templated narratives from one batch could fill
  the corpus and the sample. The fifth and the five days are choices made without data. Five days keep a window from
  resting on one or two days, and give the cluster bootstrap several days per company. A fifth of the budget lets
  five days fit when enough days are small enough.

### E12. Selection, dates and minimums, made exact (sections 2.2 and 2.3; the question; section 9)

- **The order of selection.**
  - The candidates are taken in "The order": most training-window reports first, ties by the name's UTF-8 bytes.
  - A candidate is chosen when it passes exclusions 1 to 4.
  - Exclusion 3 compares it with the names already chosen, which passed 1 to 4 earlier in that order.
  - Choosing stops at five.
- **The fold of exclusion 1** is `packs.canonical.folded`.
- **"Another manufacturer"** is a device entry whose `manufacturer_d_name` is present, not blank after stripping, and
  not the company's exact name.
  - A blank or absent entry is not another manufacturer.
  - A variant spelling is, including one that exclusion 3 removed.
- **`makers[]`** holds every name chosen under section 2.2, including companies dropped later: by E10's check, by the
  report minimums or by E11's day minimum.
- **Both minimums count reports with a narrative,** meaning a "Description of Event or Problem" entry (section 4):
  1,000 kept training reports and 500 kept test reports.
- **Dates in a search** are written `YYYYMMDD`, as the connector writes them (`page_url`, `DATE_RE`):
  - a window is `date_received:[20210101 TO 20221231]` or `date_received:[20230101 TO 20241231]`;
  - a day is `date_received:[<day> TO <day>]`.
- **What is fetched** is every report that names the company in a device entry. That includes voluntary reports and
  user-facility reports, since no report-source field is fetched.
  - The question now reads: "a pack drafted ... from the FDA adverse-event reports that name one maker's device ...
    read that maker's later such reports".
  - Section 9's "five device makers' own filed problems" now reads "the problems filed in reports that name five
    device makers' devices".

### E13. What the fetch prints, when it stops, and how it prints an error (section 2.6)

- **It prints counts only:**
  - the HTTP attempts and the `_get` calls (E9);
  - for each count list, how many names it returned; the candidates in both lists, the exclusions by reason (1 to
    4), and how many qualify;
  - per label: its training and test counts from the lists, and whether its exact-name check passed, passed on its
    repeat, or failed;
  - per label and window:
    - the days walked, kept, too large (over the per-day cap or over the budget left), empty, refetched and
      incomplete (by reason);
    - the duplicate and missing keys (E10) and the pages fetched;
    - the reports kept, those with a narrative, and those dropped for naming another manufacturer;
    - the kept days with their totals, and that list's sha256 (E11);
  - per label: its export's bytes and sha256, whether the company is used, and if not, why: the exact-name check,
    the report minimums or the day minimum.
- **It never prints:**
  - a name, a record key, a URL or a query;
  - any value of a record other than the kept days;
  - openFDA's error message;
  - an exception's text, or a traceback.
- **How it prints an error.** The fetch catches every exception at its top level. It prints the exception's class
  name only, plus, for an HTTP error, the status and openFDA's `error.code`, and exits nonzero.
  - `fetch_nhtsa.py` printed `{err}`. An exception's text can carry a record value, such as a key that `int()` could
    not parse.
  - **A test in the build** serves the fetch a malformed record through the local server: a key that is not a
    number, a date that does not parse, a problem that is not a string, each a distinctive string. It checks that
    none of those strings reaches stdout or stderr.
- **It stops, before the run starts and so not as a run,** when:
  - an HTTP error remains after the connector's retries, or the host cannot be reached;
  - openFDA answers 400 for a skip;
  - the 801st HTTP attempt would be needed (E9);
  - fewer than three companies qualify, or fewer than three remain;
  - any other exception is raised.
- **Removed from the stops:**
  - a window total that differs from its count (now E10's drop);
  - a day whose pages do not give its total (now E10's refetch);
  - a record that names neither its company nor its day (now an incomplete day);
  - the 701st request (now E9's cap).

### E14. The workflow can be started again (section 12)

- **Was:** `onboard-d003.yml` started only on a push that changes `run-003.json`. A failed fetch is not a run, and the
  rule said the run file could be pushed again unchanged. But a push filtered on paths does not fire for an unchanged
  file, and the rule named no other way to start again.
- **Now:**
  - `onboard-d003.yml` has `workflow_dispatch` beside its push trigger, as `onboard-run.yml` has. The owner may also
    use Actions' "Re-run".
  - Every start reads `run-003.json` and applies the same refusals before any request.
  - **The result is the first attempt, of any start, that prints `=== D003 RUN-START ===`.** A later attempt is
    recorded under "Runs" and decides nothing.

### E15. FDA's generic terms and the n-gram scans (section 3; section 8, M3; section 14)

- **The risk.** Rule 1.7's text check reads every string of the pack, the value-map spellings of the other bucket
  included. It compares their 8-token runs with every narrative of the export.
  - `adverse event without identified device or use problem` has exactly 8 tokens.
  - `unknown (for use when the device problem is not known)` has 10.
  - One narrative that quotes either fails M3 for its company, and so D003. Yet nothing leaks: these are FDA's public
    terms, and section 4 expects descriptions to repeat coded wording.
- **Now (P13):**
  - A string whose folded form equals one of the nine non-specific labels (section 3, `params.non_specific_labels`)
    is left out of rule 1.7's n-gram check and of the last guard's n-gram scan.
  - Those nine are constants of this rule, not values of records.
  - Every other check of rule 1.7 and of the last guard still applies to them, the refusal and the floor among
    them.
- **Reported:** the check and the last guard count, as counts only, the n-gram hits that lie wholly within a value-map
  spelling. A failure on a long specific FDA term can then be told apart from a fragment of a narrative. Such a
  failure still fails M3.
- **Section 14 adds:** a specific FDA term of 8 tokens or more that a narrative quotes fails M3 for that company, and
  the counts show it.

### E16. Numbers and their basis (sections 2.2, 2.3 and the declaration)

- **120 characters** (exclusion 2): a choice made without data. It bounds a name the search writes into a URL.
- **2,000 and 1,000** (exclusion 4): choices made without data, twice the per-company minimums, so that a company has
  room to reach them under the walk.
- **1,000 and 500** (the minimums): choices made without data. No eligible share of test reports is recorded, so
  "leaves room for the 200 drawn records" is a hope, not a derivation. When fewer than 200 are eligible, all are drawn
  (rule 3).
- **700** is removed (E9). The cap is 800 attempts, the lab's.
- **A fifth of the budget and five days** (E11): choices made without data.
- **34.4%** is the share of the 2023-2024 reports under the listed Becton Dickinson names, in four product codes (JKA,
  FOZ, FMI and MDB), that carried a problem code replay 002's pack mapped (`docs/collective/replay/CHOICE-002.md`,
  the result). It is not a share of all reports.
- **The declaration's sentence** "Each is justified below from a recorded fact or file" now reads: each is justified
  from a recorded fact or file, or labelled as a choice made without data.

### E17. The declaration, qualified (opening paragraph; "The declaration")

- **The opening sentence** "No one has seen any value of a D003 company's reports: no narrative, no problem-code
  distribution, no per-manufacturer category count" now reads as follows.
  - No one has seen any narrative of a device report.
  - For this rule, no one has read a problem-code distribution or a category count of any manufacturer.
  - The repository does hold some that may in effect cover a chosen company, and the author has seen some of them.
    They are listed below.
- **"So D003 is not blind," added:**
  - `field-coverage.json` lists, for each of the ten largest product codes of 2024 (a test-window year), up to eight
    coded problems with their counts in a 200-report sample. Where one maker files most of a code, that sample is in
    effect the maker's test-year distribution, and D003 picks the largest names.
  - `CHOICE-002.md`'s result records that 34.4% of the 2023-2024 reports under the listed Becton Dickinson names in
    four codes carried a mapped problem code (E16). That is a per-manufacturer fact in D003's test years.
- **"What is new and chosen after D002's run,"** as amended:
  - C2 as superiority with a 100-record minimum, and E2's two record-blind controls;
  - the generic problem terms;
  - the received day as the floor's unit (E4);
  - E6's two controls and E3's cluster intervals;
  - E11's per-day cap and minimum of kept days;
  - E9's cap of 800 attempts.

### E18. The changes to the onboard package, as amended (section 11)

Section 11 said D003 needs six changes. The rule as written already needed more, and E1 to E15 add others. The list,
each item off unless D003's settings turn it on:

- **P1.** Several criteria from one arm, as before.
- **P2.** A record minimum for C2, counted on records with a nonempty gold (E2).
- **P3.** The criteria the verdict takes, as before.
  - The report CLI's `--audit` becomes optional. It is needed only when the list holds M4, or when there is no list.
  - D002, with no list, still passes it as before.
- **P4.** Extra non-specific labels, as before.
- **P5.** The report's source-specific sentences come from the settings when the settings give them. Added to the
  list:
  - the verdict sentence ("{exp} passes only if all six criteria pass");
  - C2's comparison labels, including the reported-only non-inferiority line;
  - E8's "not decided" line;
  - the sentence under the criteria table about `pack/`, which was already listed.

  Without them, D002's text, byte for byte.
- **P6.** The echo-free comparison, under both definitions, for C1's and C2's comparisons (E7).
- **P7.** C2 as E2 has it:
  - the records scored, and the gold restricted to the reach;
  - the two record-blind controls;
  - the reached predicates printed per company;
  - the decision by superiority over the three readers.

  `score.criterion` today passes C2 on `ci_low > -margin` and prints the superiority comparison as "decides nothing".
  P7's switch reverses the two for D003.
- **P8.** E3's cluster intervals for the deciding differences, with the record intervals beside them.
- **P9.** E6's two C1 controls.
- **P10.** E1's guard on the readers' rejected rows, unresolved primary entities, `structured_unresolved` and
  `no_entity` drops, and C2's failure when the deciding copy does not resolve.
- **P11.** E4's and E7's reported measures, and the kept days per company (E11).
- **P12.** M1's names.
  - The settings may list earlier settings files: D003's lists `D001-settings.json` and `D002-settings.json`.
  - Their column names join the run's own in M1's package check, in M1's label check, and in rule 1.7's label check
    (`check.column_names`).
  - Today `report.criteria` takes names only from the run's own settings.
- **P13.** E15's exemption, and its count of hits within value-map spellings.

Section 11's test stands and covers every item: D002's settings on a fixed synthetic input give byte-identical
`arm.json`, `report.json` and `report.md` on the code D002 ran and on D003's code.

### The settings, as amended (section 12)

`D003-settings.json` differs from section 12's description as follows. The key names are the build's, and the
settings test checks every value against this file.

- **Columns and roles:** E5's seven columns. Record id `mdr_report_key`, date `date_received`, category
  `product_problems[]`, narrative `mdr_text`, site `received_day`, no entity column, no reporter, forbidden
  `makers[]`.
- **Parameters:** D002's, unchanged (`floor_sites` 3, E4), plus the nine non-specific labels.
- **C1:** margin 0.10; the five controls (E6); the cluster interval (E3).
- **C2:** against `device_quality`, decided by superiority over it and over both record-blind controls (E2); 100
  records with a nonempty gold; the cluster interval; the -0.05 comparison reported only.
- **M1:** the earlier settings files (P12).
- **The fetch:**
  - budgets of 70 and 50 pages, with per-day caps of 14 and 10 pages;
  - minimums of 5 kept days, and of 1,000 and 500 reports with a narrative;
  - the cap of 800 HTTP attempts;
  - one repeat of each exact-name check, and one refetch of an incomplete day.
- **The reported switches:**
  - the echo-free comparison, under both definitions;
  - digit-masked repeats;
  - terms resting on one narrative;
  - the reader guard, which also decides C2 (E1).
- **The rest as section 12 says:** the criteria list and the report's sentences (P3, P5), and a time limit of 180
  minutes.

### What these changes risk

- **The cluster intervals are wider.** With about 10 test days for a large company, and five companies, a few dozen
  clusters decide. C1 or C2 can fail on width alone, while their point differences look large.
- **The floor by days** removes categories and terms that a company files on fewer than three of its kept training
  days. A company with few kept days can lose most of its categories.
- **The added controls can only raise C1's bar.**
  - A label word shared by many labels ("device", "problem") goes to the most frequent predicate and fires on many
    narratives, so the label-words control can act partly as a prior.
  - Under the word-level definition, the echo-free subset can be small, since such words are common in narratives.
    It is reported only.
- **C2 is harder to pass.** False positives now count, and two record-blind controls must be beaten. A small reach
  can leave C2 undecided (E8).
- **The per-day cap** leaves out days of more than 1,400 training or 1,000 test reports. A maker that files mostly in
  large batches can fall under its day minimum and be dropped.
- **A successful refetch** can hide unstable paging on that day. The duplicate and missing counts still show it.
- **E10's drop** can leave fewer than five companies. With fewer than three, the fetch stops.
- **The day as the floor's unit** puts every kept day into the backstop. A report that printed a date written
  `YYYYMMDD` would be withheld. E4 keeps such dates out of everything the report reads.

### The declaration, added

- This amendment was written by the same AI system, after a review of this rule and before any of D003's code.
- **What it read for the amendment:**
  - the onboard package, `D002-settings.json` and `CHOICE-D002.md`;
  - the pack loader, the canonicaliser, the pack connector and the lexical extractor;
  - `device_quality`'s mapping, entity types, predicate ids, aliases and generator universe, and the vehicle hand
    pack's mapping and entity type;
  - `stats`, `lab/request.py` and the openFDA connector;
  - replay 002's result paragraph (the 34.4% line and its window);
  - `onboard-run.yml`'s triggers and `fetch_nhtsa.py`'s error line.
- **What it learned of the data from the review:**
  - The top eight coded problems of each of the four largest 2024 product codes (DZE, QBJ, QFG and OZP) hold at most
    one specific name of the 22-name map. The review counted this from `field-coverage.json` and gave only the
    overlap.
  - The review cited MARKET 3.6's 2024 report counts for Abbott Diabetes Care Inc and for the largest Becton
    Dickinson name.
- **What it did not open:** while writing this amendment it opened none of `field-coverage.json`, `site-split.json`,
  `inputs-001.json`, `inputs-002.json` and MARKET 3.5 or 3.6. It saw no narrative, no problem-code distribution and
  no category count by manufacturer.
- **What that knowledge could shape:** E2's controls and E8's reading of an undecided C2, which were written because
  small reaches are likely. Neither depends on which names overlap.
- **What it ran:** `check.package_hits` on the new column names, as recorded in E5. No request was made to any host.

## Runs

### Run 1: run-003, 2026-10-10 (the result): failed, on C2

[Run 38055858262](https://github.com/anovruzov/NeuralGraph/actions/runs/38055858262), run file
`docs/collective/onboard/run-003.json`, commit `85709ad`, settings sha256 `9b06b12c9697…`, 13:28 to 13:45 UTC.
- **Steps:** offline tests 2 min 2 s; the settings check; the fetch, select and split 9 min 31 s; `RUN-START` at
  13:40:25; score `maude` 5 min 4 s; the report 3 s.
- **The report step exited 1** after printing its blocks, as it does on a fail verdict. By section 12, from the marker
  on, whatever happens is the result.
- **The record:** `report.md` sha256 `9b8a60ccc753…` (printed between the D003 markers, checked against its 241
  lines). The log tool returned only the last 5,000 of the job's 7,432 log lines, so the fetch's printed counts and the
  start of `report.json`'s block were cut and are not quoted here. Every number below is from `report.md`.
- **The D002 workflow,** started by the same push (run 38055858248), refused `run-003.json` at its settings check,
  before any download, as section 12 says it must.

**Verdict: fail.** D003 passes only if C1, C2, M1, M2 and M3 all pass. C2 did not.

| Criterion | Result |
|---|---|
| C1 (reading against D002's controls) | **Passed.** Drafted micro F1 0.590 against the best control, the majority prior, 0.346, on 820 sampled records of 5 makers. Difference 0.244, 95% interval over (company, received day) clusters [0.164, 0.317], 42 clusters: at least 0.10, lower end above 0 |
| C2 (against `device_quality`, 22-name map) | **Failed, on its interval.** 114 records with a nonempty gold (at least 100 needed). Drafted 0.398, `device_quality` 0.225, all reached 0.228, most frequent reached 0.210. Drafted minus `device_quality` 0.173 [-0.009, 0.349], minus all reached 0.171 [-0.025, 0.386], minus most frequent reached 0.188 [0.002, 0.398]. Superiority needs every lower end above 0; two are not |
| M1 | Passed: no declared column name in the onboard package, no drafted label equal to one, one code commit. The download code is 2,124 lines in five files |
| M2 | Passed: 5 packs drafted, 5 loaded |
| M3 | Passed: 5 packs passed the privacy floor, and the last guard was clean. FDA's generic terms were left out of the n-gram scans, as P13 says; no n-gram hit fell inside a value-map spelling or a printed label |
| M4 | Does not apply (section 2.5) |

All readers, pooled (micro F1; 95% interval over clusters):

| Reader | Micro F1 [95%, days] | Macro F1 | Coverage |
|---|---|---|---|
| drafted | 0.590 [0.473, 0.684] | 0.369 | 0.879 |
| majority prior | 0.346 [0.251, 0.442] | 0.051 | 1.000 |
| set prior | 0.305 [0.199, 0.415] | 0.045 | 1.000 |
| permuted labels | 0.235 [0.099, 0.375] | 0.025 | 0.306 |
| label words | 0.204 [0.163, 0.249] | 0.176 | 0.799 |
| label names | 0.141 [0.072, 0.224] | 0.091 | 0.161 |

- **Companies:** 77 names were in both count lists; 1 was excluded as a placeholder, and 76 qualified. 5 were
  chosen and used, d1 to d5. Kept days, training and test: d1 20/18, d2 9/7, d3 11/6, d4 9/6, d5 15/10.
- **Per company,** drafted F1 against the best control:
  - d1 0.132 / 0.149: below its control. 10 of d1's 21 predicates learned no term, and its "Crack" terms are words
    like `customer` and `data`.
  - d2 0.726 / 0.475; d3 0.654 / 0.163; d5 0.852 / 0.685.
  - d4 0.857 / 0.600 on only 20 drawn records.
- **Label echo:** 8.5% of the sampled narratives hold their own filed label, and 27.6% hold a word of it. On the 594
  records with no word echo (reported only, deciding nothing), drafted minus the majority prior is still 0.158
  [0.074, 0.231].
- **The six-name map** of `device_quality` (reported only): drafted 0.454 against 0.212 on 73 records with a gold;
  difference 0.242 [0.009, 0.460].
- **Non-inferiority** against `device_quality` at -0.05 (reported only): the lower end -0.009 is above -0.05.
- **The learned terms read as device problems,** for example:
  - `signal loss`, `transmitter failed` and `early sensor` for a continuous glucose monitor's wireless, output and
    end-of-life problems;
  - `insulin flow` and `flow block` for obstruction of flow;
  - `osseointegrate`, `primary stability` and `sinus perforation` for a dental implant's failure to osseointegrate.
- **The refusal** removed no category and 7 floor-passing terms, 4 of which would have been assigned.

**How it is read (section 9).**
- C1 passed. On the field closest to the beachhead, packs drafted from five device makers' own reports, with no code
  per field, read those makers' later reports better than every record-blind and label-blind control, by 0.244 F1.
  This is D002's C1 holding in a third field. On word-echo-free records the margin shrinks to 0.158, but it stays
  above 0.
- C2 failed on its interval. Section 9's reading, fixed before the run, is that the illustrative hand vocabulary reads
  the filed problems it names as well as the drafted pack does. The point estimates favoured the drafted pack: 0.398
  against 0.225. But with 114 records in 42 day-clusters, the interval did not exclude 0, so superiority was not shown.
- D003 fails. The claim it tested, a better reader than the hand-built device pack, is not supported by this run.

**What it does not show.** The answer key is FDA's filed problem codes, not checked labels. The companies are fixed
and the days are resampled, so no interval covers the field in general. The reader is the lexical extractor, not a
model. Nothing here measures detection or early warning. No field is a site a company runs, so nothing here is about
sites.
