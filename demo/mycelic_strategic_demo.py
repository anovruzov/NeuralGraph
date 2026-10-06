#!/usr/bin/env python
"""Mycelic strategic-synthesis demo: a conclusion nobody in the organization could reach alone.

    python demo/mycelic_strategic_demo.py                     # nats-server + service as local processes
    python demo/mycelic_strategic_demo.py --driver compose    # against `docker compose`
    python demo/mycelic_strategic_demo.py --keep              # leave the deployment running at the end

Two regions of Northwind Robotics, EMEA and APAC, each independently run into the same trouble with the SD-9
servo drive: a port strike, slipping carrier ETAs, a supplier with two weeks of stock, committed customer
orders.  Each region's agents share their notes; Mycelic composes a *regional* supply-risk conclusion in each
region.  Head office knows two other things: order intake is up 40% and Kessler is the only qualified source.
Nobody holds all of it.  The rule ``strategic_second_source`` fires at the enterprise once supply risk is
corroborated in at least two regions: "qualify a second source".  Its lineage spans three layers in two
derivation steps: enterprise strategy <- regional conclusions <- agents' observations, resting on 8 of the 14
agents (the strongest note per slot in each region plus the two head-office notes) in 8 teams across 3 regions.

The demo then shows that strategy follows evidence (retract APAC's supplier notes: the regional conclusion and
the strategy are withdrawn; new evidence brings them back as a new version), that an agent sees its own
region's conclusion and its own team's notes while everything else in the lineage is redacted rather than
dropped, and that a rebuild from the event log reproduces the whole chain.
Every number printed is read from the live deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mycelic.harness import ComposeDriver, Driver, ProcessDriver  # noqa: E402
from mycelic.sdk import MycelicClient  # noqa: E402
from tests.smoke.mycelic_smoke import wait_idle  # noqa: E402
from tests.smoke.scenario import ENTERPRISE, ENTITY  # noqa: E402
from tests.smoke.scenario_strategic import HQ, REGIONS, STRATEGIC_QUERY, TEAMS, StrategicScenario  # noqa: E402

C = {"h": "\033[1;36m", "b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "x": "\033[0m"}
OUT = Path(__file__).resolve().parent / "results" / "mycelic-strategic"


def head(text: str) -> None:
    print(f"\n{C['h']}=== {text}{C['x']}", flush=True)


def say(text: str, kind: str = "") -> None:
    prefix = {"ok": f"{C['g']}  ✓{C['x']}", "warn": f"{C['y']}  !{C['x']}", "bad": f"{C['r']}  ✗{C['x']}", "": "   "}[kind]
    print(f"{prefix} {text}", flush=True)


def clip(t: str, n: int = 110) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def leaf(scope: str) -> str:
    return scope.rsplit("/", 1)[-1]


def tree(g: dict[str, Any], root: str, indent: str = "", seen: set | None = None) -> list[str]:
    seen = seen if seen is not None else set()
    n = g["nodes"][root]
    who = "redacted" if n["redacted"] else (n["producer_id"] if n["layer"] == "agent" else f"{n['operator']}{' ' + n['rule_id'] if n['rule_id'] else ''}")
    lines = [f"{indent}[{n['layer']}] {leaf(n['scope'])}  ({who}, confidence {n['confidence']}, support {n['support']})",
             f"{indent}   {C['d']}{'[text withheld: outside your visibility]' if n['redacted'] else clip(n['text'], 96)}{C['x']}"]
    if root in seen:
        return lines[:1] + [f"{indent}   {C['d']}(shown above){C['x']}"]
    seen.add(root)
    parents = sorted(e["parent"] for e in g["edges"] if e["child"] == root)
    for i, pid in enumerate(parents):
        lines.extend(tree(g, pid, indent + ("   │ " if i < len(parents) - 1 else "     "), seen))
    return lines


def mermaid(g: dict[str, Any]) -> str:
    out = ["graph BT"]
    for mid, n in g["nodes"].items():
        text = "redacted" if n["redacted"] else clip(n["text"], 60).replace('"', "'")
        out.append(f'  {mid}["{n["layer"]}: {text}"]')
    for e in g["edges"]:
        out.append(f"  {e['parent']} --> {e['child']}")
    return "\n".join(out) + "\n"


def strategic_answer(client: MycelicClient) -> dict[str, Any]:
    res = client.query(STRATEGIC_QUERY, scope=ENTERPRISE, min_layer="enterprise", k=5)
    hits = [h for h in res["results"] if h["memory"].get("rule_id") == "strategic_second_source"]
    return {"res": res, "hit": hits[0]["memory"] if hits else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--driver", choices=["process", "compose"], default="process")
    ap.add_argument("--agents-per-team", type=int, default=2)
    ap.add_argument("--pace", type=float, default=0.6)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    pause = lambda: time.sleep(args.pace)  # noqa: E731
    extra = {"CA_BUNDLE_FILE": os.environ["MYCELIC_BUILD_CA_BUNDLE"]} if os.environ.get("MYCELIC_BUILD_CA_BUNDLE") else None
    driver: Driver = ProcessDriver() if args.driver == "process" else ComposeDriver(project="mycelic-strategic", build=not args.no_build, extra_env=extra)
    workdir = Path(tempfile.mkdtemp(prefix="mycelic-strategic-"))
    args.out.mkdir(parents=True, exist_ok=True)
    checks: list[bool] = []

    def verdict(cond: bool, text: str) -> None:
        checks.append(cond)
        say(text, "ok" if cond else "bad")

    try:
        head(f"Mycelic strategic synthesis — starting the deployment ({driver.name})")
        driver.up()
        admin = MycelicClient(driver.base_url, driver.admin_token)
        h = admin.health()
        say(f"service {h['version']} at {driver.base_url}: {h['status']}", "ok")
        rules = {r["rule_id"]: r for r in admin.list_rules()}
        for rid in ("regional_supply_risk", "strategic_second_source"):
            r = rules[rid]
            say(f"rule {rid}: at {r['target_layer']}, needs {r['required_slots']}"
                + (f", emits '{r['emits_slot']}'" if r.get("emits_slot") else "")
                + (f", corroboration {r['min_units']}" if r.get("min_units") else ""), "ok")
        pause()

        head("The organization: two regions and head office")
        sc = StrategicScenario(workdir, agents_per_team=args.agents_per_team).build()
        sc.register_all(admin)
        sc.write_observations()
        print(f"   {ENTERPRISE} (enterprise)")
        for region, sub in REGIONS.items():
            print(f"   ├─ {region} / {sub}")
            for team, dept in TEAMS.items():
                print(f"   │   {dept}/{team}: {', '.join(a.agent_id for a in sc.by_region(region) if a.team == team)}")
        print(f"   └─ {HQ['region']} / {HQ['subsidiary']}: strategy/sourcing: hq-sourcing-1, strategy/analytics: hq-analytics-1")
        say(f"{len(sc.agents)} agents registered, each with its own key and its own local memory file", "ok")
        pause()

        head("Agents observe (each shares one note and keeps one private)")
        states = sc.run_agents(driver.base_url)
        for a in sc.agents:
            for line in (workdir / f"{a.agent_id}.log").read_text().splitlines():
                if "shared" in line:
                    print(f"   {C['d']}{line}{C['x']}")
        st = wait_idle(admin)
        pause()

        head("What each region concluded on its own")
        regional = {m["scope"]: m for m in admin.list_memories(scope=ENTERPRISE, layer="region", limit=50) if m.get("rule_id") == "regional_supply_risk"}
        for scope, m in sorted(regional.items()):
            print(f"   [{scope}] confidence {m['confidence']}, {m['support']} agents / {m['independent_teams']} teams")
            print(f"      {clip(m['text'], 150)}")
        verdict(set(regional) == {f"{ENTERPRISE}/{r}" for r in REGIONS}, "both regions reached a regional supply-risk conclusion, independently")
        emea_agent = MycelicClient(driver.base_url, sc.agent("emea-field-sales-1").api_key)
        seen = {h["memory"]["scope"] for h in emea_agent.query("supply risk sd-9", scope=ENTERPRISE, min_layer="region", k=10, include_lineage=False)["results"]
                if h["memory"]["layer"] == "region"}
        verdict(seen == {f"{ENTERPRISE}/emea"}, f"an EMEA agent sees only its own region's conclusion: {sorted(seen)}")
        pause()

        head("What head office knew")
        for a in sc.by_region("hq"):
            for m in admin.list_memories(scope=a.path, layer="agent", limit=5):
                print(f"   {a.agent_id:<16} slot {m['slot']:<24} {clip(m['text'], 100)}")
        pause()

        head(f"Strategic synthesis: {STRATEGIC_QUERY!r} (asked at the enterprise by hq-sourcing-1)")
        hq = MycelicClient(driver.base_url, sc.agent("hq-sourcing-1").api_key)
        sa = strategic_answer(hq)
        strat = sa["hit"]
        verdict(strat is not None, "the enterprise holds a strategic recommendation")
        if strat is None:
            return 1
        print(f"   {C['b']}[{strat['layer']} {strat['scope']}] confidence {strat['confidence']}, support {strat['support']} agents / {strat['independent_teams']} teams{C['x']}")
        print(f"   {strat['text']}")
        g = admin.lineage(strat["memory_id"])
        regions = strat["metadata"].get("corroborated_units", {}).get("supply_risk", {}).get("region", [])
        verdict(len(regions) >= 2, f"corroborated across regions: {[leaf(r) for r in regions]}")
        verdict(g["layers"] == ["agent", "region", "enterprise"], f"lineage crosses layers {g['layers']}: two derivation steps from agents' notes to strategy")
        parents = sorted(e["parent"] for e in g["edges"] if e["child"] == strat["memory_id"])
        by_layer: dict[str, int] = {}
        for pid in parents:
            by_layer[g["nodes"][pid]["layer"]] = by_layer.get(g["nodes"][pid]["layer"], 0) + 1
        say(f"direct evidence: {by_layer} ({len(parents)} memories); raw observations at the roots: {len(g['roots'])}; "
            f"agents: {len(g['contributing_agents'])}; teams: {len(g['contributing_teams'])}; reconstructable: {g['evidence']['reconstructable']}", "ok")
        for t in g["transformations"]:
            print(f"   {' + '.join(t['from_layers'])} → {t['to_layer']:<10} {t['operator']}{' rule ' + t['rule_id'] if t['rule_id'] else ''}  [{leaf(t['scope'])}]")
        pause()

        head("Lineage graph (administrator view)")
        for line in tree(g, strat["memory_id"], "   "):
            print(line)
        (args.out / "lineage.json").write_text(json.dumps(g, indent=1), encoding="utf-8")
        (args.out / "lineage.mmd").write_text(mermaid(g), encoding="utf-8")
        say(f"written {args.out / 'lineage.json'} and {args.out / 'lineage.mmd'}", "ok")
        emea_view = emea_agent.lineage(strat["memory_id"])
        redacted_by_region: dict[str, int] = {}
        for n in emea_view["nodes"].values():
            if n["redacted"]:
                r = n["scope"].split("/")[1] if "/" in n["scope"] else n["scope"]
                redacted_by_region[r] = redacted_by_region.get(r, 0) + 1
        hq_note_ids = [m["memory_id"] for a in sc.by_region("hq") for m in admin.list_memories(scope=a.path, layer="agent", limit=5)]
        verdict(emea_view["redacted_contributions"] == sum(redacted_by_region.values()) > 0
                and emea_view["nodes"][regional[f"{ENTERPRISE}/apac"]["memory_id"]]["text"] is None
                and not emea_view["nodes"][regional[f"{ENTERPRISE}/emea"]["memory_id"]]["redacted"]
                and all(emea_view["nodes"][mid]["redacted"] for mid in hq_note_ids if mid in emea_view["nodes"]),
                f"the same lineage for an EMEA field-sales agent: {emea_view['redacted_contributions']} of {len(emea_view['nodes'])} contributions redacted "
                f"({', '.join(f'{v} {k}' for k, v in sorted(redacted_by_region.items()))}: APAC's conclusion and notes, HQ's notes, other EMEA teams' notes); "
                f"EMEA's conclusion and its own note readable, shape intact")
        pause()

        head("Strategy follows evidence: APAC retracts its supplier notes")
        apac_supplier = [m for m in admin.list_memories(scope=f"{ENTERPRISE}/apac", layer="agent", limit=200) if m["slot"] == "supplier_buffer_low"]
        for m in apac_supplier:
            MycelicClient(driver.base_url, sc.agent(m["producer_id"]).api_key).retract(m["memory_id"], "inventory was counted twice")
            say(f"{m['producer_id']} retracted: {clip(m['text'], 80)}", "warn")
        wait_idle(admin)
        regional_now = {m["scope"] for m in admin.list_memories(scope=ENTERPRISE, layer="region", limit=50) if m.get("rule_id") == "regional_supply_risk"}
        active_strategies = [m for m in admin.list_memories(scope=ENTERPRISE, layer="enterprise", limit=50) if m.get("rule_id") == "strategic_second_source"]
        gone = strategic_answer(hq)["hit"] is None and not active_strategies
        verdict(regional_now == {f"{ENTERPRISE}/emea"} and gone,
                f"APAC's regional conclusion is withdrawn and so is the strategy (regions with a conclusion now: {[leaf(r) for r in sorted(regional_now)]})")
        strat_now = admin.get_memory(strat["memory_id"])
        verdict(strat_now["status"] == "retracted", f"the withdrawn strategy is kept as history: status {strat_now['status']}")
        apac_proc2 = sc.agent("apac-procurement-2")
        MycelicClient(driver.base_url, apac_proc2.api_key).remember(
            "Physical count confirms roughly two weeks of SD-9 inventory in the APAC warehouse.", topic="supply:sd-9/supplier",
            slot="supplier_buffer_low", entity=ENTITY, confidence=0.9, idempotency_key="apac-recount-1")
        wait_idle(admin)
        back = strategic_answer(hq)["hit"]
        verdict(back is not None and back["memory_id"] != strat["memory_id"],
                "a fresh count from another APAC agent restores the regional conclusion and the strategy, as a new version with new evidence")
        strat = back or strat
        pause()

        head("Failure: the service is killed and its database deleted — rebuild from the event log")
        before = {m["memory_id"] for m in admin.list_memories(scope=ENTERPRISE, limit=500)}
        lineage_before = admin.lineage(strat["memory_id"])
        driver.kill("mycelic")
        driver.wipe_database()
        driver.start("mycelic")
        st = wait_idle(admin, timeout=180)
        after = {m["memory_id"] for m in admin.list_memories(scope=ENTERPRISE, limit=500)}
        g2 = admin.lineage(strat["memory_id"])
        edge_set = lambda g: {(e["child"], e["parent"]) for e in g["edges"]}  # noqa: E731
        verdict(after == before and g2["roots"] == lineage_before["roots"] and edge_set(g2) == edge_set(lineage_before)
                and g2["layers"] == lineage_before["layers"],
                f"rebuilt {len(after)} active memories from {st['checks']['transport'].get('stream_messages')} events; the strategic lineage is identical")
        pause()

        head("Summary")
        by_layer_counts = st["stats"]["memories_by_layer"]
        local_total = sum(s["counts"]["local"] for s in states.values())
        private_total = sum(s["counts"]["local_only"] for s in states.values())
        print(f"   agents: {len(sc.agents)} in {len(REGIONS) + 1} regions; notes on agents' disks: {local_total}, private: {private_total}")
        print("   organizational memory by layer: " + ", ".join(f"{k}={v}" for k, v in by_layer_counts.items()))
        (args.out / "summary.json").write_text(json.dumps({"strategy": strat, "memories_by_layer": by_layer_counts, "checks": checks,
                                                          "agents": {k: v["counts"] for k, v in states.items()}}, indent=1, default=str), encoding="utf-8")
        ok = all(checks)
        say(f"{sum(checks)}/{len(checks)} checks passed; summary in {args.out / 'summary.json'}", "ok" if ok else "bad")
        if args.keep and ok:
            print(f"\n   Deployment left running at {driver.base_url}\n   MYCELIC_ADMIN_TOKEN={driver.admin_token}\n   MYCELIC_API_KEY={sc.agent('hq-sourcing-1').api_key}   # hq-sourcing-1")
            print(f"   try:  python -m mycelic query \"{STRATEGIC_QUERY}\" --scope northwind --min-layer enterprise --lineage\n   press Ctrl-C to stop")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        return 0 if ok else 1
    finally:
        driver.down()


if __name__ == "__main__":
    sys.exit(main())
