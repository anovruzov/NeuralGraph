"""Source ACLs and audience checks (product decision 2026-10-08; INGESTION.md §4.7, §10.8).

Every canonical record carries its source's ACL, ``permissions = {visibility: public|members|private, member_ids |
membership_ref}``. Disclosure of a member-restricted or private record — through ``answer_question``, ``raw_for_ref`` or a
manual response — is allowed only when the *requesting audience* is entirely inside the source's members, or when the
audience is the holder owner alone. Private sources are additionally not exportable until the owner opts the source in.

The requesting audience travels with the request as ``{"principal_ids": [...], "complete": bool, "owner": bool}``:

* ``principal_ids`` — everyone who will be able to read the result (Mycelic principal ids);
* ``complete`` — True only when ``principal_ids`` enumerates that audience completely. An incomplete or absent audience is
  never "inside" anything, so restricted records are withheld (fail closed);
* ``owner`` — the result goes only to the holder owner (the owner's own search, a personal agent, the raw view by the
  owner). The owner sees everything their holder has.

Checks are made at use time (answer, raw access), so a membership change or an opt-out takes effect immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .events import VISIBILITIES, Permissions

VISIBILITY_RANK = {"public": 0, "members": 1, "private": 2}


@dataclass(frozen=True)
class Audience:
    principal_ids: frozenset[str] = frozenset()
    complete: bool = False
    owner: bool = False

    @classmethod
    def from_payload(cls, value: Any) -> "Audience | None":
        if value is None:
            return None
        if isinstance(value, Audience):
            return value
        if not isinstance(value, Mapping):
            return cls()
        ids = value.get("principal_ids") or ()
        if isinstance(ids, str):
            ids = [ids]
        return cls(principal_ids=frozenset(str(x) for x in ids if x), complete=bool(value.get("complete")), owner=bool(value.get("owner")))

    @classmethod
    def owner_only(cls, principal_id: str | None = None) -> "Audience":
        return cls(principal_ids=frozenset([principal_id]) if principal_id else frozenset(), complete=True, owner=True)

    def to_payload(self) -> dict[str, Any]:
        return {"principal_ids": sorted(self.principal_ids), "complete": self.complete, "owner": self.owner}


REF_SEPARATOR = "&"                       # several membership references on one record: all must hold


def narrow(source: Permissions, record: Permissions | None) -> Permissions:
    """A record may be more restricted than its source, never less: the stricter visibility wins and members intersect."""
    if record is None:
        return source
    vis = max(source.visibility, record.visibility, key=lambda v: VISIBILITY_RANK[v])
    if vis == "public":
        return Permissions("public", acl_version=record.acl_version or source.acl_version)
    if source.visibility == "public":
        return Permissions(vis, record.member_ids, record.membership_ref, record.acl_version)
    if record.visibility == "public":
        return Permissions(vis, source.member_ids, source.membership_ref, source.acl_version)
    # both restricted: members must be in both lists; membership_refs on both sides are all kept ('a&b') and intersected
    # at use time, so a thread inside a channel is visible only to people in both
    if source.member_ids and record.member_ids:
        members = tuple(sorted(set(source.member_ids) & set(record.member_ids)))
    else:
        members = record.member_ids or source.member_ids
    refs = sorted({r for ref in (record.membership_ref, source.membership_ref) if ref for r in ref.split(REF_SEPARATOR) if r})
    return Permissions(vis, members, REF_SEPARATOR.join(refs) or None, record.acl_version or source.acl_version)


def members_of(perms: Permissions, resolve_ref: Callable[[str], Iterable[str]] | None = None) -> frozenset[str]:
    """Explicit member ids plus the members of ``membership_ref``. When both are present the record is visible to the
    intersection (the explicit list narrows the referenced membership)."""
    explicit = frozenset(perms.member_ids)
    if perms.membership_ref:
        if resolve_ref is None:
            return frozenset()                 # an unresolvable membership is no membership (fail closed)
        referenced: frozenset[str] | None = None
        for ref in perms.membership_ref.split(REF_SEPARATOR):
            if ref:
                got = frozenset(resolve_ref(ref))
                referenced = got if referenced is None else referenced & got
        referenced = referenced or frozenset()
        return explicit & referenced if explicit else referenced
    return explicit


def decide(perms: Permissions | None, audience: Audience | None, *, exportable: bool, owner_ids: Iterable[str] = (),
           resolve_ref: Callable[[str], Iterable[str]] | None = None) -> tuple[bool, str]:
    """``(allowed, reason)`` for disclosing one record to ``audience``.

    ``perms is None`` means the document did not come through a connector (a direct upload): it is governed by the holder
    export policy alone, as before. ``exportable`` is the source opt-in that private sources need.
    """
    if perms is None:
        return True, "upload"
    if audience is not None and audience.owner:
        return True, "owner"
    if perms.visibility not in VISIBILITIES:
        return False, "unknown_visibility"
    if perms.visibility == "public":
        return True, "public"
    if perms.visibility == "private" and not exportable:
        return False, "private_not_exportable"
    if audience is None or not audience.complete or not audience.principal_ids:
        return False, "audience_unknown"
    members = members_of(perms, resolve_ref) | frozenset(owner_ids)
    if audience.principal_ids <= members:
        return True, "audience_inside_members"
    return False, "audience_outside_members"
