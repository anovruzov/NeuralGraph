"""Accuracy test A002's fresh draw (``docs/collective/A002/CHOICE-A002.md``, "The data and the draw" as amended B2, B3,
B5 and B22): real public NHTSA complaints of the vehicle pack with two earlier cycles' records excluded, built in the
plan job over ``lab.j2labels`` unchanged.

Every earlier cycle is excluded by its artifact and by its rebuild, the five sources of item 5 (:func:`establish`):

* **J1**, J001's plan artifact: ``lab.j2labels.earlier_refs`` unchanged with the request's first ``exclude`` entry
  (``prereg/j1/labels.jsonl``, A001's hardened check A1);
* **R1**, J001's rebuild: ``lab.j2labels.rebuild_refs`` unchanged, the seed-one draw from today's complaint file;
* **A1**, A001's plan artifact: :func:`earlier_labels_refs`, A001's check generalised to the j2 layout (B3:
  ``prereg/j2/labels.jsonl`` hashing to the entry's prefix, ``prereg/j2/labels.json`` of kind ``lab_j2_labels``
  saying ``source`` ``nhtsa``, ``n`` 150 and ``seed`` 2 with ``n`` the refs found, the refs equal to
  ``prereg/j2/questions.jsonl``'s, every file cross-checked against the artifact's manifest ``prereg/prereg.json``);
  on the j1 layout the same function gives ``lab.j2labels.earlier_refs``' outcome case for case (a test pins it), and
  for the chain file it calls ``earlier_refs`` unchanged;
* **R2**, A001's rebuild: :func:`rebuild_a001_refs`, ``lab.j2labels.draw(eligible, J001_set, 150, 2)`` over
  ``lab.j2labels.eligible_records`` of today's file, verified when the bytes of ``lab.j2labels.label_lines`` over the
  sample hash to the entry's rebuild prefix; ``not attempted`` without a J001 set;
* **K**, A001's chain file ``prereg/j2/excluded.jsonl``: ``earlier_refs`` unchanged with the entry's chain keys.

**The stop rules (B5).** J001's set is established when J1 or R1 verified; else, when K verified and A001's set is
established by A1, as K less A1 (``by`` ``chain``). A001's set is established when A1 or R2 verified; else, when K
verified and J001's set is established by J1 or R1, as K less J001's set. K alone establishes neither. The consistency
counts, each a stop above 0: J1 and R1 equal when both verified, A1 and R2 equal when both verified, every other
verified source's refs inside K when K verified, K equal to the union of the two sets when both are established
without the chain. The excluded set is the union of every verified source's refs. :func:`establish` returns the
sets, the serialisable summary (counts and outcomes, never a ref) and the problem (``unverified`` when a cycle's set
is not established, ``disagrees`` when a count is above 0, else None); the plan job stops on either.

**The record (B22).** The draw is ``lab.j2labels.build_labels`` unchanged (kind ``lab_j2_labels``, ``mechanisms``
J001's pair); :func:`excluded_runs` builds its ``exclusion.excluded_runs`` entries, one per cycle in seed order with
the cycle's artifact and rebuild outcomes, the chain's for A001, ``derived`` ``by_chain`` when the chain reading was
used, the counts found and the requested keys; :func:`extend_exclusion` adds the sources, the cycles and the
consistency counts beside them. The chain file for the next cycle is ``lab.j2labels.excluded_jsonl`` over the excluded
refs and the draw's (``prereg/j3/excluded.jsonl``, 450 lines expected). Nothing here makes a network call, and nothing
read from an artifact leaves the runner but the refs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader

from . import goldlabels
from . import j1 as lab_j1
from . import j2labels as lab_j2labels
from .goldlabels import NHTSA_PACK
from .j2labels import J2_EXCLUDED_FILE, MANIFEST_FILE, VERIFIED, J2LabelsError, _manifest_entry, _refs_of_labels, \
    _refs_of_questions

# A001's labels (CHOICE-A001, Run 1: "labels sha256 623780b8a1de…"), its questions file and its chain file
A001_LABELS_SHA256_PREFIX = "623780b8a1de"
A001_QUESTIONS_SHA256_PREFIX = "2ba18ea89bb7"
A001_CHAIN_SHA256_PREFIX = "9a948cbf2cd9"
A001_REBUILD = {"source": "nhtsa", "n": 150, "seed": 2}
J2_LABELS_FILE = "prereg/j2/labels.jsonl"
J2_LABELS_RECORD = "prereg/j2/labels.json"
J2_QUESTIONS_FILE = "prereg/j2/questions.jsonl"
J2_LABELS_KIND = "lab_j2_labels"
EXCLUDE_FILES = (*lab_j2labels.EXCLUDE_FILES, J2_LABELS_FILE)
# the labels layouts the artifact check reads: file -> (record file, questions file, the record's values, its kind)
LAYOUTS = {
    lab_j2labels.J1_LABELS_FILE: (lab_j2labels.J1_LABELS_RECORD, lab_j2labels.J1_QUESTIONS_FILE, lab_j2labels.REBUILD,
                                  None),
    J2_LABELS_FILE: (J2_LABELS_RECORD, J2_QUESTIONS_FILE, A001_REBUILD, J2_LABELS_KIND),
}
NOT_ATTEMPTED = "not attempted"
OUTCOMES = (*lab_j2labels.OUTCOMES, NOT_ATTEMPTED)
SOURCES = ("J1", "R1", "A1", "R2", "K")
CYCLES = ("j001", "a001")
BY = ("artifact", "rebuild", "chain")
CONSISTENCY = ("j1_r1", "a1_r2", "outside_chain", "chain_union")
PROBLEMS = ("unverified", "disagrees")
EXPECTED_EXCLUDED = 300


# --------------------------------------------------------------------------------------------------- mechanism 1

def _check_labels(directory: Path, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    file = entry["file"]
    if file == J2_EXCLUDED_FILE:
        return lab_j2labels._check_earlier(directory, entry)
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
    refs = _refs_of_labels(data)
    record = strict_load(labels_record)
    if (not isinstance(record, dict) or any(record.get(k) != v for k, v in expected.items())
            or record.get("n") != len(refs) or (kind is not None and record.get("kind") != kind)):
        return None, "labels mismatch"
    if _refs_of_questions(questions) != refs:
        return None, "refs mismatch"
    return refs, VERIFIED


def earlier_labels_refs(directory: str | Path | None, entry: Mapping[str, Any]) -> tuple[set[str] | None, str]:
    """A001's artifact check generalised to the j2 layout (B3; see the module docstring): ``(refs, outcome)``, the
    refs only when the outcome is :data:`~lab.j2labels.VERIFIED`. ``entry`` names the run, the file (one of
    :data:`EXCLUDE_FILES`) and the sha256 prefix; the chain file is read by ``lab.j2labels`` unchanged. Every read is
    inside ``try``/``except``; nothing read from the directory escapes but the refs."""
    if directory is None:
        return None, "artifact absent"
    try:
        return _check_labels(Path(directory), entry)
    except (OSError, StrictJsonError, J2LabelsError, lab_j1.J1Error, KeyError, TypeError, ValueError):
        return None, "malformed"


# --------------------------------------------------------------------------------------------------- mechanism 2

def rebuild_a001_refs(fetch: Callable[[], bytes], excluded: set[str], expected_prefix: str = A001_LABELS_SHA256_PREFIX,
                      *, n: int = A001_REBUILD["n"], seed: int = A001_REBUILD["seed"]
                      ) -> tuple[set[str] | None, str, str]:
    """R2 (item 4): ``(refs, outcome, sha256)`` of A001's draw rebuilt as A001 drew it, ``lab.j2labels.draw`` of ``n``
    with ``seed`` over the eligible list of today's complaint file less ``excluded`` (J001's established set); the
    refs only when the bytes of ``lab.j2labels.label_lines`` over the sample hash to ``expected_prefix``."""
    try:
        pack = loader.load_pack(goldlabels.ROOT / NHTSA_PACK)
        rows, _ = lab_j2labels.nhtsa_rows(fetch(), NHTSA_PACK)
        eligible, _ = lab_j2labels.eligible_records(pack, rows)
        sample = lab_j2labels.draw(eligible, excluded, n, seed)
        data = "".join(lab_j2labels.label_lines(sample)).encode("utf-8")
    except (J2LabelsError, goldlabels.GoldLabelsError, loader.PackError, OSError, StrictJsonError, KeyError,
            TypeError, ValueError):
        return None, "malformed", ""
    sha = sha256_hex(data)
    if not sha.startswith(expected_prefix):
        return None, "sha256 mismatch", sha
    return {r["record_ref"] for r, _ in sample}, VERIFIED, sha


# --------------------------------------------------------------------------------------------------- the sources

@dataclass
class Established:
    """What the five sources gave: the refs of each verified source (``sets``, None otherwise), the rebuilt sha256
    of each rebuild (``shas``, ``""`` when not attempted or unbuildable), the serialisable summary (``summary``:
    ``sources``, ``cycles`` and ``consistency``, counts and outcomes only), the excluded union and the problem."""

    sets: dict[str, set[str] | None]
    shas: dict[str, str]
    summary: dict[str, Any]
    excluded: set[str] = field(default_factory=set)
    cycle_sets: dict[str, set[str] | None] = field(default_factory=dict)
    problem: str | None = None


def chain_entry(a001: Mapping[str, Any]) -> dict[str, Any]:
    """The entry ``lab.j2labels.earlier_refs`` reads K with: the second entry's run and artifact, its chain file and
    prefix."""
    return {"run_id": a001["run_id"], "artifact": a001["artifact"], "file": a001["chain_file"],
            "sha256_prefix": a001["chain_sha256_prefix"]}


def establish(earlier_plans: str | Path | None, j001: Mapping[str, Any], a001: Mapping[str, Any],
              fetch: Callable[[], bytes]) -> Established:
    """The five sources and the stop rules of item 5 as amended B5 (see the module docstring). ``j001`` and ``a001``
    are the request's two ``exclude`` entries (a stand-in's in a dry run), in seed order."""
    sets: dict[str, set[str] | None] = {}
    outcomes: dict[str, str] = {}
    shas = {"R1": "", "R2": ""}
    sets["J1"], outcomes["J1"] = lab_j2labels.earlier_refs(earlier_plans, j001)
    sets["R1"], outcomes["R1"], shas["R1"] = lab_j2labels.rebuild_refs(fetch, j001["rebuild_sha256_prefix"])
    sets["A1"], outcomes["A1"] = earlier_labels_refs(earlier_plans, a001)
    sets["K"], outcomes["K"] = lab_j2labels.earlier_refs(earlier_plans, chain_entry(a001))
    cycles: dict[str, dict[str, Any]] = {c: {"established": False, "by": None, "refs": None} for c in CYCLES}
    cycle_sets: dict[str, set[str] | None] = {"j001": None, "a001": None}
    # J001's set: J1 or R1; else K less A1 (by chain)
    own = [sets[s] for s in ("J1", "R1") if sets[s] is not None]
    if own:
        cycle_sets["j001"] = set().union(*own)
        cycles["j001"].update(established=True, by="artifact" if sets["J1"] is not None else "rebuild")
    elif sets["K"] is not None and sets["A1"] is not None:
        cycle_sets["j001"] = sets["K"] - sets["A1"]
        cycles["j001"].update(established=True, by="chain")
    # R2 is attempted over the eligible list less J001's set whenever that set is established
    if cycle_sets["j001"] is not None:
        sets["R2"], outcomes["R2"], shas["R2"] = rebuild_a001_refs(fetch, cycle_sets["j001"],
                                                                   a001["rebuild_sha256_prefix"],
                                                                   seed=a001["rebuild_seed"])
    else:
        sets["R2"], outcomes["R2"] = None, NOT_ATTEMPTED
    # A001's set: A1 or R2; else K less J001's set when J001's set is established by its own sources
    own = [sets[s] for s in ("A1", "R2") if sets[s] is not None]
    if own:
        cycle_sets["a001"] = set().union(*own)
        cycles["a001"].update(established=True, by="artifact" if sets["A1"] is not None else "rebuild")
    elif sets["K"] is not None and cycles["j001"]["by"] in ("artifact", "rebuild"):
        cycle_sets["a001"] = sets["K"] - cycle_sets["j001"]
        cycles["a001"].update(established=True, by="chain")
    for cycle in CYCLES:
        cycles[cycle]["refs"] = len(cycle_sets[cycle]) if cycle_sets[cycle] is not None else None
    # the consistency counts, each a stop above zero
    k = sets["K"]
    consistency: dict[str, int | None] = {
        "j1_r1": len(sets["J1"] ^ sets["R1"]) if sets["J1"] is not None and sets["R1"] is not None else None,
        "a1_r2": len(sets["A1"] ^ sets["R2"]) if sets["A1"] is not None and sets["R2"] is not None else None,
        "outside_chain": (len(set().union(*(sets[s] for s in ("J1", "R1", "A1", "R2") if sets[s] is not None)) - k)
                          if k is not None else None),
        "chain_union": (len(k ^ (cycle_sets["j001"] | cycle_sets["a001"]))
                        if k is not None and cycles["j001"]["by"] in ("artifact", "rebuild")
                        and cycles["a001"]["by"] in ("artifact", "rebuild") else None),
    }
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
    return Established(sets=sets, shas=shas, summary=summary, excluded=excluded, cycle_sets=cycle_sets,
                       problem=problem)


# --------------------------------------------------------------------------------------------------- the record

def excluded_runs(est: Established, used: tuple[Mapping[str, Any], Mapping[str, Any]],
                  requested: tuple[Mapping[str, Any], Mapping[str, Any]], stand_in: bool) -> list[dict[str, Any]]:
    """The labels record's ``exclusion.excluded_runs`` (B22): one entry per cycle in seed order, each with the
    artifact's outcome and refs found, the rebuild's seed, prefix, outcome and refs, for A001 the chain's, ``derived``
    ``by_chain`` when the cycle's set came from the chain, how the set was established, whether a stand-in was used
    and the entry as requested. Counts and hashes only."""
    out = []
    pairs = (("j001", "J1", "R1", None), ("a001", "A1", "R2", "K"))
    for (cycle, artifact, rebuild, chain), entry, asked in zip(pairs, used, requested):
        sources = est.summary["sources"]
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
    return out


def extend_exclusion(record: dict[str, Any], est: Established) -> dict[str, Any]:
    """Add the five sources' outcomes, the cycles and the consistency counts to a labels record's ``exclusion`` block
    (``lab.j2labels.build_labels``' record, extended as ``lab.prereg._j2`` extends A001's); returns the block."""
    exclusion = record["exclusion"]
    exclusion.update(sources=est.summary["sources"], cycles=est.summary["cycles"],
                     consistency=est.summary["consistency"], expected_excluded=EXPECTED_EXCLUDED)
    return exclusion


def mechanisms(est: Established) -> dict[str, str]:
    """J001's pair for ``exclusion.mechanisms``, as A001's record held it: ``artifact`` J1 and ``rebuild`` R1."""
    return {"artifact": est.summary["sources"]["J1"]["outcome"], "rebuild": est.summary["sources"]["R1"]["outcome"]}


def chain_bytes(excluded: set[str], drawn: set[str]) -> bytes:
    """Item 7's chain file for the next cycle: every excluded ref and the draw's, sorted, refs only."""
    return lab_j2labels.excluded_jsonl(excluded | drawn)
