#!/bin/sh
# Launch llama-server for the live Mycelic agents (CPU, OpenAI-compatible API).
#
#   research/mycelic/live/scripts/serve.sh edge     # Qwen3-1.7B, user agents
#   research/mycelic/live/scripts/serve.sh kernel   # Qwen3-4B-Instruct-2507, A2 reader
#   research/mycelic/live/scripts/serve.sh stop [edge|kernel]
#
# Environment (defaults in brackets):
#   LLAMA_BIN    directory with llama-server        [$LIVE_ROOT/runtime/llama.cpp/build/bin]
#   MODELS       directory with the GGUF files      [$LIVE_ROOT/models]
#   LIVE_ROOT    scratch root holding runtime/ and models/
#   THREADS      generation threads                 [4]
#   THREADS_B    prompt-processing threads          [=THREADS]
#   NP           parallel slots (-np)               [edge 4, kernel 2]
#   CTX          total KV context, split evenly across the slots
#                                                   [edge 8192*NP, kernel 8192*NP]
#   KV_TYPE      KV cache type (K and V)            [q8_0]
#   KVU          1 = one unified KV cache shared by all slots [0]
#   FA           flash attention on|off|auto        [on]
#   PORT         [edge 8081, kernel 8082]
#   LOGDIR       server logs and pid files          [$LIVE_ROOT/logs]
#
# Qwen3 thinking is disabled per request through the chat template
# (chat_template_kwargs {"enable_thinking": false}; --jinja is the default).
# Per-slot KV streams (no --kv-unified): with a unified cache every decode
# token attends over all slots' cells (masked), which made batched decode
# ~3x slower on this CPU.  8192 cells per slot fit the longest agent
# (~150 notes); q8_0 K/V (with flash attention) halves the KV memory.
# Per-slot prompt caching keeps the shared system prompt resident, so each
# call only prefills its own notes.
set -eu
ROLE=${1:-edge}
LIVE_ROOT=${LIVE_ROOT:-/tmp/claude-0/-home-user-NeuralGraph/94c168c2-a4c1-5555-b6a4-9314f0780495/scratchpad/live}
LLAMA_BIN=${LLAMA_BIN:-$LIVE_ROOT/runtime/llama.cpp/build/bin}
MODELS=${MODELS:-$LIVE_ROOT/models}
LOGDIR=${LOGDIR:-$LIVE_ROOT/logs}
mkdir -p "$LOGDIR"

if [ "$ROLE" = stop ]; then
  for r in ${2:-edge kernel}; do
    if [ -f "$LOGDIR/$r.pid" ]; then kill "$(cat "$LOGDIR/$r.pid")" 2>/dev/null || true; rm -f "$LOGDIR/$r.pid"; echo "stopped $r"; fi
  done
  exit 0
fi

case "$ROLE" in
  edge)   MODEL=${MODEL:-$MODELS/qwen3-1.7b-q4_k_m.gguf};             NP=${NP:-8}; PORT=${PORT:-8081} ;;
  kernel) MODEL=${MODEL:-$MODELS/qwen3-4b-instruct-2507-q4_k_m.gguf}; NP=${NP:-4}; PORT=${PORT:-8082} ;;
  *) echo "usage: $0 edge|kernel|stop" >&2; exit 2 ;;
esac
THREADS=${THREADS:-4}
THREADS_B=${THREADS_B:-$THREADS}
CTX=${CTX:-$((8192 * NP))}
KV_TYPE=${KV_TYPE:-q8_0}
if [ "${KVU:-0}" = 1 ]; then KVU_FLAG=--kv-unified; else KVU_FLAG=--no-kv-unified; fi

"$LLAMA_BIN/llama-server" -m "$MODEL" --host 127.0.0.1 --port "$PORT" \
  -t "$THREADS" -tb "$THREADS_B" -np "$NP" -c "$CTX" $KVU_FLAG -cb \
  -fa "${FA:-on}" -ctk "$KV_TYPE" -ctv "$KV_TYPE" -b 2048 -ub 512 --jinja --no-webui --metrics \
  > "$LOGDIR/$ROLE.log" 2>&1 &
echo $! > "$LOGDIR/$ROLE.pid"
echo "llama-server $ROLE pid $(cat "$LOGDIR/$ROLE.pid") on http://127.0.0.1:$PORT (np=$NP ctx=$CTX kv=$KV_TYPE $KVU_FLAG threads=$THREADS) log $LOGDIR/$ROLE.log"
i=0
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 300 ] && { echo "server did not become healthy; see $LOGDIR/$ROLE.log" >&2; exit 1; }
  sleep 1
done
echo "ready"
