## Lab report

- Request: `lab/requests/accuracy-003.json`, name `accuracy-003`, sha `4a851dd85365`
- Plan: `e1b7d371b9f3`; Commit: `c5dad9a81f3b77c54ec91cadc4037ad499c58e6f`; Provider: `llama-server`
- Units: 5; Shards: 5

Purpose: `The judging run of docs/collective/A003/CHOICE-A003.md (amended before any build and after the term run), cycle three toward the target: the primary model judges a fresh NHTSA draw (three earlier cycles' records and the term run's drafting set excluded, codes hidden) under the negation task with and without the drafted terms in its payload, beside three lexical judges; primary arm negation_terms. Public data; the first run is the result. Copy to lab/requests/accuracy-003.json.`

### Shards

| shard | model | state | attempt | failed step | exit code | wall seconds | CPU | reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `s001-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | 1612.6 | `AMD EPYC 7763 64-Core Processor` | n/a |
| `s002-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | 1601.5 | `AMD EPYC 7763 64-Core Processor` | n/a |
| `s003-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | 1599.2 | `AMD EPYC 7763 64-Core Processor` | n/a |
| `s004-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | 1022.0 | `AMD EPYC 9V74 80-Core Processor` | n/a |
| `s005-a-4b` | `a-4b` | `sealed` | 1 | n/a | 0 | 1036.3 | `AMD EPYC 9V74 80-Core Processor` | n/a |

### Model on runner CPU

| unit | experiment | model | shard | status | wall seconds |
| --- | --- | --- | --- | --- | --- |
| `j4-a-4b-p1` | `j4` | `a-4b` | `s001-a-4b` | `ok` | 1561.4 |
| `j4-a-4b-p2` | `j4` | `a-4b` | `s002-a-4b` | `ok` | 1551.1 |
| `j4-a-4b-p3` | `j4` | `a-4b` | `s003-a-4b` | `ok` | 1549.2 |
| `j4-a-4b-p4` | `j4` | `a-4b` | `s004-a-4b` | `ok` | 988.7 |
| `j4-a-4b-p5` | `j4` | `a-4b` | `s005-a-4b` | `ok` | 1003.2 |

#### Accuracy test, third cycle: the verifier's narrow question on a fresh draw with the drafted terms in the payload, four declared arms on one model, one primary pair

`J4` here asks one model the site verifier's narrow question about a fresh draw of real public NHTSA complaint narratives with three earlier cycles' records and the term run's drafting set excluded, one record at a time with its codes hidden, twice: under the earlier cycle's base task with the drafted terms of the asked component added to the payload, and under the base task alone; each verdict is scored against the components the complaint was filed under (filed codes, not human-checked labels), by the product's own verdict rule on the answers as answered and, as a declared secondary, after the branch's reading (the unclear-to-no mapping under the negation base, the grounding check under the evidence base), beside three lexical judges: the lexical judge with the drafted terms, the hand pack's lexical judge and the drafted term-presence judge.

- labels: source `nhtsa`, records 150, sha `cca4cdf749b0`; questions 300, parts 5; base task `negation`
- eligible records 58319; excluded records 571; eligible after exclusion 57748; overlap with the excluded set 0
- sources: `J1` `verified` (150 refs); `R1` `verified` (150 refs); `A1` `verified` (150 refs); `R2` `verified` (150 refs); `K2` `verified` (300 refs); `B1` `verified` (150 refs; questions sha `84486fa38568`); `R3` `verified` (150 refs); `K3` `verified` (450 refs)
- cycles established by: `j001` `artifact` (150 refs); `a001` `artifact` (150 refs); `a002` `artifact` (150 refs)
- consistency counts: `j1_r1` 0; `a1_r2` 0; `b1_r3` 0; `outside_k2` 0; `outside_k3` 0; `k2_union` 0; `k3_union` 0
- removed by record id 0; removed by text 121; drafting records already excluded 0; drawn records received before the cutoff 0; drawn drafting records 0
- complaint archive sha `4e5ae79183c4`; the term run's archive sha `4e5ae79183c4`; same archive as the term run yes; the term run's drafting rows 77590; drafting rows rebuilt 77590; the term run's drafting refs sha `6072b844d878`; rebuilt drafting refs sha `6072b844d878`
- excluded refs file sha `8f7b246328e1`; chain file lines 721; complaint file sha `4e5ae79183c4`
- term set sha `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`; term set verified yes; term run `38612800578`; term artifact `lab-plan-38612800578-1`; drafted pack vocabulary hash `c0260f0edab4`; term module code hash `f5743df58d41`
- lexical judge with the drafted terms, every record: balanced accuracy 0.773 (0.730, 0.817); sensitivity 0.580; specificity 0.967; unknown verdicts 0
- lexical judge, every record: balanced accuracy 0.657 (0.617, 0.697); sensitivity 0.340; specificity 0.973; unknown verdicts 0
- drafted term-presence judge, every record: balanced accuracy 0.767 (0.723, 0.810); sensitivity 0.580; specificity 0.953; unknown verdicts 0
- record-blind control, every record: balanced accuracy 0.497 (0.440, 0.553)
- predicate-only bound, every record: 0.560
- the lexical judges paired, record by record: `drafted_minus_hand` 0.117 (0.070, 0.163); `presence_minus_hand` 0.110 (0.063, 0.157); `presence_minus_drafted` -0.007 (-0.017, 0.000)
- comparisons since the loop began: 25 (4 this cycle, 21 before)

The draw excludes three earlier cycles' records, each by its plan artifact and by a rebuild from the complaint file as published on the day, and the two later cycles' chain files: a cycle's set is established by its own artifact or rebuild, or else from a chain less the other cycles' sets that chain covers; a chain alone establishes nothing; every consistency count must be zero; the excluded set is the union of every verified source's refs. Before the draw the term run's drafting set is removed from the eligible records by record id and by narrative, and after it two tripwires check that no drawn record was received before the cutoff or is a drafting record. Counts and hashes only: no record ref is written anywhere but the plan artifact.

The lexical judge is the verifier's judge when no model runs: it confirms a question only when one of the pack's phrases for the component is in the narrative and not negated. It judged every question in the plan job, before any model ran.

The record-blind control never reads a record: it confirms a question only when its component is one of the four filed most often in NHTSA's whole complaint file, and refutes it otherwise. It shows what knowing which components are common gets a judge without reading. The predicate-only bound is the most any judge that sees only the component could score on the same records; it is fitted to their answers, so it is optimistic. Neither decides anything.

Four arms on one model, declared before the run and named for the base task in force: the base task's name with the terms suffix reads the drafted-terms task's answers as answered (the primary arm, letter D); the same with the reading's name reads them through the branch's reading (letter Dr, the headline secondary); the base task's name alone, and with the reading's name, are the earlier cycle's arm and its reading replicated on this draw (letters B and Br, the controls, shown beside the earlier cycle's own figures as a between-draw difference that decides nothing). Model calls are made for the two tasks only: the reading arms are the same replies read again. The lifts are paired differences between arms, record by record: D minus B reads the lever, Dr minus Br the lever under the reading, Dr minus D and Br minus B the reading; a lift helps when its interval's low end is above zero, hurts when its high end is below zero, and is otherwise not told apart. They decide nothing toward the target.

The verdict word is read on the primary pair alone, from the figures, in this order: incomplete (a part did not finish, the set is not a model measurement, transport failures left out more than one record in a hundred, or a paired difference to a lexical judge is not a number), pass_provisional (balanced accuracy at or above the target, the paired difference to the lexical judge with the drafted terms with its low end above zero, the balanced accuracy's low end above the predicate-only bound on the same records, the unknown share reported, and the paired differences to the hand pack's lexical judge and to the drafted term-presence judge with their low ends above zero), partial (the target reached but an interval clause or an added clause failed, or every lexical judge beaten below the target), else failure. The word under lever five's text alone (the first four clauses with the drafted judge) is printed beside it. Every other arm is a secondary with the same word, which is a hypothesis for the next cycle, never a result; the headline secondary's word is printed beside the primary's as the primary under the other reading of the standing default; a first pass is provisional until the next cycle's fresh draw confirms it.

Counts and hashes only: the term set lives in the term run's plan artifact, this run's plan artifact holds a copy pinned by its full sha, and no term is printed in a summary, a console line, a report or a commit. Each question of the drafted-terms task carries the drafted terms of its own asked component, positive or negative alike; a component the drafter did not reach carries an empty list. The agreement shares say on how many questions the primary arm's verdict equals a lexical judge's, and the confirm rates how often it confirms where that judge confirms and where it refutes: a primary arm that follows a judge line for line adds nothing to it. In a dry run the fake answers the drafted-terms task as it answers the base task, so nothing in a dry run measures the terms.

Primary pair `a-4b` `negation_terms`: `failure`; under lever five's text alone: `failure`. It scored balanced accuracy 0.780 [0.733, 0.823]; gap to the target 0.860 is 0.080; drafted lexical judge 0.773 [0.730, 0.817] on the same records, paired difference 0.007 [-0.047, 0.057]; hand lexical judge 0.657 [0.617, 0.697], paired difference 0.123 [0.070, 0.173]; term presence 0.767 [0.723, 0.810], paired difference 0.013 [-0.037, 0.063]; predicate-only bound 0.560; unknown share 0.047. Under the other reading of the standing default the primary would be `negation_terms_mapped`: `failure`.

#### Accuracy test, third cycle: every arm beside the three lexical judges, the control and the bound on the same records

| model | arm | arm letter | task | reading | class | parts finished | balanced accuracy | interval low | interval high | drafted lexical judge balanced accuracy | minus the drafted lexical judge, paired by record | hand lexical judge balanced accuracy | minus the hand lexical judge, paired by record | term-presence judge balanced accuracy | minus the term-presence judge, paired by record | control balanced accuracy | predicate-only bound | sensitivity | specificity | unknown share | records left out | verdict word | word under lever five's text alone | pass test | primary pair | headline secondary | headline | headline against the hand lexical judge | headline against the term-presence judge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `negation_terms` | `D` | `judge_record_negation_terms` | `shipped` | `model` | 5 of 5 | 0.780 | 0.733 | 0.823 | 0.773 | 0.007 (-0.047, 0.057) | 0.657 | 0.123 (0.070, 0.173) | 0.767 | 0.013 (-0.037, 0.063) | 0.497 | 0.560 | 0.827 | 0.733 | 0.047 | 0 | `failure` | `failure` | no | yes | no | `not_told_apart` | `better` | `not_told_apart` |
| `a-4b` | `negation_terms_mapped` | `Dr` | `judge_record_negation_terms` | `mapped` | `model` | 5 of 5 | 0.793 | 0.750 | 0.833 | 0.773 | 0.020 (-0.033, 0.070) | 0.657 | 0.137 (0.087, 0.187) | 0.767 | 0.027 (-0.023, 0.077) | 0.497 | 0.560 | 0.827 | 0.760 | 0.013 | 0 | `failure` | `failure` | no | no | yes | `not_told_apart` | `better` | `not_told_apart` |
| `a-4b` | `negation` | `B` | `judge_record_negation` | `shipped` | `model` | 5 of 5 | 0.627 | 0.587 | 0.667 | 0.773 | -0.147 (-0.200, -0.093) | 0.657 | -0.030 (-0.083, 0.027) | 0.767 | -0.140 (-0.193, -0.083) | 0.497 | 0.560 | 0.893 | 0.360 | 0.090 | 0 | `failure` | `failure` | no | no | no | n/a | n/a | n/a |
| `a-4b` | `negation_mapped` | `Br` | `judge_record_negation` | `mapped` | `model` | 5 of 5 | 0.667 | 0.627 | 0.707 | 0.773 | -0.107 (-0.163, -0.050) | 0.657 | 0.010 (-0.043, 0.063) | 0.767 | -0.100 (-0.157, -0.043) | 0.497 | 0.560 | 0.893 | 0.440 | 0.027 | 0 | `failure` | `failure` | no | no | no | n/a | n/a | n/a |

#### Accuracy test, third cycle: the lifts between arms, paired by record

| model | lift | lever | paired records | difference | interval low | interval high | reading |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `D_minus_B` | `drafted_terms` | 150 | 0.153 | 0.103 | 0.203 | `helps` |
| `a-4b` | `Dr_minus_Br` | `drafted_terms` | 150 | 0.127 | 0.080 | 0.173 | `helps` |
| `a-4b` | `Dr_minus_D` | `unclear_mapping` | 150 | 0.013 | 0.003 | 0.027 | `helps` |
| `a-4b` | `Br_minus_B` | `unclear_mapping` | 150 | 0.040 | 0.020 | 0.063 | `helps` |

#### Accuracy test, third cycle: the three lexical judges paired with each other, record by record

| pair | paired records | difference | interval low | interval high |
| --- | --- | --- | --- | --- |
| `drafted_minus_hand` | 150 | 0.117 | 0.070 | 0.163 |
| `presence_minus_hand` | 150 | 0.110 | 0.063 | 0.157 |
| `presence_minus_drafted` | 150 | -0.007 | -0.017 | 0.000 |

#### Accuracy test, third cycle: the primary arm beside the drafted lexical judge and the term-presence judge, per kind

| model | arm | judge | kind | questions | verdicts equal to the judge's | confirms where the judge confirms | confirms where the judge refutes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `negation_terms` | `lexical_drafted` | `positive` | 150 | 0.647 (97) | 0.931 (81 of 87) | 0.683 (43 of 63) |
| `a-4b` | `negation_terms` | `lexical_drafted` | `negative` | 150 | 0.747 (112) | 0.600 (3 of 5) | 0.200 (29 of 145) |
| `a-4b` | `negation_terms` | `presence_drafted` | `positive` | 150 | 0.647 (97) | 0.931 (81 of 87) | 0.683 (43 of 63) |
| `a-4b` | `negation_terms` | `presence_drafted` | `negative` | 150 | 0.747 (112) | 0.571 (4 of 7) | 0.196 (28 of 143) |

#### Accuracy test, third cycle: the drafted terms each component's questions carried

| predicate | drafted terms carried |
| --- | --- |
| `air_bags` | 50 |
| `back_over_prevention` | 9 |
| `child_seat` | 0 |
| `electrical_system` | 50 |
| `electronic_stability_control_esc` | 0 |
| `engine` | 50 |
| `engine_and_engine_cooling` | 1 |
| `equipment` | 0 |
| `exterior_lighting` | 50 |
| `forward_collision_avoidance` | 50 |
| `fuel_propulsion_system` | 50 |
| `fuel_system_gasoline` | 3 |
| `lane_departure` | 1 |
| `latches_locks_linkages` | 11 |
| `power_train` | 50 |
| `seat_belts` | 28 |
| `seats` | 48 |
| `service_brakes` | 50 |
| `service_brakes_hydraulic` | 2 |
| `steering` | 50 |
| `structure` | 47 |
| `suspension` | 50 |
| `tires` | 20 |
| `vehicle_speed_control` | 8 |
| `visibility_wiper` | 32 |
| `wheels` | 43 |

- `a-4b` questions of the drafted-terms task whose payload carried an empty list: 0 of 300 (0.000)

#### Accuracy test, third cycle: per task, the first key, the output tokens and the replies cut at the cap

| model | task | first key is the new field | attempts | calls | median ms | ninety-fifth percentile ms | tokens out median | tokens out ninety-fifth percentile | replies cut at the cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `judge_record_negation_terms` | n/a | 300 | 300 | 13349.5 | 21862.5 | 13.0 | 16.0 | 0 |
| `a-4b` | `judge_record_negation` | n/a | 300 | 300 | 8211.8 | 15788.2 | 16.0 | 17.0 | 0 |

#### Accuracy test, third cycle: the controls beside the earlier cycle's own figures, a between-draw difference that decides nothing

| model | arm | arm letter | this draw, balanced accuracy | earlier cycle | earlier arm | earlier balanced accuracy | between-draw difference |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `negation` | `B` | 0.627 (0.587, 0.667) | `A001` | `negation` | 0.577 (0.530, 0.623) | 0.050 |
| `a-4b` | `negation_mapped` | `Br` | 0.667 (0.627, 0.707) | `A001` | `negation_mapped` | 0.653 (0.610, 0.697) | 0.014 |

#### Model call latency from the ledgers

| model | CPU | experiment | task | calls | median ms | ninety-fifth percentile ms |
| --- | --- | --- | --- | --- | --- | --- |
| `a-4b` | `AMD EPYC 7763 64-Core Processor` | `j4` | `judge_record_negation` | 180 | 9071.4 | 18897.8 |
| `a-4b` | `AMD EPYC 7763 64-Core Processor` | `j4` | `judge_record_negation_terms` | 180 | 15562.9 | 26090.0 |
| `a-4b` | `AMD EPYC 9V74 80-Core Processor` | `j4` | `judge_record_negation` | 120 | 6337.9 | 10528.1 |
| `a-4b` | `AMD EPYC 9V74 80-Core Processor` | `j4` | `judge_record_negation_terms` | 120 | 9714.1 | 15275.1 |

### Files to provision once per run

| target | model | attempt | verified | verified by | first use | Source | sha | plan matches | seconds | Problem |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `server` | n/a | 1 | yes | `lock` | no | `cache` | `2cda5ff93639` | yes | 0.5 | n/a |
| `gguf` | `a-4b` | 1 | yes | `lock` | no | `cache` | `7485fe6f11af` | yes | 1.8 | n/a |

### Lock

- Lock status: `unchanged`; New entries: 0

The lock already pins every file this run verified.

### Notes

- CPU models: `AMD EPYC 7763 64-Core Processor`, `AMD EPYC 9V74 80-Core Processor`

The shards ran on different CPU models: compare timings only between rows of the same CPU model.

Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how this runner performed, not what a site would see.

Public data: real NHTSA vehicle complaint narratives, read with each complaint's codes hidden and scored against them. The codes are the components the complaint was filed under, not checked labels, and the models may have seen public complaints in training.

Model measurement: a verified model file answered through a verified server on one shared GitHub-hosted runner; quality numbers describe this model on public complaint narratives against their filed codes, and timings describe this runner, not site hardware.
