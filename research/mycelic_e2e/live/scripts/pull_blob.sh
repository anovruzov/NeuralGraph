#!/bin/sh
# usage: pull_blob.sh <manifest.json> <out.gguf>   (Docker Hub registry HTTP API, repository ai/qwen3)
set -eu
MAN=$1; OUT=$2
DIGEST=$(jq -r '.layers[0].digest' "$MAN"); SIZE=$(jq -r '.layers[0].size' "$MAN")
for attempt in 1 2 3 4 5 6 7 8; do
  TOKEN=$(curl -sS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:ai/qwen3:pull" | jq -r .token)
  curl -sS -L -C - -H "Authorization: Bearer $TOKEN" -o "$OUT.part" "https://registry-1.docker.io/v2/ai/qwen3/blobs/$DIGEST" || true
  if [ "$(stat -c %s "$OUT.part" 2>/dev/null || echo 0)" = "$SIZE" ]; then break; fi
  sleep 5
done
mv "$OUT.part" "$OUT"
echo "$(sha256sum "$OUT" | cut -d' ' -f1) expected ${DIGEST#sha256:}"
