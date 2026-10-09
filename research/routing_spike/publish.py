"""Publishing to the fabric: verdict events and conclusions with lineage (``docs/collective/ROUTING-SPIKE.md`` 1.6).

One in-process fabric service (``MycelicService`` over the in-process transport) per seed and world. One organisation
per route label (arm, and one per random subset), so the fabric's consolidation never mixes arms. One agent per
organisation, ``hq-<label>`` (agent ids are unique in a fabric service, so the agent HQ publishes as carries its
organisation's label). Per candidate and route:

1. one ``collective.verdict`` agent event per routed site, whose payload holds the site, question id, verdict id,
   verdict, buckets, newest week, window and the sha256 of the verdict bytes as HQ received them (an HQ ``unknown``
   record has no sha256);
2. then one ``collective.conclusion`` agent event with an embedded memory: ``entity`` the entity id, ``slot`` the
   predicate, ``value`` the gate status, ``topic`` ``collective/<pack id>``, ``kind`` ``risk``, ``source_event_ids`` the
   verdict events of step 1, and metadata with the entity type, candidate key, question id, conclusion id, ``as_of``,
   snapshot week, route label and routed sites. Its text is HQ's own sentence built from the question's parameters.

Every conclusion is published, whatever its status. This module loads the fabric service, which loads numpy through
``NeuralGraph.chat_memory`` (the chain the fabric guard documents); it loads neither the backend module nor the
retrieval package.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.config import Settings
from mycelic.metrics import Metrics
from mycelic.service import MycelicService
from mycelic.transport import InProcessTransport

VERDICT_EVENT = "collective.verdict"
CONCLUSION_EVENT = "collective.conclusion"
ADMIN_TOKEN = "routing-spike-admin-token-0123456789abcdef"


def org_of(label: str) -> str:
    return f"rs-{label.lower()}"


def agent_of(label: str) -> str:
    return f"hq-{label.lower()}"


def conclusion_text(pack: Any, question: Mapping[str, Any], status: str) -> str:
    params, window = question["params"], question["window"]
    return (f"Pushdown conclusion for {pack.entity_types[params['entity_type']].label} {params['entity_id']}, "
            f"{pack.predicates[params['predicate']].label}, weeks {window['start_week']} to {window['end_week']}: "
            f"{status}.")


class Fabric:
    """One fabric service for one world."""

    def __init__(self, db_path: str | Path, pack: Any) -> None:
        settings = Settings(host="127.0.0.1", port=0, db_path=str(db_path), nats_url=None, admin_token=ADMIN_TOKEN,
                            rate_limit_rps=0.0, min_support=2, publish_interval_seconds=0.05)
        settings.validate()
        self.pack = pack
        self.service = MycelicService(settings, transport=InProcessTransport(), metrics=Metrics())
        self._keys: dict[str, str] = {}

    async def start(self, labels: Sequence[str]) -> None:
        await self.service.start()
        for label in labels:
            _, key = await self.service.register_agent({"enterprise": org_of(label), "agent_id": agent_of(label)})
            self._keys[label] = key

    def principal(self, label: str) -> Any:
        return self.service.authenticate(f"Bearer {self._keys[label]}")

    async def publish(self, label: str, question: Mapping[str, Any], *, entity_type: str, candidate_key: str,
                      snapshot_week: str, as_of: str, route: Sequence[str], received: Sequence[Any],
                      status: str) -> dict[str, Any]:
        """The verdict events, then the conclusion event with its memory; returns the ids."""
        principal = self.principal(label)
        qid = question["question_id"]
        conclusion_id = "c-" + qid[:32]
        items = []
        for r in received:
            body = r.body
            items.append({"type": VERDICT_EVENT, "idempotency_key": f"{label}:{qid}:{r.site}:verdict",
                          "payload": {"site": r.site, "question_id": qid, "source": r.source,
                                      "verdict_id": body.get("verdict_id"), "verdict": body["verdict"],
                                      "reason": body.get("reason"), "support_bucket": body.get("support_bucket"),
                                      "roots_bucket": body.get("roots_bucket"),
                                      "reporters_bucket": body.get("reporters_bucket"),
                                      "newest_week": body.get("newest_week"), "window": dict(question["window"]),
                                      "sha256": r.sha256}})
        verdicts = await self.service.ingest_events(principal, items) if items else []
        verdict_ids = [v["event_id"] for v in verdicts]
        params = question["params"]
        memory = {"text": conclusion_text(self.pack, question, status), "entity": params["entity_id"],
                  "slot": params["predicate"], "value": status, "topic": f"collective/{self.pack.id}",
                  "kind": "risk", "visibility": "team", "source_event_ids": verdict_ids,
                  "metadata": {"entity_type": entity_type, "entity_id": params["entity_id"],
                               "predicate": params["predicate"], "candidate_key": candidate_key,
                               "question_id": qid, "conclusion_id": conclusion_id, "as_of": as_of,
                               "snapshot_week": snapshot_week, "route_label": label, "routed_sites": list(route)}}
        conclusion = await self.service.ingest_events(principal, [{
            "type": CONCLUSION_EVENT, "idempotency_key": f"{label}:{qid}:conclusion",
            "payload": {"question_id": qid, "conclusion_id": conclusion_id, "status": status,
                        "route_label": label, "routed_sites": list(route), "verdict_event_ids": verdict_ids},
            "memory": memory}])
        return {"verdict_event_ids": verdict_ids, "conclusion_event_id": conclusion[0]["event_id"],
                "memory_id": conclusion[0].get("memory_id")}

    async def close(self, timeout: float = 600.0) -> bool:
        idle = await self.service.wait_idle(timeout)
        await self.service.close()
        return idle
