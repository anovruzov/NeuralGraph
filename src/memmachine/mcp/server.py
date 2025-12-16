"""MemMachine MCP Server - Main entry point.

Exposes MemMachine's Tesseract 4D Memory System as MCP tools.

Run with:
    python -m memmachine.mcp.server
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# FastMCP imports
try:
    from fastmcp import FastMCP
except ImportError:
    print("FastMCP package not installed. Run: pip install fastmcp", file=sys.stderr)
    sys.exit(1)

# MemMachine imports
from ..neural_graph.sqlite_storage import SQLiteNeuralGraphStorage
from ..neural_graph.data_types import NeuralNode, NodeLayer, NeuralEdge, EdgeType, generate_node_id, generate_edge_id
from ..neural_graph.tesseract import Tesseract
from .embeddings import get_embedding, llm_generate, check_ollama_available

logger = logging.getLogger(__name__)

# Global storage instance
_storage: SQLiteNeuralGraphStorage | None = None
_tesseract: Tesseract | None = None


def get_storage() -> SQLiteNeuralGraphStorage:
    """Get or create storage instance."""
    global _storage
    if _storage is None:
        db_path = os.environ.get("MEMMACHINE_DB", "~/.memmachine/memories.db")
        _storage = SQLiteNeuralGraphStorage(db_path)
    return _storage


def get_tesseract() -> Tesseract:
    """Get or create Tesseract instance."""
    global _tesseract
    if _tesseract is None:
        _tesseract = Tesseract(get_storage())
    return _tesseract


# Create the FastMCP server
mcp = FastMCP("memmachine")


def create_server() -> FastMCP:
    """Return the MCP server instance."""
    return mcp


# =========================================================================
# MEMORY CRUD TOOLS
# =========================================================================

@mcp.tool()
async def store_memory(
    content: str,
    session_key: str = "default",
    speaker: str | None = None,
    tags: list[str] | None = None,
    domain: str | None = None,
    category: str | None = None,
    datetime_str: str | None = None
) -> dict[str, Any]:
    """Store a new memory in the knowledge base."""
    storage = get_storage()

    embedding = await get_embedding(content)
    if embedding is None:
        return {"success": False, "error": "Failed to generate embedding. Is Ollama running?"}

    if datetime_str:
        try:
            created_at = datetime.fromisoformat(datetime_str)
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    node_id = generate_node_id()
    node = NeuralNode(
        node_id=node_id,
        session_key=session_key,
        layer=NodeLayer.MESSAGE,
        content=content,
        embedding=embedding,
        created_at=created_at,
        metadata={"speaker": speaker, "tags": tags or [], "domain": domain, "category": category}
    )

    await storage.save_node(node)
    return {"success": True, "memory_id": node_id, "session_key": session_key}


@mcp.tool()
async def get_memory_by_id(memory_id: str) -> dict[str, Any]:
    """Retrieve a specific memory by its ID."""
    storage = get_storage()
    node = await storage.get_node(memory_id)

    if node is None:
        return {"success": False, "error": f"Memory not found: {memory_id}"}

    return {
        "success": True,
        "memory": {
            "id": node.node_id,
            "content": node.content,
            "session_key": node.session_key,
            "speaker": node.metadata.get("speaker"),
            "tags": node.metadata.get("tags", []),
            "created_at": node.created_at.isoformat() if node.created_at else None,
        }
    }


@mcp.tool()
async def update_memory(
    memory_id: str,
    content: str | None = None,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """Update an existing memory."""
    storage = get_storage()
    node = await storage.get_node(memory_id)

    if node is None:
        return {"success": False, "error": f"Memory not found: {memory_id}"}

    if content is not None:
        embedding = await get_embedding(content)
        if embedding is None:
            return {"success": False, "error": "Failed to generate embedding"}
        node.content = content
        node.embedding = embedding

    if tags is not None:
        node.metadata["tags"] = tags

    await storage.update_node(node)
    return {"success": True, "memory_id": memory_id}


@mcp.tool()
async def delete_memory(memory_id: str) -> dict[str, Any]:
    """Delete a memory from the knowledge base."""
    storage = get_storage()
    deleted = await storage.delete_node(memory_id)
    return {"success": deleted, "memory_id": memory_id, "deleted": deleted}


# =========================================================================
# SEARCH TOOLS
# =========================================================================

@mcp.tool()
async def search_semantic(query: str, session_key: str = "default", limit: int = 10) -> dict[str, Any]:
    """Search memories using 4D Tesseract brain-inspired retrieval.

    Uses specialized stores for different query types:
    - Temporal: "When" questions with time-aware scoring
    - Entity: "Who/What" questions with entity binding
    - Multi-hop: Questions requiring multiple pieces of info
    - Adversarial: Negation and exception handling
    """
    tesseract = get_tesseract()

    query_embedding = await get_embedding(query)
    if query_embedding is None:
        return {"success": False, "error": "Failed to generate query embedding"}

    # Use Tesseract's 4D retrieval instead of simple vector search
    results = await tesseract.retrieve(
        query_text=query,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=limit
    )

    return {
        "success": True,
        "query": query,
        "count": len(results),
        "results": [
            {"id": node.node_id, "content": node.content, "similarity": round(score, 4)}
            for node, score in results
        ]
    }


@mcp.tool()
async def search_tags(tags: list[str], session_key: str = "default", limit: int = 50) -> dict[str, Any]:
    """Search memories by tags."""
    storage = get_storage()
    nodes = await storage.get_nodes_by_session(session_key)

    results = []
    for node in nodes:
        node_tags = set(node.metadata.get("tags", []))
        if set(tags) & node_tags:
            results.append(node)
        if len(results) >= limit:
            break

    return {
        "success": True,
        "tags": tags,
        "count": len(results),
        "results": [{"id": n.node_id, "content": n.content, "tags": n.metadata.get("tags", [])} for n in results]
    }


@mcp.tool()
async def search_hybrid(
    query: str,
    session_key: str = "default",
    tags: list[str] | None = None,
    speaker: str | None = None,
    limit: int = 10
) -> dict[str, Any]:
    """Hybrid search using Tesseract 4D retrieval with filters."""
    tesseract = get_tesseract()

    query_embedding = await get_embedding(query)
    if query_embedding is None:
        return {"success": False, "error": "Failed to generate query embedding"}

    # Use Tesseract for brain-inspired retrieval, then filter
    results = await tesseract.retrieve(
        query_text=query,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=limit * 3  # Get more for filtering
    )

    filtered = []
    for node, score in results:
        if tags and not (set(tags) & set(node.metadata.get("tags", []))):
            continue
        if speaker and node.metadata.get("speaker") != speaker:
            continue
        filtered.append((node, score))
        if len(filtered) >= limit:
            break

    return {
        "success": True,
        "query": query,
        "count": len(filtered),
        "results": [{"id": n.node_id, "content": n.content, "similarity": round(s, 4)} for n, s in filtered]
    }


# =========================================================================
# ANALYSIS TOOLS
# =========================================================================

@mcp.tool()
async def analyze_question(question: str, session_key: str = "default", mode: str = "STRICT") -> dict[str, Any]:
    """Answer a question using Tesseract 4D memory retrieval.

    The Tesseract automatically detects question type and uses specialized stores:
    - Temporal questions use time-aware retrieval
    - Entity questions use entity binding
    - Multi-hop questions get broader context
    - Adversarial questions handle negation
    """
    tesseract = get_tesseract()

    query_embedding = await get_embedding(question)
    if query_embedding is None:
        return {"success": False, "error": "Failed to generate query embedding"}

    # Use Tesseract for brain-inspired retrieval
    results = await tesseract.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=15
    )

    if not results:
        return {"success": True, "question": question, "answer": "No relevant memories found.", "memories_used": 0}

    context = "\n".join([f"[{n.metadata.get('speaker', 'Unknown')}] {n.content}" for n, _ in results])

    prompt = f"""Answer using ONLY the memories below.

MEMORIES:
{context}

QUESTION: {question}

Answer:"""

    answer = await llm_generate(prompt)
    if answer is None:
        return {"success": False, "error": "Failed to generate answer. Is Ollama running?"}

    return {"success": True, "question": question, "answer": answer, "memories_used": len(results)}


@mcp.tool()
async def analyze_summarize(session_key: str = "default", topic: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Summarize memories in a session."""
    storage = get_storage()

    if topic:
        query_embedding = await get_embedding(topic)
        if query_embedding is None:
            return {"success": False, "error": "Failed to generate topic embedding"}
        results = await storage.vector_search(query_embedding, session_key, limit)
        nodes = [node for node, _ in results]
    else:
        nodes = await storage.get_nodes_by_session(session_key)
        nodes = sorted(nodes, key=lambda n: n.created_at or datetime.min, reverse=True)[:limit]

    if not nodes:
        return {"success": True, "summary": "No memories found."}

    context = "\n".join([f"- {n.content[:300]}" for n in nodes])
    prompt = f"Summarize these memories:\n{context}\n\nSummary:"

    summary = await llm_generate(prompt, max_tokens=300)
    return {"success": True, "summary": summary or "Failed to generate summary", "memories_analyzed": len(nodes)}


# =========================================================================
# RELATIONSHIP TOOLS
# =========================================================================

@mcp.tool()
async def find_related(memory_id: str, limit: int = 10) -> dict[str, Any]:
    """Find memories related to a specific memory."""
    storage = get_storage()

    node = await storage.get_node(memory_id)
    if node is None:
        return {"success": False, "error": f"Memory not found: {memory_id}"}

    if node.embedding is None:
        return {"success": False, "error": "Memory has no embedding"}

    results = await storage.vector_search(node.embedding, node.session_key, limit + 1)
    related = [(n, s) for n, s in results if n.node_id != memory_id][:limit]

    return {
        "success": True,
        "source_id": memory_id,
        "count": len(related),
        "related": [{"id": n.node_id, "content": n.content[:200], "similarity": round(s, 4)} for n, s in related]
    }


@mcp.tool()
async def create_relationship(source_id: str, target_id: str, relation_type: str = "SEMANTIC", weight: float = 1.0) -> dict[str, Any]:
    """Create a relationship between two memories."""
    storage = get_storage()

    source = await storage.get_node(source_id)
    target = await storage.get_node(target_id)

    if source is None:
        return {"success": False, "error": f"Source not found: {source_id}"}
    if target is None:
        return {"success": False, "error": f"Target not found: {target_id}"}

    try:
        edge_type = EdgeType(relation_type.lower())
    except ValueError:
        edge_type = EdgeType.SEMANTIC

    edge_id = generate_edge_id()
    edge = NeuralEdge(edge_id=edge_id, source_id=source_id, target_id=target_id, edge_type=edge_type, base_weight=weight)
    await storage.save_edge(edge)

    return {"success": True, "edge_id": edge_id, "relation_type": edge_type.value}


# =========================================================================
# STATS & MANAGEMENT TOOLS
# =========================================================================

@mcp.tool()
async def stats_sessions(session_key: str | None = None) -> dict[str, Any]:
    """Get statistics for sessions."""
    storage = get_storage()

    if session_key:
        stats = await storage.get_session_statistics(session_key)
        return {"success": True, "session_key": session_key, "stats": stats}
    else:
        sessions = await storage.list_sessions()
        return {"success": True, "sessions": sessions}


@mcp.tool()
async def categories_list(domain: str | None = None) -> dict[str, Any]:
    """List all categories."""
    storage = get_storage()
    categories = await storage.list_categories(domain)
    return {"success": True, "categories": categories}


@mcp.tool()
async def categories_create(name: str, description: str | None = None, domain: str | None = None) -> dict[str, Any]:
    """Create a new category."""
    storage = get_storage()
    try:
        cat_id = await storage.create_category(name, description, domain)
        return {"success": True, "id": cat_id, "name": name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def domains_list() -> dict[str, Any]:
    """List all domains."""
    storage = get_storage()
    domains = await storage.list_domains()
    return {"success": True, "domains": domains}


@mcp.tool()
async def domains_create(name: str, description: str | None = None) -> dict[str, Any]:
    """Create a new domain."""
    storage = get_storage()
    try:
        domain_id = await storage.create_domain(name, description)
        return {"success": True, "id": domain_id, "name": name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def sessions_list(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List all sessions."""
    storage = get_storage()
    sessions = await storage.list_sessions(limit, offset)
    return {"success": True, "sessions": sessions}


@mcp.tool()
async def check_status() -> dict[str, Any]:
    """Check MemMachine MCP server status."""
    ollama_status = await check_ollama_available()
    storage = get_storage()
    return {
        "success": True,
        "server": "memmachine",
        "version": "1.0.0",
        "ollama": ollama_status,
        "storage": {"type": "sqlite", "path": str(storage._db_path)}
    }


# =========================================================================
# MAIN ENTRY POINT
# =========================================================================

async def run_server() -> None:
    """Run the MCP server using stdio transport."""
    await mcp.run_stdio_async()


def main() -> None:
    """Main entry point."""
    logging.basicConfig(level=logging.INFO)
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
