"""Synthetic data for accuracy test A001's tests and the lab's dry run (no NHTSA record is read, no artifact is
downloaded).

    python -m tests.lab.j2_data --out DIR [--seed 1] [--per-make 60]

writes ``DIR/complaints.zip`` (a complaint archive in NHTSA's flat layout, every value invented: per make,
``per_make`` complaints with one or two components, the old-and-new name pairs and the generic code among them, so
with six makes and ``per_make`` 60 about 360 records are eligible), ``DIR/earlier/<run id>/prereg/...`` (a stand-in
for an earlier run's plan artifact: J001's ``prereg/j1`` files built from that archive by the pinned code,
``goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1)`` and ``lab.j1.build_questions(seed=1, parts=6)``, and the
manifest ``prereg/prereg.json`` with their hashes) and ``DIR/stand-in.json`` (the exclusion stand-in ``lab.prereg
--exclude-stand-in`` reads: the synthetic run id :data:`RUN_ID`, its artifact name, the labels file and the sha256
prefix its bytes have, plus ``rebuild_sha256_prefix``, the same prefix, so that both freshness mechanisms verify on
this archive and their record sets agree).

``lab.dryrun --j2-data DIR`` hands the three to ``lab.prereg``. :func:`archive` and :func:`earlier_artifact` are what
``tests/lab/test_lab_j2.py`` builds its cases from.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import canonical_dumps
from mycelic.collective.packs import loader

from lab import goldlabels
from lab import j1 as lab_j1
from lab.goldlabels import NHTSA_MAKES, NHTSA_PACK

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = 100000000001                     # a synthetic run id: no GitHub run has it
ARTIFACT = f"lab-plan-{RUN_ID}-1"
LABELS_FILE = "prereg/j1/labels.jsonl"
COMPONENTS = (
    ("AIR BAGS", "The air bags did not deploy when the car hit a pole."),
    ("ENGINE", "The engine stalled on the highway at speed."),
    ("ENGINE AND ENGINE COOLING", "The engine overheated and coolant sprayed out."),
    ("SERVICE BRAKES, HYDRAULIC", "The brakes failed at a light and the pedal went to the floor."),
    ("SERVICE BRAKES", "The service brakes made a grinding noise."),
    ("STEERING", "The steering wheel locked while turning."),
    ("TIRES", "A tire blew out on the interstate."),
    ("ELECTRICAL SYSTEM", "The dashboard went dark while driving."),
    ("FUEL SYSTEM, GASOLINE", "Gasoline leaked under the car."),
    ("VISIBILITY/WIPER", "The wiper motor stopped in the rain."),
    ("SEAT BELTS", "The belt would not latch."),
    ("UNKNOWN OR OTHER", "Something odd happened."),
)


def _row(export: Any, odino: str, make: str, comp: str, text: str, year: str) -> str:
    r = [""] * 51
    r[export.C_ODINO], r[export.C_MAKE], r[export.C_MODEL], r[export.C_YEAR] = odino, make, "MODEL X", year
    r[export.C_COMP], r[export.C_STATE], r[export.C_LDATE] = comp, "TX", "20230510"
    r[export.C_DESCR], r[export.C_PROD] = text, "V"
    r[50] = "Operator Name Never Exported"
    return "\t".join(r)


@functools.lru_cache(maxsize=None)
def archive(per_make: int = 60, seed: int = 1) -> bytes:
    """A complaint archive in NHTSA's flat layout (see the module docstring); ``seed`` offsets the record ids so two
    archives hold different records."""
    export = goldlabels.nhtsa_export()
    lines, n = [], 0
    for make in NHTSA_MAKES:
        for i in range(per_make):
            n += 1
            odino, year = str(100000 * seed + n), str(2019 + i % 4)
            comp, text = COMPONENTS[i % len(COMPONENTS)]
            if comp == "UNKNOWN OR OTHER":           # keep it eligible with a specific second code
                lines.append(_row(export, odino, make, comp, text, year))
                comp, text = COMPONENTS[(i + 5) % (len(COMPONENTS) - 1)]
            lines.append(_row(export, odino, make, comp, text, year))
            if i % 4 == 0:
                other, more = COMPONENTS[(i * 3 + 1) % (len(COMPONENTS) - 1)]
                lines.append(_row(export, odino, make, other, more, year))
            if i % 6 == 2:                           # the old and the new name of one component
                lines.append(_row(export, odino, make, "ENGINE", "", year))
                lines.append(_row(export, odino, make, "ENGINE AND ENGINE COOLING", "", year))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CMPL.txt", "\n".join(lines) + "\n")
    return buf.getvalue()


def _write(path: Path, data: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def earlier_artifact(directory: Path, blob: bytes, run_id: int = RUN_ID, *, n: int = 150, seed: int = 1,
                     parts: int = 6) -> dict[str, Any]:
    """A stand-in for an earlier run's plan artifact under ``directory/<run id>/`` (see the module docstring);
    returns the exclusion stand-in."""
    pack = loader.load_pack(ROOT / NHTSA_PACK)
    data, record = goldlabels.build_labels("nhtsa", NHTSA_PACK, n, seed, fetch=lambda: blob)
    questions_bytes, questions = lab_j1.build_questions(pack, data, seed=seed, parts=parts)
    base = directory / str(run_id)
    files = {
        "prereg/j1/labels.jsonl": _write(base / "prereg" / "j1" / "labels.jsonl", data),
        "prereg/j1/labels.json": _write(base / "prereg" / "j1" / "labels.json",
                                        (canonical_dumps(record) + "\n").encode("utf-8")),
        "prereg/j1/questions.jsonl": _write(base / "prereg" / "j1" / "questions.jsonl", questions_bytes),
        "prereg/j1/questions.json": _write(base / "prereg" / "j1" / "questions.json",
                                           (canonical_dumps(questions) + "\n").encode("utf-8")),
    }
    manifest = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": "0" * 64, "files": dict(sorted(files.items())),
                "e1": None, "x1": None, "e2": None, "j1": {"prereg": "prereg/j1/prereg.json"}, "l1": None,
                "j2": None}
    _write(base / "prereg" / "prereg.json", (canonical_dumps(manifest) + "\n").encode("utf-8"))
    sha = files["prereg/j1/labels.jsonl"]["sha256"]
    return {"run_id": run_id, "artifact": f"lab-plan-{run_id}-1", "file": LABELS_FILE, "sha256_prefix": sha[:12],
            "rebuild_sha256_prefix": sha[:12]}


def write_data(out: Path, *, seed: int = 1, per_make: int = 60) -> Path:
    """The three things the dry run takes (see the module docstring); returns ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    blob = archive(per_make, seed)
    (out / "complaints.zip").write_bytes(blob)
    stand_in = earlier_artifact(out / "earlier", blob)
    (out / "stand-in.json").write_text(json.dumps(stand_in, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.j2_data", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--per-make", type=int, default=60)
    args = p.parse_args(argv)
    out = write_data(Path(args.out), seed=args.seed, per_make=args.per_make)
    print(f"wrote {out} (synthetic: every value invented; the earlier artifact stands in for no real run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
