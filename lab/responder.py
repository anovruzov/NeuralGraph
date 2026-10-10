"""The reply function the lab hands the collective's fake OpenAI-compatible server in plumbing runs.

:class:`Responder` is called with each chat request's parsed JSON and dispatches on the schema name the runtime
sends (``response_format.json_schema.name``, which is the task name):

* ``extract_claims``: the pack's lexical extractor, as model reply items (``edge.extract.lexical_handler``);
* ``judge_record``: the pack's lexical judge (``edge.verify.lexical_judge``); with ``pack_free_judge`` and no pack (an
  L1 unit, whose pack is drafted inside the unit), :func:`pack_free_judge`: ``mentions_entity`` yes, and
  ``describes_predicate`` yes exactly when a word of the question's predicate label (four or more letters, folded)
  is a word of the record's text (a plumbing answer, not a judgement);
* ``e3_extraction_like`` and ``e3_short_answer``: a fixed reply that satisfies the E3 workload schema;
* ``judge_candidate_raw`` and ``judge_candidate_allowed`` (E2's central comparator): ``{"score": <the payload's
  canonical sha256 as an integer, mod 101>}``, deterministic and pack-free (a plumbing answer, not a judgement).

A request without a well-formed ``response_format``, with an unknown name, without a task payload, or a pack task
when the responder has no pack, raises :class:`ResponderError` (a fixed message; nothing from the request). The fake
server then drops the connection, the client records a failed call, and the participation check makes the unit
invalid, so a broken responder can never pass for a working model.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from mycelic.collective.edge.extract import TASK_NAME, lexical_handler
from mycelic.collective.edge.verify import JUDGE_TASK, lexical_judge
from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.experiments.e2_pushdown import CENTRAL_TASKS
from mycelic.collective.inference.fakeserver import request_payload
from mycelic.collective.jsonio import canonical_bytes, sha256_hex
from mycelic.collective.packs.canonical import Canonicaliser, folded
from mycelic.collective.packs.loader import FrozenPack

E3_REPLIES = {
    WORKLOADS["extraction"][0].name: {"facts": ["the text lists routine equipment checks"]},
    WORKLOADS["short"][0].name: {"answer": "The text is routine maintenance filler."},
}


class ResponderError(Exception):
    def __init__(self) -> None:
        super().__init__("the lab responder cannot answer this request")


def pack_free_judge(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A plumbing judge that needs no pack (see the module docstring)."""
    words = {w for w in folded(str(payload["question"]["predicate_label"])).split() if len(w) >= 4 and w.isalpha()}
    text = set(folded(str(payload["record"]["text"])).split())
    return {"mentions_entity": "yes", "describes_predicate": "yes" if words & text else "no"}


class Responder:
    def __init__(self, pack: FrozenPack | None, *, pack_free_judge: bool = False) -> None:
        self.pack = pack
        self._handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {}
        if pack is not None:
            canonicaliser = Canonicaliser(pack)
            self._handlers[TASK_NAME] = lexical_handler(pack, canonicaliser)
            self._handlers[JUDGE_TASK] = lexical_judge(pack, canonicaliser)
        elif pack_free_judge:
            self._handlers[JUDGE_TASK] = globals()["pack_free_judge"]

    def __call__(self, request_json: Any) -> dict[str, Any]:
        fmt = request_json.get("response_format") if isinstance(request_json, dict) else None
        spec = fmt.get("json_schema") if isinstance(fmt, dict) and fmt.get("type") == "json_schema" else None
        name = spec.get("name") if isinstance(spec, dict) else None
        if not isinstance(name, str):
            raise ResponderError()
        payload = request_payload(request_json)
        if not isinstance(payload, dict):
            raise ResponderError()
        if name in E3_REPLIES:
            return dict(E3_REPLIES[name])
        if name in CENTRAL_TASKS:
            return {"score": int(sha256_hex(canonical_bytes(payload)), 16) % 101}
        handler = self._handlers.get(name)
        if handler is None:
            raise ResponderError()
        return handler(payload)
