"""Accuracy test A003's term artifact check (``docs/collective/A003/CHOICE-A003.md``, "The terms", item 10 as amended
C4, C21, C23 and C29, and T1 to T11): the judging run's plan job reads the term run's ``prereg/jt/terms.json`` from the
term run's plan artifact, downloaded into ``<terms plans>/<run id>/``, and refuses to run unless it is the committed
term set.

:func:`check_terms` checks, in this order, each failure one fixed outcome (:data:`OUTCOMES`: ``lab.j2labels.OUTCOMES``
plus ``terms absent``) and the name of the check that failed (:data:`CHECKS`):

* the artifact's directory exists (``artifact absent``) and holds ``prereg/jt/terms.json`` (``terms absent``; the entry
  must name that file, else ``malformed``);
* the file's bytes hash to the request's ``sha256`` in full, 64 hex digits (``sha256 mismatch``);
* the artifact's manifest ``prereg/prereg.json`` lists the term set and its two drafting files
  (``prereg/jt/drafting_refs.jsonl`` and ``prereg/jt/drafting_text.jsonl``) with their sha256 and sizes (``manifest
  mismatch``);
* the document is a term set (``kind`` ``lab_jt_terms``, schema 1, one entry per specific predicate of the hand pack, in
  id order, each a list of terms: else ``malformed``);
* its cutoff (and the request's) is ``NHTSA_WINDOW[0]`` in ISO form, its window and makes are ``lab.jt``'s, its hand
  pack hashes are the repository's hand pack's, and the code hash of ``lab/jt.py`` at this commit is the one it records
  (C29) (``labels mismatch``);
* the two drafting files hash to the sha256 the document records, the refs file's ids number its rows and the text
  file's lines its distinct narratives (C4) (``refs mismatch``);
* P_D rebuilt from the hand pack and the term set (``lab.jt.p_d_hashes``, unchanged) has the four hashes the document
  records and the request's ``vocabulary_hash`` (``sha256 mismatch``).

Every read is inside ``try``/``except``: any other failure is ``malformed``. Anything that is not ``verified`` stops the
plan job with :data:`~lab.notes.J4_TERMS_UNVERIFIED` before any draw and any model. The result's ``summary`` carries
counts and hashes only, and the document's own figures only once it verified: nothing of the artifact is printed but
counts and hashes, and no term ever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs.loader import PackError

from . import j1 as lab_j1
from . import j2labels as lab_j2labels
from . import j4labels as lab_j4labels
from . import jt as lab_jt
from .goldlabels import NHTSA_MAKES, NHTSA_PACK
from .j2labels import MANIFEST_FILE, VERIFIED, J2LabelsError, _manifest_entry
from .notes import J4_TERMS_UNVERIFIED

TERMS_FILE = lab_jt.TERMS_FILE
DRAFTING_FILES = (lab_jt.DRAFTING_REFS_FILE, lab_jt.DRAFTING_TEXT_FILE)
TERMS_ABSENT = "terms absent"
OUTCOMES = (*lab_j2labels.OUTCOMES, TERMS_ABSENT)
ENTRY_KEYS = ("run_id", "artifact", "file", "sha256", "vocabulary_hash", "cutoff")
# each check, in order, and the outcome its failure gives
CHECK_OUTCOMES = {
    "artifact": "artifact absent", "terms_file": TERMS_ABSENT, "terms_sha256": "sha256 mismatch",
    "manifest": "manifest mismatch", "document": "malformed", "cutoff": "labels mismatch",
    "window": "labels mismatch", "makes": "labels mismatch", "hand_hashes": "labels mismatch",
    "jt_code_hash": "labels mismatch", "drafting_files": "refs mismatch", "p_d_hashes": "sha256 mismatch",
}
CHECKS = tuple(CHECK_OUTCOMES)
SUMMARY_KEYS = ("outcome", "failed", "run_id", "artifact", "sha256", "expected_sha256", "jt_code_hash",
                "document_jt_code_hash", "vocabulary_hash", "p_d", "archive_sha256", "drafting_rows",
                "drafting_refs_sha256", "drafting_text_sha256", "term_counts")


class _Failed(Exception):
    def __init__(self, check: str) -> None:
        super().__init__(check)
        self.check = check


@dataclass
class TermsCheck:
    """The term artifact's check: the outcome, the check that failed (None when verified), the summary (counts and
    hashes only, :data:`SUMMARY_KEYS`) and, only when verified, the term set's bytes and document, T by predicate
    (``lab.j4.terms_map``), the term run's drafting set (``lab.j4labels.Drafting`` from its two files) and P_D's four
    hashes."""

    outcome: str
    failed: str | None
    summary: dict[str, Any]
    data: bytes | None = None
    document: dict[str, Any] | None = None
    terms: dict[str, list[str]] | None = None
    drafting: lab_j4labels.Drafting | None = None
    p_d: dict[str, str] | None = field(default=None)

    @property
    def problem(self) -> str | None:
        """:data:`~lab.notes.J4_TERMS_UNVERIFIED` unless the term set verified."""
        return None if self.outcome == VERIFIED else J4_TERMS_UNVERIFIED


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _entries(doc: Mapping[str, Any]) -> dict[str, list[str]]:
    """T from the document: one entry per specific hand predicate, in id order, each a list of non-empty strings."""
    entries = doc.get("terms")
    hand = list(lab_jt.hand_spellings(lab_jt.hand_pack()))
    if not isinstance(entries, list) or [e.get("predicate") if isinstance(e, dict) else None for e in entries] != hand:
        raise _Failed("document")
    out: dict[str, list[str]] = {}
    for entry in entries:
        terms = entry.get("terms")
        if not isinstance(terms, list) or not all(isinstance(t, str) and t for t in terms):
            raise _Failed("document")
        out[entry["predicate"]] = list(terms)
    return out


def _check(directory: Path, entry: Mapping[str, Any], found: dict[str, Any]) -> TermsCheck:
    if entry["file"] != TERMS_FILE:
        raise _Failed("document")
    base = directory / str(entry["run_id"])
    if not base.is_dir():
        raise _Failed("artifact")
    try:
        data = (base / TERMS_FILE).read_bytes()
    except OSError:
        raise _Failed("terms_file") from None
    found["sha256"] = sha256_hex(data)
    if found["sha256"] != entry["sha256"]:
        raise _Failed("terms_sha256")
    manifest = strict_load((base / MANIFEST_FILE).read_bytes())
    drafting: dict[str, bytes] = {}
    for rel in DRAFTING_FILES:
        try:
            drafting[rel] = (base / rel).read_bytes()
        except OSError:
            raise _Failed("manifest") from None
    if not _manifest_entry(manifest, TERMS_FILE, data) or not all(_manifest_entry(manifest, rel, body)
                                                                   for rel, body in drafting.items()):
        raise _Failed("manifest")
    doc = strict_load(data)
    if not isinstance(doc, dict) or doc.get("kind") != lab_jt.TERMS_KIND \
            or doc.get("schema_version") != lab_jt.SCHEMA_VERSION:
        raise _Failed("document")
    terms = _entries(doc)
    if doc.get("cutoff") != lab_jt.CUTOFF_ISO or entry["cutoff"] != lab_jt.CUTOFF_ISO:
        raise _Failed("cutoff")
    if doc.get("window") != list(lab_jt.WINDOW):
        raise _Failed("window")
    if doc.get("makes") != list(NHTSA_MAKES):
        raise _Failed("makes")
    if _get(doc, "hand", "pack") != NHTSA_PACK or _get(doc, "hand", "hashes") != lab_jt.hand_pack().hashes():
        raise _Failed("hand_hashes")
    if _get(doc, "code", "jt_code_hash") != lab_jt.code_hash():
        raise _Failed("jt_code_hash")
    refs_bytes, text_bytes = drafting[lab_jt.DRAFTING_REFS_FILE], drafting[lab_jt.DRAFTING_TEXT_FILE]
    try:
        found_drafting = lab_j4labels.drafting_from_files(refs_bytes, text_bytes)
    except (J2LabelsError, lab_j4labels.J4LabelsError, StrictJsonError, KeyError, TypeError, ValueError):
        raise _Failed("drafting_files") from None
    block = doc.get("drafting")
    if (not isinstance(block, dict) or sha256_hex(refs_bytes) != block.get("refs_file_sha256")
            or sha256_hex(refs_bytes) != block.get("refs_sha256")
            or sha256_hex(text_bytes) != block.get("text_file_sha256")
            or found_drafting.rows != block.get("rows") or len(found_drafting.text) != block.get("narratives")
            or len(text_bytes.splitlines()) != len(found_drafting.text)):
        raise _Failed("drafting_files")
    p_d = lab_jt.p_d_hashes(doc["terms"])
    if p_d != _get(doc, "p_d", "hashes") or p_d.get("vocabulary_hash") != entry["vocabulary_hash"]:
        raise _Failed("p_d_hashes")
    counts = doc.get("term_counts") if isinstance(doc.get("term_counts"), dict) else {}
    found.update(document_jt_code_hash=_get(doc, "code", "jt_code_hash"), vocabulary_hash=p_d["vocabulary_hash"],
                 p_d=dict(p_d), archive_sha256=_get(doc, "archive", "sha256"), drafting_rows=found_drafting.rows,
                 drafting_refs_sha256=found_drafting.refs_sha256, drafting_text_sha256=found_drafting.text_sha256,
                 term_counts={k: v for k, v in sorted(counts.items())
                              if isinstance(v, int) and not isinstance(v, bool)})
    return TermsCheck(outcome=VERIFIED, failed=None, summary={}, data=data, document=doc, terms=terms,
                      drafting=found_drafting, p_d=dict(p_d))


_READ_ERRORS = (OSError, StrictJsonError, J2LabelsError, lab_j4labels.J4LabelsError, lab_jt.JtError, lab_j1.J1Error,
                PackError, KeyError, TypeError, ValueError, AttributeError)


def check_terms(directory: str | Path | None, entry: Mapping[str, Any]) -> TermsCheck:
    """The term artifact's check (see the module docstring). ``directory`` is where the plan job put the term run's
    plan artifact (``<directory>/<run id>/``), None when the download step left nothing; ``entry`` is the request's
    ``terms`` entry (a stand-in's in a dry run): ``run_id``, ``artifact``, ``file``, ``sha256``, ``vocabulary_hash``
    and ``cutoff``."""
    found: dict[str, Any] = {"sha256": None}
    try:
        if directory is None:
            raise _Failed("artifact")
        result = _check(Path(directory), entry, found)
    except _Failed as failed:
        result = TermsCheck(outcome=CHECK_OUTCOMES[failed.check], failed=failed.check, summary={})
        found = {"sha256": found.get("sha256")}              # nothing of an unverified document is kept
    except _READ_ERRORS:
        result = TermsCheck(outcome="malformed", failed="document", summary={})
        found = {"sha256": found.get("sha256")}
    summary = dict.fromkeys(SUMMARY_KEYS)
    summary.update(found)
    summary.update(outcome=result.outcome, failed=result.failed, run_id=entry.get("run_id"),
                   artifact=entry.get("artifact"), expected_sha256=entry.get("sha256"),
                   jt_code_hash=lab_jt.code_hash())
    result.summary = summary
    return result
