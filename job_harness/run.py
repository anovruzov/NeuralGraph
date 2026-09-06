#!/usr/bin/env python3
"""Job harness CLI.

    python run.py --dry-run            fill and validate, never click Submit
    python run.py --apply              submit real applications
    python run.py --status             print current campaign statistics
    python run.py --discover-only      run discovery and scoring, no browser
    python run.py --check              validate configuration and connectivity
    python run.py --init-profile       write a blank applicant profile
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):                     # allow `python job_harness/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_harness.config.logging_setup import get_logger, setup_logging
from job_harness.config.settings import Config
from job_harness.dashboard.server import DashboardServer
from job_harness.dashboard.stats import collect
from job_harness.database.db import Database
from job_harness.orchestrator import Orchestrator
from job_harness.profile.applicant import Applicant, ProfileError, write_example_profile
from job_harness.qwen.fake import fake_client
from job_harness.qwen.qwen_client import QwenClient

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py", description="Autonomous job search and application harness",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="do everything except click Submit (default)")
    mode.add_argument("--apply", action="store_true",
                      help="submit applications for real")
    mode.add_argument("--status", action="store_true", help="print statistics and exit")
    mode.add_argument("--check", action="store_true",
                      help="validate config, profile, browser and Qwen, then exit")
    mode.add_argument("--init-profile", action="store_true",
                      help="write a blank applicant profile and exit")

    p.add_argument("--config", help="path to a JSON config file")
    p.add_argument("--env-file", help="path to a .env file")
    p.add_argument("--profile", help="path to applicant.json")
    p.add_argument("--resume", help="path to the resume file")
    p.add_argument("--db", help="path to the SQLite database")

    p.add_argument("--max-applications", type=int, help="stop after this many applications")
    p.add_argument("--max-per-hour", type=int, help="applications per hour cap")
    p.add_argument("--min-score", type=int, help="minimum fit score to apply")
    p.add_argument("--freshness-days", type=int, help="only consider jobs posted within N days")
    p.add_argument("--remote-only", action="store_true", help="remote positions only")
    p.add_argument("--allow-senior", action="store_true",
                   help="do not reject staff/principal/manager titles")
    p.add_argument("--roles", help="comma-separated target roles (overrides config)")

    p.add_argument("--board", action="append", default=[], metavar="SOURCE:TOKEN",
                   help="discovery target, e.g. greenhouse:anthropic (repeatable)")
    p.add_argument("--url", action="append", default=[], metavar="URL",
                   help="seed job or board URL (repeatable)")
    p.add_argument("--discover-only", action="store_true",
                   help="discover and score, then exit without opening a browser")
    p.add_argument("--no-discovery", action="store_true",
                   help="skip discovery and work the existing queue")
    p.add_argument("--loop", action="store_true",
                   help="keep running after the queue empties (unattended mode)")
    p.add_argument("--retry-blocked", action="store_true",
                   help="requeue jobs blocked for fixable reasons (a missing "
                        "profile value, a failed navigation) before running")

    p.add_argument("--dashboard", dest="dashboard", action="store_true", default=None,
                   help="serve the control dashboard (default on)")
    p.add_argument("--no-dashboard", dest="dashboard", action="store_false",
                   help="do not serve the dashboard")
    p.add_argument("--dashboard-host", help="dashboard bind address")
    p.add_argument("--dashboard-port", type=int, help="dashboard port")

    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.add_argument("--fake-qwen", action="store_true",
                   help="use the built-in rule-based backend instead of a model server")
    p.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR")
    p.add_argument("--json", action="store_true", help="machine-readable output for --status")
    return p


def apply_overrides(config: Config, args: argparse.Namespace) -> None:
    if args.apply:
        config.run.mode = "apply"
    else:
        config.run.mode = "dry-run"
    for attr, target, name in [
        ("max_applications", config.run, "max_applications"),
        ("max_per_hour", config.run, "max_applications_per_hour"),
        ("min_score", config.run, "min_score"),
        ("freshness_days", config.discovery, "freshness_days"),
    ]:
        value = getattr(args, attr, None)
        if value is not None:
            setattr(target, name, value)
    if args.min_score is not None:
        # --min-score is the floor; keep the rubric threshold at least as high so
        # the two cannot contradict each other.
        config.scoring.apply_threshold = max(config.scoring.apply_threshold,
                                             args.min_score)
    if args.remote_only:
        config.discovery.remote_only = True
    if args.allow_senior:
        config.scoring.allow_senior_roles = True
    if args.roles:
        config.discovery.target_roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    if args.profile:
        config.run.profile_path = args.profile
    if args.resume:
        config.run.resume_path = args.resume
    if args.db:
        config.run.database_path = args.db
    if args.headful:
        config.browser.headless = False
    if args.log_level:
        config.run.log_level = args.log_level
    if args.loop:
        config.run.loop_forever = True
    if args.dashboard is not None:
        config.dashboard.enabled = args.dashboard
    if args.dashboard_host:
        config.dashboard.host = args.dashboard_host
    if args.dashboard_port:
        config.dashboard.port = args.dashboard_port

    for entry in args.board or []:
        if ":" not in entry:
            raise SystemExit(f"--board expects SOURCE:TOKEN, got {entry!r}")
        source, token = entry.split(":", 1)
        config.discovery.boards.setdefault(source.strip(), [])
        if token.strip() not in config.discovery.boards[source.strip()]:
            config.discovery.boards[source.strip()].append(token.strip())
    for url in args.url or []:
        if url not in config.discovery.seed_urls:
            config.discovery.seed_urls.append(url)

    config.validate()


def make_qwen(config: Config, db: Database, fake: bool) -> QwenClient:
    if fake:
        log.warning("using the built-in rule-based backend; this is not a model")
        return fake_client(config.qwen, db=db)
    return QwenClient(config.qwen, db=db)


def cmd_status(config: Config, args: argparse.Namespace) -> int:
    db = Database(config.run.database_path)
    data = collect(db)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    run, pipeline, rates, qwen = data["run"], data["pipeline"], data["rates"], data["qwen"]
    print(f"run           {run['run_id']}  [{run['state']}]  mode={run['mode']}")
    print(f"elapsed       {run['elapsed_s']}s   current: {run['current_state']} "
          f"{run['current_company'] or ''} {run['current_role'] or ''}".rstrip())
    print("-" * 62)
    for key in ("discovered", "scored", "queued", "skipped", "ready_to_submit",
                "submitted", "verified", "needs_review", "blocked", "failed",
                "duplicates"):
        print(f"{key:<18}{pipeline[key]}")
    print("-" * 62)
    print(f"{'apps/hour':<18}{rates['applications_per_hour']}")
    print(f"{'avg fit score':<18}{rates['average_fit_score']}")
    print(f"{'qwen requests':<18}{qwen['requests']} ({qwen['cache_hits']} cached)")
    print(f"{'qwen tokens':<18}{qwen['tokens']:,}")
    print(f"{'est. cost':<18}${qwen['estimated_cost_usd']:.4f}")
    print(f"{'cost/verified':<18}${rates['cost_per_verified_application']:.4f}")
    if data["blockers"]:
        print("-" * 62)
        for b in data["blockers"]:
            print(f"blocker {b['blocker_type']:<24}{b['n']}")
    if data.get("needs_review"):
        print("-" * 62)
        print(f"{len(data['needs_review'])} submission(s) need review "
              f"(clicked Submit, no confirmation found):")
        for row in data["needs_review"][:10]:
            print(f"  {row['company']} — {row['title']}")
            print(f"    {row['canonical_apply_url']}")
    db.close()
    return 0


def cmd_check(config: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"config          OK   mode={config.run.mode} "
          f"threshold={config.scoring.apply_threshold}")

    db = Database(config.run.database_path)
    version = db.query_one("SELECT MAX(version) AS v FROM schema_version")["v"]
    print(f"database        OK   {config.run.database_path} (schema v{version})")

    try:
        applicant = Applicant.load(config.run.profile_path, config.run.resume_path,
                                   strict=False)
        missing = applicant.missing_required()
        if missing:
            ok = False
            print(f"profile         FAIL missing required fields: {', '.join(missing)}")
        else:
            print(f"profile         OK   {applicant.full_name} "
                  f"<{applicant.get('email')}>")
        if applicant.resume_text.strip():
            print(f"resume          OK   {len(applicant.resume_text)} chars extracted "
                  f"from {config.run.resume_path}")
        else:
            print(f"resume          WARN no text extracted from {config.run.resume_path}; "
                  f"install poppler-utils or pypdf")
    except ProfileError as exc:
        ok = False
        print(f"profile         FAIL {exc}")

    from job_harness.browser.engine import resolve_chromium_path
    chromium = resolve_chromium_path(config.browser.executable_path)
    if chromium:
        print(f"browser         OK   {chromium}")
    else:
        ok = False
        print("browser         FAIL no Chromium found; run: python -m playwright install chromium")

    if args.fake_qwen:
        print("qwen            SKIP using the built-in rule-based backend")
    else:
        client = QwenClient(config.qwen, db=db)
        healthy, detail = client.health_check()
        if healthy:
            print(f"qwen            OK   {config.qwen.model} at {config.qwen.base_url}")
        else:
            ok = False
            print(f"qwen            FAIL {config.qwen.base_url}: {detail}")
        client.close()

    targets = []
    for source, tokens in (config.discovery.boards or {}).items():
        targets += [f"{source}:{t}" for t in tokens or []]
    targets += list(config.discovery.seed_urls or [])
    if targets:
        print(f"discovery       OK   {len(targets)} target(s): {', '.join(targets[:6])}")
    else:
        print("discovery       WARN no targets configured "
              "(set discovery.boards / seed_urls, or pass --board/--url)")

    db.close()
    print("\n" + ("all checks passed" if ok else "one or more checks FAILED"))
    return 0 if ok else 1


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    try:
        applicant = Applicant.load(config.run.profile_path, config.run.resume_path,
                                   strict=config.run.mode == "apply")
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    db = Database(config.run.database_path)
    qwen = make_qwen(config, db, args.fake_qwen)
    orchestrator = Orchestrator(config, applicant, qwen, db)
    orchestrator.install_signal_handlers()

    dashboard: Optional[DashboardServer] = None
    if config.dashboard.enabled and not args.discover_only:
        dashboard = DashboardServer(config, db).start()
        print(f"dashboard: {dashboard.url}")

    if config.run.mode == "apply":
        log.warning("APPLY MODE: applications will be submitted for real",
                    extra={"max_applications": config.run.max_applications})
    else:
        log.info("DRY RUN: forms will be filled and validated but never submitted")

    if args.retry_blocked:
        requeued = orchestrator.retry_blocked()
        print(f"requeued {requeued} previously blocked job(s)")

    try:
        if args.discover_only:
            found = orchestrator.discover()
            scored = orchestrator.score_pending()
            print(f"discovered {found} new job(s); scored {scored}")
            summary = orchestrator.summary
        else:
            summary = orchestrator.run(discover=not args.no_discovery)
    finally:
        if dashboard is not None:
            dashboard.stop()
        qwen.close()

    print("\n" + "=" * 62)
    print(f"run {summary.run_id}  ({config.run.mode})")
    for key, value in summary.as_dict().items():
        if key in ("run_id", "stop_reason"):
            continue
        print(f"  {key:<18}{value}")
    if summary.stop_reason:
        print(f"  stop reason       {summary.stop_reason}")
    stats = qwen.stats
    print(f"  qwen requests     {stats['requests']} ({stats['cache_hits']} cached)")
    print(f"  qwen tokens       {stats['prompt_tokens'] + stats['completion_tokens']:,}")
    print(f"  estimated cost    ${stats['cost_usd']:.4f}")
    db.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.init_profile:
        config = Config.load(args.config, args.env_file)
        path = Path(args.profile or config.run.profile_path)
        if path.exists():
            print(f"refusing to overwrite the existing profile at {path}", file=sys.stderr)
            return 1
        write_example_profile(path)
        print(f"wrote a blank applicant profile to {path}\nFill it in before running.")
        return 0

    config = Config.load(args.config, args.env_file)
    apply_overrides(config, args)
    setup_logging(config.run.log_dir, config.run.log_level)

    if args.status:
        return cmd_status(config, args)
    if args.check:
        return cmd_check(config, args)
    return cmd_run(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
