"""A local, openFDA-shaped device-event server for D003's offline tests and dry run. Every maker name, report key,
narrative, lot, UDI and patient value is invented here. The problem names are FDA problem terms copied from files of
this repository; ``PROBLEM_SOURCES`` says, for each, where. Three of them (``Battery Problem``, ``Break`` and
``Insufficient Information``) are named by no value map. No record of any public source is read.

The server answers the three query shapes ``tools/onboard/fetch_openfda.py`` sends (a count list, an exact-name
check, a day's page) from a virtual archive: the reports of a (maker, day) pair are generated on demand from a seed,
so a large archive costs nothing until it is read. Faults can be switched on per test: unstable paging on chosen
days (pages that shift once or always, or a last page that repeats a key), a 429 before every answer, a 500 for
every answer, malformed records on chosen days, and an exact-name search whose total differs (always, or on the
first search of each window only). Per maker, a test can also set the reports of each day, the share of reports
without a description, and the base of the report keys (so that keys differ in length).
"""
from __future__ import annotations

import json
import random
import re
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

DESCRIPTION = "Description of Event or Problem"
# problem name -> (cue words a narrative uses, a device_quality word or none); the names are FDA's problem terms,
# copied from the files PROBLEM_SOURCES names
PROBLEMS = {
    "Crack": (("cracked", "split", "fissure"), "cracked"),
    "Fluid/Blood Leak": (("leaking", "seepage", "dripping"), "leaking"),
    "Occlusion Within Device": (("occluded", "obstruction", "flowstop"), "occluded"),
    "Material Discolored": (("discolored", "yellowing", "tint"), "discolored"),
    "Battery Problem": (("battery", "charge", "drained"), None),
    "Break": (("broke", "snapped", "fragment"), None),
    "Adverse Event Without Identified Device or Use Problem": (("reviewed", "evaluated", "concluded"), None),
    "Insufficient Information": (("limited", "details", "unavailable"), None),
}
# where each problem name comes from (BUILD-D003.md, "The dry run"; checked by FakeArchiveTests):
# - "six-name map": a name of device_quality's own value map, and so also of the 22-name map;
# - "22-name map": a name of lab/packs/device_quality_bd's value map only;
# - "top_problems": a name of no map, copied from the per-code `top_problems` samples of
#   docs/strategy/data/field-coverage.json, which the build read for this;
# - "generic_problems": a name of no map, one of the nine generic terms of field-coverage.json's `definitions`
#   (written there in lower case).
PROBLEM_SOURCES = {
    "Crack": "six-name map",
    "Fluid/Blood Leak": "six-name map",
    "Occlusion Within Device": "six-name map",
    "Material Discolored": "22-name map",
    "Battery Problem": "top_problems",
    "Break": "top_problems",
    "Adverse Event Without Identified Device or Use Problem": "six-name map",
    "Insufficient Information": "generic_problems",
}
FILLER = ("patient", "nurse", "procedure", "hospital", "reported", "device", "during", "after", "returned", "event",
          "clinical", "staff", "observed", "noted", "follow", "visit", "therapy", "monitor", "unit", "shift")
MAKERS = {   # invented names: rate (mean reports a day), problem weights
    "Quillmere Devices Inc": (34, (5, 3, 2, 1, 2, 1, 2, 1)),
    "Varrowbank Medical LLC": (32, (1, 4, 1, 3, 2, 2, 1, 1)),
    "Hobbledene Instruments Corp": (30, (2, 1, 4, 1, 1, 3, 1, 2)),
    "Fennickwold Surgical GmbH": (28, (3, 2, 1, 2, 4, 1, 2, 1)),
    "Tamsworth Pump Systems Ltd": (26, (1, 1, 3, 1, 1, 1, 3, 2)),
    "Brackenridge Catheter Co": (16, (2, 2, 2, 2, 2, 2, 2, 2)),
}
EXTRA_NAMES = {   # names the count lists hold that the selection excludes: (train count, test count)
    "UNKNOWN": (90000, 80000),
    "12345": (85000, 70000),
    'Acme "Pro" Devices': (80000, 60000),
    "Quillmere Devices, Inc.": (5000, 3000),
    "Smallfold Optics Inc": (1500, 900),
}
WINDOW = (date(2021, 1, 1), date(2024, 12, 31))
_EXACT = re.compile(r'device\.manufacturer_d_name\.exact:"(?P<name>[^"]*)" AND date_received:\[(?P<a>\d{8}) TO '
                    r'(?P<b>\d{8})\]')
_WINDOW = re.compile(r'date_received:\[(?P<a>\d{8}) TO (?P<b>\d{8})\]')


def _day(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def _ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


@dataclass
class Archive:
    """The virtual archive: per (maker, day) a seeded number of reports, generated on demand."""

    makers: dict[str, tuple[int, tuple[int, ...]]] = field(default_factory=lambda: dict(MAKERS))
    extra: dict[str, tuple[int, int]] = field(default_factory=lambda: dict(EXTRA_NAMES))
    big_days: int = 6          # days per maker and window with more reports than the per-day cap allows
    # (maker, day): "once" or "always" (pages after the first shift by one record), or "repeat" (the day's last page
    # also carries its first record again, so one key repeats while the distinct keys still number the total)
    unstable: dict[tuple[str, str], str] = field(default_factory=dict)
    # (maker, day): kind of malformed record ("key", "date", "problem"; "company": its only device entry is blank)
    malformed: dict[tuple[str, str], str] = field(default_factory=dict)
    exact_off: dict[str, int] = field(default_factory=dict)   # maker: what the exact search adds to its total
    # maker: what the exact search adds to its total on the first search of each window only
    exact_off_once: dict[str, int] = field(default_factory=dict)
    day_count: dict[str, Callable[[date], int]] = field(default_factory=dict)   # maker: its reports of a day
    no_text: dict[str, float] = field(default_factory=dict)   # maker: share of reports without a description
    key_base: dict[str, int] = field(default_factory=dict)   # maker: the base of its report numbers
    _cache: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    served: dict[tuple[str, str], int] = field(default_factory=dict)
    exact_served: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def count(self, maker: str, day: date) -> int:
        if maker in self.day_count:
            return self.day_count[maker](day)
        rate, _ = self.makers[maker]
        rng = random.Random(f"n|{maker}|{_ymd(day)}")
        if rng.random() < 0.08:
            return 0
        n = max(0, int(rng.gauss(rate, rate / 3)))
        if rng.random() < self.big_days / 730:
            n = 1600 + rng.randrange(400)
        return n

    def total(self, maker: str, first: date, last: date) -> int:
        if maker in self.extra:
            return self.extra[maker][0 if first.year < 2023 else 1]
        if maker not in self.makers:
            return 0
        return sum(self.count(maker, first + timedelta(days=i)) for i in range((last - first).days + 1))

    def records(self, maker: str, day: date) -> list[dict[str, Any]]:
        key = (maker, _ymd(day))
        if key not in self._cache:
            self._cache[key] = [self.record(maker, day, i) for i in range(self.count(maker, day))]
        return self._cache[key]

    def record(self, maker: str, day: date, i: int) -> dict[str, Any]:
        rng = random.Random(f"r|{maker}|{_ymd(day)}|{i}")
        _, weights = self.makers[maker]
        names = list(PROBLEMS)
        first = rng.choices(names, weights=weights)[0]
        problems = [first] + ([rng.choice(names)] if rng.random() < 0.25 else [])
        problems = list(dict.fromkeys(problems))
        words = []
        for p in problems:
            cues, dq = PROBLEMS[p]
            words += rng.sample(cues, 2) + ([dq] if dq and rng.random() < 0.5 else [])
        text = " ".join(words + rng.sample(FILLER, 6))
        if rng.random() < 0.1:
            text += f" Filed as {problems[0].lower()}."          # the filed label written out: an echo
        if "Adverse Event Without Identified Device or Use Problem" in problems and rng.random() < 0.3:
            text += " Coded as adverse event without identified device or use problem."   # an FDA term, 8 tokens
        if rng.random() < 0.15:
            text += f" lot reference {rng.randrange(100, 999)} reviewed"
        if rng.random() < 0.1:
            text = "Template report. The device was returned for evaluation " + str(rng.randrange(10, 99)) + "."
        maker_no = list(self.makers).index(maker) + 1
        number = self.key_base.get(maker, maker_no * 10_000_000) + (day - WINDOW[0]).days * 2000 + i
        devices = [{"manufacturer_d_name": maker, "lot_number": f"LOTX{rng.randrange(10**6):06d}",
                    "udi_di": f"UDIX{rng.randrange(10**8):08d}", "model_number": f"MDL-{rng.randrange(99)}",
                    "brand_name": "Inventbrand"}]
        if rng.random() < 0.03:
            devices.append({"manufacturer_d_name": "Otherside Implants Inc"})
        if rng.random() < 0.02:
            devices.append({"manufacturer_d_name": "   "})
        texts = [{"mdr_text_key": str(2 * number), "text_type_code": "Additional Manufacturer Narrative",
                  "text": "Manufacturer comment never exported."}]
        if rng.random() > self.no_text.get(maker, 0.05):
            texts.append({"mdr_text_key": str(2 * number + 1), "text_type_code": DESCRIPTION, "text": text})
        return {"mdr_report_key": str(number), "report_number": f"RPTX-{number}", "date_received": _ymd(day),
                "product_problems": problems, "device": devices, "mdr_text": texts,
                "patient": [{"patient_sequence_number": "1", "patient_age": "PATIENTAGEX"}],
                "event_type": "Malfunction"}

    def day_results(self, maker: str, day: str) -> list[dict[str, Any]]:
        out = [dict(r) for r in self.records(maker, _day(day))]
        kind = self.malformed.get((maker, day))
        if kind and out:
            if kind == "key":
                out[0]["mdr_report_key"] = "KEYNOTANUMBERZQX"
            elif kind == "date":
                out[0]["date_received"] = "DATENOTPARSEDZQX"
            elif kind == "problem":
                out[0]["product_problems"] = [{"nested": "PROBLEMNOTSTRINGZQX"}, "Crack"]
            elif kind == "company":
                out[0]["device"] = [{"manufacturer_d_name": "   "}]
        return out

    def count_list(self, first: date, last: date, limit: int) -> list[dict[str, Any]]:
        rows = [(n, self.total(n, first, last)) for n in [*self.makers, *self.extra]]
        rows.sort(key=lambda kv: (-kv[1], kv[0]))
        return [{"term": n, "count": c} for n, c in rows[:limit] if c]


class Handler(BaseHTTPRequestHandler):
    archive: Archive
    mode: dict[str, Any]

    def log_message(self, *args: Any) -> None:   # quiet
        return

    def _send(self, status: int, doc: Any) -> None:
        body = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:   # noqa: N802
        self.mode.setdefault("agents", []).append(self.headers.get("User-Agent"))
        self.mode["requests"] = self.mode.get("requests", 0) + 1
        if self.mode.get("always_500"):
            return self._send(500, {"error": {"code": "SERVER_ERROR", "message": "never printed ZQXMSG"}})
        if (self.mode.get("throttle") and self.mode.get("throttled", 0) < self.mode["throttle"]
                and self.mode["requests"] % 2 == 1):
            self.mode["throttled"] = self.mode.get("throttled", 0) + 1
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        parts = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        a = self.archive
        search = q.get("search", "")
        if self.mode.get("bad_total") and "count" not in q:
            return self._send(200, {"meta": {"results": {"total": "TOTALZQX"}}, "results": []})
        if "count" in q:
            m = _WINDOW.fullmatch(search)
            rows = a.count_list(_day(m["a"]), _day(m["b"]), int(q["limit"]))
            return self._send(200, {"meta": {}, "results": rows})
        m = _EXACT.fullmatch(search)
        if m is None:
            return self._send(400, {"error": {"code": "BAD_REQUEST", "message": "ZQXMSG"}})
        name, first, last = m["name"], _day(m["a"]), _day(m["b"])
        limit, skip = int(q.get("limit", "1")), int(q.get("skip", "0"))
        self.mode.setdefault("skips", []).append(skip)
        if first == last:
            results = a.day_results(name, m["a"]) if name in a.makers else []
            total = len(results)
            key = (name, m["a"])
            a.served[key] = a.served.get(key, 0) + 1
            how = a.unstable.get(key)
            pages = -(-total // limit) if limit else 1
            page = (results[1:] + results[:1]) if skip and (
                how == "always" or (how == "once" and a.served[key] <= pages)) else results
            page = page[skip:skip + limit]
            if how == "repeat" and results and skip + limit >= total:
                page = page + [results[0]]
        else:
            searched = (name, m["a"], m["b"])
            a.exact_served[searched] = a.exact_served.get(searched, 0) + 1
            total = a.total(name, first, last) + a.exact_off.get(name, 0)
            if a.exact_served[searched] == 1:
                total += a.exact_off_once.get(name, 0)
            page = [{"mdr_report_key": "0"}]
        if total == 0:
            return self._send(404, {"error": {"code": "NOT_FOUND", "message": "No matches found! ZQXMSG"}})
        return self._send(200, {"meta": {"results": {"skip": skip, "limit": limit, "total": total}}, "results": page})


class FakeServer:
    """The archive served on 127.0.0.1 at a free port, in a thread."""

    def __init__(self, archive: Archive | None = None, **mode: Any) -> None:
        self.archive = archive or Archive()
        self.mode = dict(mode)
        handler = type("BoundHandler", (Handler,), {"archive": self.archive, "mode": self.mode})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> "FakeServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
