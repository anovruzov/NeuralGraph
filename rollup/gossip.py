"""Seeded push/pull anti-entropy simulation with authenticated per-origin hash chains."""
from __future__ import annotations

import hashlib
import hmac
import random
import copy
from dataclasses import dataclass, field

from .protocol import canonical, candidates, digest, validate_summary


def origin_key(secret, origin):
    return hmac.new(secret, origin.encode(), hashlib.sha256).digest()


def envelope(summary, sequence, previous, secret):
    body = {"origin": summary["site"], "sequence": sequence, "previous": previous, "summary": summary}
    return body | {"auth": hmac.new(origin_key(secret, body["origin"]), canonical(body).encode(), hashlib.sha256).hexdigest()}


@dataclass
class Node:
    name: str
    secret: bytes
    logs: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    duplicates: int = 0
    refused: int = 0
    forks: int = 0

    def receive(self, msg):
        if set(msg) != {"origin", "sequence", "previous", "summary", "auth"}:
            self.refused += 1
            return "refused"
        if (not isinstance(msg["origin"], str) or not isinstance(msg["auth"], str)
                or type(msg["sequence"]) is not int or msg["sequence"] < 1):
            self.refused += 1
            return "refused"
        existing = (self.logs.get(msg["origin"], {}).get(msg["sequence"])
                    or self.pending.get(msg["origin"], {}).get(msg["sequence"]))
        if existing == msg:
            self.duplicates += 1
            return "duplicate"
        body = {k: v for k, v in msg.items() if k != "auth"}
        expected = hmac.new(origin_key(self.secret, msg["origin"]), canonical(body).encode(), hashlib.sha256).hexdigest()
        try:
            validate_summary(msg["summary"])
        except (ValueError, TypeError):
            self.refused += 1
            return "refused"
        if (not hmac.compare_digest(msg["auth"], expected) or msg["origin"] != msg["summary"]["site"]
                or type(msg["sequence"]) is not int or msg["sequence"] < 1):
            self.refused += 1
            return "refused"
        origin, seq = msg["origin"], msg["sequence"]
        log = self.logs.setdefault(origin, {})
        pending = self.pending.setdefault(origin, {})
        old = log.get(seq) or pending.get(seq)
        if old:
            if old != msg:
                self.forks += 1
                return "fork"
            self.duplicates += 1
            return "duplicate"
        pending[seq] = copy.deepcopy(msg)
        while len(log) + 1 in pending:
            index = len(log) + 1
            nxt = pending[index]
            prev = digest(log[index-1]) if index > 1 else "0" * 64
            if nxt["previous"] != prev:
                self.forks += 1
                del pending[index]
                return "fork"
            log[index] = pending.pop(index)
        return "accepted" if seq in log else "pending"

    def messages(self):
        return [m for origin in sorted(self.logs) for _, m in sorted(self.logs[origin].items())]

    def ids(self):
        return {(o, i) for o, log in self.logs.items() for i in log}


def fault_run(weeks, secret):
    names = sorted({s["site"] for rows in weeks.values() for s in rows})
    if len(names) != 15:
        raise ValueError("the ten-thousand-user plan requires fifteen nodes")
    nodes = {n: Node(n, secret) for n in names}
    rng = random.Random(0)
    slow = min(names, key=lambda n: hashlib.sha256(n.encode()).hexdigest())
    lost, retained = names[:2]
    emitted, backlog, queue = {}, {n: [] for n in names}, []
    rows, recovery = [], []
    round_clock, crash_w04_spread = 0, None
    attacks = {}

    def schedule(sender, receiver):
        # A receiver advertises its chain heads; sender retransmits gaps until acknowledged.
        have = nodes[receiver].ids()
        for m in nodes[sender].messages():
            identity = (m["origin"], m["sequence"])
            if identity in have or rng.random() < .1:
                continue
            delay = (1 + rng.randint(0, 3)) * (5 if sender == slow or receiver == slow else 1)
            queue.append((round_clock + delay, sender, receiver, m))
            if rng.random() < .2:
                queue.append((round_clock + delay + rng.randint(0, 3), sender, receiver, m))

    for week in range(1, 27):
        down = {lost, retained} if 5 <= week <= 8 else set()
        live = set(names) - down
        partitioned = 12 <= week <= 14
        components = [set(names[:7]) & live, set(names[7:]) & live] if partitioned else [live]
        components = [c for c in components if c]

        def linked(a, b):
            return a in live and b in live and any(a in c and b in c for c in components)

        if week == 5:
            # Crash at the first round; records remain local while edge emission is down.
            crash_w04_spread = sum((lost, 4) in n.ids() for n in nodes.values())
            nodes[lost] = Node(lost, secret)
        if week == 9:
            # Pull the origin chain from peers before emitting after disk loss.
            peers = [n for name, n in nodes.items() if name != lost]
            best = max(peers, key=lambda n: len(n.logs.get(lost, {})))
            for _, m in sorted(best.logs.get(lost, {}).items()):
                nodes[lost].receive(m)
            # Retain the origin's source summaries and rebuild any lost tail with its original sequence.
            for (origin, _), m in sorted(emitted.items()):
                if origin == lost:
                    nodes[lost].receive(m)
        for summary in weeks[week]:
            backlog[summary["site"]].append(summary)
        for name in sorted(live):
            for summary in backlog[name]:
                log = nodes[name].logs.get(name, {})
                seq = len(log) + 1
                msg = envelope(summary, seq, digest(log[seq-1]) if log else "0" * 64, secret)
                nodes[name].receive(msg)
                identity = (name, seq)
                if identity in emitted and emitted[identity] != msg:
                    raise ValueError("origin fork on rejoin")
                emitted[identity] = msg
            backlog[name].clear()
        if week == 1:
            first = nodes[names[0]].messages()[0]
            bad = dict(first, auth="0" * 64)
            attacks["tamper"] = nodes[names[1]].receive(bad)
            forged = dict(first, origin=names[1])
            attacks["claimed_other_origin"] = nodes[names[2]].receive(forged)
            nodes[names[1]].receive(first)
            attacks["replay"] = nodes[names[1]].receive(first)
        converged = [None for _ in components]
        for round_in_week in range(1, 25):
            round_clock += 1
            due, queue = [q for q in queue if q[0] <= round_clock], [q for q in queue if q[0] > round_clock]
            rng.shuffle(due)
            for _, sender, receiver, msg in due:
                if linked(sender, receiver):
                    nodes[receiver].receive(msg)
            for sender in sorted(live):
                peers = sorted(next(c for c in components if sender in c) - {sender})
                for peer in rng.sample(peers, min(2, len(peers))):
                    schedule(sender, peer)
                    schedule(peer, sender)
            for i, component in enumerate(components):
                wanted = {identity for identity in emitted if identity[0] in component}
                if converged[i] is None and all(wanted <= nodes[n].ids() for n in component):
                    converged[i] = round_in_week
        oracle = [m["summary"] for m in emitted.values()]
        oracle_keys = [c["key"] for c in candidates(oracle, week)[0]]
        per_node = {}
        for name, node in nodes.items():
            held = node.ids()
            own_keys = [c["key"] for c in candidates([m["summary"] for m in node.messages()], week)[0]]
            per_node[name] = {"held": len(held), "universe": len(emitted), "share": len(held & emitted.keys()) / len(emitted),
                              "candidate_equal": own_keys == oracle_keys,
                              "mismatch_cause": None if own_keys == oracle_keys else
                              ("missing summaries" if held != emitted.keys() else "other"),
                              "pending": sum(len(v) for v in node.pending.values())}
        row = {"week": week, "components": [{"nodes": sorted(c), "rounds": r if r is not None else ">24"}
                                                for c, r in zip(components, converged)], "nodes": per_node}
        rows.append(row)
        if week in (9, 15):
            recovery.append({"week": week, "rounds": converged[0] if converged[0] is not None else ">24"})
    return {"measurement": False, "synthetic": True, "seed": 0, "authentication": "shared-secret HMAC; outsider attacks only",
            "slow_node": slow, "disk_losing_node": lost, "disk_retaining_node": retained,
            "crash_w04_replicas": crash_w04_spread, "weeks": rows, "recovery": recovery, "attacks": attacks,
            "forks": sum(n.forks for n in nodes.values()), "duplicates_ignored": sum(n.duplicates for n in nodes.values()),
            "refused": sum(n.refused for n in nodes.values()),
            "limitations": ["In-memory discrete-round transport, not the shipped fabric.",
                            "Rejoin pulls from peers; source summaries rebuild any missing own tail.",
                            "Candidate comparison uses fresh lists without debate history."]}
