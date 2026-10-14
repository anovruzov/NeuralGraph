NO MEASUREMENT: no unit here passed every model check, so nothing below measures a model

## Lab report

- Request: `lab/templates/accuracy-002.json`, name `accuracy-002`, sha `77173e537c9f`
- Plan: `<plan>`; Commit: `<commit>`; Provider: `llama-server`
- Units: 5; Shards: 5

Purpose: `Accuracy test A002 (docs/collective/A002/CHOICE-A002.md, amended before any build), cycle two toward the target: a-4b judges a fresh NHTSA draw (J001's and A001's records excluded, codes hidden) under the negation instruction and its evidence and rationale variants, read by the shipped rule, the grounding check, the unclear-to-no mapping and a lexical composite with its ablations; primary pair a-4b, evidence arm. Public data; the first run is the result. Copy to lab/requests/accuracy-002.json.`

### Shards

| shard | model | state | attempt | failed step | exit code | wall seconds | CPU | reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `s001-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | <time> | <host> | n/a |
| `s002-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | <time> | <host> | n/a |
| `s003-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | <time> | <host> | n/a |
| `s004-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | <time> | <host> | n/a |
| `s005-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | <time> | <host> | n/a |

### Plumbing checks (fake provider): not model measurements

| unit | experiment | model | shard | status | wall seconds |
| --- | --- | --- | --- | --- | --- |
| `j3-a-4b-p1` | `j3` | `a-4b` | `s001-a-4b` | `ok` | <time> |
| `j3-a-4b-p2` | `j3` | `a-4b` | `s002-a-4b` | `ok` | <time> |
| `j3-a-4b-p3` | `j3` | `a-4b` | `s003-a-4b` | `ok` | <time> |
| `j3-a-4b-p4` | `j3` | `a-4b` | `s004-a-4b` | `ok` | <time> |
| `j3-a-4b-p5` | `j3` | `a-4b` | `s005-a-4b` | `ok` | <time> |

#### Accuracy test, second cycle: the verifier's narrow question on a fresh draw, nine declared arms on one model, one primary pair

`J3` here asks one model the site verifier's narrow question about a fresh draw of real public NHTSA complaint narratives with two earlier cycles' records excluded, one record at a time with its codes hidden, three times: under the lab-side negation instruction, under that instruction with an evidence field that asks for a verbatim passage of the record, and under it with an analysis field written before the answers; each verdict is scored against the components the complaint was filed under (filed codes, not human-checked labels), by the product's own verdict rule on the answers as answered and, as declared secondaries, after the grounding check (a yes whose passage is not in the record read as a no), after the unclear-to-no mapping, and in a composite that confirms wherever the lexical judge does.

- labels: source `nhtsa`, records 150, sha `f95b4ed9db9c`; questions 300, parts 5
- eligible records 540; excluded records 300; eligible after exclusion 240; overlap with the excluded set 0
- sources: `J1` `verified` (150 refs); `R1` `verified` (150 refs); `A1` `verified` (150 refs); `R2` `verified` (150 refs); `K` `verified` (300 refs)
- cycles established by: `j001` `artifact` (150 refs); `a001` `artifact` (150 refs)
- consistency counts: `j1_r1` 0; `a1_r2` 0; `outside_chain` 0; `chain_union` 0
- excluded refs file sha `c1e1a63cee69`; chain file lines 450; complaint file sha `<archive>`

Plumbing: test artifacts stood in for both excluded runs (lab.dryrun with test data), so the exclusion checks here test the lab, not the excluded runs.
- lexical judge, every record: balanced accuracy 0.700 (interval low 0.660, interval high 0.740)
- record-blind control, every record: balanced accuracy 0.503 (interval low 0.447, interval high 0.560)
- ablation A, every record: balanced accuracy 0.700 (interval low 0.660, interval high 0.740)
- ablation B, every record: balanced accuracy 0.610 (interval low 0.553, interval high 0.667)
- predicate-only bound, every record: 0.577; lexical judge unknown verdicts 0
- comparisons since the loop began: 21 (9 this cycle, 12 before)

The draw excludes two earlier cycles' records, each by its plan artifact and by a rebuild from the complaint file as published on the day: the first judge test's artifact and its seed-one rebuild, the first accuracy test's artifact (its labels file read by a check generalised to its layout) and its rebuild over the eligible list less the first judge test's set, and the first accuracy test's chain file. A cycle's set is established by its own artifact or rebuild, or else from the chain less the other cycle's set; the chain alone establishes neither; every consistency count must be zero; the excluded set is the union of every verified source's refs. Counts and hashes only: no record ref is written anywhere but the plan artifact.

The lexical judge is the verifier's judge when no model runs: it confirms a question only when one of the pack's phrases for the component is in the narrative and not negated. It judged every question in the plan job, before any model ran.

The record-blind control never reads a record: it confirms a question only when its component is one of the four filed most often in NHTSA's whole complaint file, and refutes it otherwise. It shows what knowing which components are common gets a judge without reading. The predicate-only bound is the most any judge that sees only the component could score on the same records; it is fitted to their answers, so it is optimistic. Neither decides anything.

Nine arms on one model, declared before the run: negation and negation_mapped are the first accuracy test's control arms replicated; evidence reads the evidence task's answers as answered (the primary arm); evidence_grounded reads a yes as a no when the cited passage is not a verbatim substring of the record's text (the headline secondary); evidence_grounded_mapped adds the unclear-to-no mapping; rationale and rationale_mapped read the rationale task's answers as answered and under the mapping; composite and composite_mapped confirm wherever the plan job's lexical judge confirms and otherwise take the grounded arm's verdict. Model calls are made for the three tasks only: every other arm is the same replies read again. The lifts are paired differences between arms, record by record; a lift helps when its interval's low end is above zero, hurts when its high end is below zero, and is otherwise not told apart. They decide nothing toward the target.

The verdict word is read on the primary pair alone, from the figures, in this order: incomplete (a part did not finish, the set is not a model measurement, transport failures left out more than one record in a hundred, or, for a composite arm, an ablation difference is not a number), pass_provisional (balanced accuracy at or above the target, the paired difference to the lexical judge with its low end above zero, the balanced accuracy's low end above the predicate-only bound on the same records, the unknown share reported and, for a composite arm, the paired differences to both ablations with their low ends above zero), partial (the target reached but an interval clause failed, the lexical judge beaten below the target, or a composite that meets the four clauses but not an ablation clause), else failure. Every other arm is a secondary with the same word, which is a hypothesis for the next cycle, never a result; the headline secondary's word is printed beside the primary's as the primary under the other reading of the standing default; a first pass is provisional until the next cycle's fresh draw confirms it.

The grounding check says that a cited passage came from the record, not that it reports the problem: a yes answer whose citation is not a verbatim substring of the record's text (case and runs of whitespace aside, at least four characters) is read as a no. The grounded share of yes answers is shown for positives and for negatives apart: a check that removes yes answers on negatives and keeps them on positives is the lever working; one that removes them alike is a cost with no reading. The composite arms inherit the lexical judge, which is why their pass test also needs the paired differences to both ablations above zero; on a draw where the lexical judge never answers unknown, ablation A is the lexical judge line for line. The first-key share says whether the model wrote the new field before its answers; the token figures and the replies cut at the cap say how much it wrote. None of these decides anything.

Primary pair `a-4b` `evidence`: `incomplete`. It scored balanced accuracy 0.700 [0.660, 0.740]; gap to the target 0.860 is 0.160; lexical judge 0.700 on the same records, paired difference 0.000 [0.000, 0.000]; predicate-only bound 0.577; unknown share 0.000. Under the other reading of the standing default the primary would be `evidence_grounded`: `incomplete`.

#### Accuracy test, second cycle: every arm beside the comparators and the ablations on the same records

| model | arm | arm letter | task | reading | class | parts finished | balanced accuracy | interval low | interval high | lexical balanced accuracy | minus the lexical judge, paired by record | minus ablation A, paired by record | minus ablation B, paired by record | control balanced accuracy | predicate-only bound | sensitivity | specificity | unknown share | records left out | verdict word | pass test | primary pair | headline secondary | headline | why no headline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `negation` | `N` | `judge_record_negation` | `shipped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | n/a | n/a | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `negation_mapped` | `D` | `judge_record_negation` | `mapped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | n/a | n/a | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `evidence` | `E` | `judge_record_evidence` | `shipped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | n/a | n/a | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | yes | no | n/a | not every part of this model is a model measurement, so it gets no headline |
| `a-4b` | `evidence_grounded` | `Eg` | `judge_record_evidence` | `grounded` | `plumbing` | 5 of 5 | 0.500 | 0.500 | 0.500 | 0.700 | -0.200 (-0.240, -0.160) | n/a | n/a | 0.503 | 0.577 | 0.000 | 1.000 | 0.000 | 0 | `incomplete` | no | no | yes | n/a | not every part of this model is a model measurement, so it gets no headline |
| `a-4b` | `evidence_grounded_mapped` | `Egm` | `judge_record_evidence` | `grounded_mapped` | `plumbing` | 5 of 5 | 0.500 | 0.500 | 0.500 | 0.700 | -0.200 (-0.240, -0.160) | n/a | n/a | 0.503 | 0.577 | 0.000 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `rationale` | `R` | `judge_record_rationale` | `shipped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | n/a | n/a | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `rationale_mapped` | `Rm` | `judge_record_rationale` | `mapped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | n/a | n/a | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `composite` | `C` | `judge_record_evidence` | `grounded` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | 0.000 (0.000, 0.000) | 0.090 (0.043, 0.137) | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |
| `a-4b` | `composite_mapped` | `Cm` | `judge_record_evidence` | `grounded_mapped` | `plumbing` | 5 of 5 | 0.700 | 0.660 | 0.740 | 0.700 | 0.000 (0.000, 0.000) | 0.000 (0.000, 0.000) | 0.090 (0.043, 0.137) | 0.503 | 0.577 | 0.400 | 1.000 | 0.000 | 0 | `incomplete` | no | no | no | n/a | n/a |

#### Accuracy test, second cycle: the lifts between arms and the ablation differences, paired by record

| model | lift | lever | paired records | difference | interval low | interval high | reading |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `E_minus_N` | `evidence_field` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `Eg_minus_E` | `grounding_check` | 150 | -0.200 | -0.240 | -0.160 | `hurts` |
| `a-4b` | `Eg_minus_N` | `evidence_field_and_check` | 150 | -0.200 | -0.240 | -0.160 | `hurts` |
| `a-4b` | `Egm_minus_Eg` | `unclear_mapping` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `Egm_minus_D` | `evidence_field_and_check` | 150 | -0.200 | -0.240 | -0.160 | `hurts` |
| `a-4b` | `R_minus_N` | `rationale_field` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `Rm_minus_D` | `rationale_field` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `D_minus_N` | `unclear_mapping` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `C_minus_Eg` | `lexical_disjunction` | 150 | 0.200 | 0.160 | 0.240 | `helps` |
| `a-4b` | `Cm_minus_Egm` | `lexical_disjunction` | 150 | 0.200 | 0.160 | 0.240 | `helps` |
| `a-4b` | `C_minus_ablation_A` | `lexical_or_no` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `C_minus_ablation_B` | `lexical_or_record_blind` | 150 | 0.090 | 0.043 | 0.137 | `helps` |
| `a-4b` | `Cm_minus_ablation_A` | `lexical_or_no` | 150 | 0.000 | 0.000 | 0.000 | `not_told_apart` |
| `a-4b` | `Cm_minus_ablation_B` | `lexical_or_record_blind` | 150 | 0.090 | 0.043 | 0.137 | `helps` |

#### Accuracy test, second cycle: the grounding check's readings on the evidence task

| model | replies | grounded share of yes answers on positives | grounded share of yes answers on negatives | empty citation share | citation length median | citation length ninety-fifth percentile |
| --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | 300 | 0.000 (0 of 60) | n/a (0 of 0) | 1.000 | 0.0 | 0.0 |

#### Accuracy test, second cycle: per task, the first key, the output tokens and the replies cut at the cap

| model | task | first key is the new field | attempts | calls | median ms | ninety-fifth percentile ms | tokens out median | tokens out ninety-fifth percentile | replies cut at the cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `judge_record_evidence` | 1.000 | 300 | 300 | <time> | <time> | 45.0 | 45.0 | 0 |
| `a-4b` | `judge_record_rationale` | 1.000 | 300 | 300 | <time> | <time> | 45.0 | 45.0 | 0 |
| `a-4b` | `judge_record_negation` | n/a | 300 | 300 | <time> | <time> | 45.0 | 45.0 | 0 |

#### Model call latency from the ledgers

| model | CPU | experiment | task | calls | median ms | ninety-fifth percentile ms |
| --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | <host> | `j3` | `judge_record_evidence` | 300 | <time> | <time> |
| `a-4b` | <host> | `j3` | `judge_record_negation` | 300 | <time> | <time> |
| `a-4b` | <host> | `j3` | `judge_record_rationale` | 300 | <time> | <time> |

### Lock

- Lock status: `unchanged`; New entries: 0

The lock already pins every file this run verified.

### Notes

- CPU models: <host>

Plumbing: a fake model answered; nothing here measures a model.

Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how this runner performed, not what a site would see.

Public data: real NHTSA vehicle complaint narratives, read with each complaint's codes hidden and scored against them. The codes are the components the complaint was filed under, not checked labels, and the models may have seen public complaints in training.
