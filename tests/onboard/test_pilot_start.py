"""``python -m mycelic.collective.pilot.start``, offline, on synthetic exports the tests write. Every value is
invented; no public record is read.

Three shapes of export, each three years of records at five sites with one category rising at three sites in the
last year:

* **MSHA-like:** pipe-delimited, every field in double quotes, a quoted pipe and doubled quotes inside the narratives,
  dates M/D/YYYY, upper-case categories, mine names as sites (run with an outcomes file);
* **NHTSA-like:** CSV with a list category (``components[]``, ``A;B``), dates YYYYMMDD, two-letter states as sites;
* **generic JSON lines** with invented key names, ISO dates with a time, a number and a list key the roles ignore.

Markers are planted in the refused columns and in a few narratives (a person's name, a sentence). Every written
output is scanned for every record id, site value and refused value of four characters or more, and for any eight
consecutive words of any narrative. Then: the roles file's errors, a privacy floor that fails, outcomes given and not
given, the guard catching a planted site name, record id and narrative, the audit being ``pilot.audit``'s own run path,
and two runs giving the same ``pilot.json``. The worked example in ``docs/collective/PILOT.md`` is checked against a
fresh run of ``example`` and ``run``.

Then the review fixes, on variants of the example export: an export with no header row or a title line above it (no
record value in any error), placeholders such as ``NULL``, ``None`` and ``Other`` in a refused or site column (the
report is shown), the review items' own alerts, held-out rows not used by reason, record ids repeated or shared across
the cut, site values that differ only in case, too few sites, a byte-order mark in the roles file, an ``--out`` that
cannot be written, and each guard path that a mutation of the first build left untested."""
from __future__ import annotations

import contextlib
import csv
import dataclasses
import hashlib
import hmac
import io
import json
import random
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

from mycelic.collective.onboard import draft as D
from mycelic.collective.onboard import report as R
from mycelic.collective.onboard.check import ngram_hits
from mycelic.collective.onboard.exports import Export, read_export
from mycelic.collective.packs.canonical import folded
from mycelic.collective.packs.loader import load_pack_dir
from mycelic.collective.pilot import audit as A
from mycelic.collective.pilot import start as P

ROOT = Path(__file__).resolve().parents[2]
PILOT_DOC = ROOT / "docs" / "collective" / "PILOT.md"
ORIGINAL = (A.run_pipeline, A.hq_results, A._alerts)
RENDER = P.render_md
DRAFT_DATA = P._draft_data
NON_SPECIFIC = D.load_language("en").non_specific

# --------------------------------------------------------------------------------------------------- the exports

FILLER = ("employee", "working", "shift", "crew", "morning", "reported", "nearby", "section", "worker", "tool",
          "area", "while", "during", "noticed", "returned", "afternoon")
PERSON = "Ezekiel Thornbury"
SENTENCE = "The zebra lantern quietly hummed beneath the copper gantry all night long"
START = date(2022, 1, 3)
WEEKS = 156
TRAIN_UNTIL = "2023-12-31"
BURST_WEEKS = (140, 147)


def narrative(rng: random.Random, cues: dict[str, tuple[str, ...]], cat: str) -> str:
    """Two cue words of the category, filler, and in about one narrative of three a cue word of another category."""
    noise = [rng.choice(cues[rng.choice(sorted(c for c in cues if c != cat))])] if rng.random() < 0.35 else []
    first = " ".join(rng.sample(cues[cat], 2) + noise + rng.sample(FILLER, 5))
    second = " ".join(rng.sample(FILLER, 4) + [str(rng.randint(2, 99)), "feet"])
    return f"{first.capitalize()}. {second.capitalize()}."


def history(cues: dict[str, tuple[str, ...]], sites: tuple[str, ...], burst: str, seed: int,
            texts: Callable[[str], str] | None = None) -> list[dict[str, Any]]:
    """Five records a week at random sites and categories, and in ``BURST_WEEKS`` four more a week of ``burst`` at
    each of the first three sites. A person's name is written into five narratives and a sentence into three.
    ``texts(category)`` replaces the narrative generator when given."""
    rng = random.Random(seed)
    cats = sorted(cues)
    out: list[dict[str, Any]] = []
    for week in range(WEEKS):
        monday = START + timedelta(days=7 * week)
        for _ in range(5):
            out.append({"site": rng.choice(sites), "day": monday + timedelta(days=rng.randrange(7)),
                        "cat": rng.choice(cats)})
        if BURST_WEEKS[0] <= week < BURST_WEEKS[1]:
            for site in sites[:3]:
                out += [{"site": site, "day": monday + timedelta(days=rng.randrange(5)), "cat": burst}
                        for _ in range(4)]
    out.sort(key=lambda r: r["day"])
    for i, r in enumerate(out, start=1):
        r["n"] = i
        r["text"] = texts(r["cat"]) if texts is not None else narrative(rng, cues, r["cat"])
        if i % 151 == 0:
            r["text"] += f" {PERSON} helped."
        elif i % 251 == 0:
            r["text"] += f" {SENTENCE}."
    return out


MSHA_CUES = {"SLIP OR FALL OF PERSON": ("slipped", "icy", "walkway", "stairs", "ladder"),
             "HANDLING OF MATERIALS": ("lifting", "carrying", "bag", "strained", "pallet"),
             "POWERED HAULAGE": ("shuttle", "scoop", "conveyor", "haul", "trolley"),
             "MACHINERY": ("crusher", "pinch", "guard", "drill", "belt"),
             "HAND TOOLS": ("hammer", "wrench", "chisel", "grinder", "knife"),
             "NO VALUE FOUND": ("assorted", "various", "general")}
MSHA_SITES = ("Quillfeather Ridge Mine", "Brackenridge Hollow", "Tamsworth Pebble Pit", "Ossory Fell Mine",
              "Wrenfield Deep")
MSHA_COLUMNS = ("DOCUMENT_NO", "MINE_NAME", "ACCIDENT_DT", "NARRATIVE", "CLASSIFICATION", "OPERATOR_NAME",
                "CONTRACTOR_ID")
MSHA_PEOPLE = (PERSON, "Marisol Quenneville", "Tobias Wrexham")


def msha_like(directory: Path, seed: int = 7) -> dict[str, Any]:
    recs = history(MSHA_CUES, MSHA_SITES, "HANDLING OF MATERIALS", seed)
    lines = ["|".join(MSHA_COLUMNS)]
    refs, refused = [], set()
    for r in recs:
        ref = f"2201{r['n']:08d}"
        person, contractor = MSHA_PEOPLE[r["n"] % 3], f"ZXQ{r['n'] % 7}K"
        text = r["text"] + ' Noted "see log | ref" later.'
        row = (ref, r["site"], r["day"].strftime("%m/%d/%Y"), text, r["cat"], person, contractor)
        lines.append("|".join('"' + v.replace('"', '""') + '"' for v in row))
        refs.append(ref)
        refused.update((person, contractor))
    path = directory / "accidents.txt"
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    roles = {"language": "en", "roles": {"record_id": "DOCUMENT_NO", "site": "MINE_NAME", "date": "ACCIDENT_DT",
                                         "narrative": "NARRATIVE", "category": "CLASSIFICATION",
                                         "forbidden": ["OPERATOR_NAME", "CONTRACTOR_ID"]}}
    outcomes = directory / "outcomes.csv"
    outcomes.write_text("outcome_id,opened,category,title\n"
                        "CAPA-7,2024-11-11,handling of materials,bags\n"      # the burst, written in lower case
                        "CAPA-8,2024-12-09,,any\n"                            # any category
                        "CAPA-9,2024-08-05,NO VALUE FOUND,other bucket\n"     # not a predicate
                        "CAPA-10,2024-03-04,MACHINERY,too early\n"            # out of scope
                        "CAPA-11,2024-07-01,ROOF FALL,never filed\n",         # not in the training records
                        encoding="utf-8")
    return {"export": path, "roles": roles, "outcomes": outcomes, "records": recs, "refs": refs,
            "sites": list(MSHA_SITES), "refused": sorted(refused), "format": "pipe"}


NHTSA_CUES = {"ENGINE": ("stalled", "idle", "rpm", "misfire", "coolant"),
              "ELECTRICAL SYSTEM": ("battery", "wiring", "fuse", "dashboard", "alternator"),
              "SERVICE BRAKES": ("brake", "pedal", "stopping", "rotor", "abs"),
              "STEERING": ("steering", "wheel", "pulled", "column", "rack"),
              "AIR BAGS": ("airbag", "deployed", "inflator", "sensor", "seat"),
              "UNKNOWN OR OTHER": ("assorted", "various", "general")}
NHTSA_SITES = ("OH", "TX", "MI", "CA", "FL")


def nhtsa_like(directory: Path, seed: int = 11) -> dict[str, Any]:
    recs = history(NHTSA_CUES, NHTSA_SITES, "SERVICE BRAKES", seed)
    rng = random.Random(seed + 1)
    cats = sorted(NHTSA_CUES)
    lines = ["odino,state,received,summary,components[],owner_name,vin"]
    refs, refused = [], set()
    for r in recs:
        ref = f"11{r['n']:06d}"
        comps = [r["cat"]] + ([rng.choice([c for c in cats if c != r["cat"]])] if rng.random() < 0.25 else [])
        owner, vin = f"{MSHA_PEOPLE[r['n'] % 3]}", f"1HGCM8263{r['n']:08d}"
        text = r["text"].replace('"', "'")
        lines.append(",".join([ref, r["site"], r["day"].strftime("%Y%m%d"), f'"{text}, the owner said"',
                               ";".join(comps), owner, vin]))
        refs.append(ref)
        refused.update((owner, vin))
    path = directory / "complaints.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    roles = {"roles": {"record_id": "odino", "site": "state", "date": "received", "narrative": "summary",
                       "category": "components[]", "forbidden": ["owner_name", "vin"], "entities": [],
                       "reporter": None}}
    return {"export": path, "roles": roles, "outcomes": None, "records": recs, "refs": refs,
            "sites": list(NHTSA_SITES), "refused": sorted(refused), "format": "comma"}


GENERIC_CUES = {"Login trouble": ("password", "locked", "login", "reset", "token"),
                "Billing dispute": ("invoice", "charged", "refund", "twice", "billing"),
                "Late delivery": ("courier", "delayed", "tracking", "late", "waiting"),
                "Damaged parcel": ("crushed", "torn", "soaked", "broken", "dented"),
                "Wrong item": ("wrong", "size", "colour", "swapped", "mismatch"),
                "Misc": ("assorted", "various", "general")}
GENERIC_SITES = ("Riverbend Works", "Ashcombe Yard", "Kestrel Point Plant", "Millbrook Depot", "Harrow Lane Mill")


def generic_jsonl(directory: Path, seed: int = 13, cues: dict[str, tuple[str, ...]] | None = None,
                  texts: Callable[[str], str] | None = None, name: str = "tickets.jsonl") -> dict[str, Any]:
    cues = cues or GENERIC_CUES
    recs = history(cues, GENERIC_SITES, "Late delivery" if "Late delivery" in cues else sorted(cues)[0], seed, texts)
    path = directory / name
    refs, refused = [], set()
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            ref = f"TKT-{r['day'].year}-{r['n']:06d}"
            staff, badge = MSHA_PEOPLE[r["n"] % 3], f"BDG-{9000 + r['n'] % 500}"
            fh.write(json.dumps({"ticket_ref": ref, "branch": r["site"],
                                 "logged_on": f"{r['day'].isoformat()}T09:41:00", "what_happened": r["text"],
                                 "issue_kind": r["cat"], "staff_member": staff, "badge": badge, "severity": r["n"] % 4, "tags[]": ["x", "y"]}) + "\n")
            refs.append(ref)
            refused.update((staff, badge))
    roles = {"language": "en", "roles": {"record_id": "ticket_ref", "site": "branch", "date": "logged_on",
                                         "narrative": "what_happened", "category": "issue_kind",
                                         "forbidden": ["staff_member", "badge"]}}
    return {"export": path, "roles": roles, "outcomes": None, "records": recs, "refs": refs,
            "sites": list(GENERIC_SITES), "refused": sorted(refused), "format": "jsonl"}


def write_roles(directory: Path, roles: Any, name: str = "roles.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(roles), encoding="utf-8")
    return path


def run_main(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = P.main(args)
    return code, out.getvalue(), err.getvalue()


def token_hits(text: str, values: list[str]) -> list[str]:
    """Values found in ``text`` as whole tokens, case-insensitive."""
    low = text.lower()
    return [v for v in values if re.search(r"(?<![0-9a-z])" + re.escape(v.lower()) + r"(?![0-9a-z])", low)]


def secrets(made: dict[str, Any]) -> list[str]:
    values = [*made["refs"], *made["sites"], *made["refused"], PERSON, *PERSON.split(), SENTENCE]
    return sorted({v for v in values if len(v) >= 4})


# --------------------------------------------------------------------------------------------------- shared runs

_RUNS: dict[str, dict[str, Any]] = {}
_HOLDER = tempfile.TemporaryDirectory()
TMP = Path(_HOLDER.name)


def shape_run(shape: str) -> dict[str, Any]:
    """One run of ``run`` per shape (made once per test process), with ``--work``."""
    if shape not in _RUNS:
        directory = TMP / shape
        directory.mkdir()
        made = {"msha": msha_like, "nhtsa": nhtsa_like, "generic": generic_jsonl}[shape](directory)
        roles = write_roles(directory, made["roles"])
        args = ["run", "--export", str(made["export"]), "--roles", str(roles), "--out", str(directory / "out"),
                "--work", str(directory / "work"), "--train-until", TRAIN_UNTIL]
        if made["outcomes"] is not None:
            args += ["--outcomes", str(made["outcomes"])]
        code, stdout, stderr = run_main(args)
        out = directory / "out"
        _RUNS[shape] = {**made, "dir": directory, "roles_path": roles, "code": code, "stdout": stdout,
                        "stderr": stderr, "md": (out / "pilot.md").read_text(encoding="utf-8"),
                        "json_text": (out / "pilot.json").read_text(encoding="utf-8")}
        _RUNS[shape]["doc"] = json.loads(_RUNS[shape]["json_text"])
    return _RUNS[shape]


def tearDownModule() -> None:
    _RUNS.clear()
    _HOLDER.cleanup()


# --------------------------------------------------------------------------------------------------- three shapes

class ShapeTests(unittest.TestCase):
    """Each shape drafts, passes the floor, audits and writes both files, and nothing identifying is in them."""

    def test_each_shape_runs_and_is_read_as_written(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                run = shape_run(shape)
                self.assertEqual(run["code"], 0, run["stderr"])
                doc = run["doc"]
                self.assertEqual(doc["status"], "shown")
                self.assertEqual(doc["inputs"]["export"]["format"], run["format"])
                self.assertEqual(doc["inputs"]["export"]["rows"], len(run["records"]))
                self.assertEqual(doc["inputs"]["export"]["rejected"], {})
                self.assertEqual(doc["split"]["train_until"], TRAIN_UNTIL)
                self.assertTrue(doc["split"]["train_until_given"])
                self.assertEqual(doc["split"]["undated"], 0)
                self.assertTrue(doc["floor"]["passed"])
                self.assertTrue(all(v["passed"] for v in doc["floor"]["checks"].values()))
                self.assertEqual(set(doc["floor"]["checks"]), set(P.CHECK_NAMES))
                cues = {"msha": MSHA_CUES, "nhtsa": NHTSA_CUES, "generic": GENERIC_CUES}[shape]
                self.assertEqual(sorted(p["label"] for p in doc["draft"]["predicates"]),
                                 sorted(c for c in cues if c.lower() not in NON_SPECIFIC))
                self.assertEqual(doc["draft"]["categories"]["other"],
                                 sum(1 for c in cues if c.lower() in NON_SPECIFIC))
                self.assertTrue(all(p["first_terms"] for p in doc["draft"]["predicates"]))

    def test_the_list_category_is_read_as_a_list(self) -> None:
        doc = shape_run("nhtsa")["doc"]
        self.assertTrue(doc["inputs"]["roles"]["list_category"])
        self.assertEqual(doc["inputs"]["roles"]["roles"]["category"], "components[]")

    def test_the_quoted_pipe_does_not_split_a_field(self) -> None:
        run = shape_run("msha")
        export = read_export(run["export"])
        self.assertEqual(export.format, "pipe")
        self.assertTrue(all(export.value(i, "NARRATIVE").endswith('Noted "see log | ref" later.')
                            for i in range(len(export))))

    def test_sites_are_labels_and_every_site_sent_cells(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                a = shape_run(shape)["doc"]["audit"]
                self.assertEqual(a["sites"], ["s01", "s02", "s03", "s04", "s05"])
                self.assertEqual([s["site"] for s in a["per_site"]], a["sites"])
                self.assertTrue(all(s["weekly_bundles"] > 0 and s["cells"] > 0 for s in a["per_site"]))
                self.assertEqual(a["cells"], sum(s["cells"] for s in a["per_site"]))
                self.assertGreater(a["sent"]["bytes"], 0)
                self.assertEqual(a["sites_in_export"], 5)

    def test_the_labels_follow_a_hash_keyed_by_the_export_not_the_names(self) -> None:
        """Labels in the sorted order of the names map back to the sites for anyone who knows the site list; the
        order is that of an HMAC keyed by a hash of the export's own bytes, which pilot.json does not print."""
        for shape, sites in (("generic", GENERIC_SITES), ("msha", MSHA_SITES)):
            with self.subTest(shape=shape):
                run = shape_run(shape)
                maps = (run["dir"] / "work" / "sites.csv").read_text(encoding="utf-8").splitlines()[1:]
                key = P.site_key(run["export"].read_bytes())
                ranked = sorted(sites, key=lambda v: hmac.new(key, folded(v).encode(), hashlib.sha256).digest())
                self.assertEqual(maps, sorted(f"s{i:02d},{v}" for i, v in enumerate(ranked, start=1)))
                self.assertNotEqual(maps, [f"s{i:02d},{v}" for i, v in enumerate(sorted(sites), start=1)])
                self.assertNotEqual(key, hashlib.sha256(run["export"].read_bytes()).digest())
                self.assertNotIn(key.hex(), run["json_text"])

    def test_alerts_keep_the_weekly_cell_channels_apart_from_the_reference_channels(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                run = shape_run(shape)
                alerts = run["doc"]["audit"]["alerts"]
                self.assertEqual(list(alerts["weekly_cells"]), ["S", "X"])
                self.assertEqual(sorted(alerts["reference"]), ["P", "PRR", "R_mf"])
                lines = [line for line in run["md"].splitlines() if line.startswith("- From the weekly cells")
                         or line.startswith("- Reference channels")]
                self.assertEqual(len(lines), 2)
                self.assertIn("X ", lines[0])
                self.assertNotIn("R_mf", lines[0])
                self.assertIn("R_mf", lines[1])

    def test_the_planted_rise_is_on_the_review_list_from_the_weekly_cells(self) -> None:
        for shape, burst in (("nhtsa", "SERVICE BRAKES"), ("generic", "Late delivery")):
            with self.subTest(shape=shape):
                review = shape_run(shape)["doc"]["review"]
                item = next(r for r in review["from_cells"] if r["predicate"] == burst)
                self.assertIn("X", item["channels"])
                self.assertGreaterEqual(len(item["sites"]), 2)
                self.assertTrue(all(re.fullmatch(r"s0[1-5]", s) for s in item["sites"]))
                self.assertEqual(review["total"], len(review["from_cells"]) + len(review["reference_only"]))

    def test_no_r_mf_only_item_is_shown_with_the_weekly_cells(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                review = shape_run(shape)["doc"]["review"]
                self.assertTrue(all(set(r["channels"]) <= {"X", "S"} and r["channels"]
                                    for r in review["from_cells"]))
                self.assertTrue(all(r["channels"] == ["R_mf"] for r in review["reference_only"]))

    def test_nothing_identifying_is_written(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                run = shape_run(shape)
                self.assertEqual(sorted(p.name for p in (run["dir"] / "out").iterdir()), ["pilot.json", "pilot.md"])
                for text in (run["md"], run["json_text"], run["stdout"], run["stderr"]):
                    self.assertEqual(token_hits(text, secrets(run)), [])
                narratives = [r["text"] for r in run["records"]]
                self.assertEqual(ngram_hits([run["md"], run["json_text"]], narratives, 8), set())
                self.assertNotIn("pilot-start-", run["json_text"])
                self.assertNotIn(str(run["dir"]), run["json_text"])

    def test_the_scan_would_find_a_planted_value(self) -> None:
        run = shape_run("generic")
        self.assertEqual(token_hits(run["md"] + " Riverbend Works", secrets(run)), ["Riverbend Works"])

    def test_the_report_states_the_guard_and_what_it_does_not_show(self) -> None:
        run = shape_run("generic")
        self.assertIn("Found: none.", run["md"])
        self.assertEqual(run["doc"]["not_shown"], list(P.NOT_SHOWN))
        section = run["md"].split("## What this does not show", 1)[1]
        for line in P.NOT_SHOWN:
            self.assertIn(line, section)
        self.assertGreater(run["doc"]["guard"]["record_ids"], 0)

    def test_pilot_md_is_rendered_from_pilot_json_alone(self) -> None:
        for shape in ("msha", "nhtsa", "generic"):
            with self.subTest(shape=shape):
                run = shape_run(shape)
                self.assertEqual(P.render_md(json.loads(run["json_text"])), run["md"])

    def test_the_watch_puts_the_audit_functions_back(self) -> None:
        shape_run("generic")
        self.assertEqual((A.run_pipeline, A.hq_results, A._alerts), ORIGINAL)

    def test_the_work_directory_holds_the_pack_and_the_audit(self) -> None:
        run = shape_run("msha")
        work = run["dir"] / "work"
        self.assertEqual(load_pack_dir(work / "pack").id, P.PACK_ID)
        audit = json.loads((work / "audit" / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["export"]["sites"], run["doc"]["audit"]["sites"])
        self.assertEqual((work / "issues.csv").read_text(encoding="utf-8").splitlines()[1], "i01,CAPA-7")


class AuditRunPathTests(unittest.TestCase):
    def test_the_audit_is_pilot_audits_own_run_path(self) -> None:
        """``pilot.audit run`` over the drafted pack, the same labelled rows and the same outcomes writes the very
        audit.json the run kept."""
        run = shape_run("msha")
        work = run["dir"] / "work"
        roles = P.load_roles(run["roles_path"])
        export = read_export(run["export"])
        params, template = D.load_params(), D.load_template()
        dated = D.date_column(export, roles, D.load_language("en"), params)
        cut = date.fromisoformat(TRAIN_UNTIL)
        held = D.Window(cut + timedelta(days=1), max(d for d in dated.dates if d is not None))
        labels = P.site_labels(export, roles, P.site_key(run["export"].read_bytes())).of_value
        d = D.draft_export(export, roles, D.Window(min(d for d in dated.dates if d), cut), P.PACK_ID)
        rows, _ = D.normalised_rows(P.labelled_export(export, roles, labels), roles, dated, held, template, d.etypes)
        D.write_jsonl(rows, run["dir"] / "rows.jsonl")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = A.main(["run", "--pack", str(work / "pack"), "--records", str(run["dir"] / "rows.jsonl"),
                           "--outcomes", str(work / "outcomes.csv"), "--out", str(run["dir"] / "audit-cli")])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual((run["dir"] / "audit-cli" / "audit.json").read_bytes(),
                         (work / "audit" / "audit.json").read_bytes())


# --------------------------------------------------------------------------------------------------- outcomes

class OutcomeTests(unittest.TestCase):
    def test_outcomes_given_are_scored_against_chance(self) -> None:
        o = shape_run("msha")["doc"]["outcomes"]
        self.assertEqual(o["given"], 5)
        self.assertEqual(o["scored"], 3)
        self.assertEqual(o["not_scored"], {"other_bucket": 1, "below_floor": 0, "refused": 0, "not_in_training": 1})
        self.assertEqual(o["out_of_scope"], ["i04"])
        self.assertEqual(o["in_scope"], 2)
        self.assertEqual(P.ordered(o["channels"]), ["X", "S", "R_mf", "P", "PRR"])
        self.assertEqual(sorted(o["channels"]), ["P", "PRR", "R_mf", "S", "X"])
        for name, s in o["channels"].items():
            with self.subTest(channel=name):
                self.assertEqual(s["outcomes"], 2)
                self.assertIsInstance(s["expected_found"], float)
                self.assertIsInstance(s["p_value"], float)
        rows = {r["issue"]: r for r in o["by_issue"]}
        self.assertEqual(sorted(rows), ["i01", "i02"])
        self.assertEqual(rows["i01"]["category"], "HANDLING OF MATERIALS")
        self.assertIsNone(rows["i02"]["category"])
        self.assertTrue(rows["i01"]["channels"]["X"]["found"])

    def test_the_report_prints_the_answers_against_chance(self) -> None:
        md = shape_run("msha")["md"]
        section = md.split("## 6. Against the issues you acted on", 1)[1].split("## What this does not show")[0]
        self.assertIn("| Channel | Found before opening |", section)
        self.assertIn("Expected by chance", section)
        self.assertIn("| i01 | HANDLING OF MATERIALS |", section)
        self.assertIn("1 in the other bucket", section)
        self.assertIn("1 not filed in any training record with a narrative", section)
        self.assertNotIn("CAPA-", md)

    def test_no_outcomes_measures_nothing(self) -> None:
        run = shape_run("generic")
        self.assertIsNone(run["doc"]["outcomes"])
        self.assertIn("No outcomes file was given", run["md"])
        self.assertEqual(run["doc"]["inputs"]["outcomes"], None)

    def test_an_outcomes_file_without_its_columns_is_refused(self) -> None:
        run = shape_run("generic")
        bad = run["dir"] / "bad-outcomes.csv"
        bad.write_text("outcome_id,opened\nX-1,2024-11-11\n", encoding="utf-8")
        code, _, err = run_main(["run", "--export", str(run["export"]), "--roles", str(run["roles_path"]),
                                 "--outcomes", str(bad), "--out", str(run["dir"] / "bad-out"),
                                 "--train-until", TRAIN_UNTIL])
        self.assertEqual(code, 2)
        self.assertIn("category", err)
        self.assertFalse((run["dir"] / "bad-out").exists())


# --------------------------------------------------------------------------------------------------- the roles file

class RolesTests(unittest.TestCase):
    """Strict checks, each error naming what is wrong. None of these runs reads past the roles and the header."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = TMP / "roles"
        cls.dir.mkdir()
        cls.made = generic_jsonl(cls.dir)

    def error(self, roles: Any) -> str:
        path = write_roles(self.dir, roles, f"r{len(list(self.dir.glob('r*.json')))}.json")
        code, _, err = run_main(["run", "--export", str(self.made["export"]), "--roles", str(path), "--out",
                                 str(self.dir / "never")])
        self.assertEqual(code, 2)
        self.assertFalse((self.dir / "never").exists())
        return err

    def roles(self, **change: Any) -> dict[str, Any]:
        r = dict(self.made["roles"]["roles"])
        for k, v in change.items():
            if v is None:
                r.pop(k)
            else:
                r[k] = v
        return {"language": "en", "roles": r}

    def test_a_missing_role_is_named(self) -> None:
        for role in ("record_id", "site", "date", "narrative", "category", "forbidden"):
            with self.subTest(role=role):
                err = self.error(self.roles(**{role: None}))
                self.assertIn(f'the role "{role}"', err)
                self.assertIn(P.ROLE_HELP[role], err)

    def test_every_missing_role_is_named_at_once(self) -> None:
        err = self.error(self.roles(site=None, date=None))
        self.assertIn('the role "site"', err)
        self.assertIn('the role "date"', err)

    def test_an_unknown_role_or_key_is_refused(self) -> None:
        self.assertIn("unknown roles plant", self.error(self.roles(plant="branch")))
        self.assertIn("unknown keys extra", self.error({**self.roles(), "extra": 1}))
        self.assertIn('no "roles" object', self.error({"language": "en"}))

    def test_a_column_the_export_lacks_is_named_with_its_role_and_the_header_is_not_listed(self) -> None:
        """The header's names are not echoed: without a header row they would be the first record's values."""
        err = self.error(self.roles(site="plant_name"))
        self.assertIn("'plant_name'", err)
        self.assertIn('"site"', err)
        self.assertIn("has 9 columns", err)
        self.assertIn("first non-empty line of the export is its header", err)
        for column in ("ticket_ref", "branch", "logged_on", "what_happened", "staff_member", "badge"):
            self.assertNotIn(column, err)

    def test_a_roles_file_with_a_byte_order_mark_is_read(self) -> None:
        path = self.dir / "bom-roles.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(self.made["roles"]).encode("utf-8"))
        roles = P.load_roles(path)
        self.assertEqual((roles.site, roles.forbidden), ("branch", ("staff_member", "badge")))

    def test_a_record_id_site_date_or_narrative_cannot_be_a_list(self) -> None:
        for role in P.SCALAR_ROLES:
            with self.subTest(role=role):
                self.assertIn("cannot be a list column", self.error(self.roles(**{role: "tags[]"})))

    def test_a_column_takes_one_role(self) -> None:
        self.assertIn("at most one role", self.error(self.roles(forbidden=["branch"])))

    def test_types_and_language_are_checked(self) -> None:
        self.assertIn("must name one column", self.error(self.roles(site=["branch"])))
        self.assertIn("must name one column", self.error(self.roles(site=" branch")))
        self.assertIn("list of column names", self.error(self.roles(forbidden="staff_member")))
        self.assertIn("no language file for 'xx'", self.error({**self.roles(), "language": "xx"}))
        self.assertIn("language code", self.error({**self.roles(), "language": "English"}))

    def test_a_file_that_is_not_json_is_refused(self) -> None:
        path = self.dir / "broken.json"
        path.write_text("{roles: }", encoding="utf-8")
        code, _, err = run_main(["run", "--export", str(self.made["export"]), "--roles", str(path), "--out",
                                 str(self.dir / "never")])
        self.assertEqual(code, 2)
        self.assertIn("is not JSON", err)

    def test_entities_and_reporter_are_optional_and_language_defaults_to_english(self) -> None:
        roles = P.roles_from_obj({"roles": self.made["roles"]["roles"]})
        self.assertEqual((roles.language, roles.entities, roles.reporter), ("en", (), None))


# --------------------------------------------------------------------------------------------------- the cut

class CutTests(unittest.TestCase):
    def test_the_default_cut_leaves_the_last_quarter_after_it(self) -> None:
        days = [date(2024, 1, d) for d in range(1, 9)]
        self.assertEqual(P.default_train_until(days), date(2024, 1, 6))
        self.assertEqual(P.default_train_until(list(reversed(days))), date(2024, 1, 6))
        self.assertEqual(P.default_train_until(days[:4]), date(2024, 1, 3))
        tied = [date(2024, 1, 1)] * 3 + [date(2024, 1, 2)] * 5
        self.assertEqual(P.default_train_until(tied), date(2024, 1, 2))

    def test_a_cut_that_leaves_nothing_is_refused(self) -> None:
        run = shape_run("generic")
        for cut, says in (("2030-01-01", "nothing left to audit"), ("2001-01-01", "nothing to draft from"),
                          ("2024-13-01", "YYYY-MM-DD")):
            with self.subTest(cut=cut):
                code, _, err = run_main(["run", "--export", str(run["export"]), "--roles", str(run["roles_path"]),
                                         "--out", str(run["dir"] / "cut-out"), "--train-until", cut])
                self.assertEqual(code, 2)
                self.assertIn(says, err)

    def test_a_cut_that_leaves_too_few_weeks_says_so(self) -> None:
        run = shape_run("generic")
        code, _, err = run_main(["run", "--export", str(run["export"]), "--roles", str(run["roles_path"]),
                                 "--out", str(run["dir"] / "late-out"), "--train-until", "2024-11-30"])
        self.assertEqual(code, 2)
        self.assertIn("earlier --train-until", err)


# --------------------------------------------------------------------------------------------------- the floor

FLOOR_CUES = dict(GENERIC_CUES)
QUOTED = "Customer said the parcel arrived soaked through after the heavy rain"
FLOOR_CUES[QUOTED] = ("drenched", "puddle", "rain")


class FloorTests(unittest.TestCase):
    """A filed category that the narratives quote: the drafted pack holds eight words of a narrative, the floor's
    n-gram check fails, and the run stops with neither the label nor any term written."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = TMP / "floor"
        cls.dir.mkdir()
        rng = random.Random(3)

        def texts(cat: str) -> str:
            text = narrative(rng, FLOOR_CUES, cat)
            return f"{QUOTED}. {text}" if cat == QUOTED else text

        cls.made = generic_jsonl(cls.dir, cues=FLOOR_CUES, texts=texts)
        roles = write_roles(cls.dir, cls.made["roles"])
        cls.code, cls.stdout, cls.stderr = run_main(["run", "--export", str(cls.made["export"]), "--roles",
                                                     str(roles), "--out", str(cls.dir / "out"),
                                                     "--train-until", TRAIN_UNTIL])
        cls.md = (cls.dir / "out" / "pilot.md").read_text(encoding="utf-8")
        cls.json_text = (cls.dir / "out" / "pilot.json").read_text(encoding="utf-8")
        cls.doc = json.loads(cls.json_text)

    def test_the_run_stops_with_a_clear_message(self) -> None:
        self.assertEqual(self.code, 1)
        self.assertIn("failed its privacy floor (narrative n-grams)", self.stderr)
        self.assertEqual(self.doc["status"], "privacy_floor_failed")
        self.assertFalse(self.doc["floor"]["passed"])
        self.assertFalse(self.doc["floor"]["checks"]["ngram"]["passed"])
        self.assertIsNone(self.doc["audit"])
        self.assertIn("Failed: the run stops here.", self.md)
        self.assertNotIn("## 3. What left each site", self.md)

    def test_no_label_or_term_is_written(self) -> None:
        for text in (self.md, self.json_text):
            self.assertNotIn("Customer said", text)
            self.assertEqual(token_hits(text, ["Late delivery", "courier", "password", *secrets(self.made)]), [])
        self.assertTrue(all(set(p) == {"code", "records", "terms"} for p in self.doc["draft"]["predicates"]))


# --------------------------------------------------------------------------------------------------- the guard

class GuardTests(unittest.TestCase):
    """The guard runs before anything is written: a site name, a record id, a refused value or eight words of a
    narrative planted in what would be written replaces both files with the hit counts. Each is planted where a
    record value would reach the report, in a string the run printed (a predicate's first terms), which the
    skeleton the exemptions are read from leaves out."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = TMP / "guard"
        cls.dir.mkdir()
        cls.made = generic_jsonl(cls.dir, seed=17)
        roles = write_roles(cls.dir, cls.made["roles"])
        cls.computed = P.compute(cls.made["export"], roles, None, TRAIN_UNTIL, cls.dir / "work")

    def present_with(self, planted: str) -> tuple[dict[str, Any], str, int]:
        doc = json.loads(json.dumps(self.computed.doc))
        doc["draft"]["predicates"][0]["first_terms"].append(planted.strip())
        return P.present(dataclasses.replace(self.computed, doc=doc))

    def assert_withheld(self, planted: str, kind: str) -> dict[str, Any]:
        doc, md, code = self.present_with(planted)
        self.assertEqual((doc["status"], code), ("withheld", 1))
        self.assertGreater(doc["guard"].get(kind, 0), 0, doc["guard"])
        self.assertNotIn(planted.strip(), md + json.dumps(doc))
        self.assertEqual(set(doc), {"kind", "schema_version", "status", "label", "guard", "withheld"})
        return doc

    def test_clean_output_is_shown(self) -> None:
        doc, md, code = P.present(self.computed)
        self.assertEqual((doc["status"], code), ("shown", 0))
        self.assertIn("Found: none.", md)

    def test_a_planted_site_name_is_caught(self) -> None:
        self.assert_withheld("\nKestrel Point Plant\n", "report_site")

    def test_a_short_planted_site_name_is_caught(self) -> None:
        site = sorted(self.made["sites"], key=len)[0]
        self.assert_withheld(f"\n{site.split()[0]} {site.split()[1]}\n", "refused_value")

    def test_a_planted_record_id_is_caught(self) -> None:
        self.assert_withheld(f"\n{self.made['refs'][123]}\n", "report_record_id")

    def test_a_planted_refused_value_is_caught(self) -> None:
        self.assert_withheld("\nMarisol Quenneville\n", "refused_value")

    def test_a_planted_narrative_is_caught(self) -> None:
        self.assert_withheld("\n" + self.made["records"][40]["text"] + "\n", "narrative_ngrams")

    def test_a_record_value_in_a_printed_term_is_caught_by_the_last_guard(self) -> None:
        planted = dict(self.computed.entry)
        facts = json.loads(json.dumps(planted["draft"]))
        facts["predicates"][0]["first_terms"].append(self.made["refs"][5])
        computed = P.Computed(doc=self.computed.doc, guard=self.computed.guard, entry={**planted, "draft": facts},
                              extra_strings=[], sent=self.computed.sent)
        doc, _, code = P.present(computed)
        self.assertEqual((doc["status"], code), ("withheld", 1))
        self.assertGreater(doc["guard"]["refused_equal"], 0)

    def test_a_value_the_fixed_text_holds_is_counted_not_looked_for(self) -> None:
        """A value every rendering holds, whatever the run, is the report's own text: it is set aside and counted.
        (No real site is called this; the test plants it in every rendering, the skeleton's too.)"""
        with mock.patch.object(P, "render_md", side_effect=lambda doc: RENDER(doc) +
                               ("\nKestrel Point Plant\n" if doc["status"] != "withheld" else "")):
            doc, md, code = P.present(self.computed)
        self.assertEqual((doc["status"], code), ("shown", 0))
        self.assertEqual(doc["guard"]["exempt_values"], 1)
        self.assertIn("own fixed text holds them: 1 of those values and 0 runs of words. Found: none.", md)
        doc, _, _ = P.present(self.computed)
        self.assertEqual(doc["guard"]["exempt_values"], 0)

    def test_a_site_value_in_what_left_the_sites_is_caught(self) -> None:
        line = json.dumps({"artifact_type": "cells_bundle", "site": "Millbrook Depot"})
        computed = P.Computed(doc=self.computed.doc, guard=self.computed.guard, entry=self.computed.entry,
                              extra_strings=[], sent=(self.computed.sent or "") + line + "\n")
        doc, _, code = P.present(computed)
        self.assertEqual((doc["status"], code), ("withheld", 1))
        self.assertGreater(doc["guard"]["sent_report_site"], 0)
        unread = P.Computed(doc=self.computed.doc, guard=self.computed.guard, entry=self.computed.entry,
                            extra_strings=[], sent=None)
        self.assertEqual(P.present(unread)[0]["guard"], {"sent_unread": 1})

    def test_what_left_the_sites_carries_labels_only(self) -> None:
        sent = self.computed.sent
        self.assertTrue(sent)
        self.assertEqual(token_hits(sent, secrets(self.made)), [])
        self.assertIn('"s01"', sent)

    def test_the_guard_runs_before_anything_is_written(self) -> None:
        out = self.dir / "planted-out"

        def planted(*args: Any) -> dict[str, Any]:
            data = DRAFT_DATA(*args)
            data["predicates"][0].setdefault("first_terms", []).append("Riverbend Works")
            return data
        with mock.patch.object(P, "_draft_data", side_effect=planted):
            code, _, err = run_main(["run", "--export", str(self.made["export"]), "--roles",
                                     str(self.dir / "roles.json"), "--out", str(out), "--train-until", TRAIN_UNTIL])
        self.assertEqual(code, 1)
        self.assertIn("withheld by the guard", err)
        for name in ("pilot.md", "pilot.json"):
            text = (out / name).read_text(encoding="utf-8")
            self.assertNotIn("Riverbend", text)
        self.assertEqual(json.loads((out / "pilot.json").read_text())["status"], "withheld")

    def test_an_error_text_naming_a_record_value_is_withheld(self) -> None:
        guard = self.computed.guard
        self.assertEqual(guard.message("the export spans 3 weeks"), "the export spans 3 weeks")
        self.assertEqual(guard.message("site Ashcombe Yard rejected a record"), P.WITHHELD_ERROR)
        self.assertEqual(guard.message(f"record {self.made['refs'][9]} is bad"), P.WITHHELD_ERROR)

    def test_an_error_text_holding_a_word_of_a_refused_name_is_withheld(self) -> None:
        """A surname alone is no refused value and no site, so only the refusal's own check (refused_own) holds it."""
        guard = self.computed.guard
        text = "the column of Quenneville could not be read"
        self.assertFalse(any(guard.texts([text]).values()))
        self.assertEqual(guard.message(text), P.WITHHELD_ERROR)

    def run_with(self, target: Any, name: str, error: Exception) -> tuple[int, str, str]:
        with mock.patch.object(target, name, side_effect=error):
            return run_main(["run", "--export", str(self.made["export"]), "--roles", str(self.dir / "roles.json"),
                             "--out", str(self.dir / f"err-{name}"), "--train-until", TRAIN_UNTIL])

    def test_an_audit_error_naming_a_site_is_withheld_on_stderr(self) -> None:
        code, out, err = self.run_with(A, "audit", A.AuditError("site Harrow Lane Mill has no week to audit"))
        self.assertEqual(code, 2)
        self.assertIn(f"error: {P.WITHHELD_ERROR}", err)
        self.assertNotIn("Harrow", out + err)
        self.assertFalse((self.dir / "err-audit").exists())

    def test_a_draft_error_naming_a_record_id_is_withheld_on_stderr(self) -> None:
        ref = self.made["refs"][77]
        code, out, err = self.run_with(D, "draft_export", D.DraftError(f"record {ref} has a value too long"))
        self.assertEqual(code, 2)
        self.assertIn(f"error: DraftError: {P.WITHHELD_ERROR}", err)
        self.assertNotIn(ref, out + err)
        code, _, err = self.run_with(D, "draft_export", D.DraftError("no category reaches the predicate minimum"))
        self.assertEqual(code, 2)
        self.assertIn("error: DraftError: no category reaches the predicate minimum", err)

    def test_a_four_character_refused_value_is_looked_for(self) -> None:
        """Refused values of four characters with a letter (a surname in its own column) are looked for; three are
        not. Record ids and site values need five (the backstop's minimum)."""
        export = Export(format="comma", encoding="utf-8", columns=("ref", "site", "day", "text", "cat", "who"),
                        rows=(("R-0001", "Ashcombe Yard", "2024-01-01", "a text", "Cat", "Pike"),
                              ("R-0002", "Ashcombe Yard", "2024-01-02", "a text", "Cat", "Ng"),
                              ("R-0003", "Ashcombe Yard", "2024-01-03", "a text", "Cat", "Low")),
                        rejected={}, blank_lines=0)
        roles = P.roles_from_obj({"roles": {"record_id": "ref", "site": "site", "date": "day", "narrative": "text",
                                            "category": "cat", "forbidden": ["who"]}})
        guard = P.Guard(export, roles, D.load_params())
        self.assertEqual(guard.texts(["reported by Pike at noon"])["refused_value"], 1)
        self.assertEqual(guard.texts(["reported by Low at noon"])["refused_value"], 0)
        self.assertEqual(guard.summary()["refused_and_site_values"], 2)

    def test_a_value_only_in_a_pilot_json_string_is_caught(self) -> None:
        """A predicate's id is in pilot.json only (pilot.md prints its label)."""
        doc = json.loads(json.dumps(self.computed.doc))
        doc["draft"]["predicates"][0]["id"] = "Kestrel Point Plant"
        computed = P.Computed(doc=doc, guard=self.computed.guard, entry=self.computed.entry, extra_strings=[],
                              sent=self.computed.sent)
        self.assertNotIn("Kestrel", P.render_md(doc))
        out, md, code = P.present(computed)
        self.assertEqual((out["status"], code), ("withheld", 1))
        self.assertGreater(out["guard"]["report_site"], 0)
        self.assertNotIn("Kestrel", md + json.dumps(out))

    def test_an_entity_id_a_review_item_prints_goes_through_the_last_guard(self) -> None:
        computed = P.Computed(doc=self.computed.doc, guard=self.computed.guard, entry=self.computed.entry,
                              extra_strings=[self.made["refs"][3]], sent=self.computed.sent)
        doc, _, code = P.present(computed)
        self.assertEqual((doc["status"], code), ("withheld", 1))
        self.assertGreater(doc["guard"]["refused_equal"], 0)

    def test_eight_words_of_a_narrative_in_what_left_the_sites_are_caught(self) -> None:
        line = json.dumps({"artifact_type": "cells_bundle", "note": self.made["records"][40]["text"]})
        computed = P.Computed(doc=self.computed.doc, guard=self.computed.guard, entry=self.computed.entry,
                              extra_strings=[], sent=(self.computed.sent or "") + line + "\n")
        doc, _, code = P.present(computed)
        self.assertEqual((doc["status"], code), ("withheld", 1))
        self.assertGreater(doc["guard"]["sent_narrative_ngrams"], 0)

    def test_an_error_text_is_scanned_with_nothing_exempt(self) -> None:
        """A narrative set aside for the report is still looked for in an error text (whose words the refusal alone
        would not catch), and the exemption is kept for the report."""
        guard = P.Guard(read_export(self.made["export"]), P.load_roles(self.dir / "roles.json"), D.load_params())
        text = self.made["records"][40]["text"]
        guard.exempt([text])
        self.assertGreater(guard.summary()["exempt_ngrams"], 0)
        self.assertEqual(guard.texts([text])["narrative_ngrams"], 0)
        self.assertIsNone(guard.sentinels.refusal.term(text))
        self.assertEqual(guard.message(text), P.WITHHELD_ERROR)
        self.assertEqual(guard.texts([text])["narrative_ngrams"], 0)

    def test_a_narrative_that_quotes_the_fixed_text_is_set_aside_and_counted(self) -> None:
        """Eight words of the report's own text in a narrative say nothing about that record: set aside, counted."""
        quoted = P.NOT_SHOWN[0].split(". ")[1]
        export = Export(format="comma", encoding="utf-8", columns=("ref", "site", "day", "text", "cat", "who"),
                        rows=(("R-0001", "Ashcombe Yard", "2024-01-01", f"Noted: {quoted}", "Cat", "Pike"),),
                        rejected={}, blank_lines=0)
        roles = P.roles_from_obj({"roles": {"record_id": "ref", "site": "site", "date": "day", "narrative": "text",
                                            "category": "cat", "forbidden": ["who"]}})
        guard = P.Guard(export, roles, D.load_params())
        self.assertGreater(guard.texts([P.NOT_SHOWN[0]])["narrative_ngrams"], 0)
        guard.exempt([P.NOT_SHOWN[0]])
        self.assertGreater(guard.summary()["exempt_ngrams"], 0)
        self.assertEqual(guard.texts([P.NOT_SHOWN[0]])["narrative_ngrams"], 0)
        self.assertGreater(guard.texts(["Pike was there"])["refused_value"], 0)

    def test_what_left_the_sites_is_read_as_json_strings(self) -> None:
        """JSON's own null is no string, so a refused NULL is not found in '"after":null'; a string is scanned."""
        export = Export(format="comma", encoding="utf-8", columns=("ref", "site", "day", "text", "cat", "who"),
                        rows=(("R-0001", "Ashcombe Yard", "2024-01-01", "a text", "Cat", "NULL"),),
                        rejected={}, blank_lines=0)
        roles = P.roles_from_obj({"roles": {"record_id": "ref", "site": "site", "date": "day", "narrative": "text",
                                            "category": "cat", "forbidden": ["who"]}})
        guard = P.Guard(export, roles, D.load_params())
        self.assertEqual(guard.texts(['{"after":null}'])["refused_value"], 1)
        self.assertFalse(any(guard.sent('{"after":null,"site":"s01"}\n').values()))
        self.assertEqual(guard.sent('{"after":"NULL"}\n')["sent_refused_value"], 1)
        self.assertEqual(guard.sent('{"after":null}\nnot json\n'), {"sent_unread": 1})


# --------------------------------------------------------------------------------------------------- example variants

class Example:
    """The worked example's export (``pilot.start example``), read back as rows, and variants of it written out."""

    def __init__(self, directory: Path, seed: int = 1) -> None:
        self.dir = directory
        self.paths = P.write_example(directory / "example", seed=seed)
        with open(self.paths["export"], newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.header, self.body = rows[0], rows[1:]
        self.col = {name: i for i, name in enumerate(self.header)}
        self.narratives = [r[self.col["what_happened"]] for r in self.body]

    def secrets(self, rows: list[list[str]] | None = None) -> list[str]:
        """Every record id, site value and refused value of four characters or more, and every distinct value of a
        refused column split into words of four letters or more (a surname)."""
        rows = self.body if rows is None else rows
        values: set[str] = set()
        for r in rows:
            for name in ("incident_no", "depot", "reported_by", "staff_no"):
                values.add(r[self.col[name]])
                if name in ("reported_by", "depot"):
                    values.update(r[self.col[name]].split())
        return sorted(v for v in values if len(v) >= 4)

    def write(self, name: str, rows: list[list[str]], header: bool = True, title: str = "") -> Path:
        path = self.dir / f"{name}.csv"
        buf = io.StringIO()
        w = csv.writer(buf)
        if header:
            w.writerow(self.header)
        w.writerows(rows)
        path.write_text(title + buf.getvalue(), encoding="utf-8")
        return path

    def with_value(self, column: str, value: str, every: int, offset: int = 0) -> list[list[str]]:
        i = self.col[column]
        return [r[:i] + [value] + r[i + 1:] if k % every == offset else list(r) for k, r in enumerate(self.body)]

    def run(self, name: str, export: Path, *extra: str) -> dict[str, Any]:
        out = self.dir / f"out-{name}"
        code, stdout, stderr = run_main(["run", "--export", str(export), "--roles", str(self.paths["roles"]),
                                         "--out", str(out), *extra])
        result: dict[str, Any] = {"code": code, "stdout": stdout, "stderr": stderr, "out": out}
        if (out / "pilot.json").is_file():
            result["json_text"] = (out / "pilot.json").read_text(encoding="utf-8")
            result["md"] = (out / "pilot.md").read_text(encoding="utf-8")
            result["doc"] = json.loads(result["json_text"])
        return result


_EXAMPLE: list[Example] = []


def example() -> Example:
    if not _EXAMPLE:
        directory = TMP / "variants"
        directory.mkdir()
        _EXAMPLE.append(Example(directory))
    return _EXAMPLE[0]


class HeaderTests(unittest.TestCase):
    """An export written without its header row, or with a title line above it, stops at the roles check. The error
    must not list the header's names: they are the first record's values, which no guard can check (that record is
    in no row)."""

    def assert_nothing_from_records(self, result: dict[str, Any], ex: Example) -> None:
        self.assertEqual(result["code"], 2)
        text = result["stdout"] + result["stderr"]
        self.assertIn("first non-empty line of the export is its header", text)
        self.assertEqual(token_hits(text, ex.secrets()), [])
        self.assertEqual(ngram_hits([text], ex.narratives, 3), set())
        first = ex.body[0]
        for value in first:
            self.assertNotIn(value, text)
        self.assertFalse(result["out"].exists())

    def test_an_export_with_no_header_row_prints_no_record_value(self) -> None:
        ex = example()
        result = ex.run("no-header", ex.write("no-header", ex.body, header=False))
        self.assert_nothing_from_records(result, ex)
        self.assertIn("has 7 columns", result["stderr"])

    def test_an_export_with_a_title_line_prints_no_record_value(self) -> None:
        ex = example()
        for name, title in (("title-comma", "Incident export, Ashford Hub and others\n"),
                            ("title-plain", "Incident export for Imogen Sallow\n")):
            with self.subTest(title=name):
                result = ex.run(name, ex.write(name, ex.body, title=title))
                self.assert_nothing_from_records(result, ex)
                self.assertNotIn("Ashford", result["stderr"])
                self.assertNotIn("Sallow", result["stderr"])


class PlaceholderTests(unittest.TestCase):
    """NULL, None and Other are what SQL and pandas exports write into optional columns. Values that the report's
    own fixed text holds ('Found: none.', 'Other bucket') are not looked for and are counted; JSON's own null in the
    bytes that left the sites is no string. The report is shown, with no record value in it."""

    def assert_shown_clean(self, result: dict[str, Any], ex: Example, rows: list[list[str]], exempt: int) -> None:
        self.assertEqual(result["code"], 0, result["stderr"])
        self.assertEqual(result["doc"]["status"], "shown")
        self.assertEqual(result["doc"]["guard"]["exempt_values"], exempt)
        self.assertIn(f"Not looked for, because this report's own fixed text holds them: {exempt} of those values",
                      result["md"])
        secrets = [v for v in ex.secrets(rows) if folded(v) not in ("none", "null", "other")]
        for text in (result["md"], result["json_text"], result["stdout"], result["stderr"]):
            self.assertEqual(token_hits(text, secrets), [])
        self.assertEqual(ngram_hits([result["md"], result["json_text"]], ex.narratives, 8), set())

    def test_null_and_none_in_refused_columns_and_none_as_a_site(self) -> None:
        ex = example()
        rows = ex.with_value("reported_by", "NULL", 50)
        i = ex.col["staff_no"]
        rows = [r[:i] + ["None"] + r[i + 1:] if k % 50 == 25 else r for k, r in enumerate(rows)]
        i = ex.col["depot"]
        rows = [r[:i] + ["None"] + r[i + 1:] if k % 50 == 10 else r for k, r in enumerate(rows)]
        result = ex.run("null-none", ex.write("null-none", rows))
        self.assert_shown_clean(result, ex, rows, exempt=1)

    def test_other_as_a_site(self) -> None:
        ex = example()
        rows = ex.with_value("depot", "Other", 50)
        result = ex.run("site-other", ex.write("site-other", rows))
        self.assert_shown_clean(result, ex, rows, exempt=1)
        self.assertEqual(result["doc"]["audit"]["sites_in_export"], 7)

    def test_the_skeleton_holds_no_string_from_records(self) -> None:
        run = shape_run("msha")
        skeleton = P.skeleton(run["doc"])
        self.assertTrue(all(p["label"] == p["id"] == "" and p["first_terms"] == []
                            for p in skeleton["draft"]["predicates"]))
        self.assertTrue(all(r["predicate"] == "" for r in skeleton["review"]["from_cells"]))
        self.assertTrue(all(r["category"] is None for r in skeleton["outcomes"]["by_issue"]))
        joined = folded("\n".join(P.skeleton_texts(run["doc"])))
        for p in run["doc"]["draft"]["predicates"]:
            self.assertNotIn(folded(p["label"]), joined)
        self.assertIn("found: none.", joined)
        self.assertNotIn(run["doc"]["split"]["train_until"], joined)
        self.assertEqual(run["doc"]["guard"]["exempt_values"], 0)


class ReviewPartTests(unittest.TestCase):
    """The X and S part of a review item holds that item's own unexplained X and S alerts: not an R_mf-only week
    (the audit pools records over the three channels) and not an X week an issue explains."""

    KEY = "export_scope:ALL:p1"

    def parts(self, alerts: dict[str, list[dict[str, Any]]], entry: dict[str, Any],
              cells: list[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        watch = P.Watch()
        watch.weeks = [f"2024-W{w:02d}" for w in range(1, 11)]
        watch.window = 2
        watch.alerts = alerts
        watch.cell_weeks = {"X": {self.KEY: cells}, "S": {}}
        doc = {"review": [entry], "outcomes": {"in_scope": 1}}
        pack = SimpleNamespace(predicates={"p1": SimpleNamespace(label="Label one")},
                               entity_types={"lot": SimpleNamespace(label="Lot")})
        return P.review_parts(doc, watch, pack, ("export_scope", "ALL"))

    def alert(self, week: str, sites: list[str], refs: list[str], key: str | None = None) -> dict[str, Any]:
        return {"key": key or self.KEY, "week": week, "sites": sites, "record_refs": refs}

    def test_an_r_mf_only_week_and_an_explained_x_week_stay_out_of_the_x_part(self) -> None:
        x = [self.alert("2024-W03", ["s01", "s02"], ["a", "b"]), self.alert("2024-W07", ["s01", "s03"], ["c", "d"])]
        entry = {"key": self.KEY, "entity_type": "export_scope", "entity_id": "ALL", "predicate": "p1",
                 "channels": ["X", "R_mf"], "alert_weeks": ["2024-W03", "2024-W05"], "sites": ["s01", "s02", "s04"],
                 "records": 5}
        cells = [("2024-W03", "s01"), ("2024-W02", "s02"), ("2024-W07", "s01"), ("2024-W06", "s03")]
        counted, reference, extra = self.parts({"X": x, "S": []}, entry, cells)
        self.assertEqual(reference, [])
        self.assertEqual(extra, [])
        self.assertEqual(counted, [{"predicate": "Label one", "entity": None, "channels": ["X"], "also": ["R_mf"],
                                    "first_week": "2024-W03", "last_week": "2024-W03", "alerts": 1,
                                    "sites": ["s01", "s02"], "records": 2}])

    def test_sites_from_hq_cells_must_match_the_audits(self) -> None:
        x = [self.alert("2024-W03", ["s01", "s02"], ["a"])]
        entry = {"key": self.KEY, "entity_type": "export_scope", "entity_id": "ALL", "predicate": "p1",
                 "channels": ["X"], "alert_weeks": ["2024-W03"], "sites": ["s01", "s02"], "records": 1}
        with self.assertRaisesRegex(P.StartError, "differ between HQ's cells and the audit"):
            self.parts({"X": x, "S": []}, entry, [("2024-W03", "s01")])
        with self.assertRaisesRegex(P.StartError, "do not match the channels"):
            self.parts({"X": [], "S": []}, entry, [])

    def test_an_entity_item_prints_its_entity_id_for_the_last_guard(self) -> None:
        key = "lot:E-LOT-1:p1"
        entry = {"key": key, "entity_type": "lot", "entity_id": "E-LOT-1", "predicate": "p1", "channels": ["R_mf"],
                 "alert_weeks": ["2024-W04"], "sites": ["s02"], "records": 3}
        counted, reference, extra = self.parts({"X": [], "S": []}, entry, [])
        self.assertEqual((counted, extra), ([], ["E-LOT-1"]))
        self.assertEqual(reference[0]["entity"], "Lot E-LOT-1")
        self.assertEqual(reference[0]["records"], 3)


class ReviewRunTests(unittest.TestCase):
    """On example seeds: each X/S item's weeks are among the audit's listed weeks for it, and its records are those of
    its own X and S alerts, never more than the audit's pooled count."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = {}
        for seed, issue in ((4, None), (5, "X-1,2025-02-03,manual handling")):
            ex = Example(TMP / f"review-seed{seed}", seed=seed)
            extra = ["--work", str(ex.dir / "work")]
            if issue is not None:
                path = ex.dir / "issue.csv"
                path.write_text("outcome_id,opened,category\n" + issue + "\n", encoding="utf-8")
                extra += ["--outcomes", str(path)]
            result = ex.run(f"seed{seed}", ex.paths["export"], *extra)
            audit = json.loads((ex.dir / "work" / "audit" / "audit.json").read_text(encoding="utf-8"))
            cls.runs[seed] = (result, {e["predicate"]: e for e in audit["review"]})

    def items(self, seed: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        result, entries = self.runs[seed]
        self.assertEqual(result["code"], 0, result["stderr"])
        slug = {p["label"]: p["id"] for p in result["doc"]["draft"]["predicates"]}
        return [(r, entries[slug[r["predicate"]]]) for r in result["doc"]["review"]["from_cells"]]

    def test_items_hold_their_own_alerts_only(self) -> None:
        for seed in self.runs:
            for item, entry in self.items(seed):
                with self.subTest(seed=seed, item=item["predicate"]):
                    self.assertIn(item["first_week"], entry["alert_weeks"])
                    self.assertIn(item["last_week"], entry["alert_weeks"])
                    self.assertLessEqual(item["records"], entry["records"])

    def test_an_r_mf_only_week_adds_no_records_to_an_x_item(self) -> None:
        """Seed 4, no issue: an item whose audit entry also lists a week only R_mf alerted in counts fewer records
        than the audit's pooled entry."""
        fewer = [(i, e) for i, e in self.items(4) if i["records"] < e["records"]]
        self.assertTrue(fewer)
        for item, entry in fewer:
            weeks = [w for w in entry["alert_weeks"] if item["first_week"] <= w <= item["last_week"]]
            self.assertLess(len(weeks), len(entry["alert_weeks"]))

    def test_an_issue_matched_x_week_is_not_in_the_item(self) -> None:
        """Seed 5 with an issue about manual handling: its later X alert matches the issue and leaves the list; the
        item keeps only the week the audit lists."""
        item, entry = next((i, e) for i, e in self.items(5) if i["predicate"] == "Manual handling")
        self.assertEqual(entry["alert_weeks"], [item["first_week"]])
        self.assertEqual((item["first_week"], item["last_week"], item["alerts"]),
                         (entry["alert_weeks"][0], entry["alert_weeks"][0], 1))


class MessyExportTests(unittest.TestCase):
    """One example export with undated rows, record ids repeated within the training rows, within the held-out rows
    and across the cut, and every third site value upper-cased."""

    @classmethod
    def setUpClass(cls) -> None:
        ex = cls.ex = Example(TMP / "messy")
        d, i, s = ex.col["reported_on"], ex.col["incident_no"], ex.col["depot"]
        rows = [list(r) for r in ex.body]
        for k, r in enumerate(rows):
            if k % 10 == 3:
                r[d] = ""
            elif k % 97 == 5:
                r[d] = "n/a"
            if k % 3 == 0:
                r[s] = r[s].upper()
        n = len(rows)
        for k in range(1, n):
            if k % 41 == 0:
                rows[k][i] = rows[k - 1][i]                 # a repeat of the row before (training or held out)
            elif k > n - n // 5 and k % 13 == 0:
                rows[k][i] = rows[k - n // 2][i]            # a held-out row reusing a training row's id
        cls.rows = rows
        cls.result = ex.run("messy", ex.write("messy", rows), "--work", str(ex.dir / "work"))
        cls.doc = cls.result["doc"]

    def expected(self) -> dict[str, int]:
        """Counted from the rows as written, with the cut the run printed."""
        cut = date.fromisoformat(self.doc["split"]["train_until"])
        d, i = self.ex.col["reported_on"], self.ex.col["incident_no"]

        def day(text: str) -> date | None:
            try:
                return datetime.strptime(text, "%d.%m.%Y").date()
            except ValueError:
                return None
        dated = [(day(r[d]), r[i]) for r in self.rows]
        training = [ref for when, ref in dated if when is not None and when <= cut]
        held = [ref for when, ref in dated if when is not None and when > cut]
        seen: set[str] = set()
        held_dups = 0
        for ref in held:
            held_dups += ref in seen
            seen.add(ref)
        shared_rows = 0
        seen = set()
        for ref in held:
            if ref in seen:
                continue
            seen.add(ref)
            shared_rows += ref in set(training)
        return {"undated": sum(1 for when, _ in dated if when is None),
                "training_repeated": len(training) - len(set(training)),
                "held_out_shared": sum(1 for ref in held if ref in set(training)),
                "duplicate_record_id": held_dups, "in_training": shared_rows, "held": len(held)}

    def test_the_run_is_shown(self) -> None:
        self.assertEqual(self.result["code"], 0, self.result["stderr"])
        self.assertEqual(self.doc["status"], "shown")

    def test_undated_rows_are_not_counted_as_held_out_rows_not_used(self) -> None:
        e = self.expected()
        self.assertGreater(e["undated"], 0)
        self.assertEqual(self.doc["split"]["undated"], e["undated"])
        a = self.doc["audit"]
        self.assertNotIn("bad_date", a["rejected_by_reason"])
        self.assertEqual(a["rejected_by_reason"], {"duplicate_record_id": e["duplicate_record_id"],
                                                   "in_training": e["in_training"]})
        self.assertEqual(a["rows_rejected"], e["duplicate_record_id"] + e["in_training"])
        self.assertEqual(a["records"] + a["rows_rejected"], e["held"])
        line = next(x for x in self.result["md"].splitlines() if x.startswith("Audited: "))
        self.assertIn(f"{a['rows_rejected']} held-out rows not used ({e['duplicate_record_id']} with a record id an "
                      f"earlier held-out row has; {e['in_training']} with a record id a training row has)", line)

    def test_record_ids_repeated_or_shared_across_the_cut_are_counted(self) -> None:
        e = self.expected()
        self.assertGreater(e["training_repeated"], 0)
        self.assertGreater(e["held_out_shared"], 0)
        s = self.doc["split"]
        self.assertEqual((s["training_repeated_ids"], s["held_out_training_ids"]),
                         (e["training_repeated"], e["held_out_shared"]))
        self.assertIn(f"- Record ids: {e['training_repeated']} training rows repeat an earlier training row's id",
                      self.result["md"])

    def test_no_held_out_row_with_a_training_id_is_audited(self) -> None:
        cut = date.fromisoformat(self.doc["split"]["train_until"])
        d, i = self.ex.col["reported_on"], self.ex.col["incident_no"]
        training = {r[i] for r in self.rows if r[d] not in ("", "n/a")
                    and datetime.strptime(r[d], "%d.%m.%Y").date() <= cut}
        audit = json.loads((self.ex.dir / "work" / "audit" / "audit.json").read_text(encoding="utf-8"))
        refs = {ref for e in audit["review"] for ref in e["record_refs"]}
        self.assertEqual(refs & training, set())

    def test_site_values_that_differ_only_in_case_are_one_site(self) -> None:
        a = self.doc["audit"]
        self.assertEqual((a["sites_in_export"], a["site_spellings"]), (6, 12))
        self.assertEqual(len(a["sites"]), 6)
        self.assertIn("6 sites in the export (12 spellings of them", self.result["md"])
        maps = (self.ex.dir / "work" / "sites.csv").read_text(encoding="utf-8").splitlines()[1:]
        labels: dict[str, set[str]] = {}
        for line in maps:
            label, value = line.split(",", 1)
            labels.setdefault(label, set()).add(value.lower())
        self.assertEqual(len(labels), 6)
        self.assertTrue(all(len(v) == 1 for v in labels.values()))


class RunStopTests(unittest.TestCase):
    """Runs that stop before drafting, with the cause named."""

    def test_records_from_fewer_sites_than_the_floor_needs_say_so(self) -> None:
        ex = example()
        for name, sites in (("one-site", ("Ashford Hub",)), ("two-sites", ("Ashford Hub", "ASHFORD HUB", "Calder"))):
            with self.subTest(sites=name):
                i = ex.col["depot"]
                rows = [r[:i] + [sites[k % len(sites)]] + r[i + 1:] for k, r in enumerate(ex.body)]
                result = ex.run(name, ex.write(name, rows))
                self.assertEqual(result["code"], 2)
                need = len({folded(s) for s in sites})
                self.assertIn("the privacy floor needs training records from at least 3 sites", result["stderr"])
                self.assertIn(f"come from {need} (site values that differ only in case", result["stderr"])
                self.assertNotIn("Ashford", result["stderr"])
                self.assertNotIn("DraftError", result["stderr"])

    def test_an_out_that_cannot_be_written_stops_before_the_run(self) -> None:
        ex = example()
        out = ex.paths["outcomes"] / "out"
        code, _, err = run_main(["run", "--export", str(ex.paths["export"]), "--roles", str(ex.paths["roles"]),
                                 "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertIn(f"{ex.paths['outcomes']} is not a directory", err)
        self.assertNotIn("(1) read the export", err)
        self.assertNotIn("Traceback", err)

    def test_a_write_error_after_the_run_is_exit_2(self) -> None:
        ex = example()
        with mock.patch.object(P, "write", side_effect=PermissionError(13, "Permission denied")):
            code, _, err = run_main(["run", "--export", str(ex.paths["export"]), "--roles", str(ex.paths["roles"]),
                                     "--out", str(ex.dir / "unwritable")])
        self.assertEqual(code, 2)
        self.assertIn("error: [Errno 13] Permission denied", err)


class EntitiesTests(unittest.TestCase):
    def test_a_run_with_a_declared_entities_column_counts_the_entity_ids_it_prints(self) -> None:
        directory = TMP / "entities"
        directory.mkdir()
        made = generic_jsonl(directory)
        roles = {"language": "en", "roles": dict(made["roles"]["roles"], entities=["severity"])}
        path = write_roles(directory, roles)
        computed = P.compute(made["export"], path, None, TRAIN_UNTIL, directory / "work")
        self.assertEqual(computed.doc["draft"]["entity_types"], 1)
        doc, _, code = P.present(computed)
        self.assertEqual((doc["status"], code), ("shown", 0))
        printed = sum(1 for part in ("from_cells", "reference_only") for r in doc["review"][part] if r["entity"])
        self.assertEqual(len(computed.extra_strings), printed)
        prefix = D.load_template()["ids.json"]["placeholder_prefix"]
        self.assertEqual(doc["guard"]["strings"], len(R.company_strings(computed.entry, prefix)) + printed)


# --------------------------------------------------------------------------------------------------- determinism

class DeterminismTests(unittest.TestCase):
    def test_two_runs_give_the_same_files(self) -> None:
        run = shape_run("generic")
        outs = []
        for k in range(2):
            out = run["dir"] / f"again-{k}"
            code, _, err = run_main(["run", "--export", str(run["export"]), "--roles", str(run["roles_path"]),
                                     "--out", str(out), "--train-until", TRAIN_UNTIL])
            self.assertEqual(code, 0, err)
            outs.append(out)
        for name in ("pilot.json", "pilot.md"):
            self.assertEqual((outs[0] / name).read_bytes(), (outs[1] / name).read_bytes())
        self.assertEqual((outs[0] / "pilot.json").read_text(encoding="utf-8"), run["json_text"])


# --------------------------------------------------------------------------------------------------- CLI

class CliTests(unittest.TestCase):
    def test_work_cannot_be_out_or_inside_it(self) -> None:
        run = shape_run("generic")
        out = run["dir"] / "o"
        code, _, err = run_main(["run", "--export", str(run["export"]), "--roles", str(run["roles_path"]),
                                 "--out", str(out), "--work", str(out / "w")])
        self.assertEqual(code, 2)
        self.assertIn("cannot be --out or inside it", err)
        self.assertFalse(out.exists())

    def test_dry_run_touches_nothing(self) -> None:
        out = TMP / "dry"
        code, stdout, _ = run_main(["run", "--export", "missing.csv", "--roles", "missing.json", "--out", str(out),
                                    "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would need: file missing.csv", stdout)
        self.assertIn(f"would write: {out / 'pilot.json'}", stdout)
        self.assertFalse(out.exists())
        code, stdout, _ = run_main(["example", "--out", str(out), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("export.csv", stdout)
        self.assertFalse(out.exists())


# --------------------------------------------------------------------------------------------------- the docs

class WorkedExampleTests(unittest.TestCase):
    """The worked example in PILOT.md: its commands are the ones it shows, and every line of the report it quotes is
    a line of a fresh run's pilot.md."""

    def test_the_quoted_report_lines_are_a_fresh_runs(self) -> None:
        text = PILOT_DOC.read_text(encoding="utf-8")
        head = "## No pack for your field? Draft one from your export"
        self.assertLess(text.index(head), text.index("## What you export"))
        self.assertEqual(text.split("\n## ", 1)[1].split("\n", 1)[0], head[3:])
        section = text.split(head, 1)[1].split("\n## What you export", 1)[0]
        commands = [c for c in re.findall(r"^python -m mycelic\.collective\.pilot\.start .*$", section, re.M)
                    if "example" in c]
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].split()[3:], ["example", "--out", "example"])
        work = TMP / "doc"
        code, _, err = run_main(["example", "--out", str(work / "example")])
        self.assertEqual(code, 0, err)
        args = commands[1].split()[3:]
        args = [a.replace("example/", f"{work / 'example'}/").replace("pilot/", f"{work / 'pilot'}/")
                if not a.startswith("--") else a for a in args]
        args = [str(work / "pilot") if a == "pilot" else a for a in args]
        code, _, err = run_main(args)
        self.assertEqual(code, 0, err)
        md = (work / "pilot" / "pilot.md").read_text(encoding="utf-8")
        quoted = re.findall(r"```text\n(.*?)```", section, re.S)
        self.assertTrue(quoted)
        lines = [line for block in quoted for line in block.splitlines() if line.strip()]
        self.assertGreater(len(lines), 10)
        for line in lines:
            with self.subTest(line=line[:60]):
                self.assertIn(line, md.splitlines())
        roles = json.loads(re.search(r"```json\n(.*?)```", section, re.S).group(1))
        self.assertEqual(roles, P.EXAMPLE_ROLES)


if __name__ == "__main__":
    unittest.main()
