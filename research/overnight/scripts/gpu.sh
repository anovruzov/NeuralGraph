#!/bin/zsh
# gpu.sh <job-name> <command...>
# Runs the command in the background under one of SLOTS GPU slots, logs to .context/overnight/logs/<job>.log,
# stamps start/end lines in LEDGER.md ("runlog" section) and writes .context/overnight/logs/<job>.done with the exit code.
# Usage from the repo root:  research/overnight/scripts/gpu.sh lme_base_ctx ./target/release/chat-memory-context lme ...
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
JOB="$1"; shift
SLOTS="${SLOTS:-2}"
LOCKDIR="$ROOT/.context/overnight/locks"
LOGDIR="$ROOT/.context/overnight/logs"
mkdir -p "$LOCKDIR" "$LOGDIR"
LOG="$LOGDIR/$JOB.log"
DONE="$LOGDIR/$JOB.done"
rm -f "$DONE"
(
  export PATH="$HOME/.cargo/bin:$PATH"
  export LLM_NO_THINK_PREFILL=1
  cd "$ROOT"
  slot=""
  while [ -z "$slot" ]; do
    for i in $(seq 1 "$SLOTS"); do
      if mkdir "$LOCKDIR/slot$i" 2>/dev/null; then slot="$i"; echo $(sh -c 'echo $PPID') > "$LOCKDIR/slot$i/pid"; break; fi
    done
    [ -z "$slot" ] && sleep 15
  done
  echo "[gpu.sh] $(date '+%F %T') start job=$JOB slot=$slot cmd: $*" | tee -a "$LOG"
  echo "- $(date '+%F %T') START $JOB (slot $slot): $*" >> "$ROOT/research/overnight/RUNLOG.md"
  t0=$(date +%s)
  "$@" >> "$LOG" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "[gpu.sh] $(date '+%F %T') end job=$JOB rc=$rc elapsed=$((t1-t0))s" | tee -a "$LOG"
  echo "- $(date '+%F %T') END   $JOB rc=$rc elapsed=$((t1-t0))s" >> "$ROOT/research/overnight/RUNLOG.md"
  echo "$rc" > "$DONE"
  rm -rf "$LOCKDIR/slot$slot"
) > /dev/null 2>&1 &
disown
echo "queued $JOB -> $LOG"
