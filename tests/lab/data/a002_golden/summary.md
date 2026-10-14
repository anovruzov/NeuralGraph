## Lab plan

- Request: `lab/templates/accuracy-002.json`, name `accuracy-002`, sha `77173e537c9f`
- Commit: `<commit>`
- Provider: `llama-server`
- Job minutes: 330; Shards at once: 5; Days kept: 30

Purpose: `Accuracy test A002 (docs/collective/A002/CHOICE-A002.md, amended before any build), cycle two toward the target: a-4b judges a fresh NHTSA draw (J001's and A001's records excluded, codes hidden) under the negation instruction and its evidence and rationale variants, read by the shipped rule, the grounding check, the unclear-to-no mapping and a lexical composite with its ablations; primary pair a-4b, evidence arm. Public data; the first run is the result. Copy to lab/requests/accuracy-002.json.`

### Models

| model | kind | alias | repository | file | revision | pin |
| --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `gguf` | `lab-a-4b` | `<repository of a-4b>` | `<file of a-4b>` | `main` | `bc640142c66e1fdd12af0bd68f40445458f3869b` `7485fe6f11af` |

### Shards

| shard | model | kind | units | planned minutes | job timeout minutes |
| --- | --- | --- | --- | --- | --- |
| `s001-a-4b` | `a-4b` | `gguf` | `j3-a-4b-p1` | 300 | 325 |
| `s002-a-4b` | `a-4b` | `gguf` | `j3-a-4b-p2` | 300 | 325 |
| `s003-a-4b` | `a-4b` | `gguf` | `j3-a-4b-p3` | 300 | 325 |
| `s004-a-4b` | `a-4b` | `gguf` | `j3-a-4b-p4` | 300 | 325 |
| `s005-a-4b` | `a-4b` | `gguf` | `j3-a-4b-p5` | 300 | 325 |

### Units

| unit | experiment | model | minutes | seed | shard |
| --- | --- | --- | --- | --- | --- |
| `j3-a-4b-p1` | `j3` | `a-4b` | 300 | 3 | `s001-a-4b` |
| `j3-a-4b-p2` | `j3` | `a-4b` | 300 | 3 | `s002-a-4b` |
| `j3-a-4b-p3` | `j3` | `a-4b` | 300 | 3 | `s003-a-4b` |
| `j3-a-4b-p4` | `j3` | `a-4b` | 300 | 3 | `s004-a-4b` |
| `j3-a-4b-p5` | `j3` | `a-4b` | 300 | 3 | `s005-a-4b` |

### Files to provision once per run

| entry | cache key |
| --- | --- |
| `server` | `lab-server-b11476-2cda5ff9363967f1` |
| `gguf-a-4b` | `lab-gguf-a-4b-7485fe6f11af2943` |

### Preregistration, fixed before any model runs

- `J3` labels: source `nhtsa`, records 150, sha `f95b4ed9db9c`; questions 300, parts 5, sha `f5583449deab`; prereg sha `<prereg>`
- eligible records 540; excluded records 300; eligible after exclusion 240; overlap with the excluded set 0
- sources: `J1` `verified` (150 refs); `R1` `verified` (150 refs); `A1` `verified` (150 refs); `R2` `verified` (150 refs); `K` `verified` (300 refs)
- cycles established by: `j001` `artifact` (150 refs); `a001` `artifact` (150 refs)
- consistency counts: `j1_r1` 0; `a1_r2` 0; `outside_chain` 0; `chain_union` 0
- excluded refs file sha `c1e1a63cee69`; chain file lines 450; complaint file sha `<archive>`

Plumbing: test artifacts stood in for both excluded runs (lab.dryrun with test data), so the exclusion checks here test the lab, not the excluded runs.
- `J3` primary pair: `a-4b` `evidence`; headline secondary `evidence_grounded`; target balanced accuracy 0.860
- `J3` lexical judge, every record: balanced accuracy 0.700 (interval low 0.660, interval high 0.740)
- `J3` record-blind control, every record: balanced accuracy 0.503 (interval low 0.447, interval high 0.560)
- `J3` ablation A, every record: balanced accuracy 0.700 (interval low 0.660, interval high 0.740)
- `J3` ablation B, every record: balanced accuracy 0.610 (interval low 0.553, interval high 0.667)
- `J3` predicate-only bound, every record: 0.577; lexical judge unknown verdicts 0

The draw excludes two earlier cycles' records, each by its plan artifact and by a rebuild from the complaint file as published on the day: the first judge test's artifact and its seed-one rebuild, the first accuracy test's artifact (its labels file read by a check generalised to its layout) and its rebuild over the eligible list less the first judge test's set, and the first accuracy test's chain file. A cycle's set is established by its own artifact or rebuild, or else from the chain less the other cycle's set; the chain alone establishes neither; every consistency count must be zero; the excluded set is the union of every verified source's refs. Counts and hashes only: no record ref is written anywhere but the plan artifact.
