#!/usr/bin/env python
"""Production smoke test for Mycelic: the canonical deployment demonstration.

    python tests/smoke/mycelic_smoke.py --driver process            # nats-server + service as local processes
    python tests/smoke/mycelic_smoke.py --driver compose            # docker compose (builds the image)
    python tests/smoke/mycelic_smoke.py --driver compose --env-file deploy/mycelic/.env --no-build  # a running stack's config

What it proves, in order (every step is asserted; the run fails on the first broken one):

 1. the deployment is healthy and ready, the rule file is loaded;
 2. agents from three teams in two departments connect as separate processes, keep private notes locally and
    share partially overlapping observations;
 3. Mycelic consolidates the logistics observations into a team memory and composes the enterprise-level
    conclusion from three teams' evidence;
 4. a query from a higher organizational layer returns that conclusion, with lineage that names the contributing
    teams and layers, is reconstructable, and redacts other teams' raw notes for a non-admin caller;
 5. SIGKILL of the service, restart: the same answer and the same lineage;
 6. the broker is killed, an agent keeps writing (outbox), the broker returns, the write is applied and changes
    the team memory as expected;
 7. the service is killed and its database deleted; on restart it rebuilds itself from the JetStream log: the
    same active memory ids, the agents' keys still work, the rule is back;
 8. an agent process restarts with its local memory: nothing is duplicated, its private note is still local;
 9. metrics report what happened (memories by layer, replayed events, recoveries).

Exit code 0 means every assertion held. A JSON report is written with --report.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mycelic.harness import ComposeDriver, Driver, ProcessDriver  # noqa: E402
from mycelic.sdk import MycelicClient, MycelicError  # noqa: E402
from tests.smoke.scenario import ENTERPRISE, ENTITY, QUERY, Scenario  # noqa: E402


class SmokeFailure(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SmokeFailure(msg)


def wait_idle(admin: MycelicClient, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            st = admin.status()
        except MycelicError:
            time.sleep(0.5)
            continue
        last = st
        c = st["checks"]
        pending = (c["publisher"].get("outbox_pending") or 0) + (c["consumer"].get("pending") or 0)
        if pending == 0 and c["consumer"]["replaying_to_seq"] is None and c["transport"].get("connected"):
            time.sleep(0.5)                          # one more round: a just-published event may still be in flight
            st = admin.status()
            c = st["checks"]
            if (c["publisher"].get("outbox_pending") or 0) + (c["consumer"].get("pending") or 0) == 0:
                return st
        time.sleep(0.5)
    raise SmokeFailure(f"deployment did not become idle: {json.dumps(last.get('checks'), default=str)[:800]}")


def active_snapshot(admin: MycelicClient) -> dict[str, tuple]:
    rows = admin.list_memories(scope=ENTERPRISE, limit=500)
    return {m["memory_id"]: (m["layer"], m["status"], m["support"], m["independent_teams"], m["confidence"]) for m in rows}


def run(driver: Driver, *, agents_per_team: int, log, keep_workdir: Path | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"driver": driver.name, "agents_per_team": agents_per_team, "steps": []}
    t_start = time.time()

    def step(name: str, **detail: Any) -> None:
        report["steps"].append({"step": name, "t": round(time.time() - t_start, 2), **detail})
        log(f"  ok  {name}" + (f"  {json.dumps(detail, default=str)[:300]}" if detail else ""))

    workdir = keep_workdir or Path(tempfile.mkdtemp(prefix="mycelic-agents-"))
    log(f"== 1. deployment up ({driver.name}) -> {driver.base_url}")
    driver.up()
    admin = MycelicClient(driver.base_url, driver.admin_token)
    health = admin.health()
    check(health["status"] in ("ok", "degraded"), f"unexpected health {health}")
    rules = admin.list_rules()
    check(any(r["rule_id"] == "component_supply_risk" for r in rules), "rule file not loaded")
    step("deployment ready", health=health["status"], rules=[r["rule_id"] for r in rules])

    log(f"== 2. register {3 * agents_per_team} agents in 3 teams / 2 departments and run them as processes")
    sc = Scenario(workdir, agents_per_team=agents_per_team).build()
    sc.register_all(admin)
    sc.write_observations()
    states = sc.run_agents(driver.base_url)
    shared = sum(s["counts"]["shared"] for s in states.values())
    local_only = sum(s["counts"]["local_only"] for s in states.values())
    check(local_only == len(sc.agents), "every agent should have kept exactly one private note")
    check(shared >= len(sc.agents), "every agent should have shared at least one observation")
    step("agents ran", agents=len(sc.agents), shared=shared, local_only=local_only)

    log("== 3. aggregation")
    st = wait_idle(admin)
    by_layer = st["stats"]["memories_by_layer"]
    check(by_layer["team"] >= 1, f"no team memory: {by_layer}")
    check(by_layer["enterprise"] >= 1, f"no enterprise conclusion: {by_layer}")
    step("aggregated", memories_by_layer=by_layer, stream=st["checks"]["transport"].get("stream_messages"))

    log(f"== 4. query from the enterprise layer: {QUERY!r}")
    sales = sc.by_team("field-sales")[-1]
    agent_client = MycelicClient(driver.base_url, sales.api_key)
    res = agent_client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")
    ans = res["answer"]
    check(ans is not None, "no answer at enterprise scope")
    check(ans["layer"] == "enterprise" and ans["rule_id"] == "component_supply_risk", f"unexpected answer {ans}")
    check(ENTITY in ans["text"].lower(), "conclusion does not mention the entity")
    lin = res["lineage"]
    check(lin["evidence"]["reconstructable"], f"lineage not reconstructable: {lin['evidence']}")
    check(len(lin["contributing_teams"]) == 3, f"expected 3 teams, got {lin['contributing_teams']}")
    check(lin["layers"][0] == "agent" and lin["layers"][-1] == "enterprise", f"layers {lin['layers']}")
    check(lin["redacted_contributions"] >= 2, "other teams' raw observations must be redacted for a sales agent")
    check(all(n["text"] is None for n in lin["nodes"].values() if n["redacted"]), "redacted nodes must carry no text")
    admin_lin = admin.lineage(ans["memory_id"])
    check(admin_lin["redacted_contributions"] == 0 and len(admin_lin["contributing_agents"]) >= 3, "admin sees every contributor")
    # a raw note of another team is invisible to the sales agent
    log_root = next(r for r in admin_lin["roots"] if admin_lin["nodes"][r]["scope"].endswith("logistics") or "/logistics/" in admin_lin["nodes"][r]["scope"])
    try:
        agent_client.get_memory(log_root)
        check(False, "sales agent could read a logistics raw note")
    except MycelicError as exc:
        check(exc.status == 404, f"expected 404, got {exc.status}")
    answer_before = {"memory_id": ans["memory_id"], "roots": sorted(lin["roots"]), "support": ans["support"],
                     "teams": ans["independent_teams"], "confidence": ans["confidence"]}
    snap_before = active_snapshot(admin)
    step("query + lineage", answer=answer_before, redacted=lin["redacted_contributions"], layers=lin["layers"],
         agents=len(admin_lin["contributing_agents"]), text=ans["text"][:200])

    log("== 5. kill the service (SIGKILL) and restart it")
    driver.kill("mycelic")
    driver.start("mycelic")
    st = wait_idle(admin)
    res2 = agent_client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")
    check(res2["answer"]["memory_id"] == answer_before["memory_id"], "answer changed after a service restart")
    check(sorted(res2["lineage"]["roots"]) == answer_before["roots"], "lineage changed after a service restart")
    check(active_snapshot(admin) == snap_before, "active memories changed after a service restart")
    step("service restart survived", recoveries=st["checks"]["consumer"])

    log("== 6. kill the broker, keep writing, bring it back")
    team_before = [m for m in admin.list_memories(scope=ENTERPRISE, layer="team", limit=100) if m["topic"] == "supply:sd-9/transport"][0]
    driver.kill("nats")
    time.sleep(1.0)
    writer = sc.by_team("logistics")[-1]
    wclient = MycelicClient(driver.base_url, writer.api_key, retries=0)
    late = wclient.remember("Antwerp reroute for SD-9 containers adds 9 days and is not yet approved.",
                            topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, confidence=0.55,
                            idempotency_key="late-note-1")
    public = MycelicClient(driver.base_url, "").health()          # unauthenticated: the minimal public view
    st_out = admin.status()
    check(st_out["checks"]["publisher"]["outbox_pending"] >= 1, "write during the outage must sit in the outbox")
    check(public["status"] == "degraded" and not public["transport_connected"], f"expected degraded health, got {public}")
    driver.start("nats")
    st = wait_idle(admin, timeout=120)
    late_mem = admin.get_memory(late["memory_id"])
    check(late_mem["applied_at"] is not None, "the outage-time write was never applied")
    team_after = [m for m in admin.list_memories(scope=ENTERPRISE, layer="team", limit=100) if m["topic"] == "supply:sd-9/transport"][0]
    check(team_after["memory_id"] != team_before["memory_id"], "the team memory must be re-derived with the new evidence")
    check(team_after["metadata"]["parent_count"] == team_before["metadata"]["parent_count"] + 1, "new evidence must appear in the lineage")
    step("broker outage absorbed", outbox_during_outage=st_out["checks"]["publisher"]["outbox_pending"],
         team_support_before=team_before["support"], team_support_after=team_after["support"], reconnects=st["checks"]["transport"].get("reconnects"))
    snap_before_wipe = active_snapshot(admin)
    answer_before_wipe = agent_client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")["answer"]["memory_id"]

    log("== 7. kill the service, delete its database, restart: rebuild from the event log")
    driver.kill("mycelic")
    driver.wipe_database()
    driver.start("mycelic")
    st = wait_idle(admin, timeout=180)
    check(st["checks"]["consumer"]["replaying_to_seq"] is None, "replay did not finish")
    snap_after = active_snapshot(admin)
    check(snap_after == snap_before_wipe, f"rebuilt state differs: {set(snap_after) ^ set(snap_before_wipe)}")
    check(len(admin.list_agents(ENTERPRISE)) == len(sc.agents), "agents were not restored from the log")
    check(any(r["rule_id"] == "component_supply_risk" for r in admin.list_rules()), "rules were not restored")
    res3 = agent_client.query(QUERY, scope=ENTERPRISE, min_layer="enterprise")     # same agent key as before the wipe
    check(res3["answer"]["memory_id"] == answer_before_wipe, "answer differs after the rebuild")
    check(res3["lineage"]["evidence"]["reconstructable"], "lineage not reconstructable after the rebuild")
    step("database rebuilt from JetStream", active_memories=len(snap_after), agents=len(sc.agents))

    log("== 8. restart an agent process with its local memory")
    one = sc.by_team("procurement")[0]
    before_state = states[one.agent_id]
    after_state = sc.run_agents(driver.base_url, agents=[one])[one.agent_id]
    check(after_state["counts"] == before_state["counts"], f"agent restart changed local counts {before_state['counts']} -> {after_state['counts']}")
    check(after_state["shared"] == before_state["shared"], "agent restart re-sent or duplicated memories")
    check(after_state["local_only"] and after_state["local_only"] == before_state["local_only"], "private note lost or shared")
    wait_idle(admin)
    check(active_snapshot(admin) == snap_after, "an agent restart must not change organizational memory")
    step("agent restart idempotent", counts=after_state["counts"])

    log("== 9. metrics")
    import urllib.request
    req = urllib.request.Request(f"{driver.base_url}/metrics", headers={"Authorization": f"Bearer {driver.admin_token}"})
    text = urllib.request.urlopen(req, timeout=5).read().decode()
    check('mycelic_memories{layer="enterprise"}' in text, "metrics lack memories by layer")
    check("mycelic_replay_events_total" in text and "mycelic_recovery_total" in text, "metrics lack replay/recovery counters")
    replayed = [l for l in text.splitlines() if l.startswith("mycelic_replay_events_total")]
    step("metrics present", replay_counter=replayed[0] if replayed else None)

    report["result"] = "PASS"
    report["seconds"] = round(time.time() - t_start, 1)
    report["final_answer"] = res3["answer"]
    report["memories_by_layer"] = st["stats"]["memories_by_layer"]
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--driver", choices=["process", "compose"], default="process")
    ap.add_argument("--agents-per-team", type=int, default=2, help="3 teams; 2 -> 6 agents, 33 -> 99 agents")
    ap.add_argument("--env-file", type=Path, help="compose: use this .env (its MYCELIC_ADMIN_TOKEN/MYCELIC_PORT)")
    ap.add_argument("--no-build", action="store_true", help="compose: do not rebuild the image")
    ap.add_argument("--project", default="mycelic-smoke", help="compose project name")
    ap.add_argument("--report", type=Path, help="write a JSON report here")
    ap.add_argument("--keep", action="store_true", help="keep the deployment running after a pass (process driver)")
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, flush=True)

    extra = {"CA_BUNDLE_FILE": os.environ["MYCELIC_BUILD_CA_BUNDLE"]} if os.environ.get("MYCELIC_BUILD_CA_BUNDLE") else None
    driver: Driver = ProcessDriver() if args.driver == "process" else ComposeDriver(env_file=args.env_file, project=args.project,
                                                                                    build=not args.no_build, extra_env=extra)
    code = 0
    report: dict[str, Any] = {}
    try:
        report = run(driver, agents_per_team=args.agents_per_team, log=log)
        log(f"\nPASS in {report['seconds']}s: {report['final_answer']['text'][:160]}")
    except SmokeFailure as exc:
        code = 1
        report = {"result": "FAIL", "error": str(exc)}
        log(f"\nFAIL: {exc}")
        log("--- mycelic log tail ---\n" + driver.logs("mycelic", 40))
        log("--- nats log tail ---\n" + driver.logs("nats", 20))
    except Exception as exc:
        code = 2
        report = {"result": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        log(f"\nERROR: {type(exc).__name__}: {exc}")
        log("--- mycelic log tail ---\n" + driver.logs("mycelic", 60))
        raise
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        if not (args.keep and code == 0):
            driver.down()
    return code


if __name__ == "__main__":
    sys.exit(main())
