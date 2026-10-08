"""Approval-routed follow-up at HQ (G7): turn a ``supported`` conclusion into work for a named owner.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here measures
it. Every doc and run file of this layer carries that label (:data:`policy.BUILT_AHEAD_LABEL`).

Tiers (STRATEGY section 7): **T0** read-only, an evidence packet assembled inside each target site
(``edge/packets.py``), of which only a bucketed, suppressed, structured summary crosses; **T1** a draft (a CAPA, SCAR
or SIU-referral style form) written at HQ from structured inputs only, which a human approves, edits or rejects; **T2**
(writes to a system of record) stays off and has no executor; **T3** is not an action type. Every tier needs exactly
one human approval before it executes, T0 included.

Modules: ``policy`` (the built-ahead and outcome labels, ``as_of`` normalisation, principals, the approvers file and
the kill switch), ``ledger`` (``followups.sqlite3``: the append-only, hash-chained ledger, its chain check and the
``verify`` CLI), ``service`` (``FollowupService``: propose, assign and escalate, draft, approve, edit, reject, execute
at most once, overdue escalation and the outcome check; the follow-up state rebuilt by replay), ``drafts`` (the draft
payload, the template drafter and the id-scope scan; the only module here that imports inference), ``executors``
(the T0 packet executor and the T1 outbox executor) and ``outcome`` (the recurrence measurement after execution:
measurement only, not causal). This file imports nothing.
"""
