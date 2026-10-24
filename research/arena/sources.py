"""Sources: the tails that turn what the runtime writes into events.

Each source owns the files it reads, remembers how far it has read (``snapshot``/``restore`` so a console
restart does not re-emit history), and appends typed events to the store with a ``ref`` to the exact file and
line.  A source that finds nothing emits nothing; the UI renders that as WAITING or NO DATA.

* :class:`SwarmSource` -- the builder layer.  One directory per agent under a root; ``START`` and ``END``
  stamp files (ISO-8601 UTC, written by the agent itself) bound its interval; any other file is an artifact.
* :class:`HarnessSource` -- one EMERGENCE run directory.  HQ<->site wire frames become ASK/REPLY, per-site
  retrieval rows become ROUTE, graph syncs SYNC, publishes PUBLISH, the partial report's phase and checks
  PHASE/VERIFY/FAIL, lineage rows in the fabric db EDGE, and the harness's own stdout LOG/TEST lines.
* :class:`GitSource` -- commits on a branch become COMMIT, with the commit's own time as ``at``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import EventStore

_E2E_LINE = re.compile(r"^\[e2e (\d\d:\d\d:\d\d)\] (.*)$")


def _mtime_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp(p: Path) -> str:
    """The ISO time inside a stamp file, or the file's mtime when the content is not a time."""
    try:
        text = p.read_text(encoding="utf-8").strip()
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except (OSError, ValueError):
        return _mtime_iso(p)


class Tail:
    """Incremental reader of a line-oriented file that may not exist yet and may still be growing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.offset = 0
        self.lineno = 0
        self._partial = b""

    def read(self) -> list[tuple[int, str]]:
        if not self.path.is_file():
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
            self.offset = fh.tell()
        if not chunk:
            return []
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        out = []
        for raw in lines:
            self.lineno += 1
            s = raw.decode("utf-8", "replace").strip()
            if s:
                out.append((self.lineno, s))
        return out

    def snapshot(self) -> dict[str, Any]:
        return {"offset": self.offset, "lineno": self.lineno}

    def restore(self, d: dict[str, Any]) -> None:
        self.offset = int(d.get("offset", 0))
        self.lineno = int(d.get("lineno", 0))
        self._partial = b""


# ------------------------------------------------------------------------------------------------- the swarm

class SwarmSource:
    name = "swarm"

    def __init__(self, root: str | Path, roles: dict[str, dict[str, Any]] | None = None) -> None:
        self.root = Path(root)
        self.roles = dict(roles or {})
        self.agents: dict[str, dict[str, Any]] = {}
        self._roles_mtime = 0.0

    def poll(self, store: EventStore) -> None:
        if not self.root.exists():
            return
        roles_file = self.root / "ROLES.json"
        if roles_file.exists():
            mt = roles_file.stat().st_mtime
            if mt != self._roles_mtime:          # re-read when the orchestrator adds a wave's launch metadata
                self._roles_mtime = mt
                try:
                    self.roles = {k: v for k, v in json.loads(roles_file.read_text()).items() if not k.startswith("_")}
                    for aid, a in self.agents.items():
                        if not a.get("role") and aid in self.roles:
                            a["role"] = self.roles[aid]
                except (OSError, ValueError):
                    pass
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            aid = d.name
            a = self.agents.setdefault(aid, {"id": aid, "start": None, "end": None, "artifacts": [],
                                             "role": self.roles.get(aid, {})})
            start, end = d / "START", d / "END"
            if a["start"] is None and start.exists():
                a["start"] = _stamp(start)
                fam = a["role"].get("family", "role unknown")
                store.append(tag="SPAWN", source=self.name, agent=aid, at=a["start"],
                             msg=f"{aid} started: {fam}", ref={"path": str(start)}, data={"role": a["role"]})
            for f in sorted(d.iterdir()):
                if f.name in ("START", "END") or not f.is_file() or f.name in a["artifacts"]:
                    continue
                a["artifacts"].append(f.name)
                size = f.stat().st_size
                store.append(tag="ARTIFACT", source=self.name, agent=aid, at=_mtime_iso(f),
                             msg=f"{aid} wrote {f.name} ({size} B)", ref={"path": str(f)}, data={"bytes": size})
            if a["end"] is None and end.exists():
                a["end"] = _stamp(end)
                store.append(tag="COMPLETE", source=self.name, agent=aid, at=a["end"],
                             msg=f"{aid} finished ({len(a['artifacts'])} artifacts)", ref={"path": str(end)},
                             data={"artifacts": list(a["artifacts"])})

    def registry(self) -> list[dict[str, Any]]:
        out = []
        for a in self.agents.values():
            state = "RUNNING" if a["start"] and not a["end"] else ("COMPLETE" if a["end"] else "QUEUED")
            out.append({**a, "state": state})
        return out

    def snapshot(self) -> dict[str, Any]:
        return {"agents": self.agents}

    def restore(self, d: dict[str, Any]) -> None:
        self.agents = dict(d.get("agents", {}))


# ----------------------------------------------------------------------------------------------- the harness

class HarnessSource:
    name = "harness"
    PHASES = ("main", "ref", "pristine")

    def __init__(self, run_dir: str | Path, log_path: str | Path | None = None, run_id: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id or self.run_dir.name
        self.log = Tail(log_path) if log_path else None
        self.tails: dict[str, Tail] = {}
        self.sites: set[str] = set()
        self.phase: str | None = None
        self.checks_seen: set[str] = set()
        self.verdict_seen = False
        self.lineage_count = 0
        self.report_mtime = 0.0
        self.report: dict[str, Any] = {}

    # discovery is repeated every poll: site files appear as the run proceeds
    def _discover(self) -> None:
        for phase in self.PHASES:
            self.tails.setdefault(f"wire|{phase}", Tail(self.run_dir / phase / "hq" / "wire.jsonl"))
            edge = self.run_dir / phase / "edge"
            if edge.exists():
                for p in edge.glob("site-*.tesseract.jsonl"):
                    sid = p.name[len("site-"):-len(".tesseract.jsonl")]
                    self.sites.add(sid)
                    self.tails.setdefault(f"tess|{phase}|{sid}", Tail(p))
                for p in edge.glob("site-*.graph.jsonl"):
                    sid = p.name[len("site-"):-len(".graph.jsonl")]
                    self.sites.add(sid)
                    self.tails.setdefault(f"graph|{phase}|{sid}", Tail(p))
        for b in ("bridge", "bridge2"):
            self.tails.setdefault(f"pub|{b}", Tail(self.run_dir / b / "publishes.jsonl"))

    def poll(self, store: EventStore) -> None:
        if not self.run_dir.exists():
            return
        self._discover()
        for key, tail in list(self.tails.items()):
            parts = key.split("|")
            for lineno, line in tail.read():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ref = {"path": str(tail.path), "line": lineno}
                if parts[0] == "wire":
                    self._wire(store, row, parts[1], ref)
                elif parts[0] == "tess":
                    qid = str(row.get("question_id", ""))
                    store.append(tag="ROUTE", source=self.name, agent=parts[2], at=row.get("ts"),
                                 msg=f"{parts[2]} retrieval q={qid[:8]}: {row.get('returned')} returned of "
                                     f"{row.get('nodes')} nodes, {row.get('read')} read, {float(row.get('seconds', 0)):.2f}s",
                                 ref=ref, data={"phase": parts[1], "site": parts[2], "question_id": qid,
                                                **{k: row.get(k) for k in ("seconds", "nodes", "nodes_as_of", "returned",
                                                                            "read", "in_window_returned", "entity_matched")}})
                elif parts[0] == "graph":
                    store.append(tag="SYNC", source=self.name, agent=parts[2], at=row.get("ts"),
                                 msg=f"{parts[2]} graph sync: {row.get('nodes_added')} nodes added, {row.get('nodes')} held, "
                                     f"{float(row.get('seconds', 0)):.2f}s",
                                 ref=ref, data={"phase": parts[1], **{k: row.get(k) for k in ("nodes", "nodes_added", "nodes_before",
                                                                                              "seq_before", "seq_after", "seconds")}})
                elif parts[0] == "pub":
                    store.append(tag="PUBLISH", source=self.name, agent="bridge", at=row.get("ts"),
                                 msg=f"{parts[1]} publish: " + ", ".join(f"{k}={v}" for k, v in list(row.items())[:4]),
                                 ref=ref, data={"bridge": parts[1], **{k: v for k, v in row.items() if isinstance(v, (str, int, float, bool))}})
        if self.log:
            for lineno, line in self.log.read():
                m = _E2E_LINE.match(line)
                msg = m.group(2) if m else line
                low = msg.lower()
                failing = bool(re.search(r"\b(error|traceback|exception)\b", low)) or (
                    re.search(r"\bfailed\b", low) is not None and re.search(r"(?:^|\D)0 failed|failed=0|failures=0", low) is None)
                tag = "FAIL" if failing else ("TEST" if "drill" in low else "LOG")
                store.append(tag=tag, source=self.name, agent="hq", msg=msg,
                             ref={"path": str(self.log.path), "line": lineno}, data={"clock": m.group(1) if m else None})
        self._report(store)
        self._lineage(store)

    def _wire(self, store: EventStore, row: dict[str, Any], phase: str, ref: dict[str, Any]) -> None:
        frame: dict[str, Any] = {}
        body: dict[str, Any] = {}
        try:
            frame = json.loads(row.get("frame", "{}")) if isinstance(row.get("frame"), str) else dict(row.get("frame") or {})
            body = json.loads(frame["body"]) if isinstance(frame.get("body"), str) else dict(frame.get("body") or {})
        except (ValueError, TypeError):
            pass
        site = row.get("site")
        qid = str(body.get("question_id") or "")
        win = body.get("window") or {}
        if row.get("direction") == "request":
            tag, agent = "ASK", "hq"
            msg = (f"hq -> {site} {frame.get('kind', '?')} {body.get('candidate_key', '')} q={qid[:8]} "
                   f"{win.get('start_week', '?')}..{win.get('end_week', '?')}")
        else:
            tag, agent = "REPLY", site
            msg = f"{site} -> hq {body.get('verdict') or frame.get('kind', '?')} q={qid[:8]}"
            if body.get("reason"):
                msg += f" ({body['reason']})"
        store.append(tag=tag, source=self.name, agent=agent, at=row.get("ts"), msg=msg, ref=ref,
                     data={"phase": phase, "site": site, "question_id": qid, "candidate_key": body.get("candidate_key"),
                           "verdict": body.get("verdict"), "kind": frame.get("kind"), "bytes": row.get("bytes"),
                           "sha256": row.get("sha256")})

    def _report(self, store: EventStore) -> None:
        p = self.run_dir / "e2e-ingest-report.json"
        if not p.exists():
            return
        mt = p.stat().st_mtime
        if mt == self.report_mtime:
            if not self.report:                  # restored positions, unchanged file: reload the summary, emit nothing
                try:
                    self.report = json.loads(p.read_text(encoding="utf-8"))
                    self.phase = self.phase or self.report.get("phase")
                except (OSError, ValueError):
                    pass
            return
        self.report_mtime = mt
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.report = doc
        ref = {"path": str(p)}
        phase = doc.get("phase")
        if phase != self.phase:
            self.phase = phase
            store.append(tag="PHASE", source=self.name, agent="hq", msg=f"run {self.run_id}: phase -> {phase}",
                         ref=ref, data={"phase": phase, "completed": doc.get("completed")})
        for name, c in (doc.get("checks") or {}).items():
            if name in self.checks_seen or not isinstance(c, dict):
                continue
            self.checks_seen.add(name)
            ok = bool(c.get("ok"))
            store.append(tag="VERIFY" if ok else "FAIL", source=self.name, agent="hq",
                         msg=f"check {name}: {'ok' if ok else 'FAILED ' + ','.join(c.get('failed') or [])} "
                             f"({c.get('assertions')} assertions)",
                         ref=ref, data={"check": name, "ok": ok, "failed": c.get("failed"), "assertions": c.get("assertions")})
        v = doc.get("verdict")
        if isinstance(v, dict) and not self.verdict_seen:
            self.verdict_seen = True
            store.append(tag="VERIFY" if v.get("pass") else "FAIL", source=self.name, agent="hq",
                         msg=f"verdict pass={v.get('pass')} checks_pass={v.get('checks_pass')} reason={v.get('reason')} "
                             f"unevaluated={v.get('unevaluated_required')}",
                         ref=ref, data=dict(v))

    def _lineage(self, store: EventStore) -> None:
        db = self.run_dir / "fabric" / "mycelic.db"
        if not db.exists():
            return
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
            try:
                n = con.execute("select count(*) from lineage_edges").fetchone()[0]
                if n > self.lineage_count:
                    cur = con.execute("select * from lineage_edges limit ? offset ?", (n - self.lineage_count, self.lineage_count))
                    cols = [c[0] for c in cur.description]
                    rows = cur.fetchall()
                    for i, r in enumerate(rows[:25]):
                        d = dict(zip(cols, r))
                        store.append(tag="EDGE", source=self.name, agent="fabric",
                                     msg="lineage edge: " + " ".join(f"{k}={str(v)[:24]}" for k, v in d.items() if v is not None),
                                     ref={"path": str(db), "table": "lineage_edges", "row": self.lineage_count + i + 1}, data=d)
                    if len(rows) > 25:
                        store.append(tag="EDGE", source=self.name, agent="fabric",
                                     msg=f"+{len(rows) - 25} more lineage edges ({n} total)",
                                     ref={"path": str(db), "table": "lineage_edges"}, data={"total": n})
                    self.lineage_count = n
            finally:
                con.close()
        except sqlite3.Error:
            return

    def summary(self) -> dict[str, Any]:
        d = self.report
        dis = d.get("discovery") or {}
        return {"run_id": self.run_id, "run_dir": str(self.run_dir), "phase": self.phase, "completed": d.get("completed"),
                "sites": sorted(self.sites), "lineage_edges": self.lineage_count,
                "checks": {k: {"ok": v.get("ok"), "assertions": v.get("assertions"), "failed": v.get("failed")}
                           for k, v in (d.get("checks") or {}).items() if isinstance(v, dict)},
                "verdict": d.get("verdict"),
                "discovery": {k: dis.get(k) for k in ("found", "gold_discoverable", "recall_all", "recall_rare",
                                                      "decoy_accepted_by_type", "background_supported")},
                "questions_asked": (d.get("routing") or {}).get("questions_asked")}

    def snapshot(self) -> dict[str, Any]:
        return {"tails": {k: t.snapshot() for k, t in self.tails.items()}, "log": self.log.snapshot() if self.log else None,
                "phase": self.phase, "checks_seen": sorted(self.checks_seen), "verdict_seen": self.verdict_seen,
                "lineage_count": self.lineage_count, "report_mtime": self.report_mtime}

    def restore(self, d: dict[str, Any]) -> None:
        self._discover()
        for k, t in (d.get("tails") or {}).items():
            self.tails.setdefault(k, Tail(self.run_dir))
            self.tails[k].restore(t)
        if self.log and d.get("log"):
            self.log.restore(d["log"])
        self.phase = d.get("phase")
        self.checks_seen = set(d.get("checks_seen") or ())
        self.verdict_seen = bool(d.get("verdict_seen"))
        self.lineage_count = int(d.get("lineage_count") or 0)
        self.report_mtime = float(d.get("report_mtime") or 0.0)


# ----------------------------------------------------------------------------------------------------- git

class GitSource:
    name = "git"

    def __init__(self, repo: str | Path, branch: str = "HEAD", limit: int = 30) -> None:
        self.repo = Path(repo)
        self.branch = branch
        self.limit = limit
        self.seen: set[str] = set()

    def poll(self, store: EventStore) -> None:
        try:
            out = subprocess.run(["git", "-C", str(self.repo), "log", f"-n{self.limit}",
                                  "--format=%H%x1f%cI%x1f%an%x1f%s", self.branch],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return
        rows = [l.split("\x1f") for l in out.splitlines() if l.count("\x1f") == 3]
        for sha, when, author, subject in reversed(rows):
            if sha in self.seen:
                continue
            self.seen.add(sha)
            store.append(tag="COMMIT", source=self.name, agent=author, at=when, msg=f"{sha[:7]} {subject}",
                         ref={"path": str(self.repo), "sha": sha}, data={"sha": sha, "branch": self.branch})

    def snapshot(self) -> dict[str, Any]:
        return {"seen": sorted(self.seen)}

    def restore(self, d: dict[str, Any]) -> None:
        self.seen = set(d.get("seen") or ())
