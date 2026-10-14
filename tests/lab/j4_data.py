"""Synthetic data for accuracy test A003's tests and the lab's dry run (no NHTSA record is read, no artifact is
downloaded): a complaint archive with a drafting-window block before the cutoff.

    python -m tests.lab.j4_data --out DIR [--seed 1] [--per-make 120] [--negated]

writes ``DIR/complaints.zip``, a complaint archive in NHTSA's flat layout, every value invented:

* **the judged window**: ``tests.lab.j2_data.archive(per_make, seed)``'s rows, unchanged (every complaint received on
  2023-05-10 in one state; at 120 per make about 720 eligible records);
* **the drafting block** (:func:`drafting_lines`): per make, for each of :data:`DRAFT_CATEGORIES`, complaints received
  on seeded days from 2020-01-01 to 2022-12-31 in the five states of :data:`STATES`, each with one component filed as
  ``<CATEGORY>:SUB PART`` and a narrative of two of the category's cue words among filler words: eight categories the
  hand pack maps to a specific predicate (two pairs of them the old and the new name of one component), one it maps
  to ``UNKNOWN OR OTHER``, one it maps to a specific predicate with too few records to be one (the other bucket), and
  one it does not map at all; so the drafter reaches eight of the hand pack's twenty-six specific predicates. Two of
  the eight are made for the term set's harder cases (item 6): the twins of :data:`TWIN_SPLIT` share their four cue
  words, so every cue word goes to the larger twin and the smaller one (:data:`PLACEHOLDER_PREDICATE`, 54 records, a
  specific drafted predicate) learns no term: the twin split, and the placeholder left out of T. The narratives of
  :data:`VEHICLE_WORD_CATEGORY` carry their row's make in lower case, a word of the forbidden ``vehicle`` column that
  the refusal must remove (A6);
* **the boundary rows** (:data:`BOUNDARY_ODINOS`): one complaint received on each of 2019-12-31, 2020-01-01,
  2022-12-31 and 2023-01-01, so the drafting window's first and last day are in the export and the day on either
  side of it is not;
* **the cases of C4 and C28**: :data:`STRADDLING_ODINOS`, two complaints each with a row received in the drafting
  window and a row received on or after the cutoff (the first one's exactly on 2023-01-01): a drafting record and an
  eligible record with one record id; :data:`OTHER_MAKE_ODINO`, a drafting complaint whose later row is filed under
  :data:`OTHER_MAKE`, outside the six makes, which is not straddling; :data:`REPEATED_NARRATIVE`, a drafting
  complaint's narrative that an eligible complaint received in 2023 repeats word for word (:data:`REPEATED_ODINOS`);
  :data:`TWO_MAKES_ODINO`, a drafting complaint filed under two makes (the pooled export keeps the first make's row
  and counts the other).

The archive's one entry carries a fixed timestamp (:data:`ZIP_DATE`), so equal arguments give equal bytes and an
equal sha256. ``lab.dryrun --jt-data DIR`` hands ``DIR/complaints.zip`` to ``lab.prereg --nhtsa-archive``.

``--negated`` writes the judging kind's dry-run variant (:data:`NEGATED_NARRATIVE`): the same rows, but the judged
window's engine narrative says the engine "never stalled" on every other window row that holds it, so on those
records the drafted lexical judge (which applies the pack's negation cues) refutes where the drafted term-presence
judge (which does not) confirms, and the two judges' paired difference is not zero (C5). The default archive is the
term run's dry run's and stays byte for byte as it was; the drafting block, and so the drafted terms, are the same in
both.

For the judging kind ``j4`` (build 2), :func:`write_data` also writes, from the same archive and every value invented:

* ``DIR/earlier/<run id>/prereg/...``, three synthetic earlier artifacts built by the pinned code: the J001-shaped one
  (``j2_data.earlier_artifact``: ``goldlabels.build_labels`` of 150 at seed 1), the A001-shaped one
  (``j3_data.a001_artifact``: ``lab.j2labels.build_labels`` of 150 at seed 2 less J001's refs, its questions and the
  chain of both draws) and the A002-shaped one (:func:`a002_artifact`: ``lab.j2labels.build_labels`` of 150 at seed 3
  less both sets, ``lab.j1.build_questions`` at seed 3 in five parts, ``prereg/j3/`` files and the chain of the three
  draws, 450 lines), each with its manifest ``prereg/prereg.json``;
* ``DIR/stand-in.json``, the three-entry exclude list (:func:`stand_in`) whose prefixes are the synthetic files', so
  every one of the eight sources verifies on this archive; the third entry also carries
  ``questions_sha256_prefix``, the synthetic A002 questions file's prefix, which a stand-in (never a request) hands
  to the check in place of A002's pinned one (C3);
* ``DIR/terms/<run id>/prereg/...``, a synthetic term artifact (:func:`term_artifact`): the term run's files written as
  ``lab.prereg``'s term step writes them, drafted from the archive's pre-cutoff rows by the real drafter through
  ``lab/jt.py`` (``pooled_export``, ``run_draft``, ``p_d_hashes``, ``terms_document``), with its manifest;
* ``DIR/terms-stand-in.json``, the term entry that names it (:func:`term_artifact`'s return: the run, the artifact,
  the file, the term set's full sha256, P_D's ``vocabulary_hash`` and the cutoff).

The synthetic J001 draw holds the two straddling complaints and the A001 draw the repeated narrative's 2023
complaint; the boundary complaint received on 2023-01-01 (:data:`BOUNDARY_ODINOS`) is in no earlier draw and repeats
the narrative of the drafting window's two boundary complaints, so the split removes it by text (C4).
"""
from __future__ import annotations

import argparse
import functools
import io
import json
import random
import shutil
import sys
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import canonical_dumps, sha256_hex
from mycelic.collective.packs import loader

from lab import goldlabels
from lab import j1 as lab_j1
from lab import j2labels
from lab import jt as lab_jt
from lab.goldlabels import NHTSA_MAKES, NHTSA_PACK
from tests.lab import j2_data, j3_data
from tests.lab.j2_data import _write

PER_MAKE = 120
ZIP_DATE = (2024, 1, 1, 0, 0, 0)
STATES = ("TX", "CA", "OH", "FL", "NY")
FILLER = ("driving", "highway", "speed", "noticed", "morning", "traffic", "suddenly", "parking", "street", "warning",
          "mileage", "garage", "commute", "weekend", "evening", "returned", "later", "yesterday")
BRAKE_CUES = ("pedal", "stopping", "braking", "grinding")
# category (as filed), its cue words, complaints per make
DRAFT_CATEGORIES = (
    ("AIR BAGS", ("inflated", "cushion", "curtain", "deployment"), 12),
    ("ENGINE", ("stalled", "misfire", "idling", "revving"), 12),
    ("ENGINE AND ENGINE COOLING", ("coolant", "overheated", "radiator", "thermostat"), 12),
    ("SERVICE BRAKES", BRAKE_CUES, 18),
    ("SERVICE BRAKES, HYDRAULIC", BRAKE_CUES, 9),       # the smaller twin: the larger one's cue words, 54 records
    ("STEERING", ("veered", "pulling", "drifted", "column"), 12),
    ("TIRES", ("tread", "puncture", "sidewall", "blowout"), 12),
    ("ELECTRICAL SYSTEM", ("battery", "dashboard", "flicker", "fuse"), 12),
    ("UNKNOWN OR OTHER", ("strange", "rattle", "odor", "smell"), 6),
    ("SEAT BELTS", ("buckle", "latch", "retractor", "webbing"), 3),
    ("PARKING BRAKE", ("lever", "ratchet", "holding", "rolled"), 9),
)
REACHED = ("air_bags", "electrical_system", "engine", "engine_and_engine_cooling", "service_brakes",
           "service_brakes_hydraulic", "steering", "tires")
TWIN_SPLIT = ("service_brakes", "service_brakes_hydraulic")    # (the larger twin, the smaller): every cue word shared
PLACEHOLDER_PREDICATE = "service_brakes_hydraulic"            # reached, a specific drafted predicate, no term learned
VEHICLE_WORD_CATEGORY = "TIRES"                               # its narratives carry the row's make, in lower case
DRAFT_ODINO_BASE = 7000000
STRADDLING_ODINO = "7900001"                  # its later row is received on the cutoff day itself
STRADDLING_ODINOS = (STRADDLING_ODINO, "7900009")
TWO_MAKES_ODINO = "7900002"
REPEATED_ODINOS = ("7900003", "7900004")      # the drafting complaint and the eligible one that repeats it
REPEATED_NARRATIVE = "The cabin filled with a sweet mist after the dashboard warning came on near the bridge."
# one complaint received on each day around the drafting window's two ends
BOUNDARY_ODINOS = {"2019-12-31": "7900005", "2020-01-01": "7900006", "2022-12-31": "7900007", "2023-01-01": "7900008"}
OTHER_MAKE = "TOYOTA"                         # not one of the six makes
OTHER_MAKE_ODINO = "7900010"                  # a drafting complaint whose later row is filed under OTHER_MAKE
FROM_DAY, LAST_DAY = "2020-01-01", "2022-12-31"   # the drafting window's two ends, each held by a boundary row
ODINO_FIELD = 1
# the negated variant (:func:`archive` with ``negated``, the judging kind's dry runs): the judged window's engine
# narrative, on every other row that holds it, states its drafted cue word only as negated, so the drafted
# lexical judge (the pack's negation cues) and the drafted term-presence judge (no negation handling) answer apart
NEGATED_NARRATIVE = ("The engine stalled on the highway at speed.", "The engine never stalled on the highway at speed.")


def _row(export, odino: str, make: str, comp: str, state: str, day: date, text: str, model: str, year: str) -> str:
    r = [""] * 51
    r[export.C_ODINO], r[export.C_MAKE], r[export.C_MODEL], r[export.C_YEAR] = odino, make, model, year
    r[export.C_COMP], r[export.C_STATE], r[export.C_LDATE] = comp, state, day.strftime("%Y%m%d")
    r[export.C_DESCR], r[export.C_PROD] = text, "V"
    r[50] = "Operator Name Never Exported"
    return "\t".join(r)


def _text(rng: random.Random, cues: tuple[str, ...], extra: str | None = None) -> str:
    first = " ".join(rng.sample(cues, 2) + rng.sample(FILLER, 5) + ([extra] if extra is not None else []))
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
                extra = make.lower() if category == VEHICLE_WORD_CATEGORY else None
                lines.append(_row(export, str(odino), make, f"{category}:SUB PART", STATES[(odino + i) % 5], day,
                                  _text(rng, cues, extra), "MODEL Y", str(2018 + i % 5)))
    # the drafting window's two ends, and the day on either side of them
    for (iso, ref), make in zip(BOUNDARY_ODINOS.items(), ("HONDA", "CHEVROLET", "DODGE", "NISSAN")):
        lines.append(_row(export, ref, make, "STEERING", "TX", date.fromisoformat(iso),
                          "The wheel shook on the ramp and the shop found nothing loose.", "MODEL Y", "2019"))
    # C4: two complaints with rows on both sides of the cutoff, the first one's later row on the cutoff day itself
    for ref, make, first, later in ((STRADDLING_ODINOS[0], "FORD", date(2022, 11, 15), date(2023, 1, 1)),
                                    (STRADDLING_ODINOS[1], "JEEP", date(2021, 3, 3), date(2023, 2, 1))):
        for day in (first, later):
            lines.append(_row(export, ref, make, "ENGINE", "OH", day,
                              "Stalled twice at the light and the misfire light stayed on all week.", "MODEL Y",
                              "2021"))
    # C4: a drafting complaint whose later row is filed under a make outside the six: not straddling
    lines.append(_row(export, OTHER_MAKE_ODINO, "HONDA", "STEERING", "CA", date(2022, 6, 6),
                      "The column clicked when turning left in the lot.", "MODEL Y", "2020"))
    lines.append(_row(export, OTHER_MAKE_ODINO, OTHER_MAKE, "STEERING", "CA", date(2023, 3, 3),
                      "The column clicked when turning left in the lot.", "MODEL Y", "2020"))
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


def _negated(window: list[str]) -> list[str]:
    """The window's rows with :data:`NEGATED_NARRATIVE`'s rewrite on every other row that holds the engine narrative
    (the first, the third, ...), so the variant holds both the plain and the negated narrative."""
    out: list[str] = []
    seen = 0
    for line in window:
        if NEGATED_NARRATIVE[0] in line:
            seen += 1
            if seen % 2 == 1:
                line = line.replace(*NEGATED_NARRATIVE)
        out.append(line)
    return out


@functools.lru_cache(maxsize=None)
def archive(per_make: int = PER_MAKE, seed: int = 1, negated: bool = False) -> bytes:
    """The archive of the module docstring: the window rows of ``j2_data.archive`` and the drafting block, one entry
    with a fixed timestamp. With ``negated`` (the judging kind's dry runs; the default archive, the term run's dry
    run's, is unchanged), the window's engine narrative states its drafted cue word as negated on every other row
    that holds it (:data:`NEGATED_NARRATIVE`): the same records, eligible and drawn alike, only that text differs."""
    with zipfile.ZipFile(io.BytesIO(j2_data.archive(per_make, seed))) as z:
        window = z.read("CMPL.txt").decode("latin-1").splitlines()
    if negated:
        window = _negated(window)
    text = "\n".join([*window, *drafting_lines(seed)]) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("CMPL.txt", date_time=ZIP_DATE), text.encode("latin-1"))
    return buf.getvalue()


# --------------------------------------------------------------------------------------------------- build 2 (j4)

J001_RUN_ID = j3_data.J001_RUN_ID
A001_RUN_ID = j3_data.A001_RUN_ID
A002_RUN_ID = j2_data.RUN_ID + 2           # synthetic run ids: no GitHub run has them
TERMS_RUN_ID = j2_data.RUN_ID + 3
A002_ARTIFACT = f"lab-plan-{A002_RUN_ID}-1"
TERMS_ARTIFACT = f"lab-plan-{TERMS_RUN_ID}-1"
J3_LABELS_FILE = "prereg/j3/labels.jsonl"
J3_CHAIN_FILE = "prereg/j3/excluded.jsonl"
SPLIT_BY_TEXT = (BOUNDARY_ODINOS["2023-01-01"],)    # removed by text from the eligible list less the 450 (C4)


def _manifest(base: Path, files: dict[str, dict[str, Any]], **blocks: Any) -> None:
    manifest = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": "0" * 64,
                "files": dict(sorted(files.items())), "e1": None, "x1": None, "e2": None, "j1": None, "l1": None,
                "j2": None, **blocks}
    _write(base / "prereg" / "prereg.json", (canonical_dumps(manifest) + "\n").encode("utf-8"))


def a002_artifact(directory: Path, blob: bytes, excluded: set[str], run_id: int = A002_RUN_ID, *, n: int = 150,
                  seed: int = 3, parts: int = 5) -> dict[str, Any]:
    """A stand-in for A002's plan artifact under ``directory/<run id>/`` (see the module docstring), built by the
    pinned code: ``lab.j2labels.build_labels`` of ``n`` at ``seed`` less ``excluded`` (J001's and A001's refs),
    ``lab.j1.build_questions`` at ``seed`` in ``parts`` parts and the chain of the three draws; returns the third
    exclude entry, with the synthetic questions file's prefix as ``questions_sha256_prefix``."""
    pack = loader.load_pack(j2_data.ROOT / NHTSA_PACK)
    data, record = j2labels.build_labels(NHTSA_PACK, n, seed, fetch=lambda: blob, excluded=excluded,
                                         mechanisms={"artifact": "verified", "rebuild": "verified"})
    questions_bytes, questions = lab_j1.build_questions(pack, data, seed=seed, parts=parts)
    drawn = {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}
    chain = j2labels.excluded_jsonl(set(excluded) | drawn)
    base = directory / str(run_id)
    files = {
        J3_LABELS_FILE: _write(base / "prereg" / "j3" / "labels.jsonl", data),
        "prereg/j3/labels.json": _write(base / "prereg" / "j3" / "labels.json",
                                        (canonical_dumps(record) + "\n").encode("utf-8")),
        "prereg/j3/questions.jsonl": _write(base / "prereg" / "j3" / "questions.jsonl", questions_bytes),
        "prereg/j3/questions.json": _write(base / "prereg" / "j3" / "questions.json",
                                           (canonical_dumps(questions) + "\n").encode("utf-8")),
        J3_CHAIN_FILE: _write(base / "prereg" / "j3" / "excluded.jsonl", chain),
    }
    _manifest(base, files, j3={"prereg": "prereg/j3/prereg.json"})
    sha = files[J3_LABELS_FILE]["sha256"]
    return {"run_id": run_id, "artifact": f"lab-plan-{run_id}-1", "file": J3_LABELS_FILE, "sha256_prefix": sha[:12],
            "rebuild_seed": seed, "rebuild_sha256_prefix": sha[:12], "chain_file": J3_CHAIN_FILE,
            "chain_sha256_prefix": sha256_hex(chain)[:12],
            "questions_sha256_prefix": files["prereg/j3/questions.jsonl"]["sha256"][:12]}


def refs_in(directory: Path, run_id: int, rel: str) -> set[str]:
    """The refs of a synthetic artifact's labels file."""
    return {r["record_ref"] for r, _ in lab_j1.parse_labels((directory / str(run_id) / rel).read_bytes())}


def stand_in(directory: Path, blob: bytes) -> list[dict[str, Any]]:
    """The three synthetic earlier artifacts under ``directory`` and the three-entry exclude list that names them
    (J001's and A001's as ``j3_data.stand_in`` makes them, then A002's)."""
    first, second = j3_data.stand_in(directory, blob)
    j001 = refs_in(directory, J001_RUN_ID, j2_data.LABELS_FILE)
    a001 = refs_in(directory, A001_RUN_ID, j3_data.J2_LABELS_FILE)
    return [first, second, a002_artifact(directory, blob, j001 | a001)]


def term_artifact(directory: Path, blob: bytes, run_id: int = TERMS_RUN_ID) -> dict[str, Any]:
    """A synthetic term artifact under ``directory/<run id>/`` (see the module docstring): the term run's files as
    ``lab.prereg``'s term step writes them, the draft and its check run in this process by ``lab.jt.run_draft`` (the
    real drafter); returns the term entry that names it."""
    base = directory / str(run_id)
    work = Path(tempfile.mkdtemp(prefix="j4-data-terms-"))
    try:
        pooled = lab_jt.pooled_export(blob)
        refs_bytes = lab_jt.drafting_refs_bytes(pooled.export)
        text_bytes = lab_jt.drafting_text_bytes(pooled.export)
        (work / "export.csv").write_bytes(pooled.data)
        if lab_jt.run_draft(work / "export.csv", work / "draft") != lab_jt.EXIT_OK:
            raise AssertionError("the synthetic draft did not pass its check")
        draft = json.loads((work / "draft" / lab_jt.DRAFT_RESULT).read_text(encoding="utf-8"))
        p_d = lab_jt.p_d_hashes(draft["terms"])
        terms = lab_jt.terms_document(archive=blob, pooled=pooled, refs_bytes=refs_bytes, text_bytes=text_bytes,
                                      draft=draft, p_d=p_d, hand=lab_jt.hand_pack().hashes())
        terms_bytes = lab_jt.document_bytes(terms)
        terms_sha = sha256_hex(terms_bytes)
        shutil.copytree(work / "draft" / "pack", base / lab_jt.PACK_DIR)
    finally:
        shutil.rmtree(work, True)
    _write(base / lab_jt.TERMS_FILE, terms_bytes)
    _write(base / lab_jt.DRAFTING_REFS_FILE, refs_bytes)
    _write(base / lab_jt.DRAFTING_TEXT_FILE, text_bytes)
    _write(base / lab_jt.CHECK_FILE, (canonical_dumps(lab_jt.check_counts(draft["check"])) + "\n").encode("utf-8"))
    prereg = lab_jt.prereg_doc(terms, terms_sha, refs_bytes, text_bytes, code_commit="synthetic", code_dirty=None)
    _write(base / lab_jt.PREREG_FILE, (canonical_dumps(prereg) + "\n").encode("utf-8"))
    files = {path.relative_to(base).as_posix(): {"sha256": sha256_hex(path.read_bytes()),
                                                 "bytes": len(path.read_bytes())}
             for path in sorted((base / "prereg").rglob("*")) if path.is_file()}
    _manifest(base, files, jt={"prereg": lab_jt.PREREG_FILE, "terms_sha256": terms_sha})
    return {"run_id": run_id, "artifact": f"lab-plan-{run_id}-1", "file": lab_jt.TERMS_FILE, "sha256": terms_sha,
            "vocabulary_hash": p_d["vocabulary_hash"], "cutoff": lab_jt.CUTOFF_ISO}


def write_data(out: Path, *, seed: int = 1, per_make: int = PER_MAKE, negated: bool = False) -> Path:
    """``DIR/complaints.zip`` and ``DIR/facts.json`` (the cases' counts the tests read), and for the judging kind the
    three earlier artifacts under ``DIR/earlier/``, ``DIR/stand-in.json``, the term artifact under ``DIR/terms/`` and
    ``DIR/terms-stand-in.json``, all from :func:`archive` (its negated variant with ``negated``); returns ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    blob = archive(per_make, seed, negated)
    (out / "complaints.zip").write_bytes(blob)
    facts = {"per_make": per_make, "seed": seed, "reached": list(REACHED), "straddling": len(STRADDLING_ODINOS),
             "cross_make_duplicates": 1, "earliest_received": FROM_DAY, "latest_received": LAST_DAY,
             "placeholder_predicate": PLACEHOLDER_PREDICATE}
    (out / "facts.json").write_text(json.dumps(facts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    entries = stand_in(out / "earlier", blob)
    (out / "stand-in.json").write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    term = term_artifact(out / "terms", blob)
    (out / "terms-stand-in.json").write_text(json.dumps(term, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.j4_data", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--per-make", type=int, default=PER_MAKE)
    p.add_argument("--negated", action="store_true",
                   help="the judging kind's dry-run variant: the window's engine narrative negates its drafted cue "
                        "word on every other row that holds it (the default archive, the term run's dry run's, is "
                        "unchanged)")
    args = p.parse_args(argv)
    out = write_data(Path(args.out), seed=args.seed, per_make=args.per_make, negated=args.negated)
    print(f"wrote {out} (synthetic: every value invented; the earlier and term artifacts stand in for no real run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
