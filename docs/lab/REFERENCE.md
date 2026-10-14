# Lab reference

Every key, file, status, class, label, column and artifact of the Mycelic cloud lab. The guide is
[README.md](README.md). The code is the authority: each section names the module it summarises.

## Request format

A request is `lab/requests/<name>.json`, `<name>` matching `[a-z0-9][a-z0-9-]{0,39}`, at most 64 KiB of strict JSON
(`lab/request.py`). Every key is required unless a default is given; unknown keys are refused at every level, and a
string of the form `<...>` anywhere is refused as `placeholder: fill in`. A seed is an int in 0 to 2147483647. The
capacity of a shard is `job_minutes` less the shard overhead of 25 minutes; every `minutes` is 1 to that capacity.

| key | values | default |
| --- | --- | --- |
| `schema_version` | 1 | |
| `purpose` | why this run, 1 to 500 characters; public | |
| `provider` | `fake` or the manifest's server program | |
| `models` | 1 to 8 distinct manifest model keys | |
| `job_minutes` | 45 to 330: each shard job's limit | |
| `max_parallel` | 1 to 16: shard jobs at once | |
| `retention_days` | 1 to 90: how long artifacts are kept | |
| `experiments` | at least one of `e1`, `e2`, `e3`, `g0`, `sim`, `x1`, `openfda`, `j1`, `l1`, `j2`, `j3`, `jt` | |
| `hosted` | per hosted model key, `{"max_calls": 1..1000000}` | `{}` |

A fake model needs provider `fake`, a gguf model the server provider; a hosted model goes with either. Provider
`fake` makes the whole run a plumbing check; its hosted units still call the configured host, which bills those calls,
and its summaries then say so (`PLUMBING_HOSTED_LINE`, `PLUMBING_HOSTED_BANNER`).

`e1`, extraction F1 of two or more models against labelled records, one unit per model and repeat:

| key | values | default |
| --- | --- | --- |
| `models` | gguf, fake or hosted models of `models`; at least 2 | `models` |
| `minutes` | per repeat unit | |
| `labels` | `source` (`generator`, `fixtures` or `nhtsa`), `pack` (a built-in pack; for `nhtsa` exactly `docs/collective/replay/vehicles/pack`), and for generator and nhtsa labels only `n` (40 to 2000) and `seed`. `nhtsa` labels are real public NHTSA complaints with their codes hidden, gold from the codes, built in the plan job (`lab.goldlabels`), and run only on models inside the runner | |
| `reference` | one of the E1 models | |
| `margin_points` | 1 to 20 | |
| `runs` | 3 to 5 repeats | |
| `seed` | a seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |

`e2`, pushdown verification against central reading on a planted synthetic world, one unit per model:

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models | `models` |
| `minutes` | per unit | |
| `pack` | a built-in pack | |
| `plant` | `plant_<name>`, a fixture of the pack | |
| `seeds` | 1 to 5 distinct seeds | |
| `weeks` | the pack's baseline plus window weeks to 104 | |
| `eval_from` | the pack's window plus minimum history weeks less one, to `weeks` - 1 | |
| `eval_to` | `eval_from` to `weeks` - 1 | |
| `grace_weeks` | 0 to 8 | 4 |
| `tie_salt` | `[A-Za-z0-9._-]{1,64}` | `lab-e2` |
| `detector_author` | 1 to 80 printable characters | `mycelic engineering` |
| `top_n` | 5 to 60 candidates per seed | |
| `min_candidates` | 1 to `top_n` | `top_n` |
| `central` | `self` or a hosted model of `models` whose manifest entry has `context_tokens` | `self` |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |

`x1`, the model-free evaluation harness, one unit: the keys `minutes`, `pack`, `plant`, `seeds` (1 to 10),
`weeks`, `eval_from`, `eval_to`, `grace_weeks`, `tie_salt` (default `lab-x1`), `detector_author`, `bootstrap_b`
and `bootstrap_seed`, as for `e2`.

`e3` and `g0`, one unit per model, and `sim`, one unit per model and seed:

| block | key | values | default |
| --- | --- | --- | --- |
| `e3` | `models` | a subset of `models`, not hosted | `models` |
| `e3` | `minutes` | per unit | |
| `e3` | `concurrency` | 1 to 4 distinct levels, each 1 to 8, run in order | |
| `e3` | `requests` | 3 to 200 | |
| `e3` | `warmup` | 0 to `requests` - 1 | |
| `e3` | `workloads` | `extraction`, `short` | |
| `e3` | `seed` | a seed | |
| `g0` | `models` | a subset of `models`, not hosted | `models` |
| `g0` | `minutes` | per unit | |
| `g0` | `pack` | a built-in pack | |
| `g0` | `records` | 50 to 2000 | |
| `g0` | `seed` | a seed | |
| `sim` | `models` | gguf or fake models | `models` |
| `sim` | `minutes` | per unit | |
| `sim` | `plant` | `plant_smoke` or `sim_small` | |
| `sim` | `weeks` | the pack's baseline plus window weeks to 52; the plant must fit every seed's world | |
| `sim` | `top_n` | 1 to 60 | |
| `sim` | `seeds` | 1 to 5 distinct seeds | |

`openfda`, the public replay and the labelling sheets, one model-free unit that reaches api.fda.gov:

| key | values | default |
| --- | --- | --- |
| `minutes` | per unit | |
| `pack` | a built-in pack, or a replay pack `lab/packs/<id>` (built by `tools/market/replay_pack.py`), with an openFDA mapping | |
| `product_codes` | 3 to 5 distinct codes of three upper-case letters | |
| `date_from`, `date_to` | calendar dates `YYYYMMDD`; the span must cover enough ISO weeks | |
| `max_records_per_code` | 1 to 25000 | |
| `manufacturers` | 1 to 10 distinct exact spellings, 1 to 120 printable characters each | |
| `manufacturer_field` | a field path, `device[].manufacturer_d_name` | |
| `partition_field` | a field path | |
| `recalling_firms` | as `manufacturers` | `manufacturers` |
| `lookback_weeks`, `post_weeks` | 1 to 104 | 26 |
| `min_partition_coverage` | a number in (0, 1] | 0.5 |
| `tie_salt` | as `e2` | `lab-replay` |
| `saw_recall_outcomes` | `yes` or `no`: did the requester see recall outcomes first | |
| `n1_sheet` | `{"n": 1..1000, "seed"}`: the N1 labelling sheet | none |
| `e1_sheet` | `{"n": 1..2000, "seed"}`: the openFDA E1 labelling sheet | none |

`j1`, judge test J001 (`docs/collective/replay/vehicles/CHOICE-J001.md`): the site verifier's narrow question on
real public complaints, one unit per model and part (`lab/j1.py`):

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models of `models`; a hosted model is refused, as the lab sends no real narrative to a host | `models` |
| `minutes` | per part unit | |
| `labels` | `e1`'s labels block with `source` `nhtsa` only: `pack` exactly `docs/collective/replay/vehicles/pack`, `n` (40 to 2000) and `seed` | |
| `parts` | 1 to 30: the records, in order, cut into this many parts | |
| `seed` | the questions' seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |

The plan job builds the labels, the questions (one positive and one negative per record, by the rule of the choice
file: the negative is drawn with the frequency of the other records' positives, so the predicate alone tells a judge
almost nothing), the lexical judge's verdicts and scores, the record-blind control's verdicts and scores (it never
reads a record: it confirms a question when its predicate is one of the four filed most often in NHTSA's whole
complaint file, `lab.j1.PRIOR_TOP`; it decides nothing), the predicate-only bound (the most a judge that sees only the
predicate could score on the records, fitted to their answers, so optimistic; it decides nothing) and the pins
(`prereg/j1/`). Each unit judges one part's questions with the shipped judge task, schema and payload through the
runtime at boundary `site:lab`, data label `public`, and the verifier's own verdict rule; the aggregate pools a model's
parts, checks every verdict line against its question and the verdict rule, scores the control and the bound on each
model's records, and reports each judge's confirms per predicate (`by_predicate`).

`j2`, accuracy test A001 (`docs/collective/A001/CHOICE-A001.md`, amended A1 to A12 before any run; the build note is
`BUILD-A001.md` beside it): J001's question on a fresh draw of real public complaints with an earlier run's records
excluded, asked twice of each model, under the product's judge instruction and under a lab-side instruction that
defines negation and absence, with a declared reply mapping read beside the shipped verdict rule; one unit per model
and part (`lab/j2.py`, `lab/j2labels.py`; the template is `lab/templates/accuracy.json`, to copy to
`lab/requests/accuracy-001.json`):

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models of `models`; a hosted model is refused (`NHTSA_HOSTED`) | `models` |
| `minutes` | per part unit; a unit judges its part's questions twice | |
| `labels` | as `j1`'s: `source` `nhtsa` only, `pack` exactly `docs/collective/replay/vehicles/pack`, `n` (40 to 2000) and `seed` (the fresh draw's) | |
| `parts` | 1 to 30 | |
| `seed` | the questions' seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |
| `exclude` | the earlier run whose records the draw excludes (A1): `run_id` (an int, 1 to 10^20), `artifact` (its plan artifact, `lab-plan-<run_id>-<attempt>`), `file` (the file in it that lists the records: `prereg/j1/labels.jsonl` for J001's artifact, `prereg/j2/excluded.jsonl` for a later cycle's), `sha256_prefix` (12 to 64 lower-case hex digits the file's bytes must hash to) | |
| `primary` | the primary pair P of the rule: `model` (one of the block's models) and `arm` (`shipped`, `shipped_mapped`, `negation` or `negation_mapped`) | |
| `comparisons_before` | 0 to 1000000: the (model, arm, cycle) comparisons made since the loop began before this cycle | 0 |

`lab.plan --github-output` echoes the excluded run as `j2_exclude_run_id` and `j2_exclude_artifact`, and the plan job
downloads that one artifact under `${{ runner.temp }}/lab-earlier/<run id>` (`actions/download-artifact` with the
workflow's read-only `actions: read`; a missing or expired artifact is an absent directory, never a job failure).
`lab.prereg` then runs both freshness mechanisms (`lab.j2labels`): the artifact's listed file must hash to the
request's prefix and agree with the artifact's manifest, its refs must be the questions file's and its labels record
must say nhtsa, 150, seed 1 (mechanism one, outcomes `verified`, `artifact absent`, `sha256 mismatch`, `manifest
mismatch`, `refs mismatch`, `labels mismatch` or `malformed`; nothing read from the artifact is printed); and J001's
seed-1, n-150 draw is rebuilt from today's complaint file and verifies only when its labels hash to the pinned prefix
`bc6d092d8bca` (mechanism two). One verifying suffices (A2); neither verifying refuses the plan with
`J2_FRESHNESS_UNVERIFIED`, both verifying with different record sets with `J2_EXCLUSION_DISAGREES`, and fewer eligible
unexcluded records than `n` with `J2_TOO_FEW_RECORDS`. The draw is `random.Random("nhtsa:<seed>").sample` over the
eligible records less the excluded refs (the eligible build is `lab.goldlabels`', pinned equal by a test, A4); then
J001's code builds the questions, the lexical judge, the record-blind control and the predicate-only bound on the
fresh draw (`prereg/j2/`), and `prereg/j2/excluded.jsonl` carries every excluded ref and the draw's forward for the
next cycle (A3). The preregistration pins both tasks (the shipped `judge_record` and the lab's
`judge_record_negation`: name, data class, instructions sha256, schema sha256, max_tokens, and the pass order, task N
first, A9), the mapping (`describes_predicate` `unclear` read as `no`), the four arms, the primary pair, the exclusion's
counts and hashes and the target balanced accuracy. Each unit judges its part's questions with task N, then with the
shipped task, through the runtime at boundary `site:lab`, both tasks routed to its one endpoint without escalation;
every verdict line holds the task and both verdicts (the shipped rule's and the mapping's), each by the pinned `decide`.
The aggregate reads a (model, task) set as complete when every part finished for that task, builds the mapped arms as
views of the same lines, scores each arm with J001's code beside the comparators on the same records, pairs each arm
with the lexical judge and the arms with each other (the lifts), and computes the verdict word of rule 10 for every
pair, the primary first (`incomplete`, `pass_provisional`, `partial`, `failure`, in that order, A5 and A6). A dry run
needs `lab.dryrun --j2-data DIR` (a `tests.lab.j2_data` directory: a synthetic complaint archive, a synthetic earlier
artifact and an exclusion stand-in), and its preregistration says so (`J2_STAND_IN`).

`j3`, accuracy test A002 (`docs/collective/A002/CHOICE-A002.md`, amended B1 to B25 before any build; the build note is
`BUILD-A002.md` beside it): J001's question on a fresh draw of real public complaints with two earlier cycles' records
excluded, asked three times of one model, under the lab-side negation instruction of A001 and under two schema variants
of it that add a field before the two answers (an evidence `citation`, a verbatim passage of the record, and an
`analysis` written before the answers), each read by the shipped verdict rule, the verbatim-substring grounding check,
the unclear-to-no mapping and a lexical composite with its two ablations: nine arms, one primary pair; one unit per
model and part (`lab/j3.py`, `lab/j3labels.py`; the template is `lab/templates/accuracy-002.json`, to copy to
`lab/requests/accuracy-002.json`):

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models of `models`; a hosted model is refused (`NHTSA_HOSTED`) | `models` |
| `minutes` | per part unit; a unit judges its part's questions three times | |
| `labels` | as `j2`'s: `source` `nhtsa` only, `pack` exactly `docs/collective/replay/vehicles/pack`, `n` (40 to 2000) and `seed` (the fresh draw's) | |
| `parts` | 1 to 30 | |
| `seed` | the questions' seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |
| `exclude` | a list of exactly two entries, the earlier cycles in seed order (B2, B3, B5): each with `j2`'s four keys (`file` pinned per entry as the rule's "The request" names them: `prereg/j1/labels.jsonl` on the first, the first judge test's labels file, and `prereg/j2/labels.jsonl` on the second, the first accuracy test's; `lab.j3labels.EXCLUDE_FILES` lists the three files the j3 kind reads, the chain file `prereg/j2/excluded.jsonl` read through the second entry's `chain_file` alone), `rebuild_seed` (the first entry's must be 1, the second's above it) and `rebuild_sha256_prefix` (12 to 64 lower-case hex digits the rebuilt labels must hash to); the second entry alone carries `chain_file` (`prereg/j2/excluded.jsonl`) and `chain_sha256_prefix` | |
| `primary` | the primary pair P of the rule: `model` (one of the block's models) and `arm` (one of the nine arms: `negation`, `negation_mapped`, `evidence`, `evidence_grounded`, `evidence_grounded_mapped`, `rationale`, `rationale_mapped`, `composite`, `composite_mapped`) | |
| `comparisons_before` | 0 to 1000000: the (model, arm, cycle) comparisons made since the loop began before this cycle | 0 |

`lab.plan --github-output` echoes the first excluded run as `j2_exclude_run_id` and `j2_exclude_artifact` (the
workflow's `earlier` step downloads it) and the second as `j3_exclude_run_id_2` and `j3_exclude_artifact_2` (the
`earlier-2` step), both under `${{ runner.temp }}/lab-earlier/<run id>` (B2). `lab.prereg` then establishes each
cycle's set from five sources (`lab.j3labels.establish`, B5): J1, the first judge test's artifact (`lab.j2labels`'
check, unchanged); R1, its seed-one rebuild from today's complaint file; A1, the first accuracy test's artifact (the
same check generalised to its layout: `prereg/j2/labels.jsonl` hashing to the entry's prefix, `prereg/j2/labels.json` a
`lab_j2_labels` record of nhtsa, 150, seed 2 with `n` the refs found, the questions' refs the labels', every file
cross-checked against the artifact's manifest, B3); R2, that cycle's rebuild, `lab.j2labels.draw` of 150 at seed 2
over the eligible list less J001's set, attempted only once J001's set is established (`not attempted` otherwise); and
K, the first accuracy test's chain file through the same check. A cycle's set is its artifact's or its rebuild's; a
cycle with neither is the chain less the other cycle's set (`by` `chain`); K alone establishes neither. Four
consistency counts must be 0: J1 against R1, A1 against R2, every verified source's refs outside K, and K against the
union of the two sets when both came from their own sources. A cycle not established refuses the plan with
`J3_FRESHNESS_UNVERIFIED`, a count above 0 with `J3_EXCLUSION_DISAGREES`, and fewer eligible unexcluded records than `n`
with `J3_TOO_FEW_RECORDS`; the excluded set is the union of every verified source's refs (300 expected). The draw,
the questions, the lexical judge, the record-blind control and the predicate-only bound are then J001's and A001's
code on the fresh draw (`prereg/j3/`), joined by the two ablation comparators (`prereg/j3/ablation_a.jsonl`: confirm
where the lexical judge confirms, else refute; `prereg/j3/ablation_b.jsonl`: else the control's verdict), and
`prereg/j3/excluded.jsonl` carries every excluded ref and the draw's forward for the next cycle. The preregistration
pins the three tasks (`judge_record_evidence` and `judge_record_rationale`, each 512 output tokens and its schema with
the new string field first; `judge_record_negation`, A001's, 256; name, data class, instructions sha256, schema sha256,
max_tokens and the pass order E, R, N), the mapping, the grounding predicate (norm: whitespace collapsed, stripped,
case-folded; at least four characters; a substring of the payload text) and its mapping (a yes read as no when the
citation is not grounded), the composite rule and both ablations, the nine arms, the lifts and the ablation
differences, the primary pair, the headline arm and the headline secondary (`evidence_grounded`, B1), the exclusion's
counts, outcomes and hashes, and the target balanced accuracy. Each unit judges its part's questions with task E, then
R, then N, through the runtime at boundary `site:lab`, the three tasks routed to its one endpoint without escalation;
every verdict line holds the task, the whole reply (the citation or the analysis with the two answers), whether the
citation is grounded, the reply's first key as written (B19) and every reading's verdict (the shipped rule's, the
mapping's, the grounding's and both), each by the pinned `decide`. The aggregate reads a (model, task) set as complete
when every part finished for that task, builds the mapped, grounded and composite arms as views of the same lines
(the composite joins the plan job's lexical lines by question id), recomputes every reading and `grounded` from the
stored reply and the record text rebuilt from the plan artifact's labels (B23; the report holds no narrative, citation
or ref), scores each arm with J001's code beside the comparators and the ablations on the same records, pairs each arm
with the lexical judge, the arms with each other (the lifts) and the composite arms with both ablations, and computes
the verdict word of rule 12 for every arm, the primary first (`incomplete`, `pass_provisional`, `partial`, `failure`;
for a composite arm `pass_provisional` also needs both ablation differences' low ends above zero, and a missing
ablation figure is `incomplete`, B8), with the headline secondary's word printed beside the primary's. It also reports,
deciding nothing, the grounded share of yes answers on positives and on negatives, the empty citations and citation
lengths, the first-key share per task and the output tokens and cut replies per task. A dry run needs
`lab.dryrun --j3-data DIR` (a `tests.lab.j3_data` directory: a synthetic complaint archive of 90 complaints per make,
two synthetic earlier artifacts and a two-entry exclusion stand-in), its fake answers tasks E and R as the lexical
judge behind an empty field (so the grounded arms refute whatever it confirmed and nothing in a dry run measures the
instructions, the check or the composite), and its preregistration says so (`J3_STAND_IN`).

`l1`, latency test L001 (`docs/collective/L001/CHOICE-L001.md`, amended K1 to K13 before any run; the build note is
`BUILD-L001.md` beside it): the product's pushdown path timed on CPU on the drafter demo's alert of MSHA's accident
file, and its site answers scored against the filed categories, one unit per model and question slot (`lab/l1.py`,
`lab/l1path.py`, `lab/l1score.py`, `lab/l1guard.py`; the template is `lab/templates/latency.json`, to copy to
`lab/requests/latency-001.json`):

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models of `models`; a hosted model is refused (`MSHA_HOSTED`), as the lab sends no real narrative to a host | `models` |
| `minutes` | per question unit | |
| `questions` | exactly 5: the alert question and the four control questions (K11) | |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed; the draws over mines use `random.Random("l1:<seed>")`, those over records `"l1:<seed>:records"` | 1 |

The plan job (`lab.prereg`, which runs `lab.l1 prereg` in a subprocess whose output goes to `prereg-logs/`) downloads
MSHA's file into `lab-msha` beside the plan directory (`tools/onboard/fetch_msha.py`), rebuilds the drafter demo's
pipeline on c1's held-out years (D002's settings), and stops before any model runs, refusing the plan with
`L1_STOPPED` and the stop's code, unless the audit gives exactly one X or S alert and the drafted pack exactly four
other specific predicates (`no_alert`, `alerts`, `questions`), the demo's figures when the file is the demo's
(`demo_figures`), a hidden-codes store that retrieves what the unchanged store does (`retrieval`), the same question
id and routes on every judge's path (`routes`) and at most 5% of rule 8's draws without a key confirm or a key refute
(`draws`); a failed download refuses it with `L1_FETCH_FAILED`. It writes `prereg/l1/prereg.json` (the file's sha256,
the pack's hashes, the audit's counts, the alert, the five questions with their ids, windows, routes, strata and each
routed mine's retrieved records, the key judge's, the lexical judge's, the record-blind control's and the route-role
baseline's verdicts and gate statuses, each on its own fresh stores, the draws, the code hash, the judge task and the
endpoints' pins), `scores.json` (their scores on every scored site answer, by stratum, the construction counts, the
predicate-only bound and the gate's agreement beside a constant status) and `warmup.json` (the warm-up's payloads: a
generated record asked about the drafted pack's other bucket, which no question asks, K5). The manifest's `l1` block
holds them with `cache_key`, `lab-msha-<16 hex of the file's sha256>`, which `lab.msha cache-key` hands the workflow.
Each unit restores the file from that cache (else downloads it), checks its sha256 and every preregistered pin before
its first model call, and runs the timed path with one runtime per mine at boundary `site:<mine>` (`judge_record`
only, HTTP deadline 600 s, 1 retry, no escalation), sites one after another with the orchestrator's deadline of
3600 s; then the unit check, the per-record replies and the lexical rerun, which must give the preregistered verdicts.

`jt`, accuracy test A003's term run (`docs/collective/A003/CHOICE-A003.md`, amended C1 to C32 before any build; the
build note is `BUILD-A003.md` beside it): the term set the judging run will read, drafted in the plan job from the six
makes' public NHTSA complaints received before the judged window by the onboard package's drafter, unchanged; one
model-free unit `jt` (shard kind `none`, as `x1`) that verifies it (`lab/jt.py`; the template is
`lab/templates/terms-003.json`, to copy to `lab/requests/terms-003.json`). A request with a `jt` block holds no other
block (`$.experiments`, the term run is a request of its own: "The terms", item 10) and its `retention_days` is 90
(`$.retention_days`, C15):

| key | values | default |
| --- | --- | --- |
| `minutes` | the unit's minutes (it only verifies the term set) | |
| `pack` | exactly `docs/collective/replay/vehicles/pack`: the hand pack | |
| `cutoff` | exactly `2023-01-01`: `NHTSA_WINDOW`'s first day in ISO form, the day the drafting set ends before (C23) | |
| `from` | exactly `2020-01-01`: the first day of D002's NHTSA training window, where the drafting set starts | |

The makes (`NHTSA_MAKES`), the roles, the columns, the language and the drafter's parameters are code constants of
`lab/jt.py`, not request keys, so that no request can draft from another window or another make set. The plan job
(`lab.prereg`) downloads the complaint archive once (the J-cycles' fetch) and builds the pooled export in memory: each
make's rows received from 2020-01-01 to 2022-12-31 (`nhtsa_export.complaints` with every component name as filed and
the `reporter` column, as D002's split), in make order, a complaint already given by an earlier make dropped and
counted (C28), as one CSV of D002's seven columns, which the parser must read back whole; no row received on or after
the cutoff is in it. It then runs `python -m lab.jt draft` in a subprocess under its step runner with its own timeout
of 900 seconds (C11): `draft_export` with `load_params()` and no argument, the drafted pack P_draft, `check_pack`
against the same export, and the term set T: for each of the hand pack's 26 specific predicates, the drafted lexicon
of the drafted category that holds its NHTSA spelling, placeholders left out, or the empty list. The draft exits 0
when the check passed, 5 when it failed, 3 when the drafter refused the export, 4 on a `MemoryError` and 6 on any other
error (its class name on stderr, never its message), never 1, Python's own exit for an uncaught exception. A download
or an archive that cannot be read refuses the plan with `JT_FETCH_FAILED`; a failed check with `JT_FLOOR_FAILED`
(after a `jt check: passed no` line with each failing check's count); an export the drafter refuses, a timeout, an
out-of-memory exit, an error, a missing or malformed result, or an export or a P_D rebuild that fails in the plan
job's own process with `JT_DRAFT_FAILED`; each prints a `jt step: stopped` line (wall seconds, the draft's wall
seconds and the largest resident memory of a finished child of the step) and nothing is written under `prereg/jt/`
then. Otherwise the step writes `prereg/jt/terms.json`
(canonical JSON; its sha256 is the term set's: the cutoff, the window, the makes, the archive, the export's and the
drafting block's counts, the drafter's summary counts, P_draft's, P_D's and the hand pack's hashes, the check by
check, the onboard code hash, the two data files' sha256, the code hash of `lab/jt.py` (C29) and T),
`prereg/jt/pack/` (P_draft), `prereg/jt/check.json` (the check's result by check, booleans and counts only:
`lab.jt.check_counts`, the same block as `terms.json`'s `check`), `prereg/jt/drafting_refs.jsonl` (the
drafting rows' record ids, sorted), `prereg/jt/drafting_text.jsonl` (the sha256 of each distinct folded narrative:
hashes, no text) and `prereg/jt/prereg.json` (what the unit verifies against), and prints counts, dates and hashes
only, in fixed lines that start `jt export:`, `jt drafting:`, `jt draft:`, `jt check:`, `jt terms:` (ending
`terms_sha256 <64 hex>`) and `jt step:` (wall seconds, the draft's wall seconds and its peak resident memory); no
line holds a term, a category spelling, a narrative, a record id, a state or a vehicle. The unit
(`python -m lab.jt verify`) re-hashes `terms.json` against the preregistration, checks the code hash of `lab/jt.py`,
the hand pack's hashes and the two drafting files, rebuilds P_D (the hand pack with each specific predicate's `en`
lexicon replaced by T's list, or by the drafter's placeholder when the list is empty; every other file the hand pack's
bytes) and checks its four hashes; a unit that does not verify is `failed` with `JT_NOT_VERIFIED`. The term set is
never committed to the repository: a dated amendment of the rule commits its sha256. A dry run needs
`lab.dryrun --jt-data DIR` (a `tests.lab.j4_data` directory: a synthetic complaint archive with a drafting block
before the cutoff), which does not go with `--j2-data` or `--j3-data`.

The fetch makes up to `product_codes` x 2 x ceil(`max_records_per_code` / page) requests, where a page is 100 records
without the `MYCELIC_LAB_OPENFDA_API_KEY` secret (openFDA refuses larger pages without a key) and 1000 with it; at most
800 requests without the secret and 1000 with it.

Hosted models run only as E1 models or as `e2.central`. `hosted` is required exactly when one is used and names
exactly those keys, each with `max_calls` at least the request's bound, the most calls it can make on the host:

    bound = [K in e1.models] x (PREFLIGHT_MAX_CALLS + e1.runs x records x 2)
          + [e2.central == K] x len(e2.models) x (2 x PREFLIGHT_MAX_CALLS + 2 x top_n x len(seeds) x 2)

where `records` is `labels.n` for generator labels, else the pack's fixture records, and `PREFLIGHT_MAX_CALLS` is
`lab.hosted`'s preflight calls per canary task and shard. The plan shows the bound and each shard's share.

## Model manifest and lock

`lab/models.json` (`lab/manifest.py`); unknown keys are refused at every level. The top level holds
`schema_version` (1), `hf_base` (the hub's https base URL), `server` and `models`.

`server` (required when a gguf model is listed):

| key | values | default |
| --- | --- | --- |
| `program` | the server program, also the request provider | |
| `tag` | the release tag | |
| `asset` | the release asset, a `.tar.gz` | |
| `url` | `https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>` | |
| `archive_root` | the archive's top directory, or empty | |
| `binary` | the binary's path under `archive_root` | |
| `format` | `tar.gz` | |
| `args` | allowlisted server arguments: `--reasoning on/off/auto`, `--reasoning-budget N`, `--jinja`, `--no-jinja` | |
| `cache_ram_mib` | 0 to 65536 | 1024 |
| `threads` | `physical` or `logical` | `physical` |

`models.<key>`, a key matching `[a-z0-9][a-z0-9-]{0,23}` (`none` and `hosted` are reserved), of one of three
kinds:

| kind | keys |
| --- | --- |
| fake | `kind`, `alias`, `persona` (default `valid`), `response_format` (`json_schema` only), `transport_schema` (`full` or `reduced`, default `full`) |
| gguf | `kind`, `alias`, `gguf`, `response_format` (`json_schema`, `json_object` or `none`, default `json_schema`), `transport_schema`, `ctx_per_slot` (default 16384), `e3_ctx_per_slot` (default 4096), `e2_ctx_per_slot` (default 32768), each 2048 to 131072, and `server_args` (allowlisted, default none) |
| hosted | `kind`, `model` (the host's id), `response_format`, `transport_schema`, `price` (optional: `per_mtok_in` and `per_mtok_out`, USD per million tokens, 0 to 1000), `deadline_s` (10 to 3600, default 300), `max_retries` (0 to 5, default 2) and `context_tokens` (optional: the context one request gets on the host, 2048 to 2000000; required for an E2 central comparator) |

A gguf entry's `gguf` holds `repo` (`<owner>/<name>`), `file` (a `.gguf` path), `revision` (a 40-hex commit or a
branch) and `license` (`apache-2.0` or `mit`).

The lock is `lab/models.lock.json`, the manifest's sibling: `schema_version` (1), `server` (null or `tag`, `asset`
and `sha256`) and `models` (per gguf key `repo`, `file`, `revision`, `commit`, `sha256` and `size`). A lock entry must
match the manifest's tag, asset, repository, file and revision; a missing entry is trusted on first use and pinned
by the lock candidate a run writes.

Cache keys are derived from a verified file's sha256 (`sha16` is its first 16 hex digits): the server archive is
cached as `lab-server-<tag>-<sha16>` under `server/<tag>`, a model file as `lab-gguf-<key>-<sha16>` under
`gguf/<key>`.

## Ids

- A unit id is `<experiment>-<model>` (E2, E3, G0), `e1-<model>-r<k>` (one E1 repeat), `j1-<model>-p<k>` (one J1
  part), `j2-<model>-p<k>` (one J2 part, judged under both tasks), `j3-<model>-p<k>` (one J3 part, judged
  under the three tasks), `l1-<model>-q<k>` (one L1 question slot, 1 the
  alert question), `sim-<model>-s<seed>` (one simulation seed), `x1`, `openfda` or `jt` (the term run's one
  unit); at most 55 characters.
- A run id is the unit id, a hyphen and the first 8 hex digits of sha256(request sha256 `|` unit id); the harness
  writes under it.
- A shard id is `s<NNN>-<label>`: a three-digit number in creation order and the shard's model key, `none` for
  model-free shards or `hosted` for the hosted shard.

## Shard root

A shard's artifact holds (`lab/shard.py`):

- `provision/` and `server/`: the prepared server and model records, and the server's logs;
- `routing/`: the routing files the harnesses read (a hosted base URL is redacted);
- `units/<unit>/`: `unit.json` (the unit record), `stdout.log` and `stderr.log` (each cut to its last 64 KiB);
- `runs/<experiment>/<run id>/`: the harness files the lab keeps (result files and ledgers, and E1's raw replies,
  `replies.jsonl`, which its harness writes for public or synthetic data only; never a database, a private directory
  or raw scratch);
- `provenance.json` and `status.json` (below); `summary/`, added by the summary step after the seal.

A J1 unit (`lab.j1 run`) keeps three files under `runs/j1/<run id>/`:

- `verdicts.jsonl`: one line per question judged, `question_id`, `record_ref`, `kind` (`positive` or `negative`),
  `predicate`, `mentions_entity` and `describes_predicate` (the model's whole reply, kept because the narratives are
  public; null when no reply passed), `verdict` (`confirm`, `refute` or `unknown`, by the verifier's rule),
  `error_kind` and `scored` (false only for a transport failure); never a narrative;
- `ledger.jsonl`: the runtime's ledger, one row per attempt;
- `run.json` (`kind: lab_j1_run`): `run_id`, `endpoint`, `part`, `parts`, `questions_planned`, `questions_done`,
  `complete`, `stopped` (null, `budget`, `server_down` or `interrupted`; a call still running when the budget runs
  out is cut short and the run stops at `budget`, and SIGTERM stops it as `interrupted`, so the final run.json is
  written either way), `measurement` (false when a fake answered),
  `models_listed`, `listed_fake`, `models_served`, `model_requested`, `pinned` (the labels' and questions' sha256, the
  pack's hashes, the code hash, the judge task and the endpoint's pins), `prereg_sha256`, `verdicts_sha256`,
  `ledger_sha256`, `failures` (model and transport, by kind), `latency_ms_p50` and `latency_ms_p95` (the successful
  calls), `budget_seconds`, `boundary`, `data_label`, `code_commit`, `started_at` and `finished_at`.

A J1 call that the model answered, a reply still invalid after its repair included, counts as answered in the
participation check: the choice file scores such a reply as `unknown`, so it is the model's answer, not its absence.
A call's last ledger row decides: a call whose repair then failed in transport is not answered. The check's floor acts
before the choice file's 1% withhold: with fifty calls a part, three transport failures make the part `invalid`, so the
model reads incomplete rather than withheld.

A J2 unit (`lab.j2 run`) keeps the same three files under `runs/j2/<run id>/`, with these differences:

- `verdicts.jsonl`: J001's line plus `task` (`judge_record_negation` or `judge_record`: which pass the line belongs to)
  and `mapped_verdict` (the pinned verdict rule over the reply read through the mapping, `unclear` on the component as
  `no`); every question of the part appears once per task, task N's lines first; never a narrative;
- `run.json` (`kind: lab_j2_run`): J001's fields plus `pass_order`, `questions_planned_by_task`,
  `questions_done_by_task`, `complete_by_task` (a part is finished for a task when every one of its calls for that task
  was made), `stopped_task` (the pass a stop fell in), `failures_by_task` and `latency_ms_by_task`; `pinned` adds both
  tasks' pins and the mapping's. A stop ends the pass it falls in and the passes after it: exit 1 with
  `complete_by_task` saying task N finished and the shipped task did not is a unit `ok` with `J2_SHIPPED_STOPPED`
  (A9: the primary's set is complete for that part, the control's is not); a stop in the first pass is `J1_STOPPED`.
  The participation floor judges task N always and the shipped task only when its pass finished.

A J3 unit (`lab.j3 run`) keeps the same three files under `runs/j3/<run id>/`, with these differences:

- `verdicts.jsonl`: J001's line plus `task` (`judge_record_evidence`, `judge_record_rationale` or
  `judge_record_negation`), `citation` (task E's passage, else null), `analysis` (task R's text, else null), `grounded`
  (task E: whether the citation is a verbatim substring of the record text after the declared normalisation),
  `first_key` (the reply's first key as the model wrote it, never recomputed, B19), `mapped_verdict`,
  `grounded_verdict` and `grounded_mapped_verdict` (the pinned verdict rule over the reply read through the mapping,
  the grounding and both; null for tasks without a citation); every question of the part appears once per task, task
  E's lines first; never a narrative beyond what the reply itself quotes;
- `run.json` (`kind: lab_j3_run`): A001's fields with three tasks, plus `tokens_by_task` (attempts, output tokens'
  median and ninety-fifth percentile, replies cut at the cap); `pinned` adds the three tasks' pins, the mapping's, the
  grounding's and the composite's. A stop ends the pass it falls in and the passes after it: exit 1 with
  `complete_by_task` saying task E finished and a later task did not is a unit `ok` with `J3_EVIDENCE_STOPPED` (rule
  14: the primary's set is complete for that part, the later sets are not); a stop in the first pass is `J1_STOPPED`.
  The participation floor judges task E always and a later task only when its pass finished.

A JT unit (`lab.jt verify`) keeps one file under `runs/jt/<run id>/`, `verify.json` (`kind: lab_jt_verify`):
`verified`, `checks` (`terms_sha256`, `jt_code_hash`, `hand_hashes`, `drafting_files` and `p_d_hashes`, each true or
false), the term set's sha256 as read, P_D's rebuilt hashes, the code hash of `lab/jt.py` at the unit's commit and the
term counts; never a term. It exits 0 when every check holds and 1 otherwise; the aggregate reads it as verified only
when its sha256 and P_D's hashes are also the preregistration's.

An L1 unit (`lab.l1 run`) keeps under `runs/l1/<run id>/` (no record id, mine id, name or narrative; mines are
`m01` onwards, records their index in their mine's retrieved list; every number in seconds with at most three
decimals, a share, or a count below 100,000, K12):

- `run.json` (`kind: lab_l1_run`, written with `complete: false` before the first call and again at the end):
  `run_id`, `endpoint`, `slot`, `question_id`, `prereg_sha256`, `pinned`, `checks` (each pin, `unit_check` (K4: calls
  equal the retrieved records, fewer only for a degraded verdict, the verifier's breaker stop (its last two calls in
  record order ended in a timeout, network error or 5xx on the endpoint) or a timed-out mine; a ledger row for every
  call) and `lexical_reproduced`), `complete`, `finished` (the timed path reached its first decision), `stopped`
  (null, `budget` or `interrupted`), `problem`, `model_calls`, `measurement`, `models_listed`, `listed_fake`,
  `models_served`, `timing` (the time to answer, the question build, each mine's seconds with `timed_out` and
  `contended`, the gaps between mines and the gate; for a path not finished, `elapsed_lower_bound_s`), `derived`
  (`at_once_s`, `default_deadline` with its status and timeouts, `no_model_s`), `time_to_final_s`,
  `late_still_running`, `verdicts` (each mine's verdict, reason and support bucket at the first decision and after the
  late verdicts, and both gate statuses), `mines` (role, stratum, records, calls, attempts, failures by class,
  `model_s`, `first_call_s`, `median_call_s`, seconds and each call's latency), `lexical`, `confirm_shares`,
  `crossing` (the overlap of every artifact that crossed, in bytes, and their size in KiB), `budget_seconds`,
  `boundary_prefix`, `data_label`, `code_commit`, `started_at` and `finished_at`;
- `records.jsonl`: one line per retrieved record of each routed mine: `slot`, `mine`, `index`, `positive` (filed
  under the question's predicate), and the model's and the lexical judge's `mentions_entity`, `describes_predicate`
  and `error_kind`;
- `ledger-<mine>.jsonl`: each mine's runtime ledger, one row per attempt.

The guard (`lab.msha guard`, `lab/l1guard.py`, K9) scans every file of the plan directory, of each shard root (before
and after its seal) and of the report directory for c1 to c5's record ids, mine ids and refused values and any 8-word
run of c1's narratives; a file with a hit is replaced by its hit counts (`kind: lab_l1_withheld`) and the step fails.
The command (`python -m lab.msha guard --plan PLAN --plan-sha256 SHA256 --dir DIR [--raw RAW]`) runs the guard when
the plan has L1 units, when an earlier guard withheld the plan (only an L1 plan's guard writes that stub), or when
MSHA's file is on the runner (`RAW`, by default `lab-msha` beside the plan's directory, holds `Accidents.zip`; every
step that reads the file puts it there) whatever the plan, so a missing or unreadable plan is then guarded with
nothing left out. Otherwise no step of the job has read the file, and the command prints `l1 guard: no l1 units` and
leaves the directories alone (exit 0).

With K14 (`CHOICE-L001.md`, after run 1), the guard first reads the plan that `--plan` names and leaves out of its
record-id, mine-id and refused-value sets every value found as a whole word in the plan's own text (folded, whole
words, the scan's matching); the plan is still scanned, and the 8-word narrative scan is unchanged. It does so only
for the bytes `lab.plan` wrote: `--plan-sha256` is the plan step's `plan_sha256` output (the workflow passes it to
every guard step as `LAB_PLAN_SHA256`; `lab.dryrun` passes the sha256 of the plan it has just written). Each guard
step prints, before its directory lines, one of four fixed lines (counts only, never a value):

- `l1 guard: values in the plan's own text, left out: record_id <n> mine_id <n> refused_value <n>`: the plan was read
  as a lab plan with the bytes `lab.plan` wrote, and these are the numbers of values left out of each set;
- `l1 guard: the plan could not be read as a lab plan; no value is left out`: the plan is not a regular file, is
  withheld, is not UTF-8 JSON, or is not `kind: lab_plan` with `schema_version` 1, or it is missing (through the
  command, only when MSHA's file is on the runner, as above); the scan is K9's;
- `l1 guard: the plan's bytes are not the ones lab.plan wrote; no value is left out`: a lab plan whose sha256 is not
  `--plan-sha256` (or with none to compare it with); the scan is K9's;
- `l1 guard: the plan's own text holds a hit; no value is left out`: with its values left out, the plan's text still
  holds a hit (only a narrative's 8-word run can), so it is not lab text alone and every value is put back; the scan
  is K9's, and a scan of the plan's directory withholds the plan.

None of them is printed when the file cannot be split (`l1 guard: the file could not be split or read; every file is
withheld`). The directory lines keep their format: `l1 guard: <dir> files <n> withheld <n> record_id <n> mine_id <n>
refused_value <n> narrative_ngrams <n> unread <n>`.

## Provenance and status files

`provenance.json` (`lab.shard.PROVENANCE_KEYS`), rewritten before the first unit and after each one:

| key | holds |
| --- | --- |
| `schema_version` | 1 |
| `kind` | `lab_provenance` |
| `shard` | the shard id |
| `complete` | whether the shard finished writing it |
| `interrupted` | whether a signal stopped the shard |
| `result_class` | `plumbing` when a fake served the shard, else `real` |
| `banner` | the plumbing banner (`PLUMBING_HOSTED_BANNER` when the shard holds hosted units), or null |
| `plan` | the plan's sha256 and path |
| `request` | the request's path, name and sha256 |
| `manifest_sha256` | the manifest's sha256 |
| `lock_sha256` | the lock's sha256 |
| `git` | the plan's commit, the checked-out commit and whether `lab/` was dirty |
| `code` | the collective's code stamps and the lab's code hash |
| `host` | the runner: CPU model, CPUs, memory, OS, Python |
| `provider` | the shard's provider |
| `server` | the pinned server archive, how it was verified, and every server start; `{"kind": "none"}` without a model, `{"kind": "hosted"}` for the hosted shard |
| `model` | the pinned model file and commit |
| `provision` | the provision records' hashes |
| `prereg` | the preregistration's sha256, or null |
| `openfda` | whether the openFDA key was present, or null |
| `hosted` | each hosted secret's state, the scheme and `host:port`, and per key its share, calls, listing and preflight; null without hosted units |
| `deadline_epoch` | the shard's deadline |
| `started_at` | when the shard started |
| `finished_at` | when it finished |
| `wall_s` | its wall seconds |
| `planned_units` | the units the plan gave it |
| `units` | each unit's status and class |
| `exit_code` | the shard's exit code |

`status.json` (`lab.shard.STATUS_KEYS`), written by the seal:

| key | holds |
| --- | --- |
| `schema_version` | 1 |
| `kind` | `lab_shard_status` |
| `shard` | the shard id |
| `out_existed` | whether the shard root existed before the seal |
| `run_attempt` | the GitHub run attempt |
| `plan_sha256` | the plan's sha256 |
| `steps` | the workflow step outcomes |
| `failed_step` | the first failed or cancelled step |
| `prepare` | the prepare step's evidence and problem |
| `provenance` | whether provenance is present and complete |
| `units` | each unit's status |
| `missing_units` | planned units without a record |
| `counts` | units per status |
| `files` | the sha256 and size of every sealed file |
| `sealed_at` | when the seal ran |

## Statuses

A unit's status (`lab.units.STATUSES`):

| status | means |
| --- | --- |
| `ok` | the harness ran to a valid result and the model answered enough; a J2 unit whose first pass finished and whose shipped pass stopped is `ok` with `J2_SHIPPED_STOPPED`; a J3 unit whose evidence pass finished and whose later pass stopped is `ok` with `J3_EVIDENCE_STOPPED` |
| `result_fail` | a valid FAIL verdict: G0's canary scan or the simulation's raw-text scan did not pass (something crossed, or the scan's positive control found nothing) |
| `invalid` | it ran, but the model answered too few calls, the server died, or G0's model path had problems |
| `failed` | the harness failed, refused its configuration or wrote no readable result, a J1 run or a J2 or J3 run's first pass stopped before its last question (`J1_STOPPED`), or an L1 unit did not finish (`L1_NOT_FINISHED`), failed a check after its model ran (`L1_CHECK_FAILED`) or could not get its file before any model call (`L1_INFRA`), or a JT unit did not verify its term set (`JT_NOT_VERIFIED`) |
| `timed_out` | the unit ran out of its minutes |
| `interrupted` | a signal stopped it |
| `skipped` | it did not start: the shard's budget, the server, the warm-up, a projection or the hosted gate |
| `not_run` | (report only) its shard left no artifact, or its sealed shard has no record of it |
| `excluded` | (report only) its shard's artifact or files could not be trusted, or it is an L1 re-run after an earlier attempt's model call (`L1_RERUN_AFTER_CALLS`) or after an earlier attempt that left no artifact (`L1_RERUN_CALLS_UNKNOWN`) |

A shard's artifact in the report is `sealed` (used), `unsealed` (no readable status file), `altered` (a file differs
from the sealed list), `other_plan` (another plan's), `ambiguous` (two artifacts of the newest attempt) or
`no_artifact`.

## Classes

A report's display classes (`lab.units.DISPLAY_CLASSES`): `model` (a measurement on the runner: a verified model
file served by a verified server, every check passing), `hosted-api` (a result of the configured host, never a
measurement on the runner), `unverified` (a real run whose checks did not all pass), `plumbing` (a fake answered, or
the unit ran in a plumbing check, where a hosted unit's calls still go to the configured host), `no-model` (a
model-free unit) and `no-result` (no result).

A unit record's measurement class is `model`, `hosted-api`, `unverified`, `plumbing` or `no-model`, with a class
reason from `CLASS_REASONS` (below). A plan's and a report's result class is `plumbing` (provider `fake`) or `real`.

A provision record's `verified_by` is `lock` (the lock's sha256), `hf-api` (the hub's published sha256),
`github-api` (the release's published digest) or `first-use` (no digest was published or reachable: trusted on first
use, and only a server archive may be).

## Exit codes

The same for every lab command (`lab/__init__.py`):

| code | means |
| --- | --- |
| 0 | done; every unit ran to a valid result (a FAIL verdict is a valid result), or nothing to run |
| 1 | a unit is invalid, failed, timed out, interrupted or skipped |
| 2 | usage or configuration error: a bad request, manifest, lock, event, plan or argument |
| 3 | network error: a download or API that stayed unreachable, or a download past its deadline |

## Labels

Every fixed sentence the summaries print comes from `lab/notes.py`. Braces stand for values the summary fills in:
`{e_one}`, `{e_two}`, `{x_one}`, `{n_one}`, `{j_one}`, `{j_two}`, `{j_three}`, `{l_one}` and `{j_t}` are E1, E2,
X1, N1, J1, J2, J3, L1 and JT,
and `{run}` and `{commit}` a run id and a commit.

The first lines and the plumbing banner:

- `PLUMBING_BANNER`: Plumbing check: a fake model answered every call. These records test the lab, not any model.
- `PLUMBING_HOSTED_BANNER`: Plumbing check: a fake model answered every call except any hosted unit's calls, which go to the configured host and are billed by it. These records test the lab, not any model.
- `PLUMBING_CHECK_LINE`: PLUMBING CHECK: no model was run
- `PLUMBING_HOSTED_LINE`: PLUMBING CHECK: no model was run on this runner, but any hosted unit's calls go to the configured host
- `NO_MEASUREMENT_LINE`: NO MEASUREMENT: no unit here passed every model check, so nothing below measures a model

The experiments' labels:

- `X1_LABEL`: {x_one} here runs the model-free evaluation harness on a same-author plant fixture that is not blind; the extractor is lexical and no model runs. It is not STRATEGY's blind {x_one} test.
- `OPENFDA_LABEL`: Public data, artificial partitioning, not a confidentiality demonstration; no model in this pipeline.
- `OPENFDA_PUBLIC_FLAG`: The replay's measurement flag says only that both caches came from the public openFDA host; it measures no model.
- `OPENFDA_SAW_RECALLS`: The requester declared that they saw recall outcomes before the codes, manufacturers and settings were fixed, so the vocabulary and detector settings were not frozen blind: the recall figures below may reflect hindsight and are not a preregistered result.
- `OPENFDA_WARNED`: The replay warned: a warning can make a channel's zero structural, as when too few sites leave no cross-site candidate, rather than a negative result.
- `OPENFDA_FALSE_ALARM_SCOPE`: False alarms per week count alerts on the request's product codes only, those in the fetch table, not on every product code of the manufacturer: the lab fetches only the requested codes, whatever the replay's own denominator note says.
- `SHEETS_LABEL`: No labels were generated: {n_one} and openFDA {e_one} have no result until a human labels and commits the sheet.
- `G0_BELOW_PROTOCOL`: Some canary scans used fewer records than the protocol scan, whose size the protocol records column gives: they are smaller checks, not the protocol scan.
- `G0_MODEL_PATH`: the canary scan found no leak, but its model path had problems, so it did not test the model in the loop
- `E1_ENDPOINT_EXCLUDED`: A model without a complete set of valid repeats was left out: the comparison is stamped incomplete and lists it among the endpoints without runs.
- `E1_VERDICTS_WITHHELD`: Verdicts withheld: non-inferiority and the kill flag are shown only for a model measurement, beneath the label above.
- `E1_HOSTED_LABEL`: Hosted endpoints answered {e_one} over the network: their scores are not deterministic across repeats, and raw synthetic text was sent to the configured host.
- `E1_DROPS_NOTE`: Reasons that start with reattached count predicates kept on the record's structured entity after the model named an entity that did not resolve: they are not losses. Every other reason counts reply items that post-processing dropped.
- `E1_SCORES_NOTE`: Each model's own scores, pooled over its valid repeats only and scored by the extraction harness's own code: predicate F one with its percentile-bootstrap interval over records, beside the lexical extractor's predicate F one on the same records when the labels carry it. In the drops column, reasons that start with reattached count predicates kept on the record's structured entity, not losses.
- `E1_SCORES_ONLY`: No reference comparison was run: these are each model's own scores, with no difference from the reference, no non-inferiority and no kill flag.
- `E1_SCORES_REFUSED`: the extraction harness's reader refused a valid repeat of this model: a run file differs from its run.json, or the run names another preregistration, model or repeat
- `E1_SCORES_UNPINNED`: no model's own scores: the preregistration, its pack or the scoring code no longer match what the extraction harness pinned

Judge test J001's labels, where `{j_one}` is J1: the label, then the lexical, record-blind control (with the
predicate-only bound) and headline notes above the per-model table; a model without a headline says why in its row; a
unit that stopped says `J1_STOPPED`:

- `J1_LABEL`: {j_one} here asks each model the site verifier's narrow question about real public NHTSA complaint narratives, one record at a time with its codes hidden, and scores each verdict against the components the complaint was filed under: filed codes, not human-checked labels.
- `J1_LEXICAL_NOTE`: The lexical judge is the verifier's judge when no model runs: it confirms a question only when one of the pack's phrases for the component is in the narrative and not negated. It judged every question in the plan job, before any model ran.
- `J1_PRIOR_NOTE`: The record-blind control never reads a record: it confirms a question only when its component is one of the four filed most often in NHTSA's whole complaint file, and refutes it otherwise. It shows what knowing which components are common gets a judge without reading. The predicate-only bound is the most any judge that sees only the component could score on the same records; it is fitted to their answers, so it is optimistic. Neither decides anything.
- `J1_HEADLINE_NOTE`: The headline compares each model's balanced accuracy interval with the lexical judge's balanced accuracy on the same records: better only when the interval's low end is above it, worse only when its high end is below it, otherwise not told apart. Each model is compared once, with no correction for several comparisons; sensitivity, specificity and the unknown share decide nothing.
- `J1_INCOMPLETE`: not every part of this model finished, so it gets no headline: its finished parts are a partial reading and decide nothing
- `J1_NOT_MEASURED`: not every part of this model is a model measurement, so it gets no headline
- `J1_WITHHELD`: transport failures left out more than one in a hundred of this model's records, so its headline is withheld
- `J1_STOPPED`: the judge run stopped before its last question, at its budget or after the server stayed down; the verdicts it wrote are kept, and the part did not finish

Accuracy test A001's labels, where `{j_two}` is J2 and the braces of the primary line are its figures: the label, then
the exclusion, lexical, control, arms and verdict notes above the primary pair's sentence and the per-pair tables; a
unit whose shipped pass stopped after task N finished says `J2_SHIPPED_STOPPED`; the plan's refusals and the
stand-in use the others:

- `J2_LABEL`: {j_two} here asks each model the site verifier's narrow question about a fresh draw of real public NHTSA complaint narratives with an earlier run's records excluded, one record at a time with its codes hidden, twice: once with the product's judge instruction and once with a lab-side instruction that defines negation and absence; each verdict is scored against the components the complaint was filed under (filed codes, not human-checked labels), by the product's own verdict rule and, as a declared secondary, after a reply mapping that reads an unclear component answer as a no.
- `J2_EXCLUSION_NOTE`: The draw excludes the earlier run's records: the plan job downloaded that run's plan artifact and read its record refs (mechanism one), and rebuilt the first judge test's seed-one draw from the complaint file as published on the day (mechanism two). One mechanism verifying suffices; a rebuilt sha that differs says the complaint file changed and stops nothing by itself. Counts and hashes only: no record ref is written anywhere but the plan artifact.
- `J2_ARMS_NOTE`: Four arms, declared before the run: shipped is the product's judge unchanged; shipped_mapped reads its replies through the mapping before the verdict rule; negation is the lab-side instruction; negation_mapped is that instruction with the mapping. Model calls are made for the two instructions only: the mapped arms are the same replies read again. The lifts are paired differences between arms, record by record; a lift helps when its interval's low end is above zero, hurts when its high end is below zero, and is otherwise not told apart. They decide nothing toward the target.
- `J2_VERDICT_NOTE`: The verdict word is read on the primary pair alone, from the figures, in this order: incomplete (a part did not finish, the set is not a model measurement, or transport failures left out more than one record in a hundred), pass_provisional (balanced accuracy at or above the target, the paired difference to the lexical judge with its low end above zero, the balanced accuracy's low end above the predicate-only bound on the same records, and the unknown share reported), partial (the target reached but an interval clause failed, or the lexical judge beaten below the target), else failure. Every other pair is a secondary with the same word, which is a hypothesis for the next cycle, never a result; a first pass is provisional until the next cycle's fresh draw confirms it.
- `J2_PRIMARY_LINE`: Primary pair {model} {arm}: {word}. It scored balanced accuracy {ba} [{lo}, {hi}]; gap to the target {target} is {gap}; lexical judge {lex} on the same records, paired difference {diff} [{dlo}, {dhi}]; predicate-only bound {bound}; unknown share {unknown}.
- `J2_STAND_IN`: Plumbing: a test artifact stood in for the excluded run (lab.dryrun with test data), so the exclusion checks here test the lab, not the excluded run.
- `J2_SHIPPED_STOPPED`: the judge run finished its first pass, the negation task, and stopped in its second, the shipped task, at its budget or after the server stayed down; the first pass's verdicts are complete and kept, and the shipped pass did not finish for this part
- `J2_FRESHNESS_UNVERIFIED`: neither freshness mechanism verified: the earlier run's plan artifact could not be read as that run's, and the rebuilt seed-one draw does not hash to the pinned prefix; the plan job stops before any model runs (the accuracy test's rule, The data and the draw, as amended)
- `J2_EXCLUSION_DISAGREES`: both freshness mechanisms verified but their record sets differ; the plan job stops before any model runs
- `J2_TOO_FEW_RECORDS`: fewer eligible unexcluded records remain than the draw needs; no further fresh draw is pre-registered

Accuracy test A002's labels, where `{j_three}` is J3 and the braces of the primary line are its figures (the
last two the headline secondary's arm and word): the label, then the exclusion, lexical, control, arms,
verdict and evidence notes above the primary pair's sentence and the per-arm tables; a unit whose later pass
stopped after task E finished says `J3_EVIDENCE_STOPPED`; the plan's refusals and the stand-in use the others:

- `J3_LABEL`: {j_three} here asks one model the site verifier's narrow question about a fresh draw of real public NHTSA complaint narratives with two earlier cycles' records excluded, one record at a time with its codes hidden, three times: under the lab-side negation instruction, under that instruction with an evidence field that asks for a verbatim passage of the record, and under it with an analysis field written before the answers; each verdict is scored against the components the complaint was filed under (filed codes, not human-checked labels), by the product's own verdict rule on the answers as answered and, as declared secondaries, after the grounding check (a yes whose passage is not in the record read as a no), after the unclear-to-no mapping, and in a composite that confirms wherever the lexical judge does.
- `J3_EXCLUSION_NOTE`: The draw excludes two earlier cycles' records, each by its plan artifact and by a rebuild from the complaint file as published on the day: the first judge test's artifact and its seed-one rebuild, the first accuracy test's artifact (its labels file read by a check generalised to its layout) and its rebuild over the eligible list less the first judge test's set, and the first accuracy test's chain file. A cycle's set is established by its own artifact or rebuild, or else from the chain less the other cycle's set; the chain alone establishes neither; every consistency count must be zero; the excluded set is the union of every verified source's refs. Counts and hashes only: no record ref is written anywhere but the plan artifact.
- `J3_ARMS_NOTE`: Nine arms on one model, declared before the run: negation and negation_mapped are the first accuracy test's control arms replicated; evidence reads the evidence task's answers as answered (the primary arm); evidence_grounded reads a yes as a no when the cited passage is not a verbatim substring of the record's text (the headline secondary); evidence_grounded_mapped adds the unclear-to-no mapping; rationale and rationale_mapped read the rationale task's answers as answered and under the mapping; composite and composite_mapped confirm wherever the plan job's lexical judge confirms and otherwise take the grounded arm's verdict. Model calls are made for the three tasks only: every other arm is the same replies read again. The lifts are paired differences between arms, record by record; a lift helps when its interval's low end is above zero, hurts when its high end is below zero, and is otherwise not told apart. They decide nothing toward the target.
- `J3_VERDICT_NOTE`: The verdict word is read on the primary pair alone, from the figures, in this order: incomplete (a part did not finish, the set is not a model measurement, transport failures left out more than one record in a hundred, or, for a composite arm, an ablation difference is not a number), pass_provisional (balanced accuracy at or above the target, the paired difference to the lexical judge with its low end above zero, the balanced accuracy's low end above the predicate-only bound on the same records, the unknown share reported and, for a composite arm, the paired differences to both ablations with their low ends above zero), partial (the target reached but an interval clause failed, the lexical judge beaten below the target, or a composite that meets the four clauses but not an ablation clause), else failure. Every other arm is a secondary with the same word, which is a hypothesis for the next cycle, never a result; the headline secondary's word is printed beside the primary's as the primary under the other reading of the standing default; a first pass is provisional until the next cycle's fresh draw confirms it.
- `J3_EVIDENCE_NOTE`: The grounding check says that a cited passage came from the record, not that it reports the problem: a yes answer whose citation is not a verbatim substring of the record's text (case and runs of whitespace aside, at least four characters) is read as a no. The grounded share of yes answers is shown for positives and for negatives apart: a check that removes yes answers on negatives and keeps them on positives is the lever working; one that removes them alike is a cost with no reading. The composite arms inherit the lexical judge, which is why their pass test also needs the paired differences to both ablations above zero; on a draw where the lexical judge never answers unknown, ablation A is the lexical judge line for line. The first-key share says whether the model wrote the new field before its answers; the token figures and the replies cut at the cap say how much it wrote. None of these decides anything.
- `J3_PRIMARY_LINE`: Primary pair {model} {arm}: {word}. It scored balanced accuracy {ba} [{lo}, {hi}]; gap to the target {target} is {gap}; lexical judge {lex} on the same records, paired difference {diff} [{dlo}, {dhi}]; predicate-only bound {bound}; unknown share {unknown}. Under the other reading of the standing default the primary would be {other_arm}: {other_word}.
- `J3_STAND_IN`: Plumbing: test artifacts stood in for both excluded runs (lab.dryrun with test data), so the exclusion checks here test the lab, not the excluded runs.
- `J3_EVIDENCE_STOPPED`: the judge run finished its first pass, the evidence task, and stopped in a later pass, the rationale task or the negation task, at its budget or after the server stayed down; the evidence pass's verdicts are complete and kept, and the later passes did not finish for this part
- `J3_FRESHNESS_UNVERIFIED`: an earlier cycle's set could not be established: neither its artifact nor its rebuild verified and the chain could not give it; the plan job stops before any model runs and the loop asks (the second accuracy test's rule, The data and the draw, as amended)
- `J3_EXCLUSION_DISAGREES`: the verified sources of the earlier cycles disagree: a consistency count is above zero; the plan job stops before any model runs
- `J3_TOO_FEW_RECORDS`: fewer eligible records remain after both earlier cycles' exclusion than the draw needs; no further fresh draw is pre-registered

Accuracy test A003's term run, where `{j_t}` is JT: the label and the terms note in the term run's section of the
report and of the plan summary's preregistration; the plan's two refusals and a unit that did not verify use the
others:

- `JT_LABEL`: {j_t} here drafts the term set of the third accuracy cycle in the plan job, before any model runs: the onboard package's drafter, unchanged, reads the six makes' public NHTSA complaints received before the first day of the judged window as one pooled export, its privacy check reads the drafted pack against the same export, and the term set is the drafted vocabulary of each of the hand pack's specific components. No model runs and nothing is drawn; the one unit only verifies the term set against its preregistration.
- `JT_TERMS_NOTE`: Counts and hashes only: the term set, the drafted pack and the two drafting files live in the plan artifact, never in a summary, a console line or the repository, and no term, category spelling, narrative, record id, state or vehicle is printed. A dated amendment of the rule commits the term set's sha before the judging run, which refuses any other bytes.
- `JT_FLOOR_FAILED`: the drafted pack failed the privacy check against its export (the loader, the term floor, the value floor, the refused strings or terms, the template rebuild or the n-gram scan); the term run stops before its term set is written, and the loop asks
- `JT_DRAFT_FAILED`: the draft and its check did not finish: the drafter refused the export, the step ran out of time or memory, stopped on an error, or wrote no result the step can read; the term run stops before its term set is written, and the remedy is a reviewed change of the workflow or the lab, never a drafter parameter
- `JT_FETCH_FAILED`: the plan job could not fetch or read NHTSA's complaint archive; nothing was drafted, and the same request may run again
- `JT_NOT_VERIFIED`: the term run's unit did not verify the term set against its preregistration: a hash, the drafted pack rebuilt from the hand pack and the term set, or this module's code hash differs

Latency test L001's labels, where `{l_one}` is L1 and `{stop}` a stop's code: the label, then the headline and
comparator notes above the per-model tables and the time note above the time tables; a model without a headline says
why in its row; a unit's status reason and the plan's refusal use the others:

- `L1_LABEL`: {l_one} here times the product's whole pushdown path on a real alert of MSHA's public accident file: HQ forms the narrow question, each routed mine's verifier judges its own records with the model inside the mine, one mine after another, and the commit gate decides. Each mine's verdict is scored against the key judge's, which reads the categories the records were filed under: filed categories, not checked labels.
- `L1_HEADLINE_NOTE`: The headline compares each model's balanced accuracy over the site answers with the lexical judge's and with the route-role baseline's, by paired intervals over mines: better only when both low ends are above zero; better than the lexical judge only, which adds nothing over HQ's cells, when only the first is; worse when the first high end is below zero; otherwise not told apart. Each model is compared once with each, with no correction for several comparisons. Latency is a measurement, not a test.
- `L1_COMPARATORS_NOTE`: The lexical judge is the verifier's judge when no model runs, and it reads with the extractor that chose the routes; the route-role baseline confirms where HQ routed a mine as contributing and refutes at a sibling, reading no record; the record-blind control answers by which question was asked; the predicate-only bound is the most a judge that knows only the question could score, fitted to the answers, so optimistic. The key judge reads the filed categories, which HQ's codes cells already hold. All of them ran in the plan job, before any model.
- `L1_TIME_NOTE`: Times are seconds on one shared runner whose one model server serves every mine in turn: a mine's first call after the first mine can reuse the question's prompt prefix from the server's cache; the time with every mine asked at once and the time under the product's default deadline are derived, not run; mines timed while a late call of the same question ran are flagged contended, and their times and their questions' times to answer are shown apart, while the question build, the gate, the judge calls and the model calls pool them with the others; a question that did not finish within its unit's budget shows its elapsed time as a lower bound; no transport between machines is timed.
- `L1_INCOMPLETE`: not every question of this model finished, so it gets no verdict: its finished questions are a partial reading and decide nothing
- `L1_NOT_MEASURED`: not every question of this model is a model measurement, so it gets no headline
- `L1_LEFT_OUT`: transport failures left out more than one in twenty of this model's scored site answers, so it gets no verdict
- `L1_WITHHELD`: more than one in twenty bootstrap draws had no key confirm or no key refute, so the intervals are withheld and the model gets no verdict
- `L1_NOT_FINISHED`: the timed path did not reach the gate's first decision within the unit's budget; its elapsed time is a lower bound, and the question did not finish
- `L1_CHECK_FAILED`: a check of the unit failed: the calls, the ledger, the routes or the lexical rerun differ from the preregistration, so the question did not finish
- `L1_INFRA`: the unit could not restore, fetch or check MSHA's file before its first model call; the shard job may run again against the same plan
- `L1_RERUN_AFTER_CALLS`: an earlier attempt of this unit made model calls, so its re-run is not taken
- `L1_RERUN_CALLS_UNKNOWN`: an earlier attempt of this unit's shard left no artifact, so whether it made model calls cannot be read, and the re-run is not taken
- `L1_STOPPED`: the latency test stopped in the plan job before any model ran ({stop}); a new choice file decides what comes next
- `L1_FETCH_FAILED`: the plan job could not fetch MSHA's accident file; nothing ran, and the same request may run again
- `L1_NO_REAGGREGATION`: a re-aggregation scans no file for MSHA's values, so it reads no latency test; the run's own report holds it

Sizing and cost:

- `SIZING_NOTE`: Sizing: the per-call medians measured on this runner and the minutes they suggest for the next request's sim block, a quarter above the estimate and rounded up; a skipped or timed-out unit's estimate is projected, not measured. One minutes value serves every model of a block, so the block needs the largest suggestion among its models.
- `E2_SIZING_NOTE`: Pushdown sizing: the model-free rehearsal's call counts times this runner's warm-up latencies, against the share of the unit's budget the projection may use; a projection above it skips the unit, and the suggested minutes are a quarter above the projection, rounded up.
- `HOSTED_COST_NOTE`: Estimated cost: the token counts the host reported times the manifest's prices. The host's bill is the real cost; calls without token counts are left out, and max_calls caps only the units a shard starts, so prepaid credits are the hard limit.

Worlds and CPUs:

- `SIM_WORLD_SAME`: Simulation units of the same plant, seed and weeks saw the same synthetic world.
- `SIM_WORLD_DIFFERS`: Simulation units of the same plant, seed and weeks saw different synthetic worlds; their results are not comparable.
- `WORLD_DIGEST_SAME`: Canary scans of the same pack, pack version, seed and size saw the same synthetic world.
- `WORLD_DIGEST_DIFFERS`: Canary scans of the same pack, pack version, seed and size saw different synthetic worlds; their results are not comparable.
- `CPU_MODELS_DIFFER`: The shards ran on different CPU models: compare timings only between rows of the same CPU model.

The lock:

- `NOT_PINNED`: not pinned: trusted on first use
- `LOCK_UNCHANGED`: The lock already pins every file this run verified.
- `LOCK_NEW`: This run verified files the lock does not pin yet: copy lock-candidate.json from the report artifact to lab/models.lock.json and commit it, so later runs verify against it.
- `LOCK_CONFLICT_NOTE`: A verified file disagrees with the lock: the upstream file changed or the lock is wrong; check the provision records before re-pinning.
- `LOCK_NOT_COMPUTED`: No lock candidate was computed: the manifest or lock changed since the plan, or the provision records were ambiguous.

A re-aggregation report (`lab-reaggregate.yml`), right under its heading, where `{run}` and `{commit}` are the
run id and the re-aggregating commit:

- `REAGGREGATION_LINE`: Re-aggregation of run {run} by commit {commit}: no unit ran again; this report re-reads that run's sealed artifacts with this commit's aggregation code.

A unit's notes, printed once under the report's notes:

- `NOTES.plumbing`: Plumbing: a fake model answered; nothing here measures a model.
- `NOTES.plumbing_hosted`: Plumbing: this unit's hosted calls went to the configured host and any other call to a fake model; it ran in a plumbing check, so nothing here measures a model.
- `NOTES.synthetic`: Synthetic data: every record and narrative was generated from a seed; no real record was used.
- `NOTES.runner_hardware`: Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how this runner performed, not what a site would see.
- `NOTES.text_only_scan`: The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, sizes or other side channels.
- `NOTES.model_measurement`: Model measurement: a verified model file answered through a verified server on one shared GitHub-hosted runner; quality numbers describe this model on synthetic data, and timings describe this runner, not site hardware.
- `NOTES.public_data`: Public data: openFDA records under an artificial partitioning; no model ran, and nothing here is confidential or a confidentiality demonstration.
- `NOTES.public_terms`: Public data: real NHTSA vehicle complaints received before the judged window, read by the pack drafter in the plan job; no model ran, and no term, narrative or record id is printed.
- `NOTES.public_narratives`: Public data: real NHTSA vehicle complaint narratives, read with each complaint's codes hidden and scored against them. The codes are the components the complaint was filed under, not checked labels, and the models may have seen public complaints in training.
- `NOTES.model_measurement_public`: Model measurement: a verified model file answered through a verified server on one shared GitHub-hosted runner; quality numbers describe this model on public complaint narratives against their filed codes, and timings describe this runner, not site hardware.
- `NOTES.public_msha`: Public data: real MSHA mine accident narratives of one operator, read inside each mine with the filed categories hidden and scored against them. The categories are what the records were filed under, not checked labels, and the models may have seen public narratives in training. No record id, mine id, name or narrative is written.
- `NOTES.model_measurement_msha`: Model measurement: a verified model file answered through a verified server on one shared GitHub-hosted runner, as every mine's model in turn; quality numbers describe this model on public mine accident narratives against their filed categories, and timings describe this runner, not site hardware.
- `NOTES.hosted_api`: Hosted API: a model on the configured OpenAI-compatible host answered over the network. It is not deterministic: temperature and seed are requests, not guarantees on shared batched servers. Its latencies include the network and the host's queue and say nothing about this runner.
- `NOTES.hosted_raw`: Raw synthetic text was sent to the configured host under allow_external_raw synthetic; the lab sends no partner data anywhere, and hosted models never run in the canary scan or the simulation.
- `NOTES.central_hosted`: The central comparator was a hosted API model: its scores are not deterministic, so the ratio and the bar verdict compare the model under test with a moving reference.

The simulation's notes; exactly one of `no_control` and `chance_control` is present, and `model_beyond_exact` only when,
in the lifts' count, X with the model found a pattern X with the lexical extractor missed (a report lists the units
that carry a note some sim rows lack):

- `SIM_NOTES.synthetic_internal`: Simulation: a seeded synthetic world with planted patterns, written by the same author as the detectors; internal only, never a result to show buyers.
- `SIM_NOTES.lexical_exact`: The lexical extractor is exact on generator text by construction, so wherever X with the model and X with the lexical extractor differ, the model's extraction errors made the difference, in either direction: errors can lose a pattern, and they can also find one X with the lexical extractor missed, as when a wrong predicate on an entity the text does name adds counts to a planted key. A find of that kind counts for the model in its lifts over S and over model-free R as well. The difference measures extraction fidelity on synthetic text, not the value of reading real narratives.
- `SIM_NOTES.model_beyond_exact`: In the lifts' count, X with the model found a pattern X with the lexical extractor missed: the lexical extractor is exact here, so the model's extraction errors made that find. It is no sign that the model reads better, yet it raises the model's lifts over S and over model-free R as well as its lift over the lexical extractor; the scorecard's patterns block gives each channel's raw outcome per pattern, before any chance correction.
- `SIM_NOTES.few_patterns`: Few planted patterns: recall, average precision and the lift intervals rest on a handful of patterns and one seed, so they are not interpretable as estimates.
- `SIM_NOTES.r_model_free`: R here is model-free: the same detectors over the fields allowed to leave, with no model; it is not the strategy's R.
- `SIM_NOTES.no_control`: No no-plant control ran: a pattern the channel would also have found without the plant counts as found, and the lifts compare raw finds.
- `SIM_NOTES.chance_control`: A no-plant control ran: the same seed's world without the plant went through the same pipelines, and a find the control made as early is a chance find; the net counts and the lifts leave chance finds out. The control's model extractions were replayed from the planted run for the records both worlds share.

The simulation's channels:

- `SIM_CHANNEL_LABELS.X_model`: X with the model: each site's in-boundary model extracted claims from its own narratives; HQ ran the detectors over the codes and text-only cells that left the sites, k-suppressed
- `SIM_CHANNEL_LABELS.X_lexical`: X with the lexical extractor: the same pipeline with the pack's lexicon in place of the model; exact on generator text by construction
- `SIM_CHANNEL_LABELS.S`: S: codes cells only (no model, no narrative); the same detectors over the same k-suppressed cells
- `SIM_CHANNEL_LABELS.R_mf`: R, model-free: the same detectors over record-level, unsuppressed counts of the fields the pack allows to leave (codes and structured ids, never narrative); not the strategy's R, which may read those fields with a frontier model
- `SIM_CHANNEL_LABELS.U`: U, a reference: every claim of the model channel at record level, unsuppressed, with exact roots and reporters; not a deployable system
- `SIM_CHANNEL_LABELS.single_site`: single site: each site alone, a per-site exceedance test over its own exact counts, sharing the same weekly alert budget
- `SIM_CHANNEL_LABELS.rules`: rules: the pack's hand-written rules over the model channel's cells (episode starts, unranked)

The simulation's lifts:

- `SIM_LIFT_LABELS.X_model_minus_S`: patterns X with the model found minus patterns S found, bootstrapped over patterns
- `SIM_LIFT_LABELS.X_model_minus_R_mf`: patterns X with the model found minus patterns model-free R found, bootstrapped over patterns
- `SIM_LIFT_LABELS.X_model_minus_X_lexical`: patterns X with the model found minus patterns X with the lexical extractor found, bootstrapped over patterns; the lexical extractor is exact on generator text, so the model's extraction errors decide every pattern only one of them found: below zero they lost more patterns than they found, above zero they found more than they lost, and none of it is reading better

Baselines a plant blinds, under the simulation's and X1's lifts whenever a plant has a narrative_only pattern (every
plant a request can name has one: `sim_small` three of four, `plant_smoke` all three). The label is the collective
harness's own (`harness.BY_CONSTRUCTION_LABEL`), carried from each scorecard's `by_construction` entries:

- `BY_CONSTRUCTION_LABEL`: by construction, not a result
- `BY_CONSTRUCTION_NOTE`: By construction, not a result: planted narrative_only records carry no codes and no structured entities, so they add nothing to the cells S and model-free R read, and a find of theirs on a narrative_only pattern is chance, from background records. Every such pattern X found and they missed counts for X in the lifts over S and over model-free R, so that share of those lifts is fixed by the plant, not measured; only the plant's other patterns compare the channels.

Why a simulation is not a measurement:

- `SIM_MEASUREMENT_REASONS.fake_model`: a fake model answered: a fake endpoint, a fake model listing or a fake-server marker
- `SIM_MEASUREMENT_REASONS.low_participation`: the share of records the lexical fallback extracted was above the threshold
- `SIM_MEASUREMENT_REASONS.skipped_projection`: the run stopped after the projection check, before any result

E1's labels, by label source:

- `E1_LABELS.generator_text`: {e_one} here scores extraction against generator ground truth on template text. It is not the STRATEGY {e_one} decision, which needs human-labelled public narratives.
- `E1_LABELS.fixtures`: {e_one} here scores extraction against the pack's author-written fixture records. It is not the STRATEGY {e_one} decision, which needs human-labelled public narratives.
- `E1_LABELS.public_nhtsa`: {e_one} here scores extraction on real public NHTSA complaint narratives, each complaint's codes hidden, against the components it was filed under: filed codes, not human-checked labels. The lexical extractor's scores on the same records are in report.json, in the extraction block's labels, under public and lexical.

E2's labels:

- `E2_LABELS.synthetic`: {e_two} here runs on planted synthetic worlds; the pack, the detectors and the plant were written by one author, so the plant is not blind. Internal only, never a result to show buyers.
- `E2_LABELS.below_protocol`: Below the protocol minimum of candidates and seeds: the conditions and the ratio are not interpretable as estimates.
- `E2_LABELS.self_central`: The central comparator is the model under test itself, so the ratio compares the model with itself; the bar verdict is not shown.

A unit's class reasons:

- `CLASS_REASONS.fake_kind`: the manifest entry is a fake model
- `CLASS_REASONS.provider_override`: the shard ran with the fake provider in place of the model server
- `CLASS_REASONS.fake_marker`: a response carried the fake-server marker
- `CLASS_REASONS.no_model`: the unit uses no model
- `CLASS_REASONS.no_evidence`: no provisioning or server evidence was recorded for the unit
- `CLASS_REASONS.model_verified`: the model file was not verified against the hub or the lock, or its record changed
- `CLASS_REASONS.server_verified`: the server archive was not verified, or its record changed
- `CLASS_REASONS.download_hosts`: a file came from a host other than the one named, or from a private address
- `CLASS_REASONS.model_path`: the server did not report the verified model file as the one it loaded
- `CLASS_REASONS.ledger_host`: a model call went to a host other than the started server
- `CLASS_REASONS.model_served`: a reply named a model other than the requested alias
- `CLASS_REASONS.harness_measurement`: the harness itself did not count the run as a measurement
- `CLASS_REASONS.participation`: the unit did not run to a valid result
- `CLASS_REASONS.verified`: every provenance condition held: a verified model file served by a verified server on this runner
- `CLASS_REASONS.hosted_host`: a hosted call went to a host other than the configured one
- `CLASS_REASONS.hosted_verified`: the configured host answered every call: a hosted API result, not a measurement on this runner

## Columns

Every column of every summary table (`lab.notes.COLUMNS`). Numbers on this runner describe this runner; numbers on
synthetic worlds describe synthetic worlds.

| column | means | does not mean |
| --- | --- | --- |
| Request | the request file's path, name and short sha | n/a: not a number |
| name | the request's name, its file name without .json | n/a: not a number |
| sha | the first twelve hex digits of a sha256 | n/a: an identifier, not a number |
| Purpose: | the request's purpose, as written; public | a description of what the run found |
| Provider | fake (the plumbing server) or the manifest's server program | n/a: not a number |
| Commit | the git commit the plan or report was made from | n/a: not a number |
| Plan | the first twelve hex digits of the plan file's sha256 | n/a: not a number |
| Job minutes | the request's job_minutes: a shard holds this many minutes less the shard overhead | how long the run took |
| Shards at once | the request's max_parallel: shard jobs that may run at the same time | how many ran at once |
| Days kept | the request's retention_days: how long the run's artifacts are kept | how long logs, summaries or the run itself are kept |
| Source | which file a plan error is about: request, manifest, lock, event or plan | n/a: not a number |
| Path | the JSON path of the refused value | n/a: not a number |
| Problem | why the value was refused, or a lock or hosted problem | n/a: not a number |
| Notice | why a push ran nothing | n/a: not a number |
| Requests | the request files a merge brought, each to run by dispatch | n/a: not a number |
| model | the model's key in lab/models.json | n/a: not a number |
| kind | the manifest kind: gguf, fake, hosted, or none for a model-free shard | n/a: not a number |
| alias | the name the server serves the model under; for a hosted model, the host's model id | n/a: not a number |
| repository | the model's hub repository | n/a: not a number |
| file | the model file in that repository | n/a: not a number |
| revision | the manifest's revision: a commit or a branch | the commit a run used (see Model commit) |
| pin | the locked commit and file sha, or the not-pinned sentence | that the file was downloaded |
| shard | the shard id | n/a: not a number |
| units | the unit ids a shard runs, in order | n/a: not a number |
| planned minutes | the sum of the shard's units' minutes from the request | how long the shard ran |
| job timeout minutes | the planned minutes plus the shard overhead: the job's time limit | how long the job ran |
| unit | the unit id | n/a: not a number |
| experiment | e1, e2, e3, g0, sim, x1 or openfda | n/a: not a number |
| minutes | the unit's minutes from the request: its time budget | how long it ran |
| seed | the unit's (first) seed | a quantity: seeds only select a synthetic world |
| entry | the provision entry: server, or gguf- and the model key | n/a: not a number |
| cache key | the actions/cache key the provision job restores | n/a: not a number |
| Failed step | the first workflow step of the shard job that failed or was cancelled | n/a: not a number |
| Prepare problem | why the shard's server or model could not be prepared | n/a: not a number |
| Provenance complete | whether the shard's provenance file was completed after its last unit | that the units passed |
| Interrupted | whether the shard was stopped by a signal | a failure of the model |
| Units by status | how many units of the shard ended in each status | measurements |
| Run attempt | the GitHub run attempt that produced the shard | n/a: not a number |
| Planned units without a record | planned units that left no unit record | n/a: not a number |
| CPU | the runner's CPU model name | the hardware a site would use |
| CPUs available | the CPUs the shard's process could use on the runner | a site's CPU count |
| Memory GiB | the runner's total memory | the memory a model needed |
| OS | the runner's operating system | n/a: not a number |
| Python | the runner's Python version | n/a: not a number |
| Server tag | the pinned server release tag | n/a: not a number |
| Server version | the server binary's own --version line | n/a: not a number |
| Server archive sha | the first twelve hex digits of the server archive's sha256 | n/a: not a number |
| verified by | how a file was verified: lock, hf-api, github-api or first-use | that a first-use file is trustworthy |
| Model repository | the hub repository of the served model file | n/a: not a number |
| Model file | the served model file | n/a: not a number |
| Model commit | the hub commit the model file came from | n/a: not a number |
| Model file sha | the first twelve hex digits of the model file's sha256 | n/a: not a number |
| status | the unit's status (see Statuses) | a verdict on the model |
| class | the unit's display class (see Classes) | n/a: not a number |
| exit code | the harness's or shard's exit code (see Exit codes) | a model result |
| wall seconds | seconds from the unit's or shard's start to its end on the runner, setup included | model latency alone |
| reason | why a unit has its status, or why a channel has no alerts | n/a: not a number |
| state | the shard artifact's state (see Statuses) | n/a: not a number |
| attempt | the run attempt of the artifact the report used | n/a: not a number |
| failed step | the first failed or cancelled step of the shard job | n/a: not a number |
| workload | the E3 workload: extraction or short, synthetic filler text | a real document |
| concurrency | how many requests E3 sent at once | how many users a site has |
| measured | requests timed after the warm-up requests | requests that succeeded |
| ok | measured requests that succeeded | that their answers were correct |
| end to end median s | the median seconds from sending a request to its last token, on this runner | what a site's hardware would see |
| end to end ninety-fifth percentile s | the ninety-fifth percentile of those seconds, on this runner | a worst case |
| first token median s | the median seconds to the first streamed token, on this runner | what a site's hardware would see |
| decode tokens per s median | the median output tokens per second after the first token, on this runner | throughput under other loads |
| requests per s | successful requests per second over the cell's wall time, on this runner | a capacity planning number for a site |
| harness measurement | whether the harness itself counted its run as a measurement | that the lab's provenance checks passed (see class) |
| pack | the domain pack the synthetic world was generated from | n/a: not a number |
| records | records in the run: generated plus planted, or the scan's size | real records |
| passed | whether the canary scan passed: nothing crossed and its positive control found its canaries | that no leak is possible: the scan reads text only |
| canaries planted | canary strings planted in the synthetic records | real secrets |
| canary hits | planted canaries found in what crossed a boundary | every possible leak |
| shingle overlap bytes | bytes of narrative text found, as shingles, in what crossed a boundary | leaks through timing, sizes or counts |
| positive control hits | canaries the scan found in a site's own database, proving it can find them | a leak |
| model path problems | how many problems G0 found in its model path (no model answer, degraded verdicts) | a leak |
| world digest | the first twelve hex digits of the synthetic world's digest | n/a: not a number |
| task | the model task the calls were for | n/a: not a number |
| calls | successful model calls in the ledgers | every call made |
| median ms | the median latency of those calls in milliseconds | a site's latency |
| ninety-fifth percentile ms | the ninety-fifth percentile latency of those calls in milliseconds | a worst case |
| target | what a provision record is for: the server or a gguf model | n/a: not a number |
| verified | whether the file matched its pin or published digest | that a first-use file is safe |
| first use | whether the file was trusted on first use, with no published digest to check | that it was verified |
| plan matches | whether the provision record belongs to this run's plan | n/a: not a number |
| seconds | seconds the provision step took | download speed elsewhere |
| Lock status | unchanged, new_entries, conflict or not_computed | n/a: not a number |
| New entries | files this run verified that the lock does not pin yet | n/a: not a number |
| CPU models | every CPU model the run's shards ran on | comparable timings across models of CPU |
| Artifacts of shards not in the plan | artifacts the report ignored | n/a: not a number |
| Skipped by the plan | units the plan removed, with the reason | failed units |
| digests | the world digests a group of units saw | n/a: not a number |
| consistent | whether a group of units saw one world | that their results agree |
| Units | units in the plan | n/a: not a number |
| Shards | shards in the plan | n/a: not a number |
| channel | the detection channel (see the channel labels) | n/a: not a number |
| found | planted patterns the channel found in their window, chance finds included | patterns found because of the plant |
| found net of chance | finds the no-plant control did not also make as early or earlier | finds that would hold on real data |
| chance finds | finds the no-plant control also made as early or earlier | false alarms |
| patterns | planted pattern units: patterns times seeds | real problems |
| recall | found over patterns | recall on real data |
| precision in the top forty | the share of true finds among the channel's forty highest-ranked alerts | precision on real data |
| average precision | average precision of the channel's alert ranking against the planted labels | precision on real data |
| alerts | alerts the channel raised in the evaluation weeks | a count of real problems |
| false alarms | alerts that matched no planted pattern window | alerts that are wrong on real data |
| lift | which lift: the difference between two channels' finds, between two J2 arms (B minus A, C minus A, D minus A, D minus B, D minus C), between two J3 arms (the ten lifts of A002's rule 10, named by their letters) or between a J3 composite arm and one of its ablations | n/a: not a number |
| estimate | the bootstrapped mean of a lift or ratio | an estimate when patterns are few |
| interval low | the low end of the bootstrap interval | a guarantee |
| interval high | the high end of the bootstrap interval | a guarantee |
| candidates | candidates verified by pushdown, or E2's candidates in all | true patterns |
| true | candidates whose key is a planted pattern in its window | real problems |
| supported | candidates pushdown ended as supported | that they are true |
| pushdown average precision | average precision of the pushdown score over the verified candidates | a result on real data |
| detector-score average precision | average precision of the detector's own score over the same candidates | a result on real data |
| raw text bytes crossed | narrative bytes found in what crossed the simulation's boundaries | leaks through timing, sizes or counts |
| lexical fallback share | the share of records the lexical extractor read because the model failed | the model's accuracy |
| records done | records the simulation extracted before it stopped or ended | n/a: not a number |
| extraction median s | the median seconds of one extraction call on this runner | a site's latency |
| judge median s | the median seconds of one judge call on this runner | a site's latency |
| estimated minutes | the run's estimated minutes: measured for a complete run, projected for a skipped one | a promise for the next run |
| suggested minutes | the minutes to request next time: a quarter above the estimate, rounded up | a guarantee that the next run fits |
| plant | the plant spec that planted the patterns | n/a: not a number |
| weeks | weeks of the synthetic world | n/a: not a number |
| labels | the E1 labels: their source, records and claims | human labels |
| claims | labelled claims in the E1 labels | n/a: not a number |
| prereg sha | the first twelve hex digits of a preregistration file's sha256 | n/a: not a number |
| Model-free rehearsal | E2's rehearsal with fake judges, which sizes the run before any model runs | a model result |
| site judge calls | judge calls the rehearsal made at the sites | calls a model made |
| most records in one central reading | the largest number of records one central reading held | a model result |
| field F one | micro F1 over (record, field, value) of the model's claims against the labels, pooled over repeats | accuracy on real narratives |
| claim F one | F1 over whole claims against the labels, pooled over repeats | accuracy on real narratives |
| valid JSON share | the share of extraction calls whose first reply was valid | accuracy |
| zero-claim share | the share of scored records, pooled over repeats, whose prediction holds no claim | records without a finding: the gold of a record can be empty too |
| model mismatch | whether a reply named a model other than the requested one | n/a: not a number |
| drop reason | a post-processing reason from the harness's run.json: a dropped reply item, or a reattached_ reason (a predicate kept on the record's structured entity after the model named an entity that did not resolve) | a loss when it starts with reattached |
| reply items | reply items counted under the reason, summed over the model's repeats | records |
| against | the reference model of a paired comparison | n/a: not a number |
| decision metric | which difference decides non-inferiority: micro field F1, or the per-record mean field F1 of a harness without it | n/a: not a number |
| difference | the model's decision metric minus the reference's over the shared records | a difference on real narratives |
| mean difference | the mean per-record field F1 difference over the shared records | the decision when the decision metric is micro field F1 |
| sign test p | the exact sign test's p-value of the per-record differences | an effect size |
| underpowered | whether fewer records were paired than the preregistered minimum | n/a: not a number |
| non-inferior | whether the interval's low end stayed above minus the margin | a decision about real data |
| kill flag | whether the model's field F1 fell below the preregistered kill threshold | a decision about real data |
| margin | the preregistered non-inferiority margin | n/a: not a number |
| reference | the preregistered reference model | n/a: not a number |
| repeats | repeats per model | n/a: not a number |
| condition | an E2 condition: stats_only, central_raw, central_allowed or pushdown | n/a: not a number |
| precision in the top k | the share of true candidates among a condition's top k | precision on real data |
| pushdown over central raw | pushdown's average precision over central raw reading's | a product number |
| pushdown raw text bytes | narrative bytes found in what pushdown sent across boundaries | leaks through timing, sizes or counts |
| bar verdict | whether the run met the preregistered bar (shown only when the harness gives it) | a decision about real data |
| withheld because | why the harness withheld the bar verdict | n/a: not a number |
| central | E2's central comparator: self, or a hosted model key | n/a: not a number |
| protocol minimum | the protocol's minimum candidates | n/a: not a number |
| decoy | candidates whose key is a planted decoy | false alarms on real data |
| background | candidates that are neither pattern nor decoy | false alarms on real data |
| projected minutes | E2's projected minutes: rehearsal call counts times warm-up latencies | a measurement |
| unit minutes | the unit's minutes | n/a: not a number |
| share the projection may use | the share of the unit's minutes the projection may fill | n/a: not a number |
| seeds | seeds of the run | n/a: not a number |
| why no alerts | why an openFDA channel raised no alerts | n/a: not a number |
| source | where E1's labels came from: generator, fixtures or nhtsa (real public complaints scored against their filed codes) | human labels |
| paired records | records both the model and the reference scored | n/a: not a number |
| underpowered below | the paired-record count below which the comparison is underpowered | n/a: not a number |
| kill flag below | the field F1 below which the kill flag is raised | n/a: not a number |
| repeats used | the model's valid repeats its own scores pool, of the repeats planned, and which (in code) | repeats that ran: a timed-out or failed repeat is left out |
| predicate F one | micro F1 over the predicate fields (each claim's predicate, negated as not:) of the model's claims against the labels, pooled over the repeats used | accuracy on real narratives |
| lexical predicate F one | the lexical extractor's predicate micro F1 on the same labelled records, counted as E1 counts a model's claims (`labels.public.lexical` in report.json) | a model result |
| transport failure share | the share of the model's record runs whose extraction ended in a transport failure (not scored) | a model error |
| drops and re-attachments | the model's post-processing counts by reason, summed over the repeats used; reattached_ reasons are predicates kept on the record's structured entity | a loss when it starts with reattached |
| why no scores | why a model with valid repeats has no own scores: the harness's reader refused one of them | a model result |
| Re-aggregation request | the re-aggregation request's path and short sha | n/a: not a number |
| Re-aggregation purpose: | the re-aggregation request's purpose, as written | n/a: not a number |
| Shards' commits | the commits the sealed shards ran at, from their provenance | the re-aggregating commit |
| shards' run ids | the GitHub run ids the sealed shards recorded | n/a: not a number |
| lab code differs from the shards' | whether this commit's lab code hash differs from one a sealed shard recorded | that the units would give other numbers |
| exceeds | whether the projection was above its share of the minutes | n/a: not a number |
| eligible | whether X1's preregistered conditions for a verdict held | a pass |
| blind | whether the plant was declared blind | that it was |
| extractor | the extractor the harness used | n/a: not a number |
| recalls in scope | public recalls the replay could score | every recall |
| recall rate | the share of those recalls a channel alerted on before the recall | a confidentiality result |
| median lead days | the median days from a channel's first alert to the recall | a promise of warning time |
| post-recall alerts | alerts that came after the recall | n/a: not a number |
| false alarms per week | alerts per week that matched no recall, on the request's product codes only | false alarms on partner data, or on the manufacturer's other product codes |
| dataset | the openFDA dataset: event or recall | n/a: not a number |
| code | an openFDA product code | n/a: not a number |
| total | records openFDA reported for the query | records fetched |
| fetched | records the lab fetched | every record |
| truncated | whether the fetch stopped at max_records_per_code | n/a: not a number |
| sheet | the labelling sheet: N1 or E1 | n/a: not a number |
| requested | rows the request asked for | n/a: not a number |
| written | rows written to the sheet | rows labelled |
| protocol records | the protocol's canary scan size | n/a: not a number |
| max calls | the request's max_calls for the hosted key | calls made |
| planned bound | the most calls the plan's units can make on the host | calls made |
| calls used | hosted calls the shard made | n/a: not a number |
| shard share | the shard's share of the hosted calls | n/a: not a number |
| tokens in | input tokens the host reported | tokens of calls without counts |
| tokens out | output tokens the host reported | tokens of calls without counts |
| calls without token counts | calls the host reported no token counts for | n/a: not a number |
| estimated USD | reported tokens times the manifest's prices | the host's bill |
| priced | whether the manifest gives the hosted model a price | n/a: not a number |
| preflight | the hosted preflight's outcome | n/a: not a number |
| preflight calls | calls the preflight made | n/a: not a number |
| Scheme | the hosted base URL's scheme | n/a: not a number |
| Host | the hosted base URL's host and port, never its path | n/a: not a number |
| model listed | whether the host listed the model id | n/a: not a number |
| model id | the host's id of the hosted model | n/a: not a number |
| visibility | which records of a planted pattern carry it: narrative_only, codes_only or both | n/a: not a number |
| label | the collective harness's own label for the row | n/a: not a number |
| recall outcomes seen before the preregistration | the requester's declaration `saw_recall_outcomes`: whether they saw recall outcomes before fixing the codes, manufacturers and settings | that the settings were frozen blind when it says no |
| warnings | the harness's own warnings for the row, as written | every problem of the run |
| parts finished | a J1 model's parts that finished (status ok, every question judged), of the parts planned | parts that ran: a stopped or timed-out part is left out |
| balanced accuracy | the mean of sensitivity and specificity against the filed codes; every record has one positive and one negative question, so it equals the accuracy; its interval is a percentile bootstrap over records | accuracy against checked labels: the filed codes are not truth |
| lexical balanced accuracy | the lexical judge's balanced accuracy on the records the model was scored on | a model result |
| lexical judge, every record | the lexical judge's scores on every record, from the plan job | the model's records |
| control balanced accuracy | the record-blind control's balanced accuracy on the records the model was scored on: what knowing which components are filed most in NHTSA's whole complaint file gets a judge without reading the record (it confirms the four filed most, fixed before any run); it decides nothing | how far the predicate alone could get a judge on these records: the predicate-only bound shows that |
| record-blind control, every record | the record-blind control's scores on every record, from the plan job | the model's records |
| predicate-only bound | the most any judge that sees only the predicate could score on the records the model was scored on: per predicate, the larger of its positive and negative counts, over the questions; it is fitted to their answers, has no interval and decides nothing | what a judge that reads only the predicate would score: it is optimistic, above one half even where the predicate tells little |
| predicate-only bound, every record | the predicate-only bound on every record, from the questions | the model's records |
| headline | J1's rule 7: `better` when the model's interval is above the lexical judge's balanced accuracy, `worse` when below, `not_told_apart` otherwise; shown only for a complete model measurement | a result corrected for three comparisons, or a pooled verifier verdict |
| sensitivity | the share of positive questions (a component the complaint was filed under) the judge confirmed | the share of real failures it would find |
| specificity | the share of negative questions (a component drawn from those it was not filed under) the judge refuted | the false alarm rate on hard negatives: most negatives name a component the narrative never mentions |
| unknown share | the share of questions answered unknown: unclear answers, a no or unclear on the vehicle, and model failures | a transport failure: those records are left out |
| records left out | records with a transport failure, left out of the model's scores; more than one in a hundred withholds the headline | model errors |
| why no headline | why a J1 model has no headline: incomplete, not a model measurement, or withheld | a model result |
| questions | J1's and J2's questions: one positive and one negative per record | n/a: not a number |
| parts | the parts J1's or J2's records are cut into, one unit per model and part | n/a: not a number |
| arm | a J2 arm: `shipped` (the product's judge unchanged), `shipped_mapped` (its replies through the mapping), `negation` (the lab-side instruction) or `negation_mapped` (that instruction with the mapping); or a J3 arm: `negation` and `negation_mapped` (A001's controls), `evidence` (the evidence task's answers as answered), `evidence_grounded` (a yes read as no when the citation is not in the record), `evidence_grounded_mapped` (that and the mapping), `rationale` and `rationale_mapped` (the rationale task's answers, as answered and mapped), `composite` and `composite_mapped` (confirm where the lexical judge confirms, else the grounded arm's verdict) | a product behaviour: the product runs the shipped arm |
| mapped | whether the arm reads the replies through the mapping (an unclear component answer as a no) before the verdict rule | a change to the product's verdict rule |
| verdict word | rule 10's word for the (model, arm) pair, in the order incomplete, pass_provisional, partial, failure: computed from the figures beside it | a result for any pair but the primary: a secondary's word is a hypothesis for the next cycle |
| pass test | whether the pair met every clause of the pass test: balanced accuracy at or above the target, the paired difference to the lexical judge with its low end above zero, the balanced accuracy's low end above the predicate-only bound on the same records, and the unknown share reported | a confirmed success: a first pass is provisional until the next cycle's fresh draw |
| primary pair | whether the pair is the rule's one primary pair P, the only pair the verdict is read on | the best pair of the run |
| gap to the target | the target balanced accuracy minus the pair's balanced accuracy; below zero, the target is reached | a distance on real failures: the key is the filed codes |
| target balanced accuracy | the pinned target the loop runs toward: balanced accuracy 0.860 against the filed-category key | a threshold any pair but the primary is judged on |
| lever | which lever a J2 lift reads: `unclear_mapping` (B minus A, D minus C), `negation_instruction` (C minus A, D minus B) or `both` (D minus A); which a J3 lift reads: `evidence_field`, `grounding_check`, `evidence_field_and_check`, `unclear_mapping`, `rationale_field` or `lexical_disjunction`; for a J3 ablation difference, the ablation (`lexical_or_no`, `lexical_or_record_blind`) | n/a: not a number |
| reading | a lift's reading: `helps` when its interval's low end is above zero, `hurts` when its high end is below zero, else `not_told_apart` | a decision toward the target: the lifts decide nothing |
| minus the lexical judge, paired by record | the arm's balanced accuracy minus the lexical judge's, record by record over the records both score, with its paired percentile-bootstrap interval | a difference against checked labels |
| eligible records | the complaint file's records eligible for the draw before any exclusion: the makes, the window and the eligibility rule of `lab.goldlabels` | records the models judged |
| excluded records | the record refs the verifying freshness mechanisms gave, removed from the eligible list before the draw | records left out for a failure |
| eligible after exclusion | the eligible records less the excluded ones; below the draw's size the plan stops | n/a: a count before the draw |
| overlap with the excluded set | records of the draw that are also excluded; above zero the plan stops, so it is zero in every plan that ran | n/a: a check |
| artifact mechanism | mechanism one's outcome: `verified`, or why the earlier run's plan artifact did not verify (`artifact absent`, `sha256 mismatch`, `manifest mismatch`, `refs mismatch`, `labels mismatch`, `malformed`) | a model result |
| rebuild mechanism | mechanism two's outcome: `verified` when the rebuilt seed-one draw hashes to the pinned prefix, else `sha256 mismatch` (the complaint file changed) or `malformed` | a reason to stop by itself: one mechanism verifying suffices |
| excluded refs file sha | the first twelve hex digits of the sha256 of `prereg/j2/excluded.jsonl`, the refs the next cycle excludes | n/a: an identifier |
| complaint file sha | the first twelve hex digits of the sha256 of the complaint archive the plan job read | n/a: an identifier |
| comparisons since the loop began | the running count of (model, arm, cycle) comparisons the loop has made, this cycle's included: the multiplicity the verdict rule prints | a correction applied to any interval |
| minus ablation A, paired by record | a J3 composite arm's balanced accuracy minus ablation A's (confirm where the lexical judge confirms, else refute), record by record over the records both score, with its paired interval; on a draw where the lexical judge never answers unknown it equals the difference to the lexical judge | a difference for any arm but the two composite arms |
| minus ablation B, paired by record | a J3 composite arm's balanced accuracy minus ablation B's (confirm where the lexical judge confirms, else the record-blind control's verdict), record by record, with its paired interval | a difference for any arm but the two composite arms |
| ablation A, every record | ablation A's scores on every question of the fresh draw, computed in the plan job | a model result |
| ablation B, every record | ablation B's scores on every question of the fresh draw, computed in the plan job | a model result |
| lexical judge unknown verdicts | how many questions of the fresh draw the lexical judge answered unknown; at zero, ablation A is the lexical judge line for line and the ablation-A clause repeats the pass test's lexical clause | a model figure |
| arm letter | the letter the rule names a J3 arm by (N, D, E, Eg, Egm, R, Rm, C, Cm), the lifts' names being differences of letters | n/a: a label |
| headline secondary | whether the arm is the rule's headline secondary (`evidence_grounded`), whose verdict word is printed beside the primary's as the primary under the other reading of the standing default | a primary pair: its word is a hypothesis, never a result |
| replies | task E replies the model answered (a model failure after the repair counts, a transport failure does not), over the finished parts | questions planned |
| grounded share of yes answers on positives | of task E's yes answers on positive questions, the share whose citation is a verbatim substring of the record text (the count of grounded of the count of yes beside it) | a measure of evidence quality: a substring of the record is not a reading of it |
| grounded share of yes answers on negatives | the same share on negative questions: a check that removes yes answers here and keeps them on positives is the lever working | a measure of evidence quality |
| empty citation share | the share of task E replies whose citation is the empty string | a refusal rate: an empty citation with a no is the instruction followed |
| citation length median | the median length in characters of task E citations after the declared normalisation | a quality |
| citation length ninety-fifth percentile | the ninety-fifth percentile of that length | a quality |
| first key is the new field | the share of a task's replies whose first key, as the model wrote it, is the task's new field (citation or analysis); n/a for task N | a result: the share decides nothing |
| attempts | model calls made for the task over the finished parts, repairs included | questions |
| tokens out median | the median output tokens of those calls, from the ledgers | a cost on site hardware |
| tokens out ninety-fifth percentile | the ninety-fifth percentile of output tokens | a cost on site hardware |
| replies cut at the cap | calls whose reply the server cut at the task's token cap (finish reason length); each gets the runtime's one repair | a model failure count: a repaired reply is scored |
| sources | the five sources of the two earlier cycles' records, each with its outcome and the refs it gave: `J1` and `A1` (the earlier plan artifacts), `R1` and `R2` (the rebuilds from today's complaint file), `K` (the first accuracy test's chain file) | records the models judged |
| cycles established by | how each earlier cycle's record set was established: `artifact`, `rebuild` or `chain` (the chain less the other cycle's set), with the refs | a model result |
| consistency counts | the four counts that must be zero before the draw: `j1_r1` and `a1_r2` (the artifact against the rebuild, when both verified), `outside_chain` (verified refs the chain lacks), `chain_union` (the chain against the union of the two sets); n/a when a side did not verify | a model figure |
| chain file lines | the lines of `prereg/j3/excluded.jsonl`: every excluded ref and the draw's, for the next cycle | records the models judged |
| questions finished | an L1 model's question units that finished (status ok, a complete run of this preregistration, every routed mine's verdict, whole timing and records), of the slots planned | questions that ran: a unit that did not finish, failed a check or was a re-run after a model call is left out |
| minus the lexical judge | D_lex: the model's balanced accuracy over the scored site answers minus the lexical judge's, with its paired percentile-bootstrap interval over mines (K3) | a per-record difference: the per-record measures are in report.json |
| minus the route-role baseline | D_route: the model's balanced accuracy minus the route-role baseline's, on the same draws (K3) | a reader of records: the baseline reads none |
| route-role balanced accuracy | the route-role baseline's balanced accuracy on the same site answers: it confirms where HQ routed a mine as contributing and refutes at a sibling (K2) | a judge that reads |
| record-blind balanced accuracy | the record-blind control's balanced accuracy on the same site answers: it answers by which question was asked; it decides nothing | what the alert alone tells HQ (K1) |
| site answers left out | an L1 model's scored site answers with a transport failure in any call, left out of every judge's scores for that model, of its scored site answers; more than one in twenty gives no verdict | model failures, which count as they fall |
| time to answer s | seconds from the call to the gate's first decision, on this runner: the median over the scope's finished questions without a contended mine (one alert gives one time) | a contended question's time (K6), which is apart; site hardware, the detection, the model's loading or any transport between machines |
| time to answer ninety-fifth percentile s | the ninety-fifth percentile of those times (`stats.percentile`); with one alert, that one time | a tail estimate from five questions |
| contended time to answer s | the median time to answer of the scope's finished questions with a contended mine, timed while a late call of the same question still ran on the one server (K6) | a time the product's own servers would give: there each mine has its own |
| questions not finished | the scope's question units that did not finish: not within the unit's budget, a failed check, no result or a re-run not taken | a question that took no time: a budget's stop shows its elapsed time as a lower bound |
| elapsed s, a lower bound | for a question unit whose timed path did not reach the gate's first decision within its budget (rule 11), the seconds it ran before the unit stopped it: its time to answer is at least this | a time to answer; the model gets no verdict while one of its questions did not finish |
| question build s | the median seconds from the call's start to the first mine's start: the window, the question, the routes | a mine's time |
| gate s | the median seconds from the last mine's end to the call's return: the last intake, the gate and the stored conclusion | the late verdicts' collection |
| mine median s | the median of a mine's seconds, its handler's start to its return (a timed-out mine ends at its deadline), contended mines apart | a contended mine: those are in report.json |
| mine ninety-fifth percentile s | the ninety-fifth percentile of those mine times | a contended mine |
| judge call median s | the median latency of the finished questions' successful judge calls, from the mines' ledgers | a call's time on a site's own server: on the one server the question part is cached after the first mine (K5) |
| judge call ninety-fifth percentile s | the ninety-fifth percentile of those latencies | a site's tail |
| model calls | the median number of judge calls per finished question: the retrieved records of its routed mines | HTTP attempts: repairs and retries are in run.json |
| at once s | derived: the question build plus the slowest mine plus the gate, what asking every mine at once would take if each had a server like this runner's | a time that ran |
| default deadline s | derived: the time had the product's default deadline of 600 s held, each mine over it counted as 600 s and an HQ timeout | a time that ran |
| no model s | the lexical path's time to answer on the same runner (the unit's lexical rerun) | a model's time |
| scope | `alert` (the alert question) or `all` (the alert and the controls together) | n/a: not a number |
| slot | the question slot: 1 the alert question, 2 to 5 the controls in sorted predicate order | n/a: a label |
| question kind | `alert` or `control` | n/a: not a number |
| contributing mines | the mines HQ routed as contributing for the question: a codes or text-only cell of its key in the window (K1) | the mines that confirm |
| codes contributors | contributing mines with a codes cell of the key in the window (K2) | mines where the key confirms by reading |
| text-only contributors | contributing mines with only text-only cells of the key in the window (K2) | mines where the key confirms |
| siblings | mines routed as siblings, at most two, chosen by cell volume | mines with records in the window |
| records retrieved | the records the question's routed mines retrieve with the codes hidden, preregistered; a model makes one call per record | every record of the window: the retrieval is cut at the pack's verify_max_records |
| key's gate status | the gate's status on the key judge's verdicts at the first decision, preregistered | a checked truth |
| MSHA file | the sha256 of MSHA's accident file the run read | n/a: not a number |
| the demo's file | whether that file is the one the drafter demo read (same sha256 and size) | n/a: not a number |
| alert | the audit's alert: channel, week and predicate | a checked event |
| empty draws share | the share of rule 8's bootstrap draws over mines with no key confirm or no key refute; above 5% the plan stops (K7) | a model result |
| constant status agreement | the number of questions with the key's most frequent gate status: what a constant status would agree on (K8) | a judge's skill |
| gate decisions equal to the key's | questions whose gate status at the first decision equals the key's, of the finished questions | a correct decision beyond the constant status: read it beside that column |
| contended questions | finished questions with a mine timed while a late call of the same question still ran (K6) | a slow question |
| judge | the comparator: `lexical`, `route_role`, `record_blind` or `key` | n/a: not a number |
| gate status, first decision | the gate's status on the model's verdicts when the call returned (K6) | the status after the late verdicts, which is in report.json |
| mines | the mines of the audited operator, each a site | n/a: a count of sites, not records |
| operator | the audited operator's label (`c1`) | a name or an id: none is written |
| predicate | the question's predicate, a filed category of the drafted pack | n/a: not a number |
| term set sha | the sha256 of `prereg/jt/terms.json`'s bytes, in full: the term set's sha the amendment commits | a term or anything that shows one |
| cutoff | the cutoff, `NHTSA_WINDOW`'s first day: the drafting set holds only complaints received before it | a setting of the request: it is the rule's |
| drafting window | the drafting window's first and last day (D002's NHTSA training window) | the judged window |
| complaint archive sha | the first twelve hex digits of the complaint archive's sha256 on the day of the term run | n/a: an identifier, not a number |
| complaint archive bytes | the complaint archive's size in bytes | the export's size |
| drafting export rows | the pooled drafting export's rows, after the cross-make drop | complaints with a narrative |
| rows per make | each make's rows in the pooled export, in make order, after the cross-make drop | the make's share of anything judged |
| cross-make duplicates dropped | rows dropped because an earlier make already gave their complaint number (C28) | duplicate narratives |
| earliest received | the earliest received date of the pooled export (a date, never a ref) | the drafting window's first day |
| latest received | the latest received date of the pooled export | the cutoff |
| drafting refs sha | the first twelve hex digits of `prereg/jt/drafting_refs.jsonl`'s sha256 (the sorted record ids) | n/a: an identifier, not a number |
| drafting text sha | the first twelve hex digits of `prereg/jt/drafting_text.jsonl`'s sha256 (the narratives' hashes) | n/a: an identifier, not a number |
| distinct narratives | distinct non-empty folded narratives of the drafting export | the drafter's corpus |
| straddling rows | drafting rows whose complaint also has a row received on or after the cutoff (C4) | records removed from the draw: the judging run removes them |
| corpus records | the drafter's corpus: training rows with a narrative | the export's rows |
| categories passing the floor | filed categories that pass the privacy floor (ten records at three states) | specific predicates |
| specific predicates drafted | the drafted pack's specific predicates | hand predicates reached |
| other bucket share | the share of corpus rows the drafter put in its other bucket | an error rate |
| rows without a specific category | corpus rows with no specific drafted category | rows without a narrative |
| candidate terms | the drafter's candidate terms | terms in the term set |
| eligible terms | candidate terms that pass the drafter's floor and thresholds | terms in the term set |
| placeholders | P_draft's predicates that learned no term and got the placeholder | hand predicates left empty |
| refused categories | categories the drafter's refusal removed | categories below the floor |
| refused terms | terms the drafter's refusal removed (record ids, states and vehicle words) | terms below the thresholds |
| refused terms that would have been assigned | refused terms that would have been assigned to a predicate | terms in the term set |
| refused terms within the cap | refused terms that would also have been within the per-predicate cap | terms in the term set |
| hand predicates | the hand pack's specific predicates: the term set's entries (26) | predicates reached |
| reached | hand predicates whose NHTSA spelling a specific drafted category holds | predicates with terms: a reached one can have none |
| left empty | hand predicates whose term list is empty (the judging run gives them the placeholder) | predicates the drafter refused |
| terms per reached predicate, min | the fewest terms of a reached hand predicate | a quality of the terms |
| median | the median terms of a reached hand predicate (the upper middle, as the drafter's summary) | a quality of the terms |
| max | the most terms of a reached hand predicate (at most fifty) | a quality of the terms |
| total terms | every term of the term set | distinct words |
| check passed | whether every check of `check_pack` passed on the drafted pack against its export | that no name can pass: the floor is mechanical |
| check | one check of `check_pack`: the loader, the term floor, the value floor, the refused strings and terms, the template rebuild or the n-gram scan | n/a: not a number |
| failures | the check's failure count (n/a for the loader and the template rebuild, which have none) | the strings that failed: none is printed |
| drafted pack hashes | the first twelve hex digits of the drafted pack's four loader hashes | n/a: identifiers, not numbers |
| hand pack with the drafted lexicons, hashes | the first twelve hex digits of P_D's four loader hashes: the hand pack with the term set as its lexicons | n/a: identifiers, not numbers |
| hand pack hashes | the first twelve hex digits of the hand pack's four loader hashes | n/a: identifiers, not numbers |
| code hashes | the first twelve hex digits of the onboard code hash, `lab/jt.py`'s code hash and the two data files' sha256 | n/a: identifiers, not numbers |
| step wall seconds | the plan job's term step in wall seconds, the export and the draft included | a time on site hardware |
| draft wall seconds | the draft subprocess's own wall seconds | the step's whole time |
| draft peak resident memory KiB | the draft subprocess's peak resident memory in KiB | the plan job's memory |
| verified by the unit | whether the unit verified the term set against its preregistration (its sha256, P_D's hashes, the code hash, the hand pack's hashes and the drafting files) | that the terms are good terms |

## Summary sections

Every section heading of the summaries (`lab.notes.HEADINGS`):

| section | what it holds |
| --- | --- |
| Lab plan | the plan: request, commit, provider, limits and purpose |
| Lab plan refused | why the plan refused the request, and the path and problem |
| Nothing to run | why a push ran nothing, and the dispatch hint |
| Models | the models the plan uses, with their pins |
| Shards | the shards: units, planned minutes and job timeouts (plan), or state, attempt and runner (report) |
| Units | the units: experiment, model, minutes and seed (plan), or status, class and wall time (shard) |
| Files to provision once per run | the server and model files the provision jobs download, with cache keys |
| Lab shard | one shard's summary |
| State | the shard's failed step, prepare problem, provenance and unit counts |
| Runner | the runner's CPU, memory, OS and Python |
| Model server and model file | the pinned server and model file a shard served, and how each was verified |
| Lab report | the report: request, plan, commit, counts and purpose |
| Units without a result | units that did not run to a result, with the reason |
| Model on runner CPU | units of display class model: measurements on this runner |
| Hosted API results: not measured on this runner | units of display class hosted-api |
| Unverified: not measurements | units of a real run whose provenance did not verify |
| Plumbing checks (fake provider): not model measurements | units a fake answered, or units of a plumbing run (a hosted unit's calls go to the configured host) |
| Units without a model | model-free units (X1, openFDA, JT) |
| Latency and throughput cells | E3's cells |
| Canary leakage scans | G0's scans |
| Model call latency from the ledgers | per task, the successful calls' median and ninety-fifth percentile |
| Multi-site simulation: planted patterns found per channel | the simulation's channels |
| Simulation lifts, bootstrapped over patterns | the simulation's lifts |
| Simulation pushdown verification | the simulation's pushdown candidates, raw text and fallback share |
| Simulation sizing for the next request | per sim unit, the measured medians and the suggested minutes |
| Baselines blind to narrative-only patterns by construction | per sim or X1 row, S and R_mf on the plant's narrative_only patterns, with the harness's label |
| Preregistration, fixed before any model runs | E1's labels, X1's and E2's prereg hashes, E2's rehearsal, J1's labels and questions, the lexical judge's and the record-blind control's scores on every record and the predicate-only bound; J2's labels, questions, exclusion counts and mechanisms, primary pair, target and comparators on every record; J3's labels, questions, exclusion counts with the five sources, the cycles and the consistency counts, primary pair, headline secondary, target, comparators and both ablations on every record; JT's term set under the term run's two headings |
| Extraction compared across models | E1's label, labels, reference and thresholds |
| Extraction per model, pooled over repeats | E1's per-model scores and zero-claim share |
| Extraction drops by reason, pooled over repeats | E1's post-processing counts per model and reason (`drops` of each endpoint in report.json), after the sentence that reattached reasons are not losses |
| Extraction paired against the reference | E1's paired comparison with the reference |
| Each model's own scores | E1's per-model scores from each model's valid repeats only (`endpoint_scores` in report.json), compared or not: predicate F1 and its interval beside the lexical extractor's, field F1, zero-claim and transport failure shares, drops and re-attachments |
| Judge test: the verifier's narrow question, per model, beside the lexical judge | J1's label, labels, questions and the lexical judge, the record-blind control and the predicate-only bound on every record, then per model: parts finished, class, balanced accuracy and its interval beside the lexical judge's, the control's and the bound on the same records, the headline, sensitivity, specificity, the unknown share and the records left out (the report's `j1` block, which also holds each judge's confirms per predicate) |
| Judge test: model minus lexical judge, paired by record | per J1 model, the mean per-record difference of balanced accuracy from the lexical judge's, with its paired percentile-bootstrap interval |
| Accuracy test: the verifier's narrow question on a fresh draw, four declared arms, one primary pair | J2's label, the labels and questions, the exclusion's counts, mechanisms and hashes (and the stand-in note in a dry run), the lexical judge, the record-blind control and the predicate-only bound on every record, the comparison count, the exclusion, lexical, control, arms and verdict notes, and the primary pair's sentence with its figures (the report's `j2` block, which also holds each arm's answer counts before and after the mapping, verdict counts and confirms per predicate) |
| Accuracy test: every (model, arm) pair beside the comparators on the same records | per J2 model and arm: task, mapped, class, parts finished of planned, balanced accuracy and its interval, the lexical judge's balanced accuracy and the paired difference with its interval on the same records, the control's balanced accuracy and the bound, sensitivity, specificity, the unknown share, the records left out, the verdict word, the pass test, whether the pair is the primary, and for the headline arm J001's headline and why there is none |
| Accuracy test: the lifts between arms, paired by record | per J2 model and lift: the lever, the paired records, the difference with its interval and its reading |
| Accuracy test, second cycle: the verifier's narrow question on a fresh draw, nine declared arms on one model, one primary pair | J3's label, the labels and questions, the exclusion's counts, sources, cycles, consistency counts and hashes (and the stand-in note in a dry run), the comparators and both ablations on every record with the lexical judge's unknown count, the comparison count, the exclusion, lexical, control, arms, verdict and evidence notes, and the primary pair's sentence with its figures and the headline secondary's word (the report's `j3` block, which also holds each arm's answer counts and confirms per predicate) |
| Accuracy test, second cycle: every arm beside the comparators and the ablations on the same records | per J3 model and arm: letter, task, reading, class, parts finished of planned, balanced accuracy and its interval, the lexical judge's balanced accuracy and the paired difference with its interval on the same records, the paired differences to both ablations for the composite arms, the control's balanced accuracy and the bound, sensitivity, specificity, the unknown share, the records left out, the verdict word, the pass test, whether the pair is the primary or the headline secondary, and for those two the headline and why there is none |
| Accuracy test, second cycle: the lifts between arms and the ablation differences, paired by record | per J3 model: each lift of rule 10 and each ablation difference with the lever or ablation, the paired records, the difference with its interval and its reading |
| Accuracy test, second cycle: the grounding check's readings on the evidence task | per J3 model: task E's replies, the grounded share of yes answers on positives and on negatives, the empty citation share and the citation lengths |
| Accuracy test, second cycle: per task, the first key, the output tokens and the replies cut at the cap | per J3 model and task: the first-key share, the attempts, the calls and their latency, the output tokens and the replies cut at the cap |
| Latency test: alert to answer on CPU, per model, beside the comparators | L1's label, the operator, the file and the audit, the alert, the questions (routes by role and stratum, records, the key's gate status), the headline and comparator notes, the comparators on every scored site answer and the predicate-only bound and empty draws, then per model: questions finished, class, balanced accuracy and its interval beside the lexical judge's, the route-role baseline's and the record-blind control's on the same answers, sensitivity, specificity, the unknown share and the answers left out; and D_lex, D_route, the headline and the gate's agreement beside a constant status (the report's `l1` block, which also holds the strata, the per-record measures and each question's verdicts and mines) |
| Latency test: where the time goes | the time note, then per model and scope (the alert, all questions) the CPU models, the time to answer and its ninety-fifth percentile (questions without a contended mine), the question build, the gate, a mine's and a judge call's median and ninety-fifth percentile, the model calls, the contended questions and their time to answer, and the questions not finished; then each question not finished with its elapsed time as a lower bound and its model calls (only when there is one), and each finished question's status, time and derived times |
| Latency test: the alert, the questions and the comparators, fixed before any model runs | (plan summary) L1's file, audit, alert, questions, the comparators' scores, the predicate-only bound, the empty draws share and the constant status agreement |
| Term run: the drafted term set, fixed before the judging run | JT's label, the term set's full sha256 (in the report, whether the unit verified it; in the plan summary, the sha of `prereg/jt/prereg.json`), the cutoff and the drafting window, the archive, the export's rows per make and pooled with the cross-make duplicates and the received dates, the drafting block, the drafter's and the refusal's counts, the term counts, P_draft's, P_D's and the hand pack's hashes, the code hashes, whether the check passed and the step's wall time and peak memory (the report's `jt` block); counts and hashes only, never a term |
| Term run: the privacy check of the drafted pack, by check | per check of `check_pack`: passed and its failure count, then the terms note |
| Pushdown verification against central reading: conditions | E2's labels and conditions |
| Pushdown ratio and verdict | E2's ratio and, for a hosted central, the bar verdict |
| Pushdown candidates | E2's candidates by label |
| Pushdown sizing | E2's projection and suggested minutes |
| Evaluation harness on a plant fixture: channels | X1's label and channels |
| Evaluation harness lifts, bootstrapped over patterns | X1's lifts, eligibility and the harness's warnings |
| openFDA public replay: recalls found per channel | the requester's declaration, the replay's warnings and channels, and what the false alarms cover |
| openFDA fetches | the openFDA fetch per dataset and product code |
| Labelling sheets for a human | the N1 and E1 sheets written for a person |
| Lock | the lock status and the sentence that says what to do |
| Notes | CPU models, world digests and the notes the units carry; a sim note some sim rows lack lists the units that carry it |
| Hosted calls and estimated cost | per hosted key, calls, tokens and the estimated cost |
| Skipped by the plan | units the plan skipped (hosted units without the secrets) |
| Hosted calls: allowed and planned | per hosted key, max_calls and the planned bound |
| Hosted endpoint | a shard's hosted host, secrets' states, preflight and calls |

## Artifacts and caches

| artifact | holds |
| --- | --- |
| `lab-plan-<run id>-<attempt>` | the plan, its preregistration and the plan summary; for a J2 plan the preregistration holds the fresh draw's labels and questions files (public complaint text and refs; the artifact, never the repository) and `prereg/j2/excluded.jsonl`, the refs the next cycle excludes; for a J3 plan the same under `prereg/j3/` with the two ablation files and `prereg/j3/excluded.jsonl`; for a JT plan `prereg/jt/`: the term set `terms.json`, P_draft (`pack/`), `check.json`, the two drafting files (record ids and narrative hashes) and `prereg.json`, never the export or a narrative |
| `lab-prov-<run id>-<attempt>-<entry>` | one provision record (`server` or `gguf-<key>`) and its lock candidate |
| `lab-run-<run id>-<attempt>-<shard>` | one sealed shard root |
| `lab-report-<class>-<run id>-<attempt>` | `report.json`, `report.md`, `report.sources.json`, `plan.json`, `lock-candidate.json` and E1's comparison files |
| `lab-reaggregate-<run id>-<re-aggregation run id>-<attempt>` | a re-aggregation's report directory, as `lab-report-` |

The workflow's summary steps pass `--log`, so `lab.summary` also prints into the job log, between
`=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===` and `=== MYCELIC-LAB <label> END ===` lines inside a
`::stop-commands::` window, the files labelled `plan/summary.md`; `shard/provenance.json` and `shard/summary.md`; and
`report/report.json`, `report/report.md` and `report/lock-candidate.json`, then an `INDEX` line; a missing file prints
`ABSENT`, one that cannot be read or printed line for line `UNREADABLE`, and one over 2,000,000 bytes `TOO-LARGE`.

**Re-aggregation** (`.github/workflows/lab-reaggregate.yml`, `lab.reaggregate`) rebuilds a finished run's report with
a later commit's `lab.aggregate` and `lab.summary`, from that run's artifacts: no unit runs again. Push
`lab/reaggregate/<name>.json`, `{"run_id": <the run's id>, "purpose": "<why>"}`, to a branch other than main (the
newest such file the push changed runs), or dispatch the workflow with its path. It downloads every `lab-` artifact of
that run (read access to the repository's actions), lays out the newest plan, the provision records and the shard
artifacts, and runs the aggregate job's own aggregate and summary commands, adding `--reaggregation`. The report and
its log blocks say `REAGGREGATION_LINE` with the run id and the commit; the aggregate checks the shards as always
(plan, preregistration, seals, unit files) and records the shards' commits and lab code hashes beside its own.

**An earlier run's artifact** (A1 of `CHOICE-A001.md`): for a plan with J2 units the plan job downloads the one
`lab-plan-` artifact the request's `exclude` names (`actions/download-artifact`, `run-id` and `name` from the plan
step's `j2_exclude_run_id` and `j2_exclude_artifact` outputs, `github-token: ${{ github.token }}`), under the
workflow's `permissions: {actions: read, contents: read}`, into `${{ runner.temp }}/lab-earlier/<run id>`: never
under `lab-plan`, so it is not uploaded with this run's plan artifact, not scanned by `lab.msha guard` and not read
by `lab.summary`. The step runs only when the run id output is not empty and continues on error, so a missing or
expired artifact reaches `lab.j2labels` as an absent directory and one fixed message. For a plan with J3 units a
second step, `earlier-2`, downloads the second excluded run's artifact the same way (`j3_exclude_run_id_2` and
`j3_exclude_artifact_2`, under the same directory by its own run id, B2 of `CHOICE-A002.md`), and
`lab.j3labels` reads both. Nothing read from the
downloaded directory is printed or written anywhere but into the exclusion set on the runner.

Artifacts are kept for the request's `retention_days`. Caches (`actions/cache`): `lab-server-<tag>-<sha16>` holds the
verified server archive and `lab-gguf-<key>-<sha16>` a verified model file; `lab-msha-<sha16>` holds MSHA's accident
file for L1 (`$RUNNER_TEMP/lab-msha`), saved by the plan job after the preregistration and restored by the run and
aggregate jobs before anything reads it (K10); a run restores only caches of its own branch or the default branch,
and an entry unused for 7 days is removed. A re-aggregation reads no L1 block (`L1_NO_REAGGREGATION`): it has no guard
over MSHA's file.
