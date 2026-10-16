"""Checkpointed code-only dry run and explicit, bounded session-pilot exchange."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from research.e2e_ingest.emergence_world import generate, gold_labels, world_counts
from research.routing_spike.world import candidate_label

from .backends import Fixture
from .edge import emulate
from .gossip import fault_run
from .protocol import ROLES, candidates, canonical, digest, lineage_ok, make_prompt, prompt_batches, scan, validate_reply

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(canonical(value) + "\n")
    tmp.replace(path)


def build(users, perfect=False):
    org, corpus, truth = generate(users, 0)
    weeks, calibration = emulate(corpus, company_key=b"rollup-001-development-company", perfect=perfect)
    return weeks, calibration, world_counts(org, corpus, truth), corpus, gold_labels(corpus, truth)


def scores(records, truth, corpus):
    supported = [dict(c, week=r["week"], **r["replies"]["judge"][c["key"]]) for r in records for c in r["stories"]
                 if r["replies"]["judge"][c["key"]]["ruling"] == "supported" and c["lineage_ok"]]
    found = {candidate_label(c["key"], f"2024-W{c['week']:02}", truth)["pattern"] for c in supported
             if candidate_label(c["key"], f"2024-W{c['week']:02}", truth)["label"] == "true"}
    readings = {}
    from research.e2e_ingest.emergence_world import CHAIN_PREDICATES, canonical_entity_id
    for mode in ("every_facet_window", "facet0_window", "every_facet_unwindowed"):
        accepted = {str(i): set() for i in range(1, 6)}
        totals = {str(i): 0 for i in range(1, 6)}
        for d in truth["decoys"]:
            totals[str(d["class"])] += 1
            keys = d["keys"]
            if mode == "facet0_window":
                p = corpus.patterns[d["pattern"]]
                first = p.facet_records[0][0]
                entity = canonical_entity_id(corpus.entities[int(corpus.recs[first]["anchor"])])
                keys = [f"entity:{entity}:{CHAIN_PREDICATES[p.chain]}"]
            for c in supported:
                week = f"2024-W{c['week']:02}"
                if c["key"] not in keys or (mode != "every_facet_unwindowed" and not d["found_from"] <= week <= d["found_to"]):
                    continue
                if mode != "facet0_window" and candidate_label(c["key"], week, truth)["label"] == "true":
                    continue
                accepted[str(d["class"])].add(d["id"])
        readings[mode] = {k: {"accepted": len(v), "total": totals[k]} for k, v in accepted.items()}
    rare = {p["id"] for p in truth["patterns"] if p["rare"]}
    debated = {(c["key"], f"2024-W{r['week']:02}") for r in records for c in r["stories"]}
    ceiling = {p["id"] for p in truth["patterns"] if any(k == p["key"] and p["found_from"] <= w <= p["found_to"]
                                                                       for k, w in debated)}
    misses = {}
    for p in truth["patterns"]:
        eligible = [(r, c) for r in records for c in r["qualified"] if c["key"] == p["key"]
                    and p["found_from"] <= f"2024-W{r['week']:02}" <= p["found_to"]]
        calls = [(r, c) for r in records for c in r["stories"] if c["key"] == p["key"]]
        window_calls = [(r, c) for r, c in calls if p["found_from"] <= f"2024-W{r['week']:02}" <= p["found_to"]]
        if p["id"] in found:
            reason = "found"
        elif any(r["replies"]["judge"][c["key"]]["ruling"] == "supported" for r, c in calls):
            reason = "supported outside window"
        elif not eligible:
            reason = "never qualified"
        elif not window_calls:
            reason = "outside cap throughout window"
        else:
            r, c = window_calls[-1]
            verdict = r["replies"]["judge"][c["key"]]
            reason = verdict["ruling"] + ":" + verdict["reason_tag"]
        misses[p["id"]] = reason
    baseline = {"p001", "p006", "p011", "p015", "p017", "p019", "p022", "p024", "p025", "p033", "p034", "p036", "p037"}
    by_contradiction = {}
    for contradicted in (False, True):
        group = {p["id"] for p in truth["patterns"] if bool(p["contradicted"]) == contradicted}
        by_contradiction[str(contradicted)] = {"found": len(found & group), "total": len(group),
                                             "baseline_found": len(baseline & group)}
    evidence = {"true": {}, **{str(i): {} for i in range(1, 6)}}
    attacks_for = {1: None, 2: "reversed_order", 3: None, 4: "copies", 5: "retraction_contradiction"}
    for r in records:
        for c in r["stories"]:
            lab = candidate_label(c["key"], f"2024-W{r['week']:02}", truth)
            bucket = "true" if lab["label"] == "true" else str(lab["decoy_class"])
            if bucket not in evidence:
                continue
            attack = attacks_for.get(lab["decoy_class"])
            tags = r["replies"]["skeptic"][c["key"]].get("attack_tags", [])
            ruling = r["replies"]["judge"][c["key"]]
            group = evidence[bucket]
            group["debated"] = group.get("debated", 0) + 1
            group["supported"] = group.get("supported", 0) + int(ruling["ruling"] == "supported")
            for tag in ("copies", "reversed_order", "retraction_contradiction", "staleness", "single_source"):
                group[tag] = group.get(tag, 0) + int(tag in tags)
            group["targeted_attack"] = attack or "no targeted attack"
            group["matching_attack_raised"] = group.get("matching_attack_raised", 0) + int(attack in tags)
            group["matching_reason_not_supported"] = group.get("matching_reason_not_supported", 0) + int(
                attack is not None and ruling["ruling"] in {"rejected", "hypothesis"} and ruling["reason_tag"] == attack)
    gold_anchors, decoy_anchors = set(), set()
    for d in truth["decoys"]:
        p = corpus.patterns[d["pattern"]]
        for facets in p.facet_records:
            for rid in facets:
                decoy_anchors.add(canonical_entity_id(corpus.entities[int(corpus.recs[rid]["anchor"])]))
    early = {}
    for c in supported:
        if c["week"] > 7:
            continue
        week = f"2024-W{c['week']:02}"
        is_gold = any(c["entity"] == p["anchor"] and p["found_from"] <= week <= p["found_to"] for p in truth["patterns"])
        early.setdefault(c["entity"], set()).add("true" if is_gold else "decoy" if c["entity"] in decoy_anchors else "background")
    early_gold = sum("true" in v for v in early.values())
    early_decoy = sum("decoy" in v and "true" not in v for v in early.values())
    debated_groups = {"true": set(), "decoy": set()}
    last_ruling = {}
    retraction_table = {"true": {"raised": 0, "not_raised": 0}, "class5": {"raised": 0, "not_raised": 0}}
    for r in records:
        for c in r["stories"]:
            lab = candidate_label(c["key"], f"2024-W{r['week']:02}", truth)
            last_ruling[c["key"]] = r["replies"]["judge"][c["key"]]["ruling"]
            if lab["label"] in debated_groups:
                debated_groups[lab["label"]].add(c["key"])
            bucket = "true" if lab["label"] == "true" else "class5" if lab["decoy_class"] == 5 else None
            if bucket:
                raised = "retraction_contradiction" in r["replies"]["skeptic"][c["key"]]["attack_tags"]
                retraction_table[bucket]["raised" if raised else "not_raised"] += 1
    final = {k: {"debated_keys": len(v), "not_supported": sum(last_ruling[key] in {"hypothesis", "rejected"} for key in v)}
             for k, v in debated_groups.items()}
    decoy_trace = []
    for d in truth["decoys"]:
        qualified_weeks = sorted({r["week"] for r in records for c in r["qualified"] if c["key"] in d["keys"]})
        debate_weeks = sorted({r["week"] for r in records for c in r["stories"] if c["key"] in d["keys"]})
        support_weeks = sorted({c["week"] for c in supported if c["key"] in d["keys"]
                                and candidate_label(c["key"], f"2024-W{c['week']:02}", truth)["label"] != "true"
                                and d["found_from"] <= f"2024-W{c['week']:02}" <= d["found_to"]})
        decoy_trace.append({"id": d["id"], "class": d["class"], "qualified_weeks": qualified_weeks,
                            "debated_weeks": debate_weeks, "supported_in_window_weeks": support_weeks,
                            "final_rulings": {k: last_ruling[k] for k in d["keys"] if k in last_ruling}})
    sufficient = final["decoy"]["debated_keys"] >= 5
    decoy_share = final["decoy"]["not_supported"] / max(1, final["decoy"]["debated_keys"])
    true_share = final["true"]["not_supported"] / max(1, final["true"]["debated_keys"])
    gate = (early_decoy / max(1, len(early)) < 9/12 and early_gold >= 2 and sufficient
            and decoy_share >= .5 and decoy_share > true_share and final["true"]["debated_keys"] > 0)
    return {"found": len(found), "total": len(truth["patterns"]), "recall_all": len(found) / len(truth["patterns"]),
            "rare_found": len(found & rare), "rare_total": len(rare), "decoys": readings,
            "debated_in_window_ceiling": {"found": len(ceiling), "total": len(truth["patterns"])},
            "miss_decomposition": misses, "supported": len(supported),
            "found_ids": sorted(found), "contradiction_split": by_contradiction,
            "retraction_attack_table_per_call": retraction_table, "decoy_trace": decoy_trace,
            "class_against_attack_per_call": evidence,
            "anchor_reading_W01_W07": {"supported_entities": len(early), "true_in_window": early_gold,
                                       "decoy_entities": early_decoy, "approximate_calibration_comparison": True},
            "gate_d": {"status": "pass" if gate else "fail" if sufficient else "not determinable",
                       "eligible_backend": False, "reason": "fixture results never authorize the model scaling step",
                       "final_key_counts": final},
            "baseline_as_recorded": {"found": 13, "total": 40, "facet0": [0, 5, 0, 9, 3], "every_facet": [5, 5, 6, 9, 3]},
            "paired_baseline": {"both": sorted(found & baseline), "debate_only": sorted(found - baseline),
                                "baseline_only": sorted(baseline - found),
                                "source": "EMERGENCE_ROOT_CAUSE_REPORT.md per-pattern table, real-run column"}}


def fixture_run(users, out, perfect=False):
    weeks, calibration, counts, corpus, truth = build(users, perfect)
    state, records, history, watch = [], [], {}, []
    backend = Fixture()
    save(out / "summaries.json", weeks)
    for week in range(1, 27):
        state.extend(weeks[week])
        stories, qualified = candidates(state, week, history, watch)
        replies, characters, ledger = {}, {}, {}
        for role in ROLES:
            combined, chars, reply_chars = {}, 0, 0
            for batch, prompt in prompt_batches(role, stories, replies):
                chars += len(prompt)
                raw = backend.complete(prompt)
                reply_chars += len(raw)
                combined.update(validate_reply(role, raw, batch))
            replies[role], characters[role] = combined, chars
            ledger[role] = {"prompt_characters": chars, "reply_characters": reply_chars,
                            "estimated_tokens_four_chars": (chars + reply_chars) / 4,
                            "reported_usage": "unavailable; deterministic fixture"}
        for c in stories:
            c["lineage_ok"] = lineage_ok(c, state)
            history[c["key"]] = {"handles": c["handles"], "ruling": replies["judge"][c["key"]]["ruling"]}
        watch = [c["key"] for c in stories if replies["judge"][c["key"]]["ruling"] == "hypothesis"]
        row = {"week": week, "stories": stories, "qualified": [{"key": c["key"]} for c in qualified], "replies": replies,
               "prompt_characters": characters, "watch": watch, "state_sha256": digest(state)}
        row["ledger"] = ledger
        records.append(row)
        save(out / f"week-{week:02}.json", row)
    report = {"measurement": False, "synthetic": True, "seed": 0, "users_target": users, "counts": counts,
              "backend": "deterministic fixture; code-only ablation", "perfect_marking": perfect,
              "edge": calibration, "scores": scores(records, truth, corpus), "cap": 8,
              "prompt_side_only_characters": sum(sum(r["prompt_characters"].values()) for r in records),
              "settings_source": "PLAN-ROLLUP-001 amendment ff4dd84", "commit": revision(),
              "limitations": ["Fixture rulings determine re-debate history; this is an attainable ceiling for that history only, "
                              "not a proof of the maximum over every possible judge trajectory.",
                              "No model intelligence is measured."]}
    save(out / "report.json", report)
    return report


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fixture", "gossip", "prepare"])
    parser.add_argument("--users", type=int, choices=[200, 10000], default=200)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--perfect", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output already exists; refusing to overwrite the first run")
    if args.command == "fixture":
        report = fixture_run(args.users, args.out, args.perfect)
        print(canonical({"output": str(args.out), "backend": report["backend"], "complete": True}))
    elif args.command == "gossip":
        if args.users != 10000:
            parser.error("gossip uses ten thousand users")
        weeks, calibration, counts, _, _ = build(10000)
        report = fault_run(weeks, b"rollup-001-development-auth")
        report.update({"counts": counts, "edge": calibration, "commit": revision()})
        save(args.out / "report.json", report)
        print(canonical({"output": str(args.out), "complete": True}))
    else:
        # A closed exchange contains no answer file. Parent harness retains scoring separately.
        weeks, calibration, counts, _, _ = build(args.users)
        save(args.out / "edge.json", {"weeks": weeks, "calibration": calibration, "counts": counts})
        save(args.out / "state.json", {"week": 1, "role": "advocate", "history": {}, "watch": [], "records": [],
                                       "measurement": False, "synthetic": True, "commit": revision()})
        state = weeks[1]
        stories, _ = candidates(state, 1)
        (args.out / "prompt.txt").write_text(make_prompt("advocate", stories))
        print(canonical({"output": str(args.out), "paused": True}))


if __name__ == "__main__":
    main()
