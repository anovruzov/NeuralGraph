"""Agent identity, organization identity and the authorization rules Mycelic enforces.

* **Agent identity** is a per-agent API key ``mk_<agent_id>.<secret>``.  Only its SHA-256 is stored; the key is
  shown once, at registration.  The agent id inside the key is a routing hint, never trusted: the secret is
  compared in constant time against the stored hash of that agent.
* **Organization identity** is the enterprise root of the agent's path.  Everything an agent reads or writes
  is confined to that organization.
* **Administrator** is the holder of ``MYCELIC_ADMIN_TOKEN`` (bootstrap, agent registration, rules, replay).

Authorization, in full:

* an agent writes memories and events only as itself (its own path; a body naming another agent is rejected);
* an agent reads agent-layer memories of its own team (or any marked ``visibility='org'``) and derived
  memories of every unit it belongs to (the memory's unit is an ancestor-or-self of the agent's path);
* an agent queries at its own team or any of its ancestor units; results are filtered by the rule above, so a
  query cannot reveal even the existence of memories outside the caller's view;
* lineage of a readable memory is returned with unreadable contributions redacted (shape kept, content
  withheld);
* an administrator can read everything on the deployment.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

from .hierarchy import AgentPath, HierarchyError, is_ancestor_or_self, unit_at_layer
from .models import Agent, Memory
from .store import MycelicStore

API_KEY_PREFIX = "mk_"


def generate_api_key(agent_id: str) -> tuple[str, str, str]:
    """Return ``(key, key_hash, key_prefix)`` for a new agent key."""
    key = f"{API_KEY_PREFIX}{agent_id}.{secrets.token_urlsafe(32)}"
    return key, hash_key(key), key[: len(API_KEY_PREFIX) + len(agent_id) + 5]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_agent_id(key: str) -> str | None:
    if not key.startswith(API_KEY_PREFIX) or "." not in key:
        return None
    agent_id = key[len(API_KEY_PREFIX):].split(".", 1)[0]
    return agent_id or None


def memory_visible(m: Memory, *, agent_path: str | None, team_path: str | None) -> bool:
    """Python twin of ``MycelicStore.visible_rows``: the one place the read rule is written in code."""
    if agent_path is None or team_path is None:
        return True
    if m.layer == "agent":
        return m.visibility == "org" or m.scope.startswith(team_path + "/")
    return is_ancestor_or_self(m.scope, agent_path)


@dataclass(frozen=True)
class Principal:
    kind: str                       # 'admin' | 'agent'
    id: str
    org_id: str | None
    scopes: frozenset[str]
    agent: Agent | None = None
    path: str | None = None
    team_path: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    def has(self, scope: str) -> bool:
        return self.is_admin or scope in self.scopes

    def can_read(self, m: Memory) -> bool:
        if self.is_admin:
            return True
        if m.org_id != self.org_id:
            return False
        return memory_visible(m, agent_path=self.path, team_path=self.team_path)

    def owns(self, m: Memory) -> bool:
        return self.is_admin or m.producer_id == self.id

    def can_query_scope(self, scope: str) -> bool:
        if self.is_admin:
            return True
        if self.path is None:
            return False
        return is_ancestor_or_self(scope, self.path) or scope.startswith((self.team_path or "") + "/")

    def default_scope(self) -> str | None:
        return self.org_id

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "org_id": self.org_id, "scopes": sorted(self.scopes),
                "path": self.path, "team_path": self.team_path,
                "agent": self.agent.to_dict() if self.agent else None}

    @classmethod
    def admin(cls) -> "Principal":
        return cls(kind="admin", id="admin", org_id=None, scopes=frozenset({"admin"}))

    @classmethod
    def for_agent(cls, agent: Agent) -> "Principal":
        try:
            ap = AgentPath.parse(agent.path)
            team = ap.team_path
        except HierarchyError:
            team = unit_at_layer(agent.path, "team") or agent.path
        return cls(kind="agent", id=agent.agent_id, org_id=agent.org_id, scopes=frozenset(agent.scopes),
                   agent=agent, path=agent.path, team_path=team)


class AuthError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Authenticator:
    def __init__(self, store: MycelicStore, *, admin_token: str | None) -> None:
        self.store = store
        self.admin_token = admin_token or None

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve an ``Authorization: Bearer ...`` header to a principal or raise :class:`AuthError`."""
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError(401, "missing bearer token")
        token = authorization[7:].strip()
        if not token:
            raise AuthError(401, "missing bearer token")
        if self.admin_token and hmac.compare_digest(token.encode("utf-8"), self.admin_token.encode("utf-8")):
            return Principal.admin()
        agent_id = parse_agent_id(token)
        if agent_id is None:
            raise AuthError(401, "invalid token")
        creds = self.store.get_agent_credentials(agent_id)
        if creds is None:
            # keep timing similar to the found case
            hmac.compare_digest(hash_key(token), hash_key("x" * len(token)))
            raise AuthError(401, "invalid token")
        agent, key_hash = creds
        if not hmac.compare_digest(hash_key(token), key_hash):
            raise AuthError(401, "invalid token")
        if agent.status != "active":
            raise AuthError(403, "agent revoked")
        return Principal.for_agent(agent)


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    """Token bucket per principal. ``rps <= 0`` disables limiting."""

    rps: float = 50.0
    burst: int = 100
    max_keys: int = 20_000
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def allow(self, key: str, now: float | None = None, cost: int = 1) -> bool:
        """Take ``cost`` tokens from the bucket (a batch request costs as many as the requests it carries)."""
        if self.rps <= 0 or cost <= 0:
            return True
        now = time.monotonic() if now is None else now
        b = self._buckets.get(key)
        if b is None:
            if len(self._buckets) >= self.max_keys:
                self._prune(now)
            b = self._buckets[key] = _Bucket(tokens=float(self.burst), updated=now)
        b.tokens = min(float(self.burst), b.tokens + (now - b.updated) * self.rps)
        b.updated = now
        if b.tokens >= float(cost):
            b.tokens -= float(cost)
            return True
        return False

    def _prune(self, now: float) -> None:
        """Drop idle buckets; if the map is still full, evict the least recently used tenth (never everyone)."""
        stale = [k for k, b in self._buckets.items() if now - b.updated > 600]
        for k in stale:
            del self._buckets[k]
        if len(self._buckets) >= self.max_keys:
            oldest = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)[: max(1, self.max_keys // 10)]
            for k, _ in oldest:
                del self._buckets[k]
