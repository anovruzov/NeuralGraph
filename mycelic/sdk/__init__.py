"""Mycelic SDK: a dependency-free client for the HTTP API plus an agent-local memory store.

    from mycelic.sdk import MycelicClient, LocalMemory

    local = LocalMemory("~/.mycelic/agent-7.db")                 # what this agent knows, privately
    client = MycelicClient("https://mycelic.example.com", api_key="mk_agent-7....", ca_file="corp-ca.pem")

    note_id = local.note("Port of Rotterdam terminal 3 strike announced for weeks 41-43",
                         topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.9)
    local.share(client, note_id)                                  # publish it; idempotent on retry
    res = client.query("Rotterdam strike sd-9", scope="northwind")   # keyword search: the words the notes use
    if res["answer"] is not None:                                 # None: nothing relevant visible to this agent
        lineage = client.lineage(res["answer"]["memory_id"])
        report = client.verify(res["answer"]["memory_id"])        # derived correctly, and still true?

The client is synchronous and uses only the standard library, so it drops into any agent runtime.  Retries cover
connection errors and the statuses 429, 502, 503 and 504, with exponential backoff; every other status (507, the
organization's note limit, among them) raises :class:`MycelicError` at once.
"""
from __future__ import annotations

import json
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["MycelicClient", "MycelicError", "LocalMemory"]


class MycelicError(Exception):
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MycelicClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0, ca_file: str | None = None,
                 retries: int = 4, backoff: float = 0.5, user_agent: str = "mycelic-sdk/0.1") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff = backoff
        self.user_agent = user_agent
        self._ctx = ssl.create_default_context(cafile=ca_file) if (ca_file or self.base_url.startswith("https://")) else None

    # ---------------------------------------------------------------- transport
    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": self.user_agent}
        if data is not None:
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"error": raw.decode("utf-8", "replace")}
                retryable = exc.code in (429, 502, 503, 504)
                if not retryable or attempt >= self.retries:
                    raise MycelicError(exc.code, str(payload.get("error") or exc.reason), payload) from None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
                if attempt >= self.retries:
                    raise MycelicError(0, f"connection failed: {exc}") from exc
            attempt += 1
            time.sleep(self.backoff * (2 ** (attempt - 1)))

    # ---------------------------------------------------------------- agent surface
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", "/whoami")

    def remember(self, text: str, *, topic: str | None = None, slot: str | None = None, entity: str | None = None,
                 kind: str = "observation", confidence: float = 0.8, visibility: str = "team",
                 idempotency_key: str | None = None, observed_at: str | None = None, local_ref: str | None = None,
                 source_event_ids: list[str] | None = None, metadata: dict[str, Any] | None = None,
                 expires_at: str | None = None, supersedes: str | None = None, value: str | None = None) -> dict[str, Any]:
        """Share one memory as the calling agent.  ``value``: what the note claims for its slot and entity ("closed"),
        stored as ``metadata.value``; notes whose values differ for one slot and entity dispute each other, and their
        consolidation and every rule conclusion over that slot are flagged ``metadata.conflict`` instead of gaining
        confidence.  ``expires_at`` (ISO-8601, in the
        future, at most ten years ahead): answers stop using it then, and the service retracts it through the log shortly
        after.  ``supersedes``: the id of one of the caller's own active memories that this one corrects; this one is the
        complete note (nothing is inherited) and needs its own idempotency key.  409 (:class:`MycelicError`) while a
        retraction or another update of that memory is still on its way through the log.  507, never retried, when the
        organization is at the operator's limit of active notes (``MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG``): retract notes
        it no longer needs (a retraction counts once it has been applied); an update and a resend of a stored note are
        never refused."""
        body = {"text": text, "topic": topic, "slot": slot, "entity": entity, "kind": kind, "confidence": confidence,
                "visibility": visibility, "idempotency_key": idempotency_key, "observed_at": observed_at,
                "local_ref": local_ref, "source_event_ids": source_event_ids, "metadata": metadata,
                "expires_at": expires_at, "supersedes": supersedes, "value": value}
        return self._request("POST", "/memory", {k: v for k, v in body.items() if v is not None})

    def publish_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/events", {"events": events})

    def query(self, text: str, *, scope: str | None = None, min_layer: str = "agent", k: int = 5,
              include_lineage: bool = True, topic: str | None = None, entity: str | None = None,
              verify: bool = False) -> dict[str, Any]:
        """Search what the caller may read.  Answers and results come from active memories only, so their ``text`` is
        never withheld; the embedded lineage follows the rule of :meth:`lineage`.  The answer's ``conflict`` is true when
        the notes beneath it claim different values for one slot and entity.  With ``verify`` (and lineage:read) the
        answer also carries ``verification``: the ``verdict``, ``derived_correctly``, ``still_true`` and ``reasons`` of
        :meth:`verify`, its ``warnings`` counted per code, and ``disputed`` (a ``disputed`` warning: the verdict stays
        ``verified``, but the conclusion is contested; do not act on it before that is resolved)."""
        body = {"query": text, "scope": scope, "min_layer": min_layer, "k": k, "include_lineage": include_lineage,
                "topic": topic, "entity": entity}
        if verify:
            body["verify"] = True
        return self._request("POST", "/query", {k: v for k, v in body.items() if v is not None})

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        """One memory the caller may read.  For a superseded or retracted memory the caller did not produce (and is not
        an administrator for), ``text`` is an empty string, ``text_withheld`` is its status and ``metadata.statements``
        and ``metadata.statement_origins`` are dropped; ``text_withheld`` is present only when the text is withheld."""
        return self._request("GET", f"/memory/{urllib.parse.quote(memory_id)}")["memory"]

    def lineage(self, memory_id: str) -> dict[str, Any]:
        """The lineage graph of a memory the caller may read.  Contributions the caller may not read are redacted
        (``text`` None); a readable node that is superseded or retracted and that the caller did not produce has
        ``text`` "" and ``text_withheld`` set to its status, with every other field kept."""
        return self._request("GET", f"/lineage/{urllib.parse.quote(memory_id)}")

    def verify(self, memory_id: str, *, max_leaf_age: int | None = None) -> dict[str, Any]:
        """Downward verification of a memory the caller may read: was it derived correctly, and is it still true?

        The report's ``verdict`` is ``verified`` (both true), ``stale`` (derived correctly, but something it rests on was
        retracted, superseded or changed, or with ``max_leaf_age`` a raw note beneath it, readable or not, was ingested or
        re-attested longer ago than that many seconds), ``failed`` (a contribution is missing, tampered with or does not recompute) or
        ``unverifiable`` (something needed to check it is unavailable); ``derived_correctly`` and ``still_true`` are
        True, False or None (not known).  Reason codes are per node and for the report as a whole.  Contributions the
        caller may not read are redacted: their id, layer, unit, operator, status and ``ok``, and of their reasons only
        those lineage shows (retracted, superseded, missing parent, cycle), any other as a ``hidden_*`` code; never their
        text, agent ids or row digests.  Raises :class:`MycelicError` 404 for an unknown or invisible id, 403 without
        lineage:read and 400 for a malformed id or ``max_leaf_age``; a server older than this method answers 404 without
        a JSON error (``message`` is aiohttp's "404: Not Found")."""
        return self._request("GET", f"/verify/{urllib.parse.quote(memory_id, safe='')}",
                             params={"max_leaf_age": max_leaf_age} if max_leaf_age is not None else None)

    def retract(self, memory_id: str, reason: str = "retracted by producer") -> dict[str, Any]:
        return self._request("POST", f"/memory/{urllib.parse.quote(memory_id)}/retract", {"reason": reason})

    def attest(self, memory_id: str, *, still_true: bool = True, reason: str | None = None) -> dict[str, Any]:
        """Re-attest one of the caller's own active notes: ``still_true`` refreshes it for verification's ``max_leaf_age``
        (its ``attested_at`` once the event applies), False retracts it.  Self-attestation: it adds freshness, not
        independent assurance."""
        body: dict[str, Any] = {"still_true": still_true}
        if reason is not None:
            body["reason"] = reason
        return self._request("POST", f"/memory/{urllib.parse.quote(memory_id, safe='')}/attest", body)

    def due_attestations(self, *, older_than: int | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """The caller's own notes that an active derived memory rests on, last ingested or attested at least
        ``older_than`` seconds ago, stalest first."""
        return self._request("GET", "/attestations/due", params={"older_than": older_than, "limit": limit})["due"]

    def list_memories(self, *, scope: str | None = None, layer: str | None = None, limit: int = 50,
                      status: str | None = None) -> list[dict[str, Any]]:
        """Memories the caller may read under ``scope``, newest first; ``status`` is ``active`` (the default),
        ``superseded`` or ``retracted``.  Text is withheld as in :meth:`get_memory`: ``text`` stays a string, empty with
        ``text_withheld`` set for an inactive memory the caller did not produce."""
        return self._request("GET", "/memories", params={"scope": scope, "layer": layer, "limit": limit,
                                                         "status": status})["memories"]

    # ---------------------------------------------------------------- admin surface (admin token)
    def register_agent(self, *, agent_id: str, enterprise: str, region: str | None = None, subsidiary: str | None = None,
                       department: str | None = None, team: str | None = None, display_name: str | None = None,
                       scopes: list[str] | None = None) -> dict[str, Any]:
        body = {"agent_id": agent_id, "enterprise": enterprise, "region": region, "subsidiary": subsidiary,
                "department": department, "team": team, "display_name": display_name, "scopes": scopes}
        return self._request("POST", "/admin/agents", {k: v for k, v in body.items() if v is not None})

    def list_agents(self, org: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/agents", params={"org": org})["agents"]

    def revoke_agent(self, agent_id: str, *, retract: bool = False) -> dict[str, Any]:
        """Revoke an agent's key; with ``retract`` remove the agent: one event retracts every note it has when it applies
        (``retracted`` counts the notes active when the call was made)."""
        return self._request("DELETE", f"/admin/agents/{urllib.parse.quote(agent_id)}",
                             params={"retract": 1} if retract else None)

    def rotate_key(self, agent_id: str) -> dict[str, Any]:
        return self._request("POST", f"/admin/agents/{urllib.parse.quote(agent_id)}/rotate")

    def upsert_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/admin/rules", rule)

    def list_rules(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/rules")["rules"]

    def replay(self) -> dict[str, Any]:
        return self._request("POST", "/admin/replay", {})

    def reaggregate(self, org_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/admin/reaggregate", {"org_id": org_id} if org_id is not None else {})

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/admin/status")

    def events(self, org: str, **params: Any) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/events", params={"org": org, **params})["events"]


class LocalMemory:
    """The agent's own notes, kept on its own disk.  Only what the agent chooses to share leaves the machine.

    Every note gets a local id; sharing sends that id as the idempotency key, so a crash between the request and
    the acknowledgement cannot duplicate a memory on the server, and ``mark_shared`` records the server ids so the
    agent can later ask for the lineage of what it contributed to.

    A note leaves the machine only through :meth:`share`, which refuses a note tagged ``private``.  :meth:`share`
    records the request (visibility, expiry, supersedes) before it is sent, so a note whose share was never
    acknowledged is listed by :meth:`pending` and can be sent again exactly as it was asked for; a note that was never
    shared is never pending.  A local store written by an SDK older than this record has none: its unacknowledged
    notes stay local until they are shared again.  A local id names one note for good: :meth:`note` with the id of a
    different note raises.
    """

    def __init__(self, path: str | Path = "~/.mycelic/local_memory.db") -> None:
        self.path = ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(self.path, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                local_id   TEXT PRIMARY KEY,
                text       TEXT NOT NULL,
                topic      TEXT, slot TEXT, entity TEXT,
                kind       TEXT NOT NULL DEFAULT 'observation',
                confidence REAL NOT NULL DEFAULT 0.8,
                tags       TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                shared_memory_id TEXT, shared_event_id TEXT, shared_at TEXT,
                share_request TEXT,
                value      TEXT
            );
        """)
        columns = {r["name"] for r in self._c.execute("PRAGMA table_info(notes)").fetchall()}
        # a store from before the share request was recorded: add the column (NULL: no share pending)
        if "share_request" not in columns:
            self._c.execute("ALTER TABLE notes ADD COLUMN share_request TEXT")
        # a store from before notes had a value: add the column (NULL: the note claims no value)
        if "value" not in columns:
            self._c.execute("ALTER TABLE notes ADD COLUMN value TEXT")

    def close(self) -> None:
        self._c.close()

    def note(self, text: str, *, topic: str | None = None, slot: str | None = None, entity: str | None = None,
             kind: str = "observation", confidence: float = 0.8, tags: list[str] | None = None, local_id: str | None = None,
             value: str | None = None) -> str:
        """Keep a note locally; returns its local id.  ``value``: what the note claims for its slot and entity
        ("closed"), sent with it by :meth:`share` (see :meth:`MycelicClient.remember`: notes whose values differ dispute
        each other); give one whenever the slot is a status that can be contradicted.  Noting the same note again under
        its id (a replayed observation) returns the id and changes nothing; a different note under an existing id raises
        :class:`ValueError`, so a reused id can never make :meth:`share` send what the earlier note said."""
        local_id = local_id or f"note_{uuid.uuid4().hex[:16]}"
        values = (text, topic, slot, entity, kind, float(confidence), json.dumps(tags or []), value)
        cur = self._c.execute("INSERT OR IGNORE INTO notes(local_id, text, topic, slot, entity, kind, confidence, tags, value, "
                              "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (local_id, *values, _now()))
        if cur.rowcount == 0:
            r = self._c.execute("SELECT text, topic, slot, entity, kind, confidence, tags, value FROM notes WHERE local_id=?",
                                (local_id,)).fetchone()
            if tuple(r) != values:
                raise ValueError(f"local id {local_id!r} already holds a different note")
        return local_id

    def get(self, local_id: str) -> dict[str, Any] | None:
        r = self._c.execute("SELECT * FROM notes WHERE local_id=?", (local_id,)).fetchone()
        return self._row(r) if r else None

    def all(self, *, shared: bool | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notes"
        if shared is True:
            sql += " WHERE shared_memory_id IS NOT NULL"
        elif shared is False:
            sql += " WHERE shared_memory_id IS NULL"
        return [self._row(r) for r in self._c.execute(sql + " ORDER BY created_at, local_id").fetchall()]

    def search(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{text}%"
        rows = self._c.execute("SELECT * FROM notes WHERE text LIKE ? OR topic LIKE ? OR entity LIKE ? ORDER BY created_at DESC LIMIT ?",
                               (like, like, like, limit)).fetchall()
        return [self._row(r) for r in rows]

    def pending(self) -> list[dict[str, Any]]:
        """Notes whose share was requested (:meth:`share`) but never acknowledged, oldest first; each carries the
        request as ``share_request`` (``visibility``, ``expires_at``, ``supersedes``)."""
        rows = self._c.execute("SELECT * FROM notes WHERE share_request IS NOT NULL AND shared_memory_id IS NULL "
                               "ORDER BY created_at, local_id").fetchall()
        return [self._row(r) for r in rows]

    def counts(self) -> dict[str, int]:
        total = int(self._c.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        shared = int(self._c.execute("SELECT COUNT(*) FROM notes WHERE shared_memory_id IS NOT NULL").fetchone()[0])
        return {"local": total, "shared": shared, "local_only": total - shared}

    def mark_shared(self, local_id: str, memory_id: str, event_id: str | None) -> None:
        self._c.execute("UPDATE notes SET shared_memory_id=?, shared_event_id=?, shared_at=? WHERE local_id=?",
                        (memory_id, event_id, _now(), local_id))

    def share(self, client: MycelicClient, local_id: str, *, visibility: str = "team", expires_at: str | None = None,
              supersedes: str | None = None) -> dict[str, Any]:
        """Share a local note (its local id is the idempotency key) with the value it was noted with.  To correct a note
        already shared, write the
        correction as a new local note and share it with ``supersedes`` set to the shared note's memory id: a new local
        note gets a new idempotency key, which an update needs.

        A note tagged ``private`` is refused with :class:`ValueError` and nothing is sent.  The request is recorded
        before it is sent, so until the server acknowledges it the note is :meth:`pending`."""
        n = self.get(local_id)
        if n is None:
            raise KeyError(local_id)
        if "private" in n["tags"]:
            raise ValueError(f"local note {local_id!r} is private and is never shared")
        request = {"visibility": visibility, "expires_at": expires_at, "supersedes": supersedes}
        self._c.execute("UPDATE notes SET share_request=? WHERE local_id=?", (json.dumps(request), local_id))
        res = client.remember(n["text"], topic=n["topic"], slot=n["slot"], entity=n["entity"], kind=n["kind"],
                              confidence=n["confidence"], visibility=visibility, idempotency_key=local_id,
                              observed_at=n["created_at"], local_ref=local_id, expires_at=expires_at, supersedes=supersedes,
                              value=n["value"])
        self.mark_shared(local_id, res["memory_id"], res.get("event_id"))
        return res

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["share_request"] = json.loads(d["share_request"]) if d.get("share_request") else None
        d["shared"] = d.get("shared_memory_id") is not None
        return d
