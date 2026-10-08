"""B2: the sealed, procedurally blind X1 run. The planter brief, the brief's own rules (``brief_problems``, also run by
the sandbox's check-plant wrapper through ``brief_main``), the pre-written results template and its helpers (the
condition evaluator, the row expander and the value formatter).

The planter and the detector author are the same AI system: the blinding is procedural only. Nothing here compares
against HEAD's code or pack hashes or needs git (suites also run on exports without ``.git``); later B2 commits add
the seeds, the preregs, the seal and the results, checked against each other and against fixed literals.

Import stays cheap (the sandbox wrapper imports this module to run ``brief_main``): heavy setup lives in classes.
"""
from __future__ import annotations

import codecs
import contextlib
import copy
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.evaluate.baselines import CHANNELS
from mycelic.collective.evaluate.plant import DECOY_CLASSES, VISIBILITIES
from mycelic.collective.runfiles import portability_problems
from tests.mycelic.test_collective_guards import POSITIVE_TAGS_ROT13, model_name_hits

ROOT = Path(__file__).resolve().parents[2]
X1_DIR = ROOT / "docs" / "collective" / "x1"
BRIEF = X1_DIR / "PLANTER_BRIEF.md"
TEMPLATE = X1_DIR / "RESULTS_TEMPLATE.md"

PLANTED_BY = "x1 planter agent (same AI system as the detector author; procedural blinding)"
DETECTOR_AUTHOR = "mycelic collective engineer agent"
PACKS = ("device_quality", "claims_integrity")
PACK_FILES = ("pack", "vocabulary", "aliases", "codes", "generator", "egress", "mapping")
PERMITTED_READS = ("BRIEF.md", "check-plant",
                   *(f"packs/{pack}/{name}.json" for pack in PACKS for name in PACK_FILES),
                   *(f"specs/{pack}.json" for pack in PACKS), "declaration.json")
EVAL_FROM, EVAL_TO, WEEKS, SITES = 26, 103, 104, 6
QUARTERS = ((26, 45), (46, 64), (65, 84), (85, 103))
LIFT_NAMES = ("X_minus_single_site", "X_minus_S", "X_minus_R_mf")
MIN_ITEMS, MIN_PER_VISIBILITY, MIN_PER_CLASS, MIN_PER_QUARTER, MIN_DECOY_QUARTERS = 20, 5, 2, 4, 3
LANGUAGE_RULES = {"device_quality": (("en", 5), ("de", 5))}
FORBIDDEN_BRIEF_WORDS = ("budget", "weight", "ranker", "cooldown", "pmi", "mutual information", "co-occurrence",
                         "cooccurrence", "burst", "base rate", "base_rate", "surprise", "binomial", "poisson", "alpha",
                         "lambda", "threshold", "min_sites", "few_reporters", "independen", "suppress", "window",
                         "baseline", "precision", "recall")
FORBIDDEN_CLAIMS = ("X1 passed", "passed X1", "X1 is met", "blind test passed")

# brief_problems' fixed messages: they name a rule, never a value from the spec
PLANTED_BY_PROBLEM = "planted_by is not the brief's text"
PREREG_PROBLEM = "prereg_sha256 is null"
PATTERNS_PROBLEM = f"fewer than {MIN_ITEMS} patterns"
DECOYS_PROBLEM = f"fewer than {MIN_ITEMS} decoys"
SPREAD_PROBLEM = (f"decoys other than stale_chain start in fewer than {MIN_DECOY_QUARTERS} of the {len(QUARTERS)} "
                  "quarters")
MODEL_PROBLEM = "notes or planted_by name a model"
PORTABILITY_PROBLEM = "notes or planted_by break the portability rules"


def visibility_problem(visibility: str) -> str:
    return f"fewer than {MIN_PER_VISIBILITY} patterns of visibility {visibility}"


def class_problem(cls: str) -> str:
    return f"fewer than {MIN_PER_CLASS} decoys of class {cls}"


def quarter_problem(lo: int, hi: int) -> str:
    return f"fewer than {MIN_PER_QUARTER} patterns start in weeks {lo} to {hi}"


def language_problem(language: str, least: int) -> str:
    return f"fewer than {least} patterns in language {language}"


def b2_portability_problems(data: bytes) -> list[str]:
    """B2's portability scan: the run-file rules with the repository, the home directory and ``/dev/shm`` forbidden,
    without ``hex64`` (preregs, specs, the seal and scorecards are full of legitimate hashes)."""
    found = portability_problems(data, forbidden=[str(ROOT), str(Path.home()), "/dev/shm"])
    return [name for name in found if name != "hex64"]


def _quarter(week: Any) -> int | None:
    return next((i for i, (lo, hi) in enumerate(QUARTERS) if isinstance(week, int) and lo <= week <= hi), None)


def brief_problems(raw_spec: Mapping[str, Any], pack_id: str) -> list[str]:
    """The brief's own rules (section 10 of PLANTER_BRIEF.md), the ones check-plant does not enforce, in a fixed
    order; each problem is a fixed message."""
    problems = []
    patterns = list(raw_spec.get("patterns") or [])
    decoys = list(raw_spec.get("decoys") or [])
    if raw_spec.get("planted_by") != PLANTED_BY:
        problems.append(PLANTED_BY_PROBLEM)
    if raw_spec.get("prereg_sha256") is None:
        problems.append(PREREG_PROBLEM)
    if len(patterns) < MIN_ITEMS:
        problems.append(PATTERNS_PROBLEM)
    for v in VISIBILITIES:
        if sum(1 for p in patterns if p.get("visibility") == v) < MIN_PER_VISIBILITY:
            problems.append(visibility_problem(v))
    if len(decoys) < MIN_ITEMS:
        problems.append(DECOYS_PROBLEM)
    for cls in DECOY_CLASSES:
        if sum(1 for d in decoys if d.get("class") == cls) < MIN_PER_CLASS:
            problems.append(class_problem(cls))
    for i, (lo, hi) in enumerate(QUARTERS):
        if sum(1 for p in patterns if _quarter(p.get("start_week")) == i) < MIN_PER_QUARTER:
            problems.append(quarter_problem(lo, hi))
    spread = {_quarter(d.get("start_week")) for d in decoys if d.get("class") != "stale_chain"} - {None}
    if len(spread) < MIN_DECOY_QUARTERS:
        problems.append(SPREAD_PROBLEM)
    for language, least in LANGUAGE_RULES.get(pack_id, ()):
        if sum(1 for p in patterns if p.get("language") == language) < least:
            problems.append(language_problem(language, least))
    texts = [t for t in (raw_spec.get("notes"), raw_spec.get("planted_by")) if isinstance(t, str)]
    if any(model_name_hits(t) for t in texts):
        problems.append(MODEL_PROBLEM)
    if any(b2_portability_problems(t.encode("utf-8")) for t in texts):
        problems.append(PORTABILITY_PROBLEM)
    return problems


def brief_main(argv: Sequence[str]) -> int:
    """``brief_main([pack, spec_path])``: prints ``brief: ok`` and returns 0, or one ``brief: <problem>`` line per
    problem and returns 2. The sandbox's check-plant wrapper runs it after the harness's check-plant succeeds."""
    if len(argv) != 2 or argv[0] not in PACKS:
        print("brief: usage: brief_main <pack> <spec>")
        return 2
    try:
        raw = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("brief: cannot read the spec")
        return 2
    if not isinstance(raw, dict):
        print("brief: the spec is not an object")
        return 2
    problems = brief_problems(raw, argv[0])
    for problem in problems or ["ok"]:
        print(f"brief: {problem}")
    return 2 if problems else 0


# --------------------------------------------------------------------------------------------------- the template

CATALOG_KEYS = ("always", "format", "governing_interval", "interpretations", "kind", "multiple_comparisons", "rows",
                "schema_version")
OPS = ("==", "!=", "<", "<=", ">", ">=")
PLACEHOLDERS = {"channel": CHANNELS, "visibility": VISIBILITIES, "class": DECOY_CLASSES, "lift": LIFT_NAMES,
                "pack": PACKS}
_PLACEHOLDER = re.compile(r"\{([a-z]+)\}")
_JSON_BLOCK = re.compile(r"^```json\n(.*?)^```$", re.M | re.S)
_OK, _NULL_PATH, _MISSING = "ok", "null_path", "missing"


def load_catalog(path: Path = TEMPLATE) -> dict[str, Any]:
    """The first ```json fenced block of the results template."""
    m = _JSON_BLOCK.search(path.read_text(encoding="utf-8"))
    if m is None:
        raise ValueError("no json block")
    return json.loads(m.group(1))


def _tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"not a pointer: {pointer}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def lookup(doc: Any, pointer: str) -> tuple[str, Any]:
    """``(status, value)``: ``ok`` with the value (which may be null), ``null_path`` when the pointer passes through
    null before its last token, ``missing`` when a key or index is absent."""
    node = doc
    for token in _tokens(pointer):
        if node is None:
            return _NULL_PATH, None
        if isinstance(node, dict):
            if token not in node:
                return _MISSING, None
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                return _MISSING, None
            node = node[int(token)]
        else:
            return _MISSING, None
    return _OK, node


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def condition_holds(doc: Any, condition: Sequence[Any]) -> bool:
    """``[pointer, op, operand]``; the operand is a literal or ``{"ptr": pointer}``. A pointer that passes through
    null or names a missing key makes the condition false, and so does an ordering op on null (or a bool)."""
    pointer, op, operand = condition
    status, left = lookup(doc, pointer)
    if status != _OK:
        return False
    if isinstance(operand, dict):
        status, right = lookup(doc, operand["ptr"])
        if status != _OK:
            return False
    else:
        right = operand
    if op in ("==", "!="):
        return _equal(left, right) == (op == "==")
    if not _number(left) or not _number(right):
        return False
    return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]


def conditions_hold(doc: Any, conditions: Sequence[Sequence[Any]]) -> bool:
    return all(condition_holds(doc, c) for c in conditions)


def select_interpretations(catalog: Mapping[str, Any], doc: Any, pack: str) -> list[str]:
    """The catalog sentences whose conditions all hold on ``doc``, in catalog order, with ``{pack}`` filled in."""
    return [i["text"].replace("{pack}", pack) for i in catalog["interpretations"] if conditions_hold(doc, i["when"])]


def expand(template: str, doc: Any) -> list[str]:
    """Every pointer of a row template: placeholders expand left to right; ``{i}`` takes each index of the array
    reached before it, and nothing when that value is null (or the path to it passes through null)."""
    m = _PLACEHOLDER.search(template)
    if m is None:
        return [template]
    name, head, tail = m.group(1), template[:m.start()], template[m.end():]
    if name != "i":
        return [p for value in PLACEHOLDERS[name] for p in expand(head + value + tail, doc)]
    status, array = lookup(doc, head.rstrip("/"))
    if status == _MISSING:
        raise KeyError(f"{head.rstrip('/')} is missing")
    if array is None:
        return []
    if not isinstance(array, list):
        raise TypeError(f"{head.rstrip('/')} is not an array")
    return [p for i in range(len(array)) for p in expand(f"{head}{i}{tail}", doc)]


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value.replace("|", "\\|")
    raise TypeError(f"not a scalar: {type(value).__name__}")


def row_value(doc: Any, pointer: str) -> Any:
    """A row's value: null when the pointer passes through null; a missing key is an error."""
    status, value = lookup(doc, pointer)
    if status == _MISSING:
        raise KeyError(f"{pointer} is missing")
    return value


def render_rows(catalog: Mapping[str, Any], group: Mapping[str, Any], doc: Any, pack: str | None = None) -> list[str]:
    """One group's rows: each pointer template expanded on ``doc``, as ``| pointer | value | source |``."""
    fmt = catalog["format"]
    source = fmt["sources"][group["source"]]
    lines = []
    for template in group["pointers"]:
        for pointer in expand(template, doc):
            src = source.replace("{pointer}", pointer).replace("{pack}", pack or "")
            lines.append(fmt["row"].replace("{pointer}", pointer)
                         .replace("{value}", format_value(row_value(doc, pointer))).replace("{source}", src))
    return lines


def _section(text: str, heading: str) -> str:
    """The text of a ``## `` section, up to the next ``## `` heading."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m is None:
        raise ValueError(f"no section {heading}")
    return m.group(1)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


# --------------------------------------------------------------------------------------------------- the brief

class PlanterBriefTests(unittest.TestCase):
    text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BRIEF.read_text(encoding="utf-8")

    def test_no_forbidden_word_once_the_class_identifiers_are_removed(self) -> None:
        text = self.text
        for name in (*DECOY_CLASSES, "planter_saw_detector_code"):
            text = text.replace(name, " ")
        low = text.casefold()
        self.assertEqual([w for w in FORBIDDEN_BRIEF_WORDS if w in low], [])
        # the removal is what makes the identifiers legal; without it the class names would trip the list
        self.assertTrue([w for w in FORBIDDEN_BRIEF_WORDS if w in self.text.casefold()])

    def test_every_class_and_planted_by_verbatim(self) -> None:
        for cls in DECOY_CLASSES:
            self.assertIn(f"`{cls}`", self.text)
        self.assertIn(f"`{PLANTED_BY}`", self.text)
        self.assertLessEqual(len(PLANTED_BY), 80)
        self.assertNotEqual(_norm(PLANTED_BY), _norm(DETECTOR_AUTHOR))
        self.assertIn("planter_saw_detector_code", self.text)

    def test_the_permitted_reads_are_exactly_the_list_and_exclude_the_preregs(self) -> None:
        section = _section(self.text, "2. What you may read")
        block = re.search(r"^```text\n(.*?)^```$", section, re.M | re.S)
        self.assertIsNotNone(block)
        self.assertEqual(tuple(block.group(1).split()), PERMITTED_READS)
        self.assertEqual(len(PERMITTED_READS), 2 + len(PACKS) * len(PACK_FILES) + len(PACKS) + 1)
        self.assertIn("packs/device_quality/mapping.json", PERMITTED_READS)
        self.assertFalse([p for p in PERMITTED_READS if "prereg" in p or p.startswith("docs/")])
        for name in ("detectors.json", "rules.json", "questions.json", "followups.json"):
            self.assertIn(f"`{name}`", _section(self.text, "3. What you must not read"))

    def test_the_stated_world_and_class_settings_follow_the_packs(self) -> None:
        from mycelic.collective.packs.loader import load_pack
        packs = {pid: load_pack(pid) for pid in PACKS}
        bands = []
        for pid in PACKS:
            pack = packs[pid]
            d, e = pack.detectors, pack.egress
            lo = EVAL_FROM - d["window_weeks"] + 1
            hi = max(w for w in range(EVAL_FROM) if 7 * (EVAL_FROM - w) + e.close_lag_days > d["decoy"]["stale_days"])
            bands.append(f"{lo} to {hi} ({pid})")
            self.assertEqual(len(pack.generator["sites"]), SITES)
        text = " ".join(self.text.split())
        self.assertIn(f"`stale_chain` lies wholly within weeks {bands[0]} or {bands[1]}, at 2 or more sites.", text)
        ks = " and ".join(str(packs[pid].egress.k) for pid in PACKS)
        self.assertIn(f"rate_per_week of at least egress.json k ({ks}).", text)
        fractions = {packs[pid].detectors["decoy"]["base_rate_site_fraction"] for pid in PACKS}
        self.assertEqual(len(fractions), 1)
        fraction = fractions.pop()
        least = min(n for n in range(1, SITES + 1) if n > fraction * SITES)
        self.assertIn(f"at least {least} of the {SITES} sites.", text)
        self.assertIn(f"The world has {WEEKS} weeks.", text)
        self.assertIn(f"The evaluation weeks are {EVAL_FROM} to {EVAL_TO}.", text)
        quarters = [f"{lo} to {hi}" for lo, hi in QUARTERS]
        self.assertIn(f"evaluation weeks: {', '.join(quarters[:-1])} and {quarters[-1]};", text)
        self.assertEqual(packs["device_quality"].languages, ("en", "de"))
        self.assertEqual(packs["claims_integrity"].languages, ("en",))

    def test_the_stated_stale_bands_are_the_plant_rule(self) -> None:
        # the band each pack's check_plant accepts at eval_from 26 in a 104-week world, edges included
        from mycelic.collective.evaluate.plant import PlantError, check_plant, parse_plant
        from mycelic.collective.packs.generator import generate
        from mycelic.collective.packs.loader import BUILTIN_ROOT, load_pack
        for pid, (lo, hi) in (("device_quality", (19, 21)), ("claims_integrity", (19, 20))):
            pack = load_pack(pid)
            world = generate(pack, 1, SITES, WEEKS)
            raw = json.loads((BUILTIN_ROOT / pid / "fixtures" / "plant_smoke.json").read_text(encoding="utf-8"))
            stale = next(d for d in raw["decoys"] if d["class"] == "stale_chain")

            def check(start: int, weeks: int) -> str | None:
                stale.update({"start_week": start, "weeks": weeks})
                try:
                    check_plant(parse_plant(raw, pack), pack, site_ids=list(world.params["site_ids"]),
                                master_data=world.master_data, n_weeks=WEEKS, eval_from=EVAL_FROM, eval_to=EVAL_TO)
                except PlantError as err:
                    return err.problem
                return None

            with self.subTest(pack=pid):
                self.assertIn(f"{lo} to {hi} ({pid})", self.text)
                self.assertIsNone(check(lo, hi - lo + 1))
                self.assertEqual(check(lo - 1, hi - lo + 2), "not inside the first evaluation week's window")
                self.assertEqual(check(lo, hi - lo + 2), "not stale at the first evaluation week")

    def test_no_absolute_path_and_no_model_name(self) -> None:
        self.assertEqual(b2_portability_problems(BRIEF.read_bytes()), [])
        self.assertEqual(model_name_hits(self.text), [])
        self.assertIn("Never write the name of any AI model, vendor or product, or any absolute path", self.text)


# --------------------------------------------------------------------------------------------------- brief rules

def passing_spec(pack_id: str) -> dict[str, Any]:
    """A spec that meets every brief rule with the least slack the rule tests need: 20 patterns (visibilities 8, 7
    and 5; five starts in each quarter; for device_quality ten in each language) and 20 decoys (two of each class,
    three of four classes), the non-stale decoys spread over every quarter. Ids and predicates come from the pack;
    this is a brief-rule fixture, not a spec check-plant would accept."""
    from mycelic.collective.packs.loader import load_pack
    pack = load_pack(pack_id)
    entity_type = next(t for t in pack.egress.egress_entity_types if pack.entity_types[t].id_format is None)
    ids = list(pack.entity_types[entity_type].ids)
    predicates = list(pack.predicates)
    sites = [s["id"] for s in pack.generator["sites"]]
    languages = list(pack.languages)
    visibilities = ["narrative_only"] * 8 + ["codes_only"] * 7 + ["both"] * 5
    patterns = []
    for i in range(20):
        lo, _ = QUARTERS[i % 4]
        patterns.append({"id": f"p{i:02d}", "entity_type": entity_type, "entity_id": ids[i % len(ids)],
                         "predicate": predicates[i % len(predicates)], "sites": sites[:2], "start_week": lo + i // 4,
                         "weeks": 2, "rate_per_week": 1, "visibility": visibilities[i],
                         "language": languages[(i // 2) % len(languages)]})
    decoys = []
    classes = list(DECOY_CLASSES) * 2 + [c for c in DECOY_CLASSES if c != "stale_chain"][:4]
    for i, cls in enumerate(classes):
        start = 19 if cls == "stale_chain" else QUARTERS[i % 4][0] + 1
        decoys.append({"id": f"d{i:02d}", "class": cls, "entity_type": entity_type, "entity_id": ids[i % len(ids)],
                       "predicate": predicates[i % len(predicates)], "sites": sites[:1], "start_week": start,
                       "weeks": 2, "rate_per_week": 1, "language": languages[0]})
    return {"kind": "plant_spec", "schema_version": 1, "pack": pack_id, "prereg_sha256": "a" * 64,
            "planted_by": PLANTED_BY, "planter_saw_detector_code": False, "notes": "Spread over the year.",
            "patterns": patterns, "decoys": decoys}


class BriefRuleTests(unittest.TestCase):
    def test_the_smoke_specs_break_the_count_visibility_class_quarter_and_planted_by_rules(self) -> None:
        from mycelic.collective.packs.loader import BUILTIN_ROOT
        raw = json.loads((BUILTIN_ROOT / "device_quality" / "fixtures" / "plant_smoke.json").read_text(
            encoding="utf-8"))
        problems = brief_problems(raw, "device_quality")
        for expected in (PLANTED_BY_PROBLEM, PREREG_PROBLEM, PATTERNS_PROBLEM, visibility_problem("codes_only"),
                         visibility_problem("both"), DECOYS_PROBLEM, class_problem("echo_marked"),
                         quarter_problem(85, 103), SPREAD_PROBLEM, language_problem("de", 5)):
            self.assertIn(expected, problems)
        self.assertNotIn(MODEL_PROBLEM, problems)
        raw = json.loads((BUILTIN_ROOT / "claims_integrity" / "fixtures" / "plant_smoke.json").read_text(
            encoding="utf-8"))
        problems = brief_problems(raw, "claims_integrity")
        self.assertIn(PATTERNS_PROBLEM, problems)
        self.assertFalse([p for p in problems if "language" in p])

    def test_a_passing_spec_built_from_pack_data_has_no_problem(self) -> None:
        for pid in PACKS:
            with self.subTest(pack=pid):
                spec = passing_spec(pid)
                self.assertEqual(brief_problems(spec, pid), [])
                self.assertEqual(len(spec["patterns"]), MIN_ITEMS)
                self.assertEqual(len(spec["decoys"]), MIN_ITEMS)

    def test_each_rule_changed_alone_trips_exactly_its_message(self) -> None:
        model = codecs.decode(POSITIVE_TAGS_ROT13[0], "rot13")

        def drop(key: str, index: int) -> Any:
            return lambda s: s[key].pop(index)

        def move(indices: Sequence[int], week: int) -> Any:
            def apply(s: dict[str, Any]) -> None:
                for i in indices:
                    s["patterns"][i]["start_week"] = week
            return apply

        def setter(**changes: Any) -> Any:
            return lambda s: s.update(changes)

        def decoys_into(weeks: Sequence[int]) -> Any:
            def apply(s: dict[str, Any]) -> None:
                movable = [d for d in s["decoys"] if d["class"] != "stale_chain"]
                for j, d in enumerate(movable):
                    d["start_week"] = weeks[j % len(weeks)]
            return apply

        def relabel(key: str, index: int, field: str, value: Any) -> Any:
            return lambda s: s[key][index].__setitem__(field, value)

        de_patterns = [i for i, p in enumerate(passing_spec("device_quality")["patterns"]) if p["language"] == "de"]
        both = [i for i, p in enumerate(passing_spec("device_quality")["patterns"]) if p["visibility"] == "both"]
        two_of = next(i for i, d in enumerate(passing_spec("device_quality")["decoys"]) if d["class"] == "stale_chain")
        cases = (
            ("planted_by", setter(planted_by="x1 planter"), PLANTED_BY_PROBLEM),
            ("prereg", setter(prereg_sha256=None), PREREG_PROBLEM),
            ("patterns", drop("patterns", 0), PATTERNS_PROBLEM),
            ("visibility", relabel("patterns", both[0], "visibility", "codes_only"), visibility_problem("both")),
            ("decoys", drop("decoys", len(passing_spec("device_quality")["decoys"]) - 1), DECOYS_PROBLEM),
            ("class", relabel("decoys", two_of, "class", "echo_marked"), class_problem("stale_chain")),
            ("quarter", move([3, 7], 30), quarter_problem(85, 103)),
            ("spread", decoys_into([27, 50]), SPREAD_PROBLEM),
            ("language", lambda s: [s["patterns"][i].__setitem__("language", "en") for i in de_patterns[:6]],
             language_problem("de", 5)),
            ("model name", setter(notes=f"chosen with {model}"), MODEL_PROBLEM),
            ("absolute path", setter(notes="see /home/planter/notes.txt"), PORTABILITY_PROBLEM),
            ("machine path", setter(notes="kept under /dev/shm/b2 while planting"), PORTABILITY_PROBLEM),
        )
        for name, mutate, message in cases:
            with self.subTest(rule=name):
                spec = passing_spec("device_quality")
                mutate(spec)
                self.assertEqual(brief_problems(spec, "device_quality"), [message])
        # the language rule is device_quality's only
        spec = passing_spec("claims_integrity")
        self.assertEqual(brief_problems(spec, "claims_integrity"), [])
        self.assertEqual(LANGUAGE_RULES, {"device_quality": (("en", 5), ("de", 5))})

    def test_brief_main_prints_ok_or_the_problem_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.json", Path(tmp) / "bad.json"
            good.write_text(json.dumps(passing_spec("device_quality")), encoding="utf-8")
            raw = passing_spec("device_quality")
            raw.update({"prereg_sha256": None, "planted_by": "someone"})
            bad.write_text(json.dumps(raw), encoding="utf-8")
            for argv, code, lines in (
                    (["device_quality", str(good)], 0, ["brief: ok"]),
                    (["device_quality", str(bad)], 2, [f"brief: {PLANTED_BY_PROBLEM}", f"brief: {PREREG_PROBLEM}"]),
                    (["device_quality", str(Path(tmp) / "absent.json")], 2, ["brief: cannot read the spec"]),
                    (["other_pack", str(good)], 2, ["brief: usage: brief_main <pack> <spec>"]),
                    (["device_quality"], 2, ["brief: usage: brief_main <pack> <spec>"])):
                with self.subTest(argv=argv[0:1] + [Path(a).name for a in argv[1:]]):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        self.assertEqual(brief_main(argv), code)
                    self.assertEqual(out.getvalue().splitlines(), lines)


# --------------------------------------------------------------------------------------------------- the template

ALWAYS = [
    "Planter and detector author are the same AI system: the blinding is procedural only, not independent.",
    "X1 as STRATEGY 11.2 defines it is not met: the planter is the same AI system.",
    "Planted text is generated by the harness from the detector author's own pack templates; extraction was not "
    "tested blind; decoys could only be the classes the detector was designed to reject.",
    "The scorecard's blind stamp is self-declared (blind_basis); true means procedural blinding only.",
    "Synthetic, same-author results: internal only; never shown to buyers.",
    "The narrative_only lifts over S and R_mf are by construction.",
]
GOVERNING_INTERVAL = ("Claim words follow verdict.pass, STRATEGY 11.2's bar on the unadjusted 95% interval; the 97.5% "
                      "interval adjusted for the two primary tests is always shown beside it.")
MULTIPLE_COMPARISONS = (
    "There are two primary tests, one per pack: the collective lift X minus single_site (net of chance finds), with "
    "X's precision@40 as the verdict's second criterion; family_size is 2, so the adjusted intervals use alpha 0.025. "
    "Every other number is exploratory and unadjusted. The two packs share one author, one detector design and one "
    "harness, so they are not independent replications.")
INTERPRETATION_IDS = ("not_eligible", "verdict_pass", "verdict_fail", "lift_not_above_0", "lift_below_0",
                      "precision_below", "adjusted_disagrees", "codes_only_S_beats_X", "codes_only_R_mf_beats_X",
                      "single_site_at_least_X")
ROW_SECTIONS = ("Protocol", "Primary endpoint", "Channels", "Strata", "Decoys", "Lifts", "By construction and warnings",
                "Stamps and provenance", "Cost", "Lab handoff")
SEAL_SECTIONS = ("Protocol", "Lab handoff")


class TemplateTests(unittest.TestCase):
    catalog: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog()

    def test_the_catalog_has_its_closed_shape_and_the_pinned_texts(self) -> None:
        c = self.catalog
        self.assertEqual(sorted(c), sorted(CATALOG_KEYS))
        self.assertEqual((c["kind"], c["schema_version"]), ("x1_results_template", 1))
        self.assertEqual(c["always"], ALWAYS)
        self.assertEqual(c["governing_interval"], GOVERNING_INTERVAL)
        self.assertEqual(c["multiple_comparisons"], MULTIPLE_COMPARISONS)
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(b2_portability_problems(TEMPLATE.read_bytes()), [])
        self.assertEqual(model_name_hits(text), [])
        for heading in ("## Fixed statements", "## Protocol", "### Primary endpoint", "### Interpretation: <pack>",
                        "### Channels", "### Strata", "### Decoys", "### Lifts", "### Stamps and provenance",
                        "## Multiple comparisons", "## Lab handoff", "## Post-hoc notes (written after the results)"):
            self.assertIn(heading, text)
        self.assertIn("meets STRATEGY 11.2's bar on a synthetic, same-author plant", text)

    def test_the_interpretations_are_unique_in_order_and_well_formed(self) -> None:
        interpretations = self.catalog["interpretations"]
        self.assertEqual(tuple(i["id"] for i in interpretations), INTERPRETATION_IDS)
        for i in interpretations:
            with self.subTest(id=i["id"]):
                self.assertEqual(sorted(i), ["id", "text", "when"])
                self.assertTrue(i["text"].startswith("{pack}: "))
                self.assertTrue(i["when"])
                for pointer, op, operand in i["when"]:
                    self.assertTrue(pointer.startswith("/"))
                    self.assertIn(op, OPS)
                    if isinstance(operand, dict):
                        self.assertEqual(sorted(operand), ["ptr"])
                        self.assertTrue(operand["ptr"].startswith("/"))
                    else:
                        self.assertIsInstance(operand, (bool, int, float, type(None)))
                for claim in FORBIDDEN_CLAIMS:
                    self.assertNotIn(claim, i["text"])

    def test_the_row_groups_and_the_format(self) -> None:
        rows = self.catalog["rows"]
        self.assertEqual(tuple(g["section"] for g in rows), ROW_SECTIONS)
        for g in rows:
            self.assertEqual(sorted(g), ["pointers", "section", "source"])
            self.assertEqual(g["source"], "seal" if g["section"] in SEAL_SECTIONS else "scorecard")
            for pointer in g["pointers"]:
                self.assertTrue(pointer.startswith("/"))
                for name in _PLACEHOLDER.findall(pointer):
                    self.assertIn(name, (*PLACEHOLDERS, "i"))
        seal = [p for g in rows if g["source"] == "seal" for p in g["pointers"]]
        self.assertEqual(len(seal), len(set(seal)))   # every SEAL row appears once
        fmt = self.catalog["format"]
        self.assertEqual(fmt["row"], "| {pointer} | {value} | {source} |")
        self.assertEqual(fmt["sources"], {"scorecard": "results/{pack}/scorecard.json#{pointer}",
                                          "seal": "SEAL.json#{pointer}"})

    def test_the_condition_evaluator(self) -> None:
        doc = {"a": {"b": 1, "t": True, "n": None, "f": 0.5}, "x": None, "list": [3, 4]}
        cases = (
            (["/a/b", "==", 1], True), (["/a/b", "!=", 1], False), (["/a/b", ">", 0], True),
            (["/a/b", "<=", {"ptr": "/a/f"}], False), (["/a/f", "<", {"ptr": "/a/b"}], True),
            (["/a/t", "==", True], True), (["/a/t", "==", 1], False), (["/a/b", "==", True], False),
            (["/a/t", "!=", False], True), (["/a/t", ">", 0], False),
            (["/a/n", "==", None], True), (["/a/n", ">", 0], False), (["/a/n", "<", 1], False),
            (["/a/n", ">=", {"ptr": "/a/n"}], False),
            (["/x/y", "==", None], False), (["/x/y", "!=", 1], False),            # passes through null
            (["/a/zz", "!=", 1], False), (["/a/b", "==", {"ptr": "/a/zz"}], False),  # a missing key
            (["/a/b", "<", {"ptr": "/x/y"}], False),
            (["/list/1", "==", 4], True), (["/list/2", "==", 4], False), (["/list/a", "==", 4], False),
        )
        for condition, expected in cases:
            with self.subTest(condition=condition):
                self.assertEqual(condition_holds(doc, condition), expected)
        self.assertTrue(conditions_hold(doc, [["/a/b", "==", 1], ["/a/t", "==", True]]))
        self.assertFalse(conditions_hold(doc, [["/a/b", "==", 1], ["/a/t", "==", False]]))
        card = {"x1": {"eligible": False, "verdict": None}, "lifts": {"X_minus_single_site": {"ci_high": -0.1}},
                "channels": {"S": {"by_visibility": {"codes_only": {"recall_net": 0.5}}},
                             "R_mf": {"by_visibility": {"codes_only": {"recall_net": None}}},
                             "X": {"by_visibility": {"codes_only": {"recall_net": 0.25}}, "recall_net": 0.3},
                             "single_site": {"recall_net": 0.3}}}
        self.assertEqual(select_interpretations(self.catalog, card, "device_quality"), [
            "device_quality: not eligible for an X1 verdict (x1.reasons lists why); no pass or fail is stated.",
            "device_quality: the whole interval lies below 0: net of chance finds, single_site found more of the "
            "planted patterns than X.",
            "device_quality: on codes_only patterns, S's net recall is higher than X's.",
            "device_quality: single_site's pooled net recall is at least X's."])
        card["x1"] = {"eligible": True, "verdict": {"pass": False, "lift_ci_low_above_0": True,
                                                    "precision_at_40_at_least_0_25": False}}
        card["lifts"]["X_minus_single_site"] = {"ci_high": 0.4, "ci_low_adjusted": -0.01}
        self.assertEqual([t.split(": ", 1)[1][:30] for t in select_interpretations(self.catalog, card, "p")],
                         ["verdict.pass is false: this ru", "X's precision@40 is below 0.25",
                          "the unadjusted 95% interval li", "on codes_only patterns, S's ne",
                          "single_site's pooled net recal"])

    def test_the_row_expander_and_renderer(self) -> None:
        doc = {"x1": {"reasons": ["a|b", "c"], "caveats": []}, "warnings": None, "verdict": None,
               "by_rate_per_week": [{"channels": {c: {"recall_net": 0.5} for c in CHANNELS}},
                                    {"channels": {c: {"recall_net": None} for c in CHANNELS}}]}
        self.assertEqual(expand("/x1/reasons/{i}", doc), ["/x1/reasons/0", "/x1/reasons/1"])
        self.assertEqual(expand("/x1/caveats/{i}", doc), [])
        self.assertEqual(expand("/warnings/{i}", doc), [])                      # null: nothing
        self.assertEqual(expand("/verdict/list/{i}", doc), [])                  # through null: nothing
        with self.assertRaises(KeyError):
            expand("/absent/{i}", doc)
        self.assertEqual(expand("/channels/{channel}/x", doc), [f"/channels/{c}/x" for c in CHANNELS])
        self.assertEqual(expand("/a/{visibility}", doc), [f"/a/{v}" for v in VISIBILITIES])
        self.assertEqual(expand("/a/{class}", doc), [f"/a/{c}" for c in DECOY_CLASSES])
        self.assertEqual(expand("/lifts/{lift}/estimate", doc), [f"/lifts/{n}/estimate" for n in LIFT_NAMES])
        self.assertEqual(expand("/planter/{pack}", doc), ["/planter/device_quality", "/planter/claims_integrity"])
        nested = expand("/by_rate_per_week/{i}/channels/{channel}/recall_net", doc)
        self.assertEqual(nested[:2], ["/by_rate_per_week/0/channels/X/recall_net",
                                      "/by_rate_per_week/0/channels/S/recall_net"])
        self.assertEqual(len(nested), 2 * len(CHANNELS))
        group = {"section": "t", "source": "scorecard", "pointers": ["/x1/reasons/{i}", "/verdict/pass",
                                                                     "/by_rate_per_week/{i}/channels/X/recall_net"]}
        self.assertEqual(render_rows(self.catalog, group, doc, "device_quality"), [
            "| /x1/reasons/0 | a\\|b | results/device_quality/scorecard.json#/x1/reasons/0 |",
            "| /x1/reasons/1 | c | results/device_quality/scorecard.json#/x1/reasons/1 |",
            "| /verdict/pass | null | results/device_quality/scorecard.json#/verdict/pass |",
            "| /by_rate_per_week/0/channels/X/recall_net | 0.500 | "
            "results/device_quality/scorecard.json#/by_rate_per_week/0/channels/X/recall_net |",
            "| /by_rate_per_week/1/channels/X/recall_net | null | "
            "results/device_quality/scorecard.json#/by_rate_per_week/1/channels/X/recall_net |"])
        with self.assertRaises(KeyError):
            render_rows(self.catalog, {"source": "seal", "pointers": ["/x1/absent"]}, doc)

    def test_the_value_formatter(self) -> None:
        for value, text in ((True, "true"), (False, "false"), (0, "0"), (-12, "-12"), (0.5, "0.500"),
                            (1 / 3, "0.333"), (2.0, "2.000"), (None, "null"), ("a|b|c", "a\\|b\\|c"), ("", "")):
            with self.subTest(value=value):
                self.assertEqual(format_value(value), text)
        for value in ([1], {"a": 1}):
            with self.assertRaises(TypeError):
                format_value(value)


class TemplateOnAScorecardTests(unittest.TestCase):
    """Every scorecard row template and interpretation pointer resolves on a real scorecard (a one-seed smoke run
    with the B2 prereg flags; not blind, not sealed, never a result)."""

    card: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        from mycelic.collective.evaluate import harness as H
        from mycelic.collective.packs.loader import BUILTIN_ROOT
        tmp = Path(tempfile.mkdtemp())
        try:
            runs = tmp / "runs"
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                assert H.main(["prereg", "--pack", "device_quality", "--seeds", "11", "--eval-from", "26",
                               "--eval-to", "51", "--tie-salt", "x1-template", "--detector-author", DETECTOR_AUTHOR,
                               "--family-size", "2", "--planter-relation", "same_system_procedural", "--run-id",
                               "pre", "--runs-dir", str(runs), "--allow-dirty"]) == 0, out.getvalue()
                assert H.main(["run", "--prereg", str(runs / "x1" / "pre" / "prereg.json"), "--plant",
                               str(BUILTIN_ROOT / "device_quality" / "fixtures" / "plant_smoke.json"), "--seeds",
                               "11", "--run-id", "card", "--runs-dir", str(runs), "--allow-dirty"]) == 0, \
                    out.getvalue()
            cls.card = json.loads((runs / "x1" / "card" / "scorecard.json").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp)
        cls.catalog = load_catalog()

    def test_every_scorecard_row_resolves(self) -> None:
        for group in self.catalog["rows"]:
            if group["source"] != "scorecard":
                continue
            with self.subTest(section=group["section"]):
                lines = render_rows(self.catalog, group, self.card, "device_quality")
                self.assertTrue(lines)
                for line in lines:
                    self.assertTrue(line.startswith("| /") and line.endswith(" |"), line)
        # the verdict is null on this smoke (not blind), so its rows read null through the null path
        self.assertIsNone(self.card["x1"]["verdict"])
        self.assertIsNone(row_value(self.card, "/x1/verdict/pass"))

    def test_every_interpretation_pointer_exists_in_the_scorecard_shape(self) -> None:
        eligible = copy.deepcopy(self.card)
        eligible["x1"]["verdict"] = {"lift_ci_low_above_0": True, "precision_at_40_at_least_0_25": True,
                                     "pass": True}
        for i in self.catalog["interpretations"]:
            for pointer, _, operand in i["when"]:
                for p in (pointer, operand["ptr"] if isinstance(operand, dict) else None):
                    if p is not None:
                        with self.subTest(pointer=p):
                            self.assertEqual(lookup(eligible, p)[0], _OK)
        selected = select_interpretations(self.catalog, self.card, "device_quality")
        self.assertEqual(selected[0], "device_quality: not eligible for an X1 verdict (x1.reasons lists why); no pass "
                                      "or fail is stated.")


if __name__ == "__main__":
    unittest.main()
