# J001 build note: the lab's `j1` experiment

What the builder adds so the lab can run judge test J001 (`CHOICE-J001.md`). The choice file is the rule; this
note is how to run it. Nothing here changes a pinned module.

## Why a new kind

No existing kind fits.
- E1 scores open extraction, with a different task and prompt.
- E2 judges with the same task, but on a planted synthetic world, and it scores candidate rankings.
- G0 and the simulation are synthetic too.

J001 needs real public records, the verifier's judge on one record at a time, and a verdict per question. So the
smallest correct change is a new kind, `j1`, whose runner lives in `lab/` and imports the pinned modules read-only.
`lab/sim.py` and `lab/openfda.py` are lab-owned runners of the same sort.

## The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` | an `experiments.j1` block |
| Plan | `lab/plan.py` | one unit per model and part: `j1-<model>-p<k>` |
| Preregistration | `lab/prereg.py` (plan job) | labels, questions, the lexical judge's verdicts and scores, the pins |
| Unit | `lab/units.py` runs `python -m lab.j1 run` | one part's questions against the shard's model server |
| Aggregate | `lab/aggregate.py` | a `j1` block: per model, the scores and the headline of rule 7 |
| Summary | `lab/summary.py` | a J1 table in the step summaries |

**The request block** (all keys required unless a default is given):

| key | values | default |
|---|---|---|
| `models` | gguf or fake models of `models`; a hosted model is refused (`NHTSA_HOSTED`) | `models` |
| `minutes` | per part unit, 1 to the shard capacity | |
| `labels` | exactly `{"source": "nhtsa", "pack": "docs/collective/replay/vehicles/pack", "n": 40..2000, "seed": <seed>}` | |
| `parts` | 1 to 30 | |
| `seed` | the questions' seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |

Append `j1` at the end of `request.EXPERIMENTS`, so existing messages only gain `, j1`.

**J001's settings,** for the request written when the run starts (not part of the build; no file goes into
`lab/requests/` now): models a-0p5b, a-1p5b and a-4b; `job_minutes` 330; `max_parallel` 9; `j1.minutes` 150;
labels n 150, seed 1; `parts` 6; `seed` 1; `bootstrap_b` 10000; `bootstrap_seed` 1; `provider` the manifest's
server program, as `reader-002.json` has it. The planner then makes 18 units in 9 shards of 2 units each. The
builder's plan test should assert exactly that.

## `lab/j1.py` (new)

Pure parts, called in-process by the plan job, the aggregate and the tests:

- `TWINS`: the three old/new pairs of `CHOICE-J001.md` (trap 2), and `EXCLUDED = ("unknown_or_other",)`.
- `build_questions(pack, labels_bytes, *, seed, parts) -> (bytes, record)`. One canonical line per question, in
  record order, positive before negative: `{"question_id", "record_ref", "part", "kind", "entity_type",
  "entity_id", "predicate", "filed"}`. The positive and negative are drawn by
  `random.Random(f"j1:{seed}:{record_ref}:positive")` and `...:negative`, choosing from sorted lists (rule 2).
  Record `i` of `n` goes to part `i * parts // n + 1`. The record holds the count, the size of each part and the
  sha256.
- `window_record(label_record) -> WindowRecord`. Codes `[]` (refuse a label record that has any), structured from
  its `entities`, the narrative, the language. `judge_payload` never sends persons or the reporter.
- `payload(pack, question, label_record)`: `verify.judge_payload(pack, {"params": {...}}, window_record(...))`.
- `verdict(window, reply)`: `verify.decide([w], [(w, reply)], 0).verdict`, and `decide([w], [], 1).verdict` when
  there is no reply.
- `lexical_verdicts(pack, labels, questions)`: `verify.lexical_judge(pack, Canonicaliser(pack))` on each payload.
- `score(questions, verdicts, *, bootstrap_b, bootstrap_seed, records=None) -> dict`:
  - sensitivity, specificity, balanced accuracy, accuracy and the unknown share;
  - each as `{"value", "ci_low", "ci_high"}` from `stats.cluster_bootstrap_mean`, one cluster per record (its two
    0/1 values), seed `f"j1:{bootstrap_seed}"`;
  - the answer-pair counts per kind, the failures by kind, the records scored and the records left out.

  `records` limits the score to a set of records, so the lexical judge can be scored on a model's records.
- `headline(model_ba, lexical_ba) -> "better" | "worse" | "not_told_apart"`, by rule 7.
- `paired(model, lexical, ...)`: `stats.paired_bootstrap` over per-record balanced accuracy.
- `routing_doc(models, keys, base_url)`: the one builder of J1 routing, used by the preregistration (at
  `lab.prereg.PLACEHOLDER_BASE_URL`) and by each unit. Endpoints are keyed by model key at boundary `site:lab`,
  with `deadline_s` 600 and `max_retries` 1. The route is `judge_record` to that key with no escalation, so
  `Runtime.run` takes the verifier's route as `SiteVerifier` does.
- `CODE_FILES` and `code_hash()`: `edge/verify.py`, `edge/extract.py`, `edge/records.py`, `packs/canonical.py`,
  `packs/loader.py`, `inference/*.py`, `schemacheck.py`, `jsonio.py`, `stats.py`, `lab/j1.py`, `lab/goldlabels.py`.
  Build it the way `e1_extract.e1_code_hash` does.

**The CLI,** `python -m lab.j1 run --prereg F --labels F --questions F --routing F --endpoint KEY --part K
--run-id ID --runs-dir D --budget-seconds N`:

- **Checks, each exit 2 before any call:**
  - the prereg's kind;
  - the sha256 of the labels and of the questions;
  - the pack's vocabulary and config hashes, and `code_hash()`;
  - the sha256 of the judge's instructions and schema;
  - the endpoint's pins (provider, boundary, model, response format, transport schema, the thinking controls);
  - the boundary `site:lab`, and data label `public` (`e1_extract.check_data_label`);
  - the part, within the prereg's parts.
- **Judging:** the part's questions in order. Each is `runtime.run(judge_task(), payload, compiled judge_schema(),
  ref=f"j1-{part}-{i:04d}")`. The runtime is built like E1's: boundary `site:lab`, data label `public`, ledger
  `ledger.jsonl` in the run directory.
  - An `InferenceError` whose kind is in `runtime.VALIDATION_KINDS` is a model failure: verdict `unknown`.
  - Any other `InferenceError` is a transport failure: not scored.
  - An `InferenceBoundaryError` is exit 2.
  - Stop at `--budget-seconds`, or after `extract.BREAKER_AFTER` consecutive failures that `extract.server_down`
    calls the server's, with `complete: false` and a `stopped` reason.
- **`verdicts.jsonl`,** one line per question, flushed as it goes: `{"question_id", "record_ref", "kind",
  "predicate", "mentions_entity", "describes_predicate", "verdict", "error_kind", "scored"}`. No narrative.
- **`run.json`** (`kind: lab_j1_run`):
  - the run id, endpoint, part and parts, and the questions planned and done;
  - `complete`, `stopped` and `measurement` (`experiments.common.measurement_flag`, false when a fake answered);
  - the models served, the pins and the prereg's sha256;
  - the sha256 of `verdicts.jsonl` and `ledger.jsonl`;
  - failures by kind, and the latency median and 95th percentile;
  - the start and finish times.
- **Exit codes:** 0 complete; 1 stopped; 2 usage or pins; 130 interrupted.

## Files to change

- **`lab/request.py`:** `_j1` and its keys; `EXPERIMENTS`; the module docstring. Refuse a hosted model with
  `NHTSA_HOSTED`, and any labels but the vehicle pack's `nhtsa` labels.
- **`lab/plan.py`:** the `j1` branch of `build_units`. Its params are `pack`, `labels`, `endpoint`, `part`,
  `parts`, `seed`, `bootstrap_b` and `bootstrap_seed`; its seeds are `[seed]`. Serving class `quality` (the
  default). Update the docstring.
- **`lab/prereg.py`:** a `_j1` step when the plan has j1 units. It writes `prereg/j1/`:
  - `labels.jsonl` and `labels.json` (`build_labels`, as E1 does);
  - `questions.jsonl` and `questions.json`;
  - `lexical.jsonl` (the lexical judge's answers and verdict per question) and `lexical.json` (its `score`);
  - `routing.json` (the pins);
  - `prereg.json` (`kind: lab_j1_prereg`): the hashes above, the judge task name, the sha256 of its instructions
    and schema, `max_tokens`, the code hash and files, the endpoints' pins, the boundary, the data label, the seeds,
    `parts`, `TWINS`, `EXCLUDED` and the bootstrap settings.

  The manifest gains a `j1` key, and stdout says `j1 yes|no`. Give `preregister` a keyword-only `nhtsa_fetch`
  that only tests pass, so no test downloads the complaint file.
- **`lab/units.py`:**
  - `ADAPTERS["j1"]`: module `lab.j1`, routed task `judge_record`, boundary `site:lab`, deadline 600, result
    `run.json`, collect `run.json`, `verdicts.jsonl` and `ledger.jsonl`, ledger `ledger.jsonl`, subcommand `run`;
  - `PREREG_EXPERIMENTS` and `required_tasks` (`[JUDGE_TASK]`);
  - the `j1` branches of `build_argv` and `write_routing` (through `j1.routing_doc`);
  - the budget for j1 in `run_unit`, as the sim's: the unit's seconds less a margin;
  - `harness_status`: exit 0 with `complete: true` is `ok`; exit 1 with `complete: false` is `failed`, with a new
    note;
  - `harness_measurement` and `harness_verdict`;
  - `public_text`, true for j1, so the notes say public narratives;
  - the fake server's `with_pack` list.
- **`lab/warmup.py`:** j1 units warm `judge_record` for their pack. `pack_tasks` needs `params["seed"]`.
- **`lab/aggregate.py`:** a `j1` block, or null without j1 units. It needs the verified preregistration. Per model:
  - the parts planned and ok, and `complete` (every part `ok`, and its files hash as recorded);
  - the display class (`model` only when every part is);
  - the scores pooled over its parts (`j1.score`), and the lexical judge's scores on the same records;
  - the paired difference, the left-out records and the withheld reason;
  - the `headline`, shown only for a complete model of display class `model`, else null with the reason.

  Copy the lexical judge's scores on all records from the preregistration. Latency rows come from the ledgers as
  for every unit.
- **`lab/summary.py`:** the plan summary shows the preregistered labels, questions and lexical scores. The run
  summary shows one row per model: parts finished, balanced accuracy with its interval, the lexical judge's
  balanced accuracy on the same records, the headline, sensitivity, specificity and the unknown share.
- **`lab/notes.py`:** the fixed sentences (no ASCII digits: `test_lab_guards` checks).
- **`lab/__init__.py`:** the module list in the docstring.
- **Docs:** `docs/lab/REFERENCE.md` (the block, the unit's files, `run.json`, the report's `j1` block, the new
  note; `test_lab_docs` requires every key); `docs/lab/README.md` (a short paragraph); `docs/lab/INTEGRATION.md`
  (the pinned functions `lab/j1.py` uses read-only: `judge_task`, `judge_schema`, `judge_payload`,
  `lexical_judge`, `decide`, `WindowRecord`, `Runtime.run`, `BREAKER_AFTER`, `server_down`).

## Files not to touch

- The pinned modules (`tests/mycelic/test_collective_x3.py`): `mycelic/collective/*.py` at the top level,
  `detect/`, `edge/`, `packs/*.py`, `evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/` and
  `connectors/openfda.py`.
- `docs/collective/replay/vehicles/pack/`.
- `lab/requests/`.
- The workflow: it runs `lab.prereg`, the shards and the aggregate the same way for every kind.

## Tests (`tests/lab/test_lab_j1.py`, new)

Build archives as `test_nhtsa_labels_hide_the_codes_and_score_them` does. Judge through the repo's fake model
server (`mycelic/collective/inference/fakeserver.py`) with the lab's `Responder`, as the E1 and E2 tests do.

1. **Questions.** The same bytes twice. Per record, one positive among the filed predicates, and one negative
   outside the filed ones, `unknown_or_other` and the filed ones' twins. Dropping a record leaves the other
   records' questions unchanged. 150 records in 6 parts give 25 each.
2. **Payload.** It equals `judge_payload` with codes `[]` and the structured vehicle, and holds no person or
   reporter.
3. **Lexical judge.** Its verdicts equal `decide` over `lexical_judge` per question. It never says `unknown` when
   the vehicle resolves.
4. **The real path with a fake server.** `lab.j1 run` through the fake server and the `Responder` gives every
   question the lexical judge's verdict, and `measurement` is false. This is the end-to-end check that the runner
   uses the shipped task, schema, payload and rule.
5. **Failures.** A schema-invalid reply after the repair is `unknown` and counted. A transport failure leaves its
   record out. More than 1% of records left out withholds the headline.
6. **Stops.** At the budget: `complete: false`, exit 1. After two consecutive server-down failures: the rest are
   not sent.
7. **Pins.** A changed labels file, questions file, pack, code hash or endpoint pin exits 2 before any call, with
   no ledger row.
8. **Scoring.** Balanced accuracy equals accuracy. The intervals equal `stats.cluster_bootstrap_mean` on the same
   clusters. `headline` gives better, worse and not told apart at the boundary values. The lexical judge is
   scored on the model's records.
9. **Aggregate.** A model with a part missing gets no headline and a partial reading. A model with every part ok
   gets one.
10. **Request and plan.** Every block error. J001's settings give 18 units and 9 shards. A hosted model is refused,
    and so are labels other than `nhtsa`.

**Existing tests to update:** the experiments message in `test_lab_request.py` and `test_lab_sim.py`; the
serving-class list in `test_lab_experiments.py`; `PREREG_EXPERIMENTS` in `tests/lab/helpers.py`; the reference
keys in `test_lab_docs.py`.

**Before merging:** run `python -m pytest tests/lab -q` and the pins and guards,
`python -m pytest tests/mycelic/test_collective_x3.py tests/mycelic/test_collective_guards.py -q`. The E1
preregistration refuses a dirty tree, so commit before the lab suite.

## As built

The build follows the note above. These details were left open, and the code fixes them. None changes a setting of
`CHOICE-J001.md`.

- **Question ids** are `<record_ref>:positive` and `<record_ref>:negative`.
- **Participation.** A lab unit with a model is `invalid` when too few of its calls got a valid reply
  (`MIN_MODEL_OK_SHARE` in `lab/units.py`). For `j1`, a reply still invalid after its repair counts as answered,
  because rule 6 scores it `unknown` and counts it. A transport failure does not count as answered.
- **Budget.** A J1 unit's `--budget-seconds` is its timeout less `SIM_BUDGET_MARGIN_S` (`lab/units.py`), as for
  the simulation.
- **Headline.** It needs every part `ok`, the model's display class `model`, and `measurement: true` in every
  part's `run.json`. A part a fake server answered never gives a headline.
- **Records not judged.** A record whose questions have no line yet (a part that stopped) is counted as not judged.
  It is neither scored nor left out.
- **Routing.** The preregistration's `routing.json` pins every model's endpoint and routes nothing. Each unit's
  routing holds its own model's endpoint and routes `judge_record` to it.
- **Labels sha.** The summaries show the labels sha256 at `SHA_SHOWN` characters (`lab/summary.py`), the same length
  as R001's `bc6d092d8bca` in `CHOICE-R001.md`, so the two can be compared by eye. The full sha256 is in
  `prereg/j1/labels.json` and in the report.
- **No run here.** The tests run the whole path with the repo's fake model server. No model has judged a narrative.
