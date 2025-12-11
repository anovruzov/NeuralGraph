"""Tesseract Memory Chat Interface.

A Streamlit-based chat interface that uses the 4D Tesseract memory
architecture for context-aware conversation with memory retrieval.

Features:
- Real-time conversation ingestion into memory
- Brain-inspired 4D memory retrieval (temporal, entity, reasoning, adversarial)
- Memory visualization showing what was retrieved
- Support for multiple users/sessions
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

import aiohttp
import streamlit as st

# Add source to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type, QueryType

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"
LLM_MODEL = "qwen2.5:7b-instruct"

# =============================================================================
# ASYNC HELPERS
# =============================================================================

def run_async(coro):
    """Run async function in sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


async def get_embedding(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding from Ollama."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception as e:
        st.error(f"Embedding error: {e}")
        return []


async def generate_response(
    session: aiohttp.ClientSession,
    prompt: str,
    context: str,
    chat_history: list[dict],
) -> str:
    """Generate LLM response with context."""
    # Build messages for chat
    messages = []

    # System message with memory context
    system_prompt = f"""You are a helpful assistant with access to conversation memory.

MEMORY CONTEXT (retrieved from past conversations):
{context if context else "No relevant memories found."}

Use this memory context to provide informed, personalized responses.
If the memory contains relevant information, reference it naturally.
If unsure, acknowledge what you remember vs what you don't know."""

    messages.append({"role": "system", "content": system_prompt})

    # Add chat history (last 10 messages)
    for msg in chat_history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
            },
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            result = await response.json()
            return result.get("message", {}).get("content", "I couldn't generate a response.")
    except Exception as e:
        return f"Error generating response: {e}"


# =============================================================================
# MEMORY MANAGER
# =============================================================================

class TesseractMemoryManager:
    """Manages memory storage and retrieval using Tesseract."""

    def __init__(self):
        self.storage = InMemoryNeuralGraphStorage()
        self.tesseract = Tesseract(self.storage)
        self.message_count = 0

    async def ingest_message(
        self,
        session: aiohttp.ClientSession,
        content: str,
        speaker: str,
        session_key: str,
    ) -> NeuralNode:
        """Ingest a message into memory."""
        embedding = await get_embedding(session, content)

        node = NeuralNode(
            node_id=f"{session_key}_msg_{self.message_count}",
            session_key=session_key,
            content=content,
            layer=NodeLayer.MESSAGE,
            embedding=embedding,
            created_at=datetime.now(),
            metadata={"speaker": speaker, "timestamp": datetime.now().isoformat()},
        )

        await self.storage.save_node(node)
        self.message_count += 1

        return node

    async def retrieve_context(
        self,
        session: aiohttp.ClientSession,
        query: str,
        session_key: str,
        limit: int = 10,
    ) -> tuple[list[tuple[NeuralNode, float]], dict[str, float]]:
        """Retrieve relevant context using Tesseract."""
        query_embedding = await get_embedding(session, query)

        if not query_embedding:
            return [], {}

        # Get query type detection
        query_types = detect_query_type(query)

        # Retrieve using tesseract
        results = await self.tesseract.retrieve(
            query_text=query,
            query_embedding=query_embedding,
            session_key=session_key,
            reference_time=datetime.now(),
            limit=limit,
        )

        return results, query_types

    def format_context(self, results: list[tuple[NeuralNode, float]]) -> str:
        """Format retrieved nodes as context string."""
        if not results:
            return ""

        context_parts = []
        for i, (node, charge) in enumerate(results[:8], 1):
            speaker = node.metadata.get("speaker", "Unknown")
            timestamp = node.metadata.get("timestamp", "")
            content = node.content[:500]  # Truncate long messages
            context_parts.append(f"[Memory {i}] {speaker}: {content}")

        return "\n\n".join(context_parts)

    async def get_memory_stats(self, session_key: str) -> dict:
        """Get statistics about stored memories."""
        nodes = await self.storage.get_nodes_by_session(session_key)
        return {
            "total_memories": len(nodes),
            "speakers": list(set(n.metadata.get("speaker", "Unknown") for n in nodes)),
        }


# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.set_page_config(
        page_title="Tesseract Memory Chat",
        page_icon="🧠",
        layout="wide"
    )

    # Custom CSS
    st.markdown("""
    <style>
    .memory-box {
        background-color: #1e1e1e;
        border-radius: 10px;
        padding: 15px;
        margin: 10px 0;
        border-left: 4px solid #4CAF50;
    }
    .query-type {
        display: inline-block;
        padding: 3px 8px;
        border-radius: 5px;
        font-size: 12px;
        margin-right: 5px;
    }
    .temporal { background-color: #2196F3; color: white; }
    .entity { background-color: #4CAF50; color: white; }
    .multi_hop { background-color: #FF9800; color: white; }
    .adversarial { background-color: #f44336; color: white; }
    .open { background-color: #9E9E9E; color: white; }
    </style>
    """, unsafe_allow_html=True)

    # Initialize session state
    if "memory_manager" not in st.session_state:
        st.session_state.memory_manager = TesseractMemoryManager()
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = cast("list[dict]", [])
    if "last_retrieval" not in st.session_state:
        st.session_state.last_retrieval = None
    if "last_query_types" not in st.session_state:
        st.session_state.last_query_types = {}

    # Sidebar
    with st.sidebar:
        st.title("🧠 Tesseract Memory")
        st.markdown("---")

        # Session settings
        st.subheader("Session Settings")
        user_name = st.text_input("Your Name", value="User")
        session_key = st.text_input("Session ID", value="default_session")

        st.markdown("---")

        # Memory stats
        st.subheader("Memory Statistics")
        if st.button("Refresh Stats"):
            async def get_stats():
                return await st.session_state.memory_manager.get_memory_stats(session_key)
            stats = run_async(get_stats())
            st.write(f"**Total Memories:** {stats['total_memories']}")
            st.write(f"**Speakers:** {', '.join(stats['speakers']) if stats['speakers'] else 'None'}")

        st.markdown("---")

        # Clear options
        if st.button("Clear Chat History"):
            st.session_state.chat_history = []
            st.session_state.last_retrieval = None
            st.session_state.last_query_types = {}
            st.rerun()

        if st.button("Clear All Memory"):
            st.session_state.memory_manager = TesseractMemoryManager()
            st.session_state.chat_history = []
            st.session_state.last_retrieval = None
            st.session_state.last_query_types = {}
            st.success("Memory cleared!")
            st.rerun()

        st.markdown("---")
        st.caption("4D Brain-Inspired Memory")
        st.caption("• Temporal (Hippocampus)")
        st.caption("• Entity (Neocortex)")
        st.caption("• Reasoning (Prefrontal)")
        st.caption("• Adversarial (Orbitofrontal)")

    # Main chat area
    col1, col2 = st.columns([2, 1])

    with col1:
        st.title("Tesseract Memory Chat")
        st.caption("Chat with AI that remembers your conversations")

        # Display chat history
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        # Chat input
        if prompt := st.chat_input("Type your message..."):
            # Add user message to history
            st.session_state.chat_history.append({
                "role": "user",
                "content": prompt,
            })

            with st.chat_message("user"):
                st.write(prompt)

            # Process with memory
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    async def process_message():
                        async with aiohttp.ClientSession() as http_session:
                            # 1. Ingest user message into memory
                            await st.session_state.memory_manager.ingest_message(
                                http_session, prompt, user_name, session_key
                            )

                            # 2. Retrieve relevant context
                            results, query_types = await st.session_state.memory_manager.retrieve_context(
                                http_session, prompt, session_key
                            )

                            # Store for display
                            st.session_state.last_retrieval = results
                            st.session_state.last_query_types = query_types

                            # 3. Format context
                            context = st.session_state.memory_manager.format_context(results)

                            # 4. Generate response
                            response = await generate_response(
                                http_session, prompt, context, st.session_state.chat_history
                            )

                            # 5. Ingest assistant response into memory
                            await st.session_state.memory_manager.ingest_message(
                                http_session, response, "Assistant", session_key
                            )

                            return response

                    response = run_async(process_message())
                    st.write(response)

            # Add assistant message to history
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": response,
            })

            st.rerun()

    with col2:
        st.subheader("🔍 Memory Retrieval")

        # Show query type detection
        if st.session_state.last_query_types:
            st.markdown("**Query Type Detection:**")
            for qtype, score in sorted(
                st.session_state.last_query_types.items(),
                key=lambda x: x[1],
                reverse=True
            ):
                if score > 0.1:
                    color = {
                        QueryType.TEMPORAL: "#2196F3",
                        QueryType.ENTITY: "#4CAF50",
                        QueryType.MULTI_HOP: "#FF9800",
                        QueryType.ADVERSARIAL: "#f44336",
                        QueryType.OPEN: "#9E9E9E",
                    }.get(qtype, "#9E9E9E")
                    st.markdown(
                        f'<span style="background-color:{color};color:white;'
                        f'padding:2px 8px;border-radius:5px;font-size:12px;">'
                        f'{qtype}: {score:.2f}</span>',
                        unsafe_allow_html=True
                    )
            st.markdown("---")

        # Show retrieved memories
        if st.session_state.last_retrieval:
            st.markdown("**Retrieved Memories:**")
            for i, (node, charge) in enumerate(st.session_state.last_retrieval[:5], 1):
                speaker = node.metadata.get("speaker", "Unknown")
                content = node.content[:150] + "..." if len(node.content) > 150 else node.content
                with st.expander(f"Memory {i} (charge: {charge:.3f})"):
                    st.markdown(f"**Speaker:** {speaker}")
                    st.markdown(f"**Content:** {content}")
        else:
            st.info("Send a message to see memory retrieval in action!")


if __name__ == "__main__":
    main()
