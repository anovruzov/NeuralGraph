#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI is required on this Mac." >&2
  exit 1
fi
REPO="${1:-}"
if [[ -z "$REPO" ]]; then
  printf 'Local MycelicLabs repository path: '
  IFS= read -r REPO
fi
if [[ ! -d "$REPO/.git" ]]; then
  echo "Expected a local git repository at: $REPO" >&2
  exit 1
fi
cd "$REPO"
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
ASSET_MANIFEST="$(find "$HERE/reference" -maxdepth 1 -name '*.jpeg' -type f -print | sort | sed 's/^/- /')"
PREAMBLE="ASSET_MANIFEST (absolute paths; use the image-reading capability on each):\n$ASSET_MANIFEST\nCURRENT_REPOSITORY: $(pwd)\nReview permissions and costs before expanding beyond normal code/test actions."
exec claude "$(cat "$HERE/OMEGA_HIVEGENESIS.spec")"$'\n\n'"$PREAMBLE"
