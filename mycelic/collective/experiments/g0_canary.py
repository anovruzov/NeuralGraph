"""G0 runner v1: plant canaries in a synthetic world, run every stage, and scan every byte that crossed a boundary.

    python -m mycelic.collective.experiments.g0_canary --pack P --records N --seed S --out DIR
        [--mode fake|lexical|routing] [--routing FILE] [--require-master-data on|off] [--dry-run]

Flow: load the pack (``--require-master-data`` that differs from the pack's frozen setting writes a pack copy to
``DIR/pack`` with that one field changed, so the override has its own ``config_hash``); generate a world of at least
N records and keep the first N; plant canaries (``leakage.plant_canaries``) and write the manifest to
``DIR/private/manifest.json``, outside every scanned path; run each stage of :data:`STAGES` under a simulated clock
(ingest the day after the last record, emit when every record week and the ingest week are closed); scan what the
stages say crossed, with the site ledgers as a separate hygiene class; scan the first site's database as a positive
control (the scanner must find canaries and narrative text there, or the run fails); write ``DIR/leakage.json``.

Modes: ``fake`` (default) extracts through an in-process fake model at each site, so a ledger and a usage summary
exist; ``lexical`` uses no model; ``routing`` uses a routing file whose extraction endpoints are all
``any-simulated`` (one machine plays every simulated site). Everything is synthetic, and nothing here measures a
model.

Exit 0 when no canary and no narrative shingle crossed, the site ledgers are clean and the positive control found
both; 1 otherwise; 2 on a usage or configuration error. ``known_limitation`` entries (class-c ids leaving as cell
keys with ``require_master_data`` off) do not fail the run. The one wall-clock value is ``created_at``.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from ..edge.egress import read_log
from ..edge.extract import TASK_NAME, lexical_handler
from ..edge.site import EdgeSite
from ..inference.fake import FakeProvider
from ..inference.routing import ConfigError, RoutingConfig, load_routing, missing_env, parse_routing
from ..inference.runtime import Runtime
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, strict_load
from ..leakage import Artifact, LeakageError, plant_canaries, scan, write_manifest
from ..packs.canonical import Canonicaliser
from ..packs.generator import GeneratorError, generate, world_digest
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack
from .common import DryRun, UsageError, code_stamps, fail, utc_clock, write_json_atomic

CLI = "experiments.g0_canary"
MODES = ("fake", "lexical", "routing")
MAX_RECORDS = 100000
MAX_SEED = 10 ** 12
MAX_WEEKS = 520
DAY_TIME = "T08:00:00.000Z"
CROSSING_CLASSES = ("cells", "hq_receive_log", "site_egress_log", "usage_summary")
TOTAL_KEYS = ("records_ingested", "rejected", "duplicates", "forwarded_in", "late", "extracted", "claims", "cells",
              "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type", "usage_groups")


class SimClock:
    """The simulated clock every stage receives; never the wall clock."""

    def __init__(self, value: str) -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@dataclass
class G0Context:
    pack: FrozenPack
    records: tuple[dict[str, Any], ...]
    master_data: Mapping[str, Mapping[str, tuple[str, ...]]]
    out: Path
    clock: SimClock
    as_of: str
    emit_at: str
    mode: str
    seed: int
    routing: RoutingConfig | None
    sites: tuple[str, ...]
    runtimes: dict[str, Runtime] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOTAL_KEYS, 0))


@dataclass(frozen=True)
class Stage:
    """``run`` returns (artifacts that crossed a boundary, hygiene artifacts that must stay clean)."""

    name: str
    run: Callable[[G0Context], tuple[list[Artifact], list[Artifact]]]


def _runtime(ctx: G0Context, site_id: str) -> Runtime | None:
    ledger = ctx.out / "edge" / f"site-{site_id}.ledger.jsonl"
    boundary = f"site:{site_id}"
    if ctx.mode == "lexical":
        return None
    if ctx.mode == "routing":
        return Runtime(ctx.routing, boundary=boundary, ledger_path=ledger, run_id=f"g0-{ctx.seed}", clock=ctx.clock,
                       data_label="synthetic", simulation=True, environ=os.environ)
    config = parse_routing({"schema_version": 1,
                            "endpoints": {"site-fake": {"provider": "fake", "boundary": boundary}},
                            "routes": {TASK_NAME: {"endpoint": "site-fake"}}}, allow_fake=True)
    provider = FakeProvider()
    provider.register(TASK_NAME, lexical_handler(ctx.pack, Canonicaliser(ctx.pack, known=ctx.master_data[site_id])))
    return Runtime(config, boundary=boundary, ledger_path=ledger, run_id=f"g0-{ctx.seed}", clock=ctx.clock,
                   data_label="synthetic", allow_fake=True, fake=provider, sleep=lambda s: None, environ={})


def edge_stage(ctx: G0Context) -> tuple[list[Artifact], list[Artifact]]:
    """Each site ingests and extracts its own records, then every site emits its cells and usage."""
    edge, hq = ctx.out / "edge", ctx.out / "hq"
    sites: list[EdgeSite] = []
    t = ctx.totals
    try:
        for site_id in ctx.sites:
            runtime = _runtime(ctx, site_id)
            if runtime is not None:
                ctx.runtimes[site_id] = runtime
            site = EdgeSite(ctx.pack, site_id, edge, runtime=runtime, clock=ctx.clock,
                            master_data=ctx.master_data[site_id], hq_dir=hq)
            sites.append(site)
            got = site.ingest([r for r in ctx.records if r["site"] == site_id])
            t["records_ingested"] += got.ingested
            t["rejected"] += sum(got.rejected.values())
            t["duplicates"] += got.duplicates
            t["forwarded_in"] += got.forwarded_in
            t["late"] += got.late
            summary = site.extract("lexical" if runtime is None else "model")
            t["extracted"] += summary.records
            t["claims"] += summary.claims
        ctx.clock.set(ctx.emit_at)
        for site in sites:
            cells = site.emit_cells(ctx.as_of)
            if cells is not None:
                for key in ("cells", "cells_n_ge_k", "suppressed_fields", "not_master_data", "non_egress_type"):
                    t[key] += cells.stats[key]
            usage = site.emit_usage(ctx.as_of)
            if usage is not None:
                t["usage_groups"] += len(usage.body["groups"])
    finally:
        for site in sites:
            site.close()
        for runtime in ctx.runtimes.values():
            runtime.close()
    crossing = [Artifact("site_egress_log", f"edge/site-{s}.egress.jsonl", path=edge / f"site-{s}.egress.jsonl")
                for s in ctx.sites]
    crossing.append(Artifact("hq_receive_log", "hq", path=hq))
    for number, row in enumerate(read_log(hq / "receive.jsonl"), start=1):
        artifact_class = "cells" if row["artifact_type"] == "cells_bundle" else "usage_summary"
        crossing.append(Artifact(artifact_class, f"hq/receive.jsonl#{number}", data=canonical_bytes(row["body"])))
    hygiene = [Artifact("site_ledger_hygiene", f"edge/site-{s}.ledger.jsonl", path=edge / f"site-{s}.ledger.jsonl")
               for s in ctx.sites if (edge / f"site-{s}.ledger.jsonl").exists()]
    return crossing, hygiene


STAGES: tuple[Stage, ...] = (Stage("edge", edge_stage),)


# --------------------------------------------------------------------------------------------------- arguments

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.g0_canary",
                                description="G0: plant canaries in a synthetic world and scan what crossed a "
                                            "boundary (text only).")
    p.add_argument("--pack", required=True, help="a built-in pack id or a pack directory")
    p.add_argument("--records", type=int, required=True, help=f"number of records, 1..{MAX_RECORDS}")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", required=True, help="output directory; absent or empty")
    p.add_argument("--mode", choices=MODES, default="fake")
    p.add_argument("--routing", help="routing file (only with --mode routing)")
    p.add_argument("--require-master-data", choices=("on", "off"), default=None,
                   help="default: the pack's frozen setting; a different value runs a copy of the pack")
    p.add_argument("--dry-run", action="store_true")
    return p


def _check_args(args: argparse.Namespace) -> None:
    if not 1 <= args.records <= MAX_RECORDS:
        raise UsageError(f"--records must be in [1, {MAX_RECORDS}]") from None
    if not 0 <= args.seed <= MAX_SEED:
        raise UsageError(f"--seed must be in [0, {MAX_SEED}]") from None
    if (args.mode == "routing") != (args.routing is not None):
        raise UsageError("--routing is required with --mode routing, and only then") from None
    out = Path(args.out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError(f"--out must be absent or an empty directory: {out}") from None


def _routing(path: str, *, check_env: bool) -> RoutingConfig:
    config = load_routing(path, tasks=[TASK_NAME], check_env=check_env)
    route = config.routes[TASK_NAME]
    for name in (route.endpoint, route.escalate_to):
        if name is None:
            continue
        endpoint = config.endpoints[name]
        if endpoint.boundary != "any-simulated":
            raise ConfigError(f"$.endpoints.{name}.boundary",
                              "must be any-simulated: one machine plays every simulated site") from None
        if endpoint.provider == "fake":
            raise ConfigError(f"$.endpoints.{name}.provider", "the fake provider runs with --mode fake") from None
    return config


def _dry_run(args: argparse.Namespace) -> int:
    dry = DryRun(CLI)
    if is_builtin_ref(args.pack) or Path(args.pack).exists():
        load_pack(args.pack)
    else:
        dry.need(f"pack {args.pack}")
    if args.mode == "routing":
        if not Path(args.routing).is_file():
            dry.need(f"routing file {args.routing}")
        else:
            config = _routing(args.routing, check_env=False)
            route = config.routes[TASK_NAME]
            names = [n for n in (route.endpoint, route.escalate_to) if n is not None]
            for var in missing_env(config, names):
                dry.need(f"env {var}")
            for name in names:
                dry.need(f"network {config.endpoints[name].host_label} (endpoint {name}, a running server)")
    out = Path(args.out)
    for line in ("leakage.json", "private/manifest.json", "edge/site-<id>.{sqlite3,egress.jsonl,ledger.jsonl}",
                 "hq/receive.jsonl"):
        dry.write(str(out / line))
    if args.require_master_data is not None:
        dry.write(f"{out / 'pack'} (only when --require-master-data differs from the pack)")
    return dry.emit()


# --------------------------------------------------------------------------------------------------- the run

def _override(base: FrozenPack, args: argparse.Namespace, out: Path) -> tuple[FrozenPack, bool]:
    """The pack the run uses: ``base``, or a copy in ``out/pack`` whose ``egress.json`` carries the requested
    ``require_master_data`` (so it has its own config_hash)."""
    if args.require_master_data is None or (args.require_master_data == "on") == base.egress.require_master_data:
        return base, False
    copy_dir = out / "pack"
    shutil.copytree(base.directory, copy_dir)
    egress = strict_load((copy_dir / "egress.json").read_bytes())
    egress["require_master_data"] = args.require_master_data == "on"
    (copy_dir / "egress.json").write_bytes((canonical_dumps(egress) + "\n").encode("utf-8"))
    return load_pack(copy_dir), True


def _world(pack: FrozenPack, seed: int, n: int) -> tuple[Any, int]:
    sites = len(pack.generator["sites"])
    weeks = pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]
    while True:
        world = generate(pack, seed, sites, weeks)
        if len(world.records) >= n:
            return world, weeks
        weeks = math.ceil(weeks * n / len(world.records)) + 1
        if weeks > MAX_WEEKS:
            raise UsageError(f"{n} records need more than {MAX_WEEKS} weeks of this world") from None


def _positive_control(ctx: G0Context, manifest: Any, narratives: list[str]) -> dict[str, Any]:
    db = ctx.out / "edge" / f"site-{ctx.sites[0]}.sqlite3"
    label = f"edge/site-{ctx.sites[0]}.sqlite3"
    if not db.exists():
        return {"label": label, "canary_hits": 0, "shingle_overlap_bytes": 0}
    artifacts = [Artifact("positive_control", label, path=db)]
    wal = db.with_name(db.name + "-wal")
    if wal.exists():
        artifacts.append(Artifact("positive_control", label + "-wal", path=wal))
    report = scan(artifacts, manifest, narratives, ctx.pack)
    return {"label": label, "canary_hits": report["hit_count"] + len(report["known_limitation"]),
            "shingle_overlap_bytes": report["shingle_overlap_bytes"]}


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    base = load_pack(args.pack)
    routing = _routing(args.routing, check_env=True) if args.mode == "routing" else None
    world, weeks = _world(base, args.seed, args.records)      # generation reads no egress field
    out.mkdir(parents=True, exist_ok=True)
    pack, overridden = _override(base, args, out)
    records = world.records[:args.records]
    planted, manifest = plant_canaries(records, random.Random(f"g0-canaries:{args.seed}"), pack)
    manifest = write_manifest(manifest, out / "private" / "manifest.json")

    ingest_day = date.fromisoformat(max(r["received_date"] for r in planted)) + timedelta(days=1)
    as_of = ingest_day + timedelta(days=7 + pack.egress.close_lag_days)
    ctx = G0Context(pack=pack, records=planted, master_data=world.master_data, out=out,
                    clock=SimClock(ingest_day.isoformat() + DAY_TIME), as_of=as_of.isoformat(),
                    emit_at=as_of.isoformat() + DAY_TIME, mode=args.mode, seed=args.seed, routing=routing,
                    sites=tuple(world.params["site_ids"]))
    crossing: list[Artifact] = []
    hygiene: list[Artifact] = []
    for stage in STAGES:
        c, h = stage.run(ctx)
        crossing += c
        hygiene += h
    narratives = [r["narrative"] for r in planted]
    report = scan(crossing, manifest, narratives, pack, hygiene=hygiene)
    for name in CROSSING_CLASSES:
        report["artifact_classes"].setdefault(name, {"bytes": 0, "items": 0})
    report["artifact_classes"] = dict(sorted(report["artifact_classes"].items()))
    positive = _positive_control(ctx, manifest, narratives)
    hygiene_report = report["site_ledger_hygiene"]
    passed = (report["hit_count"] == 0 and report["shingle_overlap_bytes"] == 0 and not hygiene_report["hits"]
              and hygiene_report["shingle_overlap_bytes"] == 0 and positive["canary_hits"] > 0
              and positive["shingle_overlap_bytes"] > 0)
    result = {
        "kind": "g0_leakage", "schema_version": 1, "synthetic": True, "data_label": "synthetic",
        "pack": pack.id, "pack_version": pack.version, "illustrative": pack.illustrative,
        "base_config_hash": base.config_hash, "require_master_data_overridden": overridden,
        "seed": args.seed, "records": len(planted), "sites": len(ctx.sites), "weeks": weeks,
        "world_digest": world_digest(world), "mode": args.mode,
        "models_fake": None if args.mode == "lexical" else args.mode == "fake",
        "as_of": ctx.as_of, "clock": {"ingest": ingest_day.isoformat() + DAY_TIME, "emit": ctx.emit_at},
        "stages": [stage.name for stage in STAGES], "positive_control": positive,
        "edge_totals": dict(ctx.totals), "passed": passed,
        **report, **code_stamps(), "created_at": utc_clock(),
    }
    write_json_atomic(out / "leakage.json", result)
    print(f"g0: pack={result['pack']} canaries={result['canaries_planted']} hits={result['hit_count']} "
          f"shingle_overlap_bytes={result['shingle_overlap_bytes']} "
          f"known_limitation={len(result['known_limitation'])} -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _check_args(args)
        if args.dry_run:
            return _dry_run(args)
        return run(args)
    except (UsageError, ConfigError, PackError, GeneratorError, LeakageError) as exc:
        return fail(str(exc))
    except (OSError, StrictJsonError) as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")


if __name__ == "__main__":
    sys.exit(main())
