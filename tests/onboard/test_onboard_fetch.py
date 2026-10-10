"""The drafting tests' download scripts (``tools/onboard/fetch_msha.py`` and ``fetch_nhtsa.py``), offline, on a
synthetic accident file and a synthetic complaint archive that the tests write. No public record is read. The
settings are D002's, the run's (``docs/collective/onboard/D002-settings.json``), whose values equal D001's but for the
experiment id. ``QuotedMshaSplitTests`` holds D002's change (a) in the MSHA split: the file written as the date probe
shows it, every value in double quotes."""
from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "docs" / "collective" / "onboard" / "D002-settings.json"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "onboard" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


M = _load("fetch_msha")
N = _load("fetch_nhtsa")
COLUMNS = json.loads(SETTINGS.read_text())["arms"]["msha"]["columns"]


def accident(controller: str, mine: str, doc: str, day: str, narrative: str, quote: bool = False) -> str:
    """One accident line; with ``quote``, every value in double quotes and a quote inside a value doubled."""
    row = dict.fromkeys(COLUMNS, "")
    row.update({"CONTROLLER_ID": controller, "CONTROLLER_NAME": f"Name of {controller}", "MINE_ID": mine,
                "DOCUMENT_NO": doc, "ACCIDENT_DT": day, "NARRATIVE": narrative, "CLASSIFICATION": "SYNTH CLASS",
                "OPERATOR_NAME": "Operator Never Printed"})
    if quote:
        return "|".join('"' + row[c].replace('"', '""') + '"' for c in COLUMNS)
    return "|".join(row[c] for c in COLUMNS)


def accident_file() -> tuple[bytes, dict[str, list[bytes]]]:
    """Controllers by construction: C03 and C20 tie on 50 training rows (C03 first), C10 has 30, C40 has too few test
    rows, C50 too few training mines. Lines end in CRLF and one narrative holds a Latin-1 byte."""
    lines: dict[str, list[bytes]] = {}
    out = ["|".join(COLUMNS).encode("latin-1") + b"\r\n"]
    n = 0

    def add(controller: str, mines: int, count: int, day: str, text: str = "Synthetic event text") -> None:
        nonlocal n
        for i in range(count):
            n += 1
            line = accident(controller, f"M{controller}{i % mines}", f"D{n:06d}", day, text).encode("latin-1") + b"\r\n"
            lines.setdefault(controller, []).append(line)
            out.append(line)

    add("C10", 4, 30, "3/4/2016")
    add("C10", 4, 20, "3/4/2017", "")
    add("C10", 4, 120, "5/6/2023", "Caf\xe9 text")
    add("C20", 3, 50, "1/2/2018")
    add("C20", 3, 100, "1/2/2022")
    add("C03", 3, 50, "7/8/2019")
    add("C03", 3, 100, "7/8/2024")
    add("C40", 5, 80, "2/2/2020")
    add("C40", 5, 99, "2/2/2022")
    add("C50", 2, 90, "4/4/2015")
    add("C50", 2, 150, "4/4/2023")
    add("C60", 3, 5, "4/4/2010")
    out.append(b"too|few|fields\r\n")
    return b"".join(out), lines


class MshaSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        data, self.lines = accident_file()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Accidents.txt", data)
        (self.raw / "Accidents.zip").write_bytes(buf.getvalue())
        (self.raw / "Accidents_Definition_File.txt").write_text(
            "CLASSIFICATION\tThe circumstances that contributed most directly.\ncontinued line\n\n"
            "ACCIDENT_TYPE\tThe kind of event.\nNARRATIVE\tThe text.\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.result = M.split(SETTINGS, self.raw, self.tmp / "split")
        self.printed = out.getvalue()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_company_rule(self) -> None:
        companies = json.loads((self.tmp / "split" / "companies.json").read_text())
        self.assertEqual(sorted(companies), ["c1", "c2", "c3"])       # fewer than five qualify: every one is used
        self.assertEqual([companies[k]["training_rows_with_narrative"] for k in ("c1", "c2", "c3")], [50, 50, 30])
        self.assertEqual(companies["c3"]["training_mines"], 4)
        self.assertEqual(companies["c3"]["test_rows_with_narrative"], 120)
        source = json.loads((self.tmp / "split" / "source.json").read_text())
        self.assertEqual((source["controllers"], source["qualifying"], source["used"]), (6, 3, 3))
        self.assertEqual(source["rejected"], {"wrong_width": 1})
        self.assertEqual(source["date_format"], "M/D/YYYY")
        self.assertEqual(source["header"]["columns"], 57)

    def test_company_files_hold_the_original_lines(self) -> None:
        header = "|".join(COLUMNS).encode() + b"\r\n"
        for label, controller in (("c1", "C03"), ("c2", "C20"), ("c3", "C10")):
            data = (self.tmp / "split" / f"{label}.txt").read_bytes()
            self.assertEqual(data, header + b"".join(self.lines[controller]))
        self.assertIn(b"Caf\xe9", (self.tmp / "split" / "c3.txt").read_bytes())

    def test_no_controller_id_or_value_leaves(self) -> None:
        for name in ("companies.json", "source.json", "definitions.json"):
            text = (self.tmp / "split" / name).read_text()
            for controller in ("C03", "C20", "C10", "C40", "C50"):
                self.assertNotIn(controller, text, name)
        for secret in ("C03", "C20", "C10", "Name of", "Operator Never Printed", "Synthetic event text", "D000",
                       "MC03"):
            self.assertNotIn(secret, self.printed)

    def test_definition_lines(self) -> None:
        defs = json.loads((self.tmp / "split" / "definitions.json").read_text())
        self.assertEqual(defs["CLASSIFICATION"], ["CLASSIFICATION\tThe circumstances that contributed most directly.",
                                                  "continued line"])
        self.assertEqual(defs["ACCIDENT_TYPE"], ["ACCIDENT_TYPE\tThe kind of event."])
        self.assertEqual(defs["DOCUMENT_NO"], [])

    def test_a_missing_declared_column_is_an_error(self) -> None:
        bad = self.tmp / "bad"
        bad.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Accidents.txt", "|".join(c for c in COLUMNS if c != "NARRATIVE") + "\n")
        (bad / "Accidents.zip").write_bytes(buf.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = M.main(["split", "--settings", str(SETTINGS), "--raw", str(bad), "--out", str(self.tmp / "o")])
        self.assertEqual(code, 2)
        self.assertIn("NARRATIVE", err.getvalue())

    def test_the_ranking_function(self) -> None:
        rule = {"min_test_rows": 100, "min_training_sites": 3, "count": 2}
        stats = {"b": {"test_rows_with_narrative": 100, "training_mines": {1, 2, 3}, "training_rows_with_narrative": 5},
                 "a": {"test_rows_with_narrative": 100, "training_mines": {1, 2, 3}, "training_rows_with_narrative": 5},
                 "z": {"test_rows_with_narrative": 200, "training_mines": {1, 2, 3}, "training_rows_with_narrative": 9},
                 "y": {"test_rows_with_narrative": 99, "training_mines": {1, 2, 3}, "training_rows_with_narrative": 99}}
        self.assertEqual(M.choose_companies(stats, rule), ["z", "a"])


def complaint_row(odino: str, make: str, comp: str, state: str, day: str, text: str) -> str:
    row = [""] * 51
    row[N.nhtsa_export.C_ODINO], row[N.nhtsa_export.C_MAKE] = odino, make
    row[N.nhtsa_export.C_MODEL], row[N.nhtsa_export.C_YEAR] = "MODELX", "2019"
    row[N.nhtsa_export.C_COMP], row[N.nhtsa_export.C_STATE] = comp, state
    row[N.nhtsa_export.C_LDATE], row[N.nhtsa_export.C_DESCR], row[N.nhtsa_export.C_PROD] = day, text, "V"
    row[50] = "Owner Name Never Exported"
    return "\t".join(row)


class NhtsaSplitTests(unittest.TestCase):
    def test_every_component_name_is_kept_and_the_reporter_repeats_the_complaint(self) -> None:
        rows = [complaint_row("11000001", "FORD", "ENGINE:COOLING", "TX", "20210305", "Engine text one"),
                complaint_row("11000001", "FORD", "SYNTH PART", "TX", "20210305", "Engine text one"),
                complaint_row("11000002", "FORD", "UNLISTED THING:SUB", "CA", "20230101", "Second text"),
                complaint_row("11000003", "FORD", "ENGINE", "CA", "20190101", "Too early"),
                complaint_row("11000004", "JEEP", "AIR BAGS", "OH", "20220202", "Jeep text, with a comma")]
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("COMPLAINTS.txt", "\n".join(rows) + "\n")
            (raw / N.FILE).write_bytes(buf.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                N.split(SETTINGS, raw, Path(tmp) / "split")
            with open(Path(tmp) / "split" / "FORD.csv", newline="") as fh:
                got = list(csv.reader(fh))
            self.assertEqual(got[0], ["odino", "state", "received", "components[]", "vehicle", "summary", "reporter"])
            self.assertEqual(got[1], ["11000001", "tx", "20210305", "ENGINE;SYNTH PART", "FORD-MODELX-2019",
                                      "Engine text one", "11000001"])
            self.assertEqual(got[2][3], "UNLISTED THING")
            self.assertEqual(len(got), 3)
            with open(Path(tmp) / "split" / "JEEP.csv", newline="") as fh:
                self.assertEqual(list(csv.reader(fh))[1][5], "Jeep text, with a comma")
            companies = json.loads((Path(tmp) / "split" / "companies.json").read_text())
            self.assertEqual(sorted(companies), sorted(json.loads(SETTINGS.read_text())["arms"]["nhtsa"]["companies"]
                                                       ["makes"]))
            self.assertEqual(companies["FORD"]["complaints"], 2)
            self.assertEqual(companies["HONDA"]["complaints"], 0)
            printed = out.getvalue()
            for secret in ("11000001", "Engine text", "Owner Name", "MODELX", "Jeep text"):
                self.assertNotIn(secret, printed)


def quoted_accident_file() -> tuple[bytes, dict[str, list[bytes]]]:
    """:func:`accident_file`'s controllers and counts, written as the date probe shows MSHA's file: the header's names
    bare, every value in double quotes, dates mm/dd/yyyy. Narratives hold a pipe and a doubled quote. Two lines are
    rejected: one whose narrative opens a quote it never closes (the line after it is still read), and one with too few
    fields."""
    lines: dict[str, list[bytes]] = {}
    out = ["|".join(COLUMNS).encode("latin-1") + b"\r\n"]
    n = 0

    def add(controller: str, mines: int, count: int, day: str, text: str = 'Synthetic "event" | text') -> None:
        nonlocal n
        m, d, y = day.split("/")
        for i in range(count):
            n += 1
            line = accident(controller, f"M{controller}{i % mines}", f"D{n:06d}", f"{int(m):02d}/{int(d):02d}/{y}",
                            text, quote=True).encode("latin-1") + b"\r\n"
            lines.setdefault(controller, []).append(line)
            out.append(line)
            if controller == "C03" and day == "7/8/2019" and i == 0:
                # a narrative that opens a quote and never closes it: the rest of its line is one field
                bad = accident("C03", "MC030", "D999999", "07/08/2019", "x", quote=True)
                bad = bad.replace('"x"', '"open quote never closed', 1).encode("latin-1") + b"\r\n"
                out.append(bad)

    add("C10", 4, 30, "3/4/2016")
    add("C10", 4, 20, "3/4/2017", "")
    add("C10", 4, 120, "5/6/2023", "Caf\xe9 text")
    add("C20", 3, 50, "1/2/2018")
    add("C20", 3, 100, "1/2/2022")
    add("C03", 3, 50, "7/8/2019")
    add("C03", 3, 100, "7/8/2024")
    add("C40", 5, 80, "2/2/2020")
    add("C40", 5, 99, "2/2/2022")
    add("C50", 2, 90, "4/4/2015")
    add("C50", 2, 150, "4/4/2023")
    add("C60", 3, 5, "4/4/2010")
    out.append(b'"too"|"few"|"fields"\r\n')
    return b"".join(out), lines


class QuotedMshaSplitTests(unittest.TestCase):
    """D002 change (a) in the MSHA split: every line through the drafter's own ``split_line``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.data, self.lines = quoted_accident_file()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Accidents.txt", self.data)
        (self.raw / "Accidents.zip").write_bytes(buf.getvalue())
        (self.raw / "Accidents_Definition_File.txt").write_text("ACCIDENT_DT\tDate of the accident (mm/dd/yyyy).\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.result = M.split(SETTINGS, self.raw, self.tmp / "split")
        self.printed = out.getvalue()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_split_uses_the_drafters_own_parser(self) -> None:
        from mycelic.collective.onboard import exports
        self.assertIs(M.split_line, exports.split_line)

    def test_quoted_dates_parse_and_the_company_rule_is_unchanged(self) -> None:
        companies = json.loads((self.tmp / "split" / "companies.json").read_text())
        self.assertEqual(sorted(companies), ["c1", "c2", "c3"])
        self.assertEqual([companies[k]["training_rows_with_narrative"] for k in ("c1", "c2", "c3")], [50, 50, 30])
        self.assertEqual([companies[k]["test_rows_with_narrative"] for k in ("c1", "c2", "c3")], [100, 100, 120])
        self.assertEqual([companies[k]["training_mines"] for k in ("c1", "c2", "c3")], [3, 3, 4])
        source = json.loads((self.tmp / "split" / "source.json").read_text())
        self.assertEqual(source["date_format"], "M/D/YYYY")
        self.assertEqual((source["controllers"], source["qualifying"], source["used"]), (6, 3, 3))
        self.assertEqual(source["rejected"], {"wrong_width": 2})      # the open quote, and the short line
        self.assertEqual(source["header"], {"columns": 57, "missing_expected": [], "unexpected": []})
        self.assertEqual(json.loads(self.printed)["date_format"], "M/D/YYYY")

    def test_d001s_split_could_not_read_these_dates(self) -> None:
        # D001's split cut each line at the pipe with no quoting: every date keeps its quotes and no format parses
        lang = M.load_language("en")
        at = COLUMNS.index("ACCIDENT_DT")
        lines = self.data.decode("latin-1").split("\r\n")[1:-1]
        dates = [line.split("|")[at].strip() for line in lines if len(line.split("|")) == len(COLUMNS)]
        self.assertEqual(len(dates), 141)     # D001 also rejected every line whose narrative holds a pipe
        self.assertTrue(all(d.startswith('"') and d.endswith('"') for d in dates))
        self.assertIsNone(M.choose_date_format(dates, lang.months, 0.95))
        self.assertEqual(M.choose_date_format([d.strip('"') for d in dates], lang.months, 0.95), "M/D/YYYY")

    def test_company_files_hold_the_original_quoted_lines(self) -> None:
        header = "|".join(COLUMNS).encode() + b"\r\n"
        for label, controller in (("c1", "C03"), ("c2", "C20"), ("c3", "C10")):
            data = (self.tmp / "split" / f"{label}.txt").read_bytes()
            self.assertEqual(data, header + b"".join(self.lines[controller]))
        c1 = (self.tmp / "split" / "c1.txt").read_bytes()
        self.assertNotIn(b"open quote never closed", c1)          # the rejected line is in no company file
        self.assertIn(b'"Synthetic ""event"" | text"', c1)

    def test_the_drafter_reads_a_company_file_without_quotes(self) -> None:
        from mycelic.collective.onboard.exports import read_export
        e = read_export(self.tmp / "split" / "c1.txt")
        self.assertEqual((e.format, len(e.columns), len(e), dict(e.rejected)), ("pipe", 57, 150, {}))
        self.assertEqual(e.value(0, "NARRATIVE"), 'Synthetic "event" | text')
        self.assertEqual(e.value(0, "ACCIDENT_DT"), "07/08/2019")
        self.assertEqual(e.value(0, "CLASSIFICATION"), "SYNTH CLASS")
        self.assertIsNone(e.value(0, "SUBUNIT"))                  # a value written "" is absent

    def test_nothing_but_counts_is_printed(self) -> None:
        for secret in ("C03", "C20", "C10", "Name of", "Operator Never Printed", "Synthetic", "event", "D000",
                       "MC03", "open quote"):
            self.assertNotIn(secret, self.printed)

    def test_a_quoted_header_is_read_the_same(self) -> None:
        header = "|".join(COLUMNS).encode("latin-1")
        data = self.data.replace(header, "|".join(f'"{c}"' for c in COLUMNS).encode("latin-1"), 1)
        self.assertNotEqual(data, self.data)
        raw = self.tmp / "raw_quoted_header"
        raw.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Accidents.txt", data)
        (raw / "Accidents.zip").write_bytes(buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            M.split(SETTINGS, raw, self.tmp / "split_quoted_header")
        for name in ("companies.json", "source.json"):
            got = json.loads((self.tmp / "split_quoted_header" / name).read_text())
            want = json.loads((self.tmp / "split" / name).read_text())
            if name == "source.json":
                got.pop("downloads"), want.pop("downloads")
            self.assertEqual(got, want)

    def test_a_narrative_holding_a_line_break_is_two_rejected_pieces(self) -> None:
        # CHOICE-D002 amendment B2: a break in the narrative (field 55 of 57) leaves a first piece of 55 fields and a
        # last one of 3, one more for each pipe in the narrative's end; neither has 57, so each is rejected and
        # counted, and no company file holds either
        broken = accident("C03", "MC030", "D888888", "07/08/2019", "first part\r\nsecond | part", quote=True)
        first, last = broken.split("\r\n")
        self.assertEqual((len(M.split_line(first, "|")), len(M.split_line(last, "|"))), (55, 4))
        raw = self.tmp / "raw_break"
        raw.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Accidents.txt", self.data + broken.encode("latin-1") + b"\r\n")
        (raw / "Accidents.zip").write_bytes(buf.getvalue())
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            M.split(SETTINGS, raw, self.tmp / "split_break")
        got = json.loads((self.tmp / "split_break" / "source.json").read_text())
        want = json.loads((self.tmp / "split" / "source.json").read_text())
        self.assertEqual((want["rejected"], got["rejected"]), ({"wrong_width": 2}, {"wrong_width": 4}))
        for doc in (got, want):
            doc.pop("downloads"), doc.pop("rejected")
        self.assertEqual(got, want)
        for name in ("companies.json", "c1.txt", "c2.txt", "c3.txt"):
            self.assertEqual((self.tmp / "split_break" / name).read_bytes(), (self.tmp / "split" / name).read_bytes())
        self.assertEqual(json.loads(printed.getvalue())["rejected"], {"wrong_width": 4})
        self.assertNotIn("part", printed.getvalue())


class NhtsaCsvReadTests(unittest.TestCase):
    """D002 change (a) on the NHTSA layout: the drafter reads each make's CSV, written one complaint per line, as the
    csv module reads the whole file (D001's comma reading)."""

    def test_the_drafter_reads_the_split_csv_as_written(self) -> None:
        from mycelic.collective.onboard.exports import parse_export
        rows = [complaint_row("11000001", "FORD", "ENGINE:COOLING", "TX", "20210305", 'Engine "stalled", then; quit'),
                complaint_row("11000001", "FORD", "SYNTH PART", "TX", "20210305", 'Engine "stalled", then; quit'),
                complaint_row("11000002", "FORD", "AIR BAGS", "CA", "20230101", "Plain, text"),
                complaint_row("11000003", "FORD", "STEERING", "OH", "20220202", "")]
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("COMPLAINTS.txt", "\r\n".join(rows) + "\r\n")
            (raw / N.FILE).write_bytes(buf.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                N.split(SETTINGS, raw, Path(tmp) / "split")
            data = (Path(tmp) / "split" / "FORD.csv").read_bytes()
        with io.StringIO(data.decode(), newline="") as fh:
            whole = list(csv.reader(fh))
        self.assertEqual(len(data.decode().splitlines()), len(whole))    # one complaint per line
        e = parse_export(data)
        self.assertEqual((e.format, e.columns, dict(e.rejected)), ("comma", tuple(whole[0]), {}))
        self.assertEqual(len(e), 3)
        self.assertEqual(e.value(0, "summary"), 'Engine "stalled", then; quit')
        self.assertEqual(e.value(0, "components[]"), ("ENGINE", "SYNTH PART"))
        self.assertIsNone(e.value(2, "summary"))
        for r, record in enumerate(whole[1:]):
            self.assertEqual([e.value(r, c) or "" for c in ("odino", "state", "received", "summary")],
                             [record[0], record[1], record[2], record[5]])


class DownloadTests(unittest.TestCase):
    def test_downloads_write_the_files_and_print_only_sizes_and_hashes(self) -> None:
        for mod, names in ((M, [n for n, _ in M.FILES]), (N, [N.FILE])):
            with self.subTest(script=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                asked: list[str] = []

                def fetcher(url: str) -> bytes:
                    asked.append(url)
                    return b"payload for " + url.encode()

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    facts = mod.download(Path(tmp), fetcher)
                self.assertEqual([f["file"] for f in facts], names)
                for f, url in zip(facts, asked):
                    data = (Path(tmp) / f["file"]).read_bytes()
                    self.assertEqual((f["bytes"], f["sha256"]), (len(data), hashlib.sha256(data).hexdigest()))
                    self.assertTrue(url.startswith("https://"))
                lines = [json.loads(line) for line in out.getvalue().splitlines()]
                self.assertEqual(lines, facts)
                self.assertTrue(all(set(x) == {"file", "bytes", "sha256"} for x in lines))
                self.assertEqual(json.loads((Path(tmp) / "download.json").read_text()), facts)


HONEST_UA = "mycelic-onboard (pack-drafting test on public data)"


class HonestFetchTests(unittest.TestCase):
    """The brief's honest-fetch rules: one plain request per file, with the run's own User-Agent, and no retry by
    any other route when a host refuses."""

    def test_both_scripts_name_themselves_in_the_user_agent(self) -> None:
        self.assertEqual((M.UA, N.UA), (HONEST_UA, HONEST_UA))

    def test_fetch_sends_the_user_agent_once_and_never_retries(self) -> None:
        import urllib.error
        from unittest import mock

        for mod in (M, N):
            with self.subTest(script=mod.__name__):
                calls: list[Any] = []

                class Resp(io.BytesIO):
                    def __enter__(self) -> "Resp":
                        return self

                    def __exit__(self, *exc: Any) -> None:
                        return None

                def ok(req: Any, timeout: float) -> Resp:
                    calls.append((req, timeout))
                    return Resp(b"body")

                with mock.patch("urllib.request.urlopen", ok):
                    self.assertEqual(mod.fetch("https://example.invalid/file.zip"), b"body")
                (req, timeout), = calls
                self.assertEqual(req.get_header("User-agent"), HONEST_UA)
                self.assertEqual((req.full_url, req.get_method(), timeout),
                                 ("https://example.invalid/file.zip", "GET", 900))

                calls.clear()

                def refuse(req: Any, timeout: float) -> Resp:
                    calls.append(req)
                    raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

                with mock.patch("urllib.request.urlopen", refuse), self.assertRaises(urllib.error.HTTPError):
                    mod.fetch("https://example.invalid/file.zip")
                self.assertEqual(len(calls), 1)

    def test_download_asks_each_file_once_and_stops_at_a_refusal(self) -> None:
        for mod, n in ((M, len(M.FILES)), (N, 1)):
            with self.subTest(script=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                asked: list[str] = []

                def fetcher(url: str) -> bytes:
                    asked.append(url)
                    return b"x"

                with contextlib.redirect_stdout(io.StringIO()):
                    mod.download(Path(tmp), fetcher)
                self.assertEqual(len(asked), n)
                self.assertEqual(len(set(asked)), n)
                asked.clear()

                def refusing(url: str) -> bytes:
                    asked.append(url)
                    raise OSError("refused")

                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
                    mod.download(Path(tmp) / "again", refusing)
                self.assertEqual(len(asked), 1)


if __name__ == "__main__":
    unittest.main()
