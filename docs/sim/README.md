# Mycelic Forest Ops (simulation game)

`mycelic_forest_ops.html` is a single-file, playable simulation of the
coordination layer's central mechanism, built for sharing (it records well in
REC mode). Open it in any browser; no build step, no server.

What it models, and what it does not:

- Seven organisms hold three premises of one capability. No single organism
  holds the whole answer. Every cycle a bounded route (4 of 7) is queried and a
  rule-based synthesizer reconstructs the answer from exported claims.
- Memories carry a lineage root and a failure domain. Blight fails a root, and
  every memory grown from it withers together: replicas that share a root are
  not redundancy. Reseeding creates an independent root and raises the
  min failure-domain cut. Lineage-aware repair verifies up to two candidates
  outside the route, preferring support independent of compromised roots.
- Silent forgetting is shown the way the pinned synthesizer reports it: on a
  failed cycle the reported confidence is the max over surviving claims, so it
  stays high while valid support drops.
- Used knowledge strengthens; unsupported knowledge decays and withers.

It is a game, not the benchmark. Numbers on screen are illustrative and are
not comparable to `NeuralGraph/coordination/artifacts/`. The benchmark's
mechanism definitions live in `NeuralGraph/coordination/benchmark.py`.
