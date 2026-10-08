"""The opt-in demonstration apps: simulated GitHub and Slack connected to the Platform team's memory once the demo
organization exists, named as simulated, idempotent across restarts, and off unless explicitly enabled."""
from __future__ import annotations

from pathlib import Path

from mycelic.runtime import build_runtime
from mycelic.seed import HOLDER_A, HOLDER_B, run_seed
from mycelic.seed.apps_demo import PLATFORM_MEMORY, demo_apps_enabled
from mycelic.seed.scenario import scenario_settings


async def test_demo_apps_are_opt_in_and_connect_the_platform_memory(tmp_path: Path, monkeypatch):
    s = scenario_settings(tmp_path)
    assert not demo_apps_enabled(s)                                   # off by default, even in demonstration mode
    monkeypatch.setenv("MYCELIC_DEMO_APPS", "1")
    monkeypatch.setenv("MYCELIC_DEMO_APPS_PORTS", "0,0")              # any free ports for the test
    assert demo_apps_enabled(s)
    s.demo_mode = False
    assert not demo_apps_enabled(s)                                   # never outside demonstration mode
    s.demo_mode = True
    cli = build_runtime(s)                                             # a one-shot command (seed) next to the server
    await cli.start(run_worker=False, run_holders=True)
    try:
        assert "demo_apps" not in cli.extras                          # only the server hosts the mocks (fixed ports)
    finally:
        await cli.stop()
    rt = build_runtime(s)
    await rt.start(run_worker=False, run_holders=True, run_demo_apps=True)
    try:
        await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"}, deliver_external=False)
        apps = rt.extras["demo_apps"]
        assert await apps.ensure_connected() is True
        hid = apps.connected_holder
        h = rt.org.get_holder(hid)
        assert h["name"] == PLATFORM_MEMORY and h["owner_type"] == "unit" and h["mode"] == "embedded"
        cons = rt.holders.ingest(hid).pipeline.db.list_connectors()
        assert sorted(c["connector_type"] for c in cons) == ["github", "slack"] and all("simulated" in c["display_name"] for c in cons)
        recs = rt.holders.ingest(hid).pipeline.db.conn.execute("SELECT source_app, COUNT(*) AS n FROM ingest_records GROUP BY source_app").fetchall()
        assert {r["source_app"] for r in recs} == {"github", "slack"}
        # the included sources are mapped to tenant domains, so the memory publishes routable domains from its records
        mapped = rt.holders.ingest(hid).pipeline.db.conn.execute(
            "SELECT DISTINCT dm.domain_id FROM domain_memberships dm WHERE dm.status='active' AND dm.method='source_mapping'").fetchall()
        assert {r["domain_id"] for r in mapped} >= {"engineering", "infrastructure.ci-cd"}
        assert len(rt.db.all("SELECT connector_id FROM connector_registry WHERE holder_id=?", (hid,))) == 2
        assert await apps.ensure_connected() is True                  # idempotent
        assert len(rt.holders.ingest(hid).pipeline.db.list_connectors()) == 2
    finally:
        await rt.stop()
