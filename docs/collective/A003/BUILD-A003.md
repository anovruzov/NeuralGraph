# A003 build note: the lab's `jt` and `j4` experiments, as built

What was built so the lab can run accuracy test A003 (`CHOICE-A003.md`, with its section "Amended before any build,
2026-10-14", C1 to C32). The choice file is the rule; this note says how each build carries it out, where the rule
left a choice to the build, what deviates (nothing of the rule's substance), what the tests pin and what the dry run
showed. The rule was committed alone and amended before any code (`ed96530`). The rule's build plan has two builds
with a lab run between them: build 1, the term-drafting kind `jt`, below; build 2, the judging kind `j4`, after the
term run's amendment, in a later section of this note.

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

`tests/lab/test_lab_jt.py`, offline (52 tests at the build commit, 75 after the fixes below; the table is as fixed):
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
  `_j3_prereg_lines`, the docstrings, the imports, `ids`, and the class-section dispatch of `render_report` and
  `_class_section` (one more argument and one more call);
- `flake8 --max-line-length 120 --select=F,E9,W6` on `lab/jt.py`, `tests/lab/j4_data.py`, `tests/lab/test_lab_jt.py`,
  `tests/lab/a002_golden.py` and `tests/lab/stubs/jt_stub.py` printed nothing (on the changed lab modules it prints
  only `lab/units.py:1204` F841, which `ed96530` already has).

### The dry run

`python -m tests.lab.j4_data --out DIR`, then `python -m lab.dryrun --request lab/templates/terms-003.json --out OUT
--jt-data DIR` (the synthetic archive; every hash below is a synthetic file's or this commit's code's):

```
plan: 1 units in 1 shards (real)
jt export: archive_sha256 b4cf3aa79e3d2a3b0b1c33bc71f3272db68d3dcaba92022b1b1d354b5a965fc8 archive_bytes 327983 rows FORD=104 CHEVROLET=102 JEEP=102 HONDA=103 NISSAN=102 DODGE=102 pooled 615 cross_make_duplicates 1 earliest_received 2020-01-02 latest_received 2022-12-31
jt drafting: rows 615 refs_sha256 8bc8364ce357bff33b120aabf635b91768e3849b035eaefc4193ba0974cf3714 drafting_refs_sha256 8bc8364ce357bff33b120aabf635b91768e3849b035eaefc4193ba0974cf3714 drafting_text_sha256 3e22d56587e94b5c3fef1ed1a38c9131df4cf8409f6d4c8f1dab52e8cbfd6c9f narratives 615 straddling 1
jt draft: corpus 615 categories_passing_floor 10 specific_predicates 8 other_bucket_share 0.088 rows_without_specific 54 terms_candidates 907 terms_eligible 335 placeholders 0 refused_categories 0 refused_terms 0 assignable 0 within_cap 0
jt check: passed yes loader=passed ngram=passed refused_strings=passed refused_terms=passed template=passed term_floor=passed value_floor=passed
jt terms: hand_predicates 26 reached 7 empty 19 terms_min 4 terms_median 4 terms_max 7 terms_total 33 p_draft_config_hash 95521bfd18f58a87a3651a5d82409d0cc1c122bd28bfce05745227971c0e0377 p_draft_vocabulary_hash 14b7c19043a5d80f19b2869110a1a4c160e409475533c79650b699c6780e902c p_draft_detector_hash 39499e9cc0a09942239d42dacba991b02db401624f201af85b975e5a92f08416 p_draft_fixtures_hash 0be097e49385ef4dfd81f0e51c3cffc37ac2bc11de6ac94aaba2453b2ba532f4 p_d_config_hash e842fee4aa5155243f93a6f6a1c27a6824596010fda78ef10a005343fa1b7354 p_d_vocabulary_hash 924379d16f66bb1b8482fdb2f2c9ab022cdcd5685e1f9c35cb9b3016b5355588 p_d_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 p_d_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f hand_config_hash a588e72d7dbd0307d42234ceb79a85a915f0454d947469a4bfa0a42595f2f7bd hand_vocabulary_hash 867df7fc83c556e267842f219e1087b4bbd6af052ff5d201206d0aa8c789aa96 hand_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 hand_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f onboard_code_hash a1cf1bf6d4202823c4ab11d28d49fc46926319e897379964bc3f89d77b7f9c0b defaults_sha256 9ca66ffc98580f3e2f22da8d702b943db52ba198c79be14e763bd97ad8efc95b lang_en_sha256 9c76178a457bba9d4d9f98460a4d8263f5112b048b978cf651a46162bbd10cdb jt_code_hash ac47010e9ec552ce5cd481d914b7803e4302aea5f290fe89523bc6bfe49e9366 terms_sha256 dd917796dae8be131e5d4b1b4a192843b8aada2c10ec310ec8e6495cf845cdb4
jt step: wall_s 0.5 draft_wall_s 0.4 draft_timeout_s 900 peak_rss_kib 36284
prereg: e1 no x1 no e2 no j1 no jt yes
lab: unit jt status ok class no-model exit 0 wall_s 0.2
lab: aggregate units 1 shards 1 class real measurements false lock unchanged
```

One unit in one model-free shard, nothing provisioned; the export of 615 rows (the drafting block of six makes and the
three cases of C4 and C28) with one cross-make duplicate dropped and one straddling complaint; 10 categories passing
the floor, 8 of them specific predicates (the seven the hand pack maps and one it does not; the other two are the
non-specific catch-all and one below the 50-record minimum); the check passed by check; 7 of the 26 hand predicates
reached, 19 left empty; P_D's detector and fixtures hashes equal to the hand pack's; the unit verified the term set (`ok`, measurement class `no-model`; the
report's display class is `plumbing`, as every dry-run record is). No term, category spelling, drafting record id,
state or vehicle appears in the console, in either summary, in the report or in any shard file: a scan of the dry
run's output directory outside `plan/prereg/jt/` and of its stdout for the 33 synthetic terms and the 615 drafting ids
found none. The synthetic archive's entry carries a fixed timestamp (`j4_data.ZIP_DATE`), so its sha256 and every
figure above repeat from one generation to the next; `jt_code_hash` and `terms_sha256` change with `lab/jt.py`.

### Known limits of the build

- No real term run happened: the drafter, the check and the pooled export ran on 615 synthetic rows. On the real
  archive the pooled corpus is the union of D002's six training corpora; the step's time and memory are measured by
  the term run itself (the `jt step:` line) and the amendment records them (C11). If the draft does not fit in 900
  seconds or in the runner's memory, the plan stops with `JT_DRAFT_FAILED` and the remedy is a reviewed workflow
  change, never a drafter parameter.
- The step's wall time includes the archive's download when the term run is the first reader of the cached fetch (it
  is, in a jt plan).
- The peak resident memory is the draft subprocess's own; the parent's export in memory is not counted in it.
- `straddling` reads the raw archive with no product filter (see the choices); the judging run's removal by record id
  (C4, build 2) is what acts on it.
- The verify unit runs at the shard's commit, which in one run is the plan's: it cannot catch a term artifact read at
  a later commit; that is the judging run's check (`lab.j4terms`, build 2), which compares `lab/jt.py`'s code hash at
  its own commit (C29).
- `actionlint` is not installed here; the workflow is unchanged and checked by `tests/lab/test_lab_workflow.py`.

### Fixes after review

(Left for the fix stage.)
