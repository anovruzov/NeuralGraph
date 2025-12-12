"""Quick test to verify entity matching fix for speaker metadata"""
import json
import asyncio
import aiohttp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract

OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"

async def get_embedding(session, text: str) -> list[float]:
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except:
        return []


async def test_entity_fix():
    # Load data
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conv = data[0]

    # Extract messages
    messages = []
    session_idx = 1
    while f"session_{session_idx}" in conv.get("conversation", conv):
        conv_data = conv.get("conversation", conv)
        session_date = conv_data.get(f"session_{session_idx}_date_time", "")
        for msg in conv_data.get(f"session_{session_idx}", []):
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": msg.get("text", ""),
                "datetime": session_date,
            })
        session_idx += 1

    print(f"Loaded {len(messages)} messages")

    # Build storage
    async with aiohttp.ClientSession() as http:
        storage = InMemoryNeuralGraphStorage()
        tesseract = Tesseract(storage)

        print("Building embeddings...")
        for msg_idx, msg in enumerate(messages):
            if msg_idx % 50 == 0:
                print(f"  {msg_idx}/{len(messages)}")
            embedding = await get_embedding(http, msg["text"])
            node = NeuralNode(
                node_id=f"msg_{msg_idx}",
                session_key="conv_0",
                content=msg["text"],
                layer=NodeLayer.MESSAGE,
                embedding=embedding,
                metadata={"speaker": msg["speaker"], "datetime": msg["datetime"]},
            )
            await storage.save_node(node)

        # Test "When did Melanie get hurt?"
        question = "When did Melanie get hurt?"
        gold = "September 2023"

        print("\n" + "="*80)
        print(f"QUESTION: {question}")
        print(f"GOLD ANSWER: {gold}")
        print("="*80)

        query_emb = await get_embedding(http, question)
        results = await tesseract.retrieve(
            query_text=question,
            query_embedding=query_emb,
            session_key="conv_0",
            limit=15,
        )

        print(f"\nTOP 15 RETRIEVED MEMORIES:")
        found_hurt = False
        hurt_rank = -1
        for i, (node, charge) in enumerate(results):
            dt = node.metadata.get("datetime", "") if node.metadata else ""
            speaker = node.metadata.get("speaker", "") if node.metadata else ""
            content_preview = node.content[:120].replace('\n', ' ')

            is_hurt_msg = "hurt" in node.content.lower() and "got hurt" in node.content.lower()
            marker = "<<<< TARGET" if is_hurt_msg else ""

            print(f"\n{i+1}. [charge={charge:.3f}] [{dt}] [{speaker}] {marker}")
            print(f"   {content_preview}...")

            if is_hurt_msg and not found_hurt:
                found_hurt = True
                hurt_rank = i + 1

        print("\n" + "="*80)
        if found_hurt:
            print(f"TARGET 'hurt' MESSAGE RANK: {hurt_rank}")
            if hurt_rank <= 3:
                print("SUCCESS! Target is in top 3")
            elif hurt_rank <= 5:
                print("CLOSE - Target is in top 5")
            else:
                print(f"NEEDS IMPROVEMENT - Target should be top 3, got rank {hurt_rank}")
        else:
            print("FAILURE - Target message not found in top 15")


if __name__ == "__main__":
    asyncio.run(test_entity_fix())
