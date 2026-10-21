#!/usr/bin/env python3
"""Fixtures for ``retrieval/hierarchy.py``: message nodes (keyword and metadata handling, batch creation),
episode segmentation of LoCoMo sessions, topic clustering, persona synthesis, traversal, layer summaries and the
errors the manager raised. Writes ``hierarchy.json``."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import _engine as E
import _reference as R

E.install()
from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.hierarchy import HierarchyConfig, HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402


async def err(coro):
    try:
        await coro
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


async def main():
    specs = E.locomo_messages(0, [1, 2, 3, 4], max_turns=12)
    out = {"specs": specs}

    # --- main session: messages -> episodes -> topics -> persona ---------------------------------------------
    st = InMemoryNeuralGraphStorage()
    h = HierarchyManager(st, HierarchyConfig(topic_similarity_threshold=0.35))
    msgs = await E.add_messages(h, specs, "h1")
    special = await h.create_message_node(
        content="We met on 8 May 2023 and talked for two years.", session_key="h1",
        embedding=fake_embedding("We met on 8 May 2023 and talked for two years.", E.DIM),
        resolved_date="2023-05-08", temporal_tokens=[], temporal_metadata={"year": 2023}, resolved_dates={},
        explicit_date="", duration_years=0, duration_months=3, since_year=2021, since_date=None,
        speaker="Caroline", note="special")
    out["special"] = special.to_dict()
    plain = await h.create_message_node(content="no embedding here", session_key="h1")
    out["plain"] = plain.to_dict()
    out["too_few_segment"] = E.ids(await h.auto_segment_to_episodes("empty-session"))
    episodes = await h.auto_segment_to_episodes("h1")
    out["episodes"] = E.ids(episodes)
    topics = await h.auto_cluster_to_topics("h1")
    out["topics"] = E.ids(topics)
    persona = await h.synthesize_persona([t.node_id for t in topics] + [episodes[0].node_id, "missing"],
                                         "interests", "h1", tone="warm")
    out["persona"] = persona.node_id
    persona2 = await h.synthesize_persona([topics[0].node_id], "solo", "h1", description="Just one topic")
    out["persona2"] = persona2.node_id
    out["ancestors"] = {m.node_id: E.ids(await h.get_ancestors(m.node_id)) for m in msgs[::5]}
    out["ancestors_missing"] = E.ids(await h.get_ancestors("missing"))
    out["descendants"] = {str(d): E.ids(await h.get_descendants(persona.node_id, max_depth=d)) for d in range(0, 5)}
    out["siblings"] = {m.node_id: E.ids(await h.get_siblings(m.node_id)) for m in msgs[::7] + [persona, plain]}
    out["summary"] = await h.get_layer_summary("h1")
    out["graph"] = await E.dump_graph(st, "h1")
    out["errors"] = {
        "episode": await err(h.consolidate_to_episode([persona.node_id, "missing"], "h1")),
        "topic": await err(h.cluster_to_topic([msgs[0].node_id], "x", "h1")),
        "persona": await err(h.synthesize_persona([], "x", "h1")),
    }

    # --- explicit consolidation paths --------------------------------------------------------------------
    st2 = InMemoryNeuralGraphStorage()
    h2 = HierarchyManager(st2, HierarchyConfig(min_messages_per_episode=2, max_messages_per_episode=4,
                                               min_episodes_per_topic=1, topic_similarity_threshold=0.9))
    batch = await h2.create_message_nodes_batch([
        {"content": "zero weight one", "embedding": [0.0, 1.0, 2.0], "importance_score": 0.0,
         "wave_amplitudes": {"entity": 0.4, "action": 0.2}, "entity_ids": ["e1"],
         "metadata": {"resolved_date": "2023-01-02", "speaker": "A"}},
        {"content": "zero weight two", "embedding": [1.0, 3.0, -2.0], "importance_score": 0.0,
         "wave_amplitudes": {"action": 0.7, "temporal": 0.1}, "entity_ids": ["e2", "e1"]},
        {"content": "no embedding", "importance_score": 0.9},
        {},
    ], "h2")
    out["batch"] = [n.to_dict() for n in batch]
    ep_mean = await h2.consolidate_to_episode([n.node_id for n in batch], "h2", summary="", origin="manual")
    out["episode_mean"] = ep_mean.to_dict()
    ep_none = await h2.consolidate_to_episode([batch[2].node_id], "h2")
    out["episode_none"] = ep_none.to_dict()
    topic = await h2.cluster_to_topic([ep_mean.node_id, ep_none.node_id, batch[0].node_id], "label", "h2",
                                      description="", extra=1)
    out["topic_explicit"] = topic.to_dict()
    topic_noemb = await h2.cluster_to_topic([ep_none.node_id], "solo", "h2")
    out["topic_noemb"] = topic_noemb.to_dict()
    persona_noemb = await h2.synthesize_persona([topic_noemb.node_id], "p", "h2")
    out["persona_noemb"] = persona_noemb.to_dict()
    # size-limited segmentation with a later session gap; unclustered singletons stay without topic
    t0 = datetime.fromisoformat(specs[0]["created_at"])
    for i in range(11):
        await h2.create_message_node(content=f"segment message {i} about pottery", session_key="h3",
                                     embedding=fake_embedding(f"segment message {i} about pottery", E.DIM),
                                     created_at=t0 + timedelta(minutes=i if i < 7 else 600 + i))
    seg = await h2.auto_segment_to_episodes("h3")
    out["segments"] = [e.to_dict() for e in seg]
    out["segment_topics"] = [t.to_dict() for t in await h2.auto_cluster_to_topics("h3")]
    out["summary_h3"] = await h2.get_layer_summary("h3")

    # --- naive and aware timestamps in one session -------------------------------------------------------
    st3 = InMemoryNeuralGraphStorage()
    h3 = HierarchyManager(st3, HierarchyConfig(min_messages_per_episode=2))
    for i in range(3):
        await h3.create_message_node(content=f"aware {i}", session_key="mixed")
    await h3.create_message_node(content="naive", session_key="mixed", created_at=datetime(2023, 5, 8, 13, 0))
    out["mixed_error"] = await err(h3.auto_segment_to_episodes("mixed"))
    R.dump("hierarchy.json", out)


asyncio.run(main())
