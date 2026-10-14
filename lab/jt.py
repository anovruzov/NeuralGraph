"""Accuracy test A003, build 1 (lab experiment ``jt``): the term run, which drafts the term set the judging run reads.

    python -m lab.jt draft --export FILE --out DIR
    python -m lab.jt verify --prereg FILE --run-id ID --runs-dir DIR

``docs/collective/A003/CHOICE-A003.md`` (amended C1 to C32 before any build) is the rule and ``BUILD-A003.md`` the
build note; "The terms: what they are, where they come from, how they are pinned" is the part this module carries out.
It changes no pinned file: the drafter, its check and its data files are the onboard package's
(``mycelic.collective.onboard``), called unchanged with :func:`~mycelic.collective.onboard.draft.load_params` and no
argument; the export is built by the vehicle replays' exporter through ``tools/onboard/fetch_nhtsa.py``, loaded by path
inside :func:`pooled_export` only (C29), never at import, and ``sys.path`` is put back after the load.

**The split** (items 1 to 3). :data:`CUTOFF` is ``NHTSA_WINDOW[0]`` (2023-01-01, the first day of the judged window);
the drafting window is :data:`FROM` (2020-01-01, D002's training window's first day) to the day before the cutoff.
:func:`pooled_export` builds, for each of :data:`MAKES` in order, the rows ``nhtsa_export.complaints`` keeps over that
window with every top-level component name as filed (``fetch_nhtsa._Every``) and the ``reporter`` column holding each
row's own complaint number (``fetch_nhtsa.REPORTER``), drops a row whose ``odino`` an earlier make already gave (a
complaint filed under two makes, C28; the count is kept), writes the rows as one CSV of D002's seven columns
(:data:`COLUMNS`, ``fetch_nhtsa.rows_csv``) and parses it with ``onboard.exports.parse_export``, which must read back
every pooled row (none rejected), else :class:`JtError`. No row received on or after the cutoff is in it. The drafting
block (C4, C14): the rows, the sha256 of their sorted record ids (the bytes
``lab.j2labels.excluded_jsonl`` writes for them, which are ``prereg/jt/drafting_refs.jsonl``), the narratives' file
``prereg/jt/drafting_text.jsonl`` (one ``{"sha256"}`` line per distinct non-empty folded narrative, sorted: hashes, no
text), ``straddling`` (the drafting rows whose ``odino`` has a row of the six makes received on or after the cutoff in
the same archive) and the earliest and latest received date.

**The draft and the check** (items 4 and 5, C11). ``python -m lab.jt draft`` is the subprocess ``lab.prereg`` runs
under its step runner with :data:`DRAFT_TIMEOUT_S`: ``draft_export`` with the roles of :data:`ROLES` (D002's NHTSA
roles), the window and ``load_params()``; the drafted pack P_draft written by ``write_pack``; ``check_pack`` against
the same export; the term set T (:func:`term_set`); its own peak resident memory (``resource.getrusage``). Exit 0 when
the check passed, :data:`EXIT_FLOOR` when it failed, :data:`EXIT_REFUSED` when the drafter refused the export,
:data:`EXIT_MEMORY` on a ``MemoryError`` and :data:`EXIT_CRASH` on any other error (its class name on stderr, never its
message, which could quote the export). No code the draft returns is 1, Python's own exit for an uncaught exception,
so a crash is never read as a failed check. Everything is written to a scratch directory, never under the plan's.

**The term set** (items 6 and 7, C18, C21, C22). T has one entry per specific predicate of the hand pack (26), in
predicate id order: the NHTSA spelling the hand value map sends to its code, the drafted predicate whose value map
holds that spelling (a specific one; null otherwise), its corpus records and its learned lexicon in the drafter's
order (placeholders left out; the empty list for an unreached predicate). :func:`terms_document` is ``terms.json``,
canonical JSON whose sha256 is the term set's: the cutoff, the window, the makes, the archive, the export and drafting
blocks, ``draft.summarise``'s counts, P_draft's and P_D's hashes apart, the hand pack's, the check's result by check
(counts only), the onboard code hash, the two data files' sha256 and the code hash of this module (C29), and T.

**P_D** (item 9, C21). :func:`drafted_pack` copies the hand pack byte for byte and rewrites only ``vocabulary.json``:
each specific predicate's ``en`` lexicon becomes T's list, or ``draft.placeholder(<id>, ids)`` (the onboard template's
ids) when it is empty; ``unknown_or_other`` keeps its hand lexicon. It is loaded with ``loader.load_pack_dir``.

**The unit** (item 10). ``python -m lab.jt verify`` re-hashes ``terms.json`` against the preregistration's pin,
checks this module's code hash and the hand pack's hashes against the file, rebuilds P_D and checks its four hashes
against the pinned ones, checks the two drafting files against the file, writes ``verify.json`` (``kind:
lab_jt_verify``) under ``<runs-dir>/jt/<run id>/`` and exits 0 when every check holds, 1 otherwise, 2 on a usage
problem. It makes no model call.

Nothing here prints a term, a category spelling, a narrative, a record id, a state or a vehicle: the console gets
counts and hashes (:func:`console_lines`), the terms live in ``prereg/jt/terms.json`` and P_draft in the plan
artifact only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import resource
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective.experiments.common import code_hash as common_code_hash
from mycelic.collective.experiments.common import write_json_atomic
from mycelic.collective.jsonio import StrictJsonError, canonical_dumps, strict_load
from mycelic.collective.onboard.check import check_pack
from mycelic.collective.onboard.draft import (DEFAULTS, LANG_DIR, Draft, DraftError, Window, draft_export,
                                              load_params, load_template, load_written, parse_window, placeholder,
                                              write_pack)
from mycelic.collective.onboard.exports import Export, parse_export
from mycelic.collective.onboard.roles import Roles, roles_from_json
from mycelic.collective.packs.canonical import folded
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack_dir

from . import ROOT
from .goldlabels import NHTSA_MAKES, NHTSA_PACK, NHTSA_WINDOW

CLI = "lab.jt"
KIND = "jt"
EXPERIMENT = "JT"
SCHEMA_VERSION = 1
CUTOFF = NHTSA_WINDOW[0]                                        # item 1: the first day of the pinned window
CUTOFF_ISO = f"{CUTOFF[:4]}-{CUTOFF[4:6]}-{CUTOFF[6:]}"
FROM = "2020-01-01"                                             # D002-settings.json, arms.nhtsa.train's first day
LAST_DAY = (date.fromisoformat(CUTOFF_ISO) - timedelta(days=1)).isoformat()
WINDOW = (FROM, LAST_DAY)
MAKES = NHTSA_MAKES                                             # item 3: the draw's six makes, verbatim
PACK = NHTSA_PACK                                               # the hand pack
LANGUAGE = "en"
# item 3: D002's seven columns in D002's order (D002-settings.json, arms.nhtsa.columns)
COLUMNS = ("odino", "state", "received", "components[]", "vehicle", "summary", "reporter")
# item 4: D002's NHTSA roles (D002-settings.json, arms.nhtsa.roles), vehicle and reporter forbidden
ROLES: dict[str, Any] = {"record_id": "odino", "site": "state", "date": "received", "narrative": "summary",
                         "category": "components[]", "entities": [], "reporter": None,
                         "forbidden": ["reporter", "vehicle"]}
REPORTER_COLUMN = "reporter"                                    # fetch_nhtsa.REPORTER
DRAFT_PACK_ID = "nhtsa_pooled"                                  # P_draft's pack id
FETCH_SCRIPT = "tools/onboard/fetch_nhtsa.py"
DOWNLOAD_CODE = ("tools/onboard/fetch_nhtsa.py", "tools/market/nhtsa_export.py", "tools/market/vehicle_pack.py")
ONBOARD_DIR = "mycelic/collective/onboard"
DATA_FILES = {"defaults": DEFAULTS, "lang_en": LANG_DIR / f"{LANGUAGE}.json"}
CODE_FILES = ("lab/jt.py",)                                     # C29: the code hash terms.json records
DRAFT_TIMEOUT_S = 900                                           # C11: below the prereg step's 20 minutes
HAND_SPECIFIC = 26                                              # item 6: the hand pack's specific predicates
HASH_KEYS = ("config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash")
JT_DIR = "prereg/jt"
TERMS_FILE = f"{JT_DIR}/terms.json"
DRAFTING_REFS_FILE = f"{JT_DIR}/drafting_refs.jsonl"
DRAFTING_TEXT_FILE = f"{JT_DIR}/drafting_text.jsonl"
CHECK_FILE = f"{JT_DIR}/check.json"
PREREG_FILE = f"{JT_DIR}/prereg.json"
PACK_DIR = f"{JT_DIR}/pack"
TERMS_KIND = "lab_jt_terms"
PREREG_KIND = "lab_jt_prereg"
DRAFT_KIND = "lab_jt_draft"
VERIFY_KIND = "lab_jt_verify"
DRAFT_RESULT = "draft.json"
VERIFY_RESULT = "verify.json"
VERIFY_CHECKS = ("terms_sha256", "jt_code_hash", "hand_hashes", "drafting_files", "p_d_hashes")
EXIT_OK = 0
EXIT_NOT_VERIFIED = 1           # the unit: the term set did not verify
EXIT_USAGE = 2
EXIT_REFUSED = 3                # the drafter refused the export: JT_DRAFT_FAILED
EXIT_MEMORY = 4                 # a MemoryError: JT_DRAFT_FAILED
EXIT_FLOOR = 5                  # the check failed: JT_FLOOR_FAILED (never 1, Python's exit for an uncaught exception)
EXIT_CRASH = 6                  # any other error in the draft: JT_DRAFT_FAILED
_DATE = re.compile(r"[0-9]{8}", re.ASCII)


class JtError(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


# --------------------------------------------------------------------------------------------------- settings

def roles() -> Roles:
    """Item 4's roles, through the onboard package's own reader."""
    return roles_from_json({"language": LANGUAGE, "roles": ROLES})


def window() -> Window:
    """The drafting window, 2020-01-01 to the day before the cutoff, as the drafter reads it."""
    return parse_window(*WINDOW)


def _ymd(iso: str) -> str:
    return iso.replace("-", "")


def _iso(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_hash() -> str:
    """C29: the code hash of ``lab/jt.py`` (``common.code_hash`` over :data:`CODE_FILES`)."""
    return common_code_hash([ROOT / rel for rel in CODE_FILES], ROOT)


def onboard_code_files() -> list[str]:
    """Item 4: every file of the onboard package (code and data, ``__pycache__`` aside) and the download code."""
    base = ROOT / ONBOARD_DIR
    files = [p.relative_to(ROOT).as_posix() for p in sorted(base.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    return sorted({*files, *DOWNLOAD_CODE})


def onboard_code_hash() -> str:
    return common_code_hash([ROOT / rel for rel in onboard_code_files()], ROOT)


def data_sha256() -> dict[str, str]:
    """The sha256 of ``data/defaults.json`` and ``data/lang/en.json``."""
    return {name: _sha(Path(path).read_bytes()) for name, path in sorted(DATA_FILES.items())}


# --------------------------------------------------------------------------------------------------- the export

IMPORTED_BY_FETCH = ("nhtsa_export", "vehicle_pack")     # what fetch_nhtsa imports by bare name through sys.path


def _fetch_module() -> ModuleType:
    """``tools/onboard/fetch_nhtsa.py``, loaded by path (``tools`` is not a package). Loading it imports the vehicle
    replays' exporter through ``sys.path``; the path is put back after the load, and the modules it imported by bare
    name (:data:`IMPORTED_BY_FETCH`) leave ``sys.modules`` again unless they were there before (C29): the loaded
    module keeps its own references, and no later bare ``import nhtsa_export`` in the process finds them."""
    saved = list(sys.path)
    before = {name for name in IMPORTED_BY_FETCH if name in sys.modules}
    try:
        spec = importlib.util.spec_from_file_location("lab_jt_fetch_nhtsa", ROOT / FETCH_SCRIPT)
        if spec is None or spec.loader is None:
            raise JtError("the download module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
        for name in IMPORTED_BY_FETCH:
            if name not in before:
                sys.modules.pop(name, None)
    return module


@dataclass(frozen=True)
class Pooled:
    """The pooled drafting export (item 3) and what the term run prints of it."""

    export: Export
    data: bytes                         # the CSV bytes the drafter reads (scratch only, never uploaded)
    rows_by_make: dict[str, int]        # rows kept per make, after the cross-make drop
    counts_by_make: dict[str, dict[str, int]]   # nhtsa_export.complaints' counts per make
    duplicates: int                     # C28: rows dropped because an earlier make gave their odino
    straddling: int                     # C4: rows whose odino has a row received on or after the cutoff
    earliest: str | None
    latest: str | None


def pooled_export(blob: bytes) -> Pooled:
    """Item 3: the six makes' rows over the drafting window, one CSV, parsed as the drafter reads it (see the module
    docstring)."""
    fetch = _fetch_module()
    exporter = fetch.nhtsa_export
    first, last = _ymd(FROM), _ymd(LAST_DAY)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    kept: dict[str, int] = {}
    counts: dict[str, dict[str, int]] = {}
    dropped = 0
    for make in MAKES:
        made, made_counts = exporter.complaints(exporter._rows(blob), make, first, last, fetch._Every(),
                                                fetch.REPORTER)
        counts[make] = dict(made_counts)
        kept[make] = 0
        for row in made:
            if row["odino"] in seen:
                dropped += 1
                continue
            seen.add(row["odino"])
            rows.append(row)
            kept[make] += 1
    later: set[str] = set()
    for row in exporter._rows(blob):
        day = exporter._field(row, exporter.C_LDATE)
        if (exporter._field(row, exporter.C_MAKE).upper() in MAKES and _DATE.fullmatch(day) is not None
                and day >= CUTOFF):
            later.add(exporter._field(row, exporter.C_ODINO))
    data = fetch.rows_csv(rows, list(COLUMNS))
    export = parse_export(data)
    if any(export.rejected.values()) or len(export) != len(rows):
        # the drafting files and the row count are read from the parsed export, so it must hold every pooled row:
        # then they are the rows nhtsa_export.complaints built, as the judging run's rebuild reads them (C4)
        raise JtError("the parsed export does not hold every pooled row")
    received = sorted(row["received"] for row in rows)
    return Pooled(export=export, data=data, rows_by_make=kept, counts_by_make=counts, duplicates=dropped,
                  straddling=sum(1 for odino in seen if odino in later),
                  earliest=_iso(received[0]) if received else None, latest=_iso(received[-1]) if received else None)


def drafting_refs_bytes(export: Export) -> bytes:
    """C4: ``prereg/jt/drafting_refs.jsonl``, one ``{"record_ref"}`` line per row of the drafting export, sorted: the
    bytes ``lab.j2labels.excluded_jsonl`` writes (its line format, written out here so that ``lab.jt`` imports no
    judging module)."""
    refs = sorted({ref for ref in (export.value(r, ROLES["record_id"]) for r in range(len(export))) if ref})
    return "".join(canonical_dumps({"record_ref": ref}) + "\n" for ref in refs).encode("utf-8")


def narrative_hashes(export: Export) -> list[str]:
    """The sha256 of every distinct non-empty folded narrative (``packs.canonical.folded``) of the export, sorted."""
    out = set()
    for r in range(len(export)):
        text = export.value(r, ROLES["narrative"])
        f = folded(text) if isinstance(text, str) else ""
        if f:
            out.add(_sha(f.encode("utf-8")))
    return sorted(out)


def drafting_text_bytes(export: Export) -> bytes:
    """C4: ``prereg/jt/drafting_text.jsonl``, one ``{"sha256"}`` line per distinct non-empty folded narrative,
    sorted: hashes, no text."""
    return "".join(canonical_dumps({"sha256": h}) + "\n" for h in narrative_hashes(export)).encode("utf-8")


def drafting_block(pooled: Pooled, refs_bytes: bytes, text_bytes: bytes) -> dict[str, Any]:
    """C4's figures: the rows, the sha256 of the sorted record ids (equal, by construction, to the refs file's), the
    two files' sha256, the distinct narratives and ``straddling``."""
    return {"rows": len(pooled.export), "refs_sha256": _sha(refs_bytes), "refs_file_sha256": _sha(refs_bytes),
            "text_file_sha256": _sha(text_bytes), "narratives": text_bytes.count(b"\n"),
            "straddling": pooled.straddling}


def export_block(pooled: Pooled) -> dict[str, Any]:
    """Item 3's figures: the rows per make and pooled, the cross-make duplicates dropped, the received dates."""
    return {"columns": list(COLUMNS), "rows": len(pooled.export), "rows_by_make": dict(pooled.rows_by_make),
            "complaints_by_make": {make: pooled.counts_by_make[make].get("complaints", 0) for make in MAKES},
            "cross_make_duplicates": pooled.duplicates, "earliest_received": pooled.earliest,
            "latest_received": pooled.latest, "rejected": dict(pooled.export.rejected)}


# --------------------------------------------------------------------------------------------------- the term set

def hand_pack() -> FrozenPack:
    return load_pack_dir(ROOT / PACK)


def hand_spellings(hand: FrozenPack) -> dict[str, str]:
    """Each specific hand predicate's NHTSA spelling: the value-map key the hand pack sends to its code."""
    value_map = hand.mapping()["codes"][0]["value_map"] or {}
    out: dict[str, str] = {}
    for spelling, code in sorted(value_map.items()):
        entry = hand.codes[code]
        if entry.specific:
            out[entry.predicate] = spelling
    return dict(sorted(out.items()))


def term_set(d: Draft, hand: FrozenPack) -> list[dict[str, Any]]:
    """Item 6: T, one entry per specific hand predicate in id order (see the module docstring)."""
    drafted = {code: pid for pid, code in d.plan.codes.items()}        # specific drafted predicates only
    entries = []
    for predicate, spelling in hand_spellings(hand).items():
        pid = drafted.get(d.plan.value_map.get(spelling, ""))
        entries.append({"predicate": predicate, "spelling": spelling, "drafted_id": pid,
                        "corpus_records": d.plan.counts[pid] if pid is not None else None,
                        "terms": list(d.learned[pid]) if pid is not None else []})
    if len(entries) != HAND_SPECIFIC:
        raise JtError("the hand pack does not have its twenty-six specific predicates")
    return entries


def term_counts(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Item 11: the hand predicates reached (a drafted predicate holds their spelling), the terms per reached
    predicate (min, median as the drafter's summary takes it, the upper middle, max, total) and the entries left
    empty."""
    sizes = sorted(len(e["terms"]) for e in entries if e["drafted_id"] is not None)
    return {"hand_predicates": len(entries), "reached": len(sizes),
            "empty": sum(1 for e in entries if not e["terms"]),
            "terms_min": sizes[0] if sizes else None, "terms_median": sizes[len(sizes) // 2] if sizes else None,
            "terms_max": sizes[-1] if sizes else None, "terms_total": sum(sizes)}


def draft_counts(d: Draft) -> dict[str, Any]:
    """C22: ``draft.summarise``'s counts, never a label, spelling or term."""
    f = d.facts
    removed = f["refusal"]
    return {"export_rows": f["export"]["rows"], "date_format": f["dates"]["format"],
            "dates_rejected": f["dates"]["rejected"], "training_rows": f["training"]["rows"],
            "without_narrative": f["training"]["without_narrative"], "corpus": f["training"]["corpus"],
            "categories_passing_floor": len(f["categories"]["passing_floor"]),
            "categories_below_floor": f["categories"]["below_floor"],
            "specific_predicates": f["categories"]["specific"], "other_categories": f["categories"]["other"],
            "refused_categories": f["categories"]["refused"], "other_bucket_rows": f["other_bucket_rows"],
            "other_bucket_share": f["other_bucket_share"], "rows_without_specific": f["rows_without_specific"],
            "terms_candidates": f["terms"]["candidates"], "terms_eligible": f["terms"]["eligible"],
            "terms_refused": f["terms"]["refused"], "terms_total": f["terms"]["total"],
            "placeholders": f["placeholders"],
            "refusal": {k: removed[k] for k in ("categories", "rows", "share", "with_minimum", "terms", "assignable",
                                                "within_cap")}}


def check_counts(check: Mapping[str, Any]) -> dict[str, Any]:
    """Item 5: the check's result by check, counts and booleans only (never a string a failing pack might hold)."""
    checks = check.get("checks") if isinstance(check.get("checks"), dict) else {}
    return {"passed": check.get("passed") is True,
            "checks": {name: {k: v for k, v in sorted(block.items()) if isinstance(v, (bool, int, float))}
                       for name, block in sorted(checks.items()) if isinstance(block, dict)}}


# --------------------------------------------------------------------------------------------------- P_D

def drafted_vocabulary(hand_dir: Path, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Item 9: the hand pack's ``vocabulary.json`` with each specific predicate's ``en`` lexicon replaced by T's list,
    or by ``draft.placeholder(<id>, ids)`` when the list is empty; every other key as the hand pack has it."""
    vocab = json.loads((Path(hand_dir) / "vocabulary.json").read_text(encoding="utf-8"))
    ids = load_template()["ids.json"]
    for entry in entries:
        lexicon = vocab["predicates"][entry["predicate"]]["lexicon"]
        lexicon[LANGUAGE] = list(entry["terms"]) or [placeholder(entry["predicate"], ids)]
    return vocab


def drafted_pack(hand_dir: str | Path, entries: Sequence[Mapping[str, Any]], out_dir: str | Path) -> FrozenPack:
    """Item 9, C21: P_D in ``out_dir`` (new or empty): every hand pack file byte for byte but ``vocabulary.json``,
    loaded by ``loader.load_pack_dir``."""
    hand_dir, out_dir = Path(hand_dir), Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise JtError("P_D's directory is not empty")
    for path in sorted(hand_dir.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            target = out_dir / path.relative_to(hand_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    vocab = drafted_vocabulary(hand_dir, entries)
    (out_dir / "vocabulary.json").write_text(json.dumps(vocab, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_pack_dir(out_dir)


def p_d_hashes(entries: Sequence[Mapping[str, Any]], hand_dir: str | Path | None = None) -> dict[str, str]:
    """P_D rebuilt in a scratch directory from the hand pack and T; its four loader hashes."""
    with tempfile.TemporaryDirectory(prefix="lab-jt-pd-") as tmp:
        pack = drafted_pack(hand_dir if hand_dir is not None else ROOT / PACK, entries, Path(tmp) / "pack")
        return pack.hashes()


# --------------------------------------------------------------------------------------------------- terms.json

def terms_document(*, archive: bytes, pooled: Pooled, refs_bytes: bytes, text_bytes: bytes,
                   draft: Mapping[str, Any], p_d: Mapping[str, str], hand: Mapping[str, str]) -> dict[str, Any]:
    """Item 7: ``terms.json``'s document. ``draft`` is the draft subprocess's ``draft.json``."""
    return {"kind": TERMS_KIND, "schema_version": SCHEMA_VERSION, "cutoff": CUTOFF_ISO, "window": list(WINDOW),
            "makes": list(MAKES), "language": LANGUAGE, "roles": dict(ROLES),
            "archive": {"sha256": _sha(archive), "bytes": len(archive)},
            "export": export_block(pooled), "drafting": drafting_block(pooled, refs_bytes, text_bytes),
            "draft": dict(draft["counts"]),
            "p_draft": {"id": DRAFT_PACK_ID, "hashes": dict(draft["p_draft"]["hashes"]),
                        "files": dict(draft["p_draft"]["files"])},
            "p_d": {"hashes": dict(p_d)}, "hand": {"pack": PACK, "hashes": dict(hand)},
            "check": check_counts(draft["check"]),
            "code": {"onboard_code_hash": onboard_code_hash(), "onboard_code_files": len(onboard_code_files()),
                     "data_sha256": data_sha256(), "jt_code_hash": code_hash()},
            "term_counts": term_counts(draft["terms"]), "terms": list(draft["terms"])}


def document_bytes(doc: Mapping[str, Any]) -> bytes:
    """Canonical JSON (sorted keys, ``jsonio.canonical_dumps``) and a final newline: the bytes the sha256 is over."""
    return (canonical_dumps(doc) + "\n").encode("utf-8")


def prereg_doc(terms: Mapping[str, Any], terms_sha256: str, refs_bytes: bytes, text_bytes: bytes,
               *, code_commit: str, code_dirty: Any) -> dict[str, Any]:
    """``prereg/jt/prereg.json``: what the unit verifies against (every hash, P_draft's and P_D's apart, C21)."""
    return {"kind": PREREG_KIND, "schema_version": SCHEMA_VERSION, "terms": TERMS_FILE, "terms_sha256": terms_sha256,
            "drafting_refs": DRAFTING_REFS_FILE, "drafting_refs_sha256": _sha(refs_bytes),
            "drafting_text": DRAFTING_TEXT_FILE, "drafting_text_sha256": _sha(text_bytes),
            "cutoff": CUTOFF_ISO, "window": list(WINDOW), "makes": list(MAKES), "hand": dict(terms["hand"]),
            "p_draft": dict(terms["p_draft"]["hashes"]), "p_d": dict(terms["p_d"]["hashes"]),
            "jt_code_hash": terms["code"]["jt_code_hash"], "onboard_code_hash": terms["code"]["onboard_code_hash"],
            "code_commit": code_commit, "code_dirty": code_dirty}


def console_lines(terms: Mapping[str, Any], terms_sha256: str) -> list[str]:
    """The term run's fixed console lines (item 11): counts, dates and hashes, never a term, a spelling, a
    narrative, a record id, a state or a vehicle. The last is the one fixed line ``jt terms: ... terms_sha256``."""
    e, dr, d, c = terms["export"], terms["drafting"], terms["draft"], terms["term_counts"]
    refusal = d["refusal"]
    checks = terms["check"]["checks"]

    def num(value: Any) -> str:
        return "none" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)

    hashes = " ".join(f"p_draft_{k} {terms['p_draft']['hashes'].get(k)}" for k in HASH_KEYS)
    pd = " ".join(f"p_d_{k} {terms['p_d']['hashes'].get(k)}" for k in HASH_KEYS)
    hand = " ".join(f"hand_{k} {terms['hand']['hashes'].get(k)}" for k in HASH_KEYS)
    return [
        f"jt export: archive_sha256 {terms['archive']['sha256']} archive_bytes {terms['archive']['bytes']} rows "
        + " ".join(f"{make}={e['rows_by_make'][make]}" for make in MAKES)
        + f" pooled {e['rows']} cross_make_duplicates {e['cross_make_duplicates']} earliest_received "
          f"{e['earliest_received']} latest_received {e['latest_received']}",
        f"jt drafting: rows {dr['rows']} refs_sha256 {dr['refs_sha256']} drafting_refs_sha256 "
        f"{dr['refs_file_sha256']} drafting_text_sha256 {dr['text_file_sha256']} narratives {dr['narratives']} "
        f"straddling {dr['straddling']}",
        f"jt draft: corpus {d['corpus']} categories_passing_floor {d['categories_passing_floor']} "
        f"specific_predicates {d['specific_predicates']} other_bucket_share {num(d['other_bucket_share'])} "
        f"rows_without_specific {d['rows_without_specific']} terms_candidates {d['terms_candidates']} "
        f"terms_eligible {d['terms_eligible']} placeholders {d['placeholders']} refused_categories "
        f"{refusal['categories']} refused_terms {refusal['terms']} assignable {refusal['assignable']} within_cap "
        f"{refusal['within_cap']}",
        "jt check: passed " + ("yes" if terms["check"]["passed"] else "no") + " " + " ".join(
            f"{name}={'passed' if block.get('passed') else 'failed'}" for name, block in checks.items()),
        f"jt terms: hand_predicates {c['hand_predicates']} reached {c['reached']} empty {c['empty']} terms_min "
        f"{num(c['terms_min'])} terms_median {num(c['terms_median'])} terms_max {num(c['terms_max'])} terms_total "
        f"{c['terms_total']} {hashes} {pd} {hand} onboard_code_hash {terms['code']['onboard_code_hash']} "
        f"defaults_sha256 {terms['code']['data_sha256']['defaults']} lang_en_sha256 "
        f"{terms['code']['data_sha256']['lang_en']} jt_code_hash {terms['code']['jt_code_hash']} terms_sha256 "
        f"{terms_sha256}",
    ]


# --------------------------------------------------------------------------------------------------- the draft step

def run_draft(export_path: Path, out: Path) -> int:
    """``python -m lab.jt draft``: the drafter and the check over the export (see the module docstring); writes
    ``out/pack/`` and ``out/draft.json``."""
    t0 = time.monotonic()
    export = parse_export(Path(export_path).read_bytes())
    params = load_params()
    r, w = roles(), window()
    d = draft_export(export, r, w, DRAFT_PACK_ID, params=params)
    files = write_pack(d.files, out / "pack")
    pack, error = load_written(out / "pack")
    checked = check_pack(out / "pack", export, r, w, params=params)
    entries = term_set(d, hand_pack())
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    doc = {"kind": DRAFT_KIND, "schema_version": SCHEMA_VERSION, "counts": draft_counts(d),
           "p_draft": {"id": DRAFT_PACK_ID, "files": files, "hashes": pack.hashes() if pack is not None else None,
                       "loader": pack is not None, "loader_file": error.split(": ", 1)[0] if error else None},
           "check": checked, "terms": entries, "peak_rss_kib": peak, "wall_s": round(time.monotonic() - t0, 3)}
    write_json_atomic(out / DRAFT_RESULT, doc)
    return EXIT_OK if checked.get("passed") is True and pack is not None else EXIT_FLOOR


def _draft_main(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print("error: --out must be absent or an empty directory", file=sys.stderr)
        return EXIT_USAGE
    out.mkdir(parents=True, exist_ok=True)
    try:
        return run_draft(Path(args.export), out)
    except DraftError:
        print("error: the drafter refused the export", file=sys.stderr)
        return EXIT_REFUSED
    except MemoryError:
        print("error: out of memory", file=sys.stderr)
        return EXIT_MEMORY
    except Exception as exc:        # noqa: BLE001 (any other error: a fixed line, never the message)
        print(f"error: the draft stopped ({type(exc).__name__})", file=sys.stderr)
        return EXIT_CRASH


# --------------------------------------------------------------------------------------------------- the unit

def verify(prereg_path: Path) -> dict[str, Any]:
    """Item 10's unit: every check of :data:`VERIFY_CHECKS` and the figures it read (see the module docstring)."""
    jt_dir = Path(prereg_path).parent
    doc = strict_load(Path(prereg_path).read_bytes())
    if not isinstance(doc, dict) or doc.get("kind") != PREREG_KIND:
        raise JtError("not a term run's preregistration")
    terms_bytes = (jt_dir / Path(TERMS_FILE).name).read_bytes()
    terms = strict_load(terms_bytes)
    if not isinstance(terms, dict) or terms.get("kind") != TERMS_KIND:
        raise JtError("not a term set")
    sha = _sha(terms_bytes)
    checks = {"terms_sha256": sha == doc.get("terms_sha256"),
              "jt_code_hash": code_hash() == doc.get("jt_code_hash") == _get(terms, "code", "jt_code_hash"),
              "hand_hashes": hand_pack().hashes() == _get(terms, "hand", "hashes") == _get(doc, "hand", "hashes")}
    refs = (jt_dir / Path(DRAFTING_REFS_FILE).name).read_bytes()
    text = (jt_dir / Path(DRAFTING_TEXT_FILE).name).read_bytes()
    checks["drafting_files"] = (_sha(refs) == _get(terms, "drafting", "refs_file_sha256")
                                == doc.get("drafting_refs_sha256")
                                and _sha(text) == _get(terms, "drafting", "text_file_sha256")
                                == doc.get("drafting_text_sha256"))
    try:
        rebuilt = p_d_hashes(terms["terms"])
    except (PackError, JtError, KeyError, TypeError):
        rebuilt = None
    checks["p_d_hashes"] = rebuilt is not None and rebuilt == doc.get("p_d") == _get(terms, "p_d", "hashes")
    return {"kind": VERIFY_KIND, "schema_version": SCHEMA_VERSION, "verified": all(checks.values()),
            "checks": checks, "terms_sha256": sha, "p_d": rebuilt, "jt_code_hash": code_hash(),
            "term_counts": terms.get("term_counts")}


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _verify_main(args: argparse.Namespace) -> int:
    run = Path(args.runs_dir) / KIND / args.run_id
    try:
        result = verify(Path(args.prereg))
    except (OSError, StrictJsonError, JtError, KeyError, TypeError):
        result = {"kind": VERIFY_KIND, "schema_version": SCHEMA_VERSION, "verified": False,
                  "checks": dict.fromkeys(VERIFY_CHECKS, False), "terms_sha256": None, "p_d": None,
                  "jt_code_hash": code_hash(), "term_counts": None}
    run.mkdir(parents=True, exist_ok=True)
    write_json_atomic(run / VERIFY_RESULT, result)
    print(f"jt verify: verified {'yes' if result['verified'] else 'no'} "
          + " ".join(f"{k}={'yes' if v else 'no'}" for k, v in result["checks"].items()), flush=True)
    return EXIT_OK if result["verified"] else EXIT_NOT_VERIFIED


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m {CLI}", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draft", help="draft and check the pooled export (the prereg step's subprocess)")
    d.add_argument("--export", required=True)
    d.add_argument("--out", required=True)
    v = sub.add_parser("verify", help="the unit: verify the term set against the preregistration")
    v.add_argument("--prereg", required=True)
    v.add_argument("--run-id", required=True)
    v.add_argument("--runs-dir", required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "draft":
        return _draft_main(args)
    return _verify_main(args)


def entries_terms(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every term of T (tests and guards only: never printed)."""
    return [t for e in entries for t in e["terms"]]


if __name__ == "__main__":
    sys.exit(main())
