# A003 build note: the lab's `jt` and `j4` experiments, as built

What was built so the lab can run accuracy test A003 (`CHOICE-A003.md`, with its section "Amended before any build,
2026-10-14", C1 to C32). The choice file is the rule; this note says how each build carries it out, where the rule
left a choice to the build, what deviates (nothing of the rule's substance), what the tests pin and what the dry run
showed. The rule was committed alone and amended before any code (`ed96530`). The rule's build plan has two builds
with a lab run between them: build 1, the term-drafting kind `jt`, below; build 2, the judging kind `j4`, after the
term run's amendment, in the section "Build 2" below.

## Build 1: the term-drafting kind `jt`

Built on branch `wf/a003-terms` from `ed96530`. Step (0) of the order (C30) came first, in its own commit: A002's
golden outputs recorded on the base commit, before any lab file changed. Build 1 changes no pinned file: `lab/j1.py`,
`lab/goldlabels.py`, `lab/j2.py`, `lab/j2labels.py`, `lab/j3.py`, `lab/j3labels.py` and `lab/shard.py` are imported
unchanged, nothing under `mycelic/` (the onboard package and its data files included), `connectors/openfda.py`,
`research/`, the vehicle pack, `tools/` (loaded by path only), any `CHOICE-*.md` (this rule's included), `run-*.json`
or `*-settings.json` changed, and `lab.aggregate.j2_block`, `lab.summary._j2_tables` and every j3 function of
`lab/aggregate.py` and `lab/summary.py` are not edited (the jt code is inserted after them). While building, no NHTSA
record was read: the sandbox reaches no NHTSA host, so every run here read the synthetic complaint archive of
`tests/lab/j4_data.py` (every value invented), and no model ran (the term run calls none). No term of a real term set
exists yet; the synthetic draft's terms are `j4_data`'s invented cue words, and no test prints them.

### The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` | an `experiments.jt` block of four keys (`_JT_KEYS`): `minutes`; `pack` exactly `docs/collective/replay/vehicles/pack` (`JT_PACK_PROBLEM`); `cutoff` exactly `2023-01-01`, `NHTSA_WINDOW[0]` in ISO form (`JT_CUTOFF_PROBLEM`); `from` exactly `2020-01-01` (`JT_FROM_PROBLEM`) (C23). The makes, roles, columns, language and drafter parameters are code constants of `lab/jt.py`. A request with a `jt` block holds no other block (`JT_ALONE_PROBLEM`, item 10) and its `retention_days` is 90 (`JT_RETENTION_PROBLEM`, C15) |
| Plan | `lab/plan.py` | one model-free unit `jt` (kind `none`, as `x1`), params `pack`, `cutoff` and `from` copied from the block (C27); the shard is `s001-none`; nothing is provisioned (C17) |
| Preregistration | `lab/prereg.py` (`_jt`) over `lab/jt.py` | the archive's one cached fetch (`_once`); the pooled export in process; the draft and the check in a subprocess, `python -m lab.jt draft`, under `_Steps.run` with `lab.jt.DRAFT_TIMEOUT_S` (900 s) and capped logs (C11); `prereg/jt/terms.json`, `pack/` (P_draft), `check.json` (the check's counts by check), `drafting_refs.jsonl`, `drafting_text.jsonl`, `prereg.json`; the fixed console lines; `JT_FETCH_FAILED`, `JT_FLOOR_FAILED` and `JT_DRAFT_FAILED`, each after a `jt step: stopped` line; the manifest's `jt` block; `main()`'s `prereg:` line gains ` jt yes` (C27) |
| Unit | `lab/units.py` runs `python -m lab.jt verify` | `ADAPTERS["jt"]` (no tasks, no routing, result `verify.json`, subcommand `verify`); `--prereg`, `--run-id`, `--runs-dir`; `PREREG_EXPERIMENTS` gains `jt`; `_jt_status`; `unit_notes` gives the jt unit `public_terms`, never `synthetic` (C27) |
| Aggregate | `lab/aggregate.py` (`jt_block`) | the term set's sha256 cross-checked against the manifest, `prereg/jt/prereg.json` and the bytes of `prereg/jt/terms.json`; the preregistration's counts and hashes; the unit's `verify.json`; the `jt` key only in a report whose plan has the unit (C30) |
| Summary | `lab/summary.py` | the term run's lines under two headings in the report's class section and in the plan summary's preregistration, the full sha256 in the report the log prints between the `MYCELIC-LAB` markers (C13) |
| Notes | `lab/notes.py` | `JT_LABEL`, `JT_TERMS_NOTE`, `JT_FLOOR_FAILED`, `JT_DRAFT_FAILED`, `JT_FETCH_FAILED`, `JT_NOT_VERIFIED`, `NOTES["public_terms"]`, two headings and the jt columns; no ASCII digit in any sentence (`test_notes_hold_no_digits` holds) |
| Dry run | `lab/dryrun.py` | `--jt-data DIR` (a `tests.lab.j4_data` directory) hands `DIR/complaints.zip` to `lab.prereg --nhtsa-archive`; refused together with `--j2-data` or `--j3-data` (C27) |
| Template | `lab/templates/terms-003.json` | the rule's term request, `retention_days` 90 (C15), to copy to `lab/requests/terms-003.json` |
| Workflow | `.github/workflows/mycelic-lab.yml` | unchanged: the term run is the existing `prereg` step's work (20 minutes inside the plan job's 30) and uploads with the plan artifact; nothing in the rule asks for a new step in build 1 |

New modules: `lab/jt.py` and the test data `tests/lab/j4_data.py`, the stub `tests/lab/stubs/jt_stub.py` and the
golden normaliser `tests/lab/a002_golden.py` with its recording `tests/lab/data/a002_golden/`. The request file is not
added under `lab/requests/`: pushing a file there starts a run, and the caller starts the run (C31).

### How each rule is carried out

- **Item 1 and C14, the cutoff and the window.** `lab.jt.CUTOFF` is `NHTSA_WINDOW[0]` (imported from
  `lab.goldlabels`, `20230101`), `CUTOFF_ISO` its ISO form, `FROM` `2020-01-01` and the window's last day the day
  before the cutoff, computed (`2022-12-31`). A test pins the window to `D002-settings.json`'s `arms.nhtsa.train` and
  the cutoff to its `test` window's first day.
- **Item 2, the received date.** The export's `received` column is `nhtsa_export.complaints`' (`LDATE`); the drafter
  reads it through the `date` role. Nothing of the build reads the date another way.
- **Item 3 and C28, the pooled export.** `pooled_export(blob)` loads `tools/onboard/fetch_nhtsa.py` by path inside the
  function (C29), calls `nhtsa_export.complaints` once per make of `NHTSA_MAKES` over the drafting window with
  `fetch_nhtsa._Every()` and `fetch_nhtsa.REPORTER`, concatenates the rows in make order, drops a row whose `odino` an
  earlier make gave and counts it, writes one CSV through `fetch_nhtsa.rows_csv` with D002's seven columns and parses
  it with `onboard.exports.parse_export`, which must read back every pooled row (a rejected or lost row refuses the
  export, `JtError`, so that the drafting files are the rows `complaints` built). No row received on or after the
  cutoff is in it: a test computes, from the synthetic archive's own lines, the complaints of the six makes received
  from 2020-01-01 to 2022-12-31 and finds the export's record ids equal to them, with boundary complaints received
  on 2019-12-31 (out), 2020-01-01 (in), 2022-12-31 (in) and 2023-01-01 (out) named one by one, and the export's
  earliest and latest received dates exactly the window's two ends.
- **C4, the drafting block.** `prereg/jt/drafting_refs.jsonl` is one `{"record_ref"}` line per drafting row, sorted:
  the bytes `lab.j2labels.excluded_jsonl` writes for the same ids (written out in `lab/jt.py`, so that the term module
  imports no judging module; a test pins the two equal). `prereg/jt/drafting_text.jsonl` is one `{"sha256"}` line per
  distinct non-empty folded narrative (`packs.canonical.folded`), sorted. `terms.json`'s `drafting` block holds the
  rows, the sha256 of the sorted record ids (equal to the refs file's sha256, by construction), both files' sha256,
  the distinct narratives and `straddling`: the drafting rows whose `odino` has a row of the six makes received on or
  after the cutoff anywhere in the same archive (the synthetic archive holds two such complaints, one whose later row
  is received on the cutoff day itself, and one drafting complaint whose later row is filed under a seventh make,
  which does not count: `straddling` is 2). The earliest and latest received dates of the export are in its
  `export` block (C14).
- **Item 4, the drafter.** `run_draft` calls `draft_export(export, roles(), window(), "nhtsa_pooled",
  params=load_params())`: the roles of `ROLES` through the onboard package's own `roles_from_json` (D002's NHTSA
  roles, `vehicle` and `reporter` forbidden), `load_params()` with no argument, read once and handed to the draft and
  the check alike (a test pins the whole parameter set handed to both equal to `defaults.json` and to
  `D002-settings.json`'s `params`, key for key, and the roles and window handed to both equal to the module's; the
  synthetic narratives of one category carry their row's make, which the `vehicle` refusal must remove: a test finds
  none in T and shows that the same export with `vehicle` not forbidden would give them to that category). The code hash is `common.code_hash` over every file of `mycelic/collective/onboard/` (code and data,
  `__pycache__` aside) and the three download files of item 4; the two data files' sha256 are printed and kept.
- **Item 5, the check.** `check_pack(out/pack, export, roles, window, params=params)` on the same parsed export the
  drafter read. `check.json`, `terms.json` and the console carry only its counts and booleans by check
  (`check_counts`: the check's pack id, the loader's error text and the n-gram scan's file names are left out, so no
  string a failing pack might hold reaches the artifact). A failed check exits 5 (`EXIT_FLOOR`, never 1, Python's own
  exit for an uncaught exception); `lab.prereg` prints `jt check: passed no ...` with each check's outcome and each
  failing check's `failures` count, then the `jt step: stopped` line, and refuses the plan with `JT_FLOOR_FAILED`
  before anything is written under `prereg/jt/`.
- **Items 6 and C18, T.** `term_set(draft, hand)`: for each specific predicate of the hand pack, in id order, the
  spelling the hand value map sends to its code, the drafted predicate whose value map holds that spelling and is a
  specific drafted code (`draft.plan.codes`), its corpus records and its learned lexicon (`draft.learned`, which never
  holds a placeholder) in the drafter's order; the empty list and `drafted_id` null when the drafter left the
  spelling out. 26 entries or `JtError`. The synthetic data reaches both of item 6's harder cases: the twins
  `service_brakes` and `service_brakes_hydraulic` share every cue word, so each shared word goes to the twin with more
  records (the larger p(c | t)) and the smaller twin, a specific drafted predicate of 54 records, learns none: its
  entry has a `drafted_id` and the empty list (the placeholder left out of T, P_draft's lexicon holding it), and P_D
  gives it `draft.placeholder(<hand id>, ids)`.
- **Item 7, C21, C22 and C29, `terms.json`.** Canonical JSON (`jsonio.canonical_dumps`) and a final newline; the keys
  of item 7: `kind` `lab_jt_terms`, `schema_version` 1, the cutoff, the window, the makes, the language, the roles,
  the archive's sha256 and bytes, the `export` block (rows per make and pooled, each make's complaint count, the
  cross-make duplicates, the received dates, the parser's rejected counts), the `drafting` block, `draft`
  (`draft.summarise`'s counts with the refusal's, from `draft.facts`: numbers and the date format, never a label),
  P_draft's id, four hashes and file list, P_D's four hashes, the hand pack's, the check by check, the code block
  (`onboard_code_hash`, the file count, the two data files' sha256, `jt_code_hash` over `lab/jt.py`), `term_counts`
  and `terms`. Its sha256 is the term set's. The step's wall time and peak memory are kept out of it (they vary from
  run to run) and go to the manifest's `jt.step` block and the console.
- **Item 9 and C21, P_D.** `drafted_pack(hand_dir, entries, out_dir)` copies every hand pack file byte for byte and
  rewrites `vocabulary.json` (the hand document, each specific predicate's `en` lexicon replaced by T's list or by
  `draft.placeholder(<id>, ids)` with the onboard template's ids; `unknown_or_other` untouched), then loads it with
  `loader.load_pack_dir`. `p_d_hashes` builds it in a scratch directory. P_D's `fixtures_hash` and `detector_hash`
  equal the hand pack's; its `vocabulary_hash` and `config_hash` differ; P_draft's four hashes differ from P_D's and
  are kept apart in `terms.json` and `prereg/jt/prereg.json`.
- **Item 10, the unit.** `python -m lab.jt verify --prereg prereg/jt/prereg.json --run-id ID --runs-dir DIR` re-hashes
  `terms.json` against the preregistration, checks `lab/jt.py`'s code hash against both documents, the hand pack's
  hashes against both, the two drafting files against both, rebuilds P_D from the hand pack and T and checks its four
  hashes; it writes `runs/jt/<run id>/verify.json` (`lab_jt_verify`) and exits 0 when every check holds, 1 otherwise.
  The aggregate counts the unit verified only when `verify.json` says so and its sha256 and P_D hashes equal the
  preregistration's.
- **Item 11, the console.** `lab.jt.console_lines`: `jt export:`, `jt drafting:`, `jt draft:`, `jt check:` and the one
  fixed line `jt terms: ... terms_sha256 <64 hex>`, then `lab.prereg`'s `jt step:` line (wall seconds of the step and
  of the draft, the timeout, the peak resident memory in KiB). Counts, dates and hashes only.
- **C11, time and memory.** The draft runs as a subprocess under `_Steps.run` with a 900-second timeout, below the
  `prereg` step's 20 minutes; a timeout (`_StepFailed`), a kill (a negative exit code, as the kernel's out-of-memory
  killer gives), a `MemoryError` (exit 4), a refused export (`DraftError`, exit 3), any other error in the draft
  (exit 6, its class name on stderr and never its message), an uncaught exception before the draft (Python's exit 1,
  which is no code of the draft's), a usage problem, a missing or malformed `draft.json`, a check that exits 0 without
  passing, or a P_D rebuild or a `terms.json` that fails in the plan job's own process refuse the plan with
  `JT_DRAFT_FAILED`; a download or an archive that is not a readable zip with `JT_FETCH_FAILED` (a new note, as L1's
  `L1_FETCH_FAILED`: the same request may run again). Every stop prints `jt step: stopped wall_s ... draft_wall_s ...
  draft_timeout_s ... children_peak_rss_kib ...` (the parent's `RUSAGE_CHILDREN` peak, which covers a killed draft),
  so the amendment can record the attempt. The export goes to a scratch directory beside the plan's (`lab-jt-*`,
  never under it: everything under it is uploaded) and is removed whatever the outcome; the subprocess's peak
  resident memory is its own `resource.getrusage(RUSAGE_SELF).ru_maxrss`.
- **C15, retention.** The template's `retention_days` is 90.
- **C27, the wiring touch points of build 1.** Each is named in the shape table; `lab/units.py`'s budget list and
  routed tasks are untouched (the jt unit has no budget and no tasks).
- **C29, the import rule.** `lab/jt.py` imports nothing from `tools/`; `_fetch_module` loads `fetch_nhtsa.py` with
  `importlib.util.spec_from_file_location` inside `pooled_export` only and puts `sys.path` back after the load (the
  download module inserts `tools/market` while it imports), and drops the modules it imported by bare name
  (`nhtsa_export`, `vehicle_pack`) from `sys.modules` unless they were there before, so that no later bare import in
  the plan job's process finds them. A test in a fresh interpreter imports `lab.jt`,
  `lab.prereg`, `lab.units`, `lab.aggregate`, `lab.summary`, `lab.request` and `lab.dryrun` and finds no `tools`
  directory on `sys.path` and no download module loaded, and two more find `sys.path` unchanged and no bare
  `nhtsa_export` or `vehicle_pack` importable after `pooled_export`.
- **C30, the golden outputs.** Below, "Step (0)".

### Choices the rule left to the build

- **The request's key names** ("the exact key names are the build's"): `minutes`, `pack`, `cutoff` and `from`, as the
  rule's own request section spells them.
- **The draft's timeout** (C11: "the build fixes the timeout"): 900 seconds, five minutes under the `prereg` step's
  20, leaving the step room for the download, the export and the writing.
- **The unit's minutes**: 10 (the template), against a unit that re-hashes and rebuilds one pack in under a second.
- **`straddling` read on the raw archive.** The count reads every archive row of the six makes received on or after
  the cutoff, with no other filter, so that it cannot miss a later row `complaints` would have dropped for another
  reason; it decides nothing in the term run (C4).
- **The median of the terms per reached predicate** is the upper middle of the sorted counts, as `draft.summarise`
  takes its medians.
- **"Reached"** is a hand predicate whose spelling a specific drafted category holds; a reached predicate can have no
  term (the drafter learned none for it), so `empty` counts every entry with an empty list, reached or not.
- **The verify unit checks more than P_D's hashes**: also `lab/jt.py`'s code hash, the hand pack's hashes and the two
  drafting files (`VERIFY_CHECKS`), all of which the judging run's check will read; each is cheap and a difference in
  any of them makes the term set unusable.
- **The report block is absent, not null, without the unit**: every other report keeps its keys and bytes (C30); the
  preregistration manifest gains `"jt": null` for every plan, as it holds `null` for every other absent kind (the
  manifest is not among the golden outputs).
- **A new note key, `public_terms`**, rather than `public_data` (openFDA's sentence) or `public_narratives` (which
  says the narratives were read with their codes hidden and scored): the term run reads complaints with their codes,
  scores nothing and runs no model.
- **`JT_NOT_VERIFIED`**, a unit status reason the rule does not name: the unit's `failed` needs a reason, and the
  existing ones say a harness failed, which this is not.
- **The display class of the term run's block** is its unit's (`no-model` on a real run, `plumbing` in a dry run, as
  every dry-run record is).

### Ambiguities and readings

- **"the sha256 of the sorted record ids"** (C4, item 11) is read as the sha256 of the refs file's bytes, which hold
  exactly the sorted ids in `excluded_jsonl`'s line format; `terms.json` keeps both names (`refs_sha256` and
  `refs_file_sha256`), equal by construction, so a reader of either finds it.
- **"the code hash over `mycelic/collective/onboard/**` (code and data)"** is read as every regular file under the
  package directory except compiled caches, plus the three download files item 4 lists; the file count is kept beside
  the hash.
- **"the check's result (counts by check, never a string)"**: `check.json` in the artifact, `terms.json`, the
  manifest, the summaries and the console all carry `check_counts`, the booleans and counts by check, never the
  check's own document (which names the pack and can hold the loader's error text and the n-gram scan's file names).
- **The `--j4-data` exclusivity** of C27 belongs to build 2 (no `j4` kind exists yet); build 1 refuses `--jt-data`
  with `--j2-data` or `--j3-data`.
- **"prints ... the categories passing the floor and the specific predicates"** (item 11) is read as their counts;
  their names are category spellings, which the same item forbids.

### Deviations

None of the rule's substance. The build plan names `draft(export)` and `check` as functions of `lab/jt.py`; they are
one function, `run_draft(export_path, out)`, the body of the `draft` subcommand the subprocess runs, because the
check must read the pack the draft wrote in the same process. The build plan's `term_set(draft, hand)` returns T's
entries; `terms_document` builds `terms.json`'s document and `document_bytes` its bytes.

### Step (0): A002's golden outputs (C30)

Before any lab file changed, on `ed96530`, `python -m tests.lab.a002_golden record --out tests/lab/data/a002_golden`
ran the j3 template dry run (`python -m tests.lab.j3_data --out DIR`, then `python -m lab.dryrun --request
lab/templates/accuracy-002.json --out OUT --j3-data DIR`) and recorded its plan summary (`plan/summary.md`),
`report/report.json` and `report/report.md`, normalised; the recording is its own commit, before the build's first.
`A002GoldenTests` re-runs the same dry run on the code under test and compares the three files byte for byte after
the same normalisation. What the normaliser replaces, each by a fixed token, and why:

- the synthetic archive's sha256 (`complaint_file_sha256`, in `report.json`, the plan summary and the report): the
  archive is a zip whose entry carries its generation time, so the hash changes with every generation of the test
  data while every draw, score and count stays fixed by the seed;
- the values that name the code, not the computation: the commit, the plan file's sha256 (it holds the commit) and
  the preregistration's sha256 (it holds the plan's), so that a later commit with the same computation compares
  equal;
- the machine's and the clock's figures: wall seconds, latency percentiles, host and CPU model, in `report.json` and
  in the report's latency and host tables;
- the manifest's model repository and file names in the plan summary's models table (the repository's no-model-names
  guard covers `tests/`), replaced by the manifest key they belong to.

Nothing else is excluded; a test changes one figure of the report and of the markdown and finds the normalised bytes
differ, and the JSON normaliser refuses a document that is not canonical.

### Tests

`tests/lab/test_lab_jt.py`, offline (52 tests at the build commit, 71 after the fixes below; the table is as fixed):
the synthetic archive of `tests/lab/j4_data.py`, the lab's fake server
for the dry runs' shards (no model is served: the unit is model-free), no network host. Four fixtures are made once:
the archive with its pooled export and one draft in process, two preregistrations of the term request in two plan
directories, the template's dry run with `--jt-data`, and the j3 template's dry run for the golden outputs.

| Class | Pins |
|---|---|
| `SettingsTests` | the cutoff equal to `NHTSA_WINDOW[0]` and the window to `D002-settings.json`'s `train`; the roles, columns, makes, download files and hand pack equal to `D002-settings.json`'s `arms.nhtsa` (with `vehicle` forbidden); the drafter called with `load_params()` and no argument, the draft and the check handed the same whole parameter set, equal to `defaults.json` and to `D002-settings.json`'s `params`, and the module's roles and window; the data files' sha256; the hashed file list; `lab/jt.py`'s code hash equal to `common.code_hash` over that file, kept by a byte-equal copy and changed by one changed byte; the draft's timeout 900 seconds, below the step's |
| `ExportTests` | the export's record ids exactly the archive's complaints of the six makes received from 2020-01-01 to 2022-12-31, computed from the archive's lines, with the boundary complaints of 2020-01-01 and 2022-12-31 in and of 2019-12-31 and 2023-01-01 out, the earliest and latest received dates the window's ends, and the exact row count; the straddling complaint's 2023-01-01 row and the repeated narrative's later complaint left out; a parse that rejects or loses a pooled row refused (`JtError`); a complaint under two makes kept once under the first make, one duplicate counted; D002's seven columns and the reporter column; the refs file equal to `excluded_jsonl`'s bytes, the text file hashes only, one per distinct folded narrative, the drafting block's figures; `straddling` 2, the two complaints with a later row of the six makes (one on the cutoff day), not the one whose later row is a seventh make's |
| `DraftTests` | the check passing with every check's counts and no string; the check reading the pack the draft wrote against the same export object, roles and window; T's 26 entries in id order with the hand spellings, the eight reached predicates of the synthetic data, the term counts; the placeholder left out (a reached predicate of 54 records with `drafted_id` set, the empty list, P_draft's placeholder, no T term with the placeholder prefix); the twin split (every shared cue word in the larger twin's list only); the make words refused (none in T, the refusal's counts, and the drafter learning them with `vehicle` not forbidden); T equal to P_draft's lexicons through the value maps; the drafter's counts equal to `draft.summarise`'s key for key; the draft's own peak memory above 10,000 KiB |
| `DraftedPackTests` | every P_D file but `vocabulary.json` the hand pack's bytes; the lexicons (T or the placeholder, the reached-but-empty predicate's placeholder, `unknown_or_other` and every other key untouched); P_D's hashes (fixtures and detector the hand pack's, vocabulary and config not, all four apart from P_draft's; the term order counts); no placeholder folds equal to any term, any hand term or any synthetic narrative; the drafted lexical judge confirming from a drafted term the hand lexicon lacks, refuting the hand label alone on a reached predicate whose list lacks it, and never confirming from an empty predicate's hand term, the hand judge the opposite, on the same shipped payload |
| `PreregTests` | the files under `prereg/jt/` and their manifest entries, no export kept and no scratch directory left; `check.json` the counts by check, no string, equal to `terms.json`'s `check`; `terms.json` canonical with item 7's keys and values; its sha256 stable across two preregistrations (and the drafting files and the check); `prereg/jt/prereg.json` and the manifest's `jt` block; the console's six fixed lines, the sha256 at the end of `jt terms:`, and no term, category spelling, record id, state, vehicle or narrative word in stdout, stderr or the step's logs; the console lines a function of `terms.json`; the draft's peak memory in the manifest and the `jt step:` line, the same number |
| `StepFailureTests` | `JT_DRAFT_FAILED` for a timeout (the step's limit lowered to three seconds, a stub that would pass after an hour, refused in under a minute, the step runner handed `timeout_s` 3 and raising "did not finish in time"), a SIGKILL, a `MemoryError`, a refused export, an error in the draft (exit 6, its class name only), an uncaught exception (exit 1), no result, a malformed result, the check's exit code with no result, exit 0 with a check that did not pass, an export the parser does not read back whole and a P_D rebuild the loader refuses; `JT_FETCH_FAILED` for an absent archive, a file that is not a zip and a truncated zip; `JT_FLOOR_FAILED` with the `jt check: passed no` line and the failing check's count; in each, `plan-error.json`, the `jt step: stopped` line, nothing under `prereg/jt/` and no scratch directory left; the real draft's step handed `DRAFT_TIMEOUT_S`; the draft command's exit codes, none of them 1 |
| `VerifyTests` | the CLI verifying a copy of the preregistration, its one line and `verify.json`; a changed `terms.json`; a term set changed and pinned again (only `p_d_hashes` fails); a changed refs file; a changed text file (only `drafting_files` fails); other hand pack hashes in `terms.json` pinned again or in the preregistration (only `hand_hashes` fails); other P_D hashes in the preregistration (only `p_d_hashes` fails); another code hash; no preregistration; the adapter's argv, `_jt_status` on every pairing, `unit_notes` and `public_text` |
| `RequestTests` | the template validating to the four keys, its top level (`retention_days` 90) and a purpose with no digit outside the rule's path and the copy's name; every block error and its problem; a `jt` block beside another block refused (`JT_ALONE_PROBLEM`), and any `retention_days` but 90 (`JT_RETENTION_PROBLEM`); the plan's one unit in one `none` shard with nothing provisioned |
| `ImportTests` | in a fresh interpreter, no `tools` directory on `sys.path` and no download module loaded after importing the lab modules; `sys.path` unchanged after `pooled_export`, and no bare `nhtsa_export` or `vehicle_pack` left in `sys.modules` or importable |
| `DryRunTests` | the template's dry run: one unit, the jt lines, the unit `ok`, `no-model` measurement; the report block equal to the manifest's figures, verified, every check true; `aggregate.jt_block` on copies of the plan: not verified when `verify.json` says false, names another sha256 or other P_D hashes, or is of another kind; `PREREG_MISSING` and no sha when `prereg/jt/prereg.json` (re-pinned in the manifest) or the manifest's `jt` block names another sha256 than `terms.json`'s bytes; both summaries with every number traced to its file, the full sha256, both headings, the label, the note; no term, spelling, drafting record id or vehicle in any file outside `prereg/jt/` or in stdout; `--jt-data` refused with either other data flag |
| `A002GoldenTests` | step (0): the j3 template dry run's three outputs equal the recording after normalisation; the recording holds each token where expected and no `jt` key; a changed figure stays visible through the normaliser; a commit printed as `unknown` (a checkout without git metadata) normalises to the same token |

The other lab suites run unchanged but for these pins: `tests/lab/test_lab_docs.py` (the six JT labels, the four
troubleshooting rows, the new template, `request._JT_KEYS`, and the draft's timeout as this note and the reference
state it), `tests/lab/test_lab_shipped.py` (the template list),
`tests/lab/test_lab_guards.py` (the stub list of the name scan) and `tests/lab/test_lab_sim.py` (the experiments
message). `tests/lab/helpers.py`'s `PREREG_EXPERIMENTS` is not changed:
it lists the kinds `make_plan` preregisters without an archive, and a jt plan needs one.

Run in the build's last stage, on the tree of the build commit:

- `python -m pytest tests/lab/test_lab_jt.py -q -p no:cacheprovider`: `52 passed, 199 subtests passed in 93.42s
  (0:01:33)`;
- `python -m pytest tests/lab/test_lab_j3.py tests/lab/test_lab_j2.py -q -p no:cacheprovider`: `121 passed, 624
  subtests passed in 269.95s (0:04:29)` (65 and 56 tests, as at A002's fix stage: `lab/j2.py`, `lab/j2labels.py`,
  `lab/j3.py` and `lab/j3labels.py` unchanged);
- `python -m pytest tests/lab -q -p no:cacheprovider`, in the background: `898 passed, 29515 subtests passed in
  1711.18s (0:28:31)`;
- `python -m pytest tests/onboard tests/mycelic/test_collective_guards.py tests/mycelic/test_collective_x3.py -q -p
  no:cacheprovider`: `402 passed, 1080 subtests passed in 291.09s (0:04:51)`;
- the j3 template dry run (`python -m tests.lab.j3_data --out DIR`, `python -m lab.dryrun --request
  lab/templates/accuracy-002.json --out OUT --j3-data DIR`) exited 0 with the console lines `BUILD-A002.md` records,
  and its plan summary, `report.json` and `report.md`, normalised, equal the recording byte for byte (4161, 116487
  and 15701 bytes);
- `git diff --stat ed96530 HEAD` over every pinned path (`lab/j1.py`, `lab/goldlabels.py`, `lab/j2.py`,
  `lab/j2labels.py`, `lab/j3.py`, `lab/j3labels.py`, `lab/shard.py`, `mycelic/`, `connectors/openfda.py`,
  `research/`, the vehicle pack, `tools/`, every `CHOICE-*.md`, `run-*.json` and `*-settings.json`) printed nothing;
  the only hunks of `lab/aggregate.py` and `lab/summary.py` since `ed96530` are insertions after `j3_block` and
  `_j3_prereg_lines`, the docstrings, the imports, `ids`, the class-section dispatch of `render_report` and
  `_class_section` (one more argument and one more call), `_prereg_section`'s jt call, and `build_report`'s source
  loop, block call and report key (the sentence as first written left out the last two; corrected after review);
- `flake8 --max-line-length 120 --select=F,E9,W6` on `lab/jt.py`, `tests/lab/j4_data.py`, `tests/lab/test_lab_jt.py`,
  `tests/lab/a002_golden.py` and `tests/lab/stubs/jt_stub.py` printed nothing (on the changed lab modules it prints
  only `lab/units.py:1204` F841, which `ed96530` already has).

### The dry run

`python -m tests.lab.j4_data --out DIR`, then `python -m lab.dryrun --request lab/templates/terms-003.json --out OUT
--jt-data DIR` (the synthetic archive; every hash below is a synthetic file's or this commit's code's):

```
plan: 1 units in 1 shards (real)
jt export: archive_sha256 64ceaf17c20964b26487cc37c772b7acd25dcfdf84c961dd6a4a390eeb3e53fb archive_bytes 350681 rows FORD=119 CHEVROLET=118 JEEP=118 HONDA=119 NISSAN=117 DODGE=118 pooled 709 cross_make_duplicates 1 earliest_received 2020-01-01 latest_received 2022-12-31
jt drafting: rows 709 refs_sha256 871e368496deea733f38ca63bbc3d2b711d0bc18d57a670f3b1cd4dffab119fb drafting_refs_sha256 871e368496deea733f38ca63bbc3d2b711d0bc18d57a670f3b1cd4dffab119fb drafting_text_sha256 8ec76c8661175c6731715e5e85cf0a843d669b8351b17acd12b19c4dd3dcac7c narratives 707 straddling 2
jt draft: corpus 709 categories_passing_floor 11 specific_predicates 9 other_bucket_share 0.076 rows_without_specific 54 terms_candidates 987 terms_eligible 374 placeholders 1 refused_categories 0 refused_terms 6 assignable 6 within_cap 6
jt check: passed yes loader=passed ngram=passed refused_strings=passed refused_terms=passed template=passed term_floor=passed value_floor=passed
jt terms: hand_predicates 26 reached 8 empty 19 terms_min 0 terms_median 5 terms_max 9 terms_total 41 p_draft_config_hash 1c463c3cbce2544ea4c4f8a91b4499c85a1da8e2fdf2733a1ba693ee1154eaae p_draft_vocabulary_hash 7732443a016fc6e0a4f7b2a26fcb64068956531b568833a95e435c97881b4dde p_draft_detector_hash 39499e9cc0a09942239d42dacba991b02db401624f201af85b975e5a92f08416 p_draft_fixtures_hash f1cc423d0f97042b8b7c9b9c5ca35082307586a30cb4c2c57ee68b8419720e54 p_d_config_hash 22b9079fd68266413d6988185ac7f754590e4c8de4ffc191e8ace9665c3a16cc p_d_vocabulary_hash 6ce58f6393623d82a54dfcde2eb59d25da2c6552bb5e00efa174b6d3ae281420 p_d_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 p_d_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f hand_config_hash a588e72d7dbd0307d42234ceb79a85a915f0454d947469a4bfa0a42595f2f7bd hand_vocabulary_hash 867df7fc83c556e267842f219e1087b4bbd6af052ff5d201206d0aa8c789aa96 hand_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 hand_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f onboard_code_hash a1cf1bf6d4202823c4ab11d28d49fc46926319e897379964bc3f89d77b7f9c0b defaults_sha256 9ca66ffc98580f3e2f22da8d702b943db52ba198c79be14e763bd97ad8efc95b lang_en_sha256 9c76178a457bba9d4d9f98460a4d8263f5112b048b978cf651a46162bbd10cdb jt_code_hash f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76 terms_sha256 a5841e66d6b9ef564ef012eac3602bc4402ecbb52c03ea3ab9966f231c2d3c65
jt step: wall_s 0.5 draft_wall_s 0.4 draft_timeout_s 900 peak_rss_kib 36780
prereg: e1 no x1 no e2 no j1 no jt yes
lab: unit jt status ok class no-model exit 0 wall_s 0.2
lab: aggregate units 1 shards 1 class real measurements false lock unchanged
```

The figures are the fixed tree's (the fix stage re-ran the dry run on the extended synthetic archive; the build
commit's run, on the earlier archive, printed 615 rows, `straddling 1`, 7 reached predicates, 0 placeholders and
`terms_sha256 dd917796…`). One unit in one model-free shard, nothing provisioned; the export of 709 rows (the drafting
block of six makes, the boundary complaints of 2020-01-01 and 2022-12-31, and the cases of C4 and C28), its earliest
and latest received dates exactly the window's ends, one cross-make duplicate dropped and two straddling complaints;
707 distinct narratives (the two straddling complaints share one, and so do the two boundary complaints); 11
categories passing the floor, 9 of them specific predicates (the eight the hand pack maps and one it does not; the
other two are the non-specific catch-all and one below the 50-record minimum); one placeholder (the smaller twin,
reached with no term, its T entry empty); the six make words refused (`refused_terms 6`, all assignable and within the
cap); the check passed by check; 8 of the 26 hand predicates reached, 19 left empty (the 18 unreached and the smaller
twin); P_D's detector and fixtures hashes equal to the hand pack's; the unit verified the term set (`ok`, measurement
class `no-model`; the report's display class is `plumbing`, as every dry-run record is). No term, category spelling,
drafting record id, state or vehicle appears in the console, in either summary, in the report or in any shard file:
`DryRunTests.test_no_term_leaves_the_term_files` scans the dry run's output directory outside `plan/prereg/jt/` and
its stdout for the synthetic terms and the drafting ids and finds none. The synthetic archive's entry carries a fixed
timestamp (`j4_data.ZIP_DATE`), so its sha256 and every figure above repeat from one generation to the next (two runs
gave the same `terms_sha256`); `jt_code_hash` and `terms_sha256` change with `lab/jt.py`.

### Known limits of the build

- No real term run happened: the drafter, the check and the pooled export ran on 709 synthetic rows. On the real
  archive the pooled corpus is the union of D002's six training corpora; the step's time and memory are measured by
  the term run itself (the `jt step:` line) and the amendment records them (C11). If the draft does not fit in 900
  seconds or in the runner's memory, the plan stops with `JT_DRAFT_FAILED` and the remedy is a reviewed workflow
  change, never a drafter parameter.
- The step's wall time includes the archive's download when the term run is the first reader of the cached fetch (it
  is, in a jt plan).
- The peak resident memory is the draft subprocess's own; the parent's export in memory is not counted in it. On a
  stop, the `jt step: stopped` line's `children_peak_rss_kib` is the largest peak of any finished child of the
  `lab.prereg` process (`RUSAGE_CHILDREN`): in a term-only plan job the draft is the only such child, so it is the
  killed draft's figure; in a test process it can be another child's.
- `straddling` reads the raw archive with no product filter (see the choices); the judging run's removal by record id
  (C4, build 2) is what acts on it.
- The verify unit runs at the shard's commit, which in one run is the plan's: it cannot catch a term artifact read at
  a later commit; that is the judging run's check (`lab.j4terms`, build 2), which compares `lab/jt.py`'s code hash at
  its own commit (C29).
- `actionlint` is not installed here; the workflow is unchanged and checked by `tests/lab/test_lab_workflow.py`.

### Fixes after review

Built on branch `wf/a003-terms-fix` from the build commit `31cee9d`. The reviews of build 1 sent fifteen blocking
findings (several of them the same gap seen by two reviewers, each with its surviving mutants) and seventeen smaller
ones. Every one was applied; none was judged wrong, so none is recorded here as declined. Three were applied in a form
other than the one a finding offered first, each said below. Every code change has a test that fails without it
(the mutation run at the end of this section). The rule is unchanged: no finding needed a change of
`CHOICE-A003.md`, and none of the fixes touches a pinned path.

- **The drafting window's two ends** (blocking; mutants `first, last = _ymd(FROM), CUTOFF`, `first = "20190101"`,
  `first = "20200102"`). The synthetic archive had no complaint on 2019-12-31, 2020-01-01 or 2023-01-01, so the
  bounds `pooled_export` uses were unpinned. `tests/lab/j4_data.py` now holds one complaint received on each of
  2019-12-31, 2020-01-01, 2022-12-31 and 2023-01-01 (`BOUNDARY_ODINOS`, fresh record ids, six-make makes);
  `ExportTests.test_no_row_on_or_after_the_cutoff` computes from the archive's own lines the complaints of the six
  makes received from 2020-01-01 to 2022-12-31 and finds the export's ids equal to them, names the four boundary
  complaints in or out, pins the export's earliest and latest received dates to `2020-01-01` and `2022-12-31` and the
  exact row count (`facts.json` carries the two dates and `straddling`).
- **`straddling` at the cutoff day and across makes** (blocking, two findings; mutants `day > CUTOFF` and the
  six-make filter removed; smaller finding E). The straddling complaint's later row moved from 2023-02-01 to
  2023-01-01, a second straddling complaint was added (its later row 2023-02-01), and a drafting complaint whose later
  row is filed under a seventh make (`OTHER_MAKE`). `straddling` is 2 in `ExportTests.test_straddling` (computed from
  the archive's lines too), in `test_the_drafting_files`, `terms.json` and the fixed line; the straddling
  complaint's 2023-01-01 row is shown absent from the export.
- **The placeholder left out and the twin split** (blocking, two findings; mutants `d.lexicon[pid]` and a
  placeholder for an empty learned list; the smaller finding on the twins' cue words). `SERVICE BRAKES` (18 per make)
  and `SERVICE BRAKES, HYDRAULIC` (9 per make, 54 records) now share their four cue words: each goes to the larger
  twin, and the smaller one is a specific drafted predicate that learns no term (`placeholders` 1). New tests: the
  placeholder left out (`drafted_id` set, at least 50 corpus records, the empty list, P_draft's lexicon holding the
  drafter's placeholder, no T term with the template's placeholder prefix, P_D giving it `draft.placeholder(<hand
  id>, ids)`) and the twin split (every shared cue word in the larger twin's list only, the two lists disjoint, no
  other entry holding one). Applied in another form: the smaller finding asked for shared cue words on the `ENGINE`
  pair; they are on the `SERVICE BRAKES` pair, which also gives the placeholder case, and the `ENGINE` pair keeps
  disjoint cue words, so the data still holds twins that are both reached with their own terms.
- **The draft's timeout pinned** (blocking, two findings; mutants dropping `timeout_s` and `timeout_s=3600`; the
  smaller finding on the 900 s figure). The `sleep` stub now sleeps an hour and then runs the real draft, which
  would pass, so only the timeout can refuse it. `test_a_timeout` patches `DRAFT_TIMEOUT_S` to 3, spies on
  `_Steps.run` (the `jt-draft` call gets `timeout_s` 3 and raises "jt-draft did not finish in time"; any other
  timeout fails the test at once instead of waiting), and times the run under 60 s;
  `test_the_real_timeout_is_handed_to_the_step` pins the real draft's call to `DRAFT_TIMEOUT_S`; `SettingsTests` pins
  900, and `tests/lab/test_lab_docs.py` pins this note's and the reference's sentence to the constant.
- **A crash is never a failed check** (blocking; mutant `code == EXIT_FLOOR` without `and good`; the smaller finding
  on uncaught exceptions). `EXIT_FLOOR` is 5, not 1 (Python's own exit for an uncaught exception); any other error in
  the draft is caught and exits `EXIT_CRASH`, 6, with one stderr line naming the exception's class and never its
  message (the message could quote the export). New stub commands: `crash` (the draft raises, exit 6), `uncaught`
  (an exception before the draft, exit 1), `floorless` (the check's exit code with no `draft.json`) and `unchecked`
  (exit 0 with a check that did not pass, smaller finding G): each gives `JT_DRAFT_FAILED`, nothing under
  `prereg/jt/` and no scratch directory left; the exit-code test pins 0, 2, 3, 4, 5 and 6 and that none is 1.
- **The peak memory** (blocking; mutant `peak = 0`). `DraftTests` pins the draft's `peak_rss_kib` above 10,000 KiB;
  `PreregTests` pins the manifest's `jt.step.peak_rss_kib` above it too and equal to the number at the end of the
  `jt step:` line.
- **The parameters, roles and window handed to the drafter** (blocking; mutants changing `token_min_letters` and
  dropping `vehicle` from the forbidden roles). The test compares the whole parameter set handed to the draft with
  `load_params()`, `defaults.json` and `D002-settings.json`'s `params` (equal key for key), checks the check gets the
  same object, and the roles and window handed to both equal `lab.jt.roles()` (forbidden `reporter` and `vehicle`)
  and `lab.jt.window()`. The narratives of the synthetic `TIRES` complaints now carry their row's make in lower case:
  the refusal removes the six make words (`refused_terms 6`), no T term holds one, and the same export drafted with
  `vehicle` not forbidden gives them to that category, so the vehicle refusal is shown in effect.
- **The code hash is `lab/jt.py`'s** (blocking; mutant hashing `lab/j1.py`). `test_the_code_hash_is_lab_jt_s` pins
  `code_hash()` to `common.code_hash([ROOT / "lab/jt.py"], ROOT)`, and with `ROOT` patched to a scratch tree shows a
  byte-equal copy keeps the hash and one changed byte changes it.
- **The drafter's counts are `draft.summarise`'s** (blocking; mutant swapping the candidate and eligible counts).
  `test_the_counts_are_draft_summarise_s` drafts the same export and compares every key of `draft_counts` with its
  `draft.summarise` path.
- **The unit's own checks** (blocking, and the smaller findings F, R and M). New `VerifyTests`: a changed
  `drafting_text.jsonl` (only `drafting_files` fails), other hand pack hashes in `terms.json` pinned again and in the
  preregistration (only `hand_hashes` fails), other P_D hashes in the preregistration (only `p_d_hashes` fails).
- **The report's `verified` and its sha cross-check** (blocking, and the smaller findings H and N).
  `DryRunTests.test_the_report_block_reads_verify_json_and_the_preregistration` calls `aggregate.jt_block` on copies
  of the dry run's plan: `verified` false when `verify.json` says false, names another sha256 or other P_D hashes, or
  is of another kind; `PREREG_MISSING` with no sha (and `verified` null) when `prereg/jt/prereg.json`, re-pinned in
  the manifest so that the manifest check passes, or the manifest's own `jt` block names another sha256 than
  `terms.json`'s bytes. `lab/aggregate.py` is unchanged.
- **`check.json`** (smaller). Applied in the first form the finding offered: `prereg/jt/check.json` is now
  `check_counts(draft["check"])`, the same booleans and counts by check as `terms.json`'s `check`, never the check's
  own document (its pack id, the loader's error text, the n-gram scan's file names); a test finds no string in it.
  The reading under "Ambiguities" and `REFERENCE.md` say so.
- **The stop lines** (smaller). A failed check's `jt check: passed no ...` line ends with each failing check's
  `failures` count (`ngram_failures 1`); every stop of the step prints `jt step: stopped wall_s ... draft_wall_s ...
  draft_timeout_s ... children_peak_rss_kib ...`, the last the parent's `RUSAGE_CHILDREN` peak, which covers a
  killed draft (C11: the amendment records the attempt).
- **Failures in the plan job's own process** (two smaller findings). A download that fails or an archive that is not
  a readable zip (absent, not a zip, truncated) now stops the plan with a new note, `JT_FETCH_FAILED` ("the plan job
  could not fetch or read NHTSA's complaint archive; nothing was drafted, and the same request may run again", as
  L1's `L1_FETCH_FAILED`); an export the parser refuses, a P_D rebuild the loader refuses, or a `draft.json` without
  the fields `terms.json` needs, with `JT_DRAFT_FAILED`, whose sentence now also names an error and an unreadable
  result. Each was a traceback with no `plan-error.json` before. The note, its troubleshooting row and its reference
  line are added, and the docs tests' lists gain it.
- **The parsed export holds every pooled row** (smaller). Applied in the second form the finding offered, a refusal:
  `pooled_export` raises `JtError` (so `JT_DRAFT_FAILED`) when `parse_export` rejects a row or holds another number of
  rows than `nhtsa_export.complaints` gave, so the drafting files, read from the parsed export, are always the
  complaints' rows, as build 2's rebuild will read them. Computing the files from the pooled rows instead would let
  the drafter read fewer rows than the files name; D002's NHTSA drafts read 40,000 rows per make with no rejected row
  (`BUILD-D002.md`), so the refusal is not expected to fire on the real archive. A test with a parse that drops a row
  and one that rejects a row pins it.
- **The term request alone, 90 days** (smaller). `lab.request.validate` refuses `$.experiments` when `jt` sits
  beside another block (`JT_ALONE_PROBLEM`, item 10: "a request whose only block is `experiments.jt`") and
  `$.retention_days` other than 90 with a `jt` block (`JT_RETENTION_PROBLEM`, C15); `RequestTests` covers both.
- **No bare download module left behind** (smaller). `_fetch_module` drops `nhtsa_export` and `vehicle_pack` from
  `sys.modules` after the load unless they were there before; `ImportTests.test_the_export_leaves_no_bare_module`
  finds neither in `sys.modules` nor importable after `pooled_export` in a fresh interpreter.
- **The golden normaliser in a checkout without git metadata** (smaller). Both commit patterns accept `unknown`; the
  module docstring says so, and `A002GoldenTests` pins that a commit printed as `unknown` normalises to the same token.
  The recording is unchanged.
- **The hand label refuted on a reached predicate** (smaller). `test_the_drafted_lexical_judge` adds the case: a
  narrative holding only the hand label of a reached predicate whose drafted list lacks it gives the drafted judge
  `no` and the hand judge `yes` (more than five such questions).
- **This note** (two smaller findings). The sentence on the hunks of `lab/aggregate.py` and `lab/summary.py` now
  names `_prereg_section`'s jt call and `build_report`'s source loop, block call and report key; the Tests table,
  the item 3 bullet and the dry run name what the tests now pin, and this section records the mutation run.

The workflow is unchanged: the draft's timeout, 900 s, sits inside the `prereg` step's 20 minutes, and nothing in C11
asks build 1 for another step or timeout.

**The mutation run.** Each mutant was applied alone to the fixed tree, `tests/lab/test_lab_jt.py` was run with `-x`
(without the golden class, but for the normaliser's mutant, run against the golden class), and the file was put back.
All 37 were killed: the reviewers' `first, last = _ymd(FROM), CUTOFF`, `first = "20190101"`, `first = "20200102"`,
`day > CUTOFF`, the six-make filter removed, `d.lexicon[pid]`, a placeholder for an empty learned list, `peak = 0`,
`token_min_letters` 4, `vehicle` not forbidden, the code hash of `lab/j1.py`, the candidate and eligible counts
swapped, `hand_hashes` forced true, the drafting text clause removed, `verify` ignoring the preregistration's P_D
hashes, the `timeout_s` argument removed, `timeout_s=3600`, `code == EXIT_FLOOR` without `and good`, the `passed`
gate removed, `jt_block` ignoring `verify.json`'s `verified`, its sha256 and its P_D hashes, and the sha
cross-check removed; and the fix stage's own: `EXIT_FLOOR` back to 1, the draft's catch-all removed, the parsed-row
refusal removed, the `sys.modules` clean-up removed, `check.json` the check's whole document, no failure counts, a
failed download uncaught, an unreadable archive reported as a draft failure, a P_D failure uncaught, no stop line, a
`jt` block allowed beside others, any `retention_days`, and the normaliser without `unknown`.

Run on the fixed tree:

- `python -m pytest tests/lab/test_lab_jt.py -q -p no:cacheprovider`: `71 passed, 271 subtests passed in 101.03s
  (0:01:41)`;
- `python -m pytest tests/lab/test_lab_j3.py tests/lab/test_lab_j2.py -q -p no:cacheprovider`: `121 passed, 624
  subtests passed in 271.23s (0:04:31)`;
- `python -m pytest tests/onboard tests/mycelic/test_collective_guards.py tests/mycelic/test_collective_x3.py -q -p
  no:cacheprovider`: `402 passed, 1080 subtests passed in 279.34s (0:04:39)` (run again after this section was written:
  the collective guards scan it);
- `python -m pytest tests/lab -q -p no:cacheprovider`, in the background: `918 passed, 29673 subtests passed in 1659.12s (0:27:39)`, no test failed;
- the terms-003 dry run above: exit 0, one unit in one shard, the check passed, the unit `ok`, `terms_sha256
  a5841e66…` twice from two generations of the data;
- the j3 template dry run: exit 0 with `BUILD-A002.md`'s console lines, and its plan summary, `report.json` and
  `report.md`, normalised, equal the recording byte for byte (4161, 116487 and 15701 bytes);
- `git diff --stat ed96530` over every pinned path (and `.github/workflows/mycelic-lab.yml`) printed nothing, and
  `lab/aggregate.py`, `lab/summary.py`, `lab/units.py`, `lab/plan.py` and `lab/dryrun.py` are unchanged since
  `31cee9d`;
- `flake8 --max-line-length 120 --select=F,E9,W6` on the changed files printed nothing.

## Build 2: the judging kind `j4`

Built in two stages from `831f424` (the loop status after the term run, which carries the amendment "Amended after
the term run, before build 2", T1 to T11, `3d73f93`): stage 1, the kind's core (`lab/j4.py`, `lab/j4labels.py`,
`lab/j4terms.py`, the extended `tests/lab/j4_data.py` and the core tests), ending at `c1d4fec`; stage 2, the wiring,
on branch `wf/a003-wire` from `c1d4fec`. Build 2 changes no pinned file: `lab/j1.py`, `lab/goldlabels.py`,
`lab/j2.py`, `lab/j2labels.py`, `lab/j3.py`, `lab/j3labels.py`, `lab/shard.py` and `lab/jt.py` (its code hash still
`f5743df5…3d76`, T7) are imported unchanged, nothing under `mycelic/`, `connectors/openfda.py`, `research/`, the
vehicle pack or `tools/` (loaded by path only) changed, no `CHOICE-*.md` (a rule change would be reported, not made;
none was needed), no `run-*.json` or `*-settings.json`, and neither `lab/templates/terms-003.json` nor a
`lab/requests/` file. `lab.aggregate.j2_block`, `lab.summary._j2_tables` and every j3 and jt function of
`lab/aggregate.py` and `lab/summary.py` are not edited: the j4 code is inserted after them. While building, no NHTSA
record was read (the sandbox reaches no NHTSA host; every run read `tests/lab/j4_data.py`'s synthetic archive, every
value invented), no earlier run's or term run's artifact was downloaded (synthetic artifacts stood in), and no model
ran (the lab's fake server answered every call). The synthetic term set's terms are `j4_data`'s invented cue words;
no test, console line, summary, report or note prints one.

### The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` (`_j4`, `_j4_exclude_list`, `_j4_exclude_entry`, `_j4_terms`) | an `experiments.j4` block of `_J4_KEYS`, `_J4_REQUIRED` with `base_task` and `terms`; `base_task` one of `lab.j4.BASES` (`J4_BASE_PROBLEM`); `exclude` exactly three entries in seed order, each with j3's entry keys, its file and rebuild seed pinned per entry (`J4_EXCLUDE_ROWS`: `prereg/j1/labels.jsonl` and 1, `prereg/j2/labels.jsonl` and 2, `prereg/j3/labels.jsonl` and 3; `J4_FILE_PROBLEMS`, `J4_SEED_PROBLEM`), the chain keys on the second and third entries only, each naming its own cycle's chain file (`J4_CHAIN_FIRST_PROBLEM`, `J4_CHAIN_REQUIRED`, `J4_CHAIN_FILE_PROBLEMS`); `terms` of `_J4_TERMS_KEYS`, the artifact of the lab's shape for the run id (`J4_TERMS_ARTIFACT_PROBLEM`), `file` exactly `prereg/jt/terms.json` (`J4_TERMS_FILE_PROBLEM`), `sha256` and `vocabulary_hash` 64 lower-case hex digits (`SHA256_FULL_RE`, `J4_HEX_PROBLEM`), `cutoff` exactly `2023-01-01` (`JT_CUTOFF_PROBLEM`) (C23); `primary` through `_primary` with the branch's four arms, then one of `lab.j4.primary_arms(base)`: `<base>_terms`, or `evidence` under base `evidence` (C1; `J4_PRIMARY_ARM_PROBLEM`); `EXPERIMENTS` gains `j4` last |
| Plan | `lab/plan.py` | `build_units`' j4 branch, one unit per model and part, its params carrying `base_task` and `terms` beside j3's (C27); `exclude_entries` giving the three entries; `terms_entry`; `plan_outputs` and `OUTPUT_KEYS` gaining `j4_exclude_run_id_3`, `j4_exclude_artifact_3`, `j4_terms_run_id` and `j4_terms_artifact`, empty for every other plan |
| Workflow | `.github/workflows/mycelic-lab.yml` | two plan-job steps after `earlier-2` and before `prereg`: `earlier-3` (keyed on `j4_exclude_run_id_3`, into `${{ runner.temp }}/lab-earlier/<run id>`) and `terms` (keyed on `j4_terms_run_id`, into `${{ runner.temp }}/lab-terms/<run id>`), both `actions/download-artifact` at the earlier steps' pinned sha, `github-token: ${{ github.token }}`, `continue-on-error: true`, under the workflow's read-only `actions: read` |
| Preregistration | `lab/prereg.py` (`_j4`) over `lab/j4terms.py`, `lab/j4labels.py`, `lab/j4.py` | in the rule's order: the term check (`check_terms` over `--terms-plans`, default `lab-terms` beside the plan directory; `J4_TERMS_UNVERIFIED` after the `j4 terms:` line); the eight sources and the stop rules (`J4_FRESHNESS_UNVERIFIED`, `J4_EXCLUSION_DISAGREES`, after the `j4 exclusion:` lines); the split and the draw (`J4_TOO_FEW_RECORDS`, `J4_SPLIT_VIOLATED` after the `j4 split:` line); P_D rebuilt from the verified term set and checked against the term check's hashes; J001's questions; the three lexical judges, the control, the bound and the judges' pairs; the files under `prereg/j4/` (the term run's `terms.json` copied byte for byte); `preregister`'s unit list, its `terms_plans` and `terms_stand_in` arguments and the manifest's `j4` key (`null` for every other plan); `load_stand_in` taking a three-entry list for j4 (its third entry may carry `questions_sha256_prefix`); `main()`'s `--terms-plans` and `--terms-stand-in` and the `prereg:` line's ` j4 yes` |
| Unit | `lab/units.py` | `ADAPTERS["j4"]` (`lab.j4 run`, the four task names as its routed tasks, boundary `site:lab`, J001's deadline, `run.json`, the three collected files); `required_tasks` reading `params.base_task` and the primary arm: the first pass's task (D, or B in the confirmation case); `build_argv` passing the budget and `--terms` (the plan artifact's `prereg/j4/terms.json`); `write_routing` routing the branch's two tasks (`lab.j4.routing_doc` with `params.base_task`); `_j4_status` (exit 1 with the first pass of `pass_order` finished and the second not is `ok` with `J4_TERMS_STOPPED`); `participation`, `harness_measurement`, `harness_verdict`, `unit_notes`, `public_text`, `PREREG_EXPERIMENTS`, `REPLY_PARTICIPATION`, `PASS_EXPERIMENTS`, `run_unit`'s budget and `with_pack` lists |
| Warm-up | `lab/warmup.py` (`j4_tasks`) | task B with the judge's generated payloads (the probe: B sorts first); task D, only when the preregistration's `terms.json` is there, with the same payloads plus `predicate_terms`: the typical one its asked predicate's list, the worst case asked about the predicate whose list is the longest by characters, carrying that list (C12); the warm-up record keeps the lists' counts and predicates (`WarmTask.terms`), never a term |
| Fake provider | `lab/responder.py` | handlers for `judge_record_negation_terms` (as `judge_record_negation`) and `judge_record_evidence_terms` (as `judge_record_evidence`, behind an empty citation), in the pack branch and the pack-free one |
| Aggregate | `lab/aggregate.py` (`j4_block` and its helpers) | the manifest cross-checked against `prereg/j4/prereg.json` (base task, primary pair, headline arm and secondary, the other reading's arm, the confirmation case, the four comparator files' sha256, the term pin) and the term set's bytes against the pin, P_D rebuilt with the pinned hashes (else `PREREG_MISSING`); the parts and the two sets; the line check with the reading, `grounded` and each task D line's terms count and sha256 recomputed; the four arms beside the three lexical judges, the control and the bound on the same records, each paired with each judge; the verdict word of C19 and the word under lever 5's text alone (C6) for every arm; the lifts; the agreement readings with both drafted judges (C5, trap 6); the empty lists; branch E's citation readings (C16); the first-key shares and the token figures; the controls beside the earlier cycle's figures; `primary_result` with the headline secondary's and the other reading's words; the report key `j4` only when the plan has J4 units (C30) |
| Summary | `lab/summary.py` | `J4_ARMS` per branch, `J4_LIFTS`; the plan summary's J4 lines (labels, the exclusion with eight sources, three cycles, seven consistency counts, the split's counts and hashes, the term pin, the primary pair, the three lexical judges, the control, the bound and the judges' pairs, the exclusion and terms notes); the report's nine sections in the J4 block's class section (`_j4_tables`); every number through `Sources.num`; the `MYCELIC-LAB` markers unchanged |
| Notes | `lab/notes.py` | `J4_FRESHNESS_UNVERIFIED`, `J4_EXCLUSION_DISAGREES`, `J4_TOO_FEW_RECORDS`, `J4_TERMS_STOPPED`, `J4_LABEL`, `J4_ARMS_NOTE`, `J4_VERDICT_NOTE`, `J4_PRIMARY_LINE`, `J4_TERMS_NOTE`, `J4_EXCLUSION_NOTE`, `J4_STAND_IN` beside stage 1's `J4_TERMS_UNVERIFIED` and `J4_SPLIT_VIOLATED`; nine headings; the j4 columns; no ASCII digit in any of them |
| Dry run | `lab/dryrun.py` | `--j4-data DIR` hands `complaints.zip`, `earlier/`, `stand-in.json`, `terms/` and `terms-stand-in.json` to `lab.prereg`; refused with `--j2-data`, `--j3-data` or `--jt-data` (C27), the existing messages unchanged |
| Template | `lab/templates/accuracy-003.json` | the rule's request, to copy to `lab/requests/accuracy-003.json` |
| Docs | `docs/lab/REFERENCE.md`, `docs/lab/README.md`, `lab/requests/README.md`, `docs/lab/INTEGRATION.md`, `lab/__init__.py` | the j4 block and its plan job, unit and aggregate; the J4 unit's files; the statuses; every J4 label; the columns and sections; the two download steps; six troubleshooting rows; INTEGRATION's section 12 (the term run and the judging run) |

### How each rule is carried out (stage 2)

- **"The request" and C23.** The template is the rule's request as amended after the term run: one model, `a-4b`;
  `labels` nhtsa, the vehicle pack, 150 records at seed 4; 5 parts at seed 4; 300 minutes per part in jobs of 330
  minutes, five at once; `bootstrap_b` 10000 at seed 4; `base_task` `negation` (branch N, in force: A002 run 1's P was
  `failure` and `E_minus_N`'s lower end below zero, so branch E's trigger did not fire); the three exclude entries as recorded (J001 run 37996675559, A001 run
  38114522160, A002 run 38439612256, with their prefixes); the term entry of T1 (run 38612800578, its plan artifact,
  the term set's sha256 `42c591cc…39cc`, P_D's vocabulary hash `c0260f0e…e579`, the cutoff); the primary pair
  (`a-4b`, `negation_terms`); `comparisons_before` 21. Its purpose names the rule's path and the copy's name and
  holds no other digit.
- **C27, the plan's outputs and the workflow.** The plan echoes the three excluded runs and the term run; each pair is
  empty for every other plan, which skips its step; the four download steps carry the workflow's token, which now
  appears in exactly five places (the provision step's environment and the four `github-token` inputs).
- **Item 10 and C29, the term check first.** `_j4` checks the term artifact before it reads any earlier artifact or
  draws anything; anything but `verified` prints the one `j4 terms:` line (outcome, the check that failed, both
  sha256, P_D's hashes and both code hashes) and stops with `J4_TERMS_UNVERIFIED`.
- **Item 4, C3 and C20, the eight sources.** `lab.j4labels.establish` over the three entries; one `j4 exclusion:` line
  per source that did not verify, then the cycles' line with the seven consistency counts; the stops as the rule
  says.
- **Items 5 to 8 and C4, the split.** `lab.j4labels.draw` with the term check's drafting set; the `j4 split:` line
  (both archives' sha, the term run's and the rebuilt rows and refs sha, the removals, the drafting records already
  excluded, both tripwires) printed before a `J4_SPLIT_VIOLATED` stop and after a good draw.
- **Rule 8 and C5, the three lexical judges.** `lab.j4.comparator_lines` writes each judge's lines; each is scored on
  every record with its unknown count, and the three are paired with each other; the plan job prints them first.
- **Rule 15 and C1, the pass order.** The unit's required task, its status reading and the participation floor all
  follow `run.json`'s `pass_order`: the first pass is the primary pair's task, D, or B in the confirmation case.
- **Rule 16 and C12, the warm-up.** As the table says; the worst case's list is the longest by characters, and the
  warm-up asks about that predicate so the payload is the one a question of it would send.
- **Rule 13, C6 and C19, the words.** Per arm the aggregate computes `lab.j4.verdict_word` and `lab.j4.lever5_word`
  from the arm's figures and the three judges' paired differences; the primary pair's sentence prints both words,
  every lexical figure and the other reading's word.
- **C16, branch E's citations.** For both tasks under base `evidence`, the grounded share of yes answers per kind and
  the grounded citations that equal or hold a drafted term of the asked component; the table appears only under
  branch E.
- **C30, A002's re-aggregation.** The report key `j4` is added only for a plan with J4 units, and the manifest gains
  `"j4": null` for every other plan (the manifest is not among the golden outputs): `A002GoldenTests` keeps passing.

### Choices the rule left to the build

- **The rebuild seeds are pinned exactly** (1, 2 and 3, per entry), not only ordered as j3 orders its two: each entry
  is a recorded cycle whose own draw seed is known.
- **The required task is the first pass's task**, D under the primary arm `<base>_terms` and B in the confirmation
  case; the build plan's "returning the D task" is that case's general form.
- **`Adapter.routed_tasks` holds the four task names** (both branches), a static tuple as the adapter needs, while
  `write_routing` routes only the branch's two from `params.base_task`.
- **Task D is warmed only with the preregistration's term set.** Without `prereg/j4/terms.json` (a warm-up called
  without the preregistration) task B alone is warmed, rather than task D with an empty list, which would not be the
  payload the run sends.
- **The earlier cycle's figures are code constants** (`lab.j4.EARLIER_ARMS`): A001's B and Br under branch N (run
  38114522160: 0.577 [0.530, 0.623] and 0.653 [0.610, 0.697]), A002's under branch E (run 38439612256: 0.540
  [0.503, 0.577] and 0.553 [0.517, 0.590]), as their recorded runs give them; the between-draw difference is printed
  beside them and decides nothing.
- **The fixed lines' order**: exclusion, draw, split, terms, comparators after a good preregistration; the term line
  first and alone when the term check fails or P_D does not rebuild (nothing else ran); after a later stop the lines
  printed so far (the sources' and the exclusion lines, the draw's sentence, the split line) and then the term line
  last (fix stage).
- **The warm-up's term counts are a console line** (C12, fix stage): `lab.warmup.warm_up` prints, for each warmed
  task D, `lab: warmup <task D> typical_terms N typical_predicate P worst_terms M worst_predicate Q
  worst_prompt_tokens T max_tokens K slot S fits yes|no`, the counts and hand predicate ids of the two payloads, the
  worst case's counted prompt and whether it fits the slot (`none` when the server did not count it); the row of the
  warm-up record keeps the same counts. Counts and ids only: a term is never printed. The shard summary and the report
  do not repeat them (the console log is the shard's own record of its warm-up).
- **The negated variant of the synthetic archive** (fix stage): the judging kind's dry runs read
  `j4_data.archive(negated=True)`, in which every other window row holding the engine narrative negates its drafted
  cue word, so that the drafted lexical judge (with the pack's negation cues) and the term-presence judge (without
  them) answer apart; the default archive, the term run's dry run's, is byte for byte build 1's.
- **The stand-in's third entry may carry `questions_sha256_prefix`**, the synthetic A002 artifact's questions file
  prefix, popped before the request's entry shape is validated and handed to `establish` in place of A002's pinned
  prefix (C3); the real request has no such key.
- **The comparisons this cycle are four per model**, the arms of the branch, as A001 and A002 count theirs; the
  template's one model gives 4 and 25 since the loop began. The preregistration and the manifest record the branch's
  arms times the models (`lab.j4.comparisons_this_cycle`), and the aggregate refuses a block whose count differs
  (fix stage: build 2 recorded 4 whatever the model count).
- **The agreement readings are per kind** (positive and negative questions apart) for the primary arm against the
  drafted lexical judge and the term-presence judge, with the equal share and both confirm rates (trap 6).
- **The stand-in note comes after the J4 lists**, not between the exclusion and the term pin, in both summaries.
- **`tests/lab/helpers.py`'s `PREREG_EXPERIMENTS` gains `jt` and `j4`**, as the build plan's test list asks. Build 1
  left `jt` out because `make_plan` preregisters without an archive; no test plans a jt or j4 request through
  `make_plan`, so no test changes behaviour, and the list now names every kind the plan job preregisters.
- **INTEGRATION's new section covers the term run too** (build 1 added none), since the judging run reads its
  artifact.

### Ambiguities and readings

- **"the plan's six outputs"** (the build plan's test list) is read as the j4 plan's exclusion and term outputs: the
  test pins all eight `j2_`, `j3_` and `j4_` outputs on a j4, a j3, a j2 and another plan.
- **"no ref and no term anywhere"** is read as anything a reader sees: the console, both summaries and their sources
  files, the report and the shard summaries. The plan artifact's data files hold refs and terms by design
  (`prereg/j4/labels.jsonl` holds the drawn narratives, `terms.json` the term set, the question and comparator files
  and `excluded.jsonl` the refs), and the shards' verdict lines hold refs; the dry-run tests scan the whole output
  tree and find refs or terms in those files only.
- **"every word incomplete"** covers both words of every arm (the C19 word and the word under lever 5's text alone):
  the dry runs' records are plumbing, never a model measurement.
- **The term pin's "verified"** in the report is the aggregate's own reading (the bytes hash to the pin and P_D
  rebuilds with the pinned hashes), not the plan job's printed outcome.

### Deviations

None of the rule's substance. The build plan names `_primary` "accepting `evidence` with `base_task` `evidence`"; the
request calls `_primary` with the branch's four arms (its own check) and then narrows to `lab.j4.primary_arms(base)`,
so `negation_terms_mapped` or `evidence_grounded` as a primary arm is refused with `J4_PRIMARY_ARM_PROBLEM` and an
arm of the other branch with `_primary`'s own problem.

### Tests

`tests/lab/test_lab_j4.py`, offline: the synthetic archive and artifacts of `tests/lab/j4_data.py`, the collective's
fake server, no network host. 92 tests: 45 from stage 1, 31 from stage 2 and 16 from the fix stage (the table names
what the fix stage added; "Fixes after review" says which finding each answers).

| Class | Pins |
|---|---|
| `RuleTests` (stage 1) | task D's text by C32's construction under both branches, every `rule N` reference, the tasks' schemas and caps, the payload the shipped one plus `predicate_terms` and nothing else, the arms, lifts and readings as the pinned functions, the primary pair and the confirmation case, the routing of the branch's two tasks, `lab/jt.py`'s frozen code hash (T7), the term set and P_D |
| `LineTests`, `VerdictWordTests`, `ComparatorTests` (stage 1) | every verdict the pinned `decide`; the four arms' views; the line check's every outcome; every cell of C19 and `lab.j2.verdict_word` where the added clauses hold; the comparators as the pinned functions and the term-presence judge confirming a negated mention the lexical judge refutes (C5) |
| `LayoutTests`, `SourcesTests`, `TermsCheckTests`, `SplitTests` (stage 1) | the three layouts case for case with the pinned functions, A002's questions prefix (C3); the eight sources and every stop rule (C20); the term check's every outcome (C29); the split's removals, the rebuild, the tripwires and the draw disjoint from the three synthetic draws with its 600-line chain plus the removals (C4) |
| `PreregDocTests`, `RunTests` (stage 1) | the preregistration document; the runner through the fake server: two passes, D first (B first in the confirmation case), every pin refused before any call, a stop in each pass, the server staying down |
| `RequestTests` | the template validating to its block, its top level and a purpose with no digit outside the two paths; every block error and its problem (base task, the list's length, each entry's file, seed and chain keys, the term entry's file, hexes, artifact and cutoff, the primary arm and model, the labels, the counts); the confirmation case and branch E's arms; fake models accepted and a hosted one refused |
| `PlanWiringTests` | the template's 5 units in 5 shards with their params; `plan_outputs` (and the written `--github-output`) on a j4, a j3, a j2 and another plan |
| `UnitWiringTests` | the adapter and the lists; `_j4_status` on every pairing, `J4_TERMS_STOPPED` under both pass orders; the floor judging the second pass only when it finished; a unit run on the fake provider: `ok`, `plumbing`, the files, `--terms`, the routing of D and B only, 40 attempts per task, D's lines first with their term counts and B's without |
| `WiringTests` | both fake handlers (D as B, behind an empty citation under E, both branches of the responder); the warm-up's two tasks, the probe B, D's typical and worst payloads (the longest list by characters), no D without the preregistration; the warm-up on the stub server (fix stage): task D's row and its one `lab: warmup` line with the term counts, predicates, counted prompt and `fits`, no term; the two workflow steps; no digit in any J4 note, heading or column and the primary line's placeholders; `--j4-data` refused with each other data flag |
| `PreregWiringTests` | `lab.prereg`'s J4 step in process: the files, `terms.json` byte for byte, the manifest block, the draw, questions and comparator scores recomputed (fix stage), the comparisons for two models, another kind's manifest without `j4`; each seed feeding its own step (fix stage); the six fixed lines in order with their figures (B1's questions prefix, P_D's four hashes), no ref and no term in them; a term set that does not verify, or a P_D that does not rebuild, stopping after one line, before any draw; every later stop (fix stage: no cycle established, a consistency count, too few records, a tripwire, a drafting set that does not rebuild, an overlap) with its path, note and lines, the term line last; the request's own entries checking B1 against A002's prefix; a removal by record id on an archive with one more straddling complaint |
| `AggregateWiringTests` | DryTree copies of the small dry run: a-4b answering D right and B as the hand judge gets `pass_provisional` (both words) for D and Dr and (`failure`, `partial`) for B and Br, every arm's paired figures, pass-test differences and both words recomputed (fix stage), `D_minus_B` helps; the primary pair's two words apart and printed each in its place (fix stage); a stop in the second pass keeps D's set and P's word; lines and a run.json the runner could not have written (fix stage); the agreement, the empty lists, the three headlines and a transport failure (fix stage); every manifest disagreement (primary arm or model, base task, headline secondary, other reading's arm, term sha, the confirmation flag, the comparisons), the document's term pin and comparisons, P_D rebuilt otherwise, and a restamped comparator or term file give `PREREG_MISSING`; no preregistration |
| `SmallDryRunTests`, `TemplateDryRunTests` | the dry runs (on the negated variant, fix stage) of a two-model request of 40 records in 2 parts and of the template: the console lines (B1's questions prefix, P_D's four hashes), every unit `ok` and `plumbing`, the report block against the manifest (every arm the hand judge line for line, every word `incomplete`, the lifts 0 and not told apart, the comparisons in the report, the manifest and the document, A001's own figures and the between-draw difference's sign, `presence_minus_drafted` not 0), both summaries with every number traced to its file, the headings and notes, B1's questions prefix, the primary line's three judges each from its own figures, no ref (every complaint id of the archive) and no term in anything a reader sees and refs or terms only in the data files that hold them by design; the template's 5 shards and 25 comparisons and its draw and questions recomputed |

Some of the term check's checks are redundant with the file hashes, and the tests cannot tell them apart (equivalent
survivors of the build reviews' mutation run): C11 and C12, `refs_file_sha256` and `refs_sha256`, are equal by
construction (T4), so either check alone would do; C15 and C16, the narratives count and the line count, are
redundant while `text_file_sha256` is checked (the fix stage pins that check); C20, a non-string term, fails later
anyway as malformed in `p_d_hashes`. A later edit must not drop a hash check and keep only the counts.

The other lab suites run unchanged but for the pins the build plan names: `tests/lab/test_lab_workflow.py`
(`github.token` in exactly five places; `github-token` on `earlier`, `earlier-2`, `earlier-3` and `terms`; seven
`download-artifact` steps with a `name`, three of them the run's own; the order `plan`, `earlier`, `earlier-2`,
`earlier-3`, `terms`, `prereg`; the two new steps' shape), `tests/lab/test_lab_plan.py` (the four output keys, in two
places), `tests/lab/test_lab_docs.py` (the thirteen J4 labels, six troubleshooting rows, the template, `_J4_KEYS` and
`_J4_TERMS_KEYS`), `tests/lab/test_lab_sim.py` (the experiments message), `tests/lab/test_lab_shipped.py` (the template
list) and `tests/lab/helpers.py` (`PREREG_EXPERIMENTS`); and two pins of `tests/lab/test_lab_j3.py` that read the
same workflow and outputs (`lab.plan.OUTPUT_KEYS` with the four new keys; `earlier` and `earlier-2` found before
`earlier-3` and `terms`, and `github.token` five times).

Run in stage 2's last step, on the tree of the build commit:

- `python -m pytest tests/lab/test_lab_j4.py tests/lab/test_lab_jt.py tests/lab/test_lab_j3.py tests/lab/test_lab_j2.py
  tests/lab/test_lab_workflow.py tests/lab/test_lab_plan.py tests/lab/test_lab_docs.py tests/lab/test_lab_guards.py
  -q -p no:cacheprovider`: `382 passed, 4789 subtests passed in 548.73s (0:09:08)` (the first run found the two
  `test_lab_j3.py` pins above, fixed before this run);
- `python -m pytest tests/lab -q -p no:cacheprovider`, in the background: `994 passed, 34687 subtests passed in 1806.82s (0:30:06)`, no test
  failed;
- `python -m pytest tests/onboard tests/mycelic/test_collective_guards.py tests/mycelic/test_collective_x3.py -q -p
  no:cacheprovider`: `402 passed, 1080 subtests passed in 286.22s (0:04:46)`;
- the template dry run below: exit 0, every line the build plan asks for;
- the terms-003 dry run (`python -m lab.dryrun --request lab/templates/terms-003.json --out OUT --jt-data DIR`): exit
  0, its ten console lines equal to build 1's above but for the machine's figures (`wall_s`, `draft_wall_s`,
  `peak_rss_kib`);
- the j3 template dry run, re-recorded by `python -m tests.lab.a002_golden record` into a scratch directory: its plan
  summary, `report.json` and `report.md`, normalised, equal the recording byte for byte (4161, 116487 and 15701
  bytes), and `A002GoldenTests` passes;
- `git diff --stat 831f424 HEAD` over every pinned path (`lab/j1.py`, `lab/goldlabels.py`, `lab/j2.py`,
  `lab/j2labels.py`, `lab/j3.py`, `lab/j3labels.py`, `lab/shard.py`, `lab/jt.py`, `mycelic/`,
  `connectors/openfda.py`, `research/`, the vehicle pack, `tools/`, every `CHOICE-*.md`, every `run-*.json` and
  `*-settings.json`, `lab/templates/terms-003.json` and `lab/requests/terms-003.json`) printed nothing; the only
  hunks of `lab/aggregate.py` and `lab/summary.py` since `831f424` are the docstrings, the imports, `ids`, insertions
  after `jt_block` and `_jt_tables`, `_prereg_section`'s j4 call, the class-section dispatch (one more argument and one
  more call) and `build_report`'s source loop, block call and report key; `lab/requests/accuracy-003.json` does not
  exist;
- `flake8 --max-line-length 120 --select=F,E9,W6` on every Python file changed since `831f424` prints only
  `lab/units.py:1255` F841 (`adapter` assigned and not used), which `831f424` already has at line 1204;
- `actionlint` is not installed here.

Run again at the fix stage ("Fixes after review"), on the fix branch's tree (the last edits before the fix commit are
text only):

- the same eight files: `398 passed, 4846 subtests passed in 806.48s (0:13:26)` (the first run found
  `ReferenceCompletenessTests.test_every_column`: the three new columns had no row in the reference's columns table,
  added before this run);
- `python -m pytest tests/lab -q -p no:cacheprovider`, in the background: `1010 passed, 34855 subtests passed in 2695.40s (0:44:55)`, no test failed;
- the onboard and guard suites: `402 passed, 1080 subtests passed in 484.04s (0:08:04)`;
- the template dry run below, on the negated variant and on the default data alike: exit 0, every line "Before each
  merge" asks for, no ref and no term in anything a reader sees;
- the terms-003 dry run: its ten console lines equal build 1's but for the machine's figures, and its outputs equal
  `831f424`'s but for them ("The dry runs");
- the j3 template dry run re-recorded into a scratch directory: equal to A002's recording byte for byte (4161, 116487
  and 15701 bytes);
- `git diff --stat 831f424 HEAD` over every pinned path printed nothing; every j2, j3 and jt function of
  `lab/aggregate.py` and `lab/summary.py` is the same text as at `831f424` (24 functions); `lab/jt.py`'s code hash is
  `f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76`; `lab/requests/accuracy-003.json` does not exist;
- `flake8 --max-line-length 120 --select=F,E9,W6` prints only the same `lab/units.py:1255` F841, and no line the fix
  stage added is longer than 120 characters.

### The dry runs

Re-recorded at the fix stage on the negated variant (`tests.lab.j4_data --negated`, "Fixes after review"): `python -m
tests.lab.j4_data --out DIR --negated`, then `python -m lab.dryrun --request lab/templates/accuracy-003.json --out OUT
--j4-data DIR` (the synthetic archive and artifacts; every hash below is a synthetic file's or the code's;
`prereg_sha256` changes with the commit; recorded on the tree of `0e523e5`), in about two minutes:

```
plan: 5 units in 5 shards (real)
j4 exclusion: j001=artifact a001=artifact a002=artifact consistency j1_r1=0 a1_r2=0 b1_r3=0 outside_k2=0 outside_k3=0 k2_union=0 k3_union=0 excluded 450
j4 draw: available 724 excluded 451 (earlier 450 split_by_record_id 0 split_by_text 1) eligible_after_exclusion 273 overlap 0 sources J1=verified R1=verified A1=verified R2=verified K2=verified B1=verified B1_questions_sha256 080d38c4fcf8 R3=verified K3=verified j001=artifact a001=artifact a002=artifact consistency j1_r1=0 a1_r2=0 b1_r3=0 outside_k2=0 outside_k3=0 k2_union=0 k3_union=0 labels_sha256 fb2e58ed9256 questions_sha256 d37ec473ba1c prereg_sha256 b8f9aed0e954 excluded_sha256 30f7aa08a6a9 excluded_lines 601 complaint_file_sha256 6c9634750c2a a001_complaint_file_sha256 a5a5de2d31f3 a002_complaint_file_sha256 216ecb36f9f5
j4 split: archive_sha256 6c9634750c2a term_archive_sha256 6c9634750c2a archives_equal yes term_rows 709 term_refs_sha256 871e368496de rebuilt_rows 709 rebuilt_refs_sha256 871e368496de by_record_id 0 by_text 1 already_excluded 3 before_cutoff 0 in_drafting 0
j4 terms: outcome verified failed none run_id 100000000004 terms_sha256 4e15dd6d0706d813f045dad647fa207d9fcc1c0aa3dac438c6933f3a030ae94e expected_sha256 4e15dd6d0706d813f045dad647fa207d9fcc1c0aa3dac438c6933f3a030ae94e p_d_config_hash 22b9079fd68266413d6988185ac7f754590e4c8de4ffc191e8ace9665c3a16cc p_d_vocabulary_hash 6ce58f6393623d82a54dfcde2eb59d25da2c6552bb5e00efa174b6d3ae281420 p_d_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 p_d_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f jt_code_hash f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76 document_jt_code_hash f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76 stand_in yes
j4 comparators: lexical_drafted balanced_accuracy 0.580 [0.553, 0.610] sensitivity 0.160 specificity 1.000 unknown 0; lexical balanced_accuracy 0.733 [0.693, 0.773] sensitivity 0.467 specificity 1.000 unknown 0; presence_drafted balanced_accuracy 0.600 [0.570, 0.633] sensitivity 0.200 specificity 1.000 unknown 0
j4 comparators: record_blind balanced_accuracy 0.490 [0.430, 0.547] predicate_only_bound 0.553 drafted_minus_hand -0.153 [-0.203, -0.103] presence_minus_hand -0.133 [-0.180, -0.087] presence_minus_drafted 0.020 [0.007, 0.037]
prereg: e1 no x1 no e2 no j1 no j4 yes
lab: unit j4-a-4b-p1 status ok class plumbing exit 0 wall_s 0.7
lab: unit j4-a-4b-p2 status ok class plumbing exit 0 wall_s 0.8
lab: unit j4-a-4b-p3 status ok class plumbing exit 0 wall_s 0.8
lab: unit j4-a-4b-p4 status ok class plumbing exit 0 wall_s 0.7
lab: unit j4-a-4b-p5 status ok class plumbing exit 0 wall_s 0.6
lab: aggregate units 5 shards 5 class real measurements false lock unchanged
```

Five units in five shards; the term stand-in verified (outcome `verified`, failed `none`, the term set's sha256 equal
to the expected one, P_D's four hashes, `lab/jt.py`'s code hash equal to the one the document records); every one of
the eight sources verified on the stand-ins, B1 against the stand-in's questions prefix (`080d38c4fcf8`, a synthetic
file's), each cycle established by its artifact, every consistency count 0, 450 earlier refs; the split on equal
archives (709 drafting rows rebuilt with the term run's refs sha), 0 removed by record id, 1 by text (a synthetic
complaint whose folded narrative is a drafting one's), 3 drafting records already excluded, both tripwires 0; 724
eligible, 451 excluded, 273 eligible after exclusion, overlap 0, 150 drawn, the chain file 601 lines (450 + 1 + 150);
the three lexical judges on every record (the drafted judge 0.580 [0.553, 0.610], the hand judge 0.733 [0.693, 0.773],
the term-presence judge 0.600 [0.570, 0.633], no unknown verdict: on the negated variant the drafted judge refutes the
negated mentions the term-presence judge confirms, `presence_minus_drafted` 0.020 [0.007, 0.037]), the control 0.490
[0.430, 0.547], the bound 0.553; every unit `ok` and `plumbing`. In the report: every arm balanced accuracy 0.733
[0.693, 0.773] (the fake answers both tasks as the hand lexical judge), every word and every word under lever 5's text
alone `incomplete`, the four lifts 0.000 and not told apart, the comparisons 25 (21 before, 4 this cycle), 103 of 300
task D questions with an empty list (19 of the 26 predicates carry none), the controls 0.156 and 0.080 above A001's
own figures (a between-draw difference on synthetic data that decides nothing). Every number of the plan summary (88)
and of the report (332) is traced to its run file; the report's arms table has the two new headline columns (`n/a`
here: no arm is a model measurement). Over the whole output tree, with the 1432 synthetic refs (every complaint id of
the synthetic `complaints.zip` and every record ref of the output's line files) and the 41 synthetic terms: the
console log, both summaries, their sources files, `report.json`, `report.md` and every shard file but the verdict
lines hold neither; refs appear only in `plan/prereg/j4/` (`labels.jsonl`, `questions.jsonl`, `excluded.jsonl` and
the four comparator files) and in the five shards' `verdicts.jsonl`, terms only in `plan/prereg/j4/terms.json` and in
the drawn narratives of `plan/prereg/j4/labels.jsonl`. The small dry run (two models, 40 records in 2 parts) gives the
same exclusion and split lines, 4 units, 491 chain lines and 29 comparisons (8 this cycle, in the manifest, the
document and the report).

The terms-003 dry run reads the default archive, whose bytes are unchanged (`64ceaf17c209`, 350681 bytes): its ten
console lines equal build 1's above but for the machine's figures, and, run from git-less copies of `831f424` and of
this tree, its plan, plan summary, report and manifest are equal but for those figures (the step's wall seconds and
peak memory, the shard's and unit's wall seconds); the manifest has no `j4` key, as at `831f424`. The j3 template dry
run's three outputs equal A002's recording after normalisation.

### Known limits of the build

- **Nothing in a dry run measures the terms.** The fake answers task D as it answers task B (the shipped judge's
  answer, behind an empty citation under branch E), so every dry-run arm is the hand lexical judge line for line and
  every lift is 0; only a real model run reads the drafted terms.
- No real NHTSA record was read and no real artifact downloaded: the eight sources, the split and the term check ran
  on synthetic artifacts. The real term artifact (run 38612800578) and the three earlier artifacts are read only by
  the run's plan job, whose `earlier-3` and `terms` steps have not run on GitHub yet.
- Branch E's aggregate and summary paths (the grounded readings, the citation table, the confirmation case's pass
  order in the aggregate) are exercised by the unit tests of stage 1 and by no dry run: the template is branch N, as
  the rule's branch in force.
- A002's questions prefix (C3) and the earlier cycle's figures are constants from the recorded runs; a re-recorded
  earlier run would need a reviewed change.
- The plan job's time is measured on synthetic data only (the whole template dry run in about a minute); the real
  preregistration adds three rebuilds and the drafting set's rebuild from the day's archive inside the `prereg`
  step's 20 minutes.
- `actionlint` is not installed here; the workflow is checked by `tests/lab/test_lab_workflow.py` and
  `WiringTests.test_the_workflow_downloads_the_third_earlier_artifact_and_the_term_artifact`.

### Fixes after review

Built on branch `wf/a003-fix` from the build commit `11bf9c4`. The three reviews of build 2 sent eighteen blocking
findings (several the same gap seen by two or three reviewers, each with its surviving mutants) and nineteen smaller
ones. Every one was applied; none was judged wrong, so none is recorded here as declined. Four were applied in a form
other than the one a finding offered first, each said below. Every code change has a test that fails without it (the
mutation run at the end of this section). The rule is unchanged: no finding needed a change of `CHOICE-A003.md`, no
fix touches a pinned path, and `lab/jt.py`'s code hash is still `f5743df5…3d76`.

- **B1's questions prefix (C3)** (blocking). `lab.j4labels.establish` keeps the prefix B1 was checked against with
  B1's outcome (`summary["sources"]["B1"]["questions_sha256_prefix"]`: A002's pinned `84486fa38568`, or a dry run
  stand-in's), which reaches the labels record, the manifest and `report.json` with the exclusion block. The `j4
  draw:` line prints `B1_questions_sha256 <prefix>` right after `B1=<outcome>`, and so does the `j4 exclusion: source
  B1 ...` line when B1 does not verify; both summaries show it beside B1's ref count (`questions sha`). Pinned in
  `SourcesTests.test_every_source_verifying` (the stand-in's prefix, and `84486fa38568` with no stand-in),
  `PreregWiringTests.test_the_fixed_lines`, `test_the_request_s_own_entries_check_b1_against_a002_s_pinned_questions_prefix`
  (`84486fa38568` on the console when no stand-in is passed) and both dry runs' console and summaries tests.
- **The template's recorded facts** (blocking, two findings). `RequestTests.test_the_template` compares the
  template's `exclude` list and `terms` entry with literal copies of "The request" and T1/T2 (every run id, artifact,
  file, prefix, rebuild seed, chain file and chain prefix of the three entries; the term run's id, artifact, file,
  sha256, `vocabulary_hash` and cutoff), finds each run id, artifact, prefix, sha256 and hash in `CHOICE-A003.md`,
  and checks A002's labels and chain prefixes against `lab.j4labels`' constants.
- **The drafting text file's sha256** (blocking, two findings). `TermsCheckTests.test_every_outcome` gains "a drafting
  text file with one hash replaced": the first line's sha256 replaced by another 64-hex value, the line count kept,
  the artifact's manifest restamped; the outcome is `refs mismatch` with `drafting_files` failing.
- **The aggregate's pass order, document pin and P_D** (blocking, two findings; mutants M28, M30, M31, E5, E12, E14).
  `AggregateWiringTests.test_a_manifest_that_disagrees_with_the_preregistration` adds P_D rebuilt as another pack
  (`lab.j4.drafted_pack` patched to return the hand pack), the document's term pin changed and restamped in the
  plan manifest's file map, `terms.json` one byte longer with its file entry and the manifest's pin restamped and the
  document untouched, the confirmation flag (smaller finding), and the comparisons (below); each gives
  `PREREG_MISSING`. The new `test_a_line_the_runner_could_not_have_written` restamps, one at a time, a task D line
  with `terms_count` plus one, a task D line with the sha256 of another predicate's list, a task B line with
  `mapped_verdict` flipped, and run.json's `pass_order` reversed: the part is then ok for neither task, both sets hold
  part 2 alone, every arm has one part finished and every word is `incomplete`; the files put back, the part reads ok
  and the primary arm's word is `failure` again. Applied in another form: the finding's `parts_finished` 4 is the
  template's five parts less one; these tests work on the small dry run's two parts, so they assert 1 of 2.
- **The earlier cycle's own figures** (blocking, three findings; mutants M36, A18a, A18b, E13).
  `RuleTests.test_the_earlier_cycle_s_own_figures` compares `lab.j4.EARLIER_ARMS` with the literal dict (A001, run
  38114522160, `negation` 0.577 [0.530, 0.623], `negation_mapped` 0.653 [0.610, 0.697]; A002, run 38439612256,
  `evidence` 0.540 [0.503, 0.577], `evidence_grounded` 0.553 [0.517, 0.590]) and finds the four intervals, both run
  ids and the cycle-two figures "arm C read as answered, 0.577; arm D through the mapping, 0.653" in `CHOICE-A003.md`.
  Both dry runs' report-block tests pin `report.json`'s `earlier` block to the same literal and, per model, each
  control's earlier figures, run id, label and `difference` equal to this draw's balanced accuracy less the earlier
  one (the sign).
- **Each consistency count alone** (blocking; six surviving mutants). `SourcesTests.test_every_consistency_count_stops_the_plan_job`
  builds, for each of the seven counts, artifacts and entries under which that count is the only one above 0 (J001's
  artifact from another archive with both chains broken; A001's drawn without J001's set; A002's drawn with J001's set
  alone excluded and K3 broken; K2 or K3 five refs short of A001's with J001 established by that chain; K2 two refs
  wide; K3 one ref wide) and asserts the stop `disagrees` and that count's value.
- **The plan job's stops** (blocking; mutants D2 to D5 and D4b). New `PreregWiringTests` cases, each running
  `lab.prereg.preregister` in this process on a fresh plan and asserting the stop's path and note, the lines printed
  and that nothing was written under `prereg/j4`: (a) no earlier artifact and J001's rebuild broken:
  `J4_FRESHNESS_UNVERIFIED`, `j4 exclusion: source J1 artifact absent`, B1's line with the stand-in's questions
  prefix, no draw or split line; (b) J001's artifact from another archive with both chains broken (`j1_r1` the
  only count above 0), and K3 one ref wider than the three sets (`k3_union` 1 alone): `J4_EXCLUSION_DISAGREES`, the
  count on the exclusion line; (c) `n` 274, one more than the 273 left:
  `J4_TOO_FEW_RECORDS` at `$.experiments.j4.labels.n`; (d) `n` 274 with `split_removals` patched to remove nothing:
  the repeated narrative's complaint is drawn, `in_drafting 1`, `J4_SPLIT_VIOLATED`, the split line and no draw line;
  (e) a rebuilt drafting set one row short of the term run's on the same archive: `J4_SPLIT_VIOLATED` before the
  removals, the split line with both row counts and no count. Every one of them ends with the term line (below).
  Applied in another form: (e) patches `lab.j4labels.rebuild_drafting` rather than building a term stand-in whose
  drafting refs differ, since a term artifact whose refs file disagrees with its own document stops the term check
  (`J4_TERMS_UNVERIFIED`) before the split is reached; the patched rebuild reaches the split's own comparison.
- **The seeds** (blocking; mutants D6, D6b, D9). `test_the_files_and_the_manifest` compares `labels.jsonl` with the
  draw at seed 4, `questions.jsonl` with J001's questions at seed 4 in 2 parts, and each comparator's score with
  `lab.j1.score` at bootstrap seed 4; the new `test_each_seed_feeds_its_own_step` preregisters a plan whose labels
  seed (5), block seed (6), bootstrap size (1001) and bootstrap seed (7) all differ and recomputes the draw, the
  questions, the comparator lines, their scores and the judges' pairs from them (and shows the other seeds would give
  other files). `TemplateDryRunTests.test_the_plan` compares the template dry run's labels and questions with the draw
  and the questions recomputed from the dry run's own data. Applied in another form: the finding compared them with
  `_Data.get().draw.data`, the default archive's draw; the dry runs now read the negated variant, whose labels file
  carries the variant's narratives (the same records), so the draw is recomputed from the dry run's own archive and
  stand-ins.
- **Which judge feeds which clause, and the word under lever 5's text alone** (blocking; mutants E1, E3, E4, H1 to
  H3). `test_the_verdict_words_the_added_clauses_and_the_lifts` recomputes, for every arm, each paired difference
  with `lab.j1.paired` over the arm's own lines, the pass test's three differences (the drafted judge in the second
  clause, the hand and term-presence judges in the added ones), the word with `lab.j4.verdict_word` and the lever-5
  word with `lab.j2.verdict_word`; B and Br, which answer as the hand judge, are pinned to (`failure`, `partial`) and
  their report rows to "`failure` | `partial`". The new `test_the_primary_pair_s_two_words_apart` (a-4b measured,
  task D answered as the hand judge) gives the primary pair `failure` and `partial` and finds "Primary pair `a-4b`
  `negation_terms`: `failure`; under lever five's text alone: `partial`." and the primary line's three judges' figures
  and paired differences, each from its own judge. Applied in another form: the finding asked for negated cue words
  in the drawn complaints of `tests/lab/j4_data.py`; changing the default archive would change the terms-003 dry
  run's data and outputs, which must stay as they are, so the negated narratives are an opt-in variant
  (`j4_data.archive(negated=True)`, `write_data(negated=True)`, `python -m tests.lab.j4_data --out DIR --negated`): on
  every other window row holding the engine narrative its drafted cue word is negated. The judging kind's two dry
  runs and every test built on them use the variant; the default archive's bytes are unchanged (`64ceaf17c209`,
  350681 bytes). On it the drafted lexical judge and the term-presence judge answer apart:
  `presence_minus_drafted` is not 0 in both dry runs (asserted), and both dry runs' summaries tests find the primary
  line's drafted, hand and term-presence figures equal to `report.json`'s `primary_result.judges` and the arm's
  paired figures.
- **The readings beside the verdict** (blocking; mutants E8, E9, E11, E16 to E18). The new
  `test_the_readings_beside_the_verdict` recounts the agreement per kind against the drafted lexical judge's and the
  term-presence judge's files (below 1 against the drafted judge), the empty lists (the questions whose predicate T
  gives no term), and each of the three headlines with `lab.j1.headline_for` (the drafted judge's is the headline and
  differs from the hand judge's); then restamps one record's two task D lines as transport failures (`network`, no
  answer, not scored): one record dropped, `J1_WITHHELD`, both words `incomplete`, no headline, every comparator
  scored on 39 records and the bound equal to `lab.j1.prior_bound` on those 39 (the record is chosen so that the bound
  moves).
- **The warm-up's term counts (C12)** (blocking, and the smaller finding on the same gap). `lab.warmup.warm_up`
  prints, for each warmed J4 task D, one line from `lab.warmup.terms_line`: `lab: warmup <task D> typical_terms N
  typical_predicate P worst_terms M worst_predicate Q worst_prompt_tokens T max_tokens K slot S fits yes|no` (`none`
  for an uncounted worst case), counts and hand predicate ids only. The new
  `WiringTests.test_the_warm_up_prints_task_d_s_term_counts_on_a_server` drives `warm_up` on the stub server for a
  J4 shard's two tasks and asserts the row's term counts (`worst_terms` the longest list's length), the counted
  prompts and the one printed line exactly (no line for task B, the probe task B's), no term in the console or the
  record, and `fits` `no` for a slot one token short. "Choices" records the reading.
- **`L1_LABEL`** (smaller, two findings). `L1_LABEL = (` and its continuation indent are restored as at `831f424`.
- **The headline against all three judges** (smaller). The report's J4 arms table gains "headline against the hand
  lexical judge" and "headline against the term-presence judge" beside the headline against the drafted judge,
  filled for the headline arm and the headline secondary (`n/a` otherwise);
  `test_the_primary_pair_s_two_words_apart` finds the three headlines in the primary arm's row, the drafted judge's
  and the hand judge's apart.
- **P_D's four hashes in the term line** (smaller, two findings). `j4 terms:` prints `p_d_config_hash`,
  `p_d_vocabulary_hash`, `p_d_detector_hash` and `p_d_fixtures_hash` (`lab.prereg.P_D_HASH_KEYS`); the fixed-line,
  stop and dry-run tests check all four against the term check's.
- **The term line on every later stop** (smaller). Every stop after the term check verified (the freshness, the
  consistency, the shortage, the overlap and the split stops) prints the term line last (`stop()` in `lab.prereg._j4`);
  the success order is unchanged. Each stop test above asserts it.
- **The overlap stop** (smaller). `lab.j4labels.draw` raises `DrawOverlap` when `lab.j2labels.build_labels` refuses a
  draw while enough records remain (its "the draw holds N excluded records"); the plan job prints the sentence and
  stops with `J4_EXCLUSION_DISAGREES` at `$.experiments.j4.labels`, while a shortage stays `J4_TOO_FEW_RECORDS`. The
  note now says both: a consistency count above zero stops the plan job before any draw, and a draw holding an
  excluded record stops it after the draw, either way before any model. `PreregWiringTests` case (f) patches
  `build_labels` to refuse with the overlap sentence.
- **P_D checked before the sources** (smaller). The plan job rebuilds P_D right after the term check and compares
  its hashes with the check's before any source is read; `PreregWiringTests` case (g) patches `lab.j4.drafted_pack`
  and finds the term line the only line.
- **The guards no test can tell apart** (smaller, two findings; mutants M10, M11, B2). A comment in `by_chain` says the
  two guards are implied by the others-established condition and kept so the at-most-one-cycle rule reads as
  written.
- **The term check's redundant checks** (smaller). "Tests" says which checks are redundant with the file hashes.
- **The confirmation flag** and **the B words** (smaller, two findings): above.
- **The removal by record id on the console** (smaller). The new
  `test_a_straddling_complaint_no_earlier_draw_holds_is_removed_by_record_id` builds, inside the test, the default
  archive plus two more complaints straddling the cutoff whose later rows no synthetic draw holds (their refs not among
  the 450), and preregisters on it: `archives_equal no`, the split line ending `by_record_id 2 by_text 1
  already_excluded 3 before_cutoff 0 in_drafting 0`, the draw line `available 726 excluded 453 (earlier 450
  split_by_record_id 2 split_by_text 1)`, both refs in the chain file and not in the draw (two by id beside one by
  text, so the two counts cannot be printed in each other's place). `write_data` is unchanged.
- **`j4` beside `j2` or `j3`** (smaller). `lab.request.validate` refuses it at `$.experiments` with
  `J4_BESIDE_PROBLEM`, as `jt` alone is refused; `RequestTests.test_a_j4_block_beside_a_j2_or_a_j3_block_is_refused`.
- **The comparisons this cycle per model** (smaller). `lab.j4.comparisons_this_cycle(base, models)`, the branch's
  four arms times the models, is what `prereg/j4/prereg.json` and the manifest record; the aggregate refuses a block
  whose document or manifest says otherwise (`PREREG_MISSING`). Pinned in `RuleTests`, `PreregDocTests`,
  `PreregWiringTests` (8 for two models), both dry runs (the manifest and the document equal the report's count) and
  `AggregateWiringTests` (the document alone, the manifest alone, and both saying 4 for two models).
- **The no-ref scan** (smaller). Both dry runs' scans use every complaint id of the synthetic `complaints.zip`
  (`goldlabels.nhtsa_export().C_ODINO`) with the line files' refs.
- **The manifest's `j4` key** (smaller). Only a plan with J4 units has a `j4` key in `prereg/prereg.json`, as the
  report keeps its key; every other plan's manifest is what it was at `831f424` but for the machine's figures (the
  terms-003 dry run, "The dry runs"). `PreregWiringTests.test_the_files_and_the_manifest` preregisters a J1 plan and finds its manifest's
  keys without `j4`.

**The mutation run.** Each mutant was applied alone to the fixed tree, the tests named for it in
`tests/lab/test_lab_j4.py` were run with `-x`, and the file was put back; 32 mutants, every one killed. In
`lab/aggregate.py`: the lever-5 word set to the rule's word (E1), the drafted clause fed the hand judge's difference
(E3), the term-presence clause fed the drafted judge's (E4), the agreement read against the hand judge (E8), the
empty lists counted as one term (E9), the headline read against the hand judge (E11), no withholding (E16), the
comparators and the bound on every record (E17, E18), the between-draw difference's sign (E13), the pass order, the
line check, the document's term pin, P_D's hashes and the comparisons left unchecked (M28, E2, M30, M31). In
`lab/summary.py`: the primary line's and the arms table's lever-5 word printed as the rule's (H1, H2), the hand
judge's figures printed from the drafted judge (H3), the hand headline column filled from the drafted judge, B1's
questions prefix left out of the summaries. In `lab/prereg.py`: the questions at the labels' seed, the comparators
bootstrapped at the block's seed, the draw at another seed, the split's two counts swapped in the draw line, B1's
prefix left out of the draw line, the P_D check before the sources removed, the stops without the term line. In
`lab/j4.py` the comparisons as the constant four; in `lab/j4labels.py` the overlap read as a shortage and B1's prefix
recorded as A002's whatever was checked; in `lab/request.py` `j4` beside `j2` or `j3` accepted; in `lab/warmup.py`
the typical payload's count printed as the worst case's. One mutant survived the first pass, the split's two counts
swapped in the draw line: the straddling case removed one record by id and one by text, so the swap printed the same
line; the case now adds two straddling complaints (two by id, one by text) and kills it.
