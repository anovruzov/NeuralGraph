"""Reference agent process: keep notes locally, share the ones worth propagating, watch for conclusions.

    python -m mycelic.sdk.agent --url http://127.0.0.1:8080 --api-key mk_... --local-db /tmp/agent-7.db \\
        --observations observations.jsonl --watch "delivery risk sd-9" --watch-scope northwind

``observations.jsonl`` holds one JSON object per line: ``{"text": ..., "topic"?, "slot"?, "entity"?,
"confidence"?, "share": true|false, "delay"?: seconds}``.  Notes with ``share: false`` stay in the local store only
(the demo uses them to show what never left the agent).  The process exits when the file is consumed unless
``--stay`` keeps it alive polling the watched query, printing every new conclusion it sees.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import LocalMemory, MycelicClient, MycelicError


def _log(agent: str, msg: str) -> None:
    print(f"[{agent}] {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("MYCELIC_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--api-key", default=os.environ.get("MYCELIC_API_KEY"))
    ap.add_argument("--ca-file", default=os.environ.get("MYCELIC_CA_FILE"))
    ap.add_argument("--local-db", required=True, help="sqlite file for this agent's private notes")
    ap.add_argument("--observations", type=Path, help="JSONL file of observations to make")
    ap.add_argument("--watch", help="query to poll for conclusions")
    ap.add_argument("--watch-scope", default=None)
    ap.add_argument("--watch-min-layer", default="team")
    ap.add_argument("--stay", action="store_true", help="keep polling after the observations are consumed")
    ap.add_argument("--poll-seconds", type=float, default=2.0)
    ap.add_argument("--state-file", type=Path, help="write a JSON summary (counts, shared ids) here on exit")
    args = ap.parse_args(argv)
    if not args.api_key:
        print("an API key is required (--api-key or MYCELIC_API_KEY)", file=sys.stderr)
        return 2

    client = MycelicClient(args.url, args.api_key, ca_file=args.ca_file)
    local = LocalMemory(args.local_db)
    me = client.whoami()
    agent_id = me["id"]
    _log(agent_id, f"connected to {args.url} as {me['path']} (local notes: {local.counts()['local']})")

    # 1. resume: anything noted but never acknowledged by the server is re-sent (idempotent by local id)
    for n in local.all(shared=False):
        if n["tags"] and "private" in n["tags"]:
            continue
        try:
            res = local.share(client, n["local_id"])
            _log(agent_id, f"re-sent unacknowledged note {n['local_id']} -> {res['memory_id']}")
        except MycelicError as exc:
            _log(agent_id, f"could not re-send {n['local_id']}: {exc}")

    # 2. observe
    if args.observations:
        for line in args.observations.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obs = json.loads(line)
            if obs.get("delay"):
                time.sleep(float(obs["delay"]))
            share = bool(obs.get("share", True))
            local_id = local.note(obs["text"], topic=obs.get("topic"), slot=obs.get("slot"), entity=obs.get("entity"),
                                  kind=obs.get("kind", "observation"), confidence=float(obs.get("confidence", 0.8)),
                                  tags=[] if share else ["private"], local_id=obs.get("local_id"))
            if not share:
                _log(agent_id, f"noted locally only: {obs['text'][:70]}")
                continue
            try:
                res = local.share(client, local_id, visibility=obs.get("visibility", "team"))
                _log(agent_id, f"shared {local_id} -> {res['memory_id']} ({'new' if res.get('created', True) else 'already known'})")
            except MycelicError as exc:
                _log(agent_id, f"share failed for {local_id}: {exc} (kept locally, will retry on next start)")

    # 3. watch for conclusions
    seen: set[str] = set()
    deadline = None if args.stay else time.monotonic() + 0.0
    while args.watch:
        try:
            res = client.query(args.watch, scope=args.watch_scope, min_layer=args.watch_min_layer, k=3, include_lineage=False)
            ans = res.get("answer")
            if ans and ans["memory_id"] not in seen:
                seen.add(ans["memory_id"])
                _log(agent_id, f"conclusion at {ans['layer']} '{ans['scope']}' (support {ans['support']} agents / "
                               f"{ans['independent_teams']} teams, confidence {ans['confidence']}): {ans['text'][:160]}")
        except MycelicError as exc:
            _log(agent_id, f"query failed: {exc}")
        if deadline is not None and time.monotonic() >= deadline:
            break
        time.sleep(args.poll_seconds)

    summary = {"agent_id": agent_id, "path": me["path"], "counts": local.counts(),
               "shared": [{"local_id": n["local_id"], "memory_id": n["shared_memory_id"]} for n in local.all(shared=True)],
               "local_only": [n["text"] for n in local.all(shared=False)], "conclusions_seen": sorted(seen)}
    if args.state_file:
        args.state_file.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    _log(agent_id, f"done: {summary['counts']}")
    local.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
