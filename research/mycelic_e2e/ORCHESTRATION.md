# Overnight repair run, 2026-10-09: charter and custody

Run window: started 00:22 CDT (05:22 UTC) on 2026-10-09; cutoff **08:00 America/Chicago, 2026-10-09** (13:00 UTC).
The clock was read with `date` at the start (Fri Oct 9 00:22:16 CDT 2026), before the deadline, so the stated cutoff applies.

## Which code is the system under test

| candidate | what it is | verdict |
|---|---|---|
| `main` (`e5014c2`) `mycelic/` | a deployable organizational-memory service (aggregation rules, JetStream, lineage DAG) | not the active implementation named in the brief: no holders, no inquiry loop, no commit gate |
| `claude/mycelic-implementation-vr034p` (`f96f263`, 2026-10-09 00:06 UTC) | the Mycelic application: per-holder NeuralGraph evidence stores, ingestion connectors, per-holder domain sharding, coordinator with authz, inquiry, discovery loop, commit gate, NATS/SQLite transport | **the active implementation; this run is based on it** |
| `claude/vnext-accuracy-70-percent-gsjg8m` (`4fc504a`) `research/mycelic/` | the enterprise-hierarchy benchmark: a numpy simulator, plus a live harness where Qwen3-1.7B agents emit claims into the simulator pipeline | the benchmark under suspicion; audited, not extended |
| `claude/mycelic-production-deployment-r93cez` (`0fd9ac2`) | public-data replays, lab requests, market and outreach work | separate thread; not touched |

All four live in the public repository `anovruzov/NeuralGraph`. The private repositories on the account
(`Mycelic1.0`, `Mycelic_1.0`, `Mycelium`, `Argus`, ...) were last pushed in 2025 or hold unrelated work, so
none of them is the active implementation; none was read or copied. An "EMERGENCE" contract is not present in
any branch of this repository (searched case-insensitively); a session titled "Mycelic launch and EMERGENCE
benchmark" worked in the private `Argus` repository and was not opened, so nothing from it is used or exposed.

Working copy: a fresh clone of `claude/mycelic-implementation-vr034p` at `/home/user/ng-impl`, on a new local
branch `claude/friendly-mayer-y9f1vt` (the designated branch). The original checkout at `/home/user/NeuralGraph`
(main-based) was left untouched: resetting it or adding a worktree was refused by the session's safety
classifier, so a separate clone was used instead.

## Compute

The user asked for the MacBook Air to run the work. A session was created in the MacBook bridge environment
(`env_01RLtkk7mBVQQWBadHesS5Qn`, session `session_01QfrwkNkUbtAYq6LJRiJrdf`) with a read-only probe; it stayed at
"Allocating sandbox" with `connection_status: disconnected`, and an earlier session on the same bridge recorded
`computer_unreachable` at 04:21 UTC. Until the bridge connects, runs execute in this cloud container
(4 vCPU, 15 GB RAM) and every result says so.

## Team (models as requested by the Agent tool; each agent reports the model id it sees)

| role | requested model | notes |
|---|---|---|
| Global orchestrator | Fable preferred | this session; it runs as `claude-opus-5-5` (the main session's model is fixed at start and cannot be switched by the session) |
| Planner | `fable` | dependency map, hypotheses, task decomposition, next experiment |
| Engineers (up to 3) | `sonnet` | disjoint owned files |
| Reviewers (up to 2) | `opus` | reproduce, critique with evidence, never approve their own work |

Cycle: planner hypothesis → engineer patch → reviewer reproduction → orchestrator accept/reject → next task.

## Benchmark jobs found at the start (custody)

| job | where | state at 05:3x UTC | action |
|---|---|---|---|
| vNext live run "seed 705 world at 350/429 agents" (Qwen3-1.7B, llama.cpp CPU) | cloud session `session_01Y7LJXgohkFqAs2gDVWXfZP`, branch `claude/vnext-accuracy-70-percent-gsjg8m` | session idle and disconnected since 04:45 UTC; its container cannot be inspected from here | not stopped (unreachable); its results are classified below as simulator-path |
| lab runs `main-001`, `reader-001` | requested on branch `claude/mycelic-production-deployment-r93cez` (lab requests) | "in progress" per that session's goal record; where they execute was not determined | not touched |
| this container | `ps aux` at start | no benchmark processes | — |
