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
replays 001 and 002, V001 and V002, all without early warning beyond chance. Those results motivated this choice of an
earlier outcome. **This is the fifth real-data test**, and it is reported whatever it shows. Every number from it carries
**`saw_recall_outcomes: yes`**.

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
