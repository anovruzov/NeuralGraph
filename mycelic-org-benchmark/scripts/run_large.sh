#!/usr/bin/env bash
# Tier-3 runs (50,000-100,000 workers; single machine with numpy): the scaling curve at 50k and 100k
# workers and the headline table at tier-3 size, 2 seeds each (DESIGN.md §14: Tier 3 uses 3-5 seeds
# at most and says so; the figures print the seed count).
#
#   scripts/run_large.sh              # 2 seeds, sequential (one tier-3 world at a time)
#   SEEDS=3 scripts/run_large.sh
#
# Expect tens of GB of RAM at 100k workers and hours of wall time; JOBS defaults to 1 for that reason.
# Processed CSVs are append-only; reset with:  rm -rf results/raw results/processed
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SEEDS="${SEEDS:-2}"
JOBS="${JOBS:-1}"
TIER="${TIER:-tier3}"
SIZES="${SIZES:-50000,100000}"
T0=$SECONDS
COMMON=(--tier "$TIER" --seeds "$SEEDS" --results "$RESULTS" --jobs "$JOBS")

echo "=== run_large: tier=$TIER seeds=$SEEDS jobs=$JOBS sizes=$SIZES results=$RESULTS quick=${QUICK:-0}"
want scaling  && run_family scaling  experiments/scaling/run.py  "${COMMON[@]}" --sizes "$SIZES"
want headline && run_family headline experiments/headline/run.py "${COMMON[@]}"
finish run_large "$T0"
