# Assistant demos

Two runnable demos of the local memory assistant
([`NeuralGraph/chat_memory`](../NeuralGraph/chat_memory/README.md)). Both work with no
model installed — `--fake-llm` substitutes a deterministic stand-in, so the whole
extraction and retrieval pipeline runs offline.

**Narrated live demo** — starts the worker, dashboard, REST API and MCP server, then
streams five chats through them while you watch memory get built:

```bash
python demo/chat_memory_live_demo.py --fake-llm
# then open http://127.0.0.1:8765/
```

**Plain cross-chat demo** — feeds the same chats in and asks questions that span them:

```bash
python demo/chat_memory_demo.py --fake-llm
```

Drop `--fake-llm` once a local model server is running to see real extraction.

`data/sample_chats.jsonl` is five chats with one person over several months — the input
both demos replay. Research harnesses and datasets live in
[`research/`](../research/README.md), not here.

## Live demo console

`demo/live/mycelic_live.py` drives the real Mycelic stack (nats-server and the Mycelic service) through nine
acts and draws what happens as a mycelial network in the browser: the agents of Orrery Robotics register,
share some notes and keep others on their own disk, each region reaches a rule conclusion, the enterprise
combines them, a retraction and a recount change the outcome, and finally the service is killed, its database
deleted and everything rebuilt from the event log. Orrery Robotics is a fictional company and all its data is
synthetic. Nothing on screen is hard-coded: every number comes from the run (the service's admin API and the
engine's own actions), and every check shown was run against it. After the last act the engine stops the stack
and the console says so.

```bash
python demo/live/mycelic_live.py                     # start the stack, open http://127.0.0.1:8765/, advance each act from the console
python demo/live/mycelic_live.py --auto --dwell 8    # the acts advance by themselves, 8 s apart
python demo/live/mycelic_live.py --replay            # no stack needed: serve the recorded run in demo/live/trace.json
python demo/live/mycelic_live.py --export page.html  # a standalone replay page with the recorded run embedded
```

A live run needs `nats-server` (on `PATH` or in `MYCELIC_NATS_SERVER_BIN`); with Docker, `--driver compose` runs
the compose deployment instead. If the stack cannot start, the script says why and prints the `--replay`
command; if a live run fails midway, the console stays up with the error and the same command. Presenter notes
open at `?presenter`; `--record PATH` records a new trace headless. The script's docstring describes the trace
format and the HTTP endpoints.

## Collective layer demo (internal)

`demo/collective/` runs the pre-pilot collective layer end to end for a fictional multi-site company: synthetic
same-author data, internal use only, never a measurement. Its [`README.md`](collective/README.md) has the commands
and the recorded run.
