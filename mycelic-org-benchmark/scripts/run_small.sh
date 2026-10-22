#!/usr/bin/env bash
# Tier-1 sweep of EVERY experiment family (laptop scale: 1,000 workers, 10k interactions, 30 rounds).
#
#   scripts/run_small.sh                       # 5 seeds, 3 parallel seed processes
#   SEEDS=30 JOBS=4 scripts/run_small.sh       # the full Tier-1 seed count of DESIGN.md §11
#   QUICK=1 SEEDS=1 scripts/run_small.sh       # smoke test of the whole pipeline (reduced grids)
#   FAMILIES="failures headline" scripts/run_small.sh
#
# Output: results/raw/<family>/<run_id>/{manifest.json,metrics.json,claims.jsonl} and one row per run
# appended to results/processed/<family>.csv (aggregation also writes compression.csv, poisoning also
# writes edge_security.csv, independent_support also writes independent_support_clusters.csv).
# Processed CSVs are append-only; reset with:  rm -rf results/raw results/processed
# Idempotent: re-running adds a new batch of rows, never deletes anything (see scripts/_common.sh).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SEEDS="${SEEDS:-5}"
JOBS="${JOBS:-3}"
TIER="${TIER:-tier1}"
T0=$SECONDS
COMMON=(--tier "$TIER" --seeds "$SEEDS" --results "$RESULTS")

echo "=== run_small: tier=$TIER seeds=$SEEDS jobs=$JOBS results=$RESULTS quick=${QUICK:-0}"
want aggregation        && run_family aggregation        experiments/aggregation/run.py         "${COMMON[@]}" --jobs "$JOBS" --only both
want poisoning          && run_family poisoning          experiments/poisoning/run.py           "${COMMON[@]}" --jobs "$JOBS" --only both
want privacy            && run_family privacy            experiments/privacy/run.py             "${COMMON[@]}" --jobs "$JOBS"
want contradictions     && run_family contradictions     experiments/contradictions/run.py      "${COMMON[@]}" --jobs "$JOBS"
want temporal           && run_family temporal           experiments/temporal/run.py            "${COMMON[@]}" --jobs "$JOBS"
want failures           && run_family failures           experiments/failures/run.py            "${COMMON[@]}" --jobs "$JOBS"
want hierarchy          && run_family hierarchy          experiments/hierarchy/run.py           "${COMMON[@]}" --jobs "$JOBS"
want scaling            && run_family scaling            experiments/scaling/run.py             "${COMMON[@]}" --jobs "$JOBS" --sizes 100,1000
want models             && run_family models             experiments/models/run.py              "${COMMON[@]}" --jobs "$JOBS"
want questioning        && run_family questioning        experiments/questioning/run.py         "${COMMON[@]}" --jobs "$JOBS"
want independent_support && run_family independent_support experiments/independent_support/run.py "${COMMON[@]}" --jobs "$JOBS"
want routing            && run_family routing            experiments/routing/run.py             "${COMMON[@]}"        # own CLI: no --jobs
want headline           && run_family headline           experiments/headline/run.py            "${COMMON[@]}" --jobs "$JOBS"
finish run_small "$T0"
