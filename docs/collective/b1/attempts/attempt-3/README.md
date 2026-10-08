# B1b attempt 3 (the last): holds on its own world, not on the robustness seeds, so it does not illustrate the case

Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

The plan (`PLAN.md`) was committed and pushed in `5f85e21` before any alert was computed on digest
`5578b5500d2edc4001d9a635b10a61df`. This directory then received the six run files of its first recording, made at
2026-10-08 22:17 UTC on that commit with a clean tree (the scorecard's `code` stamp: `5f85e21`, `dirty: no`), with
the deterministic stand-in. **This is the third and last attempt the pre-registration allows; its scenario is the
committed `demo/collective/scenario_codes_miss.json`, byte-identical, and its outcome is final.**

- **Replay:** `python demo/collective/collective_demo.py --replay docs/collective/b1/attempts/attempt-3`.

## The outcome, read from `scorecard.json`

| Pointer | Value | Meaning |
|---|---|---|
| `/codes_miss/main/x_caught`, `/codes_miss/main/x_week` | `true`, `2024-W34` | X caught the hero key (`lot:L10002:overheat`, rank 1) a week after its first week, `2024-W33` |
| `/codes_miss/gate_status` | `supported` | three confirming plants, the sibling plant refutes; all 12 run checks pass |
| `/codes_miss/main/channels/R_mf/attributed_no_later`, `.../S/attributed_no_later` | `false`, `false` | neither R (model-free) nor S has an alert of a case key by X's week that the world without the hero lacks; S's first such alert is `product:SD-9:malfunction_unspecified` in `2024-W38` |
| `/codes_miss/holds_main` | `true` | **on its own world, the run shows the case the allowed fields miss** |
| `/codes_miss/main/channels/R_mf/cooling_at_x_week` | `lot:L10002:...`, `product:SD-9:...` (under the generic code) | R had flagged the hero's lot and product under the generic code after the intake form began, before the case, and held them cooling in X's week; so `strict_no_later` is true for both channels |
| `/codes_miss/robust_holding`, `/codes_miss/robust_seeds` | `0`, `8` | **on every robustness seed, R or S flags a hero key under the generic code no later than X** (weeks 2024-W33 to W36 against X's W34) |
| `/codes_miss/holds_robust`, `/codes_miss/holds`, `/codes_miss/holds_strict` | `false`, `false`, `false` | **this run does not illustrate the case codes miss** |
| `/codes_miss/grid_holding` | 1 of 6 | reported, not part of the verdict |

## What the three attempts show

On this constructed world, with the structured fields filled at the generator's own rates (R4), the fields allowed
to leave carry the case about as early as X does: across the 42 worlds the three attempts logged (each a main world,
eight seeds and six grid cells, each against its own world without the hero), R or S flags one of the hero's keys
under the generic code within about two weeks of X in most of them. The one world where neither did by X's week is
attempt 3's main world, and even there the baselines had already flagged the hero's lot and product when the
intake form began. The pre-registered illustration therefore does not exist: no committed run shows a case the
allowed fields miss. Whether real cases look like this is what N1 and the Phase-1 signal audit measure.
