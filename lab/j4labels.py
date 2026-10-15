"""Accuracy test A003's fresh draw (``docs/collective/A003/CHOICE-A003.md``, "The data and the draw" as amended C3, C4
and C20): real public NHTSA complaints of the vehicle pack with three earlier cycles' records excluded and the term
run's drafting set removed, built in the plan job over ``lab.j2labels`` and ``lab.j3labels`` unchanged.

**The three layouts** (items 1 to 3). :func:`earlier_labels_refs` is A002's check (``lab.j3labels.earlier_labels_refs``)
generalised by one more layout row (:data:`LAYOUTS`): ``prereg/j1/labels.jsonl`` (J001), ``prereg/j2/labels.jsonl``
(A001) and ``prereg/j3/labels.jsonl`` (A002: ``prereg/j3/labels.json`` of kind ``lab_j2_labels`` saying ``source``
``nhtsa``, ``n`` 150 and ``seed`` 3 with ``n`` the refs found, ``prereg/j3/questions.jsonl``'s refs equal to the
labels' and the file hashing to :data:`A002_QUESTIONS_SHA256_PREFIX`, C3); every file cross-checked against the
artifact's manifest ``prereg/prereg.json``. On the j1 and j2 layouts it gives the pinned functions' outcome case for
case (a test pins it). :func:`earlier_chain_refs` is ``lab.j2labels._check_earlier``'s chain branch generalised to
``prereg/j3/excluded.jsonl``. Outcomes are ``lab.j2labels.OUTCOMES`` (a questions file that does not hash to the pin is
``sha256 mismatch``); every read is inside ``try``/``except``; nothing read from an artifact leaves the runner but the
refs.

**The eight sources** (:func:`establish`): J1 and R1 (J001's artifact and seed-one rebuild, ``lab.j2labels`` unchanged),
A1, R2 and K2 (A001's artifact by ``lab.j3labels.earlier_labels_refs``, its rebuild by
``lab.j3labels.rebuild_a001_refs`` and its chain by ``lab.j2labels.earlier_refs``, unchanged), B1, R3 and K3 (A002's
artifact by :func:`earlier_labels_refs`, its rebuild by :func:`rebuild_a002_refs`: ``lab.j2labels.draw`` of 150 at seed
3 over the eligible list less J001's and A001's sets, and its chain by :func:`earlier_chain_refs`). The summary
keeps, beside B1's outcome, the questions prefix B1 was checked against (``questions_sha256_prefix``: A002's pinned
one, or a dry run's stand-in's), so that the plan job prints it with B1's outcome (C3).

**The stop rules** (item 4, C20). A cycle's set is established by its own sources (J1 or R1; A1 or R2; B1 or R3), the
union when both verified, ``by`` ``artifact`` when its artifact verified, else ``rebuild``. A cycle with no verifying
source of its own is established from a verified chain less the sets of the other cycles that chain covers
(:data:`CHAIN_COVERS`), each established without that chain: J001 from K2 less A001's set or from K3 less A001's and
A002's; A001 from K2 less J001's or from K3 less J001's and A002's; A002 from K3 less J001's and A001's. A chain
establishes at most one cycle, so two chains may each establish one. R2 is attempted whenever J001's set is
established, R3 whenever J001's and A001's are, by whichever source; else ``not attempted``. The consistency counts
(:data:`CONSISTENCY`), each a stop above 0: J1 and R1, A1 and R2, B1 and R3 equal when both verified; every verified
source of J001 and A001 inside K2 when K2 verified; every verified source of all three cycles, K2 included, inside K3
when K3 verified; K2 equal to the union of J001's and A001's sets, and K3 to the union of all three, when those are
established by their own sources. The excluded set is the union of every verified source's refs (450 expected).

**The split** (item 8, C4; :func:`draw`). The drafting set is rebuilt from the day's archive exactly as the term run
built it (``lab.jt.pooled_export``, :func:`rebuild_drafting`); when the day's archive is the term run's, its row count
and the sha256 of its sorted record ids must be the term run's, else ``J4_SPLIT_VIOLATED`` before any draw; when the
archives differ, the two drafting sets are used together. Before the draw, every eligible record not already excluded
whose ``record_ref`` is a drafting record id is removed (``by_record_id``), and every other one whose non-empty folded
narrative (``packs.canonical.folded``) has its sha256 among the drafting narratives' (``by_text``); the removed refs
join the earlier cycles' in the excluded set handed to ``lab.j2labels.build_labels`` unchanged, so they are counted in
``exclusion.excluded``, held by the chain file and excluded by later cycles. After the draw two tripwires
(:data:`TRIPWIRES`) stop the plan job with ``J4_SPLIT_VIOLATED`` when above 0: drawn records received before the
cutoff, and drawn records whose record id or folded narrative is a drafting one.

**The record.** :func:`excluded_runs` builds ``exclusion.excluded_runs``: one entry per cycle in seed order in A002's
B22 shape (the chain on A001's and A002's), and one entry of kind ``split`` with the two removal counts.
:func:`extend_exclusion` adds the sources, the cycles, the consistency counts and the split's counts and hashes beside
them. The chain file for the next cycle is ``lab.j2labels.excluded_jsonl`` over the excluded refs (the earlier cycles'
and the split's removals) and the draw's (``prereg/j4/excluded.jsonl``: 600 lines plus the removals). Nothing here
makes a network call, and nothing read from an artifact or the archive leaves the runner but refs and counts.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader
from mycelic.collective.packs.canonical import folded

from . import goldlabels
from . import j1 as lab_j1
from . import j2labels as lab_j2labels
from . import j3labels as lab_j3labels
from . import jt as lab_jt
from .j2labels import (MANIFEST_FILE, VERIFIED, J2LabelsError, _manifest_entry, _refs_of_excluded, _refs_of_labels,
                       _refs_of_questions)
from .notes import J4_SPLIT_VIOLATED

# A002 run 1, lab run 38439612256 (C3): its labels, questions and chain files, and its draw
A002_LABELS_SHA256_PREFIX = "0a64a62d94a7"
A002_QUESTIONS_SHA256_PREFIX = "84486fa38568"
A002_CHAIN_SHA256_PREFIX = "65d51f0fca51"
A002_REBUILD = {"source": "nhtsa", "n": 150, "seed": 3}
# the complaint file's sha256 prefix on A001's and A002's days, printed beside the day's (item 4)
COMPLAINT_FILE_SHA256_PREFIXES = {"a001": "a5a5de2d31f3", "a002": "216ecb36f9f5"}
J3_LABELS_FILE = "prereg/j3/labels.jsonl"
J3_LABELS_RECORD = "prereg/j3/labels.json"
J3_QUESTIONS_FILE = "prereg/j3/questions.jsonl"
J3_CHAIN_FILE = "prereg/j3/excluded.jsonl"
LABELS_KIND = "lab_j2_labels"
LABELS_FILES = (lab_j2labels.J1_LABELS_FILE, lab_j3labels.J2_LABELS_FILE, J3_LABELS_FILE)
CHAIN_FILES = (lab_j2labels.J2_EXCLUDED_FILE, J3_CHAIN_FILE)
EXCLUDE_FILES = (*lab_j3labels.EXCLUDE_FILES, J3_LABELS_FILE, J3_CHAIN_FILE)
# the labels layouts the artifact check reads: file -> (record file, questions file, the record's values, its kind)
LAYOUTS = {**lab_j3labels.LAYOUTS, J3_LABELS_FILE: (J3_LABELS_RECORD, J3_QUESTIONS_FILE, A002_REBUILD, LABELS_KIND)}
PINNED_QUESTIONS = (J3_LABELS_FILE,)          # the layouts whose questions file must hash to a pinned prefix (C3)
NOT_ATTEMPTED = lab_j3labels.NOT_ATTEMPTED
OUTCOMES = lab_j3labels.OUTCOMES
SOURCES = ("J1", "R1", "A1", "R2", "K2", "B1", "R3", "K3")
CYCLES = ("j001", "a001", "a002")
OWN = {"j001": ("J1", "R1"), "a001": ("A1", "R2"), "a002": ("B1", "R3")}      # (the artifact, the rebuild)
CHAINS = ("K2", "K3")
CHAIN_COVERS = {"K2": ("j001", "a001"), "K3": ("j001", "a001", "a002")}
BY = ("artifact", "rebuild", "chain")
CONSISTENCY = ("j1_r1", "a1_r2", "b1_r3", "outside_k2", "outside_k3", "k2_union", "k3_union")
PROBLEMS = ("unverified", "disagrees")
EXPECTED_EXCLUDED = 450
EXPECTED_CHAIN_LINES = 600                    # plus the split's removals (item 6)
SPLIT_KIND = "split"
TRIPWIRES = ("before_cutoff", "in_drafting")
SPLIT_KEYS = ("archive_sha256", "term_archive_sha256", "archives_equal", "term_rows", "term_refs_sha256",
              "rebuilt_rows", "rebuilt_refs_sha256", "rebuilt_text_sha256", "by_record_id", "by_text",
              "already_excluded", "tripwires")


class J4LabelsError(ValueError):
    """A problem with the split; ``problem`` is a fixed sentence, never a value."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


class DrawOverlap(J4LabelsError):
    """Item 5's overlap stop: ``lab.j2labels.build_labels`` refused the draw although enough eligible unexcluded
    records remained, so its refusal is not a shortage (``the draw holds N excluded records``); ``problem`` is its
    sentence. The plan job reads it as the exclusion not holding, never as too few records."""


# --------------------------------------------------------------------------------------------------- the artifacts

def _check_chain(directory: Path, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    file = entry["file"]
    if file not in CHAIN_FILES:
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
    return _refs_of_excluded(data), VERIFIED


def _check_labels(directory: Path, entry: Mapping[str, Any], questions_prefix: str) -> tuple[set[str] | None, str]:
    file = entry["file"]
    if file in CHAIN_FILES:
        return _check_chain(directory, entry)
    if file not in LAYOUTS:
        return None, "malformed"
    record_file, questions_file, expected, kind = LAYOUTS[file]
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
    labels_record = (base / record_file).read_bytes()
    questions = (base / questions_file).read_bytes()
    if not _manifest_entry(manifest, record_file, labels_record) or not _manifest_entry(manifest, questions_file,
                                                                                        questions):
        return None, "manifest mismatch"
    if file in PINNED_QUESTIONS and not sha256_hex(questions).startswith(questions_prefix):
        return None, "sha256 mismatch"
    refs = _refs_of_labels(data)
    record = strict_load(labels_record)
    if (not isinstance(record, dict) or any(record.get(k) != v for k, v in expected.items())
            or record.get("n") != len(refs) or (kind is not None and record.get("kind") != kind)):
        return None, "labels mismatch"
    if _refs_of_questions(questions) != refs:
        return None, "refs mismatch"
    return refs, VERIFIED


_READ_ERRORS = (OSError, StrictJsonError, J2LabelsError, lab_j1.J1Error, KeyError, TypeError, ValueError)


def earlier_labels_refs(directory: str | Path | None, entry: Mapping[str, Any], *,
                        questions_prefix: str = A002_QUESTIONS_SHA256_PREFIX) -> tuple[set[str] | None, str]:
    """A002's artifact check generalised to the j3 layout (C3; see the module docstring): ``(refs, outcome)``, the
    refs only when the outcome is :data:`~lab.j2labels.VERIFIED`. ``entry`` names the run, the file (one of
    :data:`EXCLUDE_FILES`) and the sha256 prefix; a chain file is read by :func:`earlier_chain_refs`'s check.
    ``questions_prefix`` is the pin the j3 layout's questions file must hash to (A002's; a test or a dry run's
    stand-in may pass another, never a request)."""
    if directory is None:
        return None, "artifact absent"
    try:
        return _check_labels(Path(directory), entry, questions_prefix)
    except _READ_ERRORS:
        return None, "malformed"


def earlier_chain_refs(directory: str | Path | None, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    """A chain file's refs (``prereg/j2/excluded.jsonl`` or ``prereg/j3/excluded.jsonl``): the prefix, the manifest,
    one ``{"record_ref"}`` line per ref (``lab.j2labels._check_earlier``'s chain branch, generalised)."""
    if directory is None:
        return None, "artifact absent"
    try:
        return _check_chain(Path(directory), entry)
    except _READ_ERRORS:
        return None, "malformed"


def rebuild_a002_refs(fetch: Callable[[], bytes], excluded: set[str], expected_prefix: str = A002_LABELS_SHA256_PREFIX,
                      *, n: int = A002_REBUILD["n"], seed: int = A002_REBUILD["seed"]
                      ) -> tuple[set[str] | None, str, str]:
    """R3 (item 3): ``(refs, outcome, sha256)`` of A002's draw rebuilt as A002 drew it, ``lab.j2labels.draw`` of ``n``
    at ``seed`` over the eligible list of today's file less ``excluded`` (J001's and A001's established sets), verified
    when the bytes of ``lab.j2labels.label_lines`` over the sample hash to ``expected_prefix``: A001's rebuild
    (``lab.j3labels.rebuild_a001_refs``, unchanged) with A002's seed and prefix."""
    return lab_j3labels.rebuild_a001_refs(fetch, excluded, expected_prefix, n=n, seed=seed)


# --------------------------------------------------------------------------------------------------- the sources

@dataclass
class Established:
    """What the eight sources gave: the refs of each verified source (``sets``, None otherwise), the rebuilt sha256 of
    each rebuild (``shas``, ``""`` when not attempted or unbuildable), the serialisable summary (``summary``:
    ``sources``, ``cycles`` and ``consistency``, counts and outcomes only), the excluded union, each cycle's set and
    the problem (``unverified``, ``disagrees`` or None)."""

    sets: dict[str, set[str] | None]
    shas: dict[str, str]
    summary: dict[str, Any]
    excluded: set[str] = field(default_factory=set)
    cycle_sets: dict[str, set[str] | None] = field(default_factory=dict)
    problem: str | None = None


def chain_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The entry a chain check reads: the entry's run and artifact, its chain file and prefix
    (``lab.j3labels.chain_entry``, unchanged)."""
    return lab_j3labels.chain_entry(entry)


def establish(earlier_plans: str | Path | None, j001: Mapping[str, Any], a001: Mapping[str, Any],
              a002: Mapping[str, Any], fetch: Callable[[], bytes], *,
              a002_questions_prefix: str = A002_QUESTIONS_SHA256_PREFIX) -> Established:
    """The eight sources and the stop rules of item 4 as amended C20 (see the module docstring). ``j001``, ``a001``
    and ``a002`` are the request's three ``exclude`` entries (a stand-in's in a dry run), in seed order."""
    sets: dict[str, set[str] | None] = {}
    outcomes: dict[str, str] = {}
    shas = {"R1": "", "R2": "", "R3": ""}
    sets["J1"], outcomes["J1"] = lab_j2labels.earlier_refs(earlier_plans, j001)
    sets["R1"], outcomes["R1"], shas["R1"] = lab_j2labels.rebuild_refs(fetch, j001["rebuild_sha256_prefix"])
    sets["A1"], outcomes["A1"] = lab_j3labels.earlier_labels_refs(earlier_plans, a001)
    sets["K2"], outcomes["K2"] = lab_j2labels.earlier_refs(earlier_plans, chain_entry(a001))
    sets["B1"], outcomes["B1"] = earlier_labels_refs(earlier_plans, a002, questions_prefix=a002_questions_prefix)
    sets["K3"], outcomes["K3"] = earlier_chain_refs(earlier_plans, chain_entry(a002))
    for rebuild in ("R2", "R3"):
        sets[rebuild], outcomes[rebuild] = None, NOT_ATTEMPTED
    cycles: dict[str, dict[str, Any]] = {c: {"established": False, "by": None, "chain": None, "refs": None}
                                         for c in CYCLES}
    cycle_sets: dict[str, set[str] | None] = dict.fromkeys(CYCLES)
    used: dict[str, str] = {}                  # cycle -> the chain that established it

    def own(cycle: str) -> None:
        artifact, rebuild = OWN[cycle]
        found = [sets[s] for s in (artifact, rebuild) if sets[s] is not None]
        if found and cycles[cycle]["by"] != "chain":
            cycle_sets[cycle] = set().union(*found)
            cycles[cycle].update(established=True, by="artifact" if sets[artifact] is not None else "rebuild")

    def by_chain(cycle: str) -> None:
        if cycles[cycle]["established"]:
            return
        for chain in CHAINS:
            others = [c for c in CHAIN_COVERS[chain] if c != cycle]
            # ``chain not in used.values()`` and ``used.get(c) != chain`` are guards the others-established condition
            # already implies (a chain reads a cycle only when every other cycle it covers is established, and a
            # chain that established a cycle has that cycle among the others it covers), so no test can tell either
            # one apart; they are kept so that the at-most-one-cycle rule (item 4, C20) reads here as written
            if (cycle in CHAIN_COVERS[chain] and sets[chain] is not None and chain not in used.values()
                    and all(cycle_sets[c] is not None and used.get(c) != chain for c in others)):
                cycle_sets[cycle] = sets[chain] - set().union(*(cycle_sets[c] for c in others))
                cycles[cycle].update(established=True, by="chain", chain=chain)
                used[cycle] = chain
                return

    for cycle in CYCLES:                       # the artifacts first: no rebuild can be attempted yet but R1
        own(cycle)
    by_chain("j001")
    if cycle_sets["j001"] is not None:         # R2 over the eligible list less J001's set, however established
        sets["R2"], outcomes["R2"], shas["R2"] = lab_j3labels.rebuild_a001_refs(
            fetch, cycle_sets["j001"], a001["rebuild_sha256_prefix"], seed=a001["rebuild_seed"])
        own("a001")
    by_chain("a001")
    if cycle_sets["j001"] is not None and cycle_sets["a001"] is not None:
        sets["R3"], outcomes["R3"], shas["R3"] = rebuild_a002_refs(
            fetch, cycle_sets["j001"] | cycle_sets["a001"], a002["rebuild_sha256_prefix"], seed=a002["rebuild_seed"])
        own("a002")
    by_chain("a002")
    for cycle in CYCLES:
        cycles[cycle]["refs"] = len(cycle_sets[cycle]) if cycle_sets[cycle] is not None else None
    consistency = _consistency(sets, cycles, cycle_sets)
    excluded: set[str] = set().union(*(sets[s] for s in SOURCES if sets[s] is not None))
    problem = None
    if not all(cycles[c]["established"] for c in CYCLES):
        problem = "unverified"
    elif any(count is not None and count > 0 for count in consistency.values()):
        problem = "disagrees"
    summary = {"sources": {s: {"outcome": outcomes[s], "refs": len(sets[s]) if sets[s] is not None else None}
                           for s in SOURCES},
               "cycles": cycles, "consistency": consistency, "excluded": len(excluded),
               "expected_excluded": EXPECTED_EXCLUDED, "problem": problem}
    # C3: the questions prefix B1 was checked against, printed with B1's outcome (A002's pinned one, or a dry run's
    # stand-in's)
    summary["sources"]["B1"]["questions_sha256_prefix"] = a002_questions_prefix
    return Established(sets=sets, shas=shas, summary=summary, excluded=excluded, cycle_sets=cycle_sets,
                       problem=problem)


def _consistency(sets: Mapping[str, set[str] | None], cycles: Mapping[str, Mapping[str, Any]],
                 cycle_sets: Mapping[str, set[str] | None]) -> dict[str, int | None]:
    def pair(a: str, b: str) -> int | None:
        x, y = sets[a], sets[b]
        return len(x ^ y) if x is not None and y is not None else None

    def outside(chain: str, sources: Sequence[str]) -> int | None:
        k = sets[chain]
        if k is None:
            return None
        return len(set().union(*(sets[s] for s in sources if sets[s] is not None)) - k)

    def union(chain: str) -> int | None:
        k = sets[chain]
        covered = CHAIN_COVERS[chain]
        if k is None or any(cycles[c]["by"] not in ("artifact", "rebuild") for c in covered):
            return None
        return len(k ^ set().union(*(cycle_sets[c] for c in covered)))

    return {"j1_r1": pair("J1", "R1"), "a1_r2": pair("A1", "R2"), "b1_r3": pair("B1", "R3"),
            "outside_k2": outside("K2", ("J1", "R1", "A1", "R2")),
            "outside_k3": outside("K3", ("J1", "R1", "A1", "R2", "K2", "B1", "R3")),
            "k2_union": union("K2"), "k3_union": union("K3")}


def mechanisms(est: Established) -> dict[str, str]:
    """J001's pair for ``exclusion.mechanisms``, as A001's and A002's records held it: ``artifact`` J1 and ``rebuild``
    R1."""
    return {"artifact": est.summary["sources"]["J1"]["outcome"], "rebuild": est.summary["sources"]["R1"]["outcome"]}


# --------------------------------------------------------------------------------------------------- the split

@dataclass(frozen=True)
class Drafting:
    """A drafting set (C4): its row count, its record ids, the sha256 of its distinct non-empty folded narratives,
    and the sha256 of its two files (``prereg/jt/drafting_refs.jsonl``'s bytes, the sorted record ids, and
    ``prereg/jt/drafting_text.jsonl``'s)."""

    rows: int
    refs: frozenset[str]
    text: frozenset[str]
    refs_sha256: str
    text_sha256: str


def narrative_sha256(text: Any) -> str | None:
    """The sha256 of a narrative's non-empty folded form (``packs.canonical.folded``), as ``lab.jt.narrative_hashes``
    hashes the drafting narratives; None for an empty or absent narrative."""
    f = folded(text) if isinstance(text, str) else ""
    return hashlib.sha256(f.encode("utf-8")).hexdigest() if f else None


def _hashes_of_text(data: bytes) -> set[str]:
    out: set[str] = set()
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        doc = strict_load(line)
        if not isinstance(doc, dict) or sorted(doc) != ["sha256"] or not isinstance(doc["sha256"], str):
            raise J4LabelsError("a drafting text line is not one sha256")
        out.add(doc["sha256"])
    return out


def drafting_from_files(refs_bytes: bytes, text_bytes: bytes) -> Drafting:
    """The term run's drafting set from its two files (``prereg/jt/drafting_refs.jsonl`` and
    ``prereg/jt/drafting_text.jsonl``, verified against ``terms.json`` by ``lab.j4terms``)."""
    refs = _refs_of_excluded(refs_bytes)
    return Drafting(rows=len(refs), refs=frozenset(refs), text=frozenset(_hashes_of_text(text_bytes)),
                    refs_sha256=sha256_hex(refs_bytes), text_sha256=sha256_hex(text_bytes))


def rebuild_drafting(blob: bytes) -> Drafting:
    """C4: the drafting set rebuilt from the day's archive exactly as the term run built it (``lab.jt.pooled_export``,
    ``drafting_refs_bytes``, ``drafting_text_bytes``, unchanged)."""
    pooled = lab_jt.pooled_export(blob)
    refs_bytes = lab_jt.drafting_refs_bytes(pooled.export)
    text_bytes = lab_jt.drafting_text_bytes(pooled.export)
    found = drafting_from_files(refs_bytes, text_bytes)
    return Drafting(rows=len(pooled.export), refs=found.refs, text=found.text, refs_sha256=found.refs_sha256,
                    text_sha256=found.text_sha256)


def compare_drafting(term_rows: int, term_refs_sha256: str, term_archive_sha256: str, rebuilt: Drafting,
                     archive_sha256: str) -> str | None:
    """C4: when the day's archive is the term run's, the rebuilt drafting set's row count and the sha256 of its sorted
    record ids must be the term run's, else ``J4_SPLIT_VIOLATED``; None otherwise (the archives differing included)."""
    if archive_sha256 == term_archive_sha256 and (rebuilt.rows != term_rows
                                                  or rebuilt.refs_sha256 != term_refs_sha256):
        return J4_SPLIT_VIOLATED
    return None


def split_removals(eligible: Sequence[tuple[Mapping[str, Any], Any]], excluded: Iterable[str],
                   drafting: Sequence[Drafting]) -> tuple[set[str], set[str], int]:
    """C4's removals before the draw: ``(by_record_id, by_text, already_excluded)``. Among the eligible records not
    already excluded, those whose ``record_ref`` is a record id of any drafting set, then every other one whose
    non-empty folded narrative's sha256 is a drafting narrative's; ``already_excluded`` counts the eligible records
    of either kind that the earlier cycles' set already held."""
    excluded = set(excluded)
    refs = set().union(*(d.refs for d in drafting)) if drafting else set()
    text = set().union(*(d.text for d in drafting)) if drafting else set()
    by_ref: set[str] = set()
    by_text: set[str] = set()
    already = 0
    for record, _ in eligible:
        ref = record["record_ref"]
        hit_ref = ref in refs
        hit_text = not hit_ref and narrative_sha256(record.get("narrative")) in text
        if ref in excluded:
            already += int(hit_ref or hit_text)
        elif hit_ref:
            by_ref.add(ref)
        elif hit_text:
            by_text.add(ref)
    return by_ref, by_text, already


def tripwires(labels_bytes: bytes, drafting: Sequence[Drafting]) -> dict[str, int]:
    """C4's tripwires after the draw: the drawn records received before the cutoff (a record whose received date is
    not an ISO date on or after it counts), and the drawn records whose record id or folded narrative is a drafting
    one."""
    refs = set().union(*(d.refs for d in drafting)) if drafting else set()
    text = set().union(*(d.text for d in drafting)) if drafting else set()
    before = inside = 0
    for record, _ in lab_j1.parse_labels(labels_bytes):
        day = record.get("received_date")
        before += int(not (isinstance(day, str) and len(day) == len(lab_jt.CUTOFF_ISO) and day >= lab_jt.CUTOFF_ISO))
        inside += int(record["record_ref"] in refs or narrative_sha256(record.get("narrative")) in text)
    return {"before_cutoff": before, "in_drafting": inside}


# --------------------------------------------------------------------------------------------------- the record

def excluded_runs(est: Established, used: Sequence[Mapping[str, Any]], requested: Sequence[Mapping[str, Any]],
                  stand_in: bool, split: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    """The labels record's ``exclusion.excluded_runs``: one entry per cycle in seed order in A002's B22 shape (the
    artifact's outcome and refs found, the rebuild's seed, prefix, outcome, refs and sha256, the chain's for A001 and
    A002, ``derived`` ``by_chain`` when the cycle's set came from a chain, how it was established, whether a stand-in
    was used, the entry as requested), then, with ``split`` (the two removal counts), one entry of kind ``split``.
    Counts and hashes only."""
    out: list[dict[str, Any]] = []
    rows = (("j001", "J1", "R1", None), ("a001", "A1", "R2", "K2"), ("a002", "B1", "R3", "K3"))
    sources = est.summary["sources"]
    for (cycle, artifact, rebuild, chain), entry, asked in zip(rows, used, requested):
        row: dict[str, Any] = {
            "run_id": entry["run_id"], "artifact": entry["artifact"], "file": entry["file"],
            "sha256_prefix": entry["sha256_prefix"], "refs": sources[artifact]["refs"] or 0,
            "outcome": sources[artifact]["outcome"],
            "rebuild": {"seed": entry["rebuild_seed"], "sha256_prefix": entry["rebuild_sha256_prefix"],
                        "outcome": sources[rebuild]["outcome"], "refs": sources[rebuild]["refs"] or 0,
                        "sha256": est.shas[rebuild] or None},
            "chain": None, "derived": "by_chain" if est.summary["cycles"][cycle]["by"] == "chain" else None,
            "established_by": est.summary["cycles"][cycle]["by"], "stand_in": stand_in,
            "requested": {k: asked[k] for k in sorted(asked)}}
        if chain is not None:
            row["chain"] = {"file": entry["chain_file"], "sha256_prefix": entry["chain_sha256_prefix"],
                            "outcome": sources[chain]["outcome"], "refs": sources[chain]["refs"] or 0}
        out.append(row)
    if split is not None:
        out.append({"kind": SPLIT_KIND, "by_record_id": split[0], "by_text": split[1]})
    return out


def extend_exclusion(record: dict[str, Any], est: Established, split: Mapping[str, Any]) -> dict[str, Any]:
    """Add the eight sources' outcomes, the cycles, the consistency counts and the split's counts and hashes
    (:data:`SPLIT_KEYS`) to a labels record's ``exclusion`` block (``lab.j2labels.build_labels``' record); returns
    the block."""
    if sorted(split) != sorted(SPLIT_KEYS):
        raise J4LabelsError("the split's block is not the draw's")
    exclusion = record["exclusion"]
    exclusion.update(sources=est.summary["sources"], cycles=est.summary["cycles"],
                     consistency=est.summary["consistency"], expected_excluded=EXPECTED_EXCLUDED, split=dict(split))
    return exclusion


def chain_bytes(excluded: set[str], drawn: set[str]) -> bytes:
    """Item 6's chain file for the next cycle: every excluded ref (the split's removals included) and the draw's,
    sorted, refs only."""
    return lab_j2labels.excluded_jsonl(excluded | drawn)


# --------------------------------------------------------------------------------------------------- the draw

@dataclass
class Draw:
    """The split and the draw (item 8, C4): the labels' bytes and record (None when the split stopped before the
    draw), the excluded set handed to ``build_labels`` (the earlier cycles' and the removals), the drawn refs, the
    removals, the split's block (:data:`SPLIT_KEYS`), the ``excluded_runs`` entries, the chain file's bytes and the
    problem (``J4_SPLIT_VIOLATED`` or None)."""

    data: bytes | None
    record: dict[str, Any] | None
    excluded: set[str]
    drawn: set[str]
    by_record_id: set[str]
    by_text: set[str]
    split: dict[str, Any]
    excluded_runs: list[dict[str, Any]]
    chain: bytes | None
    problem: str | None


def draw(*, pack_id: str, n: int, seed: int, fetch: Callable[[], bytes], est: Established, term: Drafting,
         term_archive_sha256: str, used: Sequence[Mapping[str, Any]], requested: Sequence[Mapping[str, Any]],
         stand_in: bool) -> Draw:
    """Item 8 and the draw of items 5 and 6 (see the module docstring): the drafting set rebuilt and compared with the
    term run's (``term``, from its verified files, and ``term_archive_sha256``, its day's archive), the removals,
    ``lab.j2labels.build_labels`` unchanged over the excluded set, the tripwires and the chain file. Raises
    ``lab.j2labels.J2LabelsError`` when fewer than ``n`` records remain, and :class:`DrawOverlap` when
    ``build_labels`` refuses the draw otherwise (the draw holds an excluded record). ``est`` must have no problem."""
    if est.problem is not None:
        raise J4LabelsError("the earlier cycles' sets are not established")
    blob = fetch()
    archive_sha = sha256_hex(blob)
    split: dict[str, Any] = {"archive_sha256": archive_sha, "term_archive_sha256": term_archive_sha256,
                             "archives_equal": archive_sha == term_archive_sha256, "term_rows": term.rows,
                             "term_refs_sha256": term.refs_sha256, "rebuilt_rows": None, "rebuilt_refs_sha256": None,
                             "rebuilt_text_sha256": None, "by_record_id": None, "by_text": None,
                             "already_excluded": None, "tripwires": None}
    stopped = Draw(data=None, record=None, excluded=set(est.excluded), drawn=set(), by_record_id=set(), by_text=set(),
                   split=split, excluded_runs=[], chain=None, problem=J4_SPLIT_VIOLATED)
    try:
        rebuilt = rebuild_drafting(blob)
    except (lab_jt.JtError, J4LabelsError, J2LabelsError, StrictJsonError, ValueError, KeyError, OSError):
        return stopped                         # the drafting set cannot be rebuilt: the split cannot be enforced
    split.update(rebuilt_rows=rebuilt.rows, rebuilt_refs_sha256=rebuilt.refs_sha256,
                 rebuilt_text_sha256=rebuilt.text_sha256)
    if compare_drafting(term.rows, term.refs_sha256, term_archive_sha256, rebuilt, archive_sha) is not None:
        return stopped
    sets = (term, rebuilt)
    pack = loader.load_pack(goldlabels.ROOT / pack_id)
    rows, _ = lab_j2labels.nhtsa_rows(blob, pack_id)
    eligible, _ = lab_j2labels.eligible_records(pack, rows)
    by_ref, by_text, already = split_removals(eligible, est.excluded, sets)
    split.update(by_record_id=len(by_ref), by_text=len(by_text), already_excluded=already)
    excluded = est.excluded | by_ref | by_text
    runs = excluded_runs(est, used, requested, stand_in, (len(by_ref), len(by_text)))
    try:
        data, record = lab_j2labels.build_labels(pack_id, n, seed, fetch=fetch, excluded=excluded,
                                                 mechanisms=mechanisms(est), excluded_runs=runs)
    except J2LabelsError as err:
        if sum(1 for r, _ in eligible if r["record_ref"] not in excluded) < n:
            raise                              # too few eligible unexcluded records: the shortage stop
        raise DrawOverlap(err.problem) from None
    drawn = {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}
    split["tripwires"] = tripwires(data, sets)
    extend_exclusion(record, est, split)
    problem = J4_SPLIT_VIOLATED if any(split["tripwires"].values()) else None
    return Draw(data=data, record=record, excluded=excluded, drawn=drawn, by_record_id=by_ref, by_text=by_text,
                split=split, excluded_runs=runs, chain=chain_bytes(excluded, drawn), problem=problem)
