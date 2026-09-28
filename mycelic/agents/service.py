"""Scoped agents: one per employee (their private memory + what they may see) and one per organizational unit.

An agent never sees more than its principal: context is assembled through the authorizer (claims, discoveries,
conflicts) plus the caller's own holders (private memory) and any holder they were granted. Retrieved text is
passed to the model inside a data block and cited back as typed references, never executed as instructions.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping

from ..authz import Authorizer, Forbidden, Principal
from ..db.coord import CoordDB, row_to_dict, rows_to_dicts
from ..knowledge import KnowledgeService
from ..models.base import ModelError, ModelRouter
from ..org import OrgService
from ..util import j, new_id, now_iso

logger = logging.getLogger(__name__)

# async (principal, holder_id, query, k) -> list[{memory_id, text, doc_id, title, observed_at, score}]
MemorySearch = Callable[[Principal, str, str, int], Awaitable[list[dict[str, Any]]]]


class AgentService:
    def __init__(self, db: CoordDB, org: OrgService, authz: Authorizer, knowledge: KnowledgeService, router: ModelRouter | None,
                 memory_search: MemorySearch | None = None) -> None:
        self.db, self.org, self.authz, self.knowledge, self.router = db, org, authz, knowledge, router
        self.memory_search = memory_search

    # ------------------------------------------------------------------ chats
    def list_chats(self, principal: Principal) -> list[dict[str, Any]]:
        rows = rows_to_dicts(self.db.all("SELECT * FROM agent_chats WHERE tenant_id=? AND user_id=? ORDER BY updated_at DESC LIMIT 100", (principal.tenant_id, principal.id)))
        for r in rows:
            r["agent_name"] = self._agent_name(r["agent_type"], r["agent_id"])
        return rows

    def _agent_name(self, agent_type: str, agent_id: str) -> str:
        if agent_type == "user":
            u = self.org.get_user(agent_id)
            return f"{u['name']}'s agent" if u else "personal agent"
        un = self.org.get_unit(agent_id)
        return f"{un['name']} agent" if un else "unit agent"

    def _check_agent(self, principal: Principal, agent_type: str, agent_id: str) -> None:
        if agent_type == "user":
            self.authz.require(agent_id == principal.id, "agent.open", agent_id, "only your own personal agent")
        elif agent_type == "unit":
            allowed = agent_id in (self.authz.visible_unit_ids(principal) | self.authz.led_unit_ids(principal))
            self.authz.require(allowed, "agent.open", agent_id, "not a member or lead of that unit")
        else:
            raise ValueError("agent_type must be user or unit")

    async def create_chat(self, principal: Principal, agent_type: str, agent_id: str, *, title: str = "") -> dict[str, Any]:
        self._check_agent(principal, agent_type, agent_id)
        cid = new_id("chat")
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO agent_chats(chat_id, tenant_id, user_id, agent_type, agent_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (cid, principal.tenant_id, principal.id, agent_type, agent_id, title or self._agent_name(agent_type, agent_id), now, now))
        return self.get_chat(principal, cid)["chat"]

    def get_chat(self, principal: Principal, chat_id: str) -> dict[str, Any]:
        chat = row_to_dict(self.db.one("SELECT * FROM agent_chats WHERE chat_id=?", (chat_id,)))
        if chat is None:
            raise KeyError(chat_id)
        self.authz.require(chat["tenant_id"] == principal.tenant_id and chat["user_id"] == principal.id, "chat.view", chat_id)
        chat["agent_name"] = self._agent_name(chat["agent_type"], chat["agent_id"])
        msgs = rows_to_dicts(self.db.all("SELECT * FROM agent_messages WHERE chat_id=? ORDER BY id", (chat_id,)), json_fields=("citations", "usage"))
        return {"chat": chat, "messages": msgs}

    # ------------------------------------------------------------------ context assembly (authorized only)
    async def assemble_context(self, principal: Principal, agent_type: str, agent_id: str, query: str, *, k: int = 8) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        scope = agent_id if agent_type == "unit" else None
        for cl in self.knowledge.list_claims(principal, q=None, scope_unit_id=scope, limit=200):
            items.append({"type": "claim", "id": cl["claim_id"], "text": cl["text"], "status": cl["status"], "label": cl["text"][:80]})
        for d in self.knowledge.list_discoveries(principal, scope_unit_id=scope, limit=100):
            items.append({"type": "discovery", "id": d["discovery_id"], "text": f"{d['title']}. {d['summary']}", "status": d["status"], "label": d["title"][:80]})
        for k_ in self.knowledge.list_conflicts(principal, scope_unit_id=scope, limit=50):
            items.append({"type": "conflict", "id": k_["conflict_id"], "text": f"Disagreement: {k_['summary']}", "status": k_["status"], "label": k_["summary"][:80]})
        memories: list[dict[str, Any]] = []
        if self.memory_search is not None and agent_type == "user":
            holders = [h for h in self.org.holders_for_user(principal.id)] + [self.org.get_holder(g["resource_id"]) for g in principal.grants if g["resource_type"] == "holder"]
            for h in holders:
                if not h:
                    continue
                try:
                    hits = await self.memory_search(principal, h["holder_id"], query, k)
                except Exception as exc:
                    logger.info("memory search failed for %s: %s", h["holder_id"], exc)
                    continue
                for m in hits:
                    memories.append({"type": "memory", "id": m.get("memory_id"), "holder_id": h["holder_id"], "text": m.get("text", ""), "label": (m.get("title") or m.get("text", ""))[:80],
                                     "observed_at": m.get("observed_at")})
        return {"items": items, "memories": memories}

    @staticmethod
    def _rank(query: str, items: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
        from ..discovery.engine import _tokens
        qt = _tokens(query)
        scored = []
        for it in items:
            overlap = len(qt & _tokens(it.get("text", "")))
            if overlap:
                scored.append((overlap, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:k]]

    # ------------------------------------------------------------------ chat
    async def send(self, principal: Principal, chat_id: str, text: str) -> dict[str, Any]:
        state = self.get_chat(principal, chat_id)
        chat = state["chat"]
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("empty message")
        ctx = await self.assemble_context(principal, chat["agent_type"], chat["agent_id"], text)
        knowledge_items = self._rank(text, ctx["items"], 12)
        memory_items = self._rank(text, ctx["memories"], 8)
        history = [{"role": m["role"], "content": m["content"][:600]} for m in state["messages"][-6:]]
        context = [{"type": it["type"], "id": it["id"], "text": it["text"][:800], "status": it.get("status")} for it in knowledge_items + memory_items]
        usage: dict[str, Any] = {}
        if self.router is None:
            answer, citations = "No model provider is configured; here is what I could find.", [{"type": it["type"], "id": it["id"]} for it in context[:5]]
        else:
            try:
                out = await self.router.run_task("chat_answer", {"question": text, "context": context, "history": history}, tenant_id=principal.tenant_id, question_id=None,
                                                 policy_tiers=self.org.policy(principal.tenant_id, "model_tiers", {}) or {})
                answer = str(out.get("answer") or "")
                allowed_ids = {(it["type"], it["id"]) for it in context}
                citations = [c for c in (out.get("citations") or []) if isinstance(c, dict) and (c.get("type"), c.get("id")) in allowed_ids]
            except ModelError as exc:
                answer, citations = f"The model call failed: {exc}", []
        labels = {(it["type"], it["id"]): it.get("label", "") for it in knowledge_items + memory_items}
        cites = [{**c, "label": labels.get((c["type"], c["id"]), "")} for c in citations]
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO agent_messages(chat_id, role, content, citations, usage, at) VALUES (?, 'user', ?, '[]', '{}', ?)", (chat_id, text, now))
            c.execute("INSERT INTO agent_messages(chat_id, role, content, citations, usage, at) VALUES (?, 'assistant', ?, ?, ?, ?)", (chat_id, answer, j(cites), j(usage), now))
            c.execute("UPDATE agent_chats SET updated_at=?, title=CASE WHEN title='' THEN ? ELSE title END WHERE chat_id=?", (now, text[:60], chat_id))
            self.db.audit_sync(c, principal.tenant_id, "user", principal.id, "agent.chat", resource_type="chat", resource_id=chat_id,
                               detail={"context": {"claims": sum(1 for i in knowledge_items if i["type"] == "claim"), "memories": len(memory_items)}})
        msg = rows_to_dicts([self.db.one("SELECT * FROM agent_messages WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,))], json_fields=("citations", "usage"))[0]
        return {"message": msg, "citations": cites, "context_summary": {"claims": sum(1 for i in knowledge_items if i["type"] == "claim"),
                                                                        "discoveries": sum(1 for i in knowledge_items if i["type"] == "discovery"), "memories": len(memory_items)}}
