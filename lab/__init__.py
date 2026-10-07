"""The Mycelic cloud lab: run the collective layer's experiment harnesses on GitHub-hosted runners.

A founder writes a request (``lab/requests/<name>.json``, see ``request.py``) naming experiments and models from the
lab's model manifest (``lab/models.json``, see ``manifest.py``) and pushes it. The workflow's plan job finds the
request the push added (``discover.py``), validates it and expands it into units and shards (``plan.py``); each shard
job runs its units (``shard.py`` and ``units.py``) and seals its output directory; G3 aggregates the shards.
``dryrun.py`` runs the same plan and shard steps in this sandbox against the collective's fake OpenAI-compatible
server, so every gate is testable without model weights.

Modules: ``notes`` (every fixed sentence), ``request``, ``manifest``, ``discover``, ``plan``, ``responder`` (the fake
server's reply function), ``hostinfo``, ``units``, ``shard`` and ``dryrun``. The lab imports only the standard
library, ``mycelic`` and itself.

Exit codes, the same for every lab CLI:

=====  =================================================================================================
code   meaning
=====  =================================================================================================
0      done; every unit ran to a valid result (a harness FAIL verdict is a valid result), or nothing to run
1      a unit is invalid, failed, timed out or was interrupted, or the shard budget ran out
2      usage or configuration error: a bad request, manifest, lock, event, plan or argument
3      network error (reserved; unused in G1)
=====  =================================================================================================

Every path the lab prints goes through :func:`safe_path`, and every GitHub workflow command (``::error``,
``::notice``) through :func:`gh_data` or :func:`gh_property`, so no request value or unsafe key name is echoed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_UNIT = 1
EXIT_USAGE = 2
EXIT_NETWORK = 3

NAMED_PATHS = ("request", "event", "manifest", "lock")
_SAFE_PATH_RE = re.compile(r"\$((\.[a-z0-9_-]{1,40})|(\[[0-9]{1,6}\]))*", re.ASCII)
_SHOWN_PATH_RE = re.compile(r"[A-Za-z0-9._/-]{1,512}", re.ASCII)


class LabError(ValueError):
    """``str`` is ``"<path>: <problem>"``; the path is a JSON path or one of :data:`NAMED_PATHS`."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


def safe_path(p: str) -> str:
    """``p`` when it is a JSON path of lower-case keys and indexes, or a named path; otherwise ``$``."""
    if isinstance(p, str) and (p in NAMED_PATHS or _SAFE_PATH_RE.fullmatch(p) is not None):
        return p
    return "$"


def shown_path(p: str | None) -> str | None:
    """A file path as the lab may print it: ``p`` when it holds only ``[A-Za-z0-9._/-]``, else None."""
    if isinstance(p, str) and _SHOWN_PATH_RE.fullmatch(p) is not None:
        return p
    return None


def gh_data(s: str) -> str:
    """Escape a workflow-command message: ``%``, CR and LF."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def gh_property(s: str) -> str:
    """Escape a workflow-command property value: :func:`gh_data` plus ``:`` and ``,``."""
    return gh_data(s).replace(":", "%3A").replace(",", "%2C")


def check_keys(obj: dict[str, Any], allowed: Sequence[str], required: Sequence[str], path: str,
               error: type[LabError], unknown: str = "unknown key") -> None:
    """Unknown keys first, in sorted order (the key is named only when ``<path>.<key>`` is a safe path), then the
    required keys in the given order (``<path>.<key>: required``)."""
    for key in sorted(obj):
        if key not in allowed:
            child = f"{path}.{key}"
            if safe_path(child) == child:
                raise error(child, unknown) from None
            raise error(path, f"{unknown} (name not shown)") from None
    for key in required:
        if key not in obj:
            raise error(f"{path}.{key}", "required") from None


def display_path(path: str | os.PathLike[str], root: str | os.PathLike[str] | None = None) -> str:
    """POSIX path relative to ``root`` (default: the current directory) when inside it, else absolute. Symlinks are
    not resolved; ``..`` is normalised away."""
    absolute = Path(os.path.abspath(path))
    base = Path(os.path.abspath(root if root is not None else os.getcwd()))
    try:
        return absolute.relative_to(base).as_posix()
    except ValueError:
        return absolute.as_posix()
