"""The reply function the lab hands the collective's fake OpenAI-compatible server in plumbing runs.

:class:`Responder` is called with each chat request's parsed JSON and dispatches on the schema name the runtime
sends (``response_format.json_schema.name``, which is the task name):

* ``extract_claims``: the pack's lexical extractor, as model reply items (``edge.extract.lexical_handler``);
* ``judge_record``: the pack's lexical judge (``edge.verify.lexical_judge``);
* ``e3_extraction_like`` and ``e3_short_answer``: a fixed reply that satisfies the E3 workload schema.

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
from mycelic.collective.inference.fakeserver import request_payload
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import FrozenPack

E3_REPLIES = {
    WORKLOADS["extraction"][0].name: {"facts": ["the text lists routine equipment checks"]},
    WORKLOADS["short"][0].name: {"answer": "The text is routine maintenance filler."},
}


class ResponderError(Exception):
    def __init__(self) -> None:
        super().__init__("the lab responder cannot answer this request")


class Responder:
    def __init__(self, pack: FrozenPack | None) -> None:
        self.pack = pack
        self._handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {}
        if pack is not None:
            canonicaliser = Canonicaliser(pack)
            self._handlers[TASK_NAME] = lexical_handler(pack, canonicaliser)
            self._handlers[JUDGE_TASK] = lexical_judge(pack, canonicaliser)

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
        handler = self._handlers.get(name)
        if handler is None:
            raise ResponderError()
        return handler(payload)
