#!/usr/bin/env bash
# Tier-2 sweep (10,000 workers, 100,000 interactions, 30 rounds) of the families that carry the headline
# claims: headline (executive table), aggregation (all systems + compression ladder), poisoning
# (malicious-fraction and detector sweeps) and scaling (100 / 1,000 / 10,000 workers).
#
#   scripts/run_medium.sh                  # 5 seeds, 2 parallel seed processes (each holds one tier-2 world)
#   SEEDS=10 JOBS=3 scripts/run_medium.sh  # DESIGN.md §11 asks for >= 10 tier-2 seeds where runtime allows
#
# Memory: one tier-2 world plus one hierarchy run needs a few GB; keep JOBS x that below your RAM.
# Processed CSVs are append-only; reset with:  rm -rf results/raw results/processed
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SEEDS="${SEEDS:-5}"
JOBS="${JOBS:-2}"
TIER="${TIER:-tier2}"
MALICIOUS="${MALICIOUS:-0.10}"
T0=$SECONDS
COMMON=(--tier "$TIER" --seeds "$SEEDS" --results "$RESULTS" --jobs "$JOBS")

echo "=== run_medium: tier=$TIER seeds=$SEEDS jobs=$JOBS results=$RESULTS quick=${QUICK:-0}"
want headline    && run_family headline    experiments/headline/run.py    "${COMMON[@]}" --malicious "$MALICIOUS"
want aggregation && run_family aggregation experiments/aggregation/run.py "${COMMON[@]}" --only both
want poisoning   && run_family poisoning   experiments/poisoning/run.py   "${COMMON[@]}" --only both
want scaling     && run_family scaling     experiments/scaling/run.py     "${COMMON[@]}" --sizes 100,1000,10000
finish run_medium "$T0"
