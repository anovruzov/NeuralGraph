"""One model interface; no backend receives the world, answer files or a filesystem path."""
from __future__ import annotations

import json
import urllib.request

from .protocol import CHAINS, canonical


class Fixture:
    """Deterministic code-only ablation, not a substitute for a model result."""

    def complete(self, prompt):
        obj = json.loads(prompt)
        out = {}
        for c in obj["stories"]:
            positive = [r for r in c["evidence"] if not r["negated"]]
            kinds = {r["kind"] for r in positive}
            times = {k: min(r["week"] for r in positive if r["kind"] == k) for k in kinds}
            path = CHAINS[c["chain"]]
            supported = any(all(k in kinds for k in path[i:i+3]) and
                            times[path[i]] <= times[path[i+1]] <= times[path[i+2]]
                            for i in range(len(path)-2))
            reason = "none"
            if any(r["negated"] for r in c["evidence"]):
                supported, reason = False, "retraction_contradiction"
            if obj["role"] == "judge":
                out[c["key"]] = {"ruling": "supported" if supported else "hypothesis",
                                  "reason_tag": reason, "chain": c["chain"]}
            else:
                out[c["key"]] = {"text": "Code-only fixture argument.",
                                  "attack_tags": [] if reason == "none" else [reason]}
        return canonical(out)


class LocalEndpoint:
    """Local model server adapter; deliberately disabled unless instantiated by an explicit caller."""

    def __init__(self, endpoint, model):
        if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("only a local endpoint is authorized")
        self.endpoint, self.model = endpoint, model

    def complete(self, prompt):
        body = canonical({"model": self.model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0, "max_tokens": 4096}).encode()
        request = urllib.request.Request(self.endpoint + "/v1/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)["choices"][0]["message"]["content"]


class SessionExchange:
    """The h-sim pilot pauses for an audited session reply, without a paid API key."""

    def complete(self, prompt):
        raise RuntimeError("session exchange requires an audited reply via advance; no implicit API fallback")
