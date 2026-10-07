#!/usr/bin/env python
"""How the shipped stack behaves as one organization grows: the measurement behind
``MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG`` (DEPLOYMENT.md, "Volume cap").  Not collected by CI.

    python tests/perf/mycelic_volume.py --workdir /tmp/mycelic-volume --out /tmp/mycelic-volume.json
    python tests/perf/mycelic_volume.py --workdir /tmp/mycelic-volume --phase serve    # again, on the saved databases

Build (in process, the consumer's apply path): one organization of 96 agents in 32 teams across 2 regions, with the
rules of ``deploy/mycelic/rules.json`` and a signing key, is fed notes over 3 slots and about 50 entities.  At each
level of active raw notes (2,000, 5,000 and 10,000 by default) the database and the agent keys are saved, and the
``apply_event`` time of each of the last 200 notes before the level is recorded.  Building 10,000 notes takes tens
of minutes, so start it in the background.

Serve (per level): a copy of that database behind ``nats-server`` and ``python -m mycelic serve`` (same key and
rules, ``MYCELIC_RATE_LIMIT_RPS=1000``).  After 5 warm-up queries, 200 iterations of ``POST /memory`` (a new note)
immediately followed by ``POST /query`` (``{"query": one of 5 fixed phrasings, "scope": org}``), each as a random
agent; the wall time of each ``/query`` is recorded at the client.

The shipped value is the largest level whose ``/query`` p95 and apply p95 are both at most 1.0 s (0 when none is).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import secrets
import shutil
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mycelic.harness import RULES_FILE, ProcessDriver  # noqa: E402

ORG = "acme"
SLOTS = {"transport_disruption": "transport", "supplier_buffer_low": "supplier", "demand_commitment": "demand"}
PHRASINGS = ("supply risk", "transport disruption at the port", "supplier buffer is low", "committed demand for e7",
             "regional supply risk e12")
TAIL = 200                    # notes timed before each level
LIMIT_SECONDS = 1.0           # both p95 bounds of the shipped value


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {"n": len(values), "p50": statistics.median(values), "p95": pct(values, 0.95), "max": max(values)}


def note(rnd: random.Random, n: int, prefix: str) -> dict[str, Any]:
    slot = rnd.choice(sorted(SLOTS))
    entity = None if rnd.random() < 0.05 else f"e{rnd.randrange(50)}"
    topic = f"supply:{entity or 'general'}/{SLOTS[slot]}"
    return {"text": f"note {prefix}{n} on {topic}", "topic": topic, "slot": slot, "entity": entity,
            "confidence": round(rnd.uniform(0.3, 0.95), 2), "idempotency_key": f"{prefix}{n}"}


# ------------------------------------------------------------------------------------------------------------- build
async def build(workdir: Path, levels: list[int]) -> None:
    from mycelic.metrics import Metrics
    from mycelic.config import Settings
    from mycelic.service import MycelicService
    from mycelic.transport import InProcessTransport

    signing_key, admin_token = secrets.token_hex(32), secrets.token_hex(32)
    tmp = workdir / "build"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    st = Settings(host="127.0.0.1", port=0, db_path=str(tmp / "mycelic.db"), nats_url=None, admin_token=admin_token,
                  event_signing_key=signing_key, rules_file=str(RULES_FILE), rate_limit_rps=0.0, min_support=2)
    st.validate()
    s = MycelicService(st, transport=InProcessTransport(), metrics=Metrics())
    await s.start(background=False)               # loads the rules file and records the derivation meta
    seq = 0
    applies: list[float] = []

    async def pump() -> None:
        """Publish and apply every pending event in outbox order (the consumer of a service with no loops),
        timing the apply of each note."""
        nonlocal seq
        while True:
            rows = s.store.pending_events(1000)
            if not rows:
                return
            for ev in rows:
                seq += 1
                async with s.store.transaction() as tx:
                    tx.mark_published(ev.event_id, seq)
                wire = json.loads(json.dumps(s._event_wire(ev)))
                t0 = time.perf_counter()
                await s.apply_event(wire, seq=seq)
                if ev.kind == "memory.observed":
                    applies.append(time.perf_counter() - t0)

    keys: dict[str, str] = {}
    i = 0
    for region in ("emea", "apac"):
        for sub in ("s1", "s2"):
            for dept in ("d1", "d2"):
                for team in ("t1", "t2", "t3", "t4"):
                    for _ in range(3):
                        aid = f"a{i:03d}"
                        _, keys[aid] = await s.register_agent({"enterprise": ORG, "region": region,
                                                               "subsidiary": f"{region}-{sub}", "department": dept,
                                                               "team": team, "agent_id": aid})
                        i += 1
    await pump()
    principals = {aid: s.authenticate(f"Bearer {key}") for aid, key in keys.items()}
    agents = sorted(keys)
    rnd = random.Random(7)
    (workdir / "build.json").write_text(json.dumps({"signing_key": signing_key, "admin_token": admin_token, "keys": keys,
                                                    "levels": {}}))
    t_start = time.perf_counter()
    n = 0
    for level in sorted(levels):
        while n < level:
            await s.ingest_memory(principals[rnd.choice(agents)], note(rnd, n, "n"))
            await pump()
            n += 1
            if n % 500 == 0:
                print(f"  {n} notes applied, {time.perf_counter() - t_start:.0f} s", flush=True)
        s.store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out = workdir / f"level_{level}"
        out.mkdir(exist_ok=True)
        shutil.copyfile(tmp / "mycelic.db", out / "mycelic.db")
        stats = s.store.stats()
        info = json.loads((workdir / "build.json").read_text())
        info["levels"][str(level)] = {"apply_seconds": applies[-TAIL:], "active_by_layer": stats["memories_by_layer"],
                                      "active_raw_notes": stats["memories_by_layer"]["agent"],
                                      "db_bytes": (out / "mycelic.db").stat().st_size,
                                      "built_after_seconds": round(time.perf_counter() - t_start, 1)}
        (workdir / "build.json").write_text(json.dumps(info))
        a = summary(applies[-TAIL:])
        print(f"level {level}: saved ({info['levels'][str(level)]['db_bytes'] / 1e6:.0f} MB), apply of the last {TAIL} notes "
              f"p50 {a['p50']:.3f} s, p95 {a['p95']:.3f} s, max {a['max']:.3f} s", flush=True)
    await s.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------- serve
def post(url: str, key: str, body: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status, json.loads(resp.read())


def serve(workdir: Path, level: int, iterations: int) -> dict[str, Any]:
    info = json.loads((workdir / "build.json").read_text())
    keys: dict[str, str] = info["keys"]
    run = workdir / f"serve_{level}"
    shutil.rmtree(run, ignore_errors=True)
    d = ProcessDriver(workdir=run)
    d.signing_key, d.admin_token = info["signing_key"], info["admin_token"]
    shutil.copyfile(workdir / f"level_{level}" / "mycelic.db", run / "mycelic.db")
    queries: list[float] = []
    writes: list[float] = []
    try:
        d.up()
        rnd = random.Random(level)
        agents = sorted(keys)
        for phrase in PHRASINGS:                  # warm-up
            post(f"{d.base_url}/query", keys[rnd.choice(agents)], {"query": phrase, "scope": ORG})
        for i in range(iterations):
            t0 = time.perf_counter()
            status, _ = post(f"{d.base_url}/memory", keys[rnd.choice(agents)], note(rnd, i, f"serve{level}-"))
            writes.append(time.perf_counter() - t0)
            if status != 202:
                raise RuntimeError(f"POST /memory answered {status}")
            t0 = time.perf_counter()
            status, res = post(f"{d.base_url}/query", keys[rnd.choice(agents)],
                               {"query": PHRASINGS[i % len(PHRASINGS)], "scope": ORG})
            queries.append(time.perf_counter() - t0)
            if status != 200:
                raise RuntimeError(f"POST /query answered {status}")
    finally:
        d.down()
        shutil.rmtree(run, ignore_errors=True)
    return {"query_seconds": queries, "memory_post_seconds": writes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", help="where the built databases are kept (default: a new temporary directory)")
    ap.add_argument("--levels", default="2000,5000,10000", help="active raw notes at which to save and serve")
    ap.add_argument("--iterations", type=int, default=200, help="POST /memory + POST /query pairs per level")
    ap.add_argument("--phase", choices=("all", "build", "serve"), default="all")
    ap.add_argument("--out", help="write the results as JSON here")
    args = ap.parse_args()
    levels = sorted(int(x) for x in args.levels.split(","))
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="mycelic-volume-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"workdir {workdir}; {os.cpu_count()} CPUs; Python {platform.python_version()}; {platform.platform()}", flush=True)
    if args.phase in ("all", "build"):
        asyncio.run(build(workdir, levels))
    if args.phase == "build":
        return 0
    info = json.loads((workdir / "build.json").read_text())
    rows = []
    for level in levels:
        served = serve(workdir, level, args.iterations)
        built = info["levels"][str(level)]
        rows.append({"level": level, "active_raw_notes": built["active_raw_notes"], "db_bytes": built["db_bytes"],
                     "apply": summary(built["apply_seconds"]), "query": summary(served["query_seconds"]),
                     "memory_post": summary(served["memory_post_seconds"])})
        print(f"level {level} served", flush=True)
    for r in rows:
        r["qualifies"] = r["query"]["p95"] <= LIMIT_SECONDS and r["apply"]["p95"] <= LIMIT_SECONDS
    shipped = max((r["level"] for r in rows if r["qualifies"]), default=0)
    print()
    print(f"| active raw notes | DB MB | apply p50 / p95 / max (s, last {TAIL} notes) | /query after a write p50 / p95 / max "
          f"(s, {args.iterations} runs) | qualifies |")
    print("|---|---|---|---|---|")
    for r in rows:
        a, q = r["apply"], r["query"]
        print(f"| {r['level']:,} | {r['db_bytes'] / 1e6:.0f} | {a['p50']:.3f} / {a['p95']:.3f} / {a['max']:.3f} | "
              f"{q['p50']:.3f} / {q['p95']:.3f} / {q['max']:.3f} | {'yes' if r['qualifies'] else 'no'} |")
    print()
    print(f"Shipped value (largest level with /query p95 <= {LIMIT_SECONDS} s and apply p95 <= {LIMIT_SECONDS} s): "
          f"MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG={shipped}")
    if args.out:
        Path(args.out).write_text(json.dumps({"cpus": os.cpu_count(), "python": platform.python_version(),
                                              "iterations": args.iterations, "tail": TAIL, "rows": rows,
                                              "shipped": shipped}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
