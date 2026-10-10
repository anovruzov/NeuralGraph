# D002: can a pack be drafted from an export alone?

Verdict: **pass**. D002 passes only if all six criteria pass.

| Criterion | Passes | Detail |
|---|---|---|
| C1 | yes | diff 0.6666666666666666; diff_fraction 2/3; ci_low 0.537037037037037; ci_high 0.7962962962962963; ci_low > 0: yes; diff >= 0.1: yes; best_control majority_prior; what the refusal removed: refused categories 0 (corpus rows 0 of 549, share 0.000; with R rows and a specific label 0); refused floor-passing terms 2 (would have been assigned 2, of them within K 2); label-name parts refused 0; companies 3 |
| C2 | yes | diff 0.7560975609756098; ci_low 0.6363636363636364; ci_high 0.8701298701298701; ci_low > -0.05: yes; ci_low > 0 (superior, decides nothing): yes; against pack; records 72; matched names per make CHEVROLET 4, DODGE 4, FORD 4, HONDA 4, JEEP 4, NISSAN 4; what the refusal removed: refused categories 0 (corpus rows 0 of 1296, share 0.000; with R rows and a specific label 0); refused floor-passing terms 1 (would have been assigned 1, of them within K 1); label-name parts refused 0; companies 6 |
| M1 | yes | label_hits 0; code_commits 1; download_code_total 902 |
| M2 | yes | drafted 9; loaded 9 |
| M3 | yes | packs 9; passed_floor 9; last_guard_clean True |
| M4 | yes |  |

Every number below comes from the run. Filed categories are the answer key, not checked labels.
C2 compares the drafted pack with `pack/`, whose lexicons are mostly the component names themselves: a pass means no worse than that list of names, within the margin.

## Arm msha

Sampled records, pooled: 54. Share whose narrative holds its own filed label: 0.000.

Every interval resamples the sampled records of these companies. It is not an interval for the field in general.

label_names is a mechanical split of each label at '/', ',', ';', brackets and the joining words. It is not how a person would start a hand pack.

| Reader | Micro P | Micro R | Micro F1 | 95% interval | Macro F1 | 95% interval | Coverage |
|---|---|---|---|---|---|---|---|
| drafted | 1.000 | 1.000 | 1.000 | 1.000 to 1.000 | 1.000 | 1.000 to 1.000 | 1.000 |
| label_names | n/a | 0.000 | 0.000 | 0.000 to 0.000 | 0.000 | 0.000 to 0.000 | 0.000 |
| majority_prior | 0.333 | 0.333 | 0.333 | 0.204 to 0.463 | 0.167 | 0.112 to 0.211 | 1.000 |
| permuted_labels | n/a | 0.000 | 0.000 | 0.000 to 0.000 | 0.000 | 0.000 to 0.000 | 0.000 |

| Drafted minus | Difference | 95% interval |
|---|---|---|
| label_names | 1.000 | 1.000 to 1.000 |
| majority_prior | 0.667 | 0.537 to 0.796 |
| permuted_labels | 1.000 | 1.000 to 1.000 |

What the refusal removed (amendment A14), summed over the companies: refused categories 0 (corpus rows 0 of 549, share 0.000; with R rows and a specific label 0); refused floor-passing terms 2 (would have been assigned 2, of them within K 2); label-name parts refused 0; companies 3. No refused label or term is printed.

| Company | Corpus | Predicates | Placeholders | Other share | Refused categories: count, corpus rows, share | Refused terms: count, would be assigned, within K | Label-name parts refused | Drawn | Drafted F1 | Best control F1 | Roles right (see note) | Floor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c1 | 195 | 3 | 0 | 0.077 | 0, 0, 0.000 | 0, 0, 0 | 0 | 18 | 1.000 | 0.333 | 5 of 5 | passed |
| c2 | 183 | 3 | 0 | 0.082 | 0, 0, 0.000 | 2, 2, 2 | 0 | 18 | 1.000 | 0.333 | 5 of 5 | passed |
| c3 | 171 | 3 | 0 | 0.088 | 0, 0, 0.000 | 0, 0, 0 | 0 | 18 | 1.000 | 0.333 | 5 of 5 | passed |

Refused categories: the corpus rows of each, largest first: c1 none; c2 none; c3 none.

Roles right: the inference's header words and markers were chosen knowing both sources' headers (rule 1.2, amendment A9). The count says little, and for MSHA nothing.

### msha c1: predicates and first terms

- FALL OF ROOF OR BACK (60 corpus records; 0 refused terms would have been assigned): rock, bolter, roof, brow
- HANDLING OF MATERIALS (60 corpus records; 0 refused terms would have been assigned): pallet, carrying, lifting, strained
- SLIP OR FALL OF PERSON (60 corpus records; 0 refused terms would have been assigned): slipped, walkway, icy, stairs, icy slipped, stairs walkway

### msha c2: predicates and first terms

- FALL OF ROOF OR BACK (56 corpus records; 2 refused terms would have been assigned): bolter, brow, roof, rock, near
- HANDLING OF MATERIALS (56 corpus records; 0 refused terms would have been assigned): pallet, strained, carrying, lifting
- SLIP OR FALL OF PERSON (56 corpus records; 0 refused terms would have been assigned): slipped, stairs, walkway, icy

### msha c3: predicates and first terms

- FALL OF ROOF OR BACK (52 corpus records; 0 refused terms would have been assigned): bolter, rock, roof, brow
- HANDLING OF MATERIALS (52 corpus records; 0 refused terms would have been assigned): strained, pallet, lifting, carrying, carrying strained
- SLIP OR FALL OF PERSON (52 corpus records; 0 refused terms would have been assigned): walkway, slipped, stairs, icy
## Arm nhtsa

Sampled records, pooled: 72. Share whose narrative holds its own filed label: 0.139.

Every interval resamples the sampled records of these companies. It is not an interval for the field in general.

label_names is a mechanical split of each label at '/', ',', ';', brackets and the joining words. It is not how a person would start a hand pack.

| Reader | Micro P | Micro R | Micro F1 | 95% interval | Macro F1 | 95% interval | Coverage |
|---|---|---|---|---|---|---|---|
| drafted | 1.000 | 1.000 | 1.000 | 1.000 to 1.000 | 1.000 | 1.000 to 1.000 | 1.000 |
| label_names | 1.000 | 0.139 | 0.244 | 0.130 to 0.364 | 0.158 | 0.087 to 0.201 | 0.139 |
| majority_prior | 0.250 | 0.250 | 0.250 | 0.153 to 0.347 | 0.100 | 0.065 to 0.138 | 1.000 |
| permuted_labels | n/a | 0.000 | 0.000 | 0.000 to 0.000 | 0.000 | 0.000 to 0.000 | 0.000 |

| Drafted minus | Difference | 95% interval |
|---|---|---|
| label_names | 0.756 | 0.636 to 0.870 |
| majority_prior | 0.750 | 0.653 to 0.847 |
| permuted_labels | 1.000 | 1.000 to 1.000 |

Matched label space (rule 2.2):

| Hand pack | Records | Drafted F1 | Hand F1 | Drafted minus hand | 95% interval | Hand F1, own space |
|---|---|---|---|---|---|---|
| pack | 72 | 1.000 | 0.244 | 0.756 | 0.636 to 0.870 | 0.244 |
| pack-v2 | 72 | 1.000 | 0.244 | 0.756 | 0.636 to 0.870 | 0.244 |

What the refusal removed (amendment A14), summed over the companies: refused categories 0 (corpus rows 0 of 1296, share 0.000; with R rows and a specific label 0); refused floor-passing terms 1 (would have been assigned 1, of them within K 1); label-name parts refused 0; companies 6. No refused label or term is printed.

| Company | Corpus | Predicates | Placeholders | Other share | Refused categories: count, corpus rows, share | Refused terms: count, would be assigned, within K | Label-name parts refused | Drawn | Drafted F1 | Best control F1 | Roles right (see note) | Floor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CHEVROLET | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 0, 0, 0 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |
| DODGE | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 0, 0, 0 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |
| FORD | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 1, 1, 1 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |
| HONDA | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 0, 0, 0 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |
| JEEP | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 0, 0, 0 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |
| NISSAN | 216 | 4 | 0 | 0.000 | 0, 0, 0.000 | 0, 0, 0 | 0 | 12 | 1.000 | n/a | 5 of 5 | passed |

Refused categories: the corpus rows of each, largest first: CHEVROLET none; DODGE none; FORD none; HONDA none; JEEP none; NISSAN none.

Roles right: the inference's header words and markers were chosen knowing both sources' headers (rule 1.2, amendment A9). The count says little, and for MSHA nothing.

### nhtsa CHEVROLET: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): collision, deploy, inflated, airbag
- ENGINE (59 corpus records; 0 refused terms would have been assigned): misfire, idling, revving, stalled
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): pedal, grinding, braking, stopping
- STEERING (59 corpus records; 0 refused terms would have been assigned): pulling, drifted, veered, steering

### nhtsa DODGE: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): collision, airbag, deploy, inflated
- ENGINE (59 corpus records; 0 refused terms would have been assigned): idling, stalled, revving, misfire
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): grinding, pedal, braking, stopping, braking grinding
- STEERING (59 corpus records; 0 refused terms would have been assigned): steering, drifted, veered, pulling

### nhtsa FORD: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): deploy, inflated, collision, airbag
- ENGINE (59 corpus records; 1 refused terms would have been assigned): idling, revving, misfire, stalled
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): braking, pedal, grinding, stopping
- STEERING (59 corpus records; 0 refused terms would have been assigned): drifted, pulling, veered, steering

### nhtsa HONDA: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): airbag, deploy, inflated, collision
- ENGINE (59 corpus records; 0 refused terms would have been assigned): stalled, misfire, idling, revving
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): pedal, stopping, braking, grinding, pedal stopping
- STEERING (59 corpus records; 0 refused terms would have been assigned): steering, drifted, pulling, veered

### nhtsa JEEP: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): deploy, airbag, collision, inflated
- ENGINE (59 corpus records; 0 refused terms would have been assigned): revving, stalled, misfire, idling, stalled revving
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): stopping, grinding, pedal, braking
- STEERING (59 corpus records; 0 refused terms would have been assigned): pulling, steering, veered, drifted

### nhtsa NISSAN: predicates and first terms

- AIR BAGS (59 corpus records; 0 refused terms would have been assigned): inflated, airbag, deploy, collision
- ENGINE (59 corpus records; 0 refused terms would have been assigned): idling, revving, stalled, misfire
- SERVICE BRAKES (59 corpus records; 0 refused terms would have been assigned): pedal, braking, grinding, stopping
- STEERING (59 corpus records; 0 refused terms would have been assigned): steering, veered, pulling, drifted

## Pilot audit (M4)

Records 100; sites 4; weeks evaluated 136; review list 0; alerts by channel: P 4, PRR 0, R_mf 0, S 0, X 0.

## Code and inputs

- Code commit `<code>`; code hash of the onboard package and the download code `<code>`; settings sha256 `8bb9ba6ab9c8d4dca14ac86aa8df89f8cd1926b5355b9908ec3b71020c42c0e5`.
- `tools/market/nhtsa_export.py`: 271 lines (per-field code: the download code).
- `tools/market/vehicle_pack.py`: 248 lines (per-field code: the download code).
- `tools/onboard/fetch_msha.py`: 242 lines (per-field code: the download code).
- `tools/onboard/fetch_nhtsa.py`: 141 lines (per-field code: the download code).
- msha download `Accidents.zip`: 445989 bytes, sha256 `a3641432636c96f870ce27e0f17584564a4968d39e32aa46f549781c3f743cb5`.
- msha download `Accidents_Definition_File.txt`: 183 bytes, sha256 `d5b8abeebdf10953205dbc0bd8fc7e8d95e198216ea7ad76e3fe3b90f1c39121`.
- nhtsa download `COMPLAINTS_RECEIVED_2020-2024.zip`: 332441 bytes, sha256 `f66034f5676722a2b87d3f03de294e19fa9dda4fd9b4e4c07980a9ebe61b42de`.

### msha: the definition file's lines for the declared columns

- `ACCIDENT_DT`:
  > ACCIDENT_DT	The accident date, mm/dd/yyyy.
- `ACCIDENT_TYPE`:
  > (no line names it)
- `CLASSIFICATION`:
  > CLASSIFICATION	The circumstances.
- `CLOSED_DOC_NO`:
  > (no line names it)
- `CONTRACTOR_ID`:
  > (no line names it)
- `CONTROLLER_ID`:
  > (no line names it)
- `CONTROLLER_NAME`:
  > (no line names it)
- `DOCUMENT_NO`:
  > DOCUMENT_NO	The document number of the accident.
- `FIPS_STATE_CD`:
  > (no line names it)
- `MINE_ID`:
  > MINE_ID	The mine.
- `NARRATIVE`:
  > NARRATIVE	What happened.
  > In words.
- `OPERATOR_ID`:
  > (no line names it)
- `OPERATOR_NAME`:
  > (no line names it)
