"""Small worlds for the routing spike's tests: the pre-registered pack, plant and settings, fewer candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.routing_spike.hq import Candidate, HqView, plan
from research.routing_spike.world import SETTINGS, World, backup_sqlite, build_world


def small_world(workdir: str | Path, *, seed: int = 1, planted: bool = False, top_n: int = 3) -> World:
    """A world whose pipeline sites are closed (the HQ store stays open) and whose stores are copied to
    ``pristine/`` before any question, as ``run_world`` does."""
    world = build_world(seed, planted, workdir, top_n=top_n)
    for site in world.pipeline.sites.values():
        site.close()
    for sid in world.site_ids:
        backup_sqlite(world.workdir / "edge" / f"site-{sid}.sqlite3",
                      world.workdir / "pristine" / f"site-{sid}.sqlite3")
    return world


def questions(world: World) -> list[dict[str, Any]]:
    """HQ's question for each shared candidate, in (as_of, key) order."""
    view = HqView(world.pipeline.store, world.pack)
    out = []
    for c in sorted((Candidate.from_detector(c) for c in world.candidates), key=lambda c: (c.as_of, c.key)):
        question, _, _ = plan(view, world.pack, c, world.site_ids, tie_salt=SETTINGS["tie_salt"], m=SETTINGS["m"],
                              seed=world.seed)
        out.append(question)
    return out
