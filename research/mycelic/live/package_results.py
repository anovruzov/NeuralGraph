"""Bundle live results for sharing, keeping the (large) call cache separate.

    python3 -m research.mycelic.live.package_results [--out-dir DIR] [--dest DIR]

Writes two archives into ``--dest`` (default ``<out-dir>/packages``):

* ``mycelic_live_results_<host>_<stamp>.tar.gz`` - every result file
  (``*.jsonl`` and ``*.md`` in the out dir) plus ``MANIFEST.json``: per file
  its sha256, size, stage, scale, seed, prompt version and run environment
  (machine, llama.cpp build, model sha256s).  Small; meant to be committed or
  attached.
* ``mycelic_live_cache_<host>_<stamp>.tar.gz`` - the record/replay cache
  (``<out-dir>/cache/*.jsonl``).  Large at 2,000+ agents; keep it local or
  archive it elsewhere.  With it, ``run.py --replay-only`` reproduces every
  result bit for bit without a model server.

The result files themselves stay where they are, so they can also be
committed directly (the cache directory and the packages are gitignored).
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import tarfile
import time
from typing import Dict, List

from .run import LIVE_ART


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _meta(path: str) -> Dict[str, object]:
    if not path.endswith(".jsonl"):
        return {}
    try:
        with open(path) as fh:
            first = json.loads(fh.readline())
    except (OSError, ValueError):
        return {}
    if first.get("kind") != "meta":
        return {}
    return {k: first.get(k) for k in ("tag", "stage", "scale", "seed", "agents_run", "n_users",
                                      "mock", "prompt_version", "started", "finished",
                                      "edge_model_id", "kernel_model_id", "run_env")}


def main(argv=None) -> Dict[str, str]:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=os.environ.get("MYCELIC_LIVE_OUT_DIR", LIVE_ART))
    ap.add_argument("--cache-dir", default=os.environ.get("MYCELIC_LIVE_CACHE_DIR"))
    ap.add_argument("--dest", default=None)
    ap.add_argument("--no-cache", action="store_true", help="results archive only")
    a = ap.parse_args(argv)
    out_dir = a.out_dir
    cache_dir = a.cache_dir or os.path.join(out_dir, "cache")
    dest = a.dest or os.path.join(out_dir, "packages")
    os.makedirs(dest, exist_ok=True)
    host = platform.node().split(".")[0] or "host"
    stamp = time.strftime("%Y%m%dT%H%M%S")

    results: List[str] = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.endswith((".jsonl", ".md")) and os.path.isfile(os.path.join(out_dir, f)))
    manifest = {"created": stamp, "host": host, "files": []}
    for p in results:
        manifest["files"].append({"file": os.path.basename(p), "bytes": os.path.getsize(p),
                                  "sha256": _sha(p), **_meta(p)})
    res_tar = os.path.join(dest, f"mycelic_live_results_{host}_{stamp}.tar.gz")
    with tarfile.open(res_tar, "w:gz") as tf:
        for p in results:
            tf.add(p, arcname=os.path.join("results", os.path.basename(p)))
        blob = json.dumps(manifest, indent=1, default=str).encode()
        ti = tarfile.TarInfo("results/MANIFEST.json")
        ti.size = len(blob)
        tf.addfile(ti, io.BytesIO(blob))
    out = {"results": res_tar}
    print(f"results: {res_tar} ({os.path.getsize(res_tar)/1e6:.2f} MB, {len(results)} files)")

    if not a.no_cache and os.path.isdir(cache_dir):
        caches = sorted(os.path.join(cache_dir, f) for f in os.listdir(cache_dir)
                        if f.endswith(".jsonl"))
        if caches:
            cache_tar = os.path.join(dest, f"mycelic_live_cache_{host}_{stamp}.tar.gz")
            with tarfile.open(cache_tar, "w:gz") as tf:
                for p in caches:
                    tf.add(p, arcname=os.path.join("cache", os.path.basename(p)))
            out["cache"] = cache_tar
            print(f"cache:   {cache_tar} ({os.path.getsize(cache_tar)/1e6:.1f} MB, "
                  f"{len(caches)} files) - keep local; restore into {cache_dir} to replay")
    rel = os.path.relpath(out_dir)
    print("\nto commit the small results:\n"
          f"  git add {rel}/*.jsonl {rel}/*.md && git commit -m 'live Mycelic results ({host})'")
    return out


if __name__ == "__main__":
    main()
