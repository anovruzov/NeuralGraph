#!/usr/bin/env bash
# Local start for the Mycelic deployment package.
#
#   ./up.sh            distributed topology: nats + api + worker + holder-a + holder-b   (docker-compose.yml)
#   ./up.sh local      single process: api with in-process worker and embedded holders    (docker-compose.local.yml)
#   ./up.sh [mode] -d  extra arguments are passed to `docker compose up`
#
# Creates .env from .env.example on first run and fills in the generated secrets (secret key, NATS passwords,
# demo holder keys) so the stack comes up without editing anything; model provider keys stay empty (the fake
# provider is used until you add one). Without Docker:  python -m mycelic serve   (docs/mycelic/SETUP.md).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import re, secrets, pathlib
p = pathlib.Path(".env")
s = p.read_text()
for name in ("MYCELIC_SECRET_KEY", "NATS_SYS_PASSWORD", "NATS_CORE_PASSWORD", "NATS_HOLDER_A_PASSWORD", "NATS_HOLDER_B_PASSWORD",
             "MYCELIC_HOLDER_KEY_A", "MYCELIC_HOLDER_KEY_B"):
    s = re.sub(rf"^{name}=$", f"{name}={secrets.token_urlsafe(32)}", s, flags=re.M)
p.write_text(s)
PY
    echo "created .env from .env.example with generated secrets (edit it to add model provider keys)"
  else
    echo "created .env from .env.example; python3 not found, so fill in MYCELIC_SECRET_KEY, NATS_*_PASSWORD and MYCELIC_HOLDER_KEY_A/B by hand" >&2
  fi
fi

mode="${1:-nats}"
[ $# -gt 0 ] && shift
case "$mode" in
  local) exec docker compose -f docker-compose.local.yml up --build "$@" ;;
  nats)  exec docker compose -f docker-compose.yml up --build "$@" ;;
  *) echo "usage: $0 [local|nats] [docker compose up arguments...]" >&2; exit 2 ;;
esac
