"""Mycelic SDK: a dependency-free client for the HTTP API plus an agent-local memory store.

    from mycelic.sdk import MycelicClient, LocalMemory

    local = LocalMemory("~/.mycelic/agent-7.db")                 # what this agent knows, privately
    client = MycelicClient("https://mycelic.example.com", api_key="mk_agent-7....", ca_file="corp-ca.pem")

    note_id = local.note("Port of Rotterdam terminal 3 strike announced for weeks 41-43",
                         topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.9)
    local.share(client, note_id)                                  # publish it; idempotent on retry
    answer = client.query("delivery risk for RX-4 in Q4", scope="northwind")
    lineage = client.lineage(answer["answer"]["memory_id"])

The client is synchronous and uses only the standard library, so it drops into any agent runtime.  Retries cover
connection errors and 5xx/429 with exponential backoff; 4xx errors raise :class:`MycelicError` immediately.
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
                 source_event_ids: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"text": text, "topic": topic, "slot": slot, "entity": entity, "kind": kind, "confidence": confidence,
                "visibility": visibility, "idempotency_key": idempotency_key, "observed_at": observed_at,
                "local_ref": local_ref, "source_event_ids": source_event_ids, "metadata": metadata}
        return self._request("POST", "/memory", {k: v for k, v in body.items() if v is not None})

    def publish_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/events", {"events": events})

    def query(self, text: str, *, scope: str | None = None, min_layer: str = "agent", k: int = 5,
              include_lineage: bool = True, topic: str | None = None, entity: str | None = None) -> dict[str, Any]:
        body = {"query": text, "scope": scope, "min_layer": min_layer, "k": k, "include_lineage": include_lineage,
                "topic": topic, "entity": entity}
        return self._request("POST", "/query", {k: v for k, v in body.items() if v is not None})

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        return self._request("GET", f"/memory/{urllib.parse.quote(memory_id)}")["memory"]

    def lineage(self, memory_id: str) -> dict[str, Any]:
        return self._request("GET", f"/lineage/{urllib.parse.quote(memory_id)}")

    def retract(self, memory_id: str, reason: str = "retracted by producer") -> dict[str, Any]:
        return self._request("POST", f"/memory/{urllib.parse.quote(memory_id)}/retract", {"reason": reason})

    def list_memories(self, *, scope: str | None = None, layer: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self._request("GET", "/memories", params={"scope": scope, "layer": layer, "limit": limit})["memories"]

    # ---------------------------------------------------------------- admin surface (admin token)
    def register_agent(self, *, agent_id: str, enterprise: str, region: str | None = None, subsidiary: str | None = None,
                       department: str | None = None, team: str | None = None, display_name: str | None = None,
                       scopes: list[str] | None = None) -> dict[str, Any]:
        body = {"agent_id": agent_id, "enterprise": enterprise, "region": region, "subsidiary": subsidiary,
                "department": department, "team": team, "display_name": display_name, "scopes": scopes}
        return self._request("POST", "/admin/agents", {k: v for k, v in body.items() if v is not None})

    def list_agents(self, org: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/agents", params={"org": org})["agents"]

    def revoke_agent(self, agent_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/admin/agents/{urllib.parse.quote(agent_id)}")

    def rotate_key(self, agent_id: str) -> dict[str, Any]:
        return self._request("POST", f"/admin/agents/{urllib.parse.quote(agent_id)}/rotate")

    def upsert_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/admin/rules", rule)

    def list_rules(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/rules")["rules"]

    def replay(self) -> dict[str, Any]:
        return self._request("POST", "/admin/replay", {})

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/admin/status")

    def events(self, org: str, **params: Any) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/events", params={"org": org, **params})["events"]


class LocalMemory:
    """The agent's own notes, kept on its own disk.  Only what the agent chooses to share leaves the machine.

    Every note gets a local id; sharing sends that id as the idempotency key, so a crash between the request and
    the acknowledgement cannot duplicate a memory on the server, and ``mark_shared`` records the server ids so the
    agent can later ask for the lineage of what it contributed to.
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
                shared_memory_id TEXT, shared_event_id TEXT, shared_at TEXT
            );
        """)

    def close(self) -> None:
        self._c.close()

    def note(self, text: str, *, topic: str | None = None, slot: str | None = None, entity: str | None = None,
             kind: str = "observation", confidence: float = 0.8, tags: list[str] | None = None, local_id: str | None = None) -> str:
        local_id = local_id or f"note_{uuid.uuid4().hex[:16]}"
        self._c.execute("INSERT OR IGNORE INTO notes(local_id, text, topic, slot, entity, kind, confidence, tags, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (local_id, text, topic, slot, entity, kind, float(confidence), json.dumps(tags or []), _now()))
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

    def counts(self) -> dict[str, int]:
        total = int(self._c.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        shared = int(self._c.execute("SELECT COUNT(*) FROM notes WHERE shared_memory_id IS NOT NULL").fetchone()[0])
        return {"local": total, "shared": shared, "local_only": total - shared}

    def mark_shared(self, local_id: str, memory_id: str, event_id: str | None) -> None:
        self._c.execute("UPDATE notes SET shared_memory_id=?, shared_event_id=?, shared_at=? WHERE local_id=?",
                        (memory_id, event_id, _now(), local_id))

    def share(self, client: MycelicClient, local_id: str, *, visibility: str = "team") -> dict[str, Any]:
        n = self.get(local_id)
        if n is None:
            raise KeyError(local_id)
        res = client.remember(n["text"], topic=n["topic"], slot=n["slot"], entity=n["entity"], kind=n["kind"],
                              confidence=n["confidence"], visibility=visibility, idempotency_key=local_id,
                              observed_at=n["created_at"], local_ref=local_id)
        self.mark_shared(local_id, res["memory_id"], res.get("event_id"))
        return res

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["shared"] = d.get("shared_memory_id") is not None
        return d
