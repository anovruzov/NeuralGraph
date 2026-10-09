#!/usr/bin/env python
"""Can NHTSA's public investigation file date an outcome earlier than a recall? Schema and size only.

    python tools/market/nhtsa_inv_probe.py --out nhtsa-inv-probe.json

Downloads the field definitions of the defect investigation file (``INV.txt``) and, for each candidate flat file, reads
**only its size, row count and fields per row**: no make, date, component or text of any investigation is printed or
written, so a replay's rule can be fixed from the published field list before any outcome is seen (as
``nhtsa_probe.py`` did for recalls). Runs where static.nhtsa.gov is reachable (the vehicle-probe workflow).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nhtsa_probe import BASE, fetch, rows  # noqa: E402

INV_DOC = f"{BASE}/inv/INV.txt"
INV_CANDIDATES = (f"{BASE}/inv/FLAT_INV.zip", f"{BASE}/inv/INV_FLAT.zip")


def shape(blob: bytes) -> dict[str, Any]:
    """Size, rows and the field-count histogram of one tab-separated flat file; nothing of its content."""
    widths: Counter[int] = Counter(len(f) for f in rows(blob))
    return {"bytes": len(blob), "rows": sum(widths.values()), "fields_per_row": dict(widths.most_common(5))}


def probe(get: Callable[[str], bytes] = fetch) -> dict[str, Any]:
    out: dict[str, Any] = {"note": "investigation files: size, row and field counts only; no investigation read"}
    try:
        out["doc"] = get(INV_DOC).decode("latin-1")[:12000]
    except Exception as exc:                                      # noqa: BLE001 (report and go on)
        out["doc"] = f"error: {exc.__class__.__name__}: {exc}"
    out["files"] = {}
    for url in INV_CANDIDATES:
        try:
            out["files"][url] = shape(get(url))
        except Exception as exc:                                  # noqa: BLE001
            out["files"][url] = {"error": f"{exc.__class__.__name__}: {exc}"}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    result = {"kind": "nhtsa_inv_probe", **probe()}
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("=== NHTSA-INV-PROBE BEGIN ===")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    print("=== NHTSA-INV-PROBE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
