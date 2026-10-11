"""Accuracy test A001's fresh draw (``docs/collective/A001/CHOICE-A001.md``, "The data and the draw" as amended A1 to
A4): real public NHTSA complaints of the vehicle pack with an earlier cycle's records excluded, built in the plan job.

The pinned module ``lab/goldlabels.py`` builds the eligible list and samples it in one function with no exclusion
parameter (``nhtsa_lines``), so this module re-implements its eligible build (``goldlabels.py:79-87``) and a test pins
the two equal byte for byte when nothing is excluded (A4). Everything else of the draw is the pinned code's: the
makes, the window, the exporter (``tools/market/nhtsa_export.py`` through ``goldlabels.nhtsa_export``), the pack and
the label-file shape (``e1_extract.read_labels`` reads it, so ``lab.j1`` needs no change).

**Mechanism 1, the artifact** (:func:`earlier_refs`; A1). The plan job downloads one earlier run's plan artifact under
``<earlier plans>/<run id>/`` (``actions/download-artifact`` with ``actions: read``). The request's ``exclude`` entry
names the run, the artifact, the file inside it that lists the records (one of :data:`EXCLUDE_FILES`) and the sha256
prefix that file's bytes must have. The check hashes the file's bytes (the field in ``labels.json`` is not trusted),
cross-checks that sha256 and the file's size against the artifact's manifest ``prereg/prereg.json``, and reads the
refs: for ``prereg/j1/labels.jsonl`` the ``record.record_ref`` of each label line, which must be the same set as the
refs of ``prereg/j1/questions.jsonl``, with ``labels.json`` saying ``source`` ``nhtsa``, ``n`` 150 and ``seed`` 1
(:data:`REBUILD`); for ``prereg/j2/excluded.jsonl`` (a later cycle's chain, A3) one ``{"record_ref"}`` line per ref.
Every read is inside ``try``/``except``; any failure, a missing directory included, is one fixed outcome
(:data:`OUTCOMES`: ``artifact absent``, ``sha256 mismatch``, ``manifest mismatch``, ``refs mismatch``, ``labels
mismatch``, ``malformed``) and a count, never a value, a path or an exception's text. The downloaded directory is
untrusted input: nothing from it is printed or written anywhere but into the exclusion set on the runner.

**Mechanism 2, the rebuild** (:func:`rebuild_refs`; A2). ``lab.goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1,
fetch=<the preregistration's one cached fetch>)`` rebuilds J001's seed-1 draw from today's complaint file; it verifies
only when the labels' sha256 starts with :data:`J001_LABELS_SHA256_PREFIX` (RESULTS run 9), the chain's oldest link.
A different sha256 means the complaint file changed since J001: the mechanism fails, its prefix is printed beside the
pinned one, and nothing stops by itself (A2: one mechanism verifying suffices).

**The draw** (:func:`build_labels`). The eligible list less the excluded refs, ``random.Random("nhtsa:<seed>")
.sample`` of ``n``, sorted by ``record_ref``, in ``goldlabels``' label-file shape; the labels record carries
``available`` (the eligible count before exclusion, as ``goldlabels.py:143``), and an ``exclusion`` block: the
excluded count, the eligible count after exclusion, the overlap between the draw and the excluded set (0, else
:class:`J2LabelsError`), the mechanisms' outcomes and the complaint file's sha256. Fewer than ``n`` eligible unexcluded
records refuse with a fixed message. :func:`excluded_jsonl` is the carried-forward file of A3: every excluded ref and
the draw's, sorted, refs only.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from mycelic.collective.jsonio import StrictJsonError, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs import loader
from mycelic.collective.packs.connector import map_rows

from . import goldlabels
from . import j1 as lab_j1
from .goldlabels import NHTSA_MAKES, NHTSA_PACK, NHTSA_WINDOW, PUBLIC_SITE, GoldLabelsError

# the chain's oldest link: J001's seed-1 n-150 labels (RESULTS run 9; CHOICE-J001, Runs), rebuilt by mechanism 2
J001_LABELS_SHA256_PREFIX = "bc6d092d8bca"
REBUILD = {"source": "nhtsa", "n": 150, "seed": 1}
EXCLUDE_FILES = ("prereg/j1/labels.jsonl", "prereg/j2/excluded.jsonl")
J1_LABELS_FILE, J2_EXCLUDED_FILE = EXCLUDE_FILES
MANIFEST_FILE = "prereg/prereg.json"
J1_QUESTIONS_FILE = "prereg/j1/questions.jsonl"
J1_LABELS_RECORD = "prereg/j1/labels.json"
VERIFIED = "verified"
OUTCOMES = (VERIFIED, "artifact absent", "sha256 mismatch", "manifest mismatch", "refs mismatch", "labels mismatch",
            "malformed")
MECHANISMS = ("artifact", "rebuild")


class J2LabelsError(ValueError):
    """A problem with the draw; ``problem`` is a fixed sentence and a count, never a value."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


# --------------------------------------------------------------------------------------------------- the eligible list

def nhtsa_rows(blob: bytes, pack_id: str = NHTSA_PACK) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The exporter's rows for :data:`NHTSA_MAKES` in :data:`NHTSA_WINDOW`, as ``goldlabels.build_labels`` makes
    them (``goldlabels.py:132-140``), and the complaints kept per make."""
    export = goldlabels.nhtsa_export()
    categories, _ = export.pack_categories(goldlabels.ROOT / pack_id)
    rows: list[dict[str, Any]] = []
    kept: dict[str, int] = {}
    for make in NHTSA_MAKES:
        made, counts = export.complaints(export._rows(blob), make, *NHTSA_WINDOW, categories)
        rows += made
        kept[make] = counts.get("complaints", 0)
    return rows, kept


def eligible_records(pack: loader.FrozenPack, export_rows: Sequence[Mapping[str, Any]]
                     ) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], dict[str, Any]]:
    """The eligible ``(record, gold)`` pairs sorted by ``record_ref``, exactly as ``goldlabels.nhtsa_lines`` builds
    them (``goldlabels.py:78-87``): ``map_rows`` with the pack's ``mapping``, not synthetic; a non-blank narrative,
    exactly one vehicle, at least one specific component code; codes emptied and ``site`` :data:`PUBLIC_SITE`; the
    gold one claim per specific code. The counts are ``nhtsa_lines``' (``mapped``, ``rejected``, ``eligible``)."""
    mapped = map_rows(export_rows, pack, mapping="mapping", synthetic=False)
    eligible = []
    for r in mapped.records:
        vehicles = r["entities"].get("vehicle", [])
        preds = sorted({pack.codes[c].predicate for c in r["codes"] if c in pack.codes and pack.codes[c].specific})
        if r["narrative"].strip() and len(vehicles) == 1 and preds:
            gold = [{"entity_type": "vehicle", "entity_id": vehicles[0], "negated": False, "predicate": p}
                    for p in preds]
            eligible.append((dict(r, codes=[], site=PUBLIC_SITE), gold))
    eligible.sort(key=lambda pair: pair[0]["record_ref"])
    return eligible, {"mapped": len(mapped.records), "rejected": dict(sorted(mapped.rejected.items())),
                      "eligible": len(eligible)}


def draw(eligible: Sequence[tuple[dict[str, Any], list[dict[str, Any]]]], excluded: Iterable[str], n: int,
         seed: int) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """``random.Random("nhtsa:<seed>").sample`` of ``n`` over the eligible pairs less the excluded refs, sorted by
    ``record_ref``; refused when fewer than ``n`` remain."""
    left = [pair for pair in eligible if pair[0]["record_ref"] not in set(excluded)]
    if len(left) < n:
        raise J2LabelsError(f"only {len(left)} eligible unexcluded public records remain: no further fresh draw of "
                            f"{n} records is pre-registered")
    return sorted(random.Random(f"nhtsa:{seed}").sample(left, n), key=lambda pair: pair[0]["record_ref"])


def label_lines(sample: Sequence[tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[str]:
    return [canonical_dumps({"record": r, "gold": gold}) + "\n" for r, gold in sample]


# --------------------------------------------------------------------------------------------------- mechanism 1

def _manifest_entry(manifest: Any, rel: str, data: bytes) -> bool:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    meta = files.get(rel) if isinstance(files, dict) else None
    return (isinstance(meta, dict) and meta.get("sha256") == sha256_hex(data) and meta.get("bytes") == len(data))


def _refs_of_labels(data: bytes) -> set[str]:
    return {record["record_ref"] for record, _ in lab_j1.parse_labels(data)}


def _refs_of_questions(data: bytes) -> set[str]:
    return {q["record_ref"] for q in lab_j1.read_questions(data)}


def _refs_of_excluded(data: bytes) -> set[str]:
    refs: set[str] = set()
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        doc = strict_load(line)
        if not isinstance(doc, dict) or sorted(doc) != ["record_ref"] or not isinstance(doc["record_ref"], str):
            raise J2LabelsError("malformed")
        refs.add(doc["record_ref"])
    return refs


def _check_earlier(directory: Path, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    file = entry["file"]
    if file not in EXCLUDE_FILES:
        return None, "malformed"
    base = directory / str(entry["run_id"])
    if not base.is_dir():
        return None, "artifact absent"
    try:
        data = (base / file).read_bytes()
    except OSError:
        return None, "artifact absent"
    if not sha256_hex(data).startswith(entry["sha256_prefix"]):
        return None, "sha256 mismatch"
    manifest = strict_load((base / MANIFEST_FILE).read_bytes())
    if not _manifest_entry(manifest, file, data):
        return None, "manifest mismatch"
    if file == J2_EXCLUDED_FILE:
        return _refs_of_excluded(data), VERIFIED
    labels_record = (base / J1_LABELS_RECORD).read_bytes()
    questions = (base / J1_QUESTIONS_FILE).read_bytes()
    if not _manifest_entry(manifest, J1_LABELS_RECORD, labels_record) \
            or not _manifest_entry(manifest, J1_QUESTIONS_FILE, questions):
        return None, "manifest mismatch"
    refs = _refs_of_labels(data)
    record = strict_load(labels_record)
    if (not isinstance(record, dict) or any(record.get(k) != v for k, v in REBUILD.items())
            or record.get("n") != len(refs)):
        return None, "labels mismatch"
    if _refs_of_questions(questions) != refs:
        return None, "refs mismatch"
    return refs, VERIFIED


def earlier_refs(directory: str | Path | None, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    """Mechanism 1 (see the module docstring): ``(refs, outcome)``; the refs only when the outcome is
    :data:`VERIFIED`. ``directory`` is where the plan job put the earlier plan artifacts (``<directory>/<run id>/``);
    None is an absent directory. Nothing read from it escapes but the refs."""
    if directory is None:
        return None, "artifact absent"
    try:
        return _check_earlier(Path(directory), entry)
    except (OSError, StrictJsonError, J2LabelsError, lab_j1.J1Error, KeyError, TypeError, ValueError):
        return None, "malformed"


# --------------------------------------------------------------------------------------------------- mechanism 2

def rebuild_refs(fetch: Callable[[], bytes], expected_prefix: str = J001_LABELS_SHA256_PREFIX
                 ) -> tuple[set[str] | None, str, str]:
    """Mechanism 2 (see the module docstring): ``(refs, outcome, sha256)`` of the rebuilt seed-1, n-150 draw
    (``goldlabels.build_labels`` with :data:`REBUILD`); the refs only when the sha256 starts with
    ``expected_prefix``."""
    try:
        data, _ = goldlabels.build_labels(REBUILD["source"], NHTSA_PACK, REBUILD["n"], REBUILD["seed"], fetch=fetch)
    except GoldLabelsError:
        return None, "malformed", ""
    sha = sha256_hex(data)
    if not sha.startswith(expected_prefix):
        return None, "sha256 mismatch", sha
    return _refs_of_labels(data), VERIFIED, sha


# --------------------------------------------------------------------------------------------------- the draw

def excluded_jsonl(refs: Iterable[str]) -> bytes:
    """A3's carried-forward file: one ``{"record_ref"}`` line per ref, sorted, refs only."""
    return "".join(canonical_dumps({"record_ref": r}) + "\n" for r in sorted(set(refs))).encode("utf-8")


def build_labels(pack_id: str, n: int, seed: int, *, fetch: Callable[[], bytes], excluded: Iterable[str],
                 mechanisms: Mapping[str, str], excluded_runs: Sequence[Mapping[str, Any]] = ()
                 ) -> tuple[bytes, dict[str, Any]]:
    """The fresh draw's label-file bytes and record (see the module docstring). ``excluded`` are the refs the
    verifying mechanisms gave; ``mechanisms`` their outcomes (:data:`MECHANISMS` to :data:`OUTCOMES`);
    ``excluded_runs`` what the record lists under ``exclusion.excluded_runs`` (counts and hashes only)."""
    if pack_id != NHTSA_PACK:
        raise J2LabelsError(f"the fresh draw reads the vehicle pack {NHTSA_PACK}")
    pack = loader.load_pack(goldlabels.ROOT / pack_id)
    blob = fetch()
    rows, kept = nhtsa_rows(blob, pack_id)
    eligible, counts = eligible_records(pack, rows)
    excluded = set(excluded)
    sample = draw(eligible, excluded, n, seed)
    drawn = {r["record_ref"] for r, _ in sample}
    overlap = len(drawn & excluded)
    if overlap:
        raise J2LabelsError(f"the draw holds {overlap} excluded records")
    data = "".join(label_lines(sample)).encode("utf-8")
    records, claims = goldlabels._counts(data)
    record = {"schema_version": 1, "kind": "lab_j2_labels", "source": "nhtsa", "pack": pack.id,
              "pack_version": pack.version, "vocabulary_hash": pack.vocabulary_hash, "n": n, "seed": seed,
              "sites": None, "weeks": None, "available": counts["eligible"], "world_digest": None,
              "records": records, "claims": claims, "sha256": sha256_hex(data),
              "public": {"makes": list(NHTSA_MAKES), "window": list(NHTSA_WINDOW), "complaints": kept, **counts,
                         "lexical": goldlabels.lexical_scores(pack, data)},
              "exclusion": {"excluded": len(excluded), "eligible_after_exclusion": counts["eligible"] - len(excluded),
                            "overlap": overlap, "mechanisms": {m: mechanisms.get(m) for m in MECHANISMS},
                            "complaint_file_sha256": sha256_hex(blob),
                            "excluded_runs": [dict(r) for r in excluded_runs],
                            "excluded_sha256": sha256_hex(excluded_jsonl(excluded | drawn))}}
    return data, record
