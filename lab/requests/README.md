# Lab requests

Each JSON file here asks for one run of the Mycelic cloud lab: one request file is one run.

- Name the file `lab/requests/<name>.json`, where `<name>` matches `[a-z0-9][a-z0-9-]{0,39}`: lower-case letters,
  digits and hyphens, starting with a letter or a digit. Any other name is refused before anything runs.
- Start from a template: copy a file of `lab/templates/` here under a new name and edit it. The templates say in
  their `purpose` which name to copy them to.
- Push one request per push, to the branch `lab`. Adding or editing a request starts a run; an identical re-push
  does not. Pushes to `main` run nothing: there a request runs only by dispatch.
- This README triggers nothing: the workflow starts only for `lab/requests/*.json`.

Check a request locally before pushing it:

```
python -m lab.plan --request lab/requests/<name>.json --manifest lab/models.json --out <empty dir>
```

Everything a run writes is public. How to request a run, read its results and keep them is in
[the lab guide](../../docs/lab/README.md).
