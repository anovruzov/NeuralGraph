"""Re-aggregate a finished lab run from its artifacts: the report is rebuilt by this commit's ``lab.aggregate`` and
no unit runs again.

    python -m lab.reaggregate find --event-path FILE --event-name push|workflow_dispatch [--github-output FILE]
    python -m lab.reaggregate sort --artifacts DIR --run-id N --out DIR [--github-output FILE]

A re-aggregation request is ``lab/reaggregate/<name>.json`` (the name as a lab request's), a JSON object with
exactly ``run_id`` (the finished ``mycelic-lab`` run's GitHub run id, a positive integer) and ``purpose`` (one line
of printable text, at most :data:`MAX_PURPOSE` characters): :func:`load_request`. The workflow
``.github/workflows/lab-reaggregate.yml`` runs on a push that adds or changes one (never on the default branch) and
on dispatch with its path; ``lab.aggregate --reaggregation FILE`` stamps the report with it.

``find`` decides which request an event runs. ``workflow_dispatch``: ``inputs.request`` names it, a regular file at
the checked-out ref. ``push``: the event fields, the deleted-branch, tag and default-branch rules, the checkout check
and the base commit are ``lab.discover``'s; the request files the push added, modified or type-changed between the
base and the pushed commit (``git diff`` over :data:`REQUEST_GLOB`) are the candidates, and the newest of them runs:
the one whose last change in the push comes first in ``git rev-list --topo-order``, ties going to the later path. A
push that only deleted requests runs nothing (exit 0, a notice). The chosen file must match :data:`REQUEST_PATH_RE`
and load. On
success stdout is ``reaggregate: <path> run <run id>`` and ``--github-output`` gets ``request=<path>`` and
``run_id=<run id>``; when nothing runs it gets neither, so the workflow's later steps skip. A problem prints
``error: <path>: <problem>`` on stderr and an ``::error`` workflow command, and exits 2.

``sort`` lays out what ``actions/download-artifact`` wrote to ``--artifacts`` (one directory per artifact, named after
it) as ``lab.aggregate`` reads a run: ``OUT/plan`` is the plan artifact of the run's newest attempt
(``lab-plan-<run id>-<attempt>``), ``OUT/provision/<artifact>`` each ``lab-prov-<run id>-*`` and
``OUT/shards/<artifact>`` each ``lab-run-<run id>-*`` (the aggregate then picks each shard's newest attempt itself).
Every other entry (the run's report, an older plan, another run's artifact, a symlink) stays where it is. stdout is
``lab: reaggregate sort plan <artifact> provision <n> shards <n> ignored <n>``; ``--github-output`` gets the plan's
``retention_days``. ``OUT`` must be absent or empty, and a run without a plan artifact exits 2.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import EXIT_OK, EXIT_USAGE, LabError, display_path, forbidden_root, gh_data, safe_path, shown_path
from .discover import CHANGED, DELETED, EVENTS, DiscoveryError, _base, _field, _git_ok, _is_sha
from .notes import (BRANCH_DELETED, CHECKOUT_MISMATCH, DEFAULT_BRANCH, DELETE_ONLY, NO_BASE, NOT_A_BRANCH,
                    REAGGREGATE_BAD_NAME, REAGGREGATE_NO_CHANGE)

REQUEST_PATH_RE = re.compile(r"lab/reaggregate/[a-z0-9][a-z0-9-]{0,39}\.json", re.ASCII)
REQUEST_GLOB = ":(glob)lab/reaggregate/*.json"
REQUEST_KEYS = ("run_id", "purpose")
MAX_PURPOSE = 500
MAX_RUN_ID = 10 ** 20 - 1          # an artifact name holds at most twenty digits of run id (lab.aggregate)
NOTICE_TITLE = "lab reaggregate"


class ReaggregateError(LabError):
    pass


# --------------------------------------------------------------------------------------------------- the request

def load_request(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The request at ``path`` (a regular file): ``{"path", "sha256", "run_id", "purpose"}``, ``path`` as the lab may
    print it (relative to the current directory when inside it; null when it holds other characters).
    :class:`ReaggregateError` for anything else."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise OSError
        data = Path(path).read_bytes()
    except OSError:
        raise ReaggregateError("request", "not a readable regular file") from None
    try:
        doc = strict_load(data)
    except StrictJsonError as err:
        raise ReaggregateError(safe_path(err.path), f"invalid JSON ({err.reason})") from None
    if not isinstance(doc, dict) or sorted(doc) != sorted(REQUEST_KEYS):
        raise ReaggregateError("$", "must be an object with exactly run_id and purpose") from None
    run_id, purpose = doc["run_id"], doc["purpose"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or not 1 <= run_id <= MAX_RUN_ID:
        raise ReaggregateError("$.run_id", "must be a positive integer of at most twenty digits") from None
    if (not isinstance(purpose, str) or not purpose.strip() or len(purpose) > MAX_PURPOSE
            or not purpose.isprintable()):
        raise ReaggregateError("$.purpose", f"must be one line of printable text, at most {MAX_PURPOSE} "
                                            "characters") from None
    return {"path": shown_path(display_path(path)), "sha256": sha256_hex(data), "run_id": run_id,
            "purpose": purpose}


# --------------------------------------------------------------------------------------------------- find

@dataclass(frozen=True)
class Found:
    outcome: str                          # run | nothing | error
    request: dict[str, Any] | None = None
    notices: list[str] = field(default_factory=list)
    error: str | None = None


def _newest(root: Path, base: str, after: str, paths: list[str]) -> str:
    """The path whose last change in ``base..after`` comes first in ``git rev-list --topo-order`` (newest first);
    paths last changed by the same commit go to the later path."""
    order = _git_ok(root, "rev-list", "--topo-order", f"{base}..{after}").split()
    rank = {sha: i for i, sha in enumerate(order)}

    def last(path: str) -> int:
        sha = _git_ok(root, "rev-list", "--topo-order", "-1", f"{base}..{after}", "--", path).strip()
        return rank.get(sha, len(order))

    return max(paths, key=lambda p: (-last(p), p))


def _push(event: dict[str, Any], root: Path) -> tuple[str | None, str | None]:
    """(the request path, None) or (None, the notice of a push that runs nothing)."""
    after = _field(event, "$.after", _is_sha, "must be a 40-hex commit id")
    before = _field(event, "$.before", _is_sha, "must be a 40-hex commit id")
    ref = _field(event, "$.ref", lambda v: isinstance(v, str), "must be a string")
    deleted = _field(event, "$.deleted", lambda v: isinstance(v, bool), "must be a bool")
    default_branch = _field(event, "$.repository.default_branch", lambda v: isinstance(v, str) and v != "",
                            "must be a non-empty string")
    if deleted:
        return None, BRANCH_DELETED
    if not ref.startswith("refs/heads/") or ref == "refs/heads/":
        return None, NOT_A_BRANCH
    branch = ref[len("refs/heads/"):]
    if branch == default_branch:
        return None, DEFAULT_BRANCH
    if _git_ok(root, "rev-parse", "HEAD").strip() != after:
        raise DiscoveryError("event", CHECKOUT_MISMATCH) from None
    base = _base(root, before, after, branch, default_branch)
    if base is None:
        raise DiscoveryError("event", NO_BASE) from None
    out = _git_ok(root, "diff", "--no-color", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, after,
                  "--", REQUEST_GLOB)
    fields = out.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise DiscoveryError("event", REAGGREGATE_NO_CHANGE) from None
    pairs = [(fields[i], fields[i + 1]) for i in range(0, len(fields), 2)]
    changed = [path for status, path in pairs if status in CHANGED]
    if not changed:
        if any(status in DELETED for status, _ in pairs):
            return None, DELETE_ONLY
        raise DiscoveryError("event", REAGGREGATE_NO_CHANGE) from None
    return _newest(root, base, after, changed), None


def find(event_path: str | os.PathLike[str], event_name: str, root: str | os.PathLike[str]) -> Found:
    """The request one event runs, loaded; a problem comes back as outcome ``error``."""
    root = Path(root)
    try:
        if event_name not in EVENTS:
            raise DiscoveryError("event", "unsupported event (push or workflow_dispatch)") from None
        try:
            event = strict_load(Path(event_path).read_bytes())
        except OSError:
            raise DiscoveryError("event", "cannot read the event file") from None
        except StrictJsonError as err:
            raise DiscoveryError(safe_path(err.path), f"invalid event JSON ({err.reason})") from None
        if not isinstance(event, dict):
            raise DiscoveryError("$", "the event must be an object") from None
        if event_name == "push":
            path, notice = _push(event, root)
            if path is None:
                return Found("nothing", notices=[notice])
        else:
            path = _field(event, "$.inputs.request", lambda v: isinstance(v, str), "must be a string")
        if REQUEST_PATH_RE.fullmatch(path) is None:
            raise DiscoveryError("event" if event_name == "push" else "$.inputs.request", REAGGREGATE_BAD_NAME) \
                from None
        request = load_request(root / path)
    except LabError as err:
        return Found("error", error=f"{err.path}: {err.problem}")
    return Found("run", request={**request, "path": path})


# --------------------------------------------------------------------------------------------------- sort

def sort_artifacts(artifacts: Path, run_id: int, out: Path) -> dict[str, Any]:
    """Move the run's plan (newest attempt), provision and shard artifacts under ``out``; see the module docstring.
    Returns ``{"plan", "provision", "shards", "ignored", "retention_days"}`` (artifact names, sorted)."""
    plan_re = re.compile(rf"lab-plan-{run_id}-([1-9][0-9]{{0,5}})", re.ASCII)
    prov_re = re.compile(rf"lab-prov-{run_id}-[1-9][0-9]{{0,5}}-[a-z0-9][a-z0-9-]*", re.ASCII)
    run_re = re.compile(rf"lab-run-{run_id}-[1-9][0-9]{{0,5}}-s[0-9]{{3}}-[a-z0-9][a-z0-9-]{{0,23}}", re.ASCII)
    entries = sorted(artifacts.iterdir(), key=lambda p: p.name) if artifacts.is_dir() else []
    dirs = [p for p in entries if p.is_dir() and not p.is_symlink()]
    plans = sorted((int(m.group(1)), p) for p in dirs if (m := plan_re.fullmatch(p.name)) is not None)
    if not plans:
        raise ReaggregateError("artifacts", f"no plan artifact of run {run_id}") from None
    plan = plans[-1][1]
    provision = [p for p in dirs if prov_re.fullmatch(p.name)]
    shards = [p for p in dirs if run_re.fullmatch(p.name)]
    moved = {plan.name, *(p.name for p in provision), *(p.name for p in shards)}
    out.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan), str(out / "plan"))
    for target, group in (("provision", provision), ("shards", shards)):
        (out / target).mkdir()
        for path in group:
            shutil.move(str(path), str(out / target / path.name))
    retention = None
    try:
        retention = strict_load((out / "plan" / "plan.json").read_bytes()).get("retention_days")
    except (OSError, StrictJsonError, AttributeError):
        retention = None
    return {"plan": plan.name, "provision": [p.name for p in provision], "shards": [p.name for p in shards],
            "ignored": [p.name for p in entries if p.name not in moved],
            "retention_days": retention if isinstance(retention, int) and not isinstance(retention, bool)
            and retention >= 1 else None}


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.reaggregate",
                                description="Re-aggregate a finished lab run from its artifacts.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("find", help="the re-aggregation request a workflow event runs")
    s.add_argument("--event-path", required=True)
    s.add_argument("--event-name", required=True)
    s.add_argument("--github-output")
    s = sub.add_parser("sort", help="lay out a run's downloaded artifacts for lab.aggregate")
    s.add_argument("--artifacts", required=True, help="the directory download-artifact wrote")
    s.add_argument("--run-id", required=True, type=int)
    s.add_argument("--out", required=True)
    s.add_argument("--github-output")
    return p


def _outputs(path: str | None, values: dict[str, Any]) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def _fail(problem: str) -> int:
    print(f"error: {problem}", file=sys.stderr)
    print(f"::error title={NOTICE_TITLE}::{gh_data(problem)}", flush=True)
    return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "find":
        found = find(args.event_path, args.event_name, Path.cwd())
        if found.outcome == "error":
            return _fail(found.error or "event: unknown problem")
        for notice in found.notices:
            print(f"::notice title={NOTICE_TITLE}::{gh_data(notice)}", flush=True)
        if found.request is not None:
            _outputs(args.github_output, {"request": found.request["path"], "run_id": found.request["run_id"]})
            print(f"reaggregate: {found.request['path']} run {found.request['run_id']}", flush=True)
        return EXIT_OK
    out = Path(os.path.abspath(args.out))
    for path in (args.artifacts, args.out):
        name = forbidden_root(path)
        if name is not None:
            return _fail(f"artifacts: no path may lie inside {name}/")
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        return _fail("out: must be absent or an empty directory")
    if not 1 <= args.run_id <= MAX_RUN_ID:
        return _fail("run-id: must be a positive integer of at most twenty digits")
    try:
        done = sort_artifacts(Path(args.artifacts), args.run_id, out)
    except LabError as err:
        return _fail(f"{err.path}: {err.problem}")
    if done["retention_days"] is not None:
        _outputs(args.github_output, {"retention_days": done["retention_days"]})
    for name in done["ignored"]:
        print(f"lab: reaggregate ignored {name}" if shown_path(name) else "lab: reaggregate ignored an entry")
    print(f"lab: reaggregate sort plan {done['plan']} provision {len(done['provision'])} shards "
          f"{len(done['shards'])} ignored {len(done['ignored'])}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
