# B1b attempt 2: does not illustrate the case codes miss, and fails an attempt-fatal check

Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

The plan (`PLAN.md`, the scenario bytes and the rule that chose the lot) was committed and pushed in `5a15a99` before
any alert was computed on this digest. This directory then received the six run files of the first recording of
digest `5a92c52827c79f43174fbd268402db3d`, made at 2026-10-08 22:11 UTC on that commit with a clean tree (the
scorecard's `code` stamp: `5a15a99`, `dirty: no`), with the deterministic stand-in.

- **Replay:** `python demo/collective/collective_demo.py --replay docs/collective/b1/attempts/attempt-2`.

## The outcome, read from `scorecard.json`

| Pointer | Value | Meaning |
|---|---|---|
| `/codes_miss/main/x_caught`, `/codes_miss/main/x_week` | `true`, `2024-W32` | X caught the hero key (`lot:L20045:overheat`, rank 1) a week after its first week, `2024-W31` |
| `/codes_miss/gate_status` | `stale` | **attempt-fatal**: "the newest confirming week 2024-W36 ended more than 42 days before 2024-10-28" (`/hero/pushdown/gate/reasons/0`) |
| `/codes_miss/main/channels/R_mf/attributed/0` | `product:SD-12:malfunction_unspecified`, rank 1, `2024-W31` | R (model-free) flagged the hero's product under the generic code in the hero's first week; the world without the hero did not |
| `/codes_miss/main/channels/S/attributed/0` | `lot:L20045:malfunction_unspecified`, rank 1, `2024-W34` | S flagged the lot two weeks after X: attributed, but not no later than X |
| `/codes_miss/holds_main`, `/codes_miss/holds` | `false`, `false` | **this run does not illustrate the case codes miss** |
| `/codes_miss/robust_holding`, `/codes_miss/grid_holding` | 4 of 8, 1 of 6 | the same counts as attempt 1 |

Four of the run's twelve checks fail, all downstream of the gate: `gate_supported`, and the follow-up checks
(`packets_from_contributing`, `executed_once`, `ledger_chain_ok`), because only a supported conclusion can propose a
follow-up.

## What it shows

- **Moving the case to a high-volume lot did not hide it from R.** R flagged the hero's product instead of its lot,
  still in the first week. Across the 28 worlds both attempts logged (main, eight seeds and six grid cells each), R or
  S flags one of the hero's structured keys within about two weeks of X in most of them, and about half the seeds hold
  in either attempt.
- **The gate goes stale on the hero's own records.** The pre-registered hero runs weeks 31 to 36 of a 40-week world,
  and the check is asked as of 2024-10-28, more than 42 days after week 36 ended. Attempt 1's gate passed only because
  background records at the confirming plants also matched its lot and failure mode in later weeks; this lot's did
  not. That is a flaw of the cast's start week, which PREREG section 6 lists among the adjustable fields.

Both an attempt-fatal check and `holds` fail, so the pre-registration allows the third and last attempt.
