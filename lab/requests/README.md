# Lab requests

Each JSON file here asks for one run of the Mycelic cloud lab: one request file is one run.

- Name the file `lab/requests/<name>.json`, where `<name>` matches `[a-z0-9][a-z0-9-]{0,39}`: lower-case letters,
  digits and hyphens, starting with a letter or a digit. Any other name is refused before anything runs.
- Start from a template: copy a file of `lab/templates/` here under a new name and edit it. The templates say in
  their `purpose` which name to copy them to.
- Push one request per push, to the branch `lab`. Adding or editing a request starts a run; an identical re-push
  does not. Pushes to `main` run nothing: there a request runs only by dispatch.
- This README triggers nothing: the workflow starts only for `lab/requests/*.json`.
- A `j2` request (accuracy test A001, template `lab/templates/accuracy.json`) names in its `exclude` entry the earlier
  run whose records the fresh draw leaves out: the run id, its plan artifact (`lab-plan-<run id>-<attempt>`), the file
  in it that lists the records and the sha256 prefix of that file's bytes. The plan job downloads that one artifact
  (read-only `actions: read`) and checks it before anything runs; an expired artifact stops the plan job unless the
  rebuild of the first judge test's draw still verifies (REFERENCE.md, Request format). Each cycle's own artifact
  holds `prereg/j2/excluded.jsonl` for the next cycle to name.
- A `j3` request (accuracy test A002, template `lab/templates/accuracy-002.json`) names in its `exclude` list the
  two earlier cycles in seed order: each with the run id, its plan artifact, the file in it that lists the records,
  that file's sha256 prefix, the rebuild seed and the prefix the rebuilt labels must hash to; the second entry
  also names that cycle's chain file and its prefix. The plan job downloads both artifacts (read-only
  `actions: read`) and establishes each cycle's records from its artifact, its rebuild or the chain before anything
  runs (REFERENCE.md, Request format). Each cycle's own artifact holds `prereg/j3/excluded.jsonl` for the next
  cycle to name.
- A `jt` request (accuracy test A003's term run, template `lab/templates/terms-003.json`) has one block, and no
  other block beside it, whose four keys are fixed by the rule: `minutes`, `pack` (the vehicle pack), `cutoff`
  (`2023-01-01`) and `from` (`2020-01-01`); any other value is refused. It runs no model and draws nothing: the plan
  job drafts the term set from the public complaints received before the cutoff and keeps it in its plan artifact for
  90 days (`retention_days`, which must be 90), and a dated amendment of the rule commits its sha256 (REFERENCE.md,
  Request format). Push it only when no other lab run is in flight.
- A `j4` request (accuracy test A003's judging run, template `lab/templates/accuracy-003.json`) names its
  `base_task` (the branch the rule's adaptation step gives), the three earlier cycles in seed order in its `exclude`
  list (each entry as a `j3` entry, its file and rebuild seed fixed per entry, the chain keys on the second and third)
  and the term run in its `terms` entry: the run id, its plan artifact, the file `prereg/jt/terms.json`, the term
  set's full sha256 and P_D's vocabulary hash as the amendment after the term run committed them, and the cutoff
  `2023-01-01`. The plan job downloads the three earlier artifacts and the term artifact (read-only `actions: read`)
  and refuses to run unless the term set hashes to the committed sha256 (REFERENCE.md, Request format); no summary,
  log line or report prints a term. Each cycle's own artifact holds `prereg/j4/excluded.jsonl` for the next cycle to
  name.

Check a request locally before pushing it:

```
python -m lab.plan --request lab/requests/<name>.json --manifest lab/models.json --out <empty dir>
```

Everything a run writes is public. How to request a run, read its results and keep them is in
[the lab guide](../../docs/lab/README.md).
