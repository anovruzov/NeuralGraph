"""E3: latency, throughput and (optionally) energy of OpenAI-compatible endpoints, measured from this machine.

    python -m mycelic.collective.experiments.e3_latency --routing ROUTING.json --endpoint NAME [--endpoint NAME ...]
        --run-id ID [--runs-dir runs] [--boundary central|site:<id>] [--concurrency 1,4] [--requests 20]
        [--warmup 2] [--workloads extraction,short] [--seed 7] [--energy rapl] [--powercap-path PATH]
        [--server-note TEXT] [--dry-run]

STRATEGY section 11.2 asks for real latency and throughput to replace every estimate in section 5. This harness
produces those numbers on the founder's machines; it produces none in CI, where it only runs against the fake
server and therefore writes ``measurement: false``.

Two raw workloads, both on synthetic text (seeded filler from a fixed neutral word list, distinct per request so
a server's prompt cache can reuse only the shared system prefix):

* ``extraction`` (task ``e3_extraction_like``): about 1,500 words in, up to 200 tokens out, a list of short facts;
* ``short`` (task ``e3_short_answer``): about 750 words in, up to 50 tokens out, one sentence.

For every endpoint x workload x concurrency level, ``--requests`` streamed requests go through
``Runtime.single(stream=True)`` on a pool of that many threads; the first ``--warmup`` complete before the rest are
submitted and are excluded from every statistic. Each cell reports TTFT and end-to-end latency (p50/p95 over ok
measured requests, final HTTP try only), how many measured requests needed a transport retry, decode tokens/s
``(tokens_out - 1) / (e2e - ttft)``, the median prompt length the *server* reported, wall-clock throughput and,
with ``--energy rapl``, the RAPL energy counter read around the measured window. Results go to
``runs/e3/<id>/e3.json`` with the usage ledger beside it. Endpoints must be real ``openai_compat`` servers: a
routing file with a fake provider is refused. Exit 0 even when requests fail (the failures are counted); exit 2 on
a configuration or usage error.
"""
from __future__ import annotations

import argparse
import os
import platform
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..inference.client import list_models
from ..inference.ledger import read_ledger
from ..inference.routing import ConfigError, Endpoint, key_problem, load_routing, missing_env, runtime_boundary_ok
from ..inference.runtime import Attempt, Runtime, boundary_mode
from ..inference.tasks import TaskSpec
from ..stats import percentile
from .common import DryRun, UsageError, check_run_id, code_stamps, fail, measurement_flag, run_dir, split_list, \
    utc_clock, write_json_atomic

CLI = "e3_latency"
DEFAULT_POWERCAP = "/sys/class/powercap/intel-rapl:0/energy_uj"
NOT_REQUESTED = "not requested (pass --energy rapl)"
ENERGY_SCOPE = "RAPL domain on the client host; includes idle and other processes"

WORDS = (
    "valve", "housing", "sensor", "cable", "panel", "filter", "pump", "seal", "bracket", "display", "battery",
    "connector", "tube", "button", "cover", "spring", "screen", "motor", "gasket", "label", "clamp", "lever",
    "hinge", "socket", "circuit", "frame", "door", "switch", "lamp", "fuse", "routine", "check", "shift", "batch",
    "report", "service", "review", "record", "update", "sample", "measured", "replaced", "inspected", "cleaned",
    "noted", "observed", "adjusted", "tested", "returned", "logged", "during", "after", "before", "within",
    "normal", "minor", "visible", "stable", "intermittent", "loose",
)

_FACTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 80}}},
}
_ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer"],
    "properties": {"answer": {"type": "string", "maxLength": 300}},
}
WORKLOADS: dict[str, tuple[TaskSpec, dict[str, Any], int]] = {
    "extraction": (TaskSpec("e3_extraction_like", "raw",
                            "List up to five short facts that the text states, each under 80 characters.", 200),
                   _FACTS_SCHEMA, 1500),
    "short": (TaskSpec("e3_short_answer", "raw", "In one sentence, say what the text is mostly about.", 50),
              _ANSWER_SCHEMA, 750),
}

NOTES = [
    "Synthetic filler text only; no record of any kind was sent.",
    "TTFT includes time queued at the server when concurrency exceeds the server's parallel slots.",
    "Warm-up requests completed before the measured batch was submitted and are excluded from every statistic.",
    "Prompt length is the server-reported prompt_tokens (median); it is null when the server reports no usage.",
    "decode_tok_s = (tokens_out - 1) / (e2e - ttft) per ok request; null without usage, with tokens_out <= 1, "
    "without a TTFT, with at most one content chunk, or when e2e - ttft <= 1 ms.",
    "ttft_s and e2e_s time each request's final HTTP try only: a transport retry (after a 429 or 5xx answer or a "
    "dropped connection) and the wait before it are not in them. retried counts the measured requests that needed "
    "one and transport_retries the retries in all; when either is non-zero, the latencies understate what a caller "
    "waited (throughput wall_s includes everything).",
    "Each request carries distinct seeded filler, so a prompt cache can reuse only the shared system prefix.",
    "The host block describes the client machine that ran this harness; the server is described by --server-note.",
    "measurement is false whenever a fake server or provider was involved: such a run is a rehearsal, not a result.",
]


def filler(words: int, seed: str) -> str:
    rng = random.Random(seed)
    sentences, count = [], 0
    while count < words:
        n = min(rng.randint(8, 16), words - count)
        sentence = " ".join(rng.choice(WORDS) for _ in range(n))
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
        count += n
    return " ".join(sentences)


# --------------------------------------------------------------------------------------------------- host and energy

def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _command(*args: str) -> str | None:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def host_info(server_note: str | None) -> dict[str, Any]:
    cpu_model = None
    cpuinfo = _read_text("/proc/cpuinfo")
    if cpuinfo:
        for line in cpuinfo.splitlines():
            if line.lower().startswith("model name") and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    elif sys.platform == "darwin":
        cpu_model = _command("sysctl", "-n", "machdep.cpu.brand_string")
    ram_bytes = None
    meminfo = _read_text("/proc/meminfo")
    if meminfo:
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                ram_bytes = int(line.split()[1]) * 1024
                break
    elif sys.platform == "darwin":
        value = _command("sysctl", "-n", "hw.memsize")
        ram_bytes = int(value) if value and value.isdigit() else None
    gpu = _command("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    governor = _read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    return {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine(),
            "cpu_model": cpu_model, "cores_logical": os.cpu_count(), "ram_bytes": ram_bytes,
            "gpu": gpu.splitlines() if gpu else None, "governor": governor.strip() if governor else None,
            "server_note": server_note or None}


def _read_counter(path: Path) -> int | None:
    text = _read_text(str(path))
    return int(text.strip()) if text and text.strip().isdigit() else None


def energy_reading(path: Path, before: int | None, after: int | None,
                   ok: int) -> tuple[dict[str, Any] | None, str | None]:
    if before is None or after is None:
        return None, f"powercap not readable: {path}"
    delta = after - before
    if delta < 0:
        max_range = _read_counter(path.with_name("max_energy_range_uj"))
        if max_range is None:
            return None, f"counter wrapped and max_energy_range_uj not readable next to {path}"
        delta += max_range
    joules = delta / 1e6
    wh = joules / 3600
    return {"joules": round(joules, 6), "wh": round(wh, 9), "wh_per_ok_request": round(wh / ok, 9) if ok else None,
            "scope": ENERGY_SCOPE, "source": str(path)}, None


# --------------------------------------------------------------------------------------------------- one cell

def _pq(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"p50": round(percentile(values, 50), 6), "p95": round(percentile(values, 95), 6)}


def decode_rate(attempt: Attempt) -> float | None:
    row = attempt.row
    if row["tokens_out"] is None or row["tokens_out"] <= 1 or row["ttft_ms"] is None or attempt.content_chunks <= 1:
        return None
    span_s = (row["latency_ms"] - row["ttft_ms"]) / 1000
    if span_s <= 0.001:
        return None
    return (row["tokens_out"] - 1) / span_s


def run_cell(rt: Runtime, endpoint: str, workload: str, level: int, *, requests: int, warmup: int, seed: int,
             powercap: Path | None) -> dict[str, Any]:
    task, schema, words = WORKLOADS[workload]
    payloads = [{"text": filler(words, f"e3:{seed}:{workload}:c{level}:{i}")} for i in range(requests)]

    def one(i: int) -> Attempt:
        return rt.single(task, payloads[i], schema, ref=f"e3:{workload}:c{level}:{i}", endpoint=endpoint, stream=True)

    with ThreadPoolExecutor(max_workers=level) as pool:
        for future in [pool.submit(one, i) for i in range(warmup)]:
            future.result()
        before = _read_counter(powercap) if powercap is not None else None
        t0 = time.perf_counter()
        attempts = [f.result() for f in [pool.submit(one, i) for i in range(warmup, requests)]]
        wall_s = time.perf_counter() - t0
        after = _read_counter(powercap) if powercap is not None else None

    ok = [a for a in attempts if a.row["ok"]]
    retries = [a.row["transport_retries"] for a in attempts]
    failures: dict[str, int] = {}
    for a in attempts:
        if not a.row["ok"]:
            failures[a.row["error_kind"]] = failures.get(a.row["error_kind"], 0) + 1
    tokens_out = [a.row["tokens_out"] for a in ok]
    decode = [r for r in (decode_rate(a) for a in ok) if r is not None]
    prompt = [a.row["tokens_in"] for a in ok if a.row["tokens_in"] is not None]
    if powercap is None:
        energy, energy_note = None, NOT_REQUESTED
    else:
        energy, energy_note = energy_reading(powercap, before, after, len(ok))
    return {
        "endpoint": endpoint, "workload": workload, "task": task.name, "concurrency": level, "sent": requests,
        "warmup": warmup, "measured": len(attempts), "ok": len(ok), "failures": failures,
        "retried": sum(1 for r in retries if r), "transport_retries": sum(retries),
        "ttft_s": _pq([a.row["ttft_ms"] / 1000 for a in ok if a.row["ttft_ms"] is not None]),
        "e2e_s": _pq([a.row["latency_ms"] / 1000 for a in ok if a.row["latency_ms"] is not None]),
        "decode_tok_s": _pq(decode),
        "prompt_tokens_p50": percentile(prompt, 50) if prompt else None,
        "throughput": {
            "wall_s": round(wall_s, 6),
            "requests_per_s": round(len(ok) / wall_s, 6) if wall_s > 0 else None,
            "completion_tokens_per_s": (round(sum(tokens_out) / wall_s, 6)
                                        if ok and wall_s > 0 and all(t is not None for t in tokens_out) else None),
        },
        "energy": energy, "energy_note": energy_note,
    }


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.e3_latency",
                                description="E3: latency and throughput of OpenAI-compatible endpoints.")
    p.add_argument("--routing", required=True, help="routing file (see docs/collective/examples)")
    p.add_argument("--endpoint", required=True, action="append", help="endpoint name; repeat for several")
    p.add_argument("--run-id", required=True)
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--boundary", default="central", help="the runtime's boundary: central or site:<id>")
    p.add_argument("--concurrency", default="1,4", help="comma-separated concurrency levels")
    p.add_argument("--requests", type=int, default=20, help="requests per cell, warm-up included")
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--workloads", default="extraction,short")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--energy", choices=["rapl"], default=None)
    p.add_argument("--powercap-path", default=DEFAULT_POWERCAP)
    p.add_argument("--server-note", default="", help="free text describing the server machine and its settings")
    p.add_argument("--dry-run", action="store_true")
    return p


def _check_args(args: argparse.Namespace) -> tuple[list[int], list[str]]:
    check_run_id(args.run_id)
    if not runtime_boundary_ok(args.boundary):
        raise UsageError("--boundary must be central or site:<id>") from None
    try:
        levels = [int(v) for v in split_list(args.concurrency)]
    except ValueError:
        levels = []
    if not levels or any(not 1 <= v <= 256 for v in levels):
        raise UsageError("--concurrency must list integers in [1, 256]") from None
    workloads = split_list(args.workloads)
    if not workloads or any(w not in WORKLOADS for w in workloads):
        raise UsageError(f"--workloads must be a subset of {', '.join(WORKLOADS)}") from None
    if args.requests < 1 or not 0 <= args.warmup < args.requests:
        raise UsageError("need --requests >= 1 and 0 <= --warmup < --requests") from None
    return levels, workloads


def _selected(config: Any, names: list[str], boundary: str) -> list[Endpoint]:
    out = []
    for name in names:
        endpoint = config.endpoints.get(name)
        if endpoint is None:
            raise UsageError(f"endpoint {name!r} is not in the routing file") from None
        if endpoint.provider != "openai_compat":
            raise UsageError(f"endpoint {name!r} is not openai_compat; E3 measures real servers only") from None
        mode = boundary_mode(boundary, endpoint, "raw", simulation=False, allow_external_raw="synthetic")
        if mode == "refused":
            raise UsageError(f"endpoint {name!r} is outside what boundary {boundary} may call") from None
        out.append(endpoint)
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        levels, workloads = _check_args(args)
        out_dir = run_dir(args.runs_dir, "e3", args.run_id)
        powercap = Path(args.powercap_path) if args.energy == "rapl" else None
        if args.dry_run:
            dry = DryRun(CLI)
            if not Path(args.routing).is_file():
                dry.need(f"routing file {args.routing}")
            else:
                config = load_routing(args.routing, check_env=False)
                endpoints = _selected(config, list(dict.fromkeys(args.endpoint)), args.boundary)
                for var in missing_env(config, [e.name for e in endpoints]):
                    dry.need(f"env {var}")
                for endpoint in endpoints:
                    dry.need(f"network {endpoint.host_label} (endpoint {endpoint.name}, a running server)")
            if powercap is not None and _read_counter(powercap) is None:
                dry.need(f"readable powercap counter {powercap}")
            dry.write(str(out_dir / "e3.json"))
            dry.write(str(out_dir / "ledger.jsonl"))
            return dry.emit()

        config = load_routing(args.routing, check_env=False)
        endpoints = _selected(config, list(dict.fromkeys(args.endpoint)), args.boundary)
        for endpoint in endpoints:
            problem = key_problem(endpoint, os.environ)
            if problem is not None:
                raise problem from None
        out_dir.mkdir(parents=True)
    except (UsageError, ConfigError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"cannot create the run directory ({exc.__class__.__name__})")

    started_at = utc_clock()
    try:
        rt = Runtime(config, boundary=args.boundary, ledger_path=out_dir / "ledger.jsonl", run_id=args.run_id,
                     clock=utc_clock, data_label="synthetic", allow_external_raw="synthetic")
    except ConfigError as exc:
        return fail(str(exc))
    try:
        host = host_info(args.server_note)
        listed = {e.name: list_models(e) for e in endpoints}
        cells = [run_cell(rt, e.name, w, level, requests=args.requests, warmup=args.warmup, seed=args.seed,
                          powercap=powercap)
                 for e in endpoints for w in workloads for level in levels]
    finally:
        rt.close()
    rows = read_ledger(out_dir / "ledger.jsonl")
    models_fake = any(m["fake"] for m in listed.values())
    result = {
        "kind": "e3", "schema_version": 1, "run_id": args.run_id,
        "measurement": measurement_flag(endpoints, rows, models_fake), "data_label": "synthetic",
        **code_stamps(), "routing_sha256": config.sha256,
        "endpoints": [{
            "name": e.name, "provider": e.provider, "host": e.host_label, "boundary": e.boundary,
            "response_format": e.response_format, "transport_schema": e.transport_schema,
            "model_requested": e.model,
            "models_served": sorted({r["model_served"] for r in rows if r["endpoint"] == e.name and r["model_served"]}),
            "models_listed": listed[e.name]["ids"] if listed[e.name]["ok"] else None,
            "listed_fake": listed[e.name]["fake"],
        } for e in endpoints],
        "params": {"endpoints": [e.name for e in endpoints], "boundary": args.boundary, "concurrency": levels,
                   "requests": args.requests, "warmup": args.warmup, "workloads": workloads, "seed": args.seed,
                   "stream": True, "energy": args.energy, "powercap_path": str(powercap) if powercap else None,
                   "words": {w: WORKLOADS[w][2] for w in workloads}},
        "host": host, "cells": cells, "notes": NOTES, "started_at": started_at, "finished_at": utc_clock(),
        "ledger": "ledger.jsonl",
    }
    write_json_atomic(out_dir / "e3.json", result)
    print(f"e3: wrote {out_dir / 'e3.json'} (measurement={str(result['measurement']).lower()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
