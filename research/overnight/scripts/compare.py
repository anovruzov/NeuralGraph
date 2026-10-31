#!/usr/bin/env python3
"""Paired comparison of two answer_eval result files on the same question ids. Stdlib only.

  compare.py baseline.json variant.json [--metric strict,lenient,substring[,lenient_nonidk,strict_nonidk,strict_v2,...]] [--md]

Prints per metric: baseline %, variant %, delta (points), wins/losses (discordant pairs), exact McNemar p
(two-sided binomial on the discordant pairs), and a 95% bootstrap CI of the delta (2000 resamples, seed 1).
Then the flipped question ids per metric so a judge can audit them. Exit code 0 always.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

from answer_eval import nonidk_flag


def val(r: dict, m: str) -> bool:
    """Row metric; lenient_nonidk / strict_nonidk are computed on the fly for old files that lack them."""
    if m in r:
        return bool(r[m])
    if m in ("lenient_nonidk", "strict_nonidk"):
        return nonidk_flag(r, m[: -len("_nonidk")])
    raise SystemExit(f"metric {m!r} missing in row {r.get('id')}")


def load(p: str) -> dict[str, dict]:
    return {r["id"]: r for r in json.loads(Path(p).read_text())["rows"]}


def binom_two_sided(k: int, n: int) -> float:
    if n == 0:
        return 1.0
    p = 0.0
    for i in range(n + 1):
        pi = math.comb(n, i) / 2 ** n
        if pi <= math.comb(n, k) / 2 ** n + 1e-12:
            p += pi
    return min(1.0, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("variant")
    ap.add_argument("--metric", default="strict,lenient,substring")
    ap.add_argument("--md", action="store_true", help="markdown table only")
    ap.add_argument("--types", default="", help="restrict to these types (comma list)")
    a = ap.parse_args()
    b, v = load(a.base), load(a.variant)
    ids = sorted(set(b) & set(v))
    if a.types:
        keep = {t.strip() for t in a.types.split(",")}
        ids = [i for i in ids if b[i]["type"] in keep]
    missing = (set(b) | set(v)) - set(ids)
    n = len(ids)
    rng = random.Random(1)
    header = f"| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |\n|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header]
    flips = {}
    for m in a.metric.split(","):
        xb = [1 if val(b[i], m) else 0 for i in ids]
        xv = [1 if val(v[i], m) else 0 for i in ids]
        wins = [ids[k] for k in range(n) if xv[k] > xb[k]]
        losses = [ids[k] for k in range(n) if xv[k] < xb[k]]
        d = [xv[k] - xb[k] for k in range(n)]
        pb, pv = 100 * sum(xb) / max(1, n), 100 * sum(xv) / max(1, n)
        p = binom_two_sided(len(wins), len(wins) + len(losses))
        boots = []
        for _ in range(2000):
            s = [d[rng.randrange(n)] for _ in range(n)] if n else [0]
            boots.append(100 * sum(s) / max(1, n))
        boots.sort()
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]
        lines.append(f"| {m} | {pb:.1f} | {pv:.1f} | {pv - pb:+.1f} | {len(wins)} | {len(losses)} | {p:.3f} | [{lo:+.1f}, {hi:+.1f}] |")
        flips[m] = (wins, losses)
    print(f"n = {n} paired questions" + (f" ({len(missing)} unpaired ignored)" if missing else ""))
    print("\n".join(lines))
    if not a.md:
        for m, (w, l) in flips.items():
            print(f"\n{m}: fixed {w}\n{m}: broke {l}")
            for i in w + l:
                tag = "+" if i in w else "-"
                print(f"  {tag} {i} [{b[i]['type']}] gold={b[i]['gold']!r}\n      base: {b[i]['answer'][:120]!r}\n      var : {v[i]['answer'][:120]!r}")


if __name__ == "__main__":
    main()
