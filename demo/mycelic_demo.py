#!/usr/bin/env python
"""Mycelic end-to-end demo: a small organization discovers a supply risk that no single agent could see.

    python demo/mycelic_demo.py                       # nats-server + service as local processes (needs nats-server)
    python demo/mycelic_demo.py --driver compose      # against `docker compose` (builds the image)
    python demo/mycelic_demo.py --keep                # leave everything running at the end and print how to connect

The story: Northwind Robotics builds the RX-4 arm around the SD-9 servo drive.  Logistics agents notice a port
strike and slipping carrier ETAs.  A procurement agent hears the supplier has two weeks of stock left.  A sales
agent books a large November order.  Each note alone is routine.  Mycelic propagates the shared ones through the
organization, consolidates the logistics team's observations, and composes an enterprise-level supply-risk
conclusion whose lineage names every contributing observation, agent, team and layer.  Then the deployment is
broken on purpose: the service is killed, the broker is killed, the service's database is deleted.  Everything
comes back from the JetStream event log.  Every number printed is read from the live deployment.
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
from mycelic.sdk import MycelicClient, MycelicError  # noqa: E402
from tests.smoke.mycelic_smoke import wait_idle  # noqa: E402
from tests.smoke.scenario import ENTERPRISE, ENTITY, QUERY, REGION, SUBSIDIARY, TEAMS, Scenario  # noqa: E402

C = {"h": "\033[1;36m", "b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "x": "\033[0m"}
OUT = Path(__file__).resolve().parent / "results" / "mycelic"


def head(text: str) -> None:
    print(f"\n{C['h']}=== {text}{C['x']}", flush=True)


def say(text: str, kind: str = "") -> None:
    prefix = {"ok": f"{C['g']}  ✓{C['x']}", "warn": f"{C['y']}  !{C['x']}", "bad": f"{C['r']}  ✗{C['x']}", "": "   "}[kind]
    print(f"{prefix} {text}", flush=True)


def clip(t: str, n: int = 110) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def lineage_tree(g: dict[str, Any], root: str, indent: str = "") -> list[str]:
    n = g["nodes"][root]
    who = "redacted" if n["redacted"] else (n["producer_id"] if n["layer"] == "agent" else f"{n['operator']}")
    label = f"[{n['layer']}] {n['scope'].rsplit('/', 1)[-1]}  ({who}, confidence {n['confidence']}, support {n['support']})"
    text = "[text withheld: outside your visibility]" if n["redacted"] else clip(n["text"], 90)
    lines = [f"{indent}{label}", f"{indent}   {C['d']}{text}{C['x']}"]
    parents = sorted(e["parent"] for e in g["edges"] if e["child"] == root)
    for i, pid in enumerate(parents):
        lines.extend(lineage_tree(g, pid, indent + ("   │ " if i < len(parents) - 1 else "     ")))
    return lines


def mermaid(g: dict[str, Any]) -> str:
    out = ["graph BT"]
    for mid, n in g["nodes"].items():
        text = "redacted" if n["redacted"] else clip(n["text"], 60).replace('"', "'")
        out.append(f'  {mid}["{n["layer"]}: {text}"]')
    for e in g["edges"]:
        out.append(f"  {e['parent']} --> {e['child']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--driver", choices=["process", "compose"], default="process")
    ap.add_argument("--agents-per-team", type=int, default=2)
    ap.add_argument("--pace", type=float, default=0.6, help="seconds to pause between narrated steps")
    ap.add_argument("--keep", action="store_true", help="leave the deployment running at the end")
    ap.add_argument("--no-build", action="store_true", help="compose: reuse the existing image")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    pause = lambda: time.sleep(args.pace)  # noqa: E731

    extra = {"CA_BUNDLE_FILE": os.environ["MYCELIC_BUILD_CA_BUNDLE"]} if os.environ.get("MYCELIC_BUILD_CA_BUNDLE") else None
    driver: Driver = ProcessDriver() if args.driver == "process" else ComposeDriver(project="mycelic-demo", build=not args.no_build, extra_env=extra)
    workdir = Path(tempfile.mkdtemp(prefix="mycelic-demo-agents-"))
    args.out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"driver": driver.name}
    try:
        head(f"Mycelic demo — starting the deployment ({driver.name})")
        driver.up()
        admin = MycelicClient(driver.base_url, driver.admin_token)
        h = admin.health()
        say(f"service {h['version']} at {driver.base_url}: {h['status']}, transport connected={h['checks']['transport']['connected']}", "ok")
        say(f"rules loaded: {[r['rule_id'] for r in admin.list_rules()]}", "ok")
        pause()

        head("The organization")
        sc = Scenario(workdir, agents_per_team=args.agents_per_team).build()
        sc.register_all(admin)
        sc.write_observations()
        print(f"   {ENTERPRISE} (enterprise)\n   └─ {REGION} (region)\n      └─ {SUBSIDIARY} (subsidiary)")
        for dept in sorted(set(TEAMS.values())):
            print(f"         ├─ {dept} (department)")
            for team, d in TEAMS.items():
                if d == dept:
                    print(f"         │   └─ {team} (team): {', '.join(a.agent_id for a in sc.by_team(team))}")
        say(f"{len(sc.agents)} agents registered, each with its own API key and its own local memory file", "ok")
        pause()

        head("Agents observe — each keeps notes locally and shares only some of them")
        states = sc.run_agents(driver.base_url)
        for a in sc.agents:
            for line in (workdir / f"{a.agent_id}.log").read_text().splitlines():
                if "shared" in line or "locally only" in line:
                    print(f"   {C['d']}{line}{C['x']}")
        st = wait_idle(admin)
        pause()

        head("What each agent knows on its own (nothing here is a conclusion)")
        for a in sc.agents:
            s = states[a.agent_id]
            print(f"   {a.agent_id:<14} local notes: {s['counts']['local']}   shared: {s['counts']['shared']}   stayed private: {s['counts']['local_only']}")
        pause()

        head("What Mycelic concluded")
        asker = sc.by_team("field-sales")[-1]
        client = MycelicClient(driver.base_url, asker.api_key)
        res = client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")
        ans = res["answer"]
        if ans is None:
            say("no enterprise-level answer — aggregation did not produce the conclusion", "bad")
            return 1
        print(f"   {C['b']}Q ({asker.agent_id}, asking at the enterprise layer):{C['x']} {QUERY}")
        print(f"   {C['b']}A [{ans['layer']} {ans['scope']}] confidence {ans['confidence']}, support {ans['support']} agents / {ans['independent_teams']} teams{C['x']}")
        print(f"   {ans['text']}")
        g = res["lineage"]
        say(f"lineage: reconstructable={g['evidence']['reconstructable']}, roots={len(g['roots'])}, layers={g['layers']}, "
            f"first observed {g['timeline']['first_observed_at']}, produced {g['timeline']['produced_at']}", "ok")
        pause()

        head("Supporting observations and independent sources (as the sales agent sees them)")
        for rid in g["roots"]:
            n = g["nodes"][rid]
            src = f"{n['producer_id']} ({n['scope'].rsplit('/', 2)[-2]})" if not n["redacted"] else f"[redacted agent in {n['scope'].rsplit('/', 1)[-1]}]"
            text = clip(n["text"], 100) if not n["redacted"] else "[text withheld: another team's raw note]"
            print(f"   • slot {n['slot']:<22} {src:<38} conf {n['confidence']}  {C['d']}{text}{C['x']}")
        fr = g["support"]["fragility"] or {}
        say(f"independent failure domains (teams): {fr.get('unique_failure_domain_count')}, candidate observations: {fr.get('unique_lineage_root_count')}, "
            f"minimal cut: {fr.get('minimal_failure_domain_cut')} team(s) would have to be wrong for the conclusion to lose support", "ok")
        say(f"{g['redacted_contributions']} contributions are redacted for {asker.agent_id}; an administrator sees all of them:", "warn")
        ga = admin.lineage(ans["memory_id"])
        print(f"      agents: {ga['contributing_agents']}   teams: {[t.rsplit('/', 1)[-1] for t in ga['contributing_teams']]}")
        pause()

        head("Organizational propagation: which layers transformed what")
        team_mems = admin.list_memories(scope=ENTERPRISE, layer="team", limit=50)
        for m in team_mems:
            print(f"   [team {m['scope'].rsplit('/', 1)[-1]:<12}] {m['topic']:<26} support {m['support']}  ← {m['metadata'].get('parent_count')} observations  {C['d']}{clip(m['text'], 70)}{C['x']}")
        for m in admin.list_memories(scope=ENTERPRISE, layer="department", limit=50):
            print(f"   [dept {m['scope'].rsplit('/', 1)[-1]:<12}] {m['topic']:<26} promoted from a single team's consolidation")
        for t in ga["transformations"]:
            print(f"   {' + '.join(t['from_layers'])} → {t['to_layer']:<11} via {t['operator']}{' rule ' + t['rule_id'] if t['rule_id'] else ''}  at {t['at']}")
        pause()

        head("Lineage graph (administrator view)")
        for line in lineage_tree(ga, ans["memory_id"], "   "):
            print(line)
        (args.out / "lineage.json").write_text(json.dumps(ga, indent=1), encoding="utf-8")
        (args.out / "lineage.mmd").write_text(mermaid(ga), encoding="utf-8")
        say(f"written {args.out / 'lineage.json'} and {args.out / 'lineage.mmd'} (Mermaid)", "ok")
        pause()

        head("What stayed local, what was aggregated")
        by_layer = st["stats"]["memories_by_layer"]
        local_total = sum(s["counts"]["local"] for s in states.values())
        private_total = sum(s["counts"]["local_only"] for s in states.values())
        print(f"   notes on agents' own disks: {local_total}   never left the agent: {private_total}   shared: {local_total - private_total}")
        print("   organizational memory by layer: " + ", ".join(f"{k}={v}" for k, v in by_layer.items()))
        for a in sc.agents:
            for text in states[a.agent_id]["local_only"]:
                print(f"   {C['d']}kept private by {a.agent_id}: {clip(text, 80)}{C['x']}")
        pause()

        head("Failure 1: the service is killed (SIGKILL) and restarted")
        driver.kill("mycelic")
        try:
            MycelicClient(driver.base_url, asker.api_key, retries=0).health()
            say("service still answering after kill?!", "bad")
        except MycelicError as exc:
            say(f"service down: {exc.message}", "warn")
        driver.start("mycelic")
        wait_idle(admin)
        res2 = client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")
        same = res2["answer"]["memory_id"] == ans["memory_id"] and sorted(res2["lineage"]["roots"]) == sorted(g["roots"])
        say(f"back: same conclusion {res2['answer']['memory_id']} with the same lineage roots" if same else "conclusion changed after restart", "ok" if same else "bad")
        pause()

        head("Failure 2: the message broker is killed while an agent keeps working")
        driver.kill("nats")
        writer = sc.by_team("logistics")[0]
        late = MycelicClient(driver.base_url, writer.api_key, retries=0).remember(
            "Antwerp reroute for SD-9 containers adds 9 days and is not yet approved.", topic="supply:sd-9/transport",
            slot="transport_disruption", entity=ENTITY, confidence=0.55, idempotency_key="antwerp-1")
        pub = MycelicClient(driver.base_url, "").health()
        say(f"{writer.agent_id} shared a new observation during the outage: accepted ({late['memory_id']}), health={pub['status']}, "
            f"outbox pending={admin.status()['checks']['publisher']['outbox_pending']}", "warn")
        driver.start("nats")
        st = wait_idle(admin, timeout=120)
        team = [m for m in admin.list_memories(scope=ENTERPRISE, layer="team", limit=50) if m["topic"] == "supply:sd-9/transport"][0]
        say(f"broker back: outbox flushed, observation applied, logistics team memory re-derived from {team['metadata'].get('parent_count')} observations", "ok")
        pause()

        head("Failure 3: the service is killed and its database deleted — rebuild from the event log")
        before = {m["memory_id"] for m in admin.list_memories(scope=ENTERPRISE, limit=500)}
        driver.kill("mycelic")
        driver.wipe_database()
        driver.start("mycelic")
        st = wait_idle(admin, timeout=180)
        after = {m["memory_id"] for m in admin.list_memories(scope=ENTERPRISE, limit=500)}
        res3 = client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")
        ok = after == before and res3["answer"]["memory_id"] == ans["memory_id"] and res3["lineage"]["evidence"]["reconstructable"]
        say(f"rebuilt {len(after)} active memories from JetStream (stream holds {st['checks']['transport'].get('stream_messages')} events); "
            f"same conclusion id, lineage reconstructable, {len(admin.list_agents(ENTERPRISE))} agents and their keys restored", "ok" if ok else "bad")
        pause()

        results.update({"answer": res3["answer"], "lineage_roots": res3["lineage"]["roots"], "memories_by_layer": st["stats"]["memories_by_layer"],
                        "agents": {k: v["counts"] for k, v in states.items()}, "stream_messages": st["checks"]["transport"].get("stream_messages")})
        (args.out / "summary.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        head("Done")
        say(f"summary written to {args.out / 'summary.json'}", "ok")
        if args.keep:
            print(f"\n   Deployment left running at {driver.base_url}")
            print(f"   MYCELIC_URL={driver.base_url}\n   MYCELIC_ADMIN_TOKEN={driver.admin_token}\n   MYCELIC_API_KEY={asker.api_key}   # {asker.agent_id}")
            print("   try:  python -m mycelic query \"delivery risk sd-9\" --scope northwind --lineage")
            print("   MCP:  claude mcp add --transport http mycelic $MYCELIC_URL/mcp --header \"Authorization: Bearer $MYCELIC_API_KEY\"")
            print("   press Ctrl-C to stop")
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
