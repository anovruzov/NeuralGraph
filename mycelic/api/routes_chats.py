"""Agent chat routes (docs/mycelic/API.md "Agent chat"): one personal agent per person, one agent per unit.

The service assembles context through the authorizer and only for the caller, so these handlers do nothing but
validate input and shape the reply.
"""
from __future__ import annotations

from typing import Any

from aiohttp import web

from .middleware import ApiError, json_response, listing, need_str, read_json, require_user


def chat_view(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in ("chat_id", "agent_type", "agent_id", "agent_name", "title", "created_at", "updated_at")}


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    async def list_chats(request: web.Request) -> web.Response:
        p = require_user(request)
        return json_response(listing([chat_view(c) for c in rt.agents.list_chats(p)]))

    async def create_chat(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        agent_type = need_str(body, "agent_type", max_len=10)
        agent_id = body.get("agent_id") or (p.id if agent_type == "user" else None)
        if agent_type not in ("user", "unit") or not isinstance(agent_id, str) or not agent_id:
            raise ApiError(400, "agent_type must be user or unit, with agent_id")
        if agent_type == "unit":
            u = rt.org.get_unit(agent_id)
            if u is None or u["tenant_id"] != p.tenant_id:
                raise KeyError(agent_id)
        chat = await rt.agents.create_chat(p, agent_type, agent_id, title=(body.get("title") or "") if isinstance(body.get("title"), str) else "")
        return json_response({"chat": chat_view(chat)}, 201)

    async def get_chat(request: web.Request) -> web.Response:
        p = require_user(request)
        state = rt.agents.get_chat(p, request.match_info["chat_id"])
        return json_response({"chat": chat_view(state["chat"]), "messages": state["messages"]})

    async def send(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        text = need_str(body, "text", max_len=8000)
        out = await rt.agents.send(p, request.match_info["chat_id"], text)
        return json_response(out)

    app.router.add_get(f"{prefix}/chats", list_chats)
    app.router.add_post(f"{prefix}/chats", create_chat)
    app.router.add_get(f"{prefix}/chats/{{chat_id}}", get_chat)
    app.router.add_post(f"{prefix}/chats/{{chat_id}}/messages", send)


__all__ = ["setup", "chat_view"]
