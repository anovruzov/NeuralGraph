"""``python -m mycelic <command>``: serve | worker | holder | migrate | seed | scenario | backup | restore | health.

Every command builds its settings from the environment (``mycelic.config``), installs the shared logging
(``mycelic.observability.configure_logging``) and exits non-zero on failure so containers and cron notice. The
long-running commands (``serve``, ``worker``, ``holder``) stop cleanly on SIGTERM: the runtime is stopped,
leases are released and holders send an offline heartbeat before the process ends.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings, load_settings

logger = logging.getLogger("mycelic.cli")


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _settings(service: str) -> Settings:
    s = load_settings()
    s.service_name = service
    from .observability import configure_logging
    configure_logging(s)
    return s


async def _wait_for_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    await stop.wait()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    s = _settings("api")
    if args.host:
        s.host = args.host
    if args.port:
        s.port = int(args.port)
    from .api.app import run_server
    from .runtime import build_runtime
    rt = build_runtime(s)
    run_worker = s.run_worker_in_api and not args.no_worker
    run_holders = s.embedded_holders and not args.no_holders
    try:
        asyncio.run(run_server(rt, s, run_worker=run_worker, run_holders=run_holders))
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    s = _settings("worker")
    from .runtime import build_runtime, build_worker

    async def main() -> None:
        rt = build_runtime(s, with_holders=False)
        if rt.transport is not None:
            await rt.transport.start()
        worker = build_worker(rt, worker_id=args.worker_id or None)
        rt.worker = worker
        await worker.start()
        logger.info("worker %s running; waiting for SIGTERM", worker.worker_id)
        try:
            await _wait_for_signal()
        finally:
            await rt.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_holder(args: argparse.Namespace) -> int:
    _settings("holder")
    try:
        from .holder import process
    except ImportError as exc:
        print(f"the holder process module is not available: {exc}", file=sys.stderr)
        return 2
    argv: list[str] = []
    for flag, value in (("--holder-id", args.holder_id), ("--key", args.key), ("--core-url", args.core_url), ("--data-dir", args.data_dir),
                        ("--local-host", args.local_host), ("--heartbeat-seconds", args.heartbeat_seconds)):
        if value not in (None, ""):
            argv += [flag, str(value)]
    if args.local_port:
        argv += ["--local-port", str(args.local_port)]
    main = getattr(process, "main", None)
    if main is None:
        print("the holder process module has no main(); run it with `python -m mycelic.holder.process`", file=sys.stderr)
        return 2
    return int(main(argv) or 0)


def cmd_migrate(args: argparse.Namespace) -> int:
    s = _settings("migrate")
    from .db.coord import CoordDB
    from .db.migrate import apply_migrations, migration_status
    db = CoordDB(s.coord_db, migrate=False)
    try:
        if args.status:
            _print({"coord_db": s.coord_db, **migration_status(db.conn)})
            return 0
        before = migration_status(db.conn)
        applied = apply_migrations(db.conn)
        after = migration_status(db.conn)
        _print({"coord_db": s.coord_db, "applied_now": applied, "current": after["current"], "latest": after["latest"], "pending_before": len(before["pending"])})
        return 0
    finally:
        db.conn.close()


async def _with_runtime(s: Settings, fn: Any, *, run_holders: bool = True) -> Any:
    from .runtime import build_runtime
    rt = build_runtime(s)
    await rt.start(run_worker=False, run_holders=run_holders)
    try:
        return await fn(rt)
    finally:
        await rt.stop()


def cmd_seed(args: argparse.Namespace) -> int:
    s = _settings("seed")
    try:
        from .seed import run_seed
    except ImportError as exc:
        print(f"the seed module is not available in this build ({exc}); nothing was seeded", file=sys.stderr)
        return 2

    async def go(rt: Any) -> Any:
        params = inspect.signature(run_seed).parameters
        kw: dict[str, Any] = {"reset": bool(args.reset)}
        if "tenant" in params:
            kw["tenant"] = args.tenant
        elif "slug" in params:
            kw["slug"] = args.tenant
        return await _maybe_await(run_seed(rt, **kw))

    out = asyncio.run(_with_runtime(s, go))
    _print(out if out is not None else {"seeded": True, "tenant": args.tenant})
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    """Run the verification scenario. It always builds its own runtime in a temporary data directory (or --data-dir), never
    in the configured one, so it can be run next to a live deployment without touching its data."""
    try:
        from .seed.scenario import main as scenario_main
    except ImportError as exc:
        print(f"the scenario module is not available in this build ({exc})", file=sys.stderr)
        return 2
    argv: list[str] = []
    if args.data_dir:
        argv += ["--data-dir", args.data_dir]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.keep:
        argv.append("--keep")
    if args.json:
        argv += ["--json", args.json]
    if args.verbose:
        argv.append("--verbose")
    return int(scenario_main(argv) or 0)


def cmd_backup(args: argparse.Namespace) -> int:
    s = _settings("backup")
    from .observability import BackupError, backup_bundle
    try:
        manifest = backup_bundle(s, args.out, include_coord=not args.holders_only, include_holders=not args.coord_only)
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    _print({"bundle": manifest.get("bundle"), "files": [{k: f.get(k) for k in ("path", "kind", "holder_id", "bytes", "sha256")} for f in manifest.get("files", [])],
            "schema": manifest.get("schema"), "created_at": manifest.get("created_at")})
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    s = _settings("restore")
    from .observability import BackupError, restore_bundle
    try:
        out = restore_bundle(s, getattr(args, "from_path"), force=bool(args.force), holders=not args.coord_only)
    except BackupError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    _print({"restored": out.get("restored"), "moved_aside": out.get("moved_aside"), "schema": out.get("schema"), "created_at": out.get("created_at"),
            "mycelic_version": out.get("mycelic_version")})
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    s = _settings("health")
    base = (args.url or s.public_url or "").rstrip("/")
    if not base:
        host = "127.0.0.1" if s.host in ("0.0.0.0", "", "::") else s.host
        base = f"http://{host}:{s.port}"
    url = f"{base}/readyz"
    try:
        with urllib.request.urlopen(url, timeout=float(args.timeout)) as resp:  # noqa: S310 (operator-supplied URL)
            status, body = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        _print({"ok": False, "url": url, "error": str(exc)})
        return 1
    try:
        data = json.loads(body)
    except ValueError:
        data = {"raw": body[:500]}
    _print({"url": url, "status": status, **(data if isinstance(data, dict) else {"body": data})})
    return 0 if status == 200 and (not isinstance(data, dict) or data.get("ok", True)) else 1


# ---------------------------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic", description="Mycelic: local-first distributed organizational intelligence.")
    p.add_argument("--version", action="version", version=f"mycelic {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("serve", help="HTTP API + SSE + static UI (worker and embedded holders in-process by default)")
    sp.add_argument("--host", default=None, help="bind address (MYCELIC_HOST)")
    sp.add_argument("--port", type=int, default=None, help="listen port (MYCELIC_PORT)")
    sp.add_argument("--no-worker", action="store_true", help="do not run the discovery worker in this process")
    sp.add_argument("--no-holders", action="store_true", help="do not run embedded holders in this process")
    sp.set_defaults(fn=cmd_serve)

    wp = sub.add_parser("worker", help="discovery worker only (shares coord.db with the API)")
    wp.add_argument("--worker-id", default=None)
    wp.set_defaults(fn=cmd_worker)

    hp = sub.add_parser("holder", help="a standalone evidence holder process")
    hp.add_argument("--holder-id", default=None, help="holder id (MYCELIC_HOLDER_ID)")
    hp.add_argument("--key", default=None, help="holder key (MYCELIC_HOLDER_KEY)")
    hp.add_argument("--core-url", default=None, help="core API base URL (MYCELIC_CORE_URL)")
    hp.add_argument("--data-dir", default=None, help="where <holder_id>/evidence.db lives (default <MYCELIC_DATA_DIR>/holders)")
    hp.add_argument("--local-port", type=int, default=None, help="optional local owner API port")
    hp.add_argument("--local-host", default=None)
    hp.add_argument("--heartbeat-seconds", type=float, default=None)
    hp.set_defaults(fn=cmd_holder)

    mp = sub.add_parser("migrate", help="apply pending schema migrations (or show status)")
    mp.add_argument("--status", action="store_true", help="show applied/pending migrations without applying")
    mp.set_defaults(fn=cmd_migrate)

    sd = sub.add_parser("seed", help="create the demonstration organization")
    sd.add_argument("--tenant", default="demo", help="tenant slug (default demo)")
    sd.add_argument("--reset", action="store_true", help="remove the demo tenant's data first")
    sd.set_defaults(fn=cmd_seed)

    sc = sub.add_parser("scenario", help="run the verification scenario end to end and print a report")
    sc.add_argument("--data-dir", default=None, help="run inside this directory instead of a temporary one (kept afterwards)")
    sc.add_argument("--timeout", type=float, default=None, help="overall time budget in seconds (default 120)")
    sc.add_argument("--keep", action="store_true", help="keep the temporary data directory for inspection")
    sc.add_argument("--json", default=None, help="also write the report as JSON to this path")
    sc.add_argument("--verbose", action="store_true", help="INFO logs on stderr")
    sc.set_defaults(fn=cmd_scenario)

    bp = sub.add_parser("backup", help="consistent online backup of coord.db and every holder store")
    bp.add_argument("--out", required=True, help="bundle path (.tar.gz)")
    bp.add_argument("--holders-only", action="store_true", help="skip coord.db (holder machines)")
    bp.add_argument("--coord-only", action="store_true", help="skip holder stores")
    bp.set_defaults(fn=cmd_backup)

    rp = sub.add_parser("restore", help="restore a bundle into the data directory")
    rp.add_argument("--from", dest="from_path", required=True, help="bundle path")
    rp.add_argument("--force", action="store_true", help="overwrite existing databases (kept as *.pre-restore.*)")
    rp.add_argument("--coord-only", action="store_true", help="restore coord.db only")
    rp.set_defaults(fn=cmd_restore)

    he = sub.add_parser("health", help="GET /readyz of the configured server; exit 0 when ready")
    he.add_argument("--url", default=None, help="base URL (default MYCELIC_PUBLIC_URL or http://host:port)")
    he.add_argument("--timeout", type=float, default=5.0)
    he.set_defaults(fn=cmd_health)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
