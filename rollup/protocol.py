"""Abstract wire format and label-blind candidate formation (PLAN-ROLLUP-001)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import defaultdict
from pathlib import Path

CHAINS = {
    "chain_supply": ["supplier_substitution", "component_defect", "yield_drop", "shipment_delay",
                     "customer_escalation", "revenue_miss"],
    "chain_auth": ["cert_rotation", "auth_failure", "api_error_spike", "checkout_drop", "revenue_miss"],
    "chain_policy": ["policy_change", "manual_workaround", "data_exposure_risk", "audit_finding", "regulatory_notice"],
    "chain_attrition": ["attrition_spike", "backlog_growth", "sla_breach", "contract_penalty", "account_churn"],
    "chain_firmware": ["firmware_update", "sensor_drift", "false_alarm", "unplanned_downtime", "output_shortfall"],
    "chain_pricing": ["price_increase", "margin_compression", "discount_escalation", "forecast_revision"],
    "chain_network": ["network_change", "latency_regression", "batch_job_overrun", "reporting_delay", "close_delay"],
}
KINDS = {k for ch in CHAINS.values() for k in ch}
TAGS = {"copies", "reversed_order", "retraction_contradiction", "staleness", "single_source"}
ROLES = ("advocate", "skeptic", "rebuttal", "judge")
CAP = 8
VOCABULARY = Path(__file__).resolve().parents[1] / "research/e2e_ingest/pack/emergence/vocabulary.json"
ENTITIES = frozenset(json.loads(VOCABULARY.read_text())["entity_types"]["entity"]["ids"])


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def handle(company_key, fingerprint):
    return hmac.new(company_key, fingerprint.encode(), hashlib.sha256).hexdigest()[:16]


def validate_summary(summary, forbidden=()):
    """Closed schema: arbitrary record fields and free prose cannot cross the edge."""
    if set(summary) != {"site", "week", "entries"}:
        raise ValueError("summary fields")
    if not isinstance(summary["site"], str) or not re.fullmatch(r"r\d{2}-site-\d{2}", summary["site"]):
        raise ValueError("site")
    if type(summary["week"]) is not int or not 1 <= summary["week"] <= 26:
        raise ValueError("week")
    if not isinstance(summary["entries"], list):
        raise ValueError("entries")
    seen = set()
    for row in summary["entries"]:
        if set(row) != {"entity", "kind", "negated", "distinct", "copies", "handles"}:
            raise ValueError("entry fields")
        if not isinstance(row["entity"], str) or row["entity"] not in ENTITIES:
            raise ValueError("entity")
        if row["kind"] not in KINDS or type(row["negated"]) is not bool:
            raise ValueError("kind or polarity")
        if any(type(row[k]) is not int or row[k] < 0 for k in ("distinct", "copies")):
            raise ValueError("counts")
        hs = row["handles"]
        if not isinstance(hs, list) or len(set(hs)) != len(hs):
            raise ValueError("handles")
        if any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{16}", x) for x in hs):
            raise ValueError("handle format")
        if row["distinct"] != len(hs):
            raise ValueError("distinct count")
        key = (row["entity"], row["kind"], row["negated"])
        if key in seen:
            raise ValueError("duplicate entry")
        seen.add(key)
    scan(canonical(summary), forbidden)


def scan(text, forbidden=(), prompt=False):
    if re.search(r"em\d+-\d{7}|\[d\d{3}\]|R\d{2}S\d{2}-DEPT-|\bu\d+\b", text):
        raise ValueError("record identifier or reporter")
    if prompt and re.search(r"\b(?:decoy|gold|label)\b", text, re.I):
        raise ValueError("answer vocabulary")
    if any(value and str(value) in text for value in forbidden):
        raise ValueError("private content")


def candidates(summaries, week, history=None, watch=()):
    """No scorer import, answer file or pattern identifier is available here."""
    history = history or {}
    entities = defaultdict(list)
    for s in summaries:
        validate_summary(s)
        if max(1, week - 11) <= s["week"] <= week:
            for row in s["entries"]:
                entities[row["entity"]].append(dict(row, site=s["site"], week=s["week"]))
    result = []
    for entity, rows in entities.items():
        positive = [r for r in rows if not r["negated"] and r["distinct"]]
        if len({r["kind"] for r in positive}) < 2:
            continue
        # Most distinct kinds, then distinct handles, then chain name. Other-chain context stays visible.
        scores = []
        for chain, kinds in CHAINS.items():
            relevant = [r for r in positive if r["kind"] in kinds]
            scores.append((-len({r["kind"] for r in relevant}),
                           -len({h for r in relevant for h in r["handles"]}), chain))
        chain = min(scores)[2]
        hs = sorted({h for r in positive for h in r["handles"]})
        sites = sorted({r["site"] for r in positive})
        key = f"entity:{entity}:{chain}"
        old = history.get(key)
        if old:
            new = set(hs) - set(old["handles"])
            if old["ruling"] == "supported":
                continue
            if not new and not (old["ruling"] == "hypothesis" and key in watch):
                continue
        result.append({"key": key, "entity": entity, "chain": chain, "handles": hs,
                       "sites": sites, "evidence": rows})
    result.sort(key=lambda c: (-len(c["handles"]), -len(c["sites"]), hashlib.sha256(c["key"].encode()).hexdigest()))
    return result[:CAP], result


def make_prompt(role, stories, prior=None):
    if role not in ROLES:
        raise ValueError("role")
    payload = []
    for c in stories:
        rows = [dict(r, handles=r["handles"][:8]) for r in c["evidence"]]
        payload.append({k: c[k] for k in ("key", "entity", "chain", "sites")} | {"evidence": rows})
    instructions = {
        "advocate": "For each site holding evidence, give its strongest evidence-based argument separately. Do not invent events.",
        "skeptic": "Attack source independence, temporal order, contradictions, staleness and single-source dependence. Give attack_tags.",
        "rebuttal": "Answer the skeptic once using only the supplied summaries and prior arguments.",
        "judge": "Rule supported, hypothesis or rejected. Require a contiguous ordered causal path of at least three kinds. "
                 "Repeated handles are echoes, not independent events. Weigh contradictions and uncertainty. "
                 "Return ruling, reason_tag and chain; never infer an unseen event.",
    }
    schema = ("{story_key: {ruling: supported|hypothesis|rejected, reason_tag: none|attack tag, chain: chain name}}"
              if role == "judge" else
              "{story_key: {text: concise argument, attack_tags: [attack tag]}}")
    keys = {c["key"] for c in stories}
    prior = {r: {k: v for k, v in replies.items() if k in keys} for r, replies in (prior or {}).items()}
    p = canonical({"role": role, "instructions": instructions[role], "schema": schema,
                   "attack_tags": sorted(TAGS), "causal_chains": CHAINS, "stories": payload, "prior": prior or {}})
    scan(p, prompt=True)
    if len(p) * 1.2 / 4 > 100000:
        raise ValueError("prompt bound; split per story")
    return p


def prompt_batches(role, stories, prior=None):
    try:
        return [(stories, make_prompt(role, stories, prior))]
    except ValueError as error:
        if str(error) != "prompt bound; split per story":
            raise
        return [([c], make_prompt(role, [c], prior)) for c in stories]


def validate_reply(role, raw, stories, forbidden=()):
    scan(raw, forbidden, prompt=True)
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != {c["key"] for c in stories}:
        raise ValueError("reply story keys")
    for c in stories:
        row = obj[c["key"]]
        if not isinstance(row, dict):
            raise ValueError("reply row")
        if role == "judge":
            if (set(row) != {"ruling", "reason_tag", "chain"}
                    or row["ruling"] not in {"supported", "hypothesis", "rejected"}
                    or row["reason_tag"] not in TAGS | {"none"} or row["chain"] != c["chain"]):
                raise ValueError("ruling schema")
        elif (set(row) != {"text", "attack_tags"} or not isinstance(row["text"], str)
              or not isinstance(row["attack_tags"], list) or any(t not in TAGS for t in row["attack_tags"])):
            raise ValueError("argument schema")
    return obj


def lineage_ok(story, summaries):
    expected = {h for s in summaries for r in s["entries"] if r["entity"] == story["entity"]
                for h in r["handles"]}
    return bool(story["handles"]) and set(story["handles"]) <= expected
