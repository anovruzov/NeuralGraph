"""
Deep debug temporal questions - find out exactly why we're failing
"""
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


async def debug_temporal():
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

        # Test specific temporal questions that are failing
        test_questions = [
            ("When did Melanie run a charity race?", "The sunday before 25 May 2023"),
            ("When is Melanie planning on going camping?", "June 2023"),
            ("When did Caroline give a speech at a school?", "The week before 9 June 2023"),
            ("When is Caroline going to the transgender conference?", "July 2023"),
        ]

        for question, gold in test_questions:
            print("\n" + "="*80)
            print(f"QUESTION: {question}")
            print(f"GOLD ANSWER: {gold}")
            print("="*80)

            query_emb = await get_embedding(http, question)
            results = await tesseract.retrieve(
                query_text=question,
                query_embedding=query_emb,
                session_key="conv_0",
                limit=10,
            )

            print(f"\nTOP 10 RETRIEVED MEMORIES:")
            for i, (node, charge) in enumerate(results):
                dt = node.metadata.get("datetime", "") if node.metadata else ""
                speaker = node.metadata.get("speaker", "") if node.metadata else ""
                content_preview = node.content[:150].replace('\n', ' ')
                print(f"\n{i+1}. [charge={charge:.3f}] [{dt}] [{speaker}]")
                print(f"   {content_preview}...")

                # Highlight if this contains the key topic
                key_topics = []
                if "charity" in question.lower() or "race" in question.lower():
                    key_topics = ["charity", "race", "ran"]
                elif "camping" in question.lower():
                    key_topics = ["camping", "camp"]
                elif "speech" in question.lower() or "school" in question.lower():
                    key_topics = ["speech", "school", "talked", "spoke"]
                elif "conference" in question.lower():
                    key_topics = ["conference"]

                content_lower = node.content.lower()
                for topic in key_topics:
                    if topic in content_lower:
                        print(f"   *** CONTAINS KEY TOPIC: '{topic}' ***")


if __name__ == "__main__":
    asyncio.run(debug_temporal())
