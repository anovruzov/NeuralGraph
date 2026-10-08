#!/bin/bash
# Live Mycelic on a Mac (Apple Silicon, Metal): install llama.cpp, fetch the two
# models from Hugging Face, start one llama-server per role, print the run commands.
#
#   research/mycelic/live/mac_setup.sh            # both servers (edge :8081, kernel :8082)
#   research/mycelic/live/mac_setup.sh edge       # only the edge server
#   research/mycelic/live/mac_setup.sh kernel     # only the kernel server
#   research/mycelic/live/mac_setup.sh stop       # stop both
#
# Environment (defaults in brackets):
#   LIVE_HOME            working dir for logs, pids and the Python venv  [$HOME/mycelic-live]
#   EDGE_HF / KERNEL_HF  Hugging Face "<repo>:<quant>" to serve [auto: first candidate below
#                        whose repo lists a Q4_K_M .gguf]
#   EDGE_PORT [8081]  KERNEL_PORT [8082]  EDGE_NP [8]  KERNEL_NP [4]
#   EDGE_CTX_PER_SLOT [8192]  KERNEL_CTX_PER_SLOT [4096]
#
# The Hugging Face GGUF files are not byte-identical to the Docker Hub
# (ai/qwen3) files the Linux-container results used; every result row records
# the served file's sha256 (run_env.edge.model_sha256), so the two are never
# mixed up, and the replay cache is keyed on it.
set -euo pipefail
ROLE=${1:-both}
LIVE_HOME=${LIVE_HOME:-$HOME/mycelic-live}
LOGDIR=$LIVE_HOME/logs
mkdir -p "$LOGDIR"
EDGE_PORT=${EDGE_PORT:-8081}; KERNEL_PORT=${KERNEL_PORT:-8082}
EDGE_NP=${EDGE_NP:-8}; KERNEL_NP=${KERNEL_NP:-4}
EDGE_CTX_PER_SLOT=${EDGE_CTX_PER_SLOT:-8192}; KERNEL_CTX_PER_SLOT=${KERNEL_CTX_PER_SLOT:-4096}
# Candidate repos, in order.  Qwen/Qwen3-1.7B-GGUF is Qwen's own GGUF repo.  For
# Qwen3-4B-Instruct-2507 an official Qwen GGUF repo could not be confirmed when
# this was written (Hugging Face was unreachable from the build container), so
# the script checks each candidate through the Hugging Face API and takes the
# first that lists a Q4_K_M file.
EDGE_CANDIDATES="Qwen/Qwen3-1.7B-GGUF ggml-org/Qwen3-1.7B-GGUF unsloth/Qwen3-1.7B-GGUF bartowski/Qwen_Qwen3-1.7B-GGUF"
KERNEL_CANDIDATES="Qwen/Qwen3-4B-Instruct-2507-GGUF unsloth/Qwen3-4B-Instruct-2507-GGUF lmstudio-community/Qwen3-4B-Instruct-2507-GGUF bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF"

stop_role() {
  local r=$1
  if [ -f "$LOGDIR/$r.pid" ]; then kill "$(cat "$LOGDIR/$r.pid")" 2>/dev/null || true; rm -f "$LOGDIR/$r.pid"; echo "stopped $r"; fi
}
if [ "$ROLE" = stop ]; then stop_role edge; stop_role kernel; exit 0; fi

# ---- 1. prerequisites -------------------------------------------------------
if [ "$(uname -s)" != Darwin ]; then echo "this script is for macOS; on Linux use live/scripts/serve.sh" >&2; exit 1; fi
[ "$(uname -m)" = arm64 ] || echo "warning: not Apple Silicon; Metal offload may be unavailable" >&2
if ! command -v llama-server >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || { echo "install Homebrew first: https://brew.sh" >&2; exit 1; }
  brew install llama.cpp
fi
echo "llama.cpp: $(llama-server --version 2>&1 | grep -m1 -i version || true)"
# Python 3.10+ with numpy, in a venv (Homebrew Python refuses global pip installs)
PY=${PYTHON:-python3}
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  brew install python@3.12; PY=$(brew --prefix)/bin/python3.12
fi
if [ ! -x "$LIVE_HOME/venv/bin/python" ]; then "$PY" -m venv "$LIVE_HOME/venv"; fi
"$LIVE_HOME/venv/bin/python" -c 'import numpy' 2>/dev/null || "$LIVE_HOME/venv/bin/pip" install -q numpy

# ---- 2. resolve the Hugging Face repos ----------------------------------------
resolve() {  # $1 = space-separated candidate repos -> prints "<repo>:Q4_K_M"
  local r
  for r in $1; do
    if curl -sf -m 20 "https://huggingface.co/api/models/$r" \
        | "$LIVE_HOME/venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any("q4_k_m" in s["rfilename"].lower() and s["rfilename"].endswith(".gguf") for s in d.get("siblings", [])) else 1)' 2>/dev/null; then
      echo "$r:Q4_K_M"; return 0
    fi
  done
  return 1
}
[ "${EDGE_HF:-auto}" = auto ] && EDGE_HF=$(resolve "$EDGE_CANDIDATES" || { echo "no edge repo found; set EDGE_HF" >&2; exit 1; })
[ "${KERNEL_HF:-auto}" = auto ] && KERNEL_HF=$(resolve "$KERNEL_CANDIDATES" || { echo "no kernel repo found; set KERNEL_HF" >&2; exit 1; })

# ---- 3. servers (Metal: -ngl 99; per-slot KV, q8_0, flash attention) -------
start_role() {  # role hf port np ctx_per_slot
  local role=$1 hf=$2 port=$3 np=$4 cps=$5
  stop_role "$role"
  echo "starting $role: $hf on :$port (np=$np, ctx/slot=$cps); first start downloads the model"
  nohup llama-server -hf "$hf" --host 127.0.0.1 --port "$port" -ngl 99 \
    -np "$np" -c $((np * cps)) --no-kv-unified -fa on -ctk q8_0 -ctv q8_0 \
    -cb --jinja --metrics --cache-ram 1024 > "$LOGDIR/$role.log" 2>&1 &
  echo $! > "$LOGDIR/$role.pid"
  local i=0
  until curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -gt 1800 ] && { echo "$role server not healthy; see $LOGDIR/$role.log" >&2; exit 1; }
    sleep 1
  done
  local mp
  mp=$(curl -s "http://127.0.0.1:$port/props" | "$LIVE_HOME/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("model_path",""))')
  echo "$role ready on http://127.0.0.1:$port  model file: $mp"
}
case "$ROLE" in
  edge)   start_role edge "$EDGE_HF" "$EDGE_PORT" "$EDGE_NP" "$EDGE_CTX_PER_SLOT" ;;
  kernel) start_role kernel "$KERNEL_HF" "$KERNEL_PORT" "$KERNEL_NP" "$KERNEL_CTX_PER_SLOT" ;;
  both)   start_role edge "$EDGE_HF" "$EDGE_PORT" "$EDGE_NP" "$EDGE_CTX_PER_SLOT"
          start_role kernel "$KERNEL_HF" "$KERNEL_PORT" "$KERNEL_NP" "$KERNEL_CTX_PER_SLOT" ;;
  *) echo "usage: $0 [both|edge|kernel|stop]" >&2; exit 2 ;;
esac

cat <<EOF

Servers are up. In the repository root (plugged-in power; the Air is fanless and
throttles under sustained load, so expect the self-check's projection to drift):

  export MYCELIC_EDGE_URL=http://127.0.0.1:$EDGE_PORT
  export MYCELIC_KERNEL_URL=http://127.0.0.1:$KERNEL_PORT
  PY=$LIVE_HOME/venv/bin/python
  caffeinate -i \$PY -m research.mycelic.live.run --stage smoke
  caffeinate -i \$PY -m research.mycelic.live.run --stage 400
  caffeinate -i \$PY -m research.mycelic.live.run --stage 2000
  caffeinate -i \$PY -m research.mycelic.live.run --stage 10000
  \$PY -m research.mycelic.live.package_results

Each stage is resumable: rerun the same command after an interruption.
Stop the servers with: $0 stop
EOF
