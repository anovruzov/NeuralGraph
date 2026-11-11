# Shared helpers for scripts/run_*.sh (sourced, not executed).
#
# Environment knobs honoured by every script:
#   RESULTS=results        results root (raw/, processed/, figures/, tables/, logs/)
#   PYTHON=python3         interpreter
#   EXTRA_ARGS=""          appended to every runner call, e.g. EXTRA_ARGS="--set org.n_rounds=10 --tag smoke"
#   FAMILIES="a b c"       restrict to these families (default: every family of the script)
#   QUICK=1                pass --quick to every runner (reduced grids; a smoke test of the whole pipeline)
#   STOP_ON_FAIL=1         abort at the first family whose runner exits non-zero (default: continue,
#                          report the failed families at the end and exit 1)
#
# Processed CSVs (results/processed/<family>.csv) are APPEND-ONLY: every invocation adds rows with a
# fresh batch_id, and results/raw/<family>/<run_id>/ keeps every run (failed ones included, DESIGN.md §1.5).
# To start from scratch:   rm -rf results/raw results/processed results/figures results/tables

bench_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$bench_root"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
RESULTS="${RESULTS:-results}"
LOG_DIR="$RESULTS/logs"
mkdir -p "$LOG_DIR"
STOP_ON_FAIL="${STOP_ON_FAIL:-0}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
QUICK_ARGS=()
if [[ "${QUICK:-0}" == "1" ]]; then QUICK_ARGS=(--quick); fi
FAILED_FAMILIES=()

want() {   # want <family>: true when FAMILIES is unset or lists the family
  local fams="${FAMILIES:-}"
  [[ -z "$fams" || " $fams " == *" $1 "* ]]
}

run_family() {   # run_family <label> <runner.py> [args...]
  local label="$1"; shift
  local runner="$1"; shift
  local t0=$SECONDS
  echo "=== [$label] $PY $runner $* ${EXTRA_ARGS}  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  # shellcheck disable=SC2086  # EXTRA_ARGS is meant to be word-split
  if "$PY" "$runner" "$@" ${QUICK_ARGS[@]+"${QUICK_ARGS[@]}"} ${EXTRA_ARGS} 2>&1 | tee -a "$LOG_DIR/$label.log"; then
    echo "=== [$label] ok in $((SECONDS - t0))s"
  else
    echo "=== [$label] FAILED after $((SECONDS - t0))s (log: $LOG_DIR/$label.log)"
    FAILED_FAMILIES+=("$label")
    if [[ "$STOP_ON_FAIL" == "1" ]]; then exit 1; fi
  fi
}

finish() {   # finish <script name> <start seconds>
  local name="$1" t0="$2"
  echo "=== [$name] total wall time: $((SECONDS - t0))s"
  if (( ${#FAILED_FAMILIES[@]} > 0 )); then
    echo "=== [$name] FAILED families: ${FAILED_FAMILIES[*]}"
    return 1
  fi
  echo "=== [$name] every family finished; processed CSVs in $RESULTS/processed/"
  return 0
}
