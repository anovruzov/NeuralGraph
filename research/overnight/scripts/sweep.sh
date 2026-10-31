#!/bin/zsh
# sweep.sh <sweep-name> <file with one s0.py argument line per row; blank lines and # comments ignored>
# Runs the rows sequentially (each is one S0 experiment), logging to .context/overnight/logs/sweep_<name>.log.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
NAME="$1"; LIST="$2"
LOG="$ROOT/.context/overnight/logs/sweep_$NAME.log"
export LLM_NO_THINK_PREFILL=1
(
  cd "$ROOT"
  echo "[sweep] $(date '+%F %T') start $NAME" >> "$LOG"
  grep -v '^\s*#' "$LIST" | grep -v '^\s*$' | while IFS= read -r line; do
    # LM Studio unloads JIT-loaded models after their TTL and cannot always reload under load (HTTP 400 "Model unloaded"):
    # make sure the judge and the answerer are resident before each run.
    lms ps 2>/dev/null | grep -q "google/gemma-4-e4b" || lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> "$LOG" 2>&1
    lms ps 2>/dev/null | grep -q "qwen3.8-9b-mlx" || lms load qwen3.8-9b-mlx -y --context-length 16384 --parallel 4 --identifier qwen3.8-9b-mlx >> "$LOG" 2>&1
    echo "[sweep] $(date '+%F %T') run: $line" >> "$LOG"
    eval python3 research/overnight/scripts/s0.py $line >> "$LOG" 2>&1
    echo "[sweep] $(date '+%F %T') rc=$?" >> "$LOG"
  done
  echo "[sweep] $(date '+%F %T') end $NAME" >> "$LOG"
  touch "$LOG.done"
) > /dev/null 2>&1 &
disown
echo "sweep $NAME started -> $LOG"
