"""B4: the third built-in pack, ``it_incidents`` (multi-site IT operations incidents at a fictional group), built as
pure data, and the evidence under ``docs/collective/x3``.

**Same author as the generic code; internal only; not X3 by a non-author; not a buyer claim.** The pack and these
tests were written by the AI system that wrote the generic code, so nothing here measures STRATEGY section 11.2's X3
(a pack built by someone else, with the engineer-hours recorded).

* PackTests: the loader, the four pinned hashes, ``pack.json``, the egress policy, the rules and follow-up scope.
* FixtureTests: the hand-labelled fixtures, their coverage and the lexical scores recorded in ``effort.json``.
* ExportTests: a fictional ticketing export through ``mapping.json``.
* GeneratorTests: the seeded world of the pack.
* PipelineTests: a small G0 run and the plant smoke against a world and an in-test prereg.
* EvidenceTests, DemoTests: the committed G0, X1 smoke and demo run files.
* EffortTests, GapTests, PointerTests: ``effort.json`` recomputed (pack files, line counts, git numstats), every
  recorded gap reproduced on a copy of the pack, every doc pointer resolved.

The effort helpers (:func:`pack_file_rows`, :func:`lines_changed`, :func:`numstat`, :func:`structural_summary`,
:func:`fixture_scores`, :func:`presentation_gaps`, :data:`GAP_PROBES`) are pure functions of the repository; the
numbers in ``effort.json`` were generated with them. The git checks read the base commit and the commit that added
``effort.json`` (or the worktree with untracked files before that commit exists), so later commits never change
them; in a shallow clone without the base commit they assert that the clone is shallow and check only the internal
consistency of the recorded numbers.
"""
from __future__ import annotations

import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from demo.collective import collective_demo as demo
from demo.collective import lint_numbers
from demo.collective.scenario import ITEM_KEYS, TOP_KEYS, load_scenario
from mycelic.collective import runfiles
from mycelic.collective.edge.extract import LexicalExtractor, codes_channel, pair
from mycelic.collective.evaluate import harness as H
from mycelic.collective.evaluate import plant as P
from mycelic.collective.experiments import e1_extract, e2_pushdown, openfda_replay, x5_inference
from mycelic.collective.experiments.e1_extract import micro_f1, record_counts
from mycelic.collective.jsonio import strict_load
from mycelic.collective.packs.canonical import Canonicaliser, split_sentences
from mycelic.collective.packs.connector import map_rows, record_problems
from mycelic.collective.packs.generator import generate, world_digest
from mycelic.collective.packs.loader import BUILTIN_ROOT, FrozenPack, PackError, load_pack, thaw
from tests.mycelic.test_collective_guards import model_name_hits
from tests.mycelic.test_collective_leakage import run_main
from tests.mycelic.test_collective_pushdown import B4_HASHES

ROOT = Path(__file__).resolve().parents[2]
PACK_ID = "it_incidents"
TEMPLATE_ID = "device_quality"
PACK_REL = f"mycelic/collective/packs/data/{PACK_ID}"
TEMPLATE_REL = f"mycelic/collective/packs/data/{TEMPLATE_ID}"
X3 = ROOT / "docs" / "collective" / "x3"
X3_REL = "docs/collective/x3"
EFFORT_REL = f"{X3_REL}/effort.json"
REQUIREMENTS_REL = f"{X3_REL}/REQUIREMENTS.md"
DEMO_DIR = X3 / "demo" / "x3-it-incidents-demo"
BASE_COMMIT = "1cee4312617def1dda86a9cc408bf6bd5553c30c"
STATEMENT = "same author as the generic code; internal only; not X3 by a non-author; not a buyer claim"
WALL_CLOCK_LABEL = "AI agent wall clock, not engineer-hours"
F1_LABEL = "hand-labelled by the same author; not a measurement"
DIFF_METHOD = ("lines_changed_vs_template is the sum of (j2 - j1) over the non-equal opcodes of "
               "difflib.SequenceMatcher(None, template_lines, pack_lines, autojunk=False) against the same-named file "
               "of the template pack, or every line when the template has no such file; lines are "
               "text.splitlines() and bytes the file size")
CODE_PATHSPEC = ("mycelic", "demo", f":(exclude){PACK_REL}")
TEST_PATHSPEC = ("tests/mycelic/test_collective_*.py",)
DOCS_PATHSPEC = ("docs/collective/*.md",)
STATUSES = ("expressible", "approximated", "not_expressible")
CODE_HASHES = {"X1": "e7d5d82e3b9805358718b5927b6cca89bb4912c733fccd7f14c89f61a9234161",
               "E1": "07c08295d7eeeff6eadcaa1db80ee5004b3dacf3ceb415c1e3ae126ca67ec229",
               "E2": "7428e57c6c9e2b5f4e7d7a93d80a45bc4a06694a3d01b274c12617e69b1d32be",
               "openfda_replay": "452219e81aa711fb97387d8bc5da9663e7765af8f8f5b17ae6617e8acb0b9525",
               "X5": "dea481ca619129e1f42f685256834e81ec76e27995287fb21b54c33025d01816"}
# audit round 4 (finding 3, A1 per artifact type, and finding 2, the prereg's rehearsal note) changed
# x5_attacks.py and x5_inference.py after B4b; effort.json keeps the hashes B4b recorded
X5_CODE_HASH_R4 = "73a4e487727a7e665301d499a7d5d9c66f7a9b90c81e1445a72ce9bd49d03516"
EFFORT_KEYS = ("attempts", "base_commit", "code_files_changed", "code_hashes", "code_lines_changed", "code_numstat",
               "demo_lint", "diff_method", "docs_lines_added", "evidence", "files", "fit_to_code_choices",
               "fixture_disagreements", "fixture_lexical_f1", "gaps", "kind", "logic_gaps", "presentation_gaps",
               "requirements", "requirements_added_after_b4a", "requirements_commit", "requirements_sha256",
               "schema_version", "statement", "structural_diff_vs_template", "template_files_sha256",
               "template_pack", "term_renames", "test_lines_added", "totals", "wall_clock")
GAP_KEYS = ("blocking", "file", "fixed", "id", "lines_changed", "minimal_generic_fix", "requirement_id", "symptom",
            "why_data_cannot")
# the words of the device pack's field that a screen text of another field should not carry (presentation gaps)
DEVICE_NOUNS = re.compile(r"\b(plants?|complaints?|capa|scar|devices?|lots?|suppliers?|products?|patients?|"
                          r"clinicians?)\b", re.IGNORECASE)
PRESENTATION_FILES = ("demo/collective/screen.py", "demo/collective/collective_demo.py", "demo/collective/console.html")
REQUIRED_POINTERS = (
    f"{X3_REL}/effort.json#/totals", f"{X3_REL}/effort.json#/code_lines_changed",
    f"{X3_REL}/effort.json#/test_lines_added", f"{X3_REL}/effort.json#/gaps",
    f"{X3_REL}/effort.json#/presentation_gaps", f"{X3_REL}/effort.json#/logic_gaps",
    f"{X3_REL}/effort.json#/wall_clock", f"{X3_REL}/g0/leakage.json#/hits",
    f"{X3_REL}/g0/leakage.json#/positive_control", f"{X3_REL}/x1_smoke/scorecard.json#/channels",
    f"{X3_REL}/demo/x3-it-incidents-demo/scorecard.json#/checks",
)
PACK = load_pack(PACK_ID)
TEMPLATE = load_pack(TEMPLATE_ID)


def effort() -> dict[str, Any]:
    return strict_load((ROOT / EFFORT_REL).read_bytes())


# =================================================================================================== git helpers

def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def has_commit(sha: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True,
                          timeout=60).returncode == 0


def effort_endpoint() -> str | None:
    """The commit that added ``effort.json`` (the oldest such), or None: the worktree, before B4b is committed."""
    shas = git("log", "--diff-filter=A", "--format=%H", "--", EFFORT_REL).split()
    return shas[-1] if shas else None


def read_at(endpoint: str | None, rel: str) -> bytes:
    """A file's bytes at the endpoint commit, or in the worktree."""
    if endpoint is None:
        return (ROOT / rel).read_bytes()
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{endpoint}:{rel}"], capture_output=True, check=True,
                          timeout=60).stdout


def files_at(endpoint: str | None, rel_dir: str) -> list[str]:
    """Repository-relative paths of the files under ``rel_dir`` (tracked at the endpoint, or on disk)."""
    if endpoint is None:
        root = ROOT / rel_dir
        return sorted(p.relative_to(ROOT).as_posix() for p in root.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts)
    return sorted(git("ls-tree", "-r", "--name-only", endpoint, "--", rel_dir).split())


def numstat(pathspec: Sequence[str], endpoint: str | None) -> list[dict[str, Any]]:
    """``git diff --numstat`` from the base commit to the endpoint over ``pathspec`` (renames off); in worktree mode
    untracked files are added whole."""
    args = ["diff", "--numstat", "--no-renames", BASE_COMMIT, *([endpoint] if endpoint else []), "--", *pathspec]
    rows = []
    for line in git(*args).splitlines():
        added, deleted, path = line.split("\t")
        rows.append({"path": path, "added": int(added), "deleted": int(deleted)})
    if endpoint is None:
        for path in git("ls-files", "--others", "--exclude-standard", "--", *pathspec).split():
            rows.append({"path": path, "added": len((ROOT / path).read_text(encoding="utf-8").splitlines()),
                         "deleted": 0})
    return sorted(rows, key=lambda r: r["path"])


def numstat_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"added": sum(r["added"] for r in rows), "deleted": sum(r["deleted"] for r in rows), "files": list(rows)}


# =================================================================================================== effort helpers

def lines_changed(template_lines: Sequence[str] | None, pack_lines: Sequence[str]) -> int:
    """See :data:`DIFF_METHOD`."""
    if template_lines is None:
        return len(pack_lines)
    matcher = difflib.SequenceMatcher(None, list(template_lines), list(pack_lines), autojunk=False)
    return sum(j2 - j1 for tag, _, _, j1, j2 in matcher.get_opcodes() if tag != "equal")


def pack_file_rows(endpoint: str | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One row per file of the pack directory at the endpoint, and the totals."""
    template = set(files_at(endpoint, TEMPLATE_REL))
    rows = []
    for rel in files_at(endpoint, PACK_REL):
        data = read_at(endpoint, rel)
        lines = data.decode("utf-8").splitlines()
        twin = TEMPLATE_REL + rel[len(PACK_REL):]
        template_lines = read_at(endpoint, twin).decode("utf-8").splitlines() if twin in template else None
        rows.append({"path": rel, "lines": len(lines), "bytes": len(data),
                     "lines_changed_vs_template": lines_changed(template_lines, lines)})
    totals = {"files": len(rows), "lines": sum(r["lines"] for r in rows), "bytes": sum(r["bytes"] for r in rows),
              "lines_changed_vs_template": sum(r["lines_changed_vs_template"] for r in rows)}
    return rows, totals


def template_files_sha256(endpoint: str | None) -> dict[str, str]:
    return {rel[len(TEMPLATE_REL) + 1:]: hashlib.sha256(read_at(endpoint, rel)).hexdigest()
            for rel in files_at(endpoint, TEMPLATE_REL)}


def structural_summary(pack: FrozenPack) -> dict[str, Any]:
    """What a pack's structure needs from the generic code, read from the frozen pack."""
    m = pack.mapping()
    links = pack.generator["links"]
    depth = 0
    for child in links:
        hops, node = 0, child
        while node in links:
            node, hops = links[node]["parent"], hops + 1
        depth = max(depth, hops)
    paths = [m["record_ref"], m["received_date"]["path"], *(c["path"] for c in m["codes"]),
             *(p for ps in m["entities"].values() for p in ps), *(n["path"] for n in m["narrative"])]
    return {
        "entity_types": sorted(pack.entity_types),
        "id_format_kinds": {t: [kind for kind, _ in et.id_format.segments]
                            for t, et in sorted(pack.entity_types.items()) if et.id_format is not None},
        "separators": {t: et.separator for t, et in sorted(pack.entity_types.items()) if et.id_format is not None},
        "alias_only_types": sorted(t for t, et in pack.entity_types.items() if et.id_format is None),
        "egress_types": list(pack.egress.egress_entity_types),
        "non_egress_types": sorted(t for t, et in pack.entity_types.items() if not et.egress),
        "link_depth": depth,
        "languages": list(pack.languages),
        "predicates": len(pack.predicates),
        "person_fields": sorted(m["persons"]),
        "mapping_features": {"code_value_map": any(c["value_map"] is not None for c in m["codes"]),
                             "narrative_where": any(n["where"] is not None for n in m["narrative"]),
                             "fan_out": any("[]" in p for p in paths),
                             "forward_origin": m["origin_ref"] is not None,
                             "site_path": m["site"] is not None,
                             "date_format": m["received_date"]["format"]},
    }


def structural_diff() -> dict[str, Any]:
    return {"template": structural_summary(TEMPLATE), "pack": structural_summary(PACK), "link_depth_required": 2}


def claim_tuples(claims: Iterable[Any]) -> list[tuple[str, str, str | None, bool]]:
    return sorted(((c.entity_type, c.entity_id, c.predicate, bool(c.negated)) for c in claims),
                  key=lambda c: (c[0], c[1], c[2] or "", c[3]))


def lexical_claims(pack: FrozenPack, record: dict[str, Any]) -> list[tuple[str, str, str | None, bool]]:
    canon = Canonicaliser(pack)
    return claim_tuples(LexicalExtractor(pack, canon).extract(record, codes_channel(record, pack, canon)).claims)


def fixture_scores(pack: FrozenPack = PACK) -> tuple[dict[str, Any], list[str]]:
    """The lexical extractor's micro field F1 over the fixtures (E1's metric functions) and the refs of the fixtures
    whose claims differ from their labels."""
    counts, differ = [], []
    for fx in pack.fixtures:
        record = thaw(fx.record)
        got = lexical_claims(pack, record)
        want = claim_tuples(fx.gold)
        as_dicts = lambda rows: [{"entity_type": t, "entity_id": i, "predicate": p, "negated": n}  # noqa: E731
                                 for t, i, p, n in rows]
        counts.append(record_counts(as_dicts(got), as_dicts(want))["field"])
        if got != want:
            differ.append(record["record_ref"])
    score = micro_f1(counts)
    return {"value": score["value"], "tp": score["tp"], "fp": score["fp"], "fn": score["fn"],
            "records": len(pack.fixtures)}, differ


def _string_contexts(source: str) -> list[tuple[int, str, str]]:
    """``(line, enclosing top-level name or function, text)`` of every string constant (f-string parts included,
    docstrings excluded)."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    out: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, context: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            context = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and context == "<module>":
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            context = names[0] if names else context
            for child in ast.iter_child_nodes(node):
                visit(child, context)
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append((node.lineno, context, node.value))
        for child in ast.iter_child_nodes(node):
            visit(child, context)

    visit(tree, "<module>")
    return out


def presentation_gaps(endpoint: str | None = None) -> list[dict[str, str]]:
    """Every screen text of the demo that names a device-field noun: string constants of ``screen.py``, the texts
    of ``collective_demo.CHECK_TEXTS`` and the lines of ``console.html``; one entry per distinct literal and context,
    in file and line order."""
    found: list[tuple[str, int, str, str]] = []
    for rel in PRESENTATION_FILES:
        text = read_at(endpoint, rel).decode("utf-8")
        if rel.endswith(".html"):
            items = [(n, "console", line.strip()) for n, line in enumerate(text.splitlines(), start=1)]
        else:
            items = _string_contexts(text)
            if rel.endswith("collective_demo.py"):
                items = [i for i in items if i[1] == "CHECK_TEXTS"]
        found += [(rel, line, context, literal) for line, context, literal in items if DEVICE_NOUNS.search(literal)]
    out, seen = [], set()
    for rel, _, context, literal in sorted(found, key=lambda f: (PRESENTATION_FILES.index(f[0]), f[1])):
        if (rel, context, literal) in seen:
            continue
        seen.add((rel, context, literal))
        nouns = []
        for m in DEVICE_NOUNS.finditer(literal):
            if m.group().lower() not in nouns:
                nouns.append(m.group().lower())
        out.append({"id": f"G4-P{len(out) + 1:02d}", "file": rel, "constant_or_function": context,
                    "literal": literal, "noun": ", ".join(nouns)})
    return out


def requirement_ids(text: str) -> list[str]:
    return re.findall(r"^\| (R\d{2}) \|", text, flags=re.MULTILINE)


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901: ``''`` is the document; each ``/``-separated token unescapes ``~1`` to ``/`` and ``~0`` to ``~``."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = doc
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def pointer_rows(text: str, heading: str) -> list[str]:
    """The first cells of the table rows in the section under ``heading`` (up to the next heading)."""
    section = text.split(heading + "\n", 1)[1]
    section = re.split(r"\n#{2,4} ", section, maxsplit=1)[0]
    cells = []
    for line in section.splitlines():
        if line.startswith("| ") and not re.fullmatch(r"\|[\s:|-]+\|?", line):
            cells.append(line.split("|")[1].strip())
    return cells[1:]                                                      # without the header row


# =================================================================================================== gap probes

def _copy(tmp: Path, name: str) -> Path:
    d = tmp / name
    shutil.copytree(BUILTIN_ROOT / PACK_ID, d)
    return d


def _edit(d: Path, name: str, fn: Callable[[Any], None]) -> None:
    path = d / name
    obj = json.loads(path.read_text(encoding="utf-8"))
    fn(obj)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_error(d: Path) -> str:
    try:
        load_pack(str(d))
    except PackError as err:
        return str(err)
    return "loaded"


def _fixture(ref: str) -> Any:
    return next(fx for fx in PACK.fixtures if fx.record["record_ref"] == ref)


def _fixture_symptom(ref: str) -> list[str]:
    fx = _fixture(ref)
    return [f"fixture {ref}: lexical claims {lexical_claims(PACK, thaw(fx.record))}, labels {claim_tuples(fx.gold)}"]


def _id_format(segments: list[dict[str, Any]], egress: bool, label: str) -> dict[str, Any]:
    return {"label": label, "id_format": segments, "separator": ".", "case": "upper", "strip_leading_zeros": False,
            "exact_match_metric": True, "egress": egress, "ids": None}


def probe_r03(tmp: Path) -> list[str]:
    d = _copy(tmp, "r03")
    octet = {"digit": [1, 3]}
    segments = [octet, {"sep": "required"}, octet, {"sep": "required"}, octet, {"sep": "required"}, octet]
    _edit(d, "vocabulary.json",
          lambda v: v["entity_types"].__setitem__("ip_address", _id_format(segments, False, "IP address")))
    return [_load_error(d)]


def probe_r04(tmp: Path) -> list[str]:
    d = _copy(tmp, "r04")
    segments = [{"digit": [1, 2]}, {"sep": "required"}, {"digit": [1, 2]}, {"sep": "required"}, {"digit": [1, 3]}]
    _edit(d, "vocabulary.json",
          lambda v: v["entity_types"].__setitem__("software_version", _id_format(segments, True, "Software version")))
    return [_load_error(d)]


def probe_r06(tmp: Path) -> list[str]:
    d = _copy(tmp, "r06")
    _edit(d, "vocabulary.json", lambda v: v["entity_types"].__setitem__("business_service", {
        "label": "Business service", "id_format": None, "separator": None, "case": "upper",
        "strip_leading_zeros": False, "exact_match_metric": False, "egress": False, "ids": ["CORPORATE-IT"]}))
    _edit(d, "aliases.json", lambda a: a.__setitem__("business_service", {"corporate IT": "CORPORATE-IT"}))

    def two_hops(g: dict[str, Any]) -> None:
        g["universe"]["business_service"] = ["CORPORATE-IT"]
        g["links"]["it_service"] = {"parent": "business_service",
                                    "map": {"CORPORATE-IT": list(g["universe"]["it_service"])}}

    _edit(d, "generator.json", two_hops)
    return [_load_error(d)]


def probe_r10(tmp: Path) -> list[str]:
    d = _copy(tmp, "r10")
    _edit(d, "mapping.json", lambda m: m["received_date"].__setitem__("path", "opened_at"))
    value = "2024-03-04T08:15:00+01:00"
    row = {"number": "INC0200001", "company": "it-ashcombe", "opened_at": value, "language": "en",
           "short_description": "VPN down", "description": "Users of the VPN gateway saw dropped packets."}
    return [f"map_rows: rejected {dict(map_rows([row], load_pack(str(d))).rejected)} for opened_at {value}"]


def probe_r09(tmp: Path) -> list[str]:
    field = _copy(tmp, "r09-field")
    _edit(field, "mapping.json", lambda m: m.__setitem__("priority", "priority"))
    codes = _copy(tmp, "r09-codes")
    _edit(codes, "codes.json", lambda c: c.__setitem__(
        "P1", {"label": "Priority one, illustrative placeholder", "predicate": None, "specific": True}))
    return [_load_error(field), _load_error(codes)]


def probe_r05(tmp: Path) -> list[str]:
    d = _copy(tmp, "r05")
    _edit(d, "vocabulary.json", lambda v: v["entity_types"]["it_service"]["ids"].append("VPN"))

    def acronym(a: dict[str, Any]) -> None:
        del a["it_service"]["VPN"]
        a["it_service"]["vpn"] = "VPN"

    _edit(d, "aliases.json", acronym)
    return [_load_error(d)]


def probe_r07(tmp: Path) -> list[str]:
    d = _copy(tmp, "r07")
    _edit(d, "mapping.json", lambda m: m.__setitem__("change_window", {"start": "planned_start",
                                                                         "end": "planned_end"}))
    return [_load_error(d)]


def probe_r11(tmp: Path) -> list[str]:
    d = _copy(tmp, "r11")
    _edit(d, "generator.json", lambda g: g.__setitem__("alert_storm_rate", 0.05))
    world = generate(PACK, seed=7, sites=6, weeks=52)
    pools = {s["id"]: s["reporters"] for s in PACK.generator["sites"]}
    shares, whole = [], True
    for site, size in pools.items():
        counts = Counter(r["reporter"] for r in world.records if r["site"] == site and r["origin_ref"] is None)
        whole = whole and len(counts) == size
        shares.append(max(counts.values()) / sum(counts.values()))
    return [_load_error(d), f"seed-7 world: every site's independent records use its whole reporter pool: {whole}; "
                            f"the largest share of one reporter at a site is {max(shares):.2f}"]


def probe_r15(tmp: Path) -> list[str]:
    d = _copy(tmp, "r15")
    _edit(d, "generator.json", lambda g: g["codes"].__setitem__("generic_rate", [0.3, 0.5]))
    return [_load_error(d)]


def probe_r26(tmp: Path) -> list[str]:
    languages = _copy(tmp, "r26-languages")
    _edit(languages, "pack.json", lambda p: p.__setitem__("languages", ["en-GB", "de-DE"]))
    value_map = _copy(tmp, "r26-value-map")
    _edit(value_map, "mapping.json",
          lambda m: m.__setitem__("language", {"path": "language", "value_map": {"de-DE": "de", "en-GB": "en"}}))
    record = {"language": "de-DE", "codes": [], "entities": {t: [] for t in PACK.mapping()["entities"]},
              "narrative": "Der Druckdienst ist ausgefallen.", "persons": {f: None for f in PACK.mapping()["persons"]},
              "reporter": None}
    canon = Canonicaliser(PACK)
    result = LexicalExtractor(PACK, canon).extract(record, codes_channel(record, PACK, canon))
    return [_load_error(languages), _load_error(value_map),
            f"a de-DE record: language_supported {result.language_supported}, claims {claim_tuples(result.claims)}"]


def probe_r18(tmp: Path) -> list[str]:
    text = "<p>The mailbox is fine.</p><p>The printers went down after lunch.</p>"
    return [f"split_sentences: {len(split_sentences(text))} span(s) for {text!r}", *_fixture_symptom("INC0100020")]


def probe_r02(tmp: Path) -> list[str]:
    d = _copy(tmp, "r02")
    _edit(d, "generator.json", lambda g: g.__setitem__("universe", {"it-ashcombe": g["universe"]}))
    world = generate(PACK, seed=7, sites=6, weeks=52)
    foreign = sum(1 for r in world.records if r["site"] == "it-ashcombe" and any(
        g["entity_type"] == "config_item" and not g["entity_id"].startswith("ash-")
        for g in world.gold[r["record_ref"]]))
    return [_load_error(d), f"seed-7 world: {foreign} records at it-ashcombe name a configuration item with another "
                            f"subsidiary's site code"]


def probe_r01(tmp: Path) -> list[str]:
    world = generate(PACK, seed=7, sites=6, weeks=52)
    sites = "|".join(re.escape(s) for s in world.params["site_ids"])
    site_form = all(re.fullmatch(rf"(?:{sites})-[0-9]{{6}}", r["record_ref"]) for r in world.records)
    inc = sum(1 for r in world.records if re.fullmatch(r"INC[0-9]{7}", r["record_ref"]))
    return [f"seed-7 world: every record_ref is '<site id>-<six digits>': {site_form} (first "
            f"{world.records[0]['record_ref']}); {inc} are INC followed by seven digits"]


def probe_r12(tmp: Path) -> list[str]:
    base = {"opened_date": "2024-03-04", "language": "en", "category": "Network / Packet loss",
            "short_description": "Branch link", "description": "Users of the VPN gateway saw dropped packets."}
    rows = [{**base, "number": "INC0300001", "company": "it-ashcombe"},
            {**base, "number": "INC0300002", "company": "it-dornfeld",
             "forwarded_from": {"number": "INC0300001", "company": "it-ashcombe"}},
            {**base, "number": "INC0300003", "company": "it-calderwick"}]
    origins = [(r["record_ref"], r["origin_ref"]) for r in map_rows(rows, PACK).records]
    return [f"map_rows: (record_ref, origin_ref) {origins}; the re-keyed copy INC0300003 is its own root",
            f"plant hard-case decoy classes: {list(P.HARD_CASE_CLASSES)}"]


GAP_PROBES: dict[str, Callable[[Path], list[str]]] = {
    "G4-01": probe_r03, "G4-02": probe_r04, "G4-03": probe_r06, "G4-04": probe_r10, "G4-05": probe_r09,
    "G4-06": probe_r05, "G4-07": probe_r07, "G4-08": probe_r11, "G4-09": probe_r15, "G4-10": probe_r26,
    "G4-11": probe_r18, "G4-12": lambda tmp: _fixture_symptom("INC0100017"),
    "G4-13": lambda tmp: _fixture_symptom("INC0100018"), "G4-14": lambda tmp: _fixture_symptom("INC0100013"),
    "G4-15": lambda tmp: _fixture_symptom("INC0100045"), "G4-16": probe_r02, "G4-17": probe_r01,
    "G4-18": probe_r12,
}


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)


# =================================================================================================== the pack

def proposable(pack: FrozenPack, type_id: str, entity_type: str) -> bool:
    return all(spec["entity_type"] == entity_type for spec in pack.followups[type_id].args.values()
               if spec["kind"] == "entity_id")


class PackTests(unittest.TestCase):
    def test_builtin_load_and_pinned_hashes(self) -> None:
        self.assertEqual((PACK.id, PACK.version, PACK.source), (PACK_ID, "0.1.0", "builtin"))
        self.assertEqual(PACK.hashes(), B4_HASHES[PACK_ID])

    def test_the_loader_cli_prints_the_pack_and_four_hashes(self) -> None:
        r = subprocess.run([sys.executable, "-m", "mycelic.collective.packs.loader", "check", PACK_ID], cwd=ROOT,
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"pack: {PACK_ID} 0.1.0 (source builtin)")
        self.assertEqual([line.split(":")[0] for line in lines[1:]], list(PACK.hashes()))
        for line, value in zip(lines[1:], PACK.hashes().values()):
            self.assertIn(value, line)

    def test_pack_json_says_what_it_is(self) -> None:
        self.assertTrue(PACK.illustrative)
        self.assertTrue(PACK.same_author_as_code)
        self.assertEqual(PACK.languages, ("en", "de"))
        for phrase in ("fictional", "same author as the generic code", "not X3 by a non-author", "never used"):
            self.assertIn(phrase, PACK.disclaimer)
        self.assertNotIn("official", PACK.disclaimer.lower())

    def test_egress_policy(self) -> None:
        egress = PACK.egress
        self.assertFalse(PACK.entity_types["config_item"].egress)
        self.assertIn("entities.config_item", egress.never_fields)
        self.assertEqual(set(egress.central_allowed_fields),
                         {"codes", "received_date", "site", *(f"entities.{t}" for t in egress.egress_entity_types)})
        self.assertEqual(set(egress.egress_entity_types),
                         {"change_request", "it_service", "software_release", "vendor"})
        self.assertTrue(egress.require_master_data)
        self.assertEqual((egress.k, egress.verdict_count_buckets), (3, (3, 10, 50)))
        for field in ("narrative", "reporter", *(f"persons.{p}" for p in PACK.mapping()["persons"])):
            self.assertIn(field, egress.never_fields)

    def test_no_rule_names_the_demo_key(self) -> None:
        self.assertFalse([r for r in PACK.rules.values()
                          if (r.entity_type, r.predicate) == ("software_release", "crash_after_update")])
        self.assertEqual(PACK.detectors, TEMPLATE.detectors)                 # a byte copy of the template's file
        self.assertEqual((BUILTIN_ROOT / PACK_ID / "detectors.json").read_bytes(),
                         (BUILTIN_ROOT / TEMPLATE_ID / "detectors.json").read_bytes())

    def test_the_vendor_draft_is_proposable_exactly_on_vendor_keys(self) -> None:
        for type_id in sorted(PACK.followups):
            for entity_type in PACK.egress.egress_entity_types:
                with self.subTest(type=type_id, entity_type=entity_type):
                    expected = entity_type == "vendor" if type_id == "vendor_escalation_draft" else True
                    self.assertEqual(proposable(PACK, type_id, entity_type), expected)
        self.assertEqual(sorted(PACK.roles), ["it_operations_lead", "problem_manager", "vendor_manager"])


# =================================================================================================== fixtures

class FixtureTests(unittest.TestCase):
    def test_fixtures_pass_the_record_check_and_hold_enough_claims(self) -> None:
        self.assertGreaterEqual(len(PACK.fixtures), 40)
        canon = Canonicaliser(PACK)
        stored = 0
        for fx in PACK.fixtures:
            record = thaw(fx.record)
            self.assertEqual(record_problems(record, PACK), [])
            codes = codes_channel(record, PACK, canon)
            stored += len(pair(codes, LexicalExtractor(PACK, canon).extract(record, codes)))
        self.assertGreaterEqual(stored, 40)

    def test_coverage(self) -> None:
        gold = [(fx, g) for fx in PACK.fixtures for g in fx.gold]
        by_predicate = Counter(g.predicate for _, g in gold if g.predicate is not None)
        for p in PACK.predicates:
            self.assertGreaterEqual(by_predicate[p], 2, p)
        negated_languages = {fx.record["language"] for fx, g in gold if g.negated}
        self.assertLessEqual({"en", "de"}, negated_languages)
        canon = Canonicaliser(PACK)
        methods = {m.method for fx in PACK.fixtures for m in canon.scan(fx.record["narrative"]).mentions}
        self.assertEqual(methods, {"exact", "variant", "alias"})
        texts = {fx.record["record_ref"]: fx.record["narrative"] for fx in PACK.fixtures}
        near_miss = [ref for ref, text in texts.items()
                     if re.search(r"VND-[0-9]{4}|REL/[0-9]{4}/[0-9]{2}", text) and not _fixture(ref).gold]
        self.assertTrue(near_miss)
        self.assertTrue(any(g.predicate is None for _, g in gold))
        self.assertTrue(any(not fx.gold for fx in PACK.fixtures))
        self.assertTrue(any(fx.record["persons"]["requester_name"] and fx.record["persons"]["requester_email"]
                            and fx.record["persons"]["requester_name"] in fx.record["narrative"]
                            and fx.record["persons"]["requester_email"] in fx.record["narrative"]
                            for fx in PACK.fixtures))
        self.assertTrue(any("<p>" in text for text in texts.values()))
        self.assertTrue(any(fx.gold and fx.record["codes"] and not (
            {PACK.codes[c].predicate for c in fx.record["codes"]} & {g.predicate for g in fx.gold})
            for fx in PACK.fixtures))
        gold_ids = {g.entity_id for _, g in gold}
        for pattern in (r"[a-z]+\.corp\.example", r"\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b", r"\b[0-9]+\.[0-9]+\.[0-9]+\b"):
            hits = [m.group() for text in texts.values() for m in re.finditer(pattern, text)]
            self.assertTrue(hits, pattern)
            self.assertFalse(set(hits) & gold_ids)
        mixed = [fx for fx in PACK.fixtures if fx.record["language"] is None]
        self.assertTrue(mixed)
        self.assertTrue(any(re.search("[äöüß]", fx.record["narrative"]) or "Absturz" in fx.record["narrative"]
                            for fx in mixed))
        self.assertEqual(len({fx.record["record_ref"] for fx in PACK.fixtures}), len(PACK.fixtures))
        self.assertTrue(all(re.fullmatch(r"INC[0-9]{7}", fx.record["record_ref"]) for fx in PACK.fixtures))

    def test_lexical_scores_and_disagreements_equal_effort_json(self) -> None:
        score, differ = fixture_scores()
        again, differ_again = fixture_scores()
        self.assertEqual((score, differ), (again, differ_again))
        recorded = effort()
        self.assertEqual({k: v for k, v in recorded["fixture_lexical_f1"].items() if k not in ("label", "metric")},
                         score)
        self.assertEqual(recorded["fixture_lexical_f1"]["label"], F1_LABEL)
        self.assertEqual([d["record_ref"] for d in recorded["fixture_disagreements"]], differ)
        gap_ids = {g["id"] for g in recorded["gaps"]}
        for d in recorded["fixture_disagreements"]:
            self.assertTrue(d["reason"])
            self.assertIn(d["gap_id"], gap_ids | {None})


# =================================================================================================== the export

EXPORT_ROWS = [
    {"number": "INC0400001", "company": "it-ashcombe", "opened_date": "2024-03-04",
     "opened_at": "2024-03-04T08:15:00+00:00", "language": "en", "category": "Network / Packet loss",
     "service": "REMOTE-ACCESS", "cmdb_ci": "cal-gw-0601", "affected_releases": ["REL/2403/12", "REL/2403/12"],
     "vendor": {"id": "VND-0412", "name": "Kestrelnet Systems"}, "caused_by_change": "CHG0040257",
     "short_description": "VPN slow", "description": "<p>Users of the VPN gateway saw dropped packets.</p>",
     "journal": [{"kind": "work_note", "text": "Packet loss started after change CHG0040257."},
                 {"kind": "system", "text": "SECRET-SYSTEM-ENTRY state changed."},
                 {"kind": "comment", "text": "Thanks, we are looking into it."}],
     "requester_name": "Anna Berger", "requester_email": "anna.berger@corp.example",
     "requester_phone": "555-014-2231", "assignee_name": "Omar Tanaka", "opened_by": "Monitoring agent",
     "priority": "P2", "impact": "SECRET-IMPACT"},
    {"number": "INC0400002", "company": "it-dornfeld", "opened_date": "2024-03-05", "language": "de",
     "category": "Other", "service": "REMOTE-ACCESS",
     "short_description": "VPN langsam", "description": "Benutzer des Dienstes VPN melden Paketverluste.",
     "journal": [], "opened_by": "Service desk, phone",
     "forwarded_from": {"number": "INC0400001", "company": "it-ashcombe"}},
    {"number": "INC0400003", "company": "it-calderwick", "opened_date": "2024-03-06", "language": "en",
     "category": "Unmapped tool label", "short_description": "Printer", "description": "The printers went down.",
     "journal": [{"kind": "comment", "text": "Restarted the print service."}]},
]


class ExportTests(unittest.TestCase):
    def test_a_fictional_export_maps_to_clean_records(self) -> None:
        mapped = map_rows(EXPORT_ROWS, PACK)
        self.assertEqual((mapped.rows, mapped.rejected, mapped.unmapped_code_values), (3, {}, 1))
        a, b, c = mapped.records
        for r in mapped.records:
            self.assertEqual(record_problems(r, PACK), [])
        self.assertEqual((a["codes"], b["codes"], c["codes"]), (["ITC-1800"], ["ITC-9000"], []))
        self.assertEqual(dict(a["entities"]), {"change_request": ["CHG0040257"], "config_item": ["cal-gw-0601"],
                                               "it_service": ["REMOTE-ACCESS"], "software_release": ["REL/2403/12"],
                                               "vendor": ["VND-0412"]})
        self.assertEqual(a["narrative"], "VPN slow\n\n<p>Users of the VPN gateway saw dropped packets.</p>\n\n"
                                         "Packet loss started after change CHG0040257.\n\n"
                                         "Thanks, we are looking into it.")
        self.assertEqual(a["persons"], {"assignee_name": "Omar Tanaka", "requester_email": "anna.berger@corp.example",
                                        "requester_name": "Anna Berger", "requester_phone": "555-014-2231"})
        self.assertEqual((a["reporter"], a["received_date"], a["site"]), ("Monitoring agent", "2024-03-04",
                                                                          "it-ashcombe"))
        self.assertEqual((b["origin_ref"], b["origin_site"]), ("INC0400001", "it-ashcombe"))
        self.assertEqual((c["origin_ref"], c["persons"]["requester_name"]), (None, None))
        text = json.dumps(mapped.records)
        for secret in ("SECRET-SYSTEM-ENTRY", "SECRET-IMPACT", "P2", "opened_at", "Kestrelnet"):
            self.assertNotIn(secret, text)
        claims = lexical_claims(PACK, dict(a))
        self.assertIn(("it_service", "REMOTE-ACCESS", "packet_loss", False), claims)
        self.assertIn(("change_request", "CHG0040257", "packet_loss", False), claims)

    def test_a_datetime_only_received_date_is_rejected(self) -> None:
        row = {**EXPORT_ROWS[0], "number": "INC0400004", "opened_date": "2024-03-04T08:15:00+01:00"}
        self.assertEqual(map_rows([row], PACK).rejected, {"bad_date": 1})


# =================================================================================================== the world

def _digest_in_subprocess(hashseed: str) -> str:
    code = ("from mycelic.collective.packs.loader import load_pack;"
            "from mycelic.collective.packs.generator import generate, world_digest;"
            f"print(world_digest(generate(load_pack({PACK_ID!r}), seed=7, sites=6, weeks=52)))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hashseed})
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def surface_kind(pack: FrozenPack, entity_type: str, entity_id: str, text: str) -> str:
    if pack.entity_types[entity_type].id_format is None or text in pack.aliases.get(entity_type, {}):
        return "alias"
    if any(0xFF01 <= ord(c) <= 0xFF5E for c in text):
        return "fullwidth"
    if "_" in text:
        return "underscore"
    if "\u2013" in text:
        return "en_dash"
    if " " in text:
        return "space"
    return "exact" if text == entity_id else "lower"


class GeneratorTests(unittest.TestCase):
    world = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = generate(PACK, seed=7, sites=6, weeks=52)

    def test_the_digest_is_stable_across_hash_seeds(self) -> None:
        self.assertEqual(_digest_in_subprocess("0"), _digest_in_subprocess("4242"))
        self.assertEqual(_digest_in_subprocess("0"), world_digest(self.world))

    def test_the_world_exercises_the_pack(self) -> None:
        world = self.world
        gold = [g for r in world.records for g in world.gold[r["record_ref"]]]
        self.assertEqual({g["predicate"] for g in gold} - {None}, set(PACK.predicates))
        seen_types = {g["entity_type"] for g in gold} | {t for r in world.records for t, v in r["entities"].items()
                                                         if v}
        self.assertEqual(seen_types, set(PACK.entity_types))
        self.assertEqual({r["language"] for r in world.records}, {"en", "de", None})
        self.assertTrue(any(r["origin_ref"] for r in world.records))
        self.assertTrue(any(g["negated"] for g in gold))
        self.assertTrue(any(not world.gold[r["record_ref"]] for r in world.records))
        kinds = {surface_kind(PACK, g["entity_type"], g["entity_id"], g["entity_text"]) for g in gold
                 if g["entity_text"] is not None}
        self.assertEqual(kinds, {k for k, w in PACK.generator["surface"].items() if w > 0})
        for field in PACK.mapping()["persons"]:
            self.assertTrue(any(r["persons"][field] for r in world.records), field)
        values = set(PACK.generator["reporter"]["values"])
        for site in PACK.generator["sites"]:
            reporters = {r["reporter"] for r in world.records if r["site"] == site["id"] and r["origin_ref"] is None}
            self.assertLessEqual(reporters, values)
            self.assertEqual(len(reporters), site["reporters"])
        universe = PACK.generator["universe"]
        partial = [(s, t) for s, per_type in world.master_data.items() for t, ids in per_type.items()
                   if set(ids) < set(universe[t])]
        self.assertTrue(partial)


# =================================================================================================== the pipeline

class PipelineTests(TmpCase):
    def test_a_small_g0_run_passes(self) -> None:
        out = self.tmp / "g0"
        code, stdout, stderr = run_main(["--pack", PACK_ID, "--records", "200", "--seed", "11", "--out", str(out)])
        self.assertEqual(code, 0, stdout + stderr)
        d = json.loads((out / "leakage.json").read_text(encoding="utf-8"))
        self.assertEqual((d["hits"], d["shingle_overlap_bytes"], d["known_limitation"], d["passed"]), ([], 0, [], True))
        self.assertGreater(d["positive_control"]["canary_hits"], 0)
        self.assertGreater(d["positive_control"]["shingle_overlap_bytes"], 0)

    def test_the_plant_smoke_checks_against_a_world_and_an_in_test_prereg(self) -> None:
        path = BUILTIN_ROOT / PACK_ID / "fixtures" / "plant_smoke.json"
        spec = P.load_plant(path, PACK)
        world = generate(PACK, 7, 6, 52)
        P.check_plant(spec, PACK, site_ids=list(world.params["site_ids"]), master_data=world.master_data,
                      n_weeks=52, eval_from=26, eval_to=51)
        self.assertEqual((len(spec.patterns), len(spec.decoys)), (3, 8))
        self.assertEqual(sorted(d.decoy_class for d in spec.decoys), sorted(P.DECOY_CLASSES))
        self.assertEqual((spec.planter_saw_detector_code, spec.prereg_sha256), (True, None))
        self.assertEqual(spec.raw["notes"], "construction smoke, not blind, never a result")
        runs = self.tmp / "runs"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = H.main(["prereg", "--pack", PACK_ID, "--seeds", "7", "--weeks", "52", "--eval-from", "26",
                           "--eval-to", "51", "--tie-salt", "x3-test", "--detector-author", "x3 test",
                           "--run-id", "pre", "--runs-dir", str(runs), "--allow-dirty"])
            self.assertEqual(code, 0, err.getvalue())
            code = H.main(["check-plant", "--prereg", str(runs / "x1" / "pre" / "prereg.json"), "--plant", str(path),
                           "--construct", "--runs-dir", str(runs)])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertIn("construction: ok", out.getvalue())


# =================================================================================================== evidence

def _forbidden() -> list[str]:
    return [str(ROOT), str(Path.home()), "/dev/shm"]


class EvidenceTests(unittest.TestCase):
    def test_g0_leakage(self) -> None:
        d = json.loads((X3 / "g0" / "leakage.json").read_text(encoding="utf-8"))
        self.assertEqual((d["pack"], d["base_config_hash"], d["config_hash"]),
                         (PACK_ID, PACK.config_hash, PACK.config_hash))
        self.assertEqual((d["records"], d["seed"]), (1000, 11))
        self.assertEqual((d["hits"], d["shingle_overlap_bytes"], d["known_limitation"], d["passed"]), ([], 0, [], True))
        self.assertGreater(d["positive_control"]["canary_hits"], 0)
        self.assertGreater(d["positive_control"]["shingle_overlap_bytes"], 0)

    def test_the_x1_smoke(self) -> None:
        prereg_bytes = (X3 / "x1_smoke" / "prereg.json").read_bytes()
        labels_bytes = (X3 / "x1_smoke" / "labels.json").read_bytes()
        prereg, card = strict_load(prereg_bytes), strict_load((X3 / "x1_smoke" / "scorecard.json").read_bytes())
        self.assertEqual({k: prereg["pack"][k] for k in PACK.hashes()}, B4_HASHES[PACK_ID])
        self.assertEqual((prereg["pack"]["id"], prereg["pack"]["same_author_as_code"]), (PACK_ID, True))
        self.assertEqual(prereg["code_hash"], H.eval_code_hash())
        self.assertEqual(prereg["code_hash"], CODE_HASHES["X1"])
        self.assertTrue(prereg["allow_dirty"])
        self.assertEqual(card["content_hash"], H.content_hash(card))
        self.assertEqual(H.scorecard_problems(card), [])
        self.assertEqual((card["stamps"]["allow_dirty"], card["code"]["code_dirty"]), (True, True))
        self.assertEqual(card["hashes"]["prereg_sha256"], hashlib.sha256(prereg_bytes).hexdigest())
        self.assertEqual(card["hashes"]["labels_sha256"], hashlib.sha256(labels_bytes).hexdigest())
        self.assertEqual(card["paths"], {"pack_ref": PACK_REL, "plant": f"{PACK_REL}/fixtures/plant_smoke.json",
                                         "prereg": "<runs-dir>/x1/x3-smoke-prereg/prereg.json",
                                         "run_dir": "<runs-dir>/x1/x3-smoke-run",
                                         "workdir": "<runs-dir>/x1/x3-smoke-run/work"})
        self.assertEqual((card["stamps"]["synthetic"], card["stamps"]["measurement"], card["stamps"]["blind"]),
                         (True, False, False))

    def test_the_code_hashes_are_the_pinned_ones(self) -> None:
        self.assertEqual({"X1": H.eval_code_hash(), "E1": e1_extract.e1_code_hash(), "E2": e2_pushdown.e2_code_hash(),
                          "openfda_replay": openfda_replay.replay_code_hash(), "X5": x5_inference.x5_code_hash()},
                         {**CODE_HASHES, "X5": X5_CODE_HASH_R4})
        self.assertNotEqual(X5_CODE_HASH_R4, CODE_HASHES["X5"])
        self.assertEqual(effort()["code_hashes"], CODE_HASHES)

    def test_no_absolute_path_forbidden_string_or_model_name_in_x3_or_the_pack(self) -> None:
        files = [p for p in X3.rglob("*") if p.is_file()]
        self.assertGreater(len(files), 10)
        pack_files = [p for p in (ROOT / PACK_REL).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        self.assertEqual(len(pack_files), len(effort()["files"]))
        for path in (*files, *pack_files):
            data = path.read_bytes()
            rel = path.relative_to(ROOT).as_posix()
            with self.subTest(file=rel):
                problems = runfiles.portability_problems(data, forbidden=_forbidden())
                # the x3 evidence outside the demo holds sha256 bindings; the demo files and the pack data hold none
                allowed = ["hex64"] if path in files and path.suffix in (".json", ".jsonl") \
                    and "demo" not in path.parts else []
                self.assertEqual([p for p in problems if p not in allowed], [])
                self.assertEqual(model_name_hits(data.decode("utf-8")), [])


# =================================================================================================== the demo

class DemoTests(TmpCase):
    def test_the_scenario_uses_schema_one_keys_only(self) -> None:
        raw = json.loads((X3 / "scenario.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(raw), sorted(TOP_KEYS))
        for item in raw["items"]:
            self.assertEqual(sorted(item), sorted(ITEM_KEYS))
        sc = load_scenario(X3 / "scenario.json")
        self.assertEqual((sc.pack.id, sc.weeks, sc.tie_salt), (PACK_ID, 40, "x3-it-incidents-demo"))
        self.assertIn("(fictional)", sc.company)
        self.assertEqual((sc.hero.key.entity_type, sc.hero.key.predicate), ("software_release", "crash_after_update"))
        self.assertEqual([f["type"] for f in sc.followups], ["evidence_packet", "problem_record_draft"])
        self.assertIn("constructed illustration", sc.illustration)
        self.assertIn("not evidence that codes miss such cases", sc.illustration)

    def test_the_committed_run_validates_and_a_fresh_record_reproduces_it(self) -> None:
        docs = demo.read_run(DEMO_DIR)
        self.assertEqual(demo.validate_run(docs), [])
        out = self.tmp / "x3-it-incidents-demo"
        r = subprocess.run([sys.executable, "demo/collective/collective_demo.py", "--record", str(out), "--scenario",
                            str(X3 / "scenario.json")], cwd=ROOT, capture_output=True, text=True, timeout=600,
                           env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(self.tmp)})
        failing = [c["id"] for c in docs["scorecard.json"]["checks"] if not c["ok"]]
        self.assertEqual(r.returncode, 1 if failing else 0, r.stdout + r.stderr)
        fresh = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
        self.assertEqual(fresh["content_hash"], docs["scorecard.json"]["content_hash"])
        self.assertEqual(sorted(failing), sorted(g["check_or_feature"] for g in effort()["logic_gaps"]))

    def test_the_number_lint_matches_the_recorded_expectation(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lint_numbers.main([str(DEMO_DIR)])
        violations = [line for line in out.getvalue().splitlines() if not line.startswith("lint: ok")]
        self.assertEqual({"exit_code": code, "violations": violations}, effort()["demo_lint"])

    def test_the_committed_run_files_are_portable(self) -> None:
        for name in runfiles.RUN_FILES:
            with self.subTest(file=name):
                self.assertEqual(runfiles.portability_problems((DEMO_DIR / name).read_bytes(),
                                                               forbidden=_forbidden()), [])


# =================================================================================================== effort.json

class EffortTests(unittest.TestCase):
    endpoint: str | None = None
    full_git = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.full_git = has_commit(BASE_COMMIT)
        cls.endpoint = effort_endpoint() if cls.full_git else None

    def test_closed_header_statement_and_labels(self) -> None:
        e = effort()
        self.assertEqual(sorted(e), sorted(EFFORT_KEYS))
        self.assertEqual((e["kind"], e["schema_version"], e["statement"]), ("x3_effort", 1, STATEMENT))
        self.assertEqual((e["base_commit"], e["template_pack"]), (BASE_COMMIT, TEMPLATE_ID))
        self.assertEqual(e["wall_clock"]["label"], WALL_CLOCK_LABEL)
        stamps = [e["wall_clock"][k] for k in ("start", "requirements_committed", "pack_start", "pack_loaded",
                                               "evidence_done", "end")]
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s) for s in stamps))
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(e["diff_method"], DIFF_METHOD)
        self.assertEqual(e["structural_diff_vs_template"], structural_diff())
        self.assertEqual(e["code_hashes"], CODE_HASHES)

    def test_requirements_statuses_and_gaps(self) -> None:
        e = effort()
        ids = requirement_ids((ROOT / REQUIREMENTS_REL).read_text(encoding="utf-8"))
        self.assertEqual(ids[:27], [f"R{i:02d}" for i in range(1, 28)])
        self.assertEqual([r["id"] for r in e["requirements"]], ids)
        self.assertEqual(e["requirements_added_after_b4a"], [])
        gaps = {g["id"]: g for g in e["gaps"]}
        self.assertEqual(len(gaps), len(e["gaps"]))
        for r in e["requirements"]:
            with self.subTest(requirement=r["id"]):
                self.assertEqual(sorted(r), ["gap_id", "how", "id", "status"])
                self.assertIn(r["status"], STATUSES)
                self.assertTrue(r["how"])
                if r["status"] == "not_expressible":
                    self.assertIsNotNone(r["gap_id"])
                if r["gap_id"] is not None:
                    self.assertEqual(gaps[r["gap_id"]]["requirement_id"], r["id"])
        for g in e["gaps"]:
            with self.subTest(gap=g["id"]):
                self.assertEqual(sorted(g), sorted(GAP_KEYS))
                self.assertIn(g["requirement_id"], ids)
                self.assertTrue((ROOT / g["file"]).is_file(), g["file"])
                self.assertEqual((g["blocking"], g["fixed"], g["lines_changed"]), (False, False, 0))
                self.assertTrue(g["symptom"] and g["why_data_cannot"] and g["minimal_generic_fix"])
        for d in e["fixture_disagreements"]:
            self.assertIn(d["gap_id"], gaps)
        self.assertEqual(e["presentation_gaps"] and sorted(e["presentation_gaps"][0]),
                         ["constant_or_function", "file", "id", "literal", "noun"])
        for g in e["presentation_gaps"]:
            self.assertIn(g["noun"].split(", ")[0], g["literal"].lower())
        for g in e["logic_gaps"]:
            self.assertEqual(sorted(g), ["check_or_feature", "id", "observed", "why"])
        for key in ("term_renames", "fit_to_code_choices"):
            self.assertIsInstance(e[key], list)
        for c in e["fit_to_code_choices"]:
            self.assertEqual(sorted(c), ["choice", "requirement_id", "why"])
            self.assertIn(c["requirement_id"], ids)

    def test_files_lines_bytes_and_template_diff(self) -> None:
        e = effort()
        self.assertEqual(e["totals"], {k: sum(f[k] for f in e["files"]) for k in ("lines", "bytes",
                                                                                  "lines_changed_vs_template")}
                         | {"files": len(e["files"])})
        if not self.full_git:
            self.assertEqual(git("rev-parse", "--is-shallow-repository").strip(), "true")
            return
        rows, totals = pack_file_rows(self.endpoint)
        self.assertEqual(e["files"], rows)
        self.assertEqual(e["totals"], totals)
        self.assertEqual(e["template_files_sha256"], template_files_sha256(self.endpoint))
        self.assertEqual(e["presentation_gaps"], presentation_gaps(self.endpoint))

    def test_the_requirements_commit_came_first(self) -> None:
        e = effort()
        data = (ROOT / REQUIREMENTS_REL).read_bytes()
        self.assertEqual(e["requirements_sha256"], hashlib.sha256(data).hexdigest())
        if not self.full_git:
            self.assertEqual(git("rev-parse", "--is-shallow-repository").strip(), "true")
            return
        b4a = e["requirements_commit"]
        self.assertEqual(git("log", "-1", "--format=%P", b4a).split(), [BASE_COMMIT])
        self.assertEqual(git("show", "--name-only", "--format=", b4a).split(), [REQUIREMENTS_REL])
        self.assertEqual(git("ls-tree", "-r", "--name-only", b4a, "--", PACK_REL).split(), [])
        self.assertEqual(hashlib.sha256(read_at(b4a, REQUIREMENTS_REL)).hexdigest(), e["requirements_sha256"])

    def test_zero_code_change_and_the_test_and_doc_line_counts(self) -> None:
        e = effort()
        self.assertEqual((e["code_files_changed"], e["code_lines_changed"], e["code_numstat"]), ([], 0, []))
        for key in ("test_lines_added", "docs_lines_added"):
            block = e[key]
            self.assertEqual((block["added"], block["deleted"]),
                             (sum(f["added"] for f in block["files"]), sum(f["deleted"] for f in block["files"])))
        if not self.full_git:
            self.assertEqual(git("rev-parse", "--is-shallow-repository").strip(), "true")
            return
        self.assertEqual(numstat(CODE_PATHSPEC, self.endpoint), [])
        self.assertEqual(e["test_lines_added"], numstat_summary(numstat(TEST_PATHSPEC, self.endpoint)))
        self.assertEqual(e["docs_lines_added"], numstat_summary(numstat(DOCS_PATHSPEC, self.endpoint)))

    def test_attempts_and_evidence_paths(self) -> None:
        e = effort()
        self.assertEqual(sorted(e["attempts"]), ["demo", "g0", "x1_smoke"])
        for name, attempts in e["attempts"].items():
            self.assertTrue(attempts, name)
            self.assertTrue(all(a["outcome"] for a in attempts))
        for rel in e["evidence"].values():
            self.assertTrue((ROOT / rel).exists(), rel)


class GapTests(TmpCase):
    def test_every_recorded_gap_symptom_is_reproduced(self) -> None:
        gaps = effort()["gaps"]
        self.assertEqual(sorted(g["id"] for g in gaps), sorted(GAP_PROBES))
        for g in gaps:
            with self.subTest(gap=g["id"]):
                self.assertEqual(GAP_PROBES[g["id"]](self.tmp / g["id"]), g["symptom"])


class PointerTests(unittest.TestCase):
    def rows(self, doc: str, heading: str) -> list[str]:
        cells = pointer_rows((ROOT / "docs" / "collective" / doc).read_text(encoding="utf-8"), heading)
        self.assertTrue(cells, heading)
        return cells

    def test_every_pointer_row_resolves(self) -> None:
        tables = {"PACKS.md": "### 4.1 As measured for it_incidents (B4)",
                  "INTEGRATION.md": "### B4 effort and evidence (pointers)"}
        found = {}
        for doc, heading in tables.items():
            for cell in self.rows(doc, heading):
                with self.subTest(doc=doc, cell=cell):
                    m = re.fullmatch(r"`(docs/collective/x3/[^`#]+)#([^`]*)`", cell)
                    self.assertIsNotNone(m, cell)
                    target = strict_load((ROOT / m.group(1)).read_bytes())
                    resolve_pointer(target, m.group(2))
                    found.setdefault(doc, set()).add(f"{m.group(1)}#{m.group(2)}")
        self.assertLessEqual(set(REQUIRED_POINTERS), found["INTEGRATION.md"])
        self.assertLessEqual({f"{X3_REL}/effort.json#/totals", f"{X3_REL}/effort.json#/code_lines_changed"},
                             found["PACKS.md"])

    def test_the_pointer_resolver(self) -> None:
        doc = {"a/b": {"m~n": [10, 20]}, "": 1}
        self.assertEqual(resolve_pointer(doc, "/a~1b/m~0n/1"), 20)
        self.assertEqual(resolve_pointer(doc, "/"), 1)
        self.assertIs(resolve_pointer(doc, ""), doc)
        for bad in ("a", "/missing", "/a~1b/m~0n/2"):
            with self.subTest(pointer=bad), self.assertRaises((KeyError, IndexError)):
                resolve_pointer(doc, bad)


if __name__ == "__main__":
    unittest.main()
