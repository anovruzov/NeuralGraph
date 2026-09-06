#!/usr/bin/env bash
set -euo pipefail
python3 src/continual_discovery_sim.py --config configs/fast.json --out results
python3 src/analyze_results.py --input results/raw_runs.csv --out results --figures figures
python3 -m unittest discover -s tests -v
printf '\nDone. Read results/summary.md and inspect figures/.\n'
