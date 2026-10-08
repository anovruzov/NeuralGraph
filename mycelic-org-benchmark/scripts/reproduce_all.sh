#!/usr/bin/env bash
# Reproduce every number, figure and table of the benchmark from scratch:
#   small (tier 1, every family) -> medium (tier 2, headline families) -> figures -> tables.
#
#   scripts/reproduce_all.sh                 # 5 seeds per tier, results in ./results
#   SEEDS=30 scripts/reproduce_all.sh        # full Tier-1 seed count (the medium stage also uses SEEDS)
#   LARGE=1 scripts/reproduce_all.sh         # additionally run scripts/run_large.sh (tier 3) before the figures
#   SKIP_MEDIUM=1 scripts/reproduce_all.sh   # tier 1 only (laptop)
#   QUICK=1 SEEDS=1 scripts/reproduce_all.sh # end-to-end smoke test in minutes
#
# IMPORTANT - processed CSVs are APPEND-ONLY.  Every runner invocation appends rows (with a fresh
# batch_id) to results/processed/<family>.csv and adds run directories under results/raw/<family>/;
# nothing is ever deleted, failed runs included (DESIGN.md §1.5).  Figures and tables aggregate every
# row present, so a clean reproduction must start from an empty results tree:
#
#     rm -rf results/raw results/processed results/figures results/tables results/logs
#     scripts/reproduce_all.sh
#
# Every run's provenance (git commit, resolved config, model profiles, backend, seed, hardware, runtime,
# peak RSS, dataset hash, status) is in results/raw/<family>/<run_id>/manifest.json.  All results carry
# backend: simulated-slm unless configs/models.yaml was pointed at a measured profile / real endpoint
# (see README.md, "Plugging in a real model").
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
RESULTS="${RESULTS:-results}"
export RESULTS
T0=$SECONDS
status=0

stage() {   # stage <name> <command...>: run, time, remember failure, continue
  local name="$1"; shift
  local t0=$SECONDS
  echo "##### stage $name: $*  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  if "$@"; then
    echo "##### stage $name finished in $((SECONDS - t0))s"
  else
    echo "##### stage $name FAILED (exit $?) after $((SECONDS - t0))s"
    status=1
  fi
}

echo "##### reproduce_all: results=$RESULTS seeds=${SEEDS:-default} quick=${QUICK:-0} large=${LARGE:-0}"
if [[ -e "$RESULTS/processed" ]] && compgen -G "$RESULTS/processed/*.csv" > /dev/null; then
  echo "##### note: $RESULTS/processed already holds CSVs; new rows will be APPENDED (rm -rf $RESULTS/raw $RESULTS/processed to reset)"
fi

stage small "$here/run_small.sh"
if [[ "${SKIP_MEDIUM:-0}" != "1" ]]; then
  stage medium "$here/run_medium.sh"
fi
if [[ "${LARGE:-0}" == "1" ]]; then
  stage large "$here/run_large.sh"
fi
stage figures "$PY" experiments/figures.py --results "$RESULTS"
stage tables "$PY" experiments/tables.py --results "$RESULTS"

echo "##### reproduce_all: total wall time $((SECONDS - T0))s ($(( (SECONDS - T0) / 60 )) min)"
echo "##### figures: $RESULTS/figures/   tables: $RESULTS/tables/   processed CSVs: $RESULTS/processed/"
if (( status != 0 )); then echo "##### reproduce_all: at least one stage failed (see above and $RESULTS/logs/)"; fi
exit "$status"
