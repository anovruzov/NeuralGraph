"""Synthetic data for accuracy test A003's tests and the lab's dry run (no NHTSA record is read, no artifact is
downloaded): a complaint archive with a drafting-window block before the cutoff.

    python -m tests.lab.j4_data --out DIR [--seed 1] [--per-make 120]

writes ``DIR/complaints.zip``, a complaint archive in NHTSA's flat layout, every value invented:

* **the judged window**: ``tests.lab.j2_data.archive(per_make, seed)``'s rows, unchanged (every complaint received on
  2023-05-10 in one state; at 120 per make about 720 eligible records);
* **the drafting block** (:func:`drafting_lines`): per make, for each of :data:`DRAFT_CATEGORIES`, complaints received
  on seeded days from 2020-01-01 to 2022-12-31 in the five states of :data:`STATES`, each with one component filed as
  ``<CATEGORY>:SUB PART`` and a narrative of two of the category's cue words among filler words: seven categories the
  hand pack maps to a specific predicate (two of them the old and the new name of one component), one it maps to
  ``UNKNOWN OR OTHER``, one it maps to a specific predicate with too few records to be one (the other bucket), and
  one it does not map at all; so the drafter reaches seven of the hand pack's twenty-six specific predicates;
* **the three cases of C4 and C28**, each once: :data:`STRADDLING_ODINO`, a complaint with a row received in 2022
  and a row received in 2023 (a drafting record and an eligible record with one record id);
  :data:`REPEATED_NARRATIVE`, a drafting complaint's narrative that an eligible complaint received in 2023 repeats
  word for word (:data:`REPEATED_ODINOS`); :data:`TWO_MAKES_ODINO`, a drafting complaint filed under two makes (the
  pooled export keeps the first make's row and counts the other).

The archive's one entry carries a fixed timestamp (:data:`ZIP_DATE`), so equal arguments give equal bytes and an
equal sha256. ``lab.dryrun --jt-data DIR`` hands ``DIR/complaints.zip`` to ``lab.prereg --nhtsa-archive``.
"""
from __future__ import annotations

import argparse
import functools
import io
import json
import random
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

from lab import goldlabels
from lab.goldlabels import NHTSA_MAKES
from tests.lab import j2_data

PER_MAKE = 120
ZIP_DATE = (2024, 1, 1, 0, 0, 0)
STATES = ("TX", "CA", "OH", "FL", "NY")
FILLER = ("driving", "highway", "speed", "noticed", "morning", "traffic", "suddenly", "parking", "street", "warning",
          "mileage", "garage", "commute", "weekend", "evening", "returned", "later", "yesterday")
# category (as filed), its cue words, complaints per make
DRAFT_CATEGORIES = (
    ("AIR BAGS", ("inflated", "cushion", "curtain", "deployment"), 12),
    ("ENGINE", ("stalled", "misfire", "idling", "revving"), 12),
    ("ENGINE AND ENGINE COOLING", ("coolant", "overheated", "radiator", "thermostat"), 12),
    ("SERVICE BRAKES", ("pedal", "stopping", "braking", "grinding"), 12),
    ("STEERING", ("veered", "pulling", "drifted", "column"), 12),
    ("TIRES", ("tread", "puncture", "sidewall", "blowout"), 12),
    ("ELECTRICAL SYSTEM", ("battery", "dashboard", "flicker", "fuse"), 12),
    ("UNKNOWN OR OTHER", ("strange", "rattle", "odor", "smell"), 6),
    ("SEAT BELTS", ("buckle", "latch", "retractor", "webbing"), 3),
    ("PARKING BRAKE", ("lever", "ratchet", "holding", "rolled"), 9),
)
REACHED = ("air_bags", "electrical_system", "engine", "engine_and_engine_cooling", "service_brakes", "steering",
           "tires")
DRAFT_ODINO_BASE = 7000000
STRADDLING_ODINO = "7900001"
TWO_MAKES_ODINO = "7900002"
REPEATED_ODINOS = ("7900003", "7900004")      # the drafting complaint and the eligible one that repeats it
REPEATED_NARRATIVE = "The cabin filled with a sweet mist after the dashboard warning came on near the bridge."
ODINO_FIELD = 1


def _row(export, odino: str, make: str, comp: str, state: str, day: date, text: str, model: str, year: str) -> str:
    r = [""] * 51
    r[export.C_ODINO], r[export.C_MAKE], r[export.C_MODEL], r[export.C_YEAR] = odino, make, model, year
    r[export.C_COMP], r[export.C_STATE], r[export.C_LDATE] = comp, state, day.strftime("%Y%m%d")
    r[export.C_DESCR], r[export.C_PROD] = text, "V"
    r[50] = "Operator Name Never Exported"
    return "\t".join(r)


def _text(rng: random.Random, cues: tuple[str, ...]) -> str:
    first = " ".join(rng.sample(cues, 2) + rng.sample(FILLER, 5))
    second = " ".join(rng.sample(FILLER, 4))
    return first.capitalize() + ". " + second.capitalize() + "."


def _day(rng: random.Random, first: date, last: date) -> date:
    return first + timedelta(days=rng.randrange((last - first).days + 1))


def drafting_lines(seed: int = 1) -> list[str]:
    """The drafting block and the three cases (see the module docstring), as archive lines."""
    export = goldlabels.nhtsa_export()
    rng = random.Random(f"j4-data:{seed}")
    lines: list[str] = []
    odino = DRAFT_ODINO_BASE + 10000 * seed
    for make in NHTSA_MAKES:
        for category, cues, count in DRAFT_CATEGORIES:
            for i in range(count):
                odino += 1
                day = _day(rng, date(2020, 1, 1), date(2022, 12, 31))
                lines.append(_row(export, str(odino), make, f"{category}:SUB PART", STATES[(odino + i) % 5], day,
                                  _text(rng, cues), "MODEL Y", str(2018 + i % 5)))
    # C4: one complaint with rows on both sides of the cutoff
    lines.append(_row(export, STRADDLING_ODINO, "FORD", "ENGINE", "OH", date(2022, 11, 15),
                      "Stalled twice at the light and the misfire light stayed on all week.", "MODEL Y", "2021"))
    lines.append(_row(export, STRADDLING_ODINO, "FORD", "ENGINE", "OH", date(2023, 2, 1),
                      "Stalled twice at the light and the misfire light stayed on all week.", "MODEL Y", "2021"))
    # C28: one complaint filed under two makes in the drafting window
    for make in ("FORD", "JEEP"):
        lines.append(_row(export, TWO_MAKES_ODINO, make, "TIRES", "CA", date(2021, 6, 2),
                          "The tread came apart on both tires after the puncture was patched.", "MODEL Y", "2020"))
    # C4: one narrative repeated across the cutoff, word for word
    lines.append(_row(export, REPEATED_ODINOS[0], "HONDA", "ELECTRICAL SYSTEM", "NY", date(2021, 9, 9),
                      REPEATED_NARRATIVE, "MODEL Y", "2019"))
    lines.append(_row(export, REPEATED_ODINOS[1], "NISSAN", "ELECTRICAL SYSTEM", "TX", date(2023, 7, 7),
                      REPEATED_NARRATIVE, "MODEL Y", "2022"))
    return lines


@functools.lru_cache(maxsize=None)
def archive(per_make: int = PER_MAKE, seed: int = 1) -> bytes:
    """The archive of the module docstring: the window rows of ``j2_data.archive`` and the drafting block, one entry
    with a fixed timestamp."""
    with zipfile.ZipFile(io.BytesIO(j2_data.archive(per_make, seed))) as z:
        window = z.read("CMPL.txt").decode("latin-1").splitlines()
    text = "\n".join([*window, *drafting_lines(seed)]) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("CMPL.txt", date_time=ZIP_DATE), text.encode("latin-1"))
    return buf.getvalue()


def write_data(out: Path, *, seed: int = 1, per_make: int = PER_MAKE) -> Path:
    """``DIR/complaints.zip`` and ``DIR/facts.json`` (the cases' counts the tests read); returns ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "complaints.zip").write_bytes(archive(per_make, seed))
    facts = {"per_make": per_make, "seed": seed, "reached": list(REACHED), "straddling": 1, "cross_make_duplicates": 1}
    (out / "facts.json").write_text(json.dumps(facts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.j4_data", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--per-make", type=int, default=PER_MAKE)
    args = p.parse_args(argv)
    out = write_data(Path(args.out), seed=args.seed, per_make=args.per_make)
    print(f"wrote {out} (synthetic: every value invented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
