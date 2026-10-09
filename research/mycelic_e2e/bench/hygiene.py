"""Lexical-contract checks for a template bank, using the deterministic provider's own tokenizer (PLAN_v1 §B.5).

Why: ``fake.answer_from_evidence`` keeps chunks sharing >= 2 content tokens with the question, and ``fake.evaluate_responses``
clusters two responses sharing >= 3 content tokens (then flags numeric / negation "disagreements"). A bank is valid when
1. a target observation shares >= 3 content tokens with its question (context words + "service");
2. any record about ANOTHER context shares <= 1 content token with any question wording (department names included);
3. records of DIFFERENT patterns never cluster: they share < 3 content tokens outside the entity / context / number slots,
   "service" included (so every observation, decoy and correction template carries at most one boilerplate content word, and
   the two goal-only vocabularies - hidden pattern and in-scope decoy - are disjoint);
4. no observation template contains a negation word; the loop's generic question ("What recurring operational blockers related
   to <domain> ...") retrieves goal-only records (>= 2 shared tokens) and NO regular record (<= 1).
"""
from __future__ import annotations

import itertools

from mycelic.models.fake import content_tokens, has_negation

from .schema import TemplateBank

SLOT_TOKENS = {"zzsvc", "zzalpha", "zzbeta", "55"}
LOOP_QUESTION = "What recurring operational blockers related to {domain} have you recorded, and what caused them?"
TITLES = ("Note", "Update", "Entry", "Memo", "Remark", "Summary")        # shared role-neutral titles (world.py draws from this pool)


def render(t: str, **kw: str) -> str:
    return t.format(**{"svc": "zzsvc", "ctx": "zzalpha zzbeta", "n": "55", "a": "ZzDeptOne", "b": "ZzDeptTwo", "fam": "zzfam", **kw})


def toks(t: str, **kw: str) -> set[str]:
    return set(content_tokens(render(t, **kw)))


def boilerplate(t: str) -> set[str]:
    return toks(t) - SLOT_TOKENS - {"service"}


def violations(bank: TemplateBank, goal_obs: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    qs = list(bank.question_templates + bank.current_question_templates)
    dept_names = [d for d, _ in bank.departments]
    regular = {"obs": bank.obs_templates, "decoy": bank.decoy_templates, "correction": bank.correction_templates}
    goal = {"goal_obs": goal_obs, "goal_decoy": bank.goal_decoy_templates}
    others = {**regular, **goal, "filler": bank.filler_templates, "title": TITLES}
    # (2) questions vs every record text about another context
    for qt in qs:
        for da, db in itertools.permutations(dept_names, 2):
            q = toks(qt, a=da, b=db)
            for kind, tpls in others.items():
                for t in tpls:
                    r = set(content_tokens(render(t, ctx="qqother wwother")))
                    if len(q & r) >= 2:
                        out.append(f"{kind} {t[:50]!r} shares {sorted(q & r)} with question {qt[:40]!r} [{da}|{db}]")
    # (3) cross-pattern clustering
    for kind, tpls in regular.items():
        for t in tpls:
            if len(boilerplate(t)) > 1:
                out.append(f"{kind} template carries {sorted(boilerplate(t))} (more than one boilerplate content word): {t[:50]!r}")
    allr = [t for tpls in regular.values() for t in tpls]
    for t1, t2 in itertools.combinations_with_replacement(allr, 2):
        a = set(content_tokens(t1.format(svc="aaone", ctx="bbone ccone", n="11")))
        b = set(content_tokens(t2.format(svc="aatwo", ctx="bbtwo cctwo", n="22")))
        if len(a & b) >= 3:
            out.append(f"templates cluster across patterns: {t1[:40]!r} / {t2[:40]!r} share {sorted(a & b)}")
    for t1 in goal_obs:
        for t2 in bank.goal_decoy_templates:
            a = set(content_tokens(t1.format(svc="aaone", ctx="bbone ccone", n="11")))
            b = set(content_tokens(t2.format(svc="aatwo", ctx="bbtwo cctwo", n="22")))
            if len(a & b) >= 3:
                out.append(f"goal pattern and in-scope decoy cluster: {t1[:40]!r} / {t2[:40]!r} share {sorted(a & b)}")
    for t in allr + list(goal_obs) + list(bank.goal_decoy_templates):
        if has_negation(render(t)):
            out.append(f"negation in {t[:40]!r}")
        if "{svc}-service" not in t or "{n}" not in t or ("{ctx}" not in t and t not in bank.decoy_templates):
            out.append(f"template misses svc/ctx/n: {t[:50]!r}")
    # (4) the loop's generic question
    for _, dom in bank.departments:
        lq = set(content_tokens(LOOP_QUESTION.format(domain=dom)))
        for t in goal_obs:
            if len(lq & toks(t)) < 2:
                out.append(f"goal_obs {t[:40]!r} does not match the loop question for {dom}: {sorted(lq & toks(t))}")
        for t in bank.goal_decoy_templates:
            if len(lq & toks(t)) < 2:
                out.append(f"goal_decoy {t[:40]!r} does not match the loop question for {dom}")
        for kind, tpls in regular.items():
            for t in tpls:
                if len(lq & toks(t, ctx="qqother wwother")) >= 2:
                    out.append(f"{kind} {t[:40]!r} is retrieved by the loop question for {dom}")
    # (1) targets vs questions
    for qt in qs:
        q = toks(qt)
        for t in bank.obs_templates + bank.correction_templates:
            if len(q & toks(t)) < 3:
                out.append(f"target observation {t[:40]!r} shares too little with question {qt[:40]!r}")
    return out
