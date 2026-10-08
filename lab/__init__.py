"""The Mycelic cloud lab: run the collective layer's experiment harnesses on GitHub-hosted runners.

A founder writes a request (``lab/requests/<name>.json``, see ``request.py``) naming experiments and models from the
lab's model manifest (``lab/models.json``, see ``manifest.py``) and pushes it. The workflow's plan job finds the
request the push added (``discover.py``), validates it and expands it into units, shards and the files to provision
(``plan.py``), and preregisters what E1, X1 and E2 will be judged against before any model runs (``prereg.py``:
labels, harness preregistrations and a model-free E2 rehearsal, hashed into a manifest every later job verifies); the
provision matrix downloads and verifies the pinned server archive and model files once per run
(``provision.py`` over ``download.py``); each shard job restores exactly the cached files its run's provision
records name, prepares its server binary from them, runs its units against the model server it starts on loopback
(``shard.py``, ``server.py``, ``warmup.py``, ``units.py``) by a deadline taken from the job's own clock, and seals
its output directory; the aggregate job merges the shards' sealed artifacts into one report (``aggregate.py``).
Every job writes a step summary (``summary.py``) whose numbers are all read from run files, and whose first line
says when nothing in it measures a model. ``dryrun.py`` runs the same plan, shard, aggregate and summary steps in
this sandbox against the collective's fake OpenAI-compatible server, so every gate is testable without model
weights; the workflow is ``.github/workflows/mycelic-lab.yml``.

Modules: ``notes`` (every fixed sentence), ``request``, ``manifest``, ``discover``, ``plan``, ``goldlabels`` (E1's
generator or fixture labels), ``prereg``, ``download``, ``provision``, ``server``, ``warmup``, ``responder`` (the
fake server's reply function), ``hostinfo``, ``hosted`` (the optional OpenAI-compatible hosted provider: its two
secrets, routing, preflight and call shares), ``units`` (the harness adapters: E1, E2, E3, G0, sim, X1, openFDA),
``openfda`` (the openFDA unit's fetch, replay and sheet steps), ``shard``, ``sim`` (the multi-site simulation harness
the lab adds to the collective's), ``aggregate``, ``summary`` and ``dryrun``; ``plants/`` holds the lab's plant
specs. The lab imports only the standard library, ``mycelic`` and itself.

Integration notes for the collective layer (the lab never edits ``mycelic/``; each is a hook or a risk to resolve when
the branches merge):

1. ``experiments.e2_pushdown`` lets an ``InferenceError`` from a central or site call escape: it exits 1 with a
   traceback. The lab maps that exit to :data:`~lab.notes.E2_ABORTED` and keeps the ledgers it wrote.
2. ``lab.warmup.e2_worst_payloads`` mirrors the private payload shape of ``e2_pushdown._central_raw``; a test pins it
   against the harness's own requests, and a public payload builder upstream would remove the copy.
3. E2's site ledgers live under the run's ``work/seed-*/edge/``; the lab collects exactly those ledgers from ``work/``.
4. ``e1_extract compare --run-dirs`` is the one harness option taking several values, so its directories follow it as
   separate argv elements (absolute, lab-made paths); every other option is one ``--flag=value`` element.
5. Audit r2's verifier and extractor circuit breaker changes the judge call counts the E2 projection multiplies, and
   the runtime's checked synthetic exemptions are a merge risk for the lab's synthetic-labelled runs: re-run the
   all-experiments dry run (``tests/lab/data/requests/all-experiments.json``) on the trial merge.
6. E2's central task has ``max_tokens`` 64. A hosted reasoning model can exhaust it on thinking; the hosted preflight
   catches a ``finish_reason`` of ``length`` on the canary and fails the key (:data:`~lab.notes.HOSTED_LENGTH`).
7. The client always sends ``temperature`` 0 and, for ``json_schema``, a strict schema. A hosted model that refuses
   either fails the preflight with HTTP 400 (the remedy names ``json_object`` and ``none``, which helps only for the
   schema); a runtime hook to omit ``temperature`` would admit those models.
8. ``e1_extract run`` writes the host's whole ``/models`` listing into ``run.json`` (``models_listed``), which is part
   of a public artifact and may list account-specific model ids; ``e2.json`` records ``central_routing_sha256`` over
   the scratch routing file that holds the base URL (a hash, not the URL). A hook that records only whether the
   requested model was listed would remove the first.
9. The hosted call bound (``lab.request``, ``lab.plan``) assumes one repair per ``runtime.run`` call and at most
   ``top_n`` central candidates per seed: re-check it on the trial merge with the audit's circuit breaker changes.

GitHub Models is not built: the lab has no code for it and the workflow asks for no ``models`` permission.

Exit codes, the same for every lab CLI:

=====  =================================================================================================
code   meaning
=====  =================================================================================================
0      done; every unit ran to a valid result (a harness FAIL verdict is a valid result), or nothing to run
1      a unit is invalid, failed, timed out, interrupted or skipped (budget, server or warm-up)
2      usage or configuration error: a bad request, manifest, lock, event, plan or argument
3      network error: a download or API that stayed unreachable, or a download past its deadline
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
FORBIDDEN_ROOTS = ("mycelic", "research", "NeuralGraph", ".github")
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


def forbidden_root(path: str | os.PathLike[str]) -> str | None:
    """The name of the repository directory in :data:`FORBIDDEN_ROOTS` that ``path`` is or lies inside (symlinks
    resolved), else None. No lab output, cache or scratch directory may live there."""
    resolved = Path(path).resolve()
    for name in FORBIDDEN_ROOTS:
        root = (ROOT / name).resolve()
        if resolved == root or root in resolved.parents:
            return name
    return None


def display_path(path: str | os.PathLike[str], root: str | os.PathLike[str] | None = None) -> str:
    """POSIX path relative to ``root`` (default: the current directory) when inside it, else absolute. Symlinks are
    not resolved; ``..`` is normalised away."""
    absolute = Path(os.path.abspath(path))
    base = Path(os.path.abspath(root if root is not None else os.getcwd()))
    try:
        return absolute.relative_to(base).as_posix()
    except ValueError:
        return absolute.as_posix()
