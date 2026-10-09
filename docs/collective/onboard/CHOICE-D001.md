# Drafting test D001: can a pack be drafted from an export alone?

The product says a new field costs a pack directory, not code (`docs/strategy/YC-BRIEF.md`, `docs/collective/PACKS.md`).
Today a pack is written by hand. The IT pack took 13 data files (`docs/collective/x3/effort.json`, `totals`), and
`docs/collective/PILOT.md` says that mapping a partner's columns is the pilot's first week.

D001 tests a generic drafter. It turns one export (CSV or pipe- or tab-delimited text with a header, or JSON lines) into
a pack that loads and runs through the existing pipeline: the loader's check, the lexical extractor and the pilot
audit. It learns the pack's vocabulary from the export's own filed categories and narratives. It has no code per
field. D001 measures what the drafted pack reads, on real public records:

- **MSHA mine accidents**, a field no pack covers;
- **NHTSA vehicle complaints**, where two hand-built packs exist.

This rule is committed before any of the drafter's code exists and before any MSHA record value is seen. How it is
built is in `BUILD-D001.md`. The sources are in `SOURCES.md`.

**Amended before any run, on 2026-10-09.** The section of that name, just before "Runs", changes rules 1.3 to 1.7,
2.2, 4, 5, 7, 8 and M1, and adds to the declaration. Where it disagrees with the text above it, it wins.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- **MSHA:** it has seen only the probe record (`SOURCES.md`): the file's size, its row count and its 57 field names. It
  has seen no value of any field, no category distribution and no narrative.
- **What MSHA's two category columns mean** (section 2.1) is the author's reading of MSHA's published field
  definitions, from general knowledge, not from this file. The run prints the definition file's lines for the declared
  columns, so a reviewer can check that reading. The choice stands whatever they say.
- **NHTSA is not blind.** The author has seen NHTSA's 40 largest component categories with their counts
  (`docs/collective/replay/vehicles/nhtsa-probe.json`), both hand-built vehicle packs, and the results of V001 to
  V003, R001 and R002. On R001's 150 complaints, the hand pack's lexical reader scored predicate F1 0.434. Small
  models scored 0.000 in R001, a post-processing fault, and 0.026 to 0.218 in R002
  (`docs/collective/replay/vehicles/CHOICE-R001.md`, `CHOICE-R002.md`). It has also read J001's design and its
  amendment.
- **The language file's word lists** (stop words, negation cues, header words, non-specific labels; section 1) were
  written by an author who knows both sources' headers and knows that NHTSA files an "UNKNOWN OR OTHER" category.
- **No model is used.** Every reader is the pinned lexical extractor (`mycelic/collective/edge/extract.py`) with a
  different pack. The lab's models (a-0p5b, a-1p5b, a-4b) are not needed.
- **The answer key is the category each record was filed under,** not a label anyone checked. A narrative can
  describe a category it was not filed under, and the reverse. No reader can reach F1 1.0.

## 1. The drafter

The drafter is generic code in `mycelic/collective/onboard/`. Its output is determined by the export, the roles file
(data), the parameters file (data) and the neutral template (data). It reads no clock and no unseeded randomness.
Every list it writes is sorted by a fixed key.

### 1.1 Reading an export

- **Format.** JSON lines when the first non-empty line starts with `{`. Otherwise delimited text with a header line.
  The delimiter is whichever of `|`, tab and `,` occurs most often in the header line; ties go in that order.
- **Splitting.** Pipe and tab: one row per line, split on the delimiter, no quoting. Comma: Python's `csv` module with
  its default quoting. A row with a different number of fields than the header is rejected and counted.
- **Lists.** A header ending in `[]` is a list column. Its cells are split at `;`.
- **Text.** Bytes are decoded as UTF-8, a leading byte-order mark dropped. If that fails, the whole file is decoded as
  Latin-1. Every value is stripped. An empty value is absent.

### 1.2 Column roles

Each column takes at most one role:

| Role | Count | What the drafter does with it |
|---|---|---|
| `record_id` | one | the record's reference; never enters a pack file |
| `site` | one | counts distinct sites for the privacy floor; never enters a pack file |
| `date` | one | splits training from test rows; never enters a pack file |
| `narrative` | one | the only text the drafter learns terms from |
| `category` | one | the filed category: the predicates and the answer key |
| `entity` | zero or more | becomes an alias-only entity type (section 1.6); D001 declares none |
| `reporter` | zero or one | becomes the mapping's reporter; D001 declares none |
| `forbidden` | any | person, location and free-identifier columns: their values are used only to refuse terms |

Every other column is ignored: the drafter never reads it.

**Inference without hints.** The drafter can also guess the five roles `record_id`, `site`, `date`, `narrative` and
`category` from the rows alone. Its evidence per column is counted over the training rows (picked by the declared date
column; with no roles declared, over every row):

- fill: the share of rows with a value;
- distinct: the number of distinct values;
- mean tokens: the mean number of `\w+` runs per value;
- date share: the share of values that parse in one date format of section 1.3;
- letter share: the share of values with at least one letter;
- header words: the header split at every character that is not a letter or digit, lower-cased.

The rules, applied in this order, each to the columns no earlier rule took:

1. **narrative:** the column with the largest mean tokens, if that mean is at least 8 and its fill at least 0.5.
2. **date:** among columns whose date share is at least 0.95, the one with the highest fill; ties go to the leftmost.
3. **record_id:** the leftmost column with fill at least 0.99 whose values are all distinct.
4. **site:** among columns with fill at least 0.99 and at least 2 distinct values whose header words include a site
   word of the language file, the one with the most distinct values; ties go to the leftmost. If none has a site word,
   the column with fill at least 0.99, mean tokens at most 2 and the most distinct values below the row count.
5. **category:** the label-like columns are those with fill at least 0.9, letter share at least 0.9, 2 to 200
   distinct values and mean tokens at most 8. The leftmost label-like column whose header words include a category
   word of the language file; if none has one, the label-like column with the most distinct values.

**The scored run uses the declared roles** (section 2), so a wrong guess cannot change any reading result. The report
gives, for each export, how many of the five declared roles the inference got right. The author wrote these rules
knowing both sources' headers, so that count says little about other exports.

### 1.3 Dates and the training rows

- The part of a date value before its first space or `T` is parsed. The formats, tried in this order: `YYYY-MM-DD`,
  `YYYYMMDD`, `YYYY/MM/DD`, `M/D/YYYY`, `M/D/YY`, `D.M.YYYY`, `D-MON-YYYY`, `D-MON-YY` (`MON` an English month
  abbreviation from the language file, any case).
- A two-digit year `YY` is `20YY` when `YY` is below 70, else `19YY`.
- The date column's format is the first format that parses at least 0.95 of its values over the whole export. The
  date is the one field read in every row, to find the window. If no format reaches 0.95, the draft fails with an
  error. A row whose date does not parse in that format is rejected and counted.
- **The drafter reads only the rows whose date lies in the training window.** It never reads a narrative, a category
  or any other value of a row outside it.
- **The corpus** is the training rows with a non-empty narrative. Every count in sections 1.4 and 1.5 is over the
  corpus. A training row without a narrative is ignored.

### 1.4 Predicates from the category column

- **Categories.** Values are compared after stripping. Values that fold equal (`fold_phrase`, the extractor's fold)
  are one category. Its label is its most frequent spelling; ties go to the smaller string.
- **Counts.** A category's count is the number of corpus rows that carry it; its sites are the distinct site values
  among those rows. In a list column, a row counts once per category.
- **The floor.** A category whose count is below N = 10 or whose sites are fewer than S = 3 never appears in any pack
  file. Its value is left out of the value map, so the pipeline counts it as an unmapped code.
- **Specific predicates.** A category that passes the floor becomes a predicate when its count is at least R = 50 and
  its folded label is not in the language file's list of non-specific labels (such as "other" and "unknown"). At most
  199 specific predicates are kept: those with the largest counts, ties by label. Every other category that passes the
  floor maps to the other bucket.
- **Ids.** A predicate's id is its label folded to ASCII, lower-cased, every run of characters other than `a-z` and
  `0-9` replaced by `_`, `_` stripped from both ends, cut to 36 characters. When the result is shorter than 2
  characters, starts with a digit or is a reserved word of the loader, `c_` is put in front. When it repeats an id
  already given (`other_category` is given first, then predicates in descending count, ties by label), `_2`, `_3` and
  so on is added. The label is cut to 80 characters.
- **Codes.** `D-001` onwards, one specific code per predicate in descending count (ties by label). The other bucket
  has the one non-specific code `D-999`. The value map sends every spelling of a category that passed the floor to its
  code.
- **The other bucket** is the predicate `other_category`, labelled from the language file. Its one term is the
  language file's phrase for it. A reader's `other_category` claims are never scored.

### 1.5 The lexicon of each predicate

The statistics run over the corpus. A corpus row filed under no predicate (other bucket, below the floor or no
category) still counts in `df(t)` below: it is a record that contains the term and is not filed under the predicate.

- **Candidate terms.** Each narrative is split into sentences and folded exactly as the extractor does
  (`split_sentences`, then `fold_phrase` on each sentence). Tokens are `\w+` runs. A token qualifies when it is made
  of letters only, has at least 3 letters, is not a stop word and is not a word of any negation cue or terminator of
  the language file. A unigram is a qualifying token. A bigram is two qualifying tokens next to each other with exactly
  one space between them. A record counts a term once, however often it appears.
- **The floor.** A term is a candidate only if it appears in at least N = 10 corpus records at at least S = 3
  distinct sites, and no value of a site or forbidden column (folded) occurs in it as whole words.
- **The score.** For a term `t` and a predicate `c`, `df(t)` is the number of corpus records containing `t`,
  `df(t, c)` the number of those filed under `c`, and `p(c | t) = df(t, c) / df(t)`: how often a record that contains
  the term was filed under the predicate. The term goes to the predicate with the largest `p(c | t)` (ties: larger
  `df(t, c)`, then smaller predicate id), but only if `df(t, c)` is at least 10 and `p(c | t)` is at least 0.6.
- **The size.** Each predicate keeps at most K = 50 terms: largest `df(t, c)` first, then larger `p(c | t)`, then the
  term in alphabetical order. A term belongs to one predicate only, as the loader requires.
- **A predicate with no term** gets one placeholder: `unlearned-` followed by its id with `_` written as `-`. Real
  text almost never contains it. The report counts such predicates.
- **Never learned:** negation cues, terminators, the negation window and the extraction limits. They come from the
  language file and the neutral template.

### 1.6 The rest of the pack: a neutral template

Everything that is not vocabulary comes from data files in `mycelic/collective/onboard/data/`, the same for every
export.

- **Entities.** Every drafted pack has the alias-only entity type `export_scope` with the one id `ALL`. Every record
  carries it, so every predicate a sentence states attaches to it, and none is lost for want of an entity. An
  alias-only type with no alias in a narrative is never read from text. A declared `entity` column becomes another
  alias-only type whose ids are its values that pass the floor (N rows, S sites), written in capitals with every run
  of other characters as `-` and the prefix `V-`. A value whose id would be longer than 40 characters, or equal
  another value's id, is left out; at most the 500 most frequent are kept, ties by id. D001 declares none.
- **The mapping** reads the normalised export (below), not the raw one. Its value map is section 1.4's.
- **Copied unchanged from the template:** egress limits (k 3, as in the device and IT packs), detectors (the device
  pack's settings, as the IT pack copied them), questions, follow-ups (one evidence packet), no rules.
- **Generated from the language file and the vocabulary only:** the generator's world (six synthetic sites, two
  affirmed and one negated template sentences per predicate, each holding the predicate's first term) and at least 40
  fixtures (one synthetic sentence holding one term each, gold by construction). **No fixture or generator sentence
  holds record text.** The drafted pack has no plant spec, so `pilot.audit demo` does not run on it; `pilot.audit run`
  does.
- **The normalised export.** One JSON line per row: the record id, the site lower-cased (a value that does not fit the
  pipeline's site id pattern is rejected and counted), the date in ISO form, the category values, the narrative and
  the scope id `ALL`. It is what the drafted mapping reads, and what the pilot audit takes as its records.

### 1.7 The privacy floor, checked mechanically

`python -m mycelic.collective.onboard check` checks every drafted pack against its export. Every check is recomputed
from the export, not taken from the drafter's own counts.

1. Every lexicon term but the placeholders and the other bucket's phrase appears in at least N = 10 corpus records
   at at least S = 3 sites.
2. Every category spelling in the value map, and every declared entity id, passes the same floor.
3. No string in any pack file, keys included, folds equal to a value of a forbidden, site or record-id column. No
   lexicon term contains a folded site or forbidden value as whole words.
4. No string in any pack file holds 8 consecutive `\w+` tokens that occur consecutively in any narrative of the
   export, training or test.

A pack that fails any check fails the privacy floor. It is never uploaded.

## 2. Sources, companies and files

A company is the unit that would give us an export. Each company's export is drafted and scored on its own.

### 2.1 MSHA mine accidents: the field no pack covers

- **The file:** `Accidents.txt` from `Accidents.zip`, and `Accidents_Definition_File.txt` (`SOURCES.md`).
- **A company** is a `CONTROLLER_ID`. Its export is its rows of `Accidents.txt`, unchanged, under the original header.
  **Sites** are `MINE_ID`s.
- **The companies:** the five controllers with the most training rows that have a narrative, among controllers with
  at least 100 test rows that have a narrative and at least 3 distinct mines in their training rows. Ties go to the
  smaller `CONTROLLER_ID` as a string. They are called c1 to c5 in that order. If fewer than five qualify, every
  qualifying one is used and the report says so. If none qualifies, criterion C1 fails.
- **Windows** on `ACCIDENT_DT`: training 2015-01-01 to 2021-12-31; test 2022-01-01 to 2024-12-31.
- **Declared roles:** `record_id` `DOCUMENT_NO`; `site` `MINE_ID`; `date` `ACCIDENT_DT`; `narrative` `NARRATIVE`;
  `category` `CLASSIFICATION`; `forbidden` `CONTROLLER_ID`, `CONTROLLER_NAME`, `OPERATOR_ID`, `OPERATOR_NAME`,
  `CONTRACTOR_ID`, `CLOSED_DOC_NO`, `FIPS_STATE_CD`, `EQUIP_MFR_NAME`, `EQUIP_MODEL_NO`. No entity and no reporter.
  The record-id and site columns are kept out of the pack by their own roles (sections 1.2 and 1.7).

**The scored column is `CLASSIFICATION`, chosen before any of its values was seen.** By MSHA's published
definitions, as the author reads them, `CLASSIFICATION` names the circumstances that contributed most directly to the
accident. `ACCIDENT_TYPE` names the kind of event that injured the person, such as being struck or caught.

- A company counting a pattern across its sites counts what went wrong in its operations. That is the circumstance,
  the same role a failure mode has in the device pack. `CLASSIFICATION` fills that role.
- `ACCIDENT_TYPE` describes the injury event. Three other columns already describe the injury (`INJURY_SOURCE`,
  `NATURE_INJURY`, `INJ_BODY_PART`).
- Both have a code twin (`CLASSIFICATION_CD`, `ACCIDENT_TYPE_CD`), so both are filed categories, one per row.
- **The risk this choice takes:** if one classification holds most accidents, the majority prior (section 4) is a
  strong control and C1 is hard to pass. That is intended: a reader must beat it.

### 2.2 NHTSA complaints: where hand-built packs exist

- **The file:** `COMPLAINTS_RECEIVED_2020-2024.zip`, the file of the vehicle replays and of R001 and R002.
- **A company** is a make: the six makes of R001 (FORD, CHEVROLET, JEEP, HONDA, NISSAN, DODGE). **Sites** are the
  consumer's states.
- **A make's export** is its rows as `tools/market/nhtsa_export.complaints` builds them: one row per complaint
  (`ODINO`), product type vehicle, a two-letter state, a known model year, one vehicle. The columns are `odino`,
  `state`, `received`, `components[]`, `vehicle`, `summary` and `reporter` (the complaint's own `ODINO`, which
  `pack-v2/` requires). One change from the replays: `components[]` holds every top-level component name as filed
  (`category_of`). None is folded into "UNKNOWN OR OTHER".
- **Windows** on `received`: training 2020-01-01 to 2022-12-31; test 2023-01-01 to 2024-12-31 (R001's window).
- **Declared roles:** `record_id` `odino`; `site` `state`; `date` `received`; `narrative` `summary`; `category`
  `components[]`; `forbidden` `reporter` (a copy of `odino`). No entity and no reporter role.
- **The hand packs**, read unchanged: `docs/collective/replay/vehicles/pack/` (decides C2) and `pack-v2/` (reported
  beside it). Each reads the same rows through its own mapping, with the vehicle as its entity.

**How the label spaces are matched.** Both kinds of pack name the same thing: the component names as filed. A drafted
predicate stands for one name. A hand predicate stands for the names its value map sends to its code: one in
`pack/`, and up to two in `pack-v2/`, which merges three old names into new ones.

- For each make, the shared space C is the set of names that are specific predicates of that make's drafted pack and
  map to a specific code in the hand pack.
- Scoring is in the hand pack's predicates, restricted to those that C reaches. A record's gold is the hand predicate
  of each filed name in C. The hand pack's prediction is its affirmed predicates in that set. The drafted pack's
  prediction is the hand predicate of each name it predicts, when the name is in C.
- C2 uses the sampled records with at least one filed name in C.

**R001's 0.434 is background only.** R001 drew its own 150 records and counted a negated claim as a wrong value.
D001 draws its own sample and counts affirmed claims only. The two numbers are not comparable.

### 2.3 openFDA device events: not in D001

- None of the fields the probe names (`mdr_text`, `product_problems`, `device`, `date_received`,
  `manufacturer_name`) is a site that a company runs.
- The reports come through a paged API, so a fixed export would need a sampling rule that the probe did not test.
- The run's one job already holds the two arms.

A later choice file may add it.

## 3. The held-out sample and what is read

- **Eligible test records,** per company: dated in the test window; a non-empty narrative; at least one filed
  category that is a specific predicate of the company's drafted pack.
- **De-duplication:** a test record whose folded narrative equals the folded narrative of any training record of the
  same company is not eligible. Among test records with equal folded narratives, only the one with the smallest record
  id is eligible.
- **The draw:** 200 records per company (all of them when fewer are eligible), drawn by
  `random.Random("d001:sample:<arm>:<company>").sample` from the eligible records sorted by record id. `<arm>` is
  `msha` or `nhtsa`; `<company>` is c1 to c5 or the make.
- **The gold:** the record's filed categories that are specific predicates of its company's drafted pack. Other-bucket
  and below-floor values are left out.
- **What a reader reads: the narrative only.** The record goes through the reader's pack mapping. Its codes are
  emptied before the codes channel and the extractor run. Its only structured entity is `export_scope` (for a hand pack,
  the vehicle). The reader never sees the category column, any other filed code or any other column.
- **A reader's prediction** for a record: the predicates of its affirmed text claims, less `other_category`. Negated
  claims are dropped, as `pair` drops them before detection.

**Where the test's construction could leak the label, and what guards it** (J001's lesson: the asked predicates gave
part of the answer away):

| Leak | Guard |
|---|---|
| The gold follows the filed categories, so a reader that names the common ones scores without reading. | The majority prior shows how far that goes. |
| The drafter learns how common each category is, not only what its records say. | The permuted-label control keeps the counts and breaks the link between text and label. |
| A narrative may contain its own category's name. | The label-name control, and the label-in-text share (section 5). |
| The same text in training and test. | The de-duplication above. |
| Test rows shaping the pack. | The drafter reads training rows only (section 1.3); companies are chosen by row counts, never by labels or text. |

## 4. Readers and controls

Every reader runs on the same records through the same pinned lexical extractor.

- **Drafted:** the company's drafted pack.
- **Controls,** each record-blind or label-blind:
  - **majority prior:** predicts, for every record, the specific predicate with the most corpus rows (ties by id).
    It never reads a record;
  - **permuted labels:** the drafted pack's predicates and value map, with each lexicon learned again by section 1.5
    from the same corpus after the category values are permuted across rows. The corpus rows' category values, in
    record-id order, are shuffled by `random.Random("d001:permute:<arm>:<company>").shuffle` and given back to the
    rows in that order. The parameters are the same. The category counts are unchanged; the link between text and
    label is gone;
  - **label names only:** the drafted pack with each lexicon replaced by the words of its label, as a hand pack would
    start. The folded label is split at `/`, `,`, `;`, brackets and the language file's joining words ("and", "or").
    Each part of 3 to 64 characters that is not a stop word is a term. A term several labels share goes to the one
    with the most corpus rows (the rule of `tools/market/vehicle_pack.py`). A predicate left with no term gets the
    placeholder of section 1.5.
- **Hand packs (NHTSA only):** `pack/` and `pack-v2/`.

## 5. Metrics

- **Micro precision, recall and F1** over (record, predicate) pairs, pooled over an arm's companies.
- **Macro F1:** the mean over (company, predicate) pairs with at least one gold record in the sample, each F1 counted
  over that company's records.
- **Coverage:** the share of records with at least one predicted predicate.
- **Intervals:** percentile bootstrap over records, B = 10,000, seed `d001:boot:<arm>`, the same draws for every
  reader. Micro F1 by `stats.bootstrap_f1`; differences by `stats.paired_bootstrap_f1`; macro F1 by the same draw
  rule.
- **Reported, deciding nothing:**
  - the label-in-text share: the share of sampled records whose narrative contains its own filed label, folded and
    word-bounded;
  - per company and per make, every metric above;
  - the drafted packs' sizes: predicates, other-bucket share of corpus rows, terms per predicate, placeholders;
  - the hand packs and the drafted packs on each make's whole sample in their own spaces.

## 6. Pass and fail, fixed now

| Id | Criterion | Passes when |
|---|---|---|
| C1 | MSHA reading | The drafted micro F1 minus the best control's micro F1 is at least 0.10, and the lower end of the paired 95% interval of that difference is above 0. The best control is the control with the highest micro F1 on the whole sample. |
| C2 | NHTSA against the hand pack | In the matched space of section 2.2, the lower end of the paired 95% interval of drafted minus `pack/` micro F1 is above -0.05 (non-inferior, margin 0.05). Superiority (lower end above 0) and the same against `pack-v2/` are reported and decide nothing. |
| M1 | No code per field | One code commit runs both arms. In `mycelic/collective/onboard/`, code and data, no identifier, string constant or JSON string equals (case-sensitive) a declared column name of either source; words the loader reserves for the pipeline's own record fields, such as `reporter`, are exempt. The guard test checks the column names; the run checks its drafted category labels the same way. The only per-field inputs are data: the settings file's roles and the download scripts in `tools/onboard/`, whose line counts the report gives. |
| M2 | The packs load | `python -m mycelic.collective.packs.loader check` exits 0 on every drafted pack (five MSHA and six NHTSA when every company qualifies). |
| M3 | The privacy floor holds | Section 1.7 passes for every drafted pack. |
| M4 | The pilot audit runs end to end | `python -m mycelic.collective.pilot.audit run` exits 0 on c1's drafted pack, with c1's normalised test-window export and an outcomes file with no rows; it writes `audit.json` with at least one record, and channels X and S have a summary. |

- **D001 passes only if all six pass.** Each is reported on its own.
- **A criterion that cannot be computed fails.** That includes a control that cannot be built and a company that
  cannot be drafted.

## 7. How it is read

- **If D001 passes:** on two public fields, an export's own filed categories and narratives were enough to draft,
  with no code per field, a pack whose lexical reader finds the filed category in later years:
  - better than record-blind and label-blind controls, by the margin, on mine accidents;
  - no worse than a hand-built pack, within the margin, on vehicle complaints;
  - and the drafted pack runs through the pilot audit.

  That supports "a new field costs a pack directory" for reading. It is no detection claim: nothing here asks
  whether the drafted pack's alerts find real problems. It says nothing beyond these two public sources, and records
  written to a regulator are not a company's own files.
- **If C1 fails:** the drafted pack loads but reads mine accidents no better than the controls. The claim does not
  hold for reading in this field.
- **If C2 fails:** a pack drafted from complaints alone reads worse than a list of category names. A person still
  has to write the vocabulary.
- **If M1 to M4 fail:** the mechanical claim fails as stated: code per field, a pack that does not load, a pack that
  leaks, or a pack the pipeline cannot run.
- **Role inference** is reported (section 1.2) and decides nothing.
- **Filed categories are not truth.** Every reader pays for the key's errors, and the controls pay the same.

## 8. What the run prints, and what it never prints

- **Prints:** counts and rejections per company; the role inference results; the drafted packs' sizes and hashes;
  category labels with their corpus counts; for each predicate, its first 10 terms in the lexicon's own order (every
  one passed the floor); every metric and interval; the criteria; the definition file's lines for the declared MSHA
  columns; the download scripts' line counts; the code hash and the settings file's sha256.
- **Never prints or uploads:** any narrative or part of one; record ids; site values; controller, operator, mine,
  contractor or manufacturer ids or names; vehicles; the audit's review list or record references; the exports;
  the normalised records. MSHA companies appear only as c1 to c5.
- **A last guard:** before the report is written, it is scanned for any 8 consecutive tokens of a narrative, any
  record id or site value of at least 5 characters, and any forbidden value of at least 4 characters that holds a
  letter, as whole words. A hit withholds the report and fails M3.

## 9. The run

- **The settings:** every value above is in `docs/collective/onboard/D001-settings.json`, committed with the build and
  checked against this file by a test. The run file names it and its sha256.
- **The trigger:** the owner adds `docs/collective/onboard/run-001.json` after review. Pushing it starts
  `.github/workflows/onboard-run.yml`, which runs the newest run file.
- **The first run is the result.**
- **What counts as a run:** the run starts when both downloads have finished. A failure before that (an HTTP error,
  a timeout, a truncated or unreadable archive) is not a run, and the same run file may be pushed again unchanged.
  From that point on, whatever happens is the run's result, a crash or a timeout included.
- **Time:** one job, with a limit of 180 minutes (a setting of this design). A job stopped by its limit after the
  downloads is a failed run.
- **Changes:** a change before any run is an amendment, recorded here as such. Any change after a run has started is
  a new choice file.

## Amended before any run, 2026-10-09

No value of either source has been seen. No run has happened, and no run file exists. Two reviews of the build found
rules that would fail a privacy check by construction, and words that claim more than the test shows. Each change
below replaces the text it names. Where the sections above disagree with this one, this one wins. `BUILD-D001.md`
lists four conflicts the build found with the rule as written; this section settles all four.

### A1. Dates (rule 1.3)

- **Was:** the part of a date value before its first space or `T` is parsed.
- **Now:** the part before the first space, or before a `T` that is followed by a digit, is parsed.
- **Why:** an upper-case `OCT` holds a `T`. Every October date written `04-OCT-2021` would fail to parse. The MSHA
  date column could then miss the 0.95 share, and every MSHA draft would fail. An ISO time such as
  `2021-10-04T08:00` is still cut.

### A2. One refusal for every string taken from records (rules 1.3 to 1.7 and 8)

The rule refused values in three places, with three different sets: the drafter (training rows only), the check
(every row, every pack string, keys included) and the last guard (both arms pooled, the whole report, as written).
They now share one definition, computed by one function that all three call.

- **The refused values** of an export are the folded values of its record-id, site and forbidden columns, in every
  row of the export.
- **A string is refused** when, folded, it:
  1. equals a refused value;
  2. holds, as whole words, a forbidden value of at least 4 characters that holds a letter, or a record-id or site
     value of at least 5 characters (rule 8's lengths);
  3. equals a word of a forbidden value that has two or more words. A word is a run of letters and digits. Only
     words of letters alone, at least 4 of them, count. This catches a surname inside a name, or a model inside a
     vehicle.
- **A lexicon term is refused** when the term or any of its words is refused. This refuses everything the old
  rule 1.5 refused.
- **Rule 1.3:** the drafter reads the record-id, site and forbidden columns in every row, only to build the refused
  values. It still never reads a narrative, a category or any other value of a row outside the training window.
  These columns hold no label and no text. A refusal can only remove a term or a category; it can never add one.
- **Rule 1.5:** a refused term is not a candidate. `draft.json` counts the terms that passed the floor and were
  refused.
- **Rule 1.4:** a category is left out of every pack file, as if it were under the floor, when any string the
  drafter would write or print for it is refused: a spelling, its label as cut, its id or its placeholder. The
  pipeline then counts it as an unmapped code. `draft.json` counts such categories and never names them. This
  settles BUILD conflict 2: a missing-value marker such as `?`, filed both as a category and in a forbidden column,
  is left out instead of failing the floor.
- **Rule 1.6:** a declared entity value is left out when it or its id is refused.

### A3. The privacy floor check (rule 1.7, item 3)

- **Was:** no string in any pack file, keys included, folds equal to a value of a forbidden, site or record-id
  column, and no lexicon term holds a site or forbidden value as whole words.
- **Now:**
  - **3a.** The strings the drafter derived from records are the specific predicates' ids, labels and lexicon terms
    (placeholders included), their code labels, the value map's spellings, and the declared entity types' ids and
    aliases. A2 refuses none of them: a term by the term rule, every other one by the string rule.
  - **3b.** Every other string of the pack is the template's. The check rebuilds every pack file from the neutral
    template, the language file and the strings of 3a, and requires the same content. A string written anywhere
    else fails the floor.
- **Why:** the old rule compared the template's own words and keys with record values. NHTSA's sites are lower-cased
  states, and Idaho's `id` equals the key `id` in `pack.json`. One complaint from Idaho would have failed every NHTSA
  pack, with no record value in it. 3b keeps the old rule's reach: a value planted at a template position, a key
  included, still fails.

### A4. Terms the loader would refuse (rule 1.5)

A candidate term longer than 64 characters, the loader's limit, is not a candidate. This settles BUILD conflict 4.

### A5. The last guard (rule 8)

- **Was:** the whole report was scanned, case-sensitive, for the forbidden values of both arms, and for record ids,
  site values and narrative 8-grams.
- **Now:** per arm, the guard reads the strings the report prints that came from records: category labels,
  predicate ids, lexicon terms and error texts. A string is a hit when A2 refuses it against that arm's exports
  (every row of every company of the arm), or when it holds 8 consecutive tokens of a narrative of those exports.
  The report's fixed words, its keys, the settings' company labels and the definition file's lines do not come from
  records and are not scanned.
- A hit, or an export the guard cannot read, withholds the report. The withheld report gives the hit counts by kind
  and names the kinds it found. M3 fails.
- **A withheld report withholds everything that carries the same strings.** The arm files and the drafted packs are
  then not uploaded. Without a report, nothing but the job log is kept.
- **A company whose pack failed the privacy floor** has no label, id or term printed or uploaded. Its arm file gives
  counts only.
- A value of one company that appears in the printed strings of another company of the same arm is still a hit.
- **Why:** one equipment cell spelled `Other`, `UNKNOWN` or `FORD` in any MSHA row would have withheld the whole
  report. The old guard matched the report's own words ("Other share") and the other arm's make names. It missed a
  surname inside a name, and it matched folded terms against values as written. This settles BUILD conflict 3.

### A6. NHTSA's vehicle column (rule 2.2)

`vehicle` is declared forbidden. Rule 8 says the run never prints a vehicle, yet a model name could have been learned
as a term. By A2, the make and model words of every vehicle in a make's export are refused as terms. The hand packs
still read the vehicle as their entity.

### A7. The matched space against `pack-v2/` (rule 2.2)

- **Now:** a record's gold in a hand pack's matched space is the hand predicate of each filed name that the hand pack
  maps to a predicate C reaches.
- `pack/` maps exactly one name to each specific code, so this changes nothing for `pack/` or for C2.
- `pack-v2/` maps two names to each of three codes. Before, a reader that named the merged predicate for a record
  filed under the name outside C was counted wrong. Now it is not. `pack-v2/` decides nothing.

### A8. Words that said more than the test shows (rules 4, 5, 7 and M1)

- **Rule 4:** the label-names control is a mechanical split of each label. It is not how a person would start a hand
  pack.
- **Rule 5:** C1's interval resamples the sampled records of these companies (c1 to c5). It is not an interval for
  mine accidents in general. The report says so.
- **Rule 7, if D001 passes:** "no worse than the component-name list `pack/`, within the margin, on vehicle
  complaints". Most of `pack/`'s lexicons are the component names themselves. The report prints, beside C2, how many
  names each make's matched space holds.
- **M1:** the per-field code is the download scripts and every module they import from `tools/`. For NHTSA that adds
  `tools/market/nhtsa_export.py` and `tools/market/vehicle_pack.py`. The report gives each file's line count, and the
  code hash covers them.
- **The report** prints each criterion's deciding values unrounded, beside the comparison it makes.

### A9. The declaration, added

The language file's header words (site words such as `mine` and `location`; category words such as
`classification`, `type` and `component`) and its missing-value markers (`no value found`, `?`, `unknown or other`)
were chosen by an author who knows both sources' headers and, from general knowledge, their marker conventions.
`no value found` decides whether such an MSHA classification can become a scored predicate. The roles-right count
therefore says little, and for MSHA nothing. The report says so beside it.

### The settings

`D001-settings.json` changes with this amendment: `params` gain the refusal lengths of A2 and the term limit of A4;
`report_guard` keeps only the n-gram length; NHTSA's forbidden columns add `vehicle`; each arm lists its download code
(A8). The run file names the amended file's sha256.

### What these changes risk

- A word of a company, equipment or vehicle name that is also an ordinary word, such as "materials" or "machinery",
  is refused as a term. A category whose label equals such a word is left out. Each makes the drafted pack weaker,
  never stronger. The permuted-labels control learns from the same eligible terms, and a category left out is gone
  for every reader. The report counts both.
- Reading the identifying columns of test rows lets test rows remove terms. They cannot add one, and no test label or
  narrative is read.

## Runs

None yet.
