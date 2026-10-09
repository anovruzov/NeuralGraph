# Public replay V003: the same six makes, with defect investigations as the outcomes

V001 and V002 used recalls as the outcomes and found no early warning beyond chance. Across six makes, the detectors
alerted after a recall far more often than before it. V001's rule already noted that a recall is not the moment a
problem is first seen. NHTSA opens a defect investigation earlier, often after complaints accumulate: a preliminary
evaluation, or a defect petition from the public. V003 asks whether counting complaints across states flags a problem
before NHTSA opens an investigation into it. A manufacturer wants exactly that warning. The rule and the criterion
below are committed before any investigation's content is read. The probe that informs them read only the file's
published field list, size and row count (`nhtsa-inv-probe.json`; run
[37871721689](https://github.com/anovruzov/NeuralGraph/actions/runs/37871721689)).

## The declaration

The rule was written by the AI system that wrote this repository's code. Its training data includes public news of
NHTSA investigations of these makes. It had also seen the results of the four earlier real-data tests: device
replays 001 and 002, V001 and V002, all without early warning beyond chance. Those results motivated this choice of
an earlier outcome. **This is the fifth real-data test**, and it is reported whatever it shows. Every number from it
carries **`saw_recall_outcomes: yes`**.

## The rule

1. **Data:** the complaint file as in V001, and the defect investigation flat file (`FLAT_INV.zip`: 154,429 rows of 11
   fields at the probe), as published on the day of the run.
2. **Makes:** the six of V001 and V002, unchanged: FORD, CHEVROLET, JEEP, HONDA, NISSAN, DODGE.
3. **Outcomes** (`nhtsa_export.investigations`):
   - one per investigation and vehicle (`ACTION/make-model-year`), opened on the investigation's open date (`ODATE`)
     in the window 2023-01-01 to 2024-12-31;
   - only the kinds that open an inquiry into a possible defect: preliminary evaluations (action numbers beginning
     `PE`) and defect petitions (`DP`). Engineering analyses (`EA`) mostly upgrade a preliminary evaluation, and recall
     and audit queries (`RQ`, `AQ`) follow a recall, so they are left out;
   - the failure is the investigation's component category when it names exactly one of the pack's categories for
     that vehicle; otherwise the outcome matches any failure on that vehicle (as V001 did for recalls);
   - a model year of 9999 (unknown) cannot name a vehicle, and its rows are dropped and counted.
4. **Everything else is V002's**:
   - the window;
   - the complaint export (each state a site);
   - the frozen pack (config `a588e72d`, vocabulary `867df7fc`, detector `325b125a`, fixtures `9d9d8bd6`);
   - the audit settings (look-back 26 weeks, post 26 weeks, tie salt `pilot`);
   - one audit per make.
5. **The criterion:** V002's, over the six makes. For each channel (X, S, model-free R), combine the makes'
   circular-shift p-values by Fisher's method. A channel shows **early warning beyond chance only if its combined p is
   below 0.05 / 3**. A make with no investigation in scope has no p and is left out.
6. **The first run is the result.** A run that fails before the audit for an infrastructure reason may run again
   unchanged. Any change to a setting is a new choice file.

## How it is read

- **Investigations and complaints are not independent.** NHTSA screens these same complaints when it decides to open
  an investigation. A positive result would mean the cross-state count flags, before the regulator opens an inquiry,
  what the regulator later looks into, from the same public data. It would not mean the detectors find what nobody
  could see. For a manufacturer, that earlier sight of what the regulator will ask about is the value. Whether the
  company's own records give more warning stays a question for a pilot.
- **Five tests have now been run.** A single p just under its threshold should be read in that light. The threshold
  above is not lowered for it. The record of all five stands.
- The makes are not independent (V002's caveat), and Fisher's method on these discrete p-values is conservative.
- What V001 says it is not, this is not either: states are not a company's sites, public complaints are not a
  company's complaint file, and the lexical reader is not a model.

## Run 004: the result

[Run 37871956063](https://github.com/anovruzov/NeuralGraph/actions/runs/37871956063), run file `run-004.json`, commit
`c120440` (this rule: `ac8c9b5`), job 113631856587. Read in full. Each audit's sha256 matches the one the job printed:
- FORD: `6bbc1ce13ad12ada846e959421b75aa2697f4869bccd9b079f1567e79f3a939c`
- CHEVROLET: `e081aea00caeff221ad5bdab65b4ef4d2c5452b3666747bac1b449d35b74fd51`
- JEEP: `f7fdc14a02e15686df3f7c8bb50c8c1d6d8e1b667986edcef00ab72d93023876`
- HONDA: `8694a06367cb02e1b97f5783bea86a26443eaa1c1ea91c7e86b049c9b28bb53f`
- NISSAN: `7c558518e19f5c72d6279d738a672ecf3a9a1ae01453a9cb1d15799901d62cb2`
- DODGE: `d9365747ee1a9f9d9b785bfa3d1c80b48efaa1853d4bc9bb68a0c33bc89bb7a1`

The complaint exports are V001's and V002's, unchanged (the same counts). Of the six makes' investigation rows opened
in the window, the exporter kept the preliminary evaluations and defect petitions: 31 investigations, 22 of them
opened late enough to have a look-back in the evaluated weeks.

| Make | Investigations opened (in scope) | Outcomes in scope | X: found, expected, p | S: found, expected, p | R_mf: found, expected, p |
|---|---|---|---|---|---|
| FORD | 11 (9) | 71 | 3, 5.47, 0.93 | 3, 5.36, 0.89 | 3, 6.17, 0.94 |
| CHEVROLET | 4 (3) | 10 | 0, 0.30, 1.00 | 0, 0.30, 1.00 | 0, 0.60, 1.00 |
| JEEP | 3 (2) | 9 | 1, 0.90, 0.75 | 1, 0.97, 0.82 | 1, 1.26, 0.80 |
| HONDA | 4 (2) | 12 | 0, 0.30, 1.00 | 0, 0.30, 1.00 | 0, 0.30, 1.00 |
| NISSAN | 6 (5) | 20 | 2, 2.10, 0.79 | 1, 1.76, 0.91 | 1, 2.15, 0.98 |
| DODGE | 3 (1) | 4 | 0, 0.00, 1.00 | 0, 0.00, 1.00 | 0, 0.00, 1.00 |
| **All six** | **31 (22)** | **126** | **6, 9.07** | **5, 8.68** | **5, 10.48** |

**The criterion is not met in any channel.** Fisher's combined p is above 0.9999 for X, S and R_mf alike, against
0.0167. X flagged 6 of the 126 investigation outcomes before the investigation opened, where randomly timed alerts
flag 9.07. In the 26 weeks after an investigation opened, X alerted on 17 of them, S on 16 and R_mf on 19.

## Five real-data tests, one answer

| Test | Outcomes | Found before, X | Expected by chance, X |
|---|---|---|---|
| Device replay 002 (one manufacturer) | 8 recalls | 1 | 1.16 |
| V001, run 002 (FORD) | 298 recall outcomes | 5 | 10.95 |
| V002 (five makes) | 598 recall outcomes | 9 | 19.51 |
| V003 (six makes) | 126 investigation outcomes | 6 | 9.07 |

Device replay 001 is left out: its identifiers did not resolve, so it measured nothing. On public complaint data, in
two fields, counting across sites gave no warning earlier than chance. That holds whether the outcome is a recall or
the regulator's own investigation. The detectors alert more often after an outcome than before it. These tests do not
reach a company's own records, its real sites or a model reading narratives. No further public replay is planned: the
next test that can change this answer is a pilot on a company's own data.
