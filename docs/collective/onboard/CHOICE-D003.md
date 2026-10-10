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

## Runs

None yet.
