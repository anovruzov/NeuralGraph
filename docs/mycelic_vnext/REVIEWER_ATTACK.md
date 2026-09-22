# Mycelic vNext — adversarial review

_Written to falsify the claims in `NEXT_RESEARCH_REPORT.md`. Each attack is followed by what the artifacts say and what was done about it. Where an attack lands, it says so._

## Attack 1 — the gains are tuned on the evaluation seeds

The calibration seeds are 500–502; the evaluation seeds are 0–4 (10k) and 0–2 (50k). Every knob in the frozen configuration carries the tag of the paired file that confirmed it (`frozen_config_table` in the report). Two candidates that won on the calibration seeds lost on the evaluation seeds and were dropped, which is the protocol working, not a coincidence. **Where it lands**: the evaluation seeds were read more than once during this work (each accepted change was read on them). That is sequential testing on a fixed panel; the report's numbers are therefore optimistic by an amount five paired seeds cannot bound. Mitigation offered: the first item under *What would change my mind* is a fresh seed panel.

## Attack 2 — the ranker is a simulator artefact

Its features are exact quantities the simulator hands the kernel (witness signatures, lag, lineage). A real kernel would estimate them from claims with noise. The live-model harness in the main report measures the discrimination a frontier model achieves from raw notes; it does not yet measure discrimination from kernel-side claims. **Lands partly**: the *size* of the ranker gain is a simulator number; the *direction* (ordering was the binding stage) rests on the funnel, which counts gold patterns, not on the ranker.

## Attack 3 — the baselines were handicapped

The centralised controls adopt the ranker only where their own calibration says it is not worse; A2 adopted it and went from 0.663 to n/a at 10k. A_flat_rag kept the hand score by the same rule. The old A2 rows are in the OLD column of the same tables. **Does not land** on the comparison; it does mean the "gap to A2" moved less than the hierarchy's own gain.

## Attack 4 — metric gaming

Register cap: unchanged (`min(6000, max(600, n_entities))`); the unbounded register appears only in rows labelled diagnostic. Gold, worlds, seeds, metrics: unchanged (the funnel code was verified by three independent refuters before the work started, and the corrections it needed are in the research log). Question budget: a fraction of the triage queue, not of the number of gold patterns. **Does not land.**

## Attack 5 — hidden centralisation

Nothing new moves up: raw text leaving a node is 0.0 and the claim exposure fraction is identical before and after (the paired tables show `claims out` as *same*). The ranker's anchor-context features are counts over candidates the kernel already holds. Re-extraction, the one change that touched raw records, stayed local and was rejected anyway. **Does not land.**

## Attack 6 — the decoy regression is being minimised

It is not: decoy acceptance at 10k went from 0.205 to n/a and the one fix tried (decoy-weighted selection) was rejected because it cost found and rare recall. FDR fell slightly. **Lands**: a register with more true patterns and more decoys is a better register only if the reader's cost of a decoy is below the value of a pattern; the report does not claim otherwise.

## Attack 7 — the hierarchy still loses to centralised discovery

Yes: n/a vs n/a at 50k, at nan× of A2's compute. The brief's targets (0.70 / 0.60) were not met. The report says which stage holds the rest (ordering at 10k, coverage at 50k) and what would test it. **Lands.**

## Attack 8 — single-seed screens are being cited

Two negative results (weak sketch bits, descent-evidence merge) rest on one calibration seed. They are cited as screens that stopped further spending, not as findings, and they are not in the ledger table. **Lands on wording, addressed.**

## Attack 9 — the hybrid DP could leak scrambled-time decoys

Letting a link float across its clusters relaxes the order test for single-witness links. The paired runs show decoy acceptance +0.03 for the hybrid arm with the refitted ranker (inside noise) and −0.01 with the previous ranker; the D2 (temporal scramble) family is in `decoy_D2_temporal_scramble` in every row for anyone who wants to check the family separately. **Not resolved with five seeds**; watch it on the fresh panel.

## Revision after the review

- Kept: the three accepted changes and the lean arm as a labelled Pareto point.
- Reworded: the single-seed screens; the decoy regression is stated in the first paragraph of the report.
- Added to the queue: a fresh evaluation panel (seeds 5–9) before any of the reported deltas is quoted outside this repository.

## What would change my mind

- If `H_mycelic_prev` inside the new suite does not reproduce the archived v1 rows to the third decimal, the old-vs-new comparison is contaminated by a code change and every Δ in section 1 is suspect.
- If the learned ranker's out-of-sample AUC in section 7 is not above the hand-set score's, the found gain is a gate artefact and should not survive a different register cap.
- If a fresh set of evaluation seeds (5–9) gives hybrid timing a negative paired delta, it joins modal timing in the rejected column.
- If A2 with the ranker beats the hierarchy at 50k at equal compute (it does not today: 10.2e6 vs 3.9e6 units), the compute argument for the hierarchy is gone and only the privacy argument remains.
- If queue item 1 shows 50k coverage is depth-limited, the lean arm's mechanism is the wrong direction and breadth spending should be reverted.
- If the live discrimination harness shows a frontier model extracting a signal from raw notes that no kernel-side feature carries, the ranker's ceiling is a property of the simulator, not of the design.
