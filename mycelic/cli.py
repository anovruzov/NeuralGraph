"""Command line for Mycelic.

    python -m mycelic serve                       # API + publisher + consumer (configuration from MYCELIC_* env vars)
    python -m mycelic mcp --url URL --api-key KEY # MCP over stdio, proxied to a running server (Claude Desktop / Code)
    python -m mycelic register-agent --enterprise northwind --team logistics --agent-id agent-7
    python -m mycelic agents | rules | status | replay | query "delivery risk"

Admin commands are HTTP clients of a running server: MYCELIC_URL (default http://127.0.0.1:8080) and
MYCELIC_ADMIN_TOKEN.  ``query`` uses MYCELIC_API_KEY (an agent key) or the admin token.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from .config import ConfigError, Settings


def _admin_client(args: argparse.Namespace):
    from .sdk import MycelicClient

    token = args.admin_token or os.environ.get("MYCELIC_ADMIN_TOKEN")
    if not token:
        raise SystemExit("MYCELIC_ADMIN_TOKEN (or --admin-token) is required")
    return MycelicClient(args.url, token, ca_file=args.ca_file)


async def cmd_serve(args: argparse.Namespace) -> int:
    from .api import run_server
    from .service import MycelicService

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.db:
        settings.db_path = args.db
    if args.nats_url is not None:
        settings.nats_url = args.nats_url or None
    if args.rules_file:
        settings.rules_file = args.rules_file
    try:
        settings.validate()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    service = MycelicService(settings)
    runner = await run_server(service)          # bind first so /health answers while the broker comes up
    await service.start()
    print(json.dumps({"mycelic": "started", "url": f"http{'s' if settings.tls_enabled else ''}://{settings.host}:{settings.port}",
                      "db": settings.db_path, "transport": service.transport.name, "nats_url": settings.nats_url}), flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    await stop.wait()
    print("shutting down", flush=True)
    await runner.cleanup()
    await service.close()
    return 0


async def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import serve_stdio_proxy

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    key = args.api_key or os.environ.get("MYCELIC_API_KEY")
    if not key:
        print("MYCELIC_API_KEY (or --api-key) is required", file=sys.stderr)
        return 2
    await serve_stdio_proxy(args.url, key, ca_file=args.ca_file)
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    client = _admin_client(args)
    res = client.register_agent(agent_id=args.agent_id, enterprise=args.enterprise, region=args.region, subsidiary=args.subsidiary,
                                department=args.department, team=args.team, display_name=args.display_name,
                                scopes=args.scopes.split(",") if args.scopes else None)
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        a = res["agent"]
        print(f"registered {a['agent_id']} at {a['path']}")
        print(f"MYCELIC_API_KEY={res['api_key']}")
        print("(the key is shown once; store it in the agent's secret store)")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    for a in _admin_client(args).list_agents(args.org):
        print(f"{a['agent_id']:<24} {a['status']:<8} {a['path']}  last seen {a.get('last_seen_at') or '-'}")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    client = _admin_client(args)
    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        for rule in data.get("rules", data) if isinstance(data, dict) else data:
            print(f"upserted {client.upsert_rule(rule)['rule']['rule_id']}")
    for r in client.list_rules():
        print(f"{r['rule_id']:<32} -> {r['target_layer']:<12} slots={','.join(r['required_slots'])} enabled={r['enabled']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(_admin_client(args).status(), indent=1, default=str))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    print(json.dumps(_admin_client(args).replay()))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from .sdk import MycelicClient

    key = args.api_key or os.environ.get("MYCELIC_API_KEY") or os.environ.get("MYCELIC_ADMIN_TOKEN")
    if not key:
        raise SystemExit("MYCELIC_API_KEY or MYCELIC_ADMIN_TOKEN is required")
    res = MycelicClient(args.url, key, ca_file=args.ca_file).query(args.text, scope=args.scope, min_layer=args.min_layer,
                                                                    k=args.k, include_lineage=args.lineage)
    if args.json:
        print(json.dumps(res, indent=1))
        return 0
    ans = res.get("answer")
    if not ans:
        print("(nothing relevant visible to this principal)")
        return 1
    print(f"[{ans['layer']} {ans['scope']}] confidence {ans['confidence']} support {ans['support']} agents / {ans['independent_teams']} teams")
    print(ans["text"])
    lin = ans.get("lineage") or {}
    print(f"lineage: {lin.get('contributing_agents')} agents, {lin.get('contributing_teams')} teams, layers {lin.get('layers')}, "
          f"reconstructable={lin.get('reconstructable')}  (memory {ans['memory_id']})")
    for h in res["results"][1:]:
        m = h["memory"]
        print(f"  - [{m['layer']}] {m['text'][:120]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the service")
    s.add_argument("--host"); s.add_argument("--port", type=int); s.add_argument("--db")
    s.add_argument("--nats-url", default=None, help="'' disables NATS (in-process transport; development only)")
    s.add_argument("--rules-file")
    s.set_defaults(fn=cmd_serve, is_async=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--url", default=os.environ.get("MYCELIC_URL", "http://127.0.0.1:8080"))
        sp.add_argument("--admin-token", default=None)
        sp.add_argument("--ca-file", default=os.environ.get("MYCELIC_CA_FILE"))
        sp.add_argument("--json", action="store_true")

    s = sub.add_parser("mcp", help="MCP over stdio, proxied to a running server"); common(s)
    s.add_argument("--api-key", default=None); s.set_defaults(fn=cmd_mcp, is_async=True)

    s = sub.add_parser("register-agent", help="register an agent and print its key once"); common(s)
    s.add_argument("--agent-id", required=True); s.add_argument("--enterprise", required=True)
    s.add_argument("--region"); s.add_argument("--subsidiary"); s.add_argument("--department"); s.add_argument("--team")
    s.add_argument("--display-name"); s.add_argument("--scopes", help="comma-separated (default: read/write/events/lineage)")
    s.set_defaults(fn=cmd_register, is_async=False)

    s = sub.add_parser("agents", help="list agents"); common(s); s.add_argument("--org"); s.set_defaults(fn=cmd_agents, is_async=False)
    s = sub.add_parser("rules", help="list rules, optionally loading a JSON file first"); common(s)
    s.add_argument("--file"); s.set_defaults(fn=cmd_rules, is_async=False)
    s = sub.add_parser("status", help="full service status (admin)"); common(s); s.set_defaults(fn=cmd_status, is_async=False)
    s = sub.add_parser("replay", help="re-deliver the whole event log to the service (admin)"); common(s)
    s.set_defaults(fn=cmd_replay, is_async=False)
    s = sub.add_parser("query", help="ask the organization"); common(s)
    s.add_argument("text"); s.add_argument("--api-key"); s.add_argument("--scope"); s.add_argument("--min-layer", default="agent")
    s.add_argument("-k", type=int, default=5); s.add_argument("--lineage", action="store_true")
    s.set_defaults(fn=cmd_query, is_async=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "is_async", False):
            return asyncio.run(args.fn(args))
        return args.fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
