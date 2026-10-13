"""Synthetic data for accuracy test A002's tests and the lab's dry run (no NHTSA record is read, no artifact is
downloaded): ``tests.lab.j2_data``'s archive at 90 complaints per make, so that three disjoint draws of 150 fit (B21).

    python -m tests.lab.j3_data --out DIR [--seed 1] [--per-make 90]

writes ``DIR/complaints.zip`` (``j2_data.archive``: six makes, ``per_make`` complaints each, every value invented;
at 90 per make about 540 records are eligible, enough for the J001 stand-in, the A001 stand-in and the dry run's own
draw of 150, where ``j2_data``'s default of 60 gives about 360 and ``lab.j2labels.draw`` would refuse the third),
``DIR/earlier/<J001 run id>/prereg/j1/...`` (``j2_data.earlier_artifact``: the J001-shaped stand-in, seed 1, built by
the pinned code), ``DIR/earlier/<A001 run id>/prereg/j2/...`` (:func:`a001_artifact`: the A001-shaped stand-in built
from the same archive by the pinned code, ``lab.j2labels.build_labels`` of 150 at seed 2 less the J001 stand-in's
refs, ``lab.j1.build_questions`` at seed 2 and ``prereg/j2/excluded.jsonl``, the chain of both draws, with the
manifest ``prereg/prereg.json`` holding their hashes) and ``DIR/stand-in.json`` (:func:`stand_in`: the two-entry
exclude list ``lab.prereg --exclude-stand-in`` reads, whose prefixes are the synthetic files', so every one of the
five sources verifies on this archive). ``lab.dryrun --j3-data DIR`` hands the three to ``lab.prereg``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import canonical_dumps, sha256_hex
from mycelic.collective.packs import loader

from lab import j1 as lab_j1
from lab import j2labels
from lab.goldlabels import NHTSA_PACK
from tests.lab import j2_data
from tests.lab.j2_data import _write, archive, earlier_artifact

ROOT = Path(__file__).resolve().parents[2]
PER_MAKE = 90
J001_RUN_ID = j2_data.RUN_ID
A001_RUN_ID = j2_data.RUN_ID + 1
A001_ARTIFACT = f"lab-plan-{A001_RUN_ID}-1"
J2_LABELS_FILE = "prereg/j2/labels.jsonl"
J2_CHAIN_FILE = "prereg/j2/excluded.jsonl"


def a001_artifact(directory: Path, blob: bytes, j001_refs: set[str], run_id: int = A001_RUN_ID, *, n: int = 150,
                  seed: int = 2, parts: int = 5) -> dict[str, Any]:
    """A stand-in for A001's plan artifact under ``directory/<run id>/`` (see the module docstring); returns the
    second exclude entry (the labels file, its prefix, the rebuild keys and the chain keys)."""
    pack = loader.load_pack(ROOT / NHTSA_PACK)
    data, record = j2labels.build_labels(NHTSA_PACK, n, seed, fetch=lambda: blob, excluded=j001_refs,
                                         mechanisms={"artifact": "verified", "rebuild": "verified"})
    questions_bytes, questions = lab_j1.build_questions(pack, data, seed=seed, parts=parts)
    drawn = {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}
    chain = j2labels.excluded_jsonl(j001_refs | drawn)
    base = directory / str(run_id)
    files = {
        J2_LABELS_FILE: _write(base / "prereg" / "j2" / "labels.jsonl", data),
        "prereg/j2/labels.json": _write(base / "prereg" / "j2" / "labels.json",
                                        (canonical_dumps(record) + "\n").encode("utf-8")),
        "prereg/j2/questions.jsonl": _write(base / "prereg" / "j2" / "questions.jsonl", questions_bytes),
        "prereg/j2/questions.json": _write(base / "prereg" / "j2" / "questions.json",
                                           (canonical_dumps(questions) + "\n").encode("utf-8")),
        J2_CHAIN_FILE: _write(base / "prereg" / "j2" / "excluded.jsonl", chain),
    }
    manifest = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": "0" * 64, "files": dict(sorted(files.items())),
                "e1": None, "x1": None, "e2": None, "j1": None, "l1": None, "j2": {"prereg": "prereg/j2/prereg.json"}}
    _write(base / "prereg" / "prereg.json", (canonical_dumps(manifest) + "\n").encode("utf-8"))
    sha = files[J2_LABELS_FILE]["sha256"]
    return {"run_id": run_id, "artifact": f"lab-plan-{run_id}-1", "file": J2_LABELS_FILE, "sha256_prefix": sha[:12],
            "rebuild_seed": seed, "rebuild_sha256_prefix": sha[:12], "chain_file": J2_CHAIN_FILE,
            "chain_sha256_prefix": sha256_hex(chain)[:12]}


def stand_in(directory: Path, blob: bytes) -> list[dict[str, Any]]:
    """Both synthetic earlier artifacts under ``directory`` and the two-entry exclude list that names them."""
    j001 = earlier_artifact(directory, blob)
    j001_refs = {r["record_ref"] for r, _ in lab_j1.parse_labels(
        (directory / str(j001["run_id"]) / "prereg" / "j1" / "labels.jsonl").read_bytes())}
    first = {"run_id": j001["run_id"], "artifact": j001["artifact"], "file": j001["file"],
             "sha256_prefix": j001["sha256_prefix"], "rebuild_seed": 1,
             "rebuild_sha256_prefix": j001["rebuild_sha256_prefix"]}
    return [first, a001_artifact(directory, blob, j001_refs)]


def write_data(out: Path, *, seed: int = 1, per_make: int = PER_MAKE) -> Path:
    """The three things the dry run takes (see the module docstring); returns ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    blob = archive(per_make, seed)
    (out / "complaints.zip").write_bytes(blob)
    entries = stand_in(out / "earlier", blob)
    (out / "stand-in.json").write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.j3_data", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--per-make", type=int, default=PER_MAKE)
    args = p.parse_args(argv)
    out = write_data(Path(args.out), seed=args.seed, per_make=args.per_make)
    print(f"wrote {out} (synthetic: every value invented; the earlier artifacts stand in for no real run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
