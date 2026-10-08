#!/bin/sh
# Throughput benchmark for the live agents: llama-bench (raw prefill / decode
# per thread count) and an end-to-end measurement through llama-server on
# real agent prompts for several slot counts (-np).
#
#   research/mycelic/live/scripts/bench.sh edge "2 4" "1 4 8" 12
#     role  threads-list  np-list  agents-per-point
#
# Writes JSON lines to $LIVE_ROOT/bench_<role>.jsonl and llama-bench tables to
# $LIVE_ROOT/llama_bench_<role>.txt.  Uses seed 700 (benchmark seed).
# NOTE: on a shared machine, use no more threads than idle cores: llama.cpp's
# decode collapses when its threads are oversubscribed.
set -eu
ROLE=${1:-edge}
THREADS_LIST=${2:-"2 4"}
NP_LIST=${3:-"1 4 8"}
AGENTS=${4:-12}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
LIVE_ROOT=${LIVE_ROOT:-/tmp/claude-0/-home-user-NeuralGraph/94c168c2-a4c1-5555-b6a4-9314f0780495/scratchpad/live}
LLAMA_BIN=${LLAMA_BIN:-$LIVE_ROOT/runtime/llama.cpp/build/bin}
case "$ROLE" in
  edge)   MODEL=$LIVE_ROOT/models/qwen3-1.7b-q4_k_m.gguf;             PORT=8091 ;;
  kernel) MODEL=$LIVE_ROOT/models/qwen3-4b-instruct-2507-q4_k_m.gguf; PORT=8092 ;;
esac
OUT=$LIVE_ROOT/bench_$ROLE.jsonl

for T in $THREADS_LIST; do
  "$LLAMA_BIN/llama-bench" -m "$MODEL" -t "$T" -p 512 -n 64 -r 2 2>&1 | grep -E "^\|" >> "$LIVE_ROOT/llama_bench_$ROLE.txt" || true
done

for T in $THREADS_LIST; do
  for NP in $NP_LIST; do
    LOGDIR=$LIVE_ROOT/logs PORT=$PORT NP=$NP THREADS=$T MODEL=$MODEL \
      "$HERE/serve.sh" "$ROLE" >/dev/null
    (cd "$REPO" && python3 -m research.mycelic.live.bench --url "http://127.0.0.1:$PORT" \
       --concurrency "$NP" --agents "$AGENTS" --role "$ROLE" --tag "t$T-np$NP-kvu${KVU:-0}-${KV_TYPE:-q8_0}") | tee -a "$OUT"
    LOGDIR=$LIVE_ROOT/logs "$HERE/serve.sh" stop "$ROLE" >/dev/null
    sleep 2
  done
done
