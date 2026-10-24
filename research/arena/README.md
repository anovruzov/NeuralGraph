# Arena console

A live command center over the builder swarm and the EMERGENCE harness. Every line it shows is read from
something the runtime wrote; nothing is generated. Where a source has nothing, the panel says
`OFFLINE`, `WAITING` or `NO DATA`.

## Run it

```bash
pip install -r requirements.txt            # aiohttp is the only runtime dependency beyond the stdlib
export MYCELIC_NATS_SERVER_BIN=/path/to/nats-server   # only needed for the Start control (real harness runs)

python -m research.arena \
    --swarm  $SCRATCH/swarm_out \              # per-agent stamp dirs: <id>/START, <id>/END, artifacts
    --run    $SCRATCH/runs/official-ce0e357/seed-0 \   # attach an existing EMERGENCE run (optional)
    --run-log $SCRATCH/runs/official-ce0e357/seed-0.log \
    --repo   . --branch HEAD \                 # COMMIT events
    --state  ./runs/arena \                    # the append-only event log + source read positions
    --runs-root $SCRATCH/runs/arena \          # where Start puts new runs
    --port 8765
# -> http://127.0.0.1:8765/
```

Tests: `pytest tests/arena -q` (every fixture is synthetic and named so).
Screenshot of the running page: `node research/arena/screenshot.js http://127.0.0.1:8765/ out.png 100`
(uses the pre-installed Playwright Chromium; waits for the page to report ready and for ≥100 real events).

## What is real, panel by panel

| panel | reads | tags |
|---|---|---|
| 01 Strategy deck | the swarm registry: `START`/`END` stamps written by each agent, `ROLES.json` written at spawn | — |
| 02 Arena grid, stats, timeline | the same stamps; ω(E) is the largest set of overlapping stamped intervals, by sweep | SPAWN · COMPLETE · ARTIFACT |
| 03 Adversarial stream | harness drills and checks from the run's stdout log and partial report | TEST · FAIL · VERIFY |
| 04 Judge | the run report's `checks` (assertions, failed list, ok) and `verdict` (`pass`, `checks_pass`, `reason`, `unevaluated_required`) | VERIFY · FAIL |
| 05 Orchestration trace | every event, virtualised; click → the event JSON and its artifact | all |
| 06 NeuralGraph view | `main/edge/site-<id>.graph.sqlite3` (nodes, edges, sample) and `fabric/mycelic.db` (`lineage_edges`) | EDGE · SYNC |
| 07 Controls | `POST /control` → a real child process group (`Popen`, `SIGSTOP`/`SIGCONT`/`SIGTERM`); `stop` refuses without `confirm` | CONTROL |

Harness events come from the files the harness already writes: `main/hq/wire.jsonl` (HQ↔site frames →
ASK/REPLY), `main/edge/site-*.tesseract.jsonl` (retrievals → ROUTE), `site-*.graph.jsonl` (SYNC),
`bridge/publishes.jsonl` (PUBLISH), `e2e-ingest-report.json` (PHASE, checks, verdict). Each event's `ref`
names the file and line it was read from; `GET /artifact?path=` serves it, allow-listed to the configured roots.

## Transport and replay

`GET /events` is Server-Sent Events. On connect it sends `event: snapshot` (the state), then every stored event
with `id > Last-Event-ID` (or `?after=N`), then new ones as they are appended, `event: state` every two
seconds, and `: keepalive` when idle. The browser's `EventSource` reconnects by itself and resends
`Last-Event-ID`, so a dropped client resumes without gaps or duplicates; the page drops any id it has seen.
Ids are monotonic and every event is on disk before anyone sees it (`<state>/events.jsonl`), so replay from
`#0` is the same operation as reconnecting. Sources persist their read positions (`<state>/sources.json`) so a
console restart does not re-emit history. `GET /export` / `GET /export.jsonl` give the log and state.

## What it measures, and what it cannot

Registered, queued, running, complete and failed are counted separately. ω(E) is computed from the agents'
own stamps, so it is a measurement of overlapping *agent intervals*. It is not a count of concurrent model
requests: the stamps bound the agent, not its calls, and that boundary is reported as `NO DATA · not
instrumented`. Queue latency needs an `ENQUEUED` stamp that wave 1 did not write; also `NO DATA`. Inside the
benchmark the model-call count is 0 by policy, and the console says so rather than drawing a number.

Layer B (independent product workers inside Mycelic) is not instantiated by this console. The audit of what
that would take is in the research swarm's `D10/LAYER_B_AUDIT.md`: today the service's structural maximum of
overlapping apply workers is 1.

## Design

Follows `demo/live/mycelic_live.py`'s convention: stdlib `ThreadingHTTPServer`, one thread per connection,
loopback only. `events.py` (typed events, append-only store, replay) · `sources.py` (the tails) ·
`concurrency.py` (intervals, overlap, stats) · `server.py` (SSE, state, graph, artifacts, controls) ·
`console.html` (one page, no build step, canvas for the timeline and the graph). The visual language is the
reference's — near-black, amber/red/green/cyan, monospace, inset section titles, 10×10 grid, timestamped
trace — without its theatre: there is no crawler, no probes-per-second, and no number that is not read from a
file.
