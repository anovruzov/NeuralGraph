# Recovered roll-up diagnostics, 2026-10-16

**Synthetic, development seed zero, measurement: false. No model pilot ran.**
Code: `4eb11a9930f43b233487e2420093043b2b7cbb1a`. The build was reconstructed because the previous
container's unpushed branches were unavailable. The plan amendment remains the separate earlier commit
`ff4dd84`. No cap, seed, evidence window, ranking, generator or scorer setting was changed after seeing results.
The accuracy loop and slowdown work were not reconstructed or modified.

## Recorded attempts

| Run directory under `runs/` | Outcome |
|---|---|
| `recovery-gossip-001` | Stopped before transport at the small-pack entity whitelist on `bd6872b`; retained as a failure. |
| `recovery-gossip-002` | Completed the seeded transport diagnostic on `4eb11a9`; one required crash scenario was not realized. |
| `recovery-fixture-10000` | Completed all twenty-six weeks with the deterministic fixture. |
| `recovery-fixture-200` | Completed all twenty-six weeks with the deterministic fixture. |
| `recovery-perfect-200` | Completed the perfect-marking fixture ablation. |

Each directory has its `report.json`. Each fixture directory also has `checkpoints.tar.xz` containing
`summaries.json` and twenty-six week records. `manifest.json` pins every extracted file and the archive with
SHA256. All archives were decompressed and checked against those digests. Every summary passed the closed
privacy schema; every recorded candidate list, state hash, role prompt character count, reply schema and
lineage flag was checked against its checkpoint. Scores were rebuilt from those records and the unchanged
generator/scorer functions, and matched the reports. These verification replays did not make new decisions.

## Gossip diagnostic

Source: `runs/recovery-gossip-002/report.json`.

- Actual world: 9,999 users, fifteen sites, 329,730 records, 599 entities, forty discoverable patterns.
- At W26, each of fifteen nodes held all 390 emitted summaries, with no pending gaps and identical candidate
  lists. There were zero recorded forks. Duplicate deliveries ignored: 23,467.
- Tampered authentication and a claimed-other-origin message were refused. Replay was recognized as a duplicate.
- Recovery reached component convergence after twelve rounds at W09 and twelve rounds at W15.
- **The pre-full-spread crash case is still open.** At the W05 crash, the lost origin's W04 summary was already
  present at all fifteen nodes (`crash_w04_replicas: 15`). Therefore this is not a complete pass of A8's crash
  requirement. No delay, seed or crashing-node choice was tuned to change that result.

Transport is an in-memory discrete-round simulation with shared-secret HMAC authentication, not the shipped
fabric and not public-key signatures. A replay refusal count is not conflated with the two forged-message
refusals. The node-loss, partition, delay, loss and duplicate schedule is recorded in the build source and report.

## Fixture diagnostics

Sources: each named fixture's `report.json`, under `scores` and `prompt_side_only_characters`.

| Run | Found | True patterns debated in their window | Every-facet decoys accepted in window | Prompt characters |
|---|---:|---:|---:|---:|
| Target 10,000 users, calibrated markings | 0 / 40 | 1 / 40 | 0 / 50 | 20,895,240 |
| Target 200 users, calibrated markings | 10 / 40 | 15 / 40 | 9 / 50 | 3,265,914 |
| Target 200 users, perfect markings | 8 / 40 | 17 / 40 | 8 / 50 | 3,483,538 |

The target-two-hundred world has 309 actual users, as the unchanged generator and earlier calibration do.
The larger world's rare found count is zero of seventeen. In its miss decomposition, thirty-seven patterns
were outside the cap throughout their scoring windows, two never qualified, and one was a hypothesis with a
contradiction reason. These counts describe this fixture's selection and ruling history only.

The debated-in-window count is **not a proven maximum across possible model ruling histories**. Supporting a
story removes it from future debates; hypotheses and rejections change future competition for the cap. The
fixture's deliberately conservative treatment of any negation also affects its rulings. No model capability,
improvement to the shipped system, or held-out accuracy is measured by these numbers.

The target-two-hundred baseline, recorded in the root-cause report, found thirteen of forty with seventeen of
fifty decoys by facet zero and twenty-eight of fifty by every facet in its window. Each new report provides both
matching decoy readings. The systems have different edge inputs and decision rules; this is an architectural
diagnostic, not a controlled measurement of a model's contribution. There is no recorded comparator for the
target-ten-thousand world, and none is asserted.

Prompt characters are exact for fixture-generated arguments. Dividing by four gives a prompt-side token estimate,
not account usage, a measured model token count, or the cost of the planned model run. Actual arguments would
change later prompts. The perfect-marking ablation is not a parameter choice for a subsequent run.

## Gates and remaining work

- Cap stays at eight. No result-based increase has been made.
- No independent build review has completed in this recovered session. These diagnostics do not replace it.
- No h-sim advocate, skeptic, rebuttal or judge has been called. The fixture's numerical gate result cannot
  authorize step (d); its report carries `eligible_backend: false`.
- A valid stand-in pilot needs the amendment's independently auditable role transcripts. A supplied audit JSON
  does not itself prove the role was isolated. This session does not expose that transcript evidence.
- The remaining weekly allowance is also not exposed. The exchange pauses after W02 without capacity evidence;
  no twenty-six-week model run or larger model run has been started.
- A dated follow-up protocol is needed for a transport test that actually exercises loss before full spread.
  The current result is retained and the existing schedule has not been silently altered.

Checks: twenty-seven focused tests pass locally; four existing naming guards pass; thirteen new text sources
were scanned directly with the existing naming matcher with no hits. GitHub focused workflow `38975684129`
on `4eb11a9` completed successfully. At this record's writing, full workflows `38975683535` and `38975683536`
are still in progress; no full-suite green is claimed.
