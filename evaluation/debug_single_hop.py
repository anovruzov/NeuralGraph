"""Quick diagnostic: Check what's retrieved for failing single-hop questions."""

import asyncio
import json
import sys
from pathlib import Path
import aiohttp
import re
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.field_retriever import HybridFieldRetriever, FieldConfig

# Import directly from the module to get modified version
import importlib
import sys
# Force reimport to get latest changes
if 'memmachine.neural_graph.field_retriever' in sys.modules:
    importlib.reload(sys.modules['memmachine.neural_graph.field_retriever'])
from memmachine.neural_graph.field_retriever import HybridFieldRetriever

OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"

# Failing single-hop questions to debug
FAILING_QUESTIONS = [
    {
        "question": "Where did Caroline move from 4 years ago?",
        "gold": "Sweden",
        "generated": "her home country"
    },
    {
        "question": "What is Caroline's identity?",
        "gold": "Transgender woman",
        "generated": "Transgender"
    },
    {
        "question": "What career path has Caroline decided to persue?",
        "gold": "counseling or mental health for Transgender people",
        "generated": "Counseling and mental health"
    }
]

async def get_embedding(session, text: str) -> list[float]:
    async with session.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBEDDING_MODEL, "prompt": text},
        timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        result = await response.json()
        return result.get("embedding", [])

def extract_messages(item) -> list[dict]:
    """Extract messages from conversation."""
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_messages = conversation[session_key]
        datetime_str = conversation.get(datetime_key, "")

        for msg in session_messages:
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": msg.get("text", ""),
                "datetime": datetime_str,
                "session": session_idx,
            })
        session_idx += 1

    return messages

def extract_entities(text: str) -> list[str]:
    entities = re.findall(r'\b[A-Z][a-z]+\b', text)
    common = {'I', 'The', 'A', 'An', 'It', 'This', 'That', 'We', 'They', 'He', 'She', 'But', 'And', 'So', 'Yes', 'No'}
    return list(set(e for e in entities if e not in common))

async def main():
    print("=" * 80)
    print("SINGLE-HOP FAILURE DIAGNOSTIC")
    print("=" * 80)

    # Load first conversation
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conversation = data[0]  # Caroline & Melanie
    messages = extract_messages(conversation)

    print(f"\nLoaded {len(messages)} messages from conversation")

    # Create storage and retriever
    storage = InMemoryNeuralGraphStorage()
    config = FieldConfig(
        use_entity_binding=True,
        entity_binding_weight=0.45,
        use_speaker_binding=True,
        speaker_binding_weight=0.35,
    )
    retriever = HybridFieldRetriever(storage, config)

    session_key = "caroline_melanie"

    async with aiohttp.ClientSession() as http_session:
        # Ingest messages
        print("\nIngesting messages...")
        for i, msg in enumerate(messages):
            content = msg["text"]
            speaker = msg["speaker"]
            entities = extract_entities(content)
            embedding = await get_embedding(http_session, content)

            node = NeuralNode(
                node_id=f"msg_{i}",
                session_key=session_key,
                content=content,
                layer=NodeLayer.MESSAGE,
                embedding=embedding,
                created_at=datetime.now(),
                metadata={"speaker": speaker},
                entity_ids=entities,
            )
            await storage.save_node(node)

        print(f"  Ingested {len(messages)} nodes")

        # Test each failing question
        for q_data in FAILING_QUESTIONS:
            question = q_data["question"]
            gold = q_data["gold"]

            print(f"\n{'='*80}")
            print(f"QUESTION: {question}")
            print(f"GOLD ANSWER: {gold}")
            print("="*80)

            # Get query embedding
            query_embedding = await get_embedding(http_session, question)

            # Retrieve - with extended limit to see speaker expansion effect
            results = await retriever.retrieve(
                query_text=question,
                query_embedding=query_embedding,
                session_key=session_key,
                limit=40  # Increased to see more candidates
            )

            print(f"\nTOP {len(results)} RETRIEVED NODES:")
            print("-" * 60)

            # Check if gold answer is in any retrieved content
            gold_lower = gold.lower()
            found_gold = False

            for rank, (node, charge) in enumerate(results, 1):
                content = node.content[:200].replace('\n', ' ')
                has_gold = gold_lower in node.content.lower()
                marker = " <<< CONTAINS GOLD!" if has_gold else ""
                if has_gold:
                    found_gold = True

                print(f"\n[{rank}] charge={charge:.3f}{marker}")
                print(f"    Speaker: {node.metadata.get('speaker', '?')}")
                print(f"    Entities: {node.entity_ids}")
                print(f"    Content: {content}...")

            print(f"\n>>> GOLD ANSWER FOUND IN TOP 15: {'YES' if found_gold else 'NO'}")

            # Also search ALL messages for gold answer
            if not found_gold:
                print("\nSearching ALL messages for gold answer...")
                all_nodes = await storage.get_all_nodes(session_key)
                for node in all_nodes:
                    if gold_lower in node.content.lower():
                        # Compute similarity for this specific node
                        if node.embedding:
                            import numpy as np
                            node_emb = np.array(node.embedding, dtype=np.float32)
                            query_emb = np.array(query_embedding, dtype=np.float32)
                            node_norm = np.linalg.norm(node_emb)
                            query_norm = np.linalg.norm(query_emb)
                            if node_norm > 1e-10 and query_norm > 1e-10:
                                sim = float(np.dot(node_emb / node_norm, query_emb / query_norm))
                        else:
                            sim = 0
                        print(f"  FOUND in node {node.node_id} (similarity={sim:.3f}): {node.content[:150]}...")
                        break
                else:
                    print(f"  NOT FOUND anywhere in conversation!")

if __name__ == "__main__":
    asyncio.run(main())
