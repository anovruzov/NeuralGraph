# B1b attempt 1: does not illustrate the case codes miss

Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

The first of at most three attempts that `docs/collective/b1/PREREG.md` (section 6) allows. This directory holds the
scenario bytes (`scenario_codes_miss.json`, sha256 `c534c9b76d956daad5bea2b274f41e6661428e837a347250749fdba992b43f27`,
scenario digest `c534c9b76d956daad5bea2b274f41e66`) and the six run files of the first recording of that digest
(`scorecard.json` is its first scorecard). Nothing computed alerts on this digest before this recording.

- **Recorded** 2026-10-08 21:44 UTC with `python demo/collective/collective_demo.py --record DIR --run-id
  collective-tarnwick-b1b --scenario demo/collective/scenario_codes_miss.json`, on the code of the commit that adds
  this directory (the scorecard's `code` stamp names its parent `064b8cf` with uncommitted changes: the B1b code was
  committed together with this attempt). The plants' model is the deterministic stand-in (no model).
- **What changed and why:** nothing; this is the pre-registered cast as PREREG section 3 fixes it.
- **Replay:** `python demo/collective/collective_demo.py --replay docs/collective/b1/attempts/attempt-1`.

## The outcome, read from `scorecard.json`

| Pointer | Value | Meaning |
|---|---|---|
| `/codes_miss/main/x_caught` | `true` | X alerted the hero key (`lot:L10002:overheat`) from the hero's first week, and the world without the hero did not |
| `/codes_miss/main/x_week` | `2024-W32` | X's first alert of the hero key; the hero's first week is `2024-W31` (`/codes_miss/hero_first_week`) |
| `/codes_miss/gate_status` | `supported` | the check with the sites |
| `/codes_miss/main/channels/R_mf/attributed/0` | `lot:L10002:malfunction_unspecified`, rank 1, `2024-W31` | R (model-free) flagged the hero's lot under the generic code in the hero's first week; the world without the hero did not |
| `/codes_miss/main/channels/S/attributed/0` | `lot:L10002:malfunction_unspecified`, rank 2, `2024-W31` | S flagged the same key in the same week |
| `/codes_miss/holds_main` | `false` | both channels have an attributed alert no later than X's week |
| `/codes_miss/robust_holding`, `/codes_miss/robust_seeds` | `4`, `8` | `holds_robust` is `true` (at least half) |
| `/codes_miss/grid_holding`, `/codes_miss/grid_cells` | `1`, `6` | reported, not part of the verdict |
| `/codes_miss/holds`, `/codes_miss/holds_strict` | `false`, `false` | **this run does not illustrate the case codes miss** |

The attempt-fatal checks (PREREG section 6) pass: X caught the case and the gate says supported. `holds` is false, so
the pre-registration allows another attempt, adjusting only the hero lot, the hero sites and their languages, the
hero rate, the hero start week (the shift follows) and the hero narrative templates.

**Why, as far as the scorecard shows it.** R reads record-level counts of the fields allowed to leave, with no
k-suppression, and the hero's lot field is filled in 60% of the hero records (`/scenario/structured_fill`; R4 fixes
it at the generator's own rate). One lot under the generic code at three plants is then a visible rise in the case's
first week, even with the intake form writing that code on every complaint since `2024-W23`. X's own key first
alerted a week later. On this world, the field allowed to leave carried the case.
