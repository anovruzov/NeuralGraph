"""Questions: the narrow structured question HQ asks the sites about one candidate (G6).

A question body is exactly the Boundary's ``question`` artifact (``edge/egress.py``): ``schema_version``, ``pack``,
``pack_hash``, ``question_id``, ``candidate_key``, ``template_id``, ``params {entity_type, entity_id, predicate}``,
``window {start_week, end_week}`` and ``as_of``. Its display text (:func:`render_text`) is for HQ's screens only and
is never sent.

Rules:

* **Window** (:func:`question_window`): it ends at the last week closed at ``as_of`` (the pack's
  ``close_lag_days``) and starts at the earlier of the candidate's window start and the week ``min_window_weeks - 1``
  weeks before the end, counted on ISO Mondays, so week 53 and year ends are handled (a window closing 2026-W53 with
  6 weeks starts 2026-W48; one closing 2027-W01 starts 2026-W49).
* **Template** (:func:`select_template`): the first template id in sorted order whose ``entity_types`` hold the type
  and whose ``predicates`` are null or hold the predicate; a template restricted to some predicates is never chosen
  for another.
* **Params** (:func:`build_question`) are checked before anything is built: an egress entity type, a canonical id (an
  alias-only type's id must be one of its ids), a pack predicate. The body is then checked with the Boundary's own
  validator, so a question HQ builds is exactly one a site accepts (apart from the site clock's look-ahead check).

:class:`PushdownError` names a JSON path and a fixed problem and never holds a value. Pure: no clock, no I/O.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Mapping

from ..detect.rules import series_key
from ..edge.egress import ENTITY_ID_RE, SCHEMA_VERSION, check_artifact, question_id
from ..edge.weeks import closed_through, iso_week, local_date, valid_week, week_monday

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack


class PushdownError(ValueError):
    """``str`` is ``pushdown: <path>: <problem>``; the path is built from key names only."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"pushdown: {path}: {problem}")
        self.path = path
        self.problem = problem


def weeks_back(week: str, n: int) -> str:
    """The ISO week ``n`` weeks before ``week``."""
    return iso_week(week_monday(week) - timedelta(weeks=n))


def window_weeks(window: Mapping[str, str]) -> int:
    """How many ISO weeks ``[start_week, end_week]`` spans."""
    return (week_monday(window["end_week"]) - week_monday(window["start_week"])).days // 7 + 1


def question_window(pack: "FrozenPack", *, as_of: str, candidate_window_start: str | None = None) -> dict[str, str]:
    if not isinstance(as_of, str) or local_date(as_of) != as_of:
        raise PushdownError("$.as_of", "not a calendar date") from None
    end = closed_through(as_of, pack.egress.close_lag_days)
    floor = weeks_back(end, pack.egress.min_window_weeks - 1)
    if candidate_window_start is not None and not valid_week(candidate_window_start):
        raise PushdownError("$.window.start_week", "not an ISO week") from None
    start = floor if candidate_window_start is None else min(candidate_window_start, floor)
    return {"start_week": start, "end_week": end}


def select_template(pack: "FrozenPack", entity_type: str, predicate: str) -> str:
    for template_id in sorted(pack.questions):
        template = pack.questions[template_id]
        if entity_type in template.entity_types and (template.predicates is None or predicate in template.predicates):
            return template_id
    raise PushdownError("$.entity_type", "no question template") from None


def check_params(pack: "FrozenPack", entity_type: Any, entity_id: Any, predicate: Any) -> None:
    """An egress entity type, an id in its canonical form, a pack predicate; else :class:`PushdownError`."""
    if not isinstance(entity_type, str) or entity_type not in pack.egress.egress_entity_types:
        raise PushdownError("$.entity_type", "not an egress entity type") from None
    et = pack.entity_types[entity_type]
    canonical = isinstance(entity_id, str) and ENTITY_ID_RE.fullmatch(entity_id) is not None and (
        entity_id in et.ids if et.id_format is None else et.id_format.is_canonical(entity_id))
    if not canonical:
        raise PushdownError("$.entity_id", "not canonical") from None
    if not isinstance(predicate, str) or predicate not in pack.predicates:
        raise PushdownError("$.predicate", "unknown predicate") from None


def build_question(pack: "FrozenPack", *, entity_type: str, entity_id: str, predicate: str,
                   window: Mapping[str, str], as_of: str) -> dict[str, Any]:
    """The question body; the params are checked first and nothing is built when one is refused."""
    check_params(pack, entity_type, entity_id, predicate)
    template_id = select_template(pack, entity_type, predicate)
    params = {"entity_type": entity_type, "entity_id": entity_id, "predicate": predicate}
    key = series_key(entity_type, entity_id, predicate)
    win = {"start_week": window["start_week"], "end_week": window["end_week"]}
    body = {"schema_version": SCHEMA_VERSION, "pack": pack.id, "pack_hash": pack.config_hash,
            "question_id": question_id(key, template_id, params, win), "candidate_key": key,
            "template_id": template_id, "params": params, "window": win, "as_of": as_of}
    found = check_artifact(pack, "", "question", body)
    if found is not None:
        raise PushdownError(found[0], f"fails the question schema ({found[1]})") from None
    return body


def render_text(pack: "FrozenPack", body: Mapping[str, Any]) -> str:
    """The question's template text for HQ's display; never sent to a site."""
    params = body["params"]
    template = pack.questions[body["template_id"]]
    return template.text.format(window=f"{window_weeks(body['window'])} weeks",
                                predicate_label=pack.predicates[params["predicate"]].label,
                                entity_type_label=pack.entity_types[params["entity_type"]].label,
                                entity_id=params["entity_id"])
