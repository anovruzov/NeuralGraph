"""Pushdown verification at HQ (G6): ask the sites a narrow structured question about a candidate and gate the answer.

``questions`` builds the question body from a pack template (the window, the template choice, the id), ``gate`` is the
deterministic commit gate ported from vr034p (supported, hypothesis, contested, stale or rejected, from bucketed
verdicts), and ``orchestrator`` routes a question to the contributing and sibling sites, delivers it on worker
threads with a deadline, takes the verdicts in through the shared validator and keeps versioned, append-only
conclusions in HQ's collective store. The sites answer inside their boundaries (``edge/verify.py``); nothing here
imports a site-side module, a harness or an inference module. This file imports nothing.
"""
