#!/bin/sh
# Remaining experiment suite, run in sequence. Each appends to its own JSONL,
# so an interrupted suite still leaves usable rows.
cd /home/user/NeuralGraph
for e in e1b e2 e3b e9 e3 e3c e6 e5 e4 e4b e7 e8 e11 e1c e10; do
  echo "=== $e start $(date -Is) ==="
  timeout 5400 python3 -m research.mycelic.experiments $e
  echo "=== $e end $(date -Is) ==="
done
echo "=== suite complete $(date -Is) ==="
