"""The arena console: a live command center over the swarm and the EMERGENCE harness.

Every event the console shows is read from something the runtime wrote -- a stamp file, a wire frame, a
retrieval row, a report, a commit.  Nothing is generated.  When a source has nothing, the console says so.

    python -m research.arena --swarm <dir> [--run <dir>] [--repo <dir>] --port 8765

Modules: ``events`` (typed events, append-only store, replay), ``sources`` (the tails that turn runtime
artifacts into events), ``concurrency`` (intervals, overlap, the parallelism proof), ``server`` (SSE,
state, artifacts, controls).
"""
