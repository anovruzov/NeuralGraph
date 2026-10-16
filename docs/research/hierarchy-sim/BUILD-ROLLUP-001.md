# Roll-up build recovery, 2026-10-16

Recovered from deployment head `34504be`, whose parent is the separate plan amendment `ff4dd84`.
The old container's build branches are not present in this checkout or in the remote branch inventory.
The last ten Actions runs are complete; none is a roll-up build. The three old background tasks cannot
be resumed or described as running here. Only the roll-up arm is reconstructed in this change.
The accuracy and slowdown work has not been reconstructed or modified.

## Implemented

- `rollup/edge.py`: seeded keep, copy and negation marking at the amendment's rates; chronological site-local
  memory formation; company-keyed opaque lineage handles. Perfect marking is a fixture-only ablation.
- `rollup/protocol.py`: closed summary schema, entity whitelist from the unchanged pack, identifier and
  narrative-marker scans, twelve-week evidence window, cap eight, frozen ranking and re-debate behavior.
  Prompts carry counts and at most eight handles per site/kind/week entry. Oversize batches split per story.
- `rollup/gossip.py`: per-origin authenticated hash chains; buffered out-of-order messages, idempotent replay,
  fork refusal, peer push/pull with the frozen seeded fault schedule. Recovery pulls the lost origin's head
  from peers before emitting the site's backlog. The edge retains source summaries to rebuild a lost tail.
- `rollup/backends.py`: deterministic fixture, explicit local server adapter, and paused session exchange.
  No paid API call, implicit hosted fallback, real-record model call or production integration exists.
- `rollup/run.py`: immutable output directories, weekly checkpoints, token-character ledgers, abstract-summary
  snapshots, unchanged world generator and scorer imports, both windowed decoy readings and an unwindowed
  reading, rare and contradicted splits, paired baseline set comparison, attack tables, miss traces and gate.
- `rollup/exchange.py`: audited role replies, strict schemas, one retry then an empty argument or no ruling,
  and a stop after two weeks unless remaining weekly capacity exceeds the projected remaining run.
  The caller must supply actual transcript evidence. Accepting a JSON audit is not a filesystem sandbox;
  the transcript source must be independently checked before a pilot is considered valid.

The baseline found set is available in the committed root-cause report's per-pattern table, real-run column.
It is used only by the scorer, never by candidate ranking, the fixture or the role prompts.

## Checks before any development-world run

`python -m unittest discover -s tests/rollup -q`: 26 tests pass on hand-built data.
`python -m unittest tests.mycelic.test_collective_guards.NoModelNamesTests -q`: four pass.
Compile checks pass. No frozen application, generator, scorer, lab, request or settings file is changed.
The new workflow runs these focused checks. Existing full suites have not been re-run locally.
No independent build review has been completed in this recovered session.

## Execution and limits

From the repository root:

```
python -m rollup.run gossip --users 10000 --out docs/research/hierarchy-sim/runs/recovery-gossip-001
python -m rollup.run fixture --users 10000 --out docs/research/hierarchy-sim/runs/recovery-fixture-10000
python -m rollup.run fixture --users 200 --out docs/research/hierarchy-sim/runs/recovery-fixture-200
python -m rollup.run fixture --users 200 --perfect --out docs/research/hierarchy-sim/runs/recovery-perfect-200
```

These are synthetic code diagnostics, `measurement: false`. They do not satisfy the model-pilot gate or
replace the required independent build review. The first output is retained even if a fault scenario is not
realized; no seed, cap or setting is adjusted in response. Specifically, report whether the crashing node's
W04 summary had already fully replicated at the W05 crash. If it had, the amendment's pre-full-spread crash
case remains untested. The final recovery rows must also be read; a lack of forks alone is not convergence.

The fixture's debated-in-window number is conditional on fixture rulings and their re-debate history. It is
not a proven maximum over possible model rulings. Its prompt counts are exact for fixture-generated arguments;
actual arguments change later prompt lengths. No claim about model recall follows from that diagnostic.

Session-pilot preparation (`python -m rollup.run prepare --users 200 --out <new-directory>`) writes only
abstract summaries, control state and the first prompt. Each role can use the exchange's show/reply operations.
The harness retains scoring separately. A role with other tool use is void. The current session does not expose
independently verifiable child transcripts or the remaining account token allowance; no compliant model pilot
is claimed until those audit and capacity inputs exist. No stand-in model has run during this reconstruction.

All benchmark settings remain those of `PLAN-ROLLUP-001.md` with amendment `ff4dd84`; this note changes none.
The frozen accuracy loop remains separate. No code from `rollup/` is copied into a deployment image.
