"""Fail-closed session exchange. Audits and usage evidence must accompany every role reply."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .protocol import ROLES, candidates, canonical, digest, lineage_ok, prompt_batches, validate_reply
from .run import save


def current(run):
    state = json.loads((run / "state.json").read_text())
    edge = json.loads((run / "edge.json").read_text())["weeks"]
    summaries = [s for w in range(1, state["week"]+1) for s in edge[str(w)]]
    stories, qualified = candidates(summaries, state["week"], state["history"], state["watch"])
    replies = state.get("replies", {})
    batches = prompt_batches(state["role"], stories, replies)
    return state, summaries, stories, qualified, batches


def advance(run, raw, audit, weekly_remaining=None):
    state, summaries, stories, qualified, batches = current(run)
    if state["week"] > 2:
        closed = len(state["records"])
        projection = sum(r["estimated_tokens_four_chars"] for r in state["ledger"]) / closed * (27-state["week"])
        if weekly_remaining is None or projection >= weekly_remaining:
            raise ValueError("remaining weekly capacity must exceed projected remaining run")
    batch_index = state.get("batch_index", 0)
    batch, prompt = batches[batch_index]
    # This validates supplied transcript evidence. It cannot manufacture a missing transcript.
    if set(audit) != {"prompt_sha256", "reply_sha256", "events", "reported_usage", "transcript_source"}:
        raise ValueError("complete transcript audit required")
    if audit["prompt_sha256"] != digest(prompt) or audit["reply_sha256"] != digest(raw):
        raise ValueError("audit does not bind this exchange")
    if not audit["transcript_source"]:
        raise ValueError("auditable transcript source required")
    allowed = {("advance.show", str(run.resolve())), ("advance.reply", str(run.resolve()))}
    for event in audit["events"]:
        if set(event) != {"operation", "run"} or (event["operation"], event["run"]) not in allowed:
            raise ValueError("role call void: other tool use")
    used = (len(prompt) + len(raw)) / 4
    state.setdefault("ledger", []).append({"week": state["week"], "role": state["role"], "batch": batch_index,
                                            "prompt_characters": len(prompt), "reply_characters": len(raw),
                                            "estimated_tokens_four_chars": used, "reported_usage": audit["reported_usage"],
                                            "transcript_source": audit["transcript_source"], "retry": state.get("retry", 0)})
    try:
        reply = validate_reply(state["role"], raw, batch)
    except (ValueError, TypeError) as error:
        # Exactly one retry. A second failure is recorded without accepting a ruling.
        if state.get("retry", 0) == 0:
            state["retry"] = 1
            state["parse_error"] = type(error).__name__
            save(run / "state.json", state)
            raise ValueError("one retry required") from error
        reply = {c["key"]: ({"ruling": "no_ruling", "reason_tag": "none", "chain": c["chain"]}
                             if state["role"] == "judge" else {"text": "", "attack_tags": []}) for c in batch}
    state.setdefault("replies", {}).setdefault(state["role"], {}).update(reply)
    state.pop("retry", None)
    state.pop("parse_error", None)
    if batch_index + 1 < len(batches):
        state["batch_index"] = batch_index + 1
    else:
        state["batch_index"] = 0
        role_index = ROLES.index(state["role"])
        if role_index < 3:
            state["role"] = ROLES[role_index+1]
        else:
            for c in stories:
                c["lineage_ok"] = lineage_ok(c, summaries)
                state["history"][c["key"]] = {"handles": c["handles"],
                                               "ruling": state["replies"]["judge"][c["key"]]["ruling"]}
            state["watch"] = [c["key"] for c in stories if state["history"][c["key"]]["ruling"] == "hypothesis"]
            row = {"week": state["week"], "stories": stories, "qualified": [{"key": c["key"]} for c in qualified],
                   "replies": state["replies"], "watch": state["watch"], "state_sha256": digest(summaries),
                   "ledger": [r for r in state["ledger"] if r["week"] == state["week"]]}
            save(run / f"week-{state['week']:02}.json", row)
            state["records"].append(state["week"])
            state["replies"] = {}
            state["role"] = "advocate"
            state["week"] += 1
    save(run / "state.json", state)
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=["show", "reply"])
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--reply", type=Path)
    p.add_argument("--audit", type=Path)
    p.add_argument("--weekly-remaining", type=int)
    args = p.parse_args()
    state = json.loads((args.run / "state.json").read_text())
    if state["week"] > 26:
        p.error("run complete; scoring is a separate harness operation")
    if state["week"] > 2:
        projection = sum(r["estimated_tokens_four_chars"] for r in state["ledger"]) / len(state["records"]) * (27-state["week"])
        if args.weekly_remaining is None or projection >= args.weekly_remaining:
            p.error("paused: remaining weekly capacity must exceed the projected remaining run")
    if args.operation == "show":
        _, _, _, _, batches = current(args.run)
        prompt = batches[state.get("batch_index", 0)][1]
        print(prompt)
    else:
        if args.reply is None or args.audit is None:
            p.error("reply and transcript audit are required")
        result = advance(args.run, args.reply.read_text(), json.loads(args.audit.read_text()), args.weekly_remaining)
        print(canonical({"week": result["week"], "role": result["role"], "closed_weeks": result["records"]}))


if __name__ == "__main__":
    main()
