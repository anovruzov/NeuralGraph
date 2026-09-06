#!/usr/bin/env bash
# Protocol-required sensitivity sweep: one factor at a time over the five
# parameters named in docs/EXPERIMENT_PROTOCOL.md. Takes a couple of minutes.
set -euo pipefail
python3 src/sensitivity_sweep.py --config configs/fast.json --out results --figures figures
python3 -m unittest discover -s tests -v
printf '\nSweep complete. Read results/sensitivity_summary.md, including the negative results.\n'
