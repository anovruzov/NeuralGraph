"""Lexical-contract checks for a template bank, using the deterministic provider's own tokenizer (PLAN_v1 §B.5)."""
from __future__ import annotations

import itertools

from mycelic.models.fake import content_tokens, has_negation

from .schema import TemplateBank


def render(t: str, **kw: str) -> str:
    return t.format(**{"svc": "zzsvc", "ctx": "zzalpha zzbeta", "n": "55", "a": "ZzDeptOne", "b": "ZzDeptTwo", "fam": "zzfam", **kw})


def violations(bank: TemplateBank, goal_obs: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    qs = list(bank.question_templates + bank.current_question_templates)
    dept_names = [d for d, _ in bank.departments]
    others = {"obs": bank.obs_templates, "goal_obs": goal_obs, "decoy": bank.decoy_templates, "filler": bank.filler_templates,
              "correction": bank.correction_templates}
    for qt in qs:
        for da, db in itertools.permutations(dept_names, 2):
            q = set(content_tokens(render(qt, a=da, b=db)))
            for kind, tpls in others.items():
                for t in tpls:
                    # a record about ANOTHER context must share at most one content token with the question ("service" counts)
                    r = set(content_tokens(render(t, ctx="qqother wwother")))
                    if len(q & r) >= 2:
                        out.append(f"{kind} {t[:50]!r} shares {sorted(q & r)} with question {qt[:40]!r} [{da}|{db}]")
    for t in bank.obs_templates + goal_obs + bank.correction_templates:
        if has_negation(render(t)):
            out.append(f"negation in {t[:40]!r}")
        if "{svc}-service" not in t or "{ctx}" not in t or "{n}" not in t:
            out.append(f"observation template misses svc/ctx/n: {t[:50]!r}")
    for qt in qs:
        q = set(content_tokens(render(qt)))
        for t in bank.obs_templates:
            if len(q & set(content_tokens(render(t)))) < 3:        # two context words + "service" at least
                out.append(f"target observation {t[:40]!r} shares too little with question {qt[:40]!r}")
    return out
