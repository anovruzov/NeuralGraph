# Environment that points the Mycelic model router at the local llama-server (Qwen3-4B-Instruct-2507, Q4_K_M).
# Usage:  set -a; . research/mycelic_e2e/live/live_env.sh; set +a
export OPENAI_BASE_URL=http://127.0.0.1:8082/v1          # must end in /v1: the provider POSTs {base}/chat/completions
export OPENAI_API_KEY=local                              # any non-empty string; llama-server runs without --api-key
export MYCELIC_MODEL_LIGHT=openai:qwen3-4b-instruct-2507
export MYCELIC_MODEL_STANDARD=openai:qwen3-4b-instruct-2507
export MYCELIC_MODEL_HEAVY=openai:qwen3-4b-instruct-2507
export MYCELIC_MODEL_MAX_PARALLEL=4                      # = llama-server -np 4
export MYCELIC_MODEL_TIMEOUT_SECONDS=900                 # ~4 tok/s decode on 2 shared threads: the default 120 s is too short for 1200-token tasks
export MYCELIC_EMBED_PROVIDER=hash                       # deterministic embedder; the server runs without --embedding
