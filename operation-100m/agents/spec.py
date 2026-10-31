"""Independent Python implementation of the corpus rules in bench/spec/corpus_spec.json.

Written separately from bench/src/corpus.rs so the Critic does not have to trust the Rust generator: if the two
implementations disagree on any record or ground-truth set, the Critic fails the run.
"""
import datetime
import json
import os

M64 = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15
K2 = 0xD1B54A32D192ED03

F_ATTR_OFF, F_VAL, F_TMPL, F_FILLN, F_DAY, F_IMP, F_DUP, F_DIS_E, F_DIS_A, F_CHAT = 1, 2, 6, 7, 8, 9, 10, 11, 12, 13
F_FILL0 = 20
F_Q_TYPE, F_Q_SLOT, F_Q_TMPL, F_Q_ENT, F_Q_ATTR = 40, 41, 42, 43, 44

SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "bench", "spec", "corpus_spec.json")


def mix(z):
    z = (z + GOLDEN) & M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


def h(seed, i, f):
    return mix(seed ^ mix(((i * K2) & M64) ^ f))


class Corpus:
    def __init__(self, global_seed, bank, n, spec_path=SPEC_PATH):
        self.spec = json.load(open(spec_path))
        self.bank, self.n = bank, n
        self.seed = mix(global_seed ^ ((bank * GOLDEN) & M64))
        fr = self.spec["fractions_percent"]
        self.slots = n * fr["slots"] // 100
        self.updates = n * fr["updates"] // 100
        self.dups = n * fr["duplicates"] // 100
        self.contras = n * fr["contradictions"] // 100
        self.distractors = n - self.slots - self.updates - self.dups - self.contras
        self.entities = max(n // self.spec["entities_per_memory_divisor"], self.spec["min_entities"])
        self.attrs = self.spec["attributes"]
        self.base = datetime.date.fromisoformat(self.spec["time_base"])

    def mid(self, i):
        return "b%02dm%09d" % (self.bank, i)

    def name(self, e):
        perm = (((e >> 1) * 0x2545F491) + self.seed) & ((1 << 29) - 1)
        p = (perm << 1) | (e & 1)
        syl = self.spec["syllables"]
        s = [syl[(p >> (5 * k)) & 31] for k in range(6)]
        return ("".join(s[0:3])).capitalize() + " " + ("".join(s[3:6])).capitalize()

    def slot_entity(self, s):
        return s % self.entities

    def slot_attr(self, s):
        e = self.slot_entity(s)
        return ((s // self.entities) + h(self.seed, e, F_ATTR_OFF)) % len(self.attrs)

    def v0(self, s):
        return h(self.seed, s, F_VAL) % len(self.attrs[self.slot_attr(s)]["values"])

    def _other(self, s, field):
        n = len(self.attrs[self.slot_attr(s)]["values"])
        return (self.v0(s) + 1 + h(self.seed, s, field) % (n - 1)) % n

    def v1(self, s):
        return self._other(s, F_VAL + 1)

    def vc(self, s):
        return self._other(s, F_VAL + 2)

    def current(self, s):
        return self.v1(s) if s < self.updates else self.v0(s)

    def dup_slot(self, j):
        return h(self.seed, j, F_DUP) % self.slots

    def contradicted(self, s):
        return self.updates <= s < self.updates + self.contras

    def day(self, d):
        return (self.base + datetime.timedelta(days=d)).isoformat() + "T12:00:00+00:00"

    def _fill(self, i, text):
        k = h(self.seed, i, F_FILLN) % 4
        for j in range(k):
            text += " " + self.spec["filler"][h(self.seed, i, F_FILL0 + j) % len(self.spec["filler"])]
        return text

    def record(self, i):
        """(memory_id, text, status, superseded_by, observed_at, entity, attr, value_index or None, kind)"""
        dh = h(self.seed, i, F_DAY)
        b1 = self.slots
        b2 = b1 + self.updates
        b3 = b2 + self.dups
        b4 = b3 + self.contras
        if i < b1:
            kind, s = "fact", i
            e, a, v, d = self.slot_entity(s), self.slot_attr(s), self.v0(s), dh % 365
        elif i < b2:
            kind, s = "update", i - b1
            e, a, v, d = self.slot_entity(s), self.slot_attr(s), self.v1(s), 365 + dh % 180
        elif i < b3:
            kind, s = "duplicate", self.dup_slot(i - b2)
            e, a, v, d = self.slot_entity(s), self.slot_attr(s), self.current(s), 545 + dh % 30
        elif i < b4:
            kind, s = "contradiction", self.updates + (i - b3)
            d0 = h(self.seed, s, F_DAY) % 365
            e, a, v, d = self.slot_entity(s), self.slot_attr(s), self.vc(s), d0 + 1 + dh % 10
        else:
            kind, j = "distractor", i - b4
            e = h(self.seed, j, F_DIS_E) % self.entities
            a = h(self.seed, j, F_DIS_A) % len(self.attrs)
            v, d = None, dh % 575
        attr = self.attrs[a]
        if v is None:
            t = self.spec["distractor"][h(self.seed, i, F_TMPL) % len(self.spec["distractor"])]
            text = t.replace("{name}", self.name(e)).replace("{phrase}", attr["phrase"])
        else:
            t = attr["memory"][h(self.seed, i, F_TMPL) % len(attr["memory"])]
            text = t.replace("{name}", self.name(e)).replace("{value}", attr["values"][v])
        text = self._fill(i, text)
        superseded = kind == "fact" and i < self.updates
        return (self.mid(i), text, "superseded" if superseded else "active",
                self.mid(self.slots + i) if superseded else None, self.day(d), e, a, v, kind)

    def entity_attrs(self, e):
        out, s = [], e
        while s < self.slots:
            out.append(self.slot_attr(s))
            s += self.entities
        return out

    def queries(self, nq):
        qs = mix(self.seed ^ 0x5157)
        out = []
        for q in range(nq):
            t = h(qs, q, F_Q_TYPE) % 100
            pick = h(qs, q, F_Q_SLOT)
            if t < 60 or (t < 70 and self.updates == 0) or (t < 80 and self.contras == 0):
                qtype, slot = "current", pick % self.slots
            elif t < 70:
                qtype, slot = "historical", pick % self.updates
            elif t < 80:
                qtype, slot = "contradiction", self.updates + pick % self.contras
            else:
                qtype, slot = "unanswerable", None
            if slot is not None:
                e, a = self.slot_entity(slot), self.slot_attr(slot)
            else:
                e = h(qs, q, F_Q_ENT) % self.entities
                have = self.entity_attrs(e)
                start = h(qs, q, F_Q_ATTR) % len(self.attrs)
                a = next((start + k) % len(self.attrs) for k in range(len(self.attrs))
                         if (start + k) % len(self.attrs) not in have)
            attr = self.attrs[a]
            if qtype == "historical":
                text = self.spec["historical_query"].replace("{name}", self.name(e)).replace("{phrase}", attr["phrase"])
            else:
                text = attr["query"][h(qs, q, F_Q_TMPL) % len(attr["query"])].replace("{name}", self.name(e))
            out.append({"qid": q, "type": qtype, "slot": slot, "entity": e, "attr": a, "text": text})
        wanted = {q["slot"] for q in out if q["slot"] is not None}
        dups = {}
        for j in range(self.dups):
            s = self.dup_slot(j)
            if s in wanted:
                dups.setdefault(s, []).append(self.slots + self.updates + j)
        for q in out:
            s = q["slot"]
            vals = self.attrs[q["attr"]]["values"]
            rel, exp = [], []
            if s is not None:
                if q["type"] == "historical":
                    rel.append(s)
                    exp.append(vals[self.v0(s)])
                else:
                    rel.append(self.slots + s if s < self.updates else s)
                    exp.append(vals[self.current(s)])
                    if self.contradicted(s):
                        rel.append(self.slots + self.updates + self.dups + (s - self.updates))
                        exp.append(vals[self.vc(s)])
                    rel.extend(dups.get(s, []))
            q["relevant"] = [self.mid(i) for i in sorted(set(rel))]
            q["expected_values"] = exp
        return out
