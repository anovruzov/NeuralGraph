"""Which request a workflow event asks to run, decided from the event file and the checked-out repository.

``workflow_dispatch``: ``inputs.request`` names the file, which must match ``lab/requests/<name>.json`` and exist at
the checked-out ref as a regular file (not a symlink).

``push``: the request the push added or changed, found with the rule GitHub's ``paths`` filter applies:

1. ``after`` and ``before`` are 40-hex ids, ``ref`` a string, ``deleted`` a bool, ``repository.default_branch`` a
   string; anything else is a usage error (exit 2).
2. A deleted branch, a non-branch ref (a tag) and a push to the default branch run nothing (exit 0 with a notice).
3. ``HEAD`` must be ``after`` (the plan reads the pushed tree).
4. The base commit is ``before`` when it exists in the clone; otherwise (a new branch, or a force push whose old tip
   was not fetched) the single boundary commit of ``git rev-list --boundary <after> --not
   --exclude=origin/<branch> --remotes=origin``; with no or several boundary commits (a new branch whose tip is a
   merge), ``git merge-base origin/<default branch> <after>``; with none of these, a usage error.
5. ``git diff --no-renames --name-status -z <base> <after> -- ':(glob)lab/requests/*.json'`` lists the request files
   the push touched (subdirectories are outside the glob; a rename is a delete plus an add). Added, modified and
   type-changed files count as changed, deleted files as deleted.

Exactly one changed file runs (a changed file whose name breaks the request pattern is a usage error and is never
printed). Only deletions run nothing (exit 0). No request change at all is a usage error (exit 2): the workflow's
path filter and this rule disagree. Several changed files run nothing when the tip is a merge (exit 0, one dispatch
command per request, the paths in :attr:`Discovery.requests`) and are a usage error otherwise. Every value comes
from the event file, never from a workflow expression, and git runs without the environment variables that change
pathspec or repository meaning.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import StrictJsonError, strict_load

from . import EXIT_OK, EXIT_USAGE, LabError, safe_path
from .notes import (BAD_REQUEST_NAME, BRANCH_DELETED, CHECKOUT_MISMATCH, DEFAULT_BRANCH, DELETE_ONLY, DISPATCH_HINT,
                    GIT_FAILED, MERGE_SEVERAL, NO_BASE, NO_REQUEST_CHANGE, NOT_A_BRANCH, SEVERAL_REQUESTS)

REQUEST_PATH_RE = re.compile(r"lab/requests/[a-z0-9][a-z0-9-]{0,39}\.json", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
ZERO_SHA = "0" * 40
EVENTS = ("push", "workflow_dispatch")
WORKFLOW_FILE = "mycelic-lab.yml"
REQUEST_GLOB = ":(glob)lab/requests/*.json"
GIT_TIMEOUT_S = 30
GIT_ENV_DROPPED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
                   "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")
CHANGED = ("A", "M", "T")
DELETED = ("D",)


class DiscoveryError(LabError):
    """A usage error of the event; ``hints`` are further lines to print (dispatch commands)."""

    def __init__(self, path: str, problem: str, hints: list[str] | None = None) -> None:
        super().__init__(path, problem)
        self.hints = list(hints or [])


@dataclass(frozen=True)
class Discovery:
    outcome: str                           # run | nothing | error
    request: str | None
    notices: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    exit_code: int = EXIT_OK
    error: DiscoveryError | None = None
    requests: list[str] = field(default_factory=list)     # the request paths of a merge that brought several


def dispatch_command(branch: str, path: str) -> str:
    return f"gh workflow run {WORKFLOW_FILE} --ref {shlex.quote(branch)} -f request={path}"


# --------------------------------------------------------------------------------------------------- git

def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROPPED}
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                              timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise DiscoveryError("event", GIT_FAILED) from None


def _git_ok(root: Path, *args: str) -> str:
    r = git(root, *args)
    if r.returncode != 0:
        raise DiscoveryError("event", GIT_FAILED) from None
    return r.stdout


def _base(root: Path, before: str, after: str, branch: str, default_branch: str) -> str | None:
    if before != ZERO_SHA and git(root, "cat-file", "-e", f"{before}^{{commit}}").returncode == 0:
        return before
    listed = git(root, "rev-list", "--boundary", after, "--not", f"--exclude=origin/{branch}", "--remotes=origin")
    if listed.returncode == 0:
        boundary = [line[1:] for line in listed.stdout.splitlines() if line.startswith("-")]
        if len(boundary) == 1 and SHA_RE.fullmatch(boundary[0]):
            return boundary[0]
    merged = git(root, "merge-base", f"origin/{default_branch}", after)
    sha = merged.stdout.strip()
    return sha if merged.returncode == 0 and SHA_RE.fullmatch(sha) else None


def _diff(root: Path, base: str, after: str) -> list[tuple[str, str]]:
    out = _git_ok(root, "diff", "--no-color", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, after,
                  "--", REQUEST_GLOB)
    fields = out.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise DiscoveryError("event", GIT_FAILED) from None
    return [(fields[i], fields[i + 1]) for i in range(0, len(fields), 2)]


# --------------------------------------------------------------------------------------------------- events

def _field(event: dict[str, Any], path: str, ok: Any, problem: str) -> Any:
    node: Any = event
    for name in path.split(".")[1:]:
        node = node.get(name) if isinstance(node, dict) else None
    if not ok(node):
        raise DiscoveryError(path, problem) from None
    return node


def _is_sha(v: Any) -> bool:
    return isinstance(v, str) and SHA_RE.fullmatch(v) is not None


def _push(event: dict[str, Any], root: Path) -> Discovery:
    after = _field(event, "$.after", _is_sha, "must be a 40-hex commit id")
    before = _field(event, "$.before", _is_sha, "must be a 40-hex commit id")
    ref = _field(event, "$.ref", lambda v: isinstance(v, str), "must be a string")
    deleted = _field(event, "$.deleted", lambda v: isinstance(v, bool), "must be a bool")
    default_branch = _field(event, "$.repository.default_branch", lambda v: isinstance(v, str) and v != "",
                            "must be a non-empty string")
    if deleted:
        return Discovery("nothing", None, notices=[BRANCH_DELETED])
    if not ref.startswith("refs/heads/") or ref == "refs/heads/":
        return Discovery("nothing", None, notices=[NOT_A_BRANCH])
    branch = ref[len("refs/heads/"):]
    if branch == default_branch:
        return Discovery("nothing", None, notices=[DEFAULT_BRANCH])
    if _git_ok(root, "rev-parse", "HEAD").strip() != after:
        raise DiscoveryError("event", CHECKOUT_MISMATCH) from None
    base = _base(root, before, after, branch, default_branch)
    if base is None:
        raise DiscoveryError("event", NO_BASE, [DISPATCH_HINT, dispatch_command(branch, "lab/requests/<name>.json")])
    changed, deleted_paths = [], []
    for status, path in _diff(root, base, after):
        if status in CHANGED:
            changed.append(path)
        elif status in DELETED:
            deleted_paths.append(path)
    valid = [p for p in changed if REQUEST_PATH_RE.fullmatch(p)]
    if len(changed) == 1:
        if not valid:
            raise DiscoveryError("event", BAD_REQUEST_NAME) from None
        return Discovery("run", valid[0])
    if not changed:
        if deleted_paths:
            return Discovery("nothing", None, notices=[DELETE_ONLY])
        raise DiscoveryError("event", NO_REQUEST_CHANGE,
                             [DISPATCH_HINT, dispatch_command(branch, "lab/requests/<name>.json")])
    commands = [dispatch_command(branch, p) for p in valid]
    parents = _git_ok(root, "rev-list", "--parents", "-n", "1", after).split()
    if len(parents) >= 3:
        return Discovery("nothing", None, notices=[MERGE_SEVERAL, *commands], requests=valid)
    raise DiscoveryError("event", SEVERAL_REQUESTS, [*valid, DISPATCH_HINT, *commands])


def _dispatch(event: dict[str, Any], root: Path) -> Discovery:
    path = _field(event, "$.inputs.request", lambda v: isinstance(v, str) and REQUEST_PATH_RE.fullmatch(v),
                  "must be lab/requests/<name>.json")
    try:
        mode = os.lstat(root / path).st_mode
    except OSError:
        mode = 0
    if not stat.S_ISREG(mode):
        raise DiscoveryError("$.inputs.request", "not found at the checked-out ref") from None
    return Discovery("run", path)


def discover(event_path: str | os.PathLike[str], event_name: str, root: str | os.PathLike[str]) -> Discovery:
    """The :class:`Discovery` for one event; a usage problem comes back as outcome ``error`` with exit code 2."""
    try:
        if event_name not in EVENTS:
            raise DiscoveryError("event", "unsupported event (push or workflow_dispatch)") from None
        try:
            data = Path(event_path).read_bytes()
        except OSError:
            raise DiscoveryError("event", "cannot read the event file") from None
        try:
            event = strict_load(data)
        except StrictJsonError as err:
            raise DiscoveryError(safe_path(err.path), f"invalid event JSON ({err.reason})") from None
        if not isinstance(event, dict):
            raise DiscoveryError("$", "the event must be an object") from None
        root = Path(root)
        return _push(event, root) if event_name == "push" else _dispatch(event, root)
    except DiscoveryError as err:
        return Discovery("error", None, errors=[f"{err.path}: {err.problem}", *err.hints], exit_code=EXIT_USAGE,
                         error=err)
