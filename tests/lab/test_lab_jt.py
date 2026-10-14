"""Tests of the term-drafting kind ``jt`` (accuracy test A003, build 1: ``docs/collective/A003/CHOICE-A003.md`` as
amended C1 to C32, "The terms", "The request" and "The build plan"; ``docs/collective/A003/BUILD-A003.md``) and of
step (0), A002's golden outputs (C30).

No NHTSA record is read and no artifact is downloaded: every archive here is ``tests/lab/j4_data.py``'s or
``tests/lab/j3_data.py``'s synthetic one, every value invented. The terms the drafter learns from it are the
synthetic cue words of ``j4_data``; the tests compare them, never print them.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

from lab import dryrun as lab_dryrun
from lab import j1 as lab_j1
from lab import j2labels as lab_j2labels
from lab import jt as lab_jt
from lab import notes
from lab import prereg as lab_prereg
from lab import request as lab_request
from lab import summary as lab_summary
from lab import units as lab_units
from lab import aggregate as lab_aggregate
from lab.goldlabels import NHTSA_MAKES, NHTSA_PACK, NHTSA_WINDOW, build_labels
from lab.goldlabels import nhtsa_export as goldlabels_export
from lab.manifest import load_manifest
from lab.request import RequestError
from mycelic.collective.edge.verify import lexical_judge
from mycelic.collective.experiments.common import code_hash as common_code_hash
from mycelic.collective.jsonio import canonical_dumps
from mycelic.collective.onboard.draft import DEFAULTS, draft_export, load_params, load_template, placeholder, summarise
from mycelic.collective.onboard.exports import parse_export
from mycelic.collective.onboard.roles import roles_from_json
from mycelic.collective.packs.canonical import Canonicaliser, folded
from mycelic.collective.packs.loader import load_pack_dir
from tests.lab import a002_golden, j4_data
from tests.lab.stubs import jt_stub
from tests.lab.helpers import LAB_MANIFEST, ROOT, TEMPLATES, check_sources, kill_mentioning, lab_env, run_plan

D002_SETTINGS = ROOT / "docs" / "collective" / "onboard" / "D002-settings.json"
TEMPLATE = TEMPLATES / "terms-003.json"
STUB = "tests.lab.stubs.jt_stub"
STATES = frozenset(j4_data.STATES)
VEHICLE = "MODEL Y"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def template() -> dict[str, Any]:
    return read_json(TEMPLATE)


def word_hits(text: str, words: set[str]) -> list[str]:
    """The words (each a term: letters and spaces) found in ``text`` as whole words, ignoring case."""
    low = text.lower()
    return sorted(w for w in words if re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", low))


def ref_hits(text: str, refs: set[str]) -> list[str]:
    """The record ids found in ``text`` as whole tokens (never inside a hex digest)."""
    return sorted(r for r in refs if re.search(r"(?<![0-9A-Za-z])" + re.escape(r) + r"(?![0-9A-Za-z])", text))


# --------------------------------------------------------------------------------------------------- shared runs

class _Data:
    """The synthetic archive, its pooled export and one draft of it (in process), made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> type[_Data]:
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-jt-data-"))
            cls.dir = j4_data.write_data(cls.tmp / "data")
            cls.zip = cls.dir / "complaints.zip"
            cls.blob = cls.zip.read_bytes()
            cls.pooled = lab_jt.pooled_export(cls.blob)
            (cls.tmp / "export.csv").write_bytes(cls.pooled.data)
            cls.code = lab_jt.run_draft(cls.tmp / "export.csv", cls.tmp / "draft")
            cls.draft = read_json(cls.tmp / "draft" / lab_jt.DRAFT_RESULT)
            cls.entries = cls.draft["terms"]
            cls.terms = {t for t in lab_jt.entries_terms(cls.entries)}
            cls.refs = {json.loads(line)["record_ref"]
                        for line in lab_jt.drafting_refs_bytes(cls.pooled.export).decode("utf-8").splitlines()}
            cls.spellings = {e["spelling"] for e in cls.entries} | {c for c, _, _ in j4_data.DRAFT_CATEGORIES}
        return cls


def _jt_plan(directory: Path, request: dict[str, Any] | None = None) -> Path:
    """The term request (the template unless ``request``) planned into ``directory/plan``; the plan's path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "terms-003.json"
    path.write_text(json.dumps(request if request is not None else template(), indent=2) + "\n", encoding="utf-8")
    code, _, err = run_plan(["--request", str(path), "--manifest", str(LAB_MANIFEST), "--out", str(directory / "plan")])
    if code != 0:
        raise AssertionError(f"plan failed: {err}")
    return directory / "plan" / "plan.json"


def _prereg(plan: Path, archive: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lab_prereg.main(["--plan", str(plan), "--nhtsa-archive", str(archive)])
    return code, out.getvalue(), err.getvalue()


class _Prereg:
    """Two preregistrations of the term request on the synthetic archive (two plan directories), made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> type[_Prereg]:
        if cls.tmp is None:
            data = _Data.get()
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-jt-prereg-"))
            cls.runs = []
            for name in ("one", "two"):
                plan = _jt_plan(cls.tmp / name)
                code, out, err = _prereg(plan, data.zip)
                cls.runs.append((plan.parent, code, out, err))
            cls.dir, cls.code, cls.stdout, cls.stderr = cls.runs[0]
            cls.jt_dir = cls.dir / lab_jt.JT_DIR
            cls.manifest = read_json(cls.dir / "prereg" / "prereg.json") if cls.code == 0 else None
        return cls


class _Dry:
    """``lab.dryrun`` of ``lab/templates/terms-003.json`` (the request the run copies) with ``--jt-data``, made
    once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> type[_Dry]:
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-jt-dry-"))
            data = j4_data.write_data(cls.tmp / "data")
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(TEMPLATE), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST), "--jt-data", str(data)])
            cls.stdout = stdout.getvalue()
            cls.plan = read_json(cls.out / "plan" / "plan.json")
            cls.report = read_json(cls.out / "report" / "report.json")
            cls.manifest = read_json(cls.out / "plan" / "prereg" / "prereg.json")
        return cls


def tearDownModule() -> None:
    for holder in (_Data, _Prereg, _Dry):
        if holder.tmp is not None:
            kill_mentioning(str(holder.tmp))
            shutil.rmtree(holder.tmp, True)


# --------------------------------------------------------------------------------------------------- settings

class SettingsTests(unittest.TestCase):
    """Items 1 to 4 and C23: the window, the makes, the roles, the columns and the drafter's parameters are code
    constants equal to the rule's sources, not request fields."""

    def test_the_window_is_pinned(self) -> None:
        self.assertEqual(lab_jt.CUTOFF, NHTSA_WINDOW[0])
        self.assertEqual((lab_jt.CUTOFF, lab_jt.CUTOFF_ISO), ("20230101", "2023-01-01"))
        self.assertEqual(lab_jt.WINDOW, ("2020-01-01", "2022-12-31"))
        d002 = read_json(D002_SETTINGS)["arms"]["nhtsa"]
        self.assertEqual(list(lab_jt.WINDOW), d002["train"])
        self.assertEqual(lab_jt.FROM, d002["train"][0])
        self.assertEqual(d002["test"][0], lab_jt.CUTOFF_ISO)
        w = lab_jt.window()
        self.assertEqual((w.first.isoformat(), w.last.isoformat()), lab_jt.WINDOW)

    def test_the_roles_and_columns_are_d002_s(self) -> None:
        d002 = read_json(D002_SETTINGS)["arms"]["nhtsa"]
        self.assertEqual(lab_jt.ROLES, d002["roles"])
        self.assertEqual(list(lab_jt.COLUMNS), d002["columns"])
        self.assertEqual(lab_jt.MAKES, NHTSA_MAKES)
        self.assertEqual(list(lab_jt.MAKES), d002["companies"]["makes"])
        self.assertEqual(list(lab_jt.DOWNLOAD_CODE), d002["download_code"])
        self.assertEqual(lab_jt.FETCH_SCRIPT, d002["download_script"])
        self.assertEqual(lab_jt.PACK, NHTSA_PACK)
        self.assertEqual(lab_jt.PACK, d002["hand_packs"]["pack"])
        r = lab_jt.roles()
        self.assertEqual((r.language, r.record_id, r.site, r.date, r.narrative, r.category, list(r.entities),
                          r.reporter, sorted(r.forbidden)),
                         ("en", "odino", "state", "received", "summary", "components[]", [], None,
                          ["reporter", "vehicle"]))
        self.assertIn("vehicle", lab_jt.ROLES["forbidden"])         # A6: make and model words are refused

    def test_the_drafter_reads_the_onboard_defaults(self) -> None:
        """``load_params()`` with no argument, every call: the draft and the check get the defaults' values."""
        seen: list[tuple[Any, ...]] = []

        def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
            seen.append((args, kwargs))
            return load_params(*args, **kwargs)

        data = _Data.get()
        with tempfile.TemporaryDirectory(prefix="lab-jt-params-") as tmp, \
                mock.patch.object(lab_jt, "load_params", spy), \
                mock.patch.object(lab_jt, "draft_export", wraps=lab_jt.draft_export) as drafted, \
                mock.patch.object(lab_jt, "check_pack", wraps=lab_jt.check_pack) as checked:
            (Path(tmp) / "e.csv").write_bytes(data.pooled.data)
            self.assertEqual(lab_jt.run_draft(Path(tmp) / "e.csv", Path(tmp) / "out"), lab_jt.EXIT_OK)
        self.assertEqual(seen, [((), {})])
        defaults = json.loads(Path(DEFAULTS).read_text(encoding="utf-8"))
        params = drafted.call_args.kwargs["params"]
        self.assertEqual({k: params[k] for k in ("floor_records", "floor_sites", "predicate_min_records",
                                                 "max_terms", "term_min_df", "term_min_p")},
                         {k: defaults[k] for k in ("floor_records", "floor_sites", "predicate_min_records",
                                                   "max_terms", "term_min_df", "term_min_p")})
        # every parameter, not six: the defaults, which are D002's parameters key for key
        self.assertEqual(params, load_params())
        self.assertEqual(params, defaults)
        self.assertEqual(params, read_json(D002_SETTINGS)["params"])
        self.assertIs(checked.call_args.kwargs["params"], params)
        # the roles and the window the draft and the check are handed are the module's, vehicle forbidden (A6)
        roles, window = drafted.call_args.args[1:3]
        self.assertEqual(roles, lab_jt.roles())
        self.assertEqual(sorted(roles.forbidden), ["reporter", "vehicle"])
        self.assertEqual(window, lab_jt.window())
        self.assertEqual((window.first.isoformat(), window.last.isoformat()), ("2020-01-01", "2022-12-31"))
        self.assertEqual(checked.call_args.args[2:4], (roles, window))
        self.assertEqual(lab_jt.data_sha256(),
                         {"defaults": sha(Path(DEFAULTS).read_bytes()),
                          "lang_en": sha((ROOT / "mycelic/collective/onboard/data/lang/en.json").read_bytes())})

    def test_the_hashed_code(self) -> None:
        files = lab_jt.onboard_code_files()
        self.assertTrue(set(lab_jt.DOWNLOAD_CODE) <= set(files))
        self.assertIn("mycelic/collective/onboard/draft.py", files)
        self.assertIn("mycelic/collective/onboard/data/defaults.json", files)
        self.assertFalse([f for f in files if "__pycache__" in f or f.endswith(".pyc")])
        self.assertEqual(lab_jt.CODE_FILES, ("lab/jt.py",))
        self.assertRegex(lab_jt.code_hash(), r"^[0-9a-f]{64}$")
        self.assertNotEqual(lab_jt.code_hash(), lab_jt.onboard_code_hash())
        self.assertEqual(lab_jt.DRAFT_TIMEOUT_S, 900)               # the build's choice (C11), BUILD-A003.md's
        self.assertLess(lab_jt.DRAFT_TIMEOUT_S, 20 * 60)            # inside the prereg step's own limit (C11)

    def test_the_code_hash_is_lab_jt_s(self) -> None:
        """C29: the hash is ``common.code_hash`` over ``lab/jt.py`` itself, so one changed byte of the module changes
        it, and a copy of the same bytes keeps it."""
        self.assertEqual(lab_jt.code_hash(), common_code_hash([ROOT / "lab" / "jt.py"], ROOT))
        source = (ROOT / "lab" / "jt.py").read_bytes()
        with tempfile.TemporaryDirectory(prefix="lab-jt-hash-") as tmp:
            (Path(tmp) / "lab").mkdir()
            (Path(tmp) / "lab" / "jt.py").write_bytes(source)
            with mock.patch.object(lab_jt, "ROOT", Path(tmp)):
                same = lab_jt.code_hash()
            (Path(tmp) / "lab" / "jt.py").write_bytes(source.replace(b"DRAFT_TIMEOUT_S = 900", b"DRAFT_TIMEOUT_S = 901",
                                                                      1))
            with mock.patch.object(lab_jt, "ROOT", Path(tmp)):
                changed = lab_jt.code_hash()
        self.assertEqual(same, lab_jt.code_hash())
        self.assertNotEqual(changed, lab_jt.code_hash())


# --------------------------------------------------------------------------------------------------- the export

class ExportTests(unittest.TestCase):
    """Item 3, C4, C14 and C28 on the synthetic archive."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Data.get()
        cls.export = cls.data.pooled.export

    def rows(self) -> list[dict[str, str]]:
        return [{c: self.export.value(r, c) for c in ("odino", "received", "vehicle", "state")}
                for r in range(len(self.export))]

    def archive_rows(self) -> list[tuple[str, str, str]]:
        """(odino, make, received) of every line of the synthetic archive, read from its bytes without the
        exporter."""
        with zipfile.ZipFile(io.BytesIO(self.data.blob)) as z:
            text = z.read("CMPL.txt").decode("latin-1")
        export = goldlabels_export()
        out = []
        for line in text.splitlines():
            f = line.split("\t")
            out.append((f[export.C_ODINO], f[export.C_MAKE], f[export.C_LDATE]))
        return out

    def test_no_row_on_or_after_the_cutoff(self) -> None:
        """Items 1 and 3: the export's record ids are exactly the archive's complaints of the six makes received
        from 2020-01-01 to 2022-12-31, both days included, computed here from the archive's own lines; the boundary
        rows are in or out by name."""
        received = [row["received"] for row in self.rows()]
        self.assertTrue(received)
        self.assertTrue(all("20200101" <= day < lab_jt.CUTOFF for day in received), (min(received), max(received)))
        odinos = [row["odino"] for row in self.rows()]
        expected = {odino for odino, make, day in self.archive_rows()
                    if make in NHTSA_MAKES and "20200101" <= day <= "20221231"}
        self.assertEqual(set(odinos), expected)
        self.assertEqual(len(odinos), len(expected))
        boundary = j4_data.BOUNDARY_ODINOS
        self.assertIn(boundary["2020-01-01"], odinos)                 # the window's first day is in
        self.assertIn(boundary["2022-12-31"], odinos)                 # and its last
        self.assertNotIn(boundary["2019-12-31"], odinos)              # the day before it is out
        self.assertNotIn(boundary["2023-01-01"], odinos)              # and the cutoff day itself
        self.assertNotIn("20230101", received)
        self.assertNotIn("20191231", received)
        self.assertIn(j4_data.STRADDLING_ODINO, odinos)               # its 2022 row only
        self.assertEqual(odinos.count(j4_data.STRADDLING_ODINO), 1)
        straddling_row = next(row for row in self.rows() if row["odino"] == j4_data.STRADDLING_ODINO)
        self.assertEqual(straddling_row["received"], "20221115")     # not its row of 2023-01-01
        self.assertIn(j4_data.REPEATED_ODINOS[0], odinos)
        self.assertNotIn(j4_data.REPEATED_ODINOS[1], odinos)           # received in 2023: not a drafting row
        # the judged window's synthetic rows (j2_data's, received 2023-05-10) are all left out; of the drafting
        # lines, the two-makes complaint's second row, the two straddling complaints' later rows, the repeated
        # narrative's 2023 complaint, the boundary rows of 2019-12-31 and 2023-01-01 and the other make's row
        self.assertEqual(len(odinos), len(j4_data.drafting_lines()) - 1 - 2 - 1 - 2 - 1)
        self.assertEqual((self.data.pooled.earliest, self.data.pooled.latest), ("2020-01-01", "2022-12-31"))
        self.assertEqual((self.data.pooled.earliest, self.data.pooled.latest),
                         (f"{min(received)[:4]}-{min(received)[4:6]}-{min(received)[6:]}",
                          f"{max(received)[:4]}-{max(received)[4:6]}-{max(received)[6:]}"))
        facts = read_json(self.data.dir / "facts.json")
        self.assertEqual((facts["earliest_received"], facts["latest_received"], facts["straddling"]),
                         ("2020-01-01", "2022-12-31", 2))

    def test_the_parsed_export_holds_every_pooled_row(self) -> None:
        """The drafting files and counts are read from the parsed export: a row the parser rejects, or a row lost
        between the CSV and the parse, refuses the export (``JtError``) instead of leaving the refs short."""
        real = lab_jt.parse_export

        def short(data: bytes) -> Any:
            head, _, rest = data.partition(b"\n")
            first, _, others = rest.partition(b"\n")
            return real(head + b"\n" + others)                       # one row fewer than pooled

        def rejecting(data: bytes) -> Any:
            return real(data + b"1,2\n")                             # a row of the wrong width

        for name, fake in (("short", short), ("rejecting", rejecting)):
            with self.subTest(case=name), mock.patch.object(lab_jt, "parse_export", fake):
                with self.assertRaises(lab_jt.JtError):
                    lab_jt.pooled_export(self.data.blob)
        self.assertEqual(dict(self.export.rejected), {})
        self.assertEqual(len(self.export), sum(self.data.pooled.rows_by_make.values()))

    def test_a_complaint_under_two_makes_is_kept_once(self) -> None:
        odinos = [row["odino"] for row in self.rows()]
        self.assertEqual(len(odinos), len(set(odinos)))
        self.assertEqual(odinos.count(j4_data.TWO_MAKES_ODINO), 1)
        self.assertEqual(self.data.pooled.duplicates, 1)
        self.assertEqual(sum(self.data.pooled.rows_by_make.values()), len(self.export))
        self.assertEqual(list(self.data.pooled.rows_by_make), list(NHTSA_MAKES))
        # the first make in NHTSA_MAKES order keeps it (FORD before JEEP); the second make's count is one less
        complaints = {m: self.data.pooled.counts_by_make[m].get("complaints") for m in NHTSA_MAKES}
        self.assertEqual(self.data.pooled.rows_by_make["FORD"], complaints["FORD"])
        self.assertEqual(self.data.pooled.rows_by_make["JEEP"], complaints["JEEP"] - 1)

    def test_the_export_is_d002_s_seven_columns(self) -> None:
        header = self.data.pooled.data.split(b"\n", 1)[0].decode("utf-8").strip()
        self.assertEqual(header.split(","), list(lab_jt.COLUMNS))
        reporter = [self.export.value(r, "reporter") for r in range(len(self.export))]
        self.assertEqual(reporter, [self.export.value(r, "odino") for r in range(len(self.export))])

    def test_the_drafting_files(self) -> None:
        """C4: the refs file is ``lab.j2labels.excluded_jsonl`` of the drafting rows' ids; the text file holds one
        sha256 per distinct folded narrative and no text; the straddling count is the one complaint of the
        archive with rows on both sides."""
        refs_bytes = lab_jt.drafting_refs_bytes(self.export)
        odinos = [row["odino"] for row in self.rows()]
        self.assertEqual(refs_bytes, lab_j2labels.excluded_jsonl(odinos))
        text_bytes = lab_jt.drafting_text_bytes(self.export)
        lines = [json.loads(line) for line in text_bytes.decode("utf-8").splitlines()]
        self.assertTrue(all(sorted(line) == ["sha256"] and re.fullmatch(r"[0-9a-f]{64}", line["sha256"])
                            for line in lines))
        hashes = [line["sha256"] for line in lines]
        self.assertEqual(hashes, sorted(set(hashes)))
        narratives = {folded(self.export.value(r, "summary")) for r in range(len(self.export))}
        self.assertEqual(set(hashes), {sha(n.encode("utf-8")) for n in narratives if n})
        self.assertIn(sha(folded(j4_data.REPEATED_NARRATIVE).encode("utf-8")), hashes)
        self.assertEqual(word_hits(text_bytes.decode("utf-8"), {"stalled", "tread", "cabin"}), [])
        block = lab_jt.drafting_block(self.data.pooled, refs_bytes, text_bytes)
        self.assertEqual(block, {"rows": len(self.export), "refs_sha256": sha(refs_bytes),
                                 "refs_file_sha256": sha(refs_bytes), "text_file_sha256": sha(text_bytes),
                                 "narratives": len(hashes), "straddling": 2})

    def test_straddling(self) -> None:
        """C4: the drafting rows whose record id has a row of the six makes received on or after the cutoff: the
        two straddling complaints (one of them with its later row on the cutoff day itself), not the complaint whose
        later row is filed under a seventh make; computed here from the archive's lines."""
        self.assertEqual(self.data.pooled.straddling, len(j4_data.STRADDLING_ODINOS))
        odinos = {row["odino"] for row in self.rows()}
        later = {odino for odino, make, day in self.archive_rows() if make in NHTSA_MAKES and day >= "20230101"}
        self.assertEqual(odinos & later, set(j4_data.STRADDLING_ODINOS))
        self.assertIn(j4_data.OTHER_MAKE_ODINO, odinos)               # a drafting row, its later row another make's
        self.assertIn((j4_data.STRADDLING_ODINO, "FORD", "20230101"), self.archive_rows())
        self.assertIn((j4_data.OTHER_MAKE_ODINO, j4_data.OTHER_MAKE, "20230303"), self.archive_rows())
        self.assertNotIn(j4_data.OTHER_MAKE, NHTSA_MAKES)


# --------------------------------------------------------------------------------------------------- the draft

class DraftTests(unittest.TestCase):
    """Items 4 to 7, C18, C21 and C22: the drafter's run, its check, T and its counts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Data.get()
        cls.hand = lab_jt.hand_pack()

    def test_the_check_passes_on_the_synthetic_export(self) -> None:
        self.assertEqual(self.data.code, lab_jt.EXIT_OK)
        check = self.data.draft["check"]
        self.assertTrue(check["passed"])
        self.assertEqual(sorted(check["checks"]), sorted(["loader", "term_floor", "value_floor", "refused_strings",
                                                          "refused_terms", "template", "ngram"]))
        counts = lab_jt.check_counts(check)
        self.assertTrue(counts["passed"])
        for name, block in counts["checks"].items():
            with self.subTest(check=name):
                self.assertTrue(all(isinstance(v, (bool, int, float)) for v in block.values()))
                self.assertIs(block["passed"], True)
        self.assertNotIn("error", counts["checks"]["loader"])         # a string a failing pack might hold

    def test_the_check_reads_the_drafted_pack_against_the_same_export(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-jt-check-") as tmp, \
                mock.patch.object(lab_jt, "check_pack", wraps=lab_jt.check_pack) as checked, \
                mock.patch.object(lab_jt, "draft_export", wraps=lab_jt.draft_export) as drafted:
            (Path(tmp) / "e.csv").write_bytes(self.data.pooled.data)
            lab_jt.run_draft(Path(tmp) / "e.csv", Path(tmp) / "out")
            pack_dir, export, roles, window = checked.call_args.args
            self.assertEqual(Path(pack_dir), Path(tmp) / "out" / "pack")
            self.assertIs(export, drafted.call_args.args[0])
            self.assertEqual((roles, window), drafted.call_args.args[1:3])

    def test_t_has_twenty_six_entries(self) -> None:
        entries = self.data.entries
        spellings = lab_jt.hand_spellings(self.hand)
        self.assertEqual(len(entries), lab_jt.HAND_SPECIFIC)
        self.assertEqual(len(entries), 26)
        self.assertEqual([e["predicate"] for e in entries], sorted(spellings))
        self.assertNotIn("unknown_or_other", spellings)
        for e in entries:
            with self.subTest(predicate=e["predicate"]):
                self.assertEqual(sorted(e), ["corpus_records", "drafted_id", "predicate", "spelling", "terms"])
                self.assertEqual(e["spelling"], spellings[e["predicate"]])
                self.assertEqual(self.hand.codes[self.hand.mapping()["codes"][0]["value_map"][e["spelling"]]]
                                 .predicate, e["predicate"])
                self.assertLessEqual(len(e["terms"]), 50)
                if e["drafted_id"] is None:
                    self.assertEqual((e["terms"], e["corpus_records"]), ([], None))
                self.assertFalse(any(t.startswith("unlearned-") for t in e["terms"]))     # placeholders left out
        reached = sorted(e["predicate"] for e in entries if e["drafted_id"] is not None)
        self.assertEqual(reached, sorted(j4_data.REACHED))
        counts = lab_jt.term_counts(entries)
        sizes = sorted(len(e["terms"]) for e in entries if e["drafted_id"] is not None)
        self.assertEqual(counts, {"hand_predicates": 26, "reached": 8, "empty": 26 - sum(1 for s in sizes if s),
                                  "terms_min": sizes[0], "terms_median": sizes[len(sizes) // 2],
                                  "terms_max": sizes[-1], "terms_total": sum(sizes)})
        self.assertEqual((counts["reached"], counts["empty"], counts["terms_min"]), (8, 19, 0))

    def test_the_placeholder_left_out(self) -> None:
        """Item 6: a reached predicate that learned no term (a specific drafted predicate of at least 50 records,
        whose drafted lexicon is the drafter's placeholder) has the empty list in T, never the placeholder; no term of
        T starts with the template's placeholder prefix."""
        prefix = load_template()["ids.json"]["placeholder_prefix"]
        self.assertGreaterEqual(self.data.draft["counts"]["placeholders"], 1)
        entry = next(e for e in self.data.entries if e["predicate"] == j4_data.PLACEHOLDER_PREDICATE)
        self.assertIsNotNone(entry["drafted_id"])
        self.assertGreaterEqual(entry["corpus_records"], 50)
        self.assertEqual(entry["terms"], [])
        self.assertFalse([t for t in self.data.terms if t.startswith(prefix)])
        vocab = json.loads((self.data.tmp / "draft" / "pack" / "vocabulary.json").read_text(encoding="utf-8"))
        drafted = vocab["predicates"][entry["drafted_id"]]["lexicon"]["en"]
        self.assertEqual(drafted, [placeholder(entry["drafted_id"], load_template()["ids.json"])])   # P_draft's

    def test_the_twin_split(self) -> None:
        """Item 6 and trap 11: the two twins share every cue word; each shared word goes to one twin only, the one
        with more records under it (the larger p(c | t)), and the smaller twin is left with none."""
        big, small = (next(e for e in self.data.entries if e["predicate"] == p) for p in j4_data.TWIN_SPLIT)
        self.assertGreater(big["corpus_records"], small["corpus_records"])
        self.assertTrue(big["terms"])
        self.assertEqual(set(big["terms"]) & set(small["terms"]), set())
        shared = set(j4_data.BRAKE_CUES)
        learned = shared & set(self.data.terms)
        self.assertTrue(learned)
        self.assertEqual(learned, shared & set(big["terms"]))         # every shared word the drafter kept: the larger
        self.assertFalse(shared & set(small["terms"]))
        for e in self.data.entries:
            if e["predicate"] not in j4_data.TWIN_SPLIT:
                self.assertFalse(shared & set(e["terms"]), e["predicate"])

    def test_the_vehicle_words_are_refused(self) -> None:
        """A6: the make words one category's narratives carry are words of the forbidden ``vehicle`` column; the
        refusal removes them (its counts say so) and no term of T holds one. With ``vehicle`` not forbidden, the same
        export would give them to that category: the synthetic data exercises the refusal."""
        makes = {m.lower() for m in NHTSA_MAKES}
        refusal = self.data.draft["counts"]["refusal"]
        self.assertGreaterEqual(refusal["terms"], len(makes))
        self.assertGreaterEqual(refusal["assignable"], len(makes))
        self.assertFalse([t for t in self.data.terms if set(t.split()) & makes])
        export = parse_export(self.data.pooled.data)
        loose = roles_from_json({"language": "en", "roles": {**lab_jt.ROLES, "forbidden": ["reporter"]}})
        d = draft_export(export, loose, lab_jt.window(), "loose", params=load_params())
        self.assertTrue([t for p in d.plan.ids for t in d.learned[p] if set(t.split()) & makes])

    def test_t_is_the_drafted_lexicon_mapped_through_the_hand_value_map(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-jt-pdraft-") as tmp:
            shutil.copytree(self.data.tmp / "draft" / "pack", Path(tmp) / "pack")
            p_draft = load_pack_dir(Path(tmp) / "pack")
        self.assertEqual(p_draft.hashes(), self.data.draft["p_draft"]["hashes"])
        vocab = json.loads((self.data.tmp / "draft" / "pack" / "vocabulary.json").read_text(encoding="utf-8"))
        value_map = p_draft.mapping()["codes"][0]["value_map"]
        for e in self.data.entries:
            if e["drafted_id"] is None:
                continue
            with self.subTest(predicate=e["predicate"]):
                self.assertEqual(p_draft.codes[value_map[e["spelling"]]].predicate, e["drafted_id"])
                lexicon = [t for t in vocab["predicates"][e["drafted_id"]]["lexicon"]["en"]
                           if not t.startswith("unlearned-")]
                self.assertEqual(e["terms"], lexicon)

    def test_the_drafter_s_counts(self) -> None:
        counts = self.data.draft["counts"]
        self.assertEqual(counts["export_rows"], len(self.data.pooled.export))
        self.assertEqual((counts["specific_predicates"], counts["placeholders"]), (9, 1))
        self.assertEqual(sorted(counts["refusal"]), ["assignable", "categories", "rows", "share", "terms",
                                                     "with_minimum", "within_cap"])
        self.assertTrue(all(isinstance(v, (int, float, str)) for v in counts.values() if not isinstance(v, dict)))
        self.assertEqual(word_hits(json.dumps(counts), self.data.terms), [])
        self.assertIsInstance(self.data.draft["peak_rss_kib"], int)
        # the draft's own peak resident memory: it loads the onboard package and the hand pack, far above 10 MB
        self.assertGreater(self.data.draft["peak_rss_kib"], 10000)

    def test_the_counts_are_draft_summarise_s(self) -> None:
        """C22: each count is ``draft.summarise``'s, key for key, on the same export."""
        export = parse_export(self.data.pooled.data)
        d = draft_export(export, lab_jt.roles(), lab_jt.window(), lab_jt.DRAFT_PACK_ID, params=load_params())
        f = summarise(export, d)
        want = {"export_rows": f["export"]["rows"], "date_format": f["dates"]["format"],
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
                "refusal": {k: f["refusal"][k] for k in ("categories", "rows", "share", "with_minimum", "terms",
                                                         "assignable", "within_cap")}}
        self.assertEqual(lab_jt.draft_counts(d), want)
        self.assertEqual(self.data.draft["counts"], want)
        self.assertNotEqual(want["terms_candidates"], want["terms_eligible"])


# --------------------------------------------------------------------------------------------------- P_D

class DraftedPackTests(unittest.TestCase):
    """Item 9 and C21: P_D is the hand pack with the term set as its lexicons; its hashes apart from P_draft's."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Data.get()
        cls.hand_dir = ROOT / NHTSA_PACK
        cls.hand = lab_jt.hand_pack()
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-jt-pd-"))
        cls.pd = lab_jt.drafted_pack(cls.hand_dir, cls.data.entries, cls.tmp / "pd")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, True)

    def test_every_file_but_the_vocabulary_is_the_hand_pack_s(self) -> None:
        hand_files = sorted(p.relative_to(self.hand_dir) for p in self.hand_dir.rglob("*") if p.is_file()
                            and "__pycache__" not in p.parts)
        pd_files = sorted(p.relative_to(self.tmp / "pd") for p in (self.tmp / "pd").rglob("*") if p.is_file())
        self.assertEqual(pd_files, hand_files)
        for rel in hand_files:
            with self.subTest(file=str(rel)):
                same = (self.tmp / "pd" / rel).read_bytes() == (self.hand_dir / rel).read_bytes()
                self.assertEqual(same, rel.as_posix() != "vocabulary.json")

    def test_the_lexicons(self) -> None:
        vocab = json.loads((self.tmp / "pd" / "vocabulary.json").read_text(encoding="utf-8"))
        hand_vocab = json.loads((self.hand_dir / "vocabulary.json").read_text(encoding="utf-8"))
        ids = load_template()["ids.json"]
        for e in self.data.entries:
            with self.subTest(predicate=e["predicate"]):
                want = e["terms"] or [placeholder(e["predicate"], ids)]
                self.assertEqual(vocab["predicates"][e["predicate"]]["lexicon"]["en"], want)
        self.assertEqual(vocab["predicates"]["unknown_or_other"], hand_vocab["predicates"]["unknown_or_other"])
        # the reached predicate that learned no term gets the placeholder of its hand id (C21), as an unreached one
        self.assertEqual(vocab["predicates"][j4_data.PLACEHOLDER_PREDICATE]["lexicon"]["en"],
                         [placeholder(j4_data.PLACEHOLDER_PREDICATE, ids)])
        for p in vocab["predicates"]:
            vocab["predicates"][p]["lexicon"]["en"] = hand_vocab["predicates"][p]["lexicon"]["en"]
        self.assertEqual(vocab, hand_vocab)             # nothing else changed

    def test_the_hashes(self) -> None:
        pd, hand, p_draft = self.pd.hashes(), self.hand.hashes(), self.data.draft["p_draft"]["hashes"]
        self.assertEqual(sorted(pd), sorted(lab_jt.HASH_KEYS))
        self.assertEqual((pd["fixtures_hash"], pd["detector_hash"]), (hand["fixtures_hash"], hand["detector_hash"]))
        self.assertNotEqual(pd["vocabulary_hash"], hand["vocabulary_hash"])
        self.assertNotEqual(pd["config_hash"], hand["config_hash"])
        for key in lab_jt.HASH_KEYS:
            self.assertNotEqual(pd[key], p_draft[key], key)
        self.assertEqual(lab_jt.p_d_hashes(self.data.entries), pd)           # rebuilt in a scratch directory
        changed = [dict(e) for e in self.data.entries]
        first = next(i for i, e in enumerate(changed) if e["terms"])
        changed[first]["terms"] = changed[first]["terms"][1:] + changed[first]["terms"][:1]
        self.assertNotEqual(lab_jt.p_d_hashes(changed)["vocabulary_hash"], pd["vocabulary_hash"])   # order counts

    def test_no_placeholder_folds_equal_to_any_term(self) -> None:
        ids = load_template()["ids.json"]
        placeholders = {folded(placeholder(p, ids)) for p in self.hand.predicates}
        self.assertEqual(len(placeholders), len(self.hand.predicates))
        self.assertEqual(placeholders & {folded(t) for t in self.data.terms}, set())
        hand_vocab = json.loads((self.hand_dir / "vocabulary.json").read_text(encoding="utf-8"))
        hand_terms = {folded(t) for p in hand_vocab["predicates"].values() for lex in p["lexicon"].values()
                      for t in lex}
        self.assertEqual(placeholders & hand_terms, set())
        narratives = " ".join(folded(self.data.pooled.export.value(r, "summary"))
                              for r in range(len(self.data.pooled.export)))
        self.assertEqual([p for p in placeholders if p in narratives], [])

    def test_the_drafted_lexical_judge(self) -> None:
        """Item 9: ``verify.lexical_judge(P_D, Canonicaliser(P_D))`` on the shipped payload confirms a predicate
        from a drafted term the hand lexicon lacks and never from an empty predicate's hand term; the hand judge on
        the same payloads does the opposite; the payload is the hand pack's (the same for both judges)."""
        labels, _ = build_labels("nhtsa", NHTSA_PACK, 40, 1, fetch=lambda: self.data.blob)
        questions = lab_j1.read_questions(lab_j1.build_questions(self.hand, labels, seed=1, parts=2)[0])
        records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels(labels)}
        drafted = lexical_judge(self.pd, Canonicaliser(self.pd))
        hand = lexical_judge(self.hand, Canonicaliser(self.hand))
        hand_vocab = json.loads((self.hand_dir / "vocabulary.json").read_text(encoding="utf-8"))
        by = {e["predicate"]: e for e in self.data.entries}
        seen = {"reached": 0, "empty": 0, "reached_hand_label": 0}
        for q in questions:
            entry = by.get(q["predicate"])
            if entry is None:
                continue
            hand_word = hand_vocab["predicates"][q["predicate"]]["lexicon"]["en"][0]
            cases = [("reached", entry["terms"][0])] if entry["terms"] else [("empty", hand_word)]
            if entry["terms"] and not any(folded(t) in folded(hand_word) for t in entry["terms"]):
                # a reached predicate whose drafted list lacks the hand label: the hand label alone is refuted
                cases.append(("reached_hand_label", hand_word))
            for kind, word in cases:
                record = dict(records[q["record_ref"]], narrative=f"The owner reported {word} again on the highway.")
                payload = lab_j1.payload(self.hand, q, record)
                self.assertEqual(lab_j1.payload(self.pd, q, record), payload)
                got, base = drafted(payload), hand(payload)
                with self.subTest(question=q["question_id"], kind=kind):
                    self.assertEqual(got["mentions_entity"], "yes")
                    self.assertEqual((got["describes_predicate"], base["describes_predicate"]),
                                     ("yes", "no") if kind == "reached" else ("no", "yes"))
                seen[kind] += 1
        self.assertGreater(seen["reached"], 5)
        self.assertGreater(seen["empty"], 5)
        self.assertGreater(seen["reached_hand_label"], 5)


# --------------------------------------------------------------------------------------------------- prereg

class PreregTests(unittest.TestCase):
    """Items 7, 10 and 11, C11, C13, C22 and C29: the plan job's term step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.p = _Prereg.get()
        cls.data = _Data.get()

    def setUp(self) -> None:
        self.assertEqual(self.p.code, 0, self.p.stderr + self.p.stdout)

    def test_the_files(self) -> None:
        names = sorted(p.relative_to(self.p.jt_dir).as_posix() for p in self.p.jt_dir.rglob("*")
                       if p.is_file() and not p.relative_to(self.p.jt_dir).as_posix().startswith("pack/"))
        self.assertEqual(names, ["check.json", "drafting_refs.jsonl", "drafting_text.jsonl", "prereg.json",
                                 "terms.json"])
        pack = sorted(p.relative_to(self.p.jt_dir / "pack").as_posix()
                      for p in (self.p.jt_dir / "pack").rglob("*") if p.is_file())
        self.assertEqual(pack, sorted(self.data.draft["p_draft"]["files"]))
        self.assertEqual((self.p.jt_dir / "drafting_refs.jsonl").read_bytes(),
                         lab_jt.drafting_refs_bytes(self.data.pooled.export))
        self.assertEqual((self.p.jt_dir / "drafting_text.jsonl").read_bytes(),
                         lab_jt.drafting_text_bytes(self.data.pooled.export))
        self.assertFalse([p for p in self.p.dir.rglob("*") if p.name.endswith(".csv")])     # never the export
        self.assertFalse([p for p in self.p.dir.parent.iterdir() if p.name.startswith("lab-jt-")])
        files = self.p.manifest["files"]
        for rel in ("prereg/jt/terms.json", "prereg/jt/prereg.json", "prereg/jt/drafting_refs.jsonl"):
            self.assertEqual(files[rel]["sha256"], sha((self.p.dir / rel).read_bytes()), rel)

    def test_check_json_holds_counts_by_check(self) -> None:
        """Item 5: the check's result in the artifact is counts and booleans by check, never a string (the check's
        own document names the pack and could hold the loader's error text); it equals ``terms.json``'s ``check``."""
        check = read_json(self.p.jt_dir / "check.json")
        self.assertEqual(check, lab_jt.check_counts(self.data.draft["check"]))
        self.assertEqual(check, read_json(self.p.dir / lab_jt.TERMS_FILE)["check"])
        self.assertEqual(sorted(check), ["checks", "passed"])

        def strings(node: Any) -> list[str]:
            if isinstance(node, dict):
                return [s for v in node.values() for s in strings(v)]
            if isinstance(node, list):
                return [s for v in node for s in strings(v)]
            return [node] if isinstance(node, str) else []

        self.assertEqual(strings(check), [])
        self.assertTrue(check["passed"])

    def test_the_peak_memory_is_the_draft_s(self) -> None:
        """C11: the draft subprocess's own peak resident memory reaches the manifest and the ``jt step:`` line, the
        same number in both."""
        peak = self.p.manifest["jt"]["step"]["peak_rss_kib"]
        self.assertIsInstance(peak, int)
        self.assertGreater(peak, 10000)
        (line,) = [line for line in self.p.stdout.splitlines() if line.startswith("jt step:")]
        self.assertTrue(line.endswith(f" peak_rss_kib {peak}"), line)
        self.assertRegex(line, r"^jt step: wall_s [0-9]+\.[0-9] draft_wall_s [0-9]+\.[0-9] draft_timeout_s 900 "
                               r"peak_rss_kib [0-9]+$")

    def test_terms_json(self) -> None:
        raw = (self.p.jt_dir / "terms.json").read_bytes()
        doc = json.loads(raw)
        self.assertEqual(raw, (canonical_dumps(doc) + "\n").encode("utf-8"))
        self.assertEqual(sorted(doc), ["archive", "check", "code", "cutoff", "draft", "drafting", "export", "hand",
                                       "kind", "language", "makes", "p_d", "p_draft", "roles", "schema_version",
                                       "term_counts", "terms", "window"])
        self.assertEqual((doc["kind"], doc["schema_version"], doc["cutoff"], doc["window"], doc["makes"]),
                         ("lab_jt_terms", 1, "2023-01-01", ["2020-01-01", "2022-12-31"], list(NHTSA_MAKES)))
        self.assertEqual(doc["archive"], {"sha256": sha(self.data.blob), "bytes": len(self.data.blob)})
        self.assertEqual((doc["export"]["cross_make_duplicates"], doc["drafting"]["straddling"]), (1, 2))
        self.assertEqual(doc["terms"], self.data.entries)
        self.assertEqual(doc["p_d"]["hashes"], lab_jt.p_d_hashes(doc["terms"]))
        self.assertEqual(doc["hand"]["hashes"], lab_jt.hand_pack().hashes())
        self.assertEqual(doc["p_draft"]["hashes"], self.data.draft["p_draft"]["hashes"])
        self.assertEqual(doc["code"]["jt_code_hash"], lab_jt.code_hash())
        self.assertEqual(doc["code"]["onboard_code_hash"], lab_jt.onboard_code_hash())
        self.assertEqual(doc["code"]["data_sha256"], lab_jt.data_sha256())
        self.assertEqual(doc["check"], lab_jt.check_counts(self.data.draft["check"]))
        self.assertEqual(doc["draft"], self.data.draft["counts"])
        self.assertNotIn("peak_rss_kib", json.dumps(doc))       # the step's machine figures stay out of the term set

    def test_the_sha256_is_stable(self) -> None:
        (one, code_one, _, _), (two, code_two, _, _) = self.p.runs
        self.assertEqual((code_one, code_two), (0, 0))
        for rel in ("terms.json", "drafting_refs.jsonl", "drafting_text.jsonl", "check.json"):
            with self.subTest(file=rel):
                self.assertEqual((one / lab_jt.JT_DIR / rel).read_bytes(), (two / lab_jt.JT_DIR / rel).read_bytes())
        a = read_json(one / lab_jt.PREREG_FILE)
        b = read_json(two / lab_jt.PREREG_FILE)
        self.assertEqual(a["terms_sha256"], b["terms_sha256"])
        self.assertEqual(a["terms_sha256"], sha((one / lab_jt.TERMS_FILE).read_bytes()))

    def test_the_preregistration_document(self) -> None:
        doc = read_json(self.p.dir / lab_jt.PREREG_FILE)
        terms = read_json(self.p.dir / lab_jt.TERMS_FILE)
        self.assertEqual(doc["kind"], "lab_jt_prereg")
        self.assertEqual(doc["terms_sha256"], sha((self.p.dir / lab_jt.TERMS_FILE).read_bytes()))
        self.assertEqual((doc["p_draft"], doc["p_d"], doc["hand"]["hashes"]),
                         (terms["p_draft"]["hashes"], terms["p_d"]["hashes"], terms["hand"]["hashes"]))
        self.assertNotEqual(doc["p_draft"], doc["p_d"])
        self.assertEqual(doc["jt_code_hash"], terms["code"]["jt_code_hash"])
        block = self.p.manifest["jt"]
        self.assertEqual(block["terms_sha256"], doc["terms_sha256"])
        self.assertEqual((block["prereg"], block["terms"]), (lab_jt.PREREG_FILE, lab_jt.TERMS_FILE))
        self.assertEqual(sorted(block["step"]), ["draft_timeout_s", "draft_wall_s", "peak_rss_kib", "wall_s"])
        self.assertEqual(block["step"]["draft_timeout_s"], lab_jt.DRAFT_TIMEOUT_S)
        for key in ("cutoff", "window", "makes", "archive", "export", "drafting", "draft", "term_counts", "check",
                    "code"):
            self.assertEqual(block[key], terms[key], key)
        self.assertEqual(self.p.manifest["files"][lab_jt.PREREG_FILE]["sha256"],
                         sha((self.p.dir / lab_jt.PREREG_FILE).read_bytes()))

    def test_the_fixed_lines_hold_no_term(self) -> None:
        lines = self.p.stdout.splitlines()
        starts = [line.split(":", 1)[0] for line in lines]
        self.assertEqual(starts, ["jt export", "jt drafting", "jt draft", "jt check", "jt terms", "jt step",
                                  "prereg"])
        self.assertEqual(lines[-1], "prereg: e1 no x1 no e2 no j1 no jt yes")
        terms_sha = sha((self.p.dir / lab_jt.TERMS_FILE).read_bytes())
        self.assertTrue(lines[4].endswith(f" terms_sha256 {terms_sha}"), lines[4])
        self.assertIn("jt check: passed yes ", lines[3])
        self.assertIn(" cross_make_duplicates 1 ", lines[0])
        self.assertTrue(lines[1].endswith(" straddling 2"))
        text = self.p.stdout + self.p.stderr
        self.assertEqual(word_hits(text, self.data.terms), [])
        self.assertEqual([s for s in self.data.spellings if s in text], [])
        self.assertEqual(ref_hits(text, self.data.refs), [])
        self.assertEqual([s for s in STATES if re.search(rf"(?<![A-Za-z]){s}(?![A-Za-z])", text)], [])
        self.assertNotIn(VEHICLE, text)
        self.assertNotIn(j4_data.REPEATED_NARRATIVE.split()[1], text.lower().split())
        logs = "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in (self.p.dir / "prereg-logs").iterdir())
        self.assertEqual(word_hits(logs, self.data.terms), [])

    def test_the_console_lines_are_a_function_of_the_term_set(self) -> None:
        terms = read_json(self.p.dir / lab_jt.TERMS_FILE)
        sha_ = sha((self.p.dir / lab_jt.TERMS_FILE).read_bytes())
        self.assertEqual(lab_jt.console_lines(terms, sha_), self.p.stdout.splitlines()[:5])


class StepFailureTests(unittest.TestCase):
    """C11 and item 5: a draft that does not finish, is killed, runs out of memory, is refused or writes no result
    refuses the plan with ``JT_DRAFT_FAILED``; a check that fails with ``JT_FLOOR_FAILED``; nothing is written under
    ``prereg/jt/`` and the scratch directory is removed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Data.get()

    def run_with(self, command: str = "draft", timeout_s: int | None = None, *, archive: Path | None = None,
                 patches: tuple[Any, ...] = ()) -> tuple[Path, int, str, str]:
        """The term request planned and preregistered with the draft step replaced by the stub's ``command`` (the
        real draft for ``draft``), the draft's timeout ``timeout_s`` and the archive ``archive``."""
        tmp = Path(tempfile.mkdtemp(prefix=f"lab-jt-fail-{command}-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(kill_mentioning, str(tmp))
        plan = _jt_plan(tmp / "run")
        with contextlib.ExitStack() as stack:
            if command != "draft":
                stack.enter_context(mock.patch.object(lab_prereg, "JT_MODULE", STUB))
                stack.enter_context(mock.patch.object(lab_prereg, "JT_DRAFT_COMMAND", command))
            if timeout_s is not None:
                stack.enter_context(mock.patch.object(lab_jt, "DRAFT_TIMEOUT_S", timeout_s))
            for patch in patches:
                stack.enter_context(patch)
            code, out, err = _prereg(plan, archive if archive is not None else self.data.zip)
        return plan.parent, code, out, err

    def assert_refused(self, root: Path, code: int, problem: str, out: str | None = None) -> None:
        self.assertEqual(code, lab_prereg.EXIT_USAGE)
        error = read_json(root / "plan-error.json")
        self.assertEqual((error["source"], error["path"], error["problem"]), ("prereg", "$.experiments.jt", problem))
        self.assertFalse((root / lab_jt.JT_DIR).exists())
        self.assertFalse((root / "prereg" / "prereg.json").exists())
        self.assertFalse([p for p in root.parent.iterdir() if p.name.startswith("lab-jt-")])
        if out is not None:
            # the stop's fixed line: wall seconds, the draft's, the timeout and the children's peak memory
            (line,) = [line for line in out.splitlines() if line.startswith("jt step:")]
            self.assertRegex(line, r"^jt step: stopped wall_s [0-9]+\.[0-9] draft_wall_s (none|[0-9]+\.[0-9]) "
                                   r"draft_timeout_s [0-9]+ children_peak_rss_kib [0-9]+$")
            self.assertNotIn("jt terms:", out)

    def test_a_timeout(self) -> None:
        """C11: the draft step runs under the step runner with :data:`lab.jt.DRAFT_TIMEOUT_S` (here three seconds);
        the stub sleeps an hour and would then write a passing result, so only the timeout refuses it, within a
        minute."""
        calls: list[tuple[str, Any]] = []
        real = lab_prereg._Steps.run

        def spy(steps: Any, step: str, *args: Any, **kwargs: Any) -> int:
            calls.append((step, kwargs.get("timeout_s")))
            try:
                return real(steps, step, *args, **kwargs)
            except lab_prereg._StepFailed as failed:
                calls.append(("raised", failed.problem))
                raise

        t0 = time.monotonic()
        root, code, out, _ = self.run_with("sleep", timeout_s=3,
                                           patches=(mock.patch.object(lab_prereg._Steps, "run", spy),))
        elapsed = time.monotonic() - t0
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
        self.assertEqual(calls, [("jt-draft", 3), ("raised", "jt-draft did not finish in time")])
        self.assertLess(elapsed, 60)
        self.assertIn("draft_timeout_s 3 ", out)
        self.assertGreater(jt_stub.SLEEP_S, 20 * 60)               # longer than the step's own limit

    def test_the_real_timeout_is_handed_to_the_step(self) -> None:
        """The real draft's step gets ``timeout_s`` equal to :data:`lab.jt.DRAFT_TIMEOUT_S`, not the runner's
        default."""
        calls: list[tuple[str, Any]] = []
        real = lab_prereg._Steps.run

        def spy(steps: Any, step: str, *args: Any, **kwargs: Any) -> int:
            calls.append((step, kwargs.get("timeout_s")))
            return real(steps, step, *args, **kwargs)

        root, code, _, err = self.run_with(patches=(mock.patch.object(lab_prereg._Steps, "run", spy),))
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [("jt-draft", lab_jt.DRAFT_TIMEOUT_S)])
        self.assertNotEqual(lab_jt.DRAFT_TIMEOUT_S, lab_prereg.STEP_TIMEOUT_S)

    def test_a_kill_by_the_out_of_memory_killer(self) -> None:
        root, code, out, _ = self.run_with("kill")
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)

    def test_a_memory_error(self) -> None:
        root, code, out, _ = self.run_with("memory")
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
        self.assertIn("error: out of memory", (root / "prereg-logs" / "jt-draft.stderr.log").read_text())

    def test_a_refused_export(self) -> None:
        root, code, out, _ = self.run_with("refuse")
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
        self.assertIn("the drafter refused the export", (root / "prereg-logs" / "jt-draft.stderr.log").read_text())

    def test_a_crash_in_the_draft(self) -> None:
        """Any other error in the draft exits :data:`lab.jt.EXIT_CRASH` with its class name only; an uncaught one
        before the draft exits 1, Python's own code, which is no code of the check: both are ``JT_DRAFT_FAILED``."""
        root, code, out, _ = self.run_with("crash")
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
        log = (root / "prereg-logs" / "jt-draft.stderr.log").read_text()
        self.assertIn("error: the draft stopped (RuntimeError)", log)
        self.assertNotIn("a fault inside the draft", log)             # the message is never written
        root, code, out, _ = self.run_with("uncaught")
        self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
        self.assertIn("RuntimeError", (root / "prereg-logs" / "jt-draft.stderr.log").read_text())

    def test_no_result_or_a_wrong_result(self) -> None:
        """No result, a result that is not the draft's, the check's exit code with no result, and exit 0 with a
        result whose check did not pass: each is ``JT_DRAFT_FAILED``, never a traceback or a written term set."""
        for command in ("nothing", "garbage", "floorless", "unchecked"):
            with self.subTest(command=command):
                root, code, out, _ = self.run_with(command)
                self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)

    def test_a_failed_check(self) -> None:
        root, code, out, _ = self.run_with("floor")
        self.assert_refused(root, code, notes.JT_FLOOR_FAILED, out)
        (line,) = [line for line in out.splitlines() if line.startswith("jt check:")]
        self.assertTrue(line.startswith("jt check: passed no "), line)
        self.assertIn("ngram=failed", line)
        self.assertIn("term_floor=passed", line)
        self.assertTrue(line.endswith(" ngram_failures 1"), line)      # each failing check's count, numbers only
        self.assertNotIn("term_floor_failures", line)
        self.assertEqual(word_hits(out, self.data.terms), [])

    def test_an_archive_that_cannot_be_fetched_or_read(self) -> None:
        """A download that fails, a file that is not a zip and a truncated zip stop the plan with
        ``JT_FETCH_FAILED``, before any draft, with no traceback."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-jt-archives-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "text.zip").write_bytes(b"not a zip archive\n")
        (tmp / "short.zip").write_bytes(self.data.blob[: len(self.data.blob) // 2])
        for name in ("absent.zip", "text.zip", "short.zip"):
            with self.subTest(archive=name):
                root, code, out, err = self.run_with(archive=tmp / name)
                self.assert_refused(root, code, notes.JT_FETCH_FAILED, out)
                self.assertIn("draft_wall_s none ", out)
                self.assertNotIn("Traceback", err)
                self.assertFalse((root / "prereg-logs" / "jt-draft.stdout.log").exists())

    def test_a_failure_in_the_plan_job_s_own_process(self) -> None:
        """An export the parser does not read back whole, and a P_D rebuild the loader refuses, stop the plan with
        ``JT_DRAFT_FAILED`` and no traceback."""
        real_parse = lab_jt.parse_export
        cases = (("export", mock.patch.object(lab_jt, "parse_export", lambda data: real_parse(data + b"1,2\n"))),
                 ("p_d", mock.patch.object(lab_jt, "p_d_hashes",
                                           side_effect=lab_jt.PackError("vocabulary.json", "$", "refused"))))
        for name, patch in cases:
            with self.subTest(case=name):
                root, code, out, err = self.run_with(patches=(patch,))
                self.assert_refused(root, code, notes.JT_DRAFT_FAILED, out)
                self.assertNotIn("Traceback", err)

    def test_the_draft_command_s_exit_codes(self) -> None:
        """The real ``lab.jt draft``: a usage problem is 2, a refused export 3, a ``MemoryError`` 4, any other error
        6 (its class name, never its message); a failed check is 5; no code is 1, Python's own for an uncaught
        exception."""
        with tempfile.TemporaryDirectory(prefix="lab-jt-exits-") as tmp:
            (Path(tmp) / "e.csv").write_bytes(self.data.pooled.data)
            (Path(tmp) / "full").mkdir()
            (Path(tmp) / "full" / "x").write_text("x")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(lab_jt.main(["draft", "--export", f"{tmp}/e.csv", "--out", f"{tmp}/full"]),
                                 lab_jt.EXIT_USAGE)
                with mock.patch.object(lab_jt, "draft_export", side_effect=lab_jt.DraftError("x")):
                    self.assertEqual(lab_jt.main(["draft", "--export", f"{tmp}/e.csv", "--out", f"{tmp}/a"]),
                                     lab_jt.EXIT_REFUSED)
                with mock.patch.object(lab_jt, "draft_export", side_effect=MemoryError()):
                    self.assertEqual(lab_jt.main(["draft", "--export", f"{tmp}/e.csv", "--out", f"{tmp}/b"]),
                                     lab_jt.EXIT_MEMORY)
                with mock.patch.object(lab_jt, "term_set", side_effect=lab_jt.JtError("a secret spelling")):
                    self.assertEqual(lab_jt.main(["draft", "--export", f"{tmp}/e.csv", "--out", f"{tmp}/c"]),
                                     lab_jt.EXIT_CRASH)
                self.assertFalse((Path(tmp) / "c" / lab_jt.DRAFT_RESULT).exists())
                real = lab_jt.check_pack

                def failing(*a: Any, **k: Any) -> dict[str, Any]:
                    result = real(*a, **k)
                    result["passed"] = False
                    return result

                with mock.patch.object(lab_jt, "check_pack", failing):
                    self.assertEqual(lab_jt.main(["draft", "--export", f"{tmp}/e.csv", "--out", f"{tmp}/d"]),
                                     lab_jt.EXIT_FLOOR)
            self.assertIn("error: the draft stopped (JtError)", err.getvalue())
            self.assertNotIn("a secret spelling", err.getvalue())
        self.assertEqual((lab_jt.EXIT_OK, lab_jt.EXIT_USAGE, lab_jt.EXIT_REFUSED, lab_jt.EXIT_MEMORY,
                          lab_jt.EXIT_FLOOR, lab_jt.EXIT_CRASH), (0, 2, 3, 4, 5, 6))
        self.assertNotIn(1, (lab_jt.EXIT_OK, lab_jt.EXIT_USAGE, lab_jt.EXIT_REFUSED, lab_jt.EXIT_MEMORY,
                             lab_jt.EXIT_FLOOR, lab_jt.EXIT_CRASH))


# --------------------------------------------------------------------------------------------------- the unit

class VerifyTests(unittest.TestCase):
    """Item 10: ``python -m lab.jt verify`` against a copy of the preregistration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.p = _Prereg.get()

    def setUp(self) -> None:
        self.assertEqual(self.p.code, 0, self.p.stderr)
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-jt-verify-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(self.p.dir, self.tmp / "plan")
        self.prereg = self.tmp / "plan" / lab_jt.PREREG_FILE

    def cli(self) -> tuple[int, str, dict[str, Any]]:
        proc = subprocess.run([sys.executable, "-m", "lab.jt", "verify", "--prereg", str(self.prereg),
                               "--run-id", "jt-test", "--runs-dir", str(self.tmp / "runs")],
                              cwd=ROOT, env=lab_env(), capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout, read_json(self.tmp / "runs" / "jt" / "jt-test" / "verify.json")

    def test_it_verifies(self) -> None:
        code, out, result = self.cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), ["jt verify: verified yes terms_sha256=yes jt_code_hash=yes "
                                            "hand_hashes=yes drafting_files=yes p_d_hashes=yes"])
        self.assertEqual(sorted(result), ["checks", "jt_code_hash", "kind", "p_d", "schema_version", "term_counts",
                                          "terms_sha256", "verified"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["terms_sha256"], self.p.manifest["jt"]["terms_sha256"])
        self.assertEqual(result["p_d"], self.p.manifest["jt"]["p_d"])
        self.assertEqual(word_hits(json.dumps(result), _Data.get().terms), [])

    def test_a_changed_term_set(self) -> None:
        terms = self.prereg.parent / "terms.json"
        terms.write_bytes(terms.read_bytes().replace(b'"kind":', b'"kind" :', 1))
        code, out, result = self.cli()
        self.assertEqual(code, 1)
        self.assertFalse(result["verified"])
        self.assertFalse(result["checks"]["terms_sha256"])
        self.assertIn("jt verify: verified no ", out)

    def test_a_changed_term_set_with_its_sha_pinned_again(self) -> None:
        """A term set rebuilt to other terms and pinned again still fails: P_D's rebuilt hashes differ from the
        preregistered ones."""
        terms_path = self.prereg.parent / "terms.json"
        doc = read_json(terms_path)
        first = next(e for e in doc["terms"] if e["terms"])
        first["terms"] = first["terms"][1:]
        data = lab_jt.document_bytes(doc)
        terms_path.write_bytes(data)
        pre = read_json(self.prereg)
        pre["terms_sha256"] = sha(data)
        self.prereg.write_text(json.dumps(pre), encoding="utf-8")
        code, _, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"], {"terms_sha256": True, "jt_code_hash": True, "hand_hashes": True,
                                            "drafting_files": True, "p_d_hashes": False})

    def test_a_changed_drafting_file(self) -> None:
        refs = self.prereg.parent / "drafting_refs.jsonl"
        refs.write_bytes(refs.read_bytes() + b'{"record_ref":"1"}\n')
        code, _, result = self.cli()
        self.assertEqual(code, 1)
        self.assertFalse(result["checks"]["drafting_files"])
        self.assertTrue(result["checks"]["terms_sha256"])

    def test_a_changed_drafting_text_file(self) -> None:
        text = self.prereg.parent / "drafting_text.jsonl"
        text.write_bytes(text.read_bytes() + b'{"sha256":"' + b"0" * 64 + b'"}\n')
        code, _, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"], {"terms_sha256": True, "jt_code_hash": True, "hand_hashes": True,
                                            "drafting_files": False, "p_d_hashes": True})

    def pin_again(self, change: Any) -> None:
        """``terms.json`` changed by ``change`` and its new sha256 pinned in the preregistration again."""
        terms_path = self.prereg.parent / "terms.json"
        doc = read_json(terms_path)
        change(doc)
        data = lab_jt.document_bytes(doc)
        terms_path.write_bytes(data)
        pre = read_json(self.prereg)
        pre["terms_sha256"] = sha(data)
        self.prereg.write_text(json.dumps(pre), encoding="utf-8")

    def test_other_hand_pack_hashes(self) -> None:
        """The hand pack's hashes in ``terms.json`` (pinned again) or in the preregistration, unlike the hand pack's
        own: only ``hand_hashes`` fails."""
        self.pin_again(lambda doc: doc["hand"]["hashes"].update(config_hash="0" * 64))
        code, _, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"], {"terms_sha256": True, "jt_code_hash": True, "hand_hashes": False,
                                            "drafting_files": True, "p_d_hashes": True})
        shutil.rmtree(self.tmp / "plan")
        shutil.rmtree(self.tmp / "runs", True)
        shutil.copytree(self.p.dir, self.tmp / "plan")
        pre = read_json(self.prereg)
        pre["hand"]["hashes"]["vocabulary_hash"] = "0" * 64
        self.prereg.write_text(json.dumps(pre), encoding="utf-8")
        result = lab_jt.verify(self.prereg)
        self.assertEqual(result["checks"], {"terms_sha256": True, "jt_code_hash": True, "hand_hashes": False,
                                            "drafting_files": True, "p_d_hashes": True})

    def test_other_p_d_hashes_in_the_preregistration(self) -> None:
        """P_D's hashes in the preregistration unlike ``terms.json``'s and the rebuilt ones: ``p_d_hashes`` fails."""
        pre = read_json(self.prereg)
        pre["p_d"]["vocabulary_hash"] = "0" * 64
        self.prereg.write_text(json.dumps(pre), encoding="utf-8")
        result = lab_jt.verify(self.prereg)
        self.assertFalse(result["verified"])
        self.assertEqual(result["checks"], {"terms_sha256": True, "jt_code_hash": True, "hand_hashes": True,
                                            "drafting_files": True, "p_d_hashes": False})

    def test_another_code_hash(self) -> None:
        with mock.patch.object(lab_jt, "code_hash", lambda: "1" * 64):
            result = lab_jt.verify(self.prereg)
        self.assertFalse(result["verified"])
        self.assertFalse(result["checks"]["jt_code_hash"])
        self.assertTrue(result["checks"]["p_d_hashes"])

    def test_no_preregistration(self) -> None:
        self.prereg.unlink()
        code, out, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"], dict.fromkeys(lab_jt.VERIFY_CHECKS, False))
        self.assertIn("verified no", out)

    def test_the_adapter(self) -> None:
        plan = read_json(self.tmp / "plan" / "plan.json")
        (unit,) = plan["units"]
        self.assertEqual((unit["unit"], unit["experiment"], unit["kind"], unit["model"]), ("jt", "jt", "none", None))
        self.assertEqual(unit["params"], {"pack": NHTSA_PACK, "cutoff": "2023-01-01", "from": "2020-01-01"})
        argv = lab_units.build_argv(unit, self.tmp / "out", self.tmp / "routing.json", prereg=self.tmp / "plan")
        runs = self.tmp / "out" / "work" / "jt"
        self.assertEqual(argv, [sys.executable, "-m", "lab.jt", "verify",
                                f"--prereg={self.tmp / 'plan' / lab_jt.PREREG_FILE}", f"--run-id={unit['run_id']}",
                                f"--runs-dir={runs}"])
        with self.assertRaises(ValueError):
            lab_units.build_argv(unit, self.tmp / "out", self.tmp / "routing.json")
        ok = {"kind": lab_jt.VERIFY_KIND, "verified": True}
        bad = {"kind": lab_jt.VERIFY_KIND, "verified": False}
        status = lab_units._jt_status
        self.assertEqual(status(0, ok), ("ok", None))
        self.assertEqual(status(1, bad), ("failed", notes.JT_NOT_VERIFIED))
        self.assertEqual(status(0, bad), ("failed", notes.RESULT_CONTRADICTS_EXIT))
        self.assertEqual(status(1, ok), ("failed", notes.RESULT_CONTRADICTS_EXIT))
        self.assertEqual(status(0, None), ("failed", notes.RESULT_MISSING))
        self.assertEqual(status(2, ok), ("failed", notes.UNEXPECTED_EXIT))
        self.assertEqual(lab_units.unit_notes("jt", "no-model"), ["public_terms"])
        self.assertEqual(lab_units.unit_notes("jt", "plumbing"), ["plumbing", "public_terms"])
        self.assertFalse(lab_units.public_text(unit))
        self.assertIn("jt", lab_units.PREREG_EXPERIMENTS)


# --------------------------------------------------------------------------------------------------- request

class RequestTests(unittest.TestCase):
    """C23 and C15: the term request's block and the template."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(LAB_MANIFEST)

    def error(self, **changes: Any) -> tuple[str, str]:
        obj = template()
        block = obj["experiments"]["jt"]
        for key, value in changes.items():
            if value is None:
                block.pop(key)
            else:
                block[key] = value
        with self.assertRaises(RequestError) as caught:
            lab_request.validate(obj, self.manifest)
        return caught.exception.path, caught.exception.problem

    def test_the_template(self) -> None:
        obj = template()
        out = lab_request.validate(obj, self.manifest)
        self.assertEqual(out["experiments"], {"jt": {"minutes": 10, "pack": NHTSA_PACK, "cutoff": "2023-01-01",
                                                     "from": "2020-01-01"}})
        self.assertEqual((obj["provider"], obj["models"], obj["job_minutes"], obj["max_parallel"],
                          obj["retention_days"]), ("llama-server", ["a-4b"], 45, 1, 90))
        purpose = obj["purpose"]
        self.assertIn("docs/collective/A003/CHOICE-A003.md", purpose)
        self.assertIn("lab/requests/terms-003.json", purpose)
        rest = purpose.replace("docs/collective/A003/CHOICE-A003.md", "").replace("lab/requests/terms-003.json", "")
        self.assertIsNone(re.search(r"[0-9]", rest))
        self.assertEqual(lab_request._JT_KEYS, ("minutes", "pack", "cutoff", "from"))

    def test_the_errors(self) -> None:
        cases = [
            (dict(minutes=0), "$.experiments.jt.minutes"),
            (dict(minutes=21), "$.experiments.jt.minutes"),
            (dict(pack="docs/collective/replay/vehicles/pack-v2"), "$.experiments.jt.pack"),
            (dict(cutoff="2024-01-01"), "$.experiments.jt.cutoff"),
            (dict(cutoff="20230101"), "$.experiments.jt.cutoff"),
            (dict(**{"from": "2020-01-02"}), "$.experiments.jt.from"),
            (dict(**{"from": "2019-01-01"}), "$.experiments.jt.from"),
            (dict(makes=["FORD"]), "$.experiments.jt.makes"),
            (dict(cutoff=None), "$.experiments.jt.cutoff"),
        ]
        for changes, path in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.error(**changes)[0], path)
        self.assertEqual(self.error(pack="docs/collective/replay/vehicles/pack-v2")[1], lab_request.JT_PACK_PROBLEM)
        self.assertEqual(self.error(cutoff="2024-01-01")[1], lab_request.JT_CUTOFF_PROBLEM)
        self.assertEqual(self.error(**{"from": "2019-01-01"})[1], lab_request.JT_FROM_PROBLEM)
        self.assertIn("2023-01-01", lab_request.JT_CUTOFF_PROBLEM)
        self.assertIn("2020-01-01", lab_request.JT_FROM_PROBLEM)

    def test_the_term_run_is_a_request_of_its_own(self) -> None:
        """Item 10 and C15: a request with a ``jt`` block holds no other block, and keeps its artifact 90 days."""
        j3 = read_json(TEMPLATES / "accuracy-002.json")["experiments"]["j3"]
        for name, other in (("j3", j3), ("x1", {})):
            with self.subTest(beside=name):
                obj = template()
                obj["experiments"][name] = other
                with self.assertRaises(RequestError) as caught:
                    lab_request.validate(obj, self.manifest)
                self.assertEqual((caught.exception.path, caught.exception.problem),
                                 ("$.experiments", lab_request.JT_ALONE_PROBLEM))
        for days in (1, 30, 89):
            with self.subTest(retention_days=days):
                obj = template()
                obj["retention_days"] = days
                with self.assertRaises(RequestError) as caught:
                    lab_request.validate(obj, self.manifest)
                self.assertEqual((caught.exception.path, caught.exception.problem),
                                 ("$.retention_days", lab_request.JT_RETENTION_PROBLEM))
        self.assertEqual(lab_request.JT_RETENTION_DAYS, 90)
        self.assertIsNone(re.search(r"[0-9]", lab_request.JT_ALONE_PROBLEM))
        self.assertEqual(lab_request.validate(template(), self.manifest)["retention_days"], 90)

    def test_the_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-jt-plan-") as tmp:
            plan = read_json(_jt_plan(Path(tmp)))
        self.assertEqual([(u["unit"], u["experiment"], u["kind"], u["minutes"]) for u in plan["units"]],
                         [("jt", "jt", "none", 10)])
        self.assertEqual([(s["kind"], s["units"]) for s in plan["shards"]], [("none", ["jt"])])
        self.assertEqual(plan["provision"], [])          # the listed model is never served (C17)


# --------------------------------------------------------------------------------------------------- imports

class ImportTests(unittest.TestCase):
    """C29: importing ``lab.jt`` (or the modules that import it) loads no download module and leaves ``sys.path``
    alone; ``pooled_export`` puts ``sys.path`` back. Each in a fresh interpreter, ``tools/`` never on its path."""

    def fresh(self, code: str) -> dict[str, Any]:
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=lab_env(), capture_output=True, text=True,
                              timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.splitlines()[-1])

    def test_the_import(self) -> None:
        got = self.fresh(
            "import json, sys\n"
            "before = list(sys.path)\n"
            "import lab.jt, lab.prereg, lab.units, lab.aggregate, lab.summary, lab.request, lab.dryrun\n"
            "tools = [p for p in sys.path if 'tools' in p.replace(chr(92), '/').split('/')]\n"
            "mods = sorted(m for m in sys.modules if m in ('nhtsa_export', 'vehicle_pack', 'fetch_nhtsa',\n"
            "              'lab_jt_fetch_nhtsa'))\n"
            "print(json.dumps({'tools': tools, 'mods': mods, 'same': sys.path == before}))\n")
        self.assertEqual(got, {"tools": [], "mods": [], "same": True})

    def test_the_export_puts_the_path_back(self) -> None:
        got = self.fresh(
            "import json, sys\n"
            "import lab.jt\n"
            "from tests.lab import j4_data\n"
            "blob = j4_data.archive()\n"
            "before = list(sys.path)\n"
            "pooled = lab.jt.pooled_export(blob)\n"
            "print(json.dumps({'same': sys.path == before, 'rows': len(pooled.export)}))\n")
        self.assertTrue(got["same"])
        self.assertGreater(got["rows"], 500)

    def test_the_export_leaves_no_bare_module(self) -> None:
        """C29: after ``pooled_export``, a bare ``import nhtsa_export`` or ``import vehicle_pack`` in the same
        process finds nothing: the modules the download module imported by bare name leave ``sys.modules`` and
        ``tools/market`` leaves ``sys.path``. The archive is read from a file, so nothing else loads them."""
        with tempfile.TemporaryDirectory(prefix="lab-jt-bare-") as tmp:
            (Path(tmp) / "complaints.zip").write_bytes(j4_data.archive())
            got = self.fresh(
                "import importlib.util, json, sys\n"
                "import lab.jt\n"
                f"blob = open({str(Path(tmp) / 'complaints.zip')!r}, 'rb').read()\n"
                "before = list(sys.path)\n"
                "pooled = lab.jt.pooled_export(blob)\n"
                "mods = sorted(m for m in ('nhtsa_export', 'vehicle_pack') if m in sys.modules)\n"
                "found = [m for m in ('nhtsa_export', 'vehicle_pack') if importlib.util.find_spec(m) is not None]\n"
                "print(json.dumps({'same': sys.path == before, 'mods': mods, 'found': found,\n"
                "                  'rows': len(pooled.export)}))\n")
        self.assertEqual((got["same"], got["mods"], got["found"]), (True, [], []))
        self.assertGreater(got["rows"], 500)


# --------------------------------------------------------------------------------------------------- the dry run

class DryRunTests(unittest.TestCase):
    """``lab.dryrun`` of the template the run copies to ``lab/requests/terms-003.json`` with ``--jt-data``: one
    model-free unit, the check passed, the unit verified, no term printed or written outside ``prereg/jt/``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Dry.get()

    def test_the_run(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn("plan: 1 units in 1 shards (real)", lines)
        self.assertIn("prereg: e1 no x1 no e2 no j1 no jt yes", lines)
        self.assertTrue(any(line.startswith("jt check: passed yes ") for line in lines), self.dry.stdout)
        sha_ = sha((self.dry.out / "plan" / lab_jt.TERMS_FILE).read_bytes())
        self.assertTrue(any(line.startswith("jt terms: ") and line.endswith(f"terms_sha256 {sha_}")
                            for line in lines))
        (unit,) = self.dry.report["units"]
        self.assertEqual((unit["unit"], unit["status"], unit["measurement_class"], unit["class_reason"],
                          unit["kind"], unit["exit_code"]), ("jt", "ok", "no-model", "no_model", "none", 0))
        self.assertEqual([(s["kind"], s["units"]) for s in self.dry.plan["shards"]], [("none", ["jt"])])
        self.assertEqual(self.dry.plan["provision"], [])

    def test_the_report_block(self) -> None:
        jt = self.dry.report["jt"]
        sha_ = sha((self.dry.out / "plan" / lab_jt.TERMS_FILE).read_bytes())
        # a dry run's records all say plumbing (lab.dryrun); the block takes its unit's display class
        self.assertEqual((jt["label"], jt["reason"], jt["display_class"], jt["terms_sha256"], jt["verified"]),
                         ("public_nhtsa_terms", None, "plumbing", sha_, True))
        self.assertEqual(jt["unit"], {"unit": "jt", "status": "ok", "status_reason": None})
        self.assertEqual(jt["verify_checks"], dict.fromkeys(lab_jt.VERIFY_CHECKS, True))
        block = self.dry.manifest["jt"]
        for key in ("cutoff", "window", "makes", "archive", "export", "drafting", "draft", "term_counts", "check",
                    "p_draft", "p_d", "hand", "code", "step"):
            self.assertEqual(jt[key], block[key], key)
        (unit,) = self.dry.report["units"]
        self.assertEqual((unit["unit"], unit["status"], unit["display_class"]), ("jt", "ok", "plumbing"))
        self.assertIn("public_terms", unit["notes"])
        self.assertNotIn("synthetic", unit["notes"])

    def test_the_report_block_reads_verify_json_and_the_preregistration(self) -> None:
        """``aggregate.jt_block`` on a copy of the dry run's plan: ``verified`` only when ``verify.json`` says so and
        its sha256 and P_D's hashes are the preregistration's; a preregistration whose ``jt`` block or
        ``prereg/jt/prereg.json`` names another term set sha256 than ``terms.json``'s bytes gives
        ``PREREG_MISSING`` and no sha."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-jt-block-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (verify_path,) = list((self.dry.out / "shards").rglob(lab_jt.VERIFY_RESULT))
        good = read_json(verify_path)
        plan = read_json(self.dry.out / "plan" / "plan.json")
        (unit,) = plan["units"]
        row = {"unit": "jt", "status": "ok", "status_reason": None, "display_class": "no-model",
               "run_id": unit["run_id"]}
        sha_ = sha((self.dry.out / "plan" / lab_jt.TERMS_FILE).read_bytes())

        def block(verify: dict[str, Any] | None = None, change: Any = None) -> dict[str, Any]:
            case = Path(tempfile.mkdtemp(dir=tmp))
            shutil.copytree(self.dry.out / "plan", case / "plan")
            if change is not None:
                change(case / "plan")
            root = case / "shard"
            (root / "runs" / "jt" / unit["run_id"]).mkdir(parents=True)
            (root / "runs" / "jt" / unit["run_id"] / lab_jt.VERIFY_RESULT).write_text(
                json.dumps(verify if verify is not None else good), encoding="utf-8")
            plan_path = case / "plan" / "plan.json"
            return lab_aggregate.jt_block(read_json(plan_path), plan_path, {"jt": (row, {}, root)})

        base = block()
        self.assertEqual((base["verified"], base["terms_sha256"], base["reason"]), (True, sha_, None))
        for name, verify in (("verified false", {**good, "verified": False}),
                             ("another sha", {**good, "terms_sha256": "0" * 64}),
                             ("other P_D hashes", {**good, "p_d": {**good["p_d"], "vocabulary_hash": "0" * 64}}),
                             ("another kind", {**good, "kind": "other"})):
            with self.subTest(case=name):
                got = block(verify)
                self.assertIs(got["verified"], False)
                self.assertEqual((got["terms_sha256"], got["reason"]), (sha_, None))

        def repin(plan_dir: Path, rel: str, edit: Any) -> None:
            path = plan_dir / rel
            doc = read_json(path)
            edit(doc)
            path.write_text(json.dumps(doc), encoding="utf-8")
            manifest_path = plan_dir / "prereg" / "prereg.json"
            manifest = read_json(manifest_path)
            if rel in manifest["files"]:
                manifest["files"][rel] = {"sha256": sha(path.read_bytes()), "bytes": len(path.read_bytes())}
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        for name, rel, edit in (
                ("prereg/jt/prereg.json", lab_jt.PREREG_FILE, lambda d: d.update(terms_sha256="0" * 64)),
                ("the manifest's jt block", "prereg/prereg.json", lambda d: d["jt"].update(terms_sha256="0" * 64))):
            with self.subTest(case=name):
                got = block(change=lambda plan_dir, rel=rel, edit=edit: repin(plan_dir, rel, edit))
                self.assertEqual((got["reason"], got["terms_sha256"], got["verified"]),
                                 (notes.PREREG_MISSING, None, None))

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        sha_ = sha((self.dry.out / "plan" / lab_jt.TERMS_FILE).read_bytes())
        self.assertIn(f"#### {notes.HEADINGS['jt']}", md)
        self.assertIn(f"#### {notes.HEADINGS['jt-check']}", md)
        self.assertIn(lab_summary.ids(notes.JT_LABEL), md)
        self.assertIn(notes.JT_TERMS_NOTE, md)
        self.assertIn(f"- `JT` term set sha `{sha_}`; verified by the unit yes", md)
        self.assertIn(notes.NOTES["public_terms"], md)
        check_sources(self, md, read_json(root / "report.sources.json")["sources"], root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        check_sources(self, plan_md, read_json(self.dry.out / "plan" / "summary.sources.json")["sources"],
                      self.dry.out / "plan")
        self.assertIn(f"#### {notes.HEADINGS['jt']}", plan_md)
        prereg_sha = sha((self.dry.out / "plan" / lab_jt.PREREG_FILE).read_bytes())[:12]
        self.assertIn(f"- `JT` term set sha `{sha_}`; prereg sha `{prereg_sha}`", plan_md)
        self.assertIn(f"#### {notes.HEADINGS['jt-check']}", plan_md)
        self.assertIn("JT", lab_summary.ids(notes.JT_LABEL))
        self.assertNotIn("{", lab_summary.ids(notes.JT_LABEL))

    def test_no_term_leaves_the_term_files(self) -> None:
        terms = read_json(self.dry.out / "plan" / lab_jt.TERMS_FILE)
        words = set(lab_jt.entries_terms(terms["terms"]))
        spellings = {e["spelling"] for e in terms["terms"]}
        refs = {json.loads(line)["record_ref"] for line in
                (self.dry.out / "plan" / lab_jt.DRAFTING_REFS_FILE).read_text(encoding="utf-8").splitlines()}
        self.assertTrue(words and refs)
        texts = {"stdout": self.dry.stdout}
        for path in self.dry.out.rglob("*"):
            rel = path.relative_to(self.dry.out).as_posix()
            if path.is_file() and not rel.startswith(f"plan/{lab_jt.JT_DIR}/"):
                texts[rel] = path.read_text(encoding="utf-8", errors="replace")
        for name, text in texts.items():
            with self.subTest(file=name):
                self.assertEqual(word_hits(text, words), [])
                self.assertEqual([s for s in spellings if s in text], [])
                self.assertEqual(ref_hits(text, refs), [])
                self.assertNotIn(VEHICLE, text)

    def test_the_data_flags_do_not_go_together(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lab-jt-dryrun-flags-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for other in ("--j2-data", "--j3-data"):
            with self.subTest(other=other):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = lab_dryrun.main(["--request", str(TEMPLATE), "--out", str(tmp / "out"),
                                            "--jt-data", str(tmp / "a"), other, str(tmp / "b")])
                self.assertEqual(code, lab_dryrun.EXIT_USAGE)
                self.assertIn("--jt-data does not go with --j2-data or --j3-data", err.getvalue())
                self.assertFalse((tmp / "out").exists())


# --------------------------------------------------------------------------------------------------- step (0)

class _Golden:
    """The j3 template dry run on the code under test, run once for the class (about two minutes)."""

    _tmp: tempfile.TemporaryDirectory[str] | None = None
    proc = None
    out: Path | None = None

    @classmethod
    def get(cls) -> type[_Golden]:
        if cls._tmp is None:
            cls._tmp = tempfile.TemporaryDirectory(prefix="lab-a002-golden-")
            cls.proc, cls.out = a002_golden.dry_run(Path(cls._tmp.name))
        return cls


class A002GoldenTests(unittest.TestCase):
    """Step (0) and C30: the j3 template dry run's plan summary, ``report.json`` and ``report.md`` equal the bytes
    recorded on the base commit (``tests/lab/data/a002_golden/``), after the normalisation of
    ``tests/lab/a002_golden.py`` (the synthetic archive's sha256, the values that name the code, the machine's and
    the clock's figures, the manifest's model file names), and nothing else is excluded."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Golden.get()

    def test_the_dry_run_ran(self) -> None:
        self.assertEqual(self.dry.proc.returncode, 0, self.dry.proc.stdout[-3000:] + self.dry.proc.stderr[-3000:])
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j3 yes", self.dry.proc.stdout.splitlines())

    def test_each_output_equals_the_recorded_bytes(self) -> None:
        found = a002_golden.outputs(self.dry.out)
        self.assertEqual(sorted(found), sorted(a002_golden.OUTPUTS))
        for name, data in found.items():
            with self.subTest(output=name):
                expected = (a002_golden.GOLDEN / name).read_bytes()
                if data != expected:
                    got, want = data.decode("utf-8").split("\n"), expected.decode("utf-8").split("\n")
                    first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
                    self.fail(f"{name} differs from the recording at line {first + 1}")

    def test_the_recording_holds_only_the_named_tokens(self) -> None:
        summary = (a002_golden.GOLDEN / "summary.md").read_text(encoding="utf-8")
        report_md = (a002_golden.GOLDEN / "report.md").read_text(encoding="utf-8")
        report = json.loads((a002_golden.GOLDEN / "report.json").read_bytes())
        self.assertEqual(summary.count(a002_golden.COMMIT), 1)
        self.assertEqual(summary.count(a002_golden.PREREG), 1)
        self.assertEqual(summary.count(a002_golden.ARCHIVE), 1)
        self.assertEqual(report_md.count(a002_golden.ARCHIVE), 1)
        self.assertEqual((report["plan"]["git_sha"], report["plan"]["sha256"]), (a002_golden.COMMIT, a002_golden.PLAN))
        self.assertEqual(report["j3"]["exclusion"]["complaint_file_sha256"], a002_golden.ARCHIVE)
        # what is pinned: every score, count and hash of the draw, as the template dry run printed them
        self.assertEqual(report["j3"]["labels"]["sha256"][:12], "f95b4ed9db9c")
        self.assertEqual(report["j3"]["comparisons"], {"this_cycle": 9, "before": 12, "since_loop_began": 21})
        self.assertIn("excluded refs file sha `c1e1a63cee69`; chain file lines 450", summary)
        # C30: a report whose plan has no term run has no jt key, so it keeps the base commit's keys
        self.assertNotIn("jt", report)

    def test_the_normalisation_leaves_a_changed_figure_visible(self) -> None:
        raw = (self.dry.out / "report" / "report.json").read_bytes()
        doc = json.loads(raw)
        doc["j3"]["comparisons"]["since_loop_began"] = 22
        changed = (canonical_dumps(doc) + "\n").encode("utf-8")
        self.assertNotEqual(a002_golden.normalise_json(changed), a002_golden.normalise_json(raw))
        doc = json.loads(raw)
        doc["units"][0]["wall_s"] = 99.0
        doc["j3"]["exclusion"]["complaint_file_sha256"] = "0" * 64
        timed = (canonical_dumps(doc) + "\n").encode("utf-8")
        self.assertEqual(a002_golden.normalise_json(timed), a002_golden.normalise_json(raw))
        md = (self.dry.out / "report" / "report.md").read_text(encoding="utf-8")
        self.assertNotEqual(a002_golden.normalise_md(md.replace("0.700", "0.701")), a002_golden.normalise_md(md))
        with self.assertRaises(a002_golden.GoldenError):
            a002_golden.normalise_json(json.dumps(json.loads(raw), indent=1).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
