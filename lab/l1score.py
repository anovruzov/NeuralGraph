"""Latency test L001's scoring (lab experiment ``l1``): pure functions over site answers, per-record replies and the
timings, used by the plan job (``lab.l1 prereg``), the aggregate and the tests. ``docs/collective/L001/CHOICE-L001.md``
is the rule; the K numbers are its section "Amended before any run".

**A site answer** is one mine's verdict on one question (rule 7). It is scored when the key judge's verdict there is
``confirm`` or ``refute``; it is correct when a judge's verdict equals the key's (``unknown`` never is). Sensitivity is
the share of the key's confirms a judge confirmed, specificity the share of its refutes it refuted, balanced accuracy
their mean; accuracy and the unknown share are reported too. A model's site answer with a transport failure in any of
its calls is left out of every judge's scores for that model and counted; more than :data:`WITHHOLD_SHARE` left out
gives the model no verdict.

**Intervals** (rule 8, K7): a percentile bootstrap over mines, ``B`` draws of ``random.Random("l1:<seed>")``, each
draw ``n`` mines with replacement (``randrange``, as ``stats.cluster_bootstrap_mean`` draws) from the sorted list of
mines with a scored answer; a mine's answers are drawn together, and every judge and model is scored on the same
draws (:func:`draws`). A draw with no key confirm or no key refute gives no balanced accuracy: such draws are counted
and left out, and above :data:`WITHHOLD_SHARE` of the draws the interval is withheld. The interval is the 2.5th and
the 97.5th percentile (``stats.percentile``). The paired differences ``D_lex`` (a model's balanced accuracy minus the
lexical judge's) and ``D_route`` (minus the route-role baseline's) come from the same draws (:func:`paired`).

**The headline** (K3, :func:`headline`): ``better`` when the lower ends of both intervals are above 0;
``better_than_lexical_only`` when D_lex's is and D_route's is not; ``worse`` when D_lex's upper end is below 0;
``not_told_apart`` otherwise; ``no_verdict`` when an interval is withheld, too many answers are left out, or a unit did
not finish.

**Beside it, deciding nothing:** the predicate-only bound (:func:`predicate_bound`), the strata (K2,
:func:`by_stratum`), the gate's agreement with the key beside a constant status (K8, :func:`gate_agreement`), the
per-record measures (rule 10, :func:`per_record`, bootstrapped over records with seed ``l1:<seed>:records``) and the
latency figures (rule 6, :func:`latency`).

**Numbers** (K12, :func:`r3`): seconds and shares are rounded to 3 places; counts are integers. Nothing here writes a
total in milliseconds.
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import decide
from mycelic.collective.experiments.e1_extract import failure_class

VERDICTS = ("confirm", "refute", "unknown")
SCORED = ("confirm", "refute")
METRICS = ("sensitivity", "specificity", "balanced_accuracy", "accuracy", "unknown_share")
STRATA = ("codes", "text_only", "sibling")
WITHHOLD_SHARE = 0.05
HEADLINES = ("better", "better_than_lexical_only", "worse", "not_told_apart", "no_verdict")
UNKNOWN_REASONS = ("unclear", "degraded", "no_records", "timeout", "error", "budget", "not_master_data", "no_secret")
ALPHA = 0.05


def r3(value: float | None) -> float | None:
    """K12: a time in seconds or a share, at most 3 decimals."""
    return None if value is None else round(float(value), 3)


def seed_of(bootstrap_seed: int) -> str:
    return f"l1:{bootstrap_seed}"


def record_seed_of(bootstrap_seed: int) -> str:
    return f"l1:{bootstrap_seed}:records"


# --------------------------------------------------------------------------------------------------- site answers

def site_answers(questions: Sequence[Mapping[str, Any]], verdicts: Mapping[str, Mapping[str, Mapping[str, str]]],
                 key: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    """One row per question and routed mine, in slot then mine order: ``{"slot", "mine", "stratum", "key", <judge>:
    verdict...}``. ``questions`` carry ``slot``, ``routes`` and ``strata``; ``verdicts[judge][slot][mine]`` and
    ``key[slot][mine]`` are verdicts (``confirm``, ``refute``, ``unknown``); a judge without a verdict at a mine gets
    None."""
    rows = []
    for q in questions:
        slot = str(q["slot"])
        for mine in sorted(q["routes"]):
            row = {"slot": int(q["slot"]), "mine": mine, "stratum": q["strata"][mine], "key": key[slot][mine]}
            for judge, by_slot in verdicts.items():
                row[judge] = by_slot.get(slot, {}).get(mine)
            rows.append(row)
    return rows


def scored(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The scored site answers: the key confirms or refutes there."""
    return [r for r in rows if r["key"] in SCORED]


def draws(mines: Sequence[str], b: int, seed: str) -> list[list[int]]:
    """``b`` draws of ``len(mines)`` mine indices with replacement, from ``random.Random(seed)``."""
    rng = random.Random(seed)
    n = len(mines)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(b)] if n else []


def _counts(rows: Iterable[Mapping[str, Any]], judge: str) -> list[int]:
    """[key confirms, key refutes, judge confirms where the key confirms, judge refutes where the key refutes,
    judge unknowns, answers]."""
    c = [0, 0, 0, 0, 0, 0]
    for r in rows:
        v = r.get(judge)
        c[5] += 1
        c[4] += int(v == "unknown" or v is None)
        if r["key"] == "confirm":
            c[0] += 1
            c[2] += int(v == "confirm")
        elif r["key"] == "refute":
            c[1] += 1
            c[3] += int(v == "refute")
    return c


def _metrics(c: Sequence[int]) -> dict[str, float | None]:
    sens = c[2] / c[0] if c[0] else None
    spec = c[3] / c[1] if c[1] else None
    ba = (sens + spec) / 2 if sens is not None and spec is not None else None
    return {"sensitivity": sens, "specificity": spec, "balanced_accuracy": ba,
            "accuracy": (c[2] + c[3]) / c[5] if c[5] else None, "unknown_share": c[4] / c[5] if c[5] else None}


def _interval(values: list[float], n_draws: int, empty: int) -> dict[str, Any]:
    withheld = n_draws > 0 and empty > WITHHOLD_SHARE * n_draws
    if withheld or not values:
        return {"ci_low": None, "ci_high": None, "empty_draws": empty, "withheld": withheld or not values}
    return {"ci_low": r3(stats.percentile(values, 100 * ALPHA / 2)),
            "ci_high": r3(stats.percentile(values, 100 * (1 - ALPHA / 2))), "empty_draws": empty, "withheld": False}


class Drawn:
    """Per-draw sums of :func:`_counts` for a set of judges over the same mines and draws."""

    def __init__(self, rows: Sequence[Mapping[str, Any]], judges: Sequence[str], mines: Sequence[str],
                 drawn: Sequence[Sequence[int]]) -> None:
        by_mine = {m: [r for r in rows if r["mine"] == m] for m in mines}
        self.mines = list(mines)
        self.judges = list(judges)
        self.point = {j: _counts(rows, j) for j in judges}
        per_mine = {j: [_counts(by_mine[m], j) for m in mines] for j in judges}
        self.sums: dict[str, list[list[int]]] = {j: [] for j in judges}
        for d in drawn:
            for j in judges:
                acc = [0, 0, 0, 0, 0, 0]
                for i in d:
                    c = per_mine[j][i]
                    for k in range(6):
                        acc[k] += c[k]
                self.sums[j].append(acc)
        self.n_draws = len(drawn)

    def score(self, judge: str) -> dict[str, Any]:
        """Each metric's point value and interval, with its empty draws."""
        point = _metrics(self.point[judge])
        reps = [_metrics(c) for c in self.sums[judge]]
        out: dict[str, Any] = {"answers": self.point[judge][5], "key_confirms": self.point[judge][0],
                               "key_refutes": self.point[judge][1]}
        for m in METRICS:
            values = [x[m] for x in reps if x[m] is not None]
            out[m] = {"value": r3(point[m]), **_interval(values, self.n_draws, self.n_draws - len(values))}
        return out

    def paired(self, model: str, other: str) -> dict[str, Any]:
        """The model's balanced accuracy minus ``other``'s on the same draws."""
        a, b = _metrics(self.point[model])["balanced_accuracy"], _metrics(self.point[other])["balanced_accuracy"]
        values = []
        for ca, cb in zip(self.sums[model], self.sums[other]):
            x, y = _metrics(ca)["balanced_accuracy"], _metrics(cb)["balanced_accuracy"]
            if x is not None and y is not None:
                values.append(x - y)
        diff = a - b if a is not None and b is not None else None
        return {"against": other, "value": r3(diff), **_interval(values, self.n_draws, self.n_draws - len(values))}


def empty_draw_share(rows: Sequence[Mapping[str, Any]], b: int, seed: str) -> dict[str, Any]:
    """K7: over the key's scored site answers, the share of rule 8's draws with no key confirm or no key refute."""
    rows = scored(rows)
    mines = sorted({r["mine"] for r in rows})
    drawn = draws(mines, b, seed)
    d = Drawn(rows, ["key"], mines, drawn)
    empty = sum(1 for c in d.sums["key"] if c[0] == 0 or c[1] == 0)
    return {"draws": len(drawn), "empty": empty, "share": r3(empty / len(drawn)) if drawn else None,
            "stops": not drawn or empty > WITHHOLD_SHARE * len(drawn), "mines": len(mines)}


def predicate_bound(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rule 8: half the sum, over the questions, of max(P_q / P, N_q / N), where P and N are the key's confirms and
    refutes among the scored site answers and P_q, N_q those of question q: each question answered the one way that
    scores more. Optimistic, with no interval; None without a confirm and a refute."""
    rows = scored(rows)
    p = sum(1 for r in rows if r["key"] == "confirm")
    n = sum(1 for r in rows if r["key"] == "refute")
    if not p or not n:
        return {"value": None, "questions": len({r["slot"] for r in rows}), "answers": len(rows)}
    total = 0.0
    for slot in sorted({r["slot"] for r in rows}):
        pq = sum(1 for r in rows if r["slot"] == slot and r["key"] == "confirm")
        nq = sum(1 for r in rows if r["slot"] == slot and r["key"] == "refute")
        total += max(pq / p, nq / n)
    return {"value": r3(total / 2), "questions": len({r["slot"] for r in rows}), "answers": len(rows)}


def score_judges(rows: Sequence[Mapping[str, Any]], judges: Sequence[str], *, b: int, seed: str,
                 mines: Sequence[str] | None = None) -> tuple[dict[str, Any], Drawn]:
    """Every judge's scores over the scored rows on the same draws (``mines`` fixes the draw's mine list; default the
    mines with a scored row)."""
    rows = scored(rows)
    mines = sorted({r["mine"] for r in rows}) if mines is None else list(mines)
    d = Drawn(rows, judges, mines, draws(mines, b, seed))
    return {j: d.score(j) for j in judges}, d


def by_stratum(rows: Sequence[Mapping[str, Any]], judges: Sequence[str], *, b: int, seed: str,
               mines: Sequence[str]) -> dict[str, Any]:
    """K2: per judge and stratum, the counts of each verdict and how many scored answers equal the key's; and, with
    rule 8's interval on the same draws, sensitivity over the codes contributors and specificity over the text-only
    contributors and over the siblings, apart."""
    out: dict[str, Any] = {}
    drawn = draws(list(mines), b, seed)
    for stratum in STRATA:
        part = [r for r in rows if r["stratum"] == stratum]
        part_scored = scored(part)
        d = Drawn(part_scored, judges, list(mines), drawn)
        entry: dict[str, Any] = {}
        for j in judges:
            counts = {v: sum(1 for r in part if r.get(j) == v) for v in VERDICTS}
            counts["none"] = sum(1 for r in part if r.get(j) is None)
            metric = "sensitivity" if stratum == "codes" else "specificity"
            s = d.score(j)
            entry[j] = {"verdicts": counts, "scored": len(part_scored),
                        "equal_key": sum(1 for r in part_scored if r.get(j) == r["key"]), metric: s[metric]}
        out[stratum] = entry
    return out


def construction(rows: Sequence[Mapping[str, Any]], records: Mapping[str, Mapping[str, int]],
                 codes_with_text: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """K2's construction counts (reported, never refused): mines where the key does not confirm at a codes
    contributor or does not refute at a text-only contributor or at a sibling with records; mines where the lexical
    judge does not confirm at a text-only contributor or does not refute at a sibling with records; and the lexical
    judge's confirms and refutes at codes contributors that sent no ``text_only`` cell. ``records[slot][mine]`` is the
    mine's retrieved count."""
    out = dict.fromkeys(("key_not_confirm_codes", "key_not_refute_text_only", "key_not_refute_sibling",
                         "lexical_not_confirm_text_only", "lexical_not_refute_sibling",
                         "lexical_confirm_codes_only", "lexical_refute_codes_only"), 0)
    for r in rows:
        slot = str(r["slot"])
        has_records = records[slot][r["mine"]] > 0
        if r["stratum"] == "codes":
            out["key_not_confirm_codes"] += int(r["key"] != "confirm")
            if r["mine"] not in codes_with_text.get(slot, ()):
                out["lexical_confirm_codes_only"] += int(r.get("lexical") == "confirm")
                out["lexical_refute_codes_only"] += int(r.get("lexical") == "refute")
        elif r["stratum"] == "text_only":
            out["key_not_refute_text_only"] += int(r["key"] != "refute")
            out["lexical_not_confirm_text_only"] += int(r.get("lexical") != "confirm")
        elif has_records:
            out["key_not_refute_sibling"] += int(r["key"] != "refute")
            out["lexical_not_refute_sibling"] += int(r.get("lexical") != "refute")
    return out


def gate_agreement(statuses: Mapping[str, Mapping[str, str]], key: Mapping[str, str]) -> dict[str, Any]:
    """K8 and rule 10: per judge, how many questions' gate status equals the key's, and how many agree on
    ``supported`` or not; beside them the key's statuses and the agreement a constant status gets (the number of
    questions with the key's most frequent status)."""
    counts: dict[str, int] = {}
    for status in key.values():
        counts[status] = counts.get(status, 0) + 1
    out: dict[str, Any] = {"key_statuses": dict(sorted(counts.items())), "questions": len(key),
                           "constant": max(counts.values()) if counts else 0, "judges": {}}
    for judge, by_slot in statuses.items():
        same = sum(1 for slot, s in key.items() if by_slot.get(slot) == s)
        supported = sum(1 for slot, s in key.items()
                        if by_slot.get(slot) is not None and (by_slot[slot] == "supported") == (s == "supported"))
        out["judges"][judge] = {"equal": same, "supported_agrees": supported}
    return out


def headline(*, finished: bool, measured: bool, left_out_share: float | None, d_lex: Mapping[str, Any] | None,
             d_route: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """K3's headline and, when it is ``no_verdict``, why: ``incomplete`` (one of the model's units did not finish),
    ``left_out`` (more than :data:`WITHHOLD_SHARE` of the answers are left out) or ``withheld`` (an interval is
    withheld). (None, ``not_measured``) when a complete model is not a model measurement on the runner: L001 does not
    read it at all."""
    if not finished:
        return "no_verdict", "incomplete"
    if not measured:
        return None, "not_measured"
    if left_out_share is None or left_out_share > WITHHOLD_SHARE:
        return "no_verdict", "left_out"
    if d_lex is None or d_route is None or d_lex.get("withheld") or d_route.get("withheld"):
        return "no_verdict", "withheld"
    if d_lex["ci_low"] > 0 and d_route["ci_low"] > 0:
        return "better", None
    if d_lex["ci_low"] > 0:
        return "better_than_lexical_only", None
    if d_lex["ci_high"] < 0:
        return "worse", None
    return "not_told_apart", None


# --------------------------------------------------------------------------------------------------- per record

def record_verdict(reply: Mapping[str, str] | None) -> str:
    """The verifier's rule over one record (``decide``): yes and yes confirms; a mention with no description and no
    unclear answer refutes; anything else, and no valid reply, is unknown."""
    w = WindowRecord(record_ref="r", iso_week="", root_ref="r", reporter_id=None, language=None, codes=[],
                     structured={}, narrative="")
    if reply is None:
        return decide([w], [], 1).verdict
    return decide([w], [(w, reply)], 0).verdict


def answer_pair(reply: Mapping[str, str] | None) -> str:
    return "failed" if reply is None else f"{reply['mentions_entity']}_{reply['describes_predicate']}"


def per_record(lines: Sequence[Mapping[str, Any]], judges: Sequence[str], *, b: int, seed: str) -> dict[str, Any]:
    """Rule 10's per-record measures, J001's: each judged record (a mine and its index in the retrieved list) is a
    positive for a question when it was filed under the question's predicate. ``lines`` hold ``{"slot", "mine",
    "index", "positive", <judge>: {"mentions_entity", "describes_predicate", "error_kind"} | None}``. A judgement whose
    call ended in a transport failure is left out; a model failure is ``unknown``. Per judge: sensitivity, specificity,
    balanced accuracy and the unknown share with a percentile bootstrap over records (the same draws for every judge),
    and the answer pairs by positive and negative."""
    keyed: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for line in lines:
        keyed.setdefault((line["mine"], int(line["index"])), []).append(line)
    records = sorted(keyed)
    n = len(records)
    rng = random.Random(seed)
    drawn = [[rng.randrange(n) for _ in range(n)] for _ in range(b)] if n else []
    out: dict[str, Any] = {"records": n, "judgements": len(lines), "bootstrap": {"b": b, "seed": seed}}
    for judge in judges:
        per: list[list[int]] = []
        pairs: dict[str, dict[str, int]] = {"positive": {}, "negative": {}}
        left_out = 0
        for ref in records:
            c = [0, 0, 0, 0, 0, 0]      # positives, confirmed positives, negatives, refuted negatives, unknown, n
            for line in keyed[ref]:
                j = line.get(judge)
                if j is None:
                    continue
                if failure_class(j.get("error_kind")) == "transport":
                    left_out += 1
                    continue
                reply = None if j.get("mentions_entity") is None else {
                    "mentions_entity": j["mentions_entity"], "describes_predicate": j["describes_predicate"]}
                verdict = record_verdict(reply)
                kind = "positive" if line["positive"] else "negative"
                pairs[kind][answer_pair(reply)] = pairs[kind].get(answer_pair(reply), 0) + 1
                c[5] += 1
                c[4] += int(verdict == "unknown")
                if line["positive"]:
                    c[0] += 1
                    c[1] += int(verdict == "confirm")
                else:
                    c[2] += 1
                    c[3] += int(verdict == "refute")
            per.append(c)
        total = [sum(c[k] for c in per) for k in range(6)]

        def ms(c: Sequence[int]) -> dict[str, float | None]:
            sens = c[1] / c[0] if c[0] else None
            spec = c[3] / c[2] if c[2] else None
            return {"sensitivity": sens, "specificity": spec,
                    "balanced_accuracy": (sens + spec) / 2 if sens is not None and spec is not None else None,
                    "unknown_share": c[4] / c[5] if c[5] else None}

        reps = []
        for d in drawn:
            acc = [0, 0, 0, 0, 0, 0]
            for i in d:
                for k in range(6):
                    acc[k] += per[i][k]
            reps.append(ms(acc))
        point = ms(total)
        entry: dict[str, Any] = {"judged": total[5], "positives": total[0], "negatives": total[2],
                                 "left_out": left_out,
                                 "pairs": {k: dict(sorted(v.items())) for k, v in pairs.items()}}
        for m in ("sensitivity", "specificity", "balanced_accuracy", "unknown_share"):
            values = [x[m] for x in reps if x[m] is not None]
            entry[m] = {"value": r3(point[m]), **_interval(values, len(drawn), len(drawn) - len(values))}
        out[judge] = entry
    return out


# --------------------------------------------------------------------------------------------------- latency

def _pct(values: Sequence[float]) -> dict[str, Any]:
    return {"n": len(values), "median": r3(stats.percentile(values, 50)), "p95": r3(stats.percentile(values, 95))}


POOLED_WITH_CONTENDED = ("between_mines_s", "gate_s", "judge_call_s", "model_calls", "question_build_s")


def latency(questions: Sequence[Mapping[str, Any]], call_latencies_s: Mapping[int, Sequence[float]]) -> dict[str, Any]:
    """Rule 6's figures over the given questions (each a unit's ``timing`` block with its ``slot``, ``calls`` and
    ``finished``): the median and the 95th percentile (``stats.percentile``) of the time to answer, each stage (the
    question build, the gaps between mines, the gate), a mine's answer time, a judge call's latency
    (``call_latencies_s[slot]``, seconds) and the model calls per question. K6: a contended mine's time and a
    contended question's time to answer (a question with a contended mine) are apart (``mine_s_contended``,
    ``time_to_answer_s_contended``); ``pooled_with_contended`` names the figures that pool a contended question's parts
    with the others (:data:`POOLED_WITH_CONTENDED`, when there is one). Unfinished questions are counted, never pooled;
    rule 11: each one's elapsed time, a lower bound when its run gave one, is kept by slot
    (``not_finished_lower_bounds``)."""
    done = [q for q in questions if q.get("finished")]
    mines = [m for q in done for m in q["mines"] if not m["contended"]]
    contended = [m for q in done for m in q["mines"] if m["contended"]]
    busy = [q for q in done if any(m["contended"] for m in q["mines"])]
    quiet = [q for q in done if not any(m["contended"] for m in q["mines"])]
    return {"questions": len(done), "not_finished": len(questions) - len(done),
            "time_to_answer_s": _pct([q["time_to_answer_s"] for q in quiet]),
            "time_to_answer_s_contended": _pct([q["time_to_answer_s"] for q in busy]),
            "question_build_s": _pct([q["question_build_s"] for q in done]),
            "between_mines_s": _pct([g for q in done for g in q["between_s"]]),
            "gate_s": _pct([q["gate_s"] for q in done]),
            "mine_s": _pct([m["seconds"] for m in mines]),
            "mine_s_contended": _pct([m["seconds"] for m in contended]),
            "judge_call_s": _pct([x for q in done for x in call_latencies_s.get(int(q["slot"]), ())]),
            "model_calls": _pct([float(q["calls"]) for q in done]),
            "contended_questions": len(busy),
            "pooled_with_contended": list(POOLED_WITH_CONTENDED) if busy else [],
            "not_finished_lower_bounds": [{"slot": int(q["slot"]), "elapsed_lower_bound_s": (
                r3(q["elapsed_lower_bound_s"]) if finite(q.get("elapsed_lower_bound_s")) else None)}
                for q in sorted((q for q in questions if not q.get("finished")), key=lambda q: int(q["slot"]))]}


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
