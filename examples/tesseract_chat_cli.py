"""Tesseract Memory Chat - Command Line Interface.

A simple CLI chat interface that demonstrates the 4D Tesseract memory system.
Run with: python tesseract_chat_cli.py

Features:
- Real-time conversation memory
- Brain-inspired 4D retrieval
- Memory visualization
- Commands: /memory, /clear, /stats, /quit
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

# Add source to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type, QueryType

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"
LLM_MODEL = "qwen2.5:7b-instruct"

# ANSI colors
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header():
    """Print welcome header."""
    print(f"""
{Colors.CYAN}{'='*60}
{Colors.BOLD}  TESSERACT MEMORY CHAT{Colors.END}
{Colors.CYAN}  4D Brain-Inspired Memory Architecture
{'='*60}{Colors.END}

{Colors.GREEN}Commands:{Colors.END}
  /memory  - Show last retrieved memories
  /stats   - Show memory statistics
  /clear   - Clear conversation history
  /quit    - Exit the chat

{Colors.YELLOW}The system uses 4 specialized memory stores:{Colors.END}
  - Temporal (Hippocampus): When questions
  - Entity (Neocortex): What/Who questions
  - Reasoning (Prefrontal): Multi-hop inference
  - Adversarial (Orbitofrontal): Verification

""")


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
        print(f"{Colors.RED}Embedding error: {e}{Colors.END}")
        return []


async def generate_response(
    session: aiohttp.ClientSession,
    prompt: str,
    context: str,
    chat_history: list[dict],
) -> str:
    """Generate LLM response with context."""
    messages = []

    system_prompt = f"""You are a helpful assistant with access to conversation memory.

MEMORY CONTEXT (retrieved from past conversations):
{context if context else "No relevant memories found."}

Use this memory context to provide informed, personalized responses.
If the memory contains relevant information, reference it naturally.
Be concise but helpful."""

    messages.append({"role": "system", "content": system_prompt})

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


class TesseractChat:
    """CLI Chat with Tesseract Memory."""

    def __init__(self):
        self.storage = InMemoryNeuralGraphStorage()
        self.tesseract = Tesseract(self.storage)
        self.message_count = 0
        self.chat_history: list[dict] = []
        self.last_retrieval: list[tuple[NeuralNode, float]] = []
        self.last_query_types: dict[str, float] = {}
        self.session_key = "cli_session"
        self.user_name = "User"

    async def ingest_message(
        self,
        session: aiohttp.ClientSession,
        content: str,
        speaker: str,
    ) -> None:
        """Ingest a message into memory."""
        embedding = await get_embedding(session, content)

        node = NeuralNode(
            node_id=f"{self.session_key}_msg_{self.message_count}",
            session_key=self.session_key,
            content=content,
            layer=NodeLayer.MESSAGE,
            embedding=embedding,
            created_at=datetime.now(),
            metadata={"speaker": speaker, "timestamp": datetime.now().isoformat()},
        )

        await self.storage.save_node(node)
        self.message_count += 1

    async def retrieve_context(
        self,
        session: aiohttp.ClientSession,
        query: str,
    ) -> str:
        """Retrieve and format relevant context."""
        query_embedding = await get_embedding(session, query)

        if not query_embedding:
            return ""

        self.last_query_types = detect_query_type(query)

        results = await self.tesseract.retrieve(
            query_text=query,
            query_embedding=query_embedding,
            session_key=self.session_key,
            reference_time=datetime.now(),
            limit=10,
        )

        self.last_retrieval = results

        if not results:
            return ""

        context_parts = []
        for i, (node, charge) in enumerate(results[:8], 1):
            speaker = node.metadata.get("speaker", "Unknown")
            content = node.content[:500]
            context_parts.append(f"[Memory {i}] {speaker}: {content}")

        return "\n\n".join(context_parts)

    def print_query_types(self):
        """Print detected query types."""
        if not self.last_query_types:
            return

        print(f"\n{Colors.CYAN}Query Type Detection:{Colors.END}")
        for qtype, score in sorted(
            self.last_query_types.items(),
            key=lambda x: x[1],
            reverse=True
        ):
            if score > 0.1:
                color = {
                    QueryType.TEMPORAL: Colors.BLUE,
                    QueryType.ENTITY: Colors.GREEN,
                    QueryType.MULTI_HOP: Colors.YELLOW,
                    QueryType.ADVERSARIAL: Colors.RED,
                    QueryType.OPEN: Colors.END,
                }.get(qtype, Colors.END)
                bar = "█" * int(score * 10)
                print(f"  {color}{qtype:12s}{Colors.END} {bar} {score:.2f}")

    def print_memories(self):
        """Print last retrieved memories."""
        if not self.last_retrieval:
            print(f"{Colors.YELLOW}No memories retrieved yet.{Colors.END}")
            return

        print(f"\n{Colors.CYAN}{'='*50}")
        print(f"Retrieved Memories")
        print(f"{'='*50}{Colors.END}\n")

        for i, (node, charge) in enumerate(self.last_retrieval[:5], 1):
            speaker = node.metadata.get("speaker", "Unknown")
            content = node.content[:200]
            if len(node.content) > 200:
                content += "..."

            print(f"{Colors.GREEN}[{i}] Charge: {charge:.3f}{Colors.END}")
            print(f"    Speaker: {speaker}")
            print(f"    Content: {content}")
            print()

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        nodes = await self.storage.get_nodes_by_session(self.session_key)
        speakers = list(set(n.metadata.get("speaker", "Unknown") for n in nodes))
        return {
            "total_memories": len(nodes),
            "speakers": speakers,
        }

    async def process_input(
        self,
        session: aiohttp.ClientSession,
        user_input: str,
    ) -> str | None:
        """Process user input and return response or None for commands."""
        user_input = user_input.strip()

        # Handle commands
        if user_input.startswith("/"):
            cmd = user_input.lower()

            if cmd == "/quit" or cmd == "/exit":
                print(f"\n{Colors.CYAN}Goodbye!{Colors.END}")
                return None

            elif cmd == "/memory":
                self.print_memories()
                return ""

            elif cmd == "/stats":
                stats = await self.get_stats()
                print(f"\n{Colors.CYAN}Memory Statistics:{Colors.END}")
                print(f"  Total memories: {stats['total_memories']}")
                print(f"  Speakers: {', '.join(stats['speakers']) if stats['speakers'] else 'None'}")
                return ""

            elif cmd == "/clear":
                self.chat_history = []
                print(f"{Colors.GREEN}Chat history cleared.{Colors.END}")
                return ""

            else:
                print(f"{Colors.YELLOW}Unknown command: {user_input}{Colors.END}")
                return ""

        # Regular message processing
        # 1. Ingest user message
        await self.ingest_message(session, user_input, self.user_name)
        self.chat_history.append({"role": "user", "content": user_input})

        # 2. Retrieve context
        print(f"{Colors.YELLOW}Retrieving memories...{Colors.END}")
        context = await self.retrieve_context(session, user_input)

        # 3. Show query types
        self.print_query_types()

        # 4. Generate response
        print(f"{Colors.YELLOW}Generating response...{Colors.END}")
        response = await generate_response(
            session, user_input, context, self.chat_history
        )

        # 5. Ingest response
        await self.ingest_message(session, response, "Assistant")
        self.chat_history.append({"role": "assistant", "content": response})

        return response


async def main():
    """Main chat loop."""
    print_header()

    chat = TesseractChat()

    # Get user name
    name = input(f"{Colors.CYAN}Enter your name (default: User): {Colors.END}").strip()
    if name:
        chat.user_name = name
    print(f"\n{Colors.GREEN}Welcome, {chat.user_name}!{Colors.END}\n")

    async with aiohttp.ClientSession() as session:
        # Check Ollama connection
        try:
            async with session.get(f"{OLLAMA_BASE_URL}/api/tags") as response:
                if response.status != 200:
                    print(f"{Colors.RED}Warning: Cannot connect to Ollama at {OLLAMA_BASE_URL}{Colors.END}")
        except Exception as e:
            print(f"{Colors.RED}Warning: Ollama not available: {e}{Colors.END}")
            print(f"{Colors.YELLOW}Make sure Ollama is running with: ollama serve{Colors.END}\n")

        while True:
            try:
                user_input = input(f"\n{Colors.BOLD}{chat.user_name}:{Colors.END} ")

                if not user_input.strip():
                    continue

                result = await chat.process_input(session, user_input)

                if result is None:  # Quit command
                    break
                elif result:  # Got a response
                    print(f"\n{Colors.BOLD}Assistant:{Colors.END} {result}")

            except KeyboardInterrupt:
                print(f"\n\n{Colors.CYAN}Goodbye!{Colors.END}")
                break
            except Exception as e:
                print(f"{Colors.RED}Error: {e}{Colors.END}")


if __name__ == "__main__":
    asyncio.run(main())
