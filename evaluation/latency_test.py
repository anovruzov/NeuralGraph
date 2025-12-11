"""Quick latency test for Tesseract retrieval."""
import asyncio
import time
import sys
sys.path.insert(0, 'src')
import aiohttp
import numpy as np

from memmachine.neural_graph.tesseract import Tesseract
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage as NeuralStorage

OLLAMA_BASE_URL = 'http://localhost:11434'
EMBEDDING_MODEL = 'nomic-embed-text'

async def get_embedding(session, text):
    async with session.post(
        f'{OLLAMA_BASE_URL}/api/embeddings',
        json={'model': EMBEDDING_MODEL, 'prompt': text}
    ) as response:
        result = await response.json()
        return np.array(result['embedding'], dtype=np.float32)

async def run_latency_test():
    storage = NeuralStorage()
    tesseract = Tesseract(storage)
    session_key = 'latency_test'

    # Ingest some test data - similar to a conversation
    print('Ingesting test messages...')
    from memmachine.neural_graph.data_types import NeuralNode
    import uuid
    for i in range(500):
        content = f'Test message {i} about topic {i % 10} with some interesting content. This message contains information about activities like hiking, painting, and playing instruments.'
        node = NeuralNode(
            node_id=str(uuid.uuid4()),
            content=content,
            embedding=np.random.randn(768).astype(np.float32),
            layer='default',
            session_key=session_key,
            metadata={'speaker': f'user_{i % 5}'}
        )
        await storage.save_node(node)

    print(f'Ingested {len(storage._nodes)} nodes')

    async with aiohttp.ClientSession() as http:
        # Run retrieval latency tests
        test_queries = [
            'What did the user say about topic 3?',
            'Tell me about the conversation',
            'When did they discuss topic 5?',
            'Who mentioned the project deadline?',
            'What activities do they enjoy?',
            'Where did they move from?',
            'What instruments does the person play?',
        ]

        latencies = []
        embed_latencies = []

        for query in test_queries:
            # Get embedding
            embed_start = time.perf_counter()
            embedding = await get_embedding(http, query)
            embed_time = (time.perf_counter() - embed_start) * 1000
            embed_latencies.append(embed_time)

            # Retrieval only
            retrieval_start = time.perf_counter()
            results = await tesseract.retrieve(
                query_text=query,
                query_embedding=embedding,
                session_key=session_key,
                limit=50
            )
            retrieval_time = (time.perf_counter() - retrieval_start) * 1000
            latencies.append(retrieval_time)

            print(f'Query: "{query[:40]}..."')
            print(f'  Embedding: {embed_time:.2f}ms')
            print(f'  Retrieval: {retrieval_time:.2f}ms (returned {len(results)} nodes)')

        print()
        print('=' * 60)
        print(f'LATENCY SUMMARY (500 nodes, {len(test_queries)} queries)')
        print('=' * 60)
        print(f'EMBEDDING (ollama nomic-embed-text):')
        print(f'  Mean: {np.mean(embed_latencies):.2f}ms')
        print(f'  Min: {np.min(embed_latencies):.2f}ms')
        print(f'  Max: {np.max(embed_latencies):.2f}ms')
        print()
        print(f'RETRIEVAL (Tesseract 4D fusion):')
        print(f'  Mean: {np.mean(latencies):.2f}ms')
        print(f'  Min: {np.min(latencies):.2f}ms')
        print(f'  Max: {np.max(latencies):.2f}ms')

if __name__ == '__main__':
    asyncio.run(run_latency_test())
