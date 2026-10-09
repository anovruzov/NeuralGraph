#!/bin/sh
# Launch / stop llama-server (CPU, OpenAI-compatible API) under /root/mycelic-live.
#   serve.sh 4b            # Qwen3-4B-Instruct-2507 Q4_K_M on 127.0.0.1:8082   (THREADS=2 NP=4)
#   serve.sh 1.7b          # Qwen3-1.7B Q4_K_M on 127.0.0.1:8081               (THREADS=2 NP=4)
#   serve.sh stop [4b|1.7b]
# Env overrides: THREADS NP CTX_PER_SLOT PORT
set -eu
ROOT=/root/mycelic-live
ROLE=${1:-4b}
if [ "$ROLE" = stop ]; then
  for r in ${2:-4b 1.7b}; do
    if [ -f "$ROOT/logs/server-$r.pid" ]; then kill "$(cat "$ROOT/logs/server-$r.pid")" 2>/dev/null || true; rm -f "$ROOT/logs/server-$r.pid"; echo "stopped $r"; fi
  done
  exit 0
fi
case "$ROLE" in
  4b)   MODEL=$ROOT/models/qwen3-4b-instruct-2507-q4_k_m.gguf; PORT=${PORT:-8082}; EXTRA="" ;;
  1.7b) MODEL=$ROOT/models/qwen3-1.7b-q4_k_m.gguf;             PORT=${PORT:-8081}
        # the 1.7B has a thinking mode: disable it server-wide (the product cannot send chat_template_kwargs per request)
        EXTRA='--chat-template-kwargs {"enable_thinking":false}' ;;
  *) echo "usage: $0 4b|1.7b|stop" >&2; exit 2 ;;
esac
THREADS=${THREADS:-2}; NP=${NP:-4}; CTX=$(( ${CTX_PER_SLOT:-8192} * NP ))
# shellcheck disable=SC2086
nohup nice -n 5 "$ROOT/build/bin/llama-server" -m "$MODEL" --host 127.0.0.1 --port "$PORT" \
  -t "$THREADS" -tb "$THREADS" -np "$NP" -c "$CTX" -cb -fa -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 \
  --jinja --no-webui --metrics $EXTRA > "$ROOT/logs/server-$ROLE.log" 2>&1 &
echo $! > "$ROOT/logs/server-$ROLE.pid"
echo "llama-server $ROLE pid $(cat "$ROOT/logs/server-$ROLE.pid") http://127.0.0.1:$PORT np=$NP ctx=$CTX threads=$THREADS log $ROOT/logs/server-$ROLE.log"
i=0
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 300 ] && { echo "not healthy; see log" >&2; exit 1; }
  sleep 1
done
echo ready
