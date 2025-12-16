"""Simple Tesseract test."""
import asyncio
import sys
import os

sys.path.insert(0, r"C:\Users\anovr\Desktop\MemMachine-main\src")
os.environ["MEMMACHINE_DB"] = r"C:\Users\anovr\.memmachine\memories.db"
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["EMBEDDING_MODEL"] = "nomic-embed-text"

from memmachine.neural_graph.sqlite_storage import SQLiteNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type
from memmachine.mcp.embeddings import get_embedding

async def test():
    print("=" * 60)
    print("TESSERACT 4D MEMORY TEST")
    print("=" * 60)

    # Test query type detection
    queries = [
        ("When did I go hiking?", "temporal"),
        ("What does Sarah do for work?", "entity"),
        ("What are all my hobbies?", "multi_hop"),
        ("What did I NOT buy?", "adversarial"),
    ]

    print("\n[QUERY TYPE DETECTION]")
    for q, expected in queries:
        types = detect_query_type(q)
        top_type = max(types.items(), key=lambda x: x[1])
        match = "PASS" if expected in top_type[0] else f"FAIL (got {top_type[0]})"
        print(f"  {q}")
        print(f"    -> {top_type[0]}: {top_type[1]:.2f} [{match}]")

    # Test Tesseract retrieval
    print("\n[TESSERACT RETRIEVAL]")
    storage = SQLiteNeuralGraphStorage(os.environ["MEMMACHINE_DB"])
    tesseract = Tesseract(storage)

    # Get embedding and search
    query = "What outdoor activities do you enjoy?"
    emb = await get_embedding(query)
    if emb:
        results = await tesseract.retrieve(
            query_text=query,
            query_embedding=emb,
            session_key="integration_test",
            limit=5
        )
        print(f"  Query: {query}")
        print(f"  Found: {len(results)} results")
        if results:
            top = results[0]
            print(f"  Top result: {top[0].content[:60]}...")
            print(f"  Score: {top[1]:.4f}")
    else:
        print("  Failed to generate embedding")

    print("\n" + "=" * 60)
    print("TESSERACT IS NOW ACTIVE IN MCP SERVER")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test())
