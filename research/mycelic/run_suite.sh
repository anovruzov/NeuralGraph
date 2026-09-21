#!/bin/sh
# Remaining experiment suite, run in sequence. Each appends to its own JSONL,
# so an interrupted suite still leaves usable rows.
cd /home/user/NeuralGraph
for e in e2 e3 e3b e3c e4 e4b e6 e5 e7 e8; do
  echo "=== $e start $(date -Is) ==="
  timeout 5400 python3 -m research.mycelic.experiments $e
  echo "=== $e end $(date -Is) ==="
done
