"""Debug single_hop retrieval failures - Steve Jobs style."""

import json
import asyncio
import aiohttp
import re
import math
from pathlib import Path
from collections import Counter
import numpy as np

OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"

# The failing questions
FAILING_SINGLE_HOP = [
    {
        "question": "What do Melanie's kids like?",
        "gold": "dinosaurs, nature",
        "generated": "painting, exploring nature, camping, playing at the beach"
    },
    {
        "question": "What did Melanie paint recently?",
        "gold": "sunset",
        "generated": "horse"
    },
    {
        "question": "What are Melanie's pets' names?",
        "gold": "Oliver, Luna, Bailey",
        "generated": "Oliver and Oscar"
    },
    {
        "question": "What subject have Caroline and Melanie both painted?",
        "gold": "Sunsets",
        "generated": "abstract art"
    },
    {
        "question": "What symbols are important to Caroline?",
        "gold": "Rainbow flag, transgender symbol",
        "generated": "rainbow flag mural, eagle, sunflowers, roses"
    },
]

STOPWORDS = set([
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "this", "that",
    "these", "those", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just",
    "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain"
])

def simple_tokenize(text: str) -> list[str]:
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]

def compute_bm25_scores(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    query_tokens = simple_tokenize(query)
    if not query_tokens:
        return [0.0] * len(documents)

    doc_tokens = [simple_tokenize(doc) for doc in documents]
    doc_lengths = [len(tokens) for tokens in doc_tokens]
    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1

    N = len(documents)
    idf = {}
    for term in set(query_tokens):
        df = sum(1 for tokens in doc_tokens if term in tokens)
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    scores = []
    for i, tokens in enumerate(doc_tokens):
        score = 0.0
        term_freq = Counter(tokens)
        doc_len = doc_lengths[i]
        for term in query_tokens:
            if term in term_freq:
                tf = term_freq[term]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * doc_len / avg_doc_length)
                score += idf.get(term, 0) * (numerator / denominator)
        scores.append(score)
    return scores

def cosine_similarity(vec1, vec2) -> float:
    if vec1 is None or vec2 is None:
        return 0.0
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    dot = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

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

async def main():
    print("=" * 80)
    print("SINGLE-HOP FAILURE DEBUG - FINDING THE ROOT CAUSE")
    print("=" * 80)

    # Load first conversation (Caroline & Melanie)
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conversation = data[0]  # Caroline & Melanie
    messages = extract_messages(conversation)

    print(f"\nLoaded {len(messages)} messages from conversation")

    # First, find where gold answers appear in the conversation
    for failure in FAILING_SINGLE_HOP:
        q = failure["question"]
        gold = failure["gold"]

        print(f"\n{'='*80}")
        print(f"QUESTION: {q}")
        print(f"GOLD: {gold}")
        print("="*80)

        # Search for gold answer in messages
        gold_lower = gold.lower()
        gold_words = set(gold_lower.replace(",", " ").replace('"', '').split())

        found_in = []
        for i, msg in enumerate(messages):
            text_lower = msg['text'].lower()
            # Check if any gold word appears
            matches = [w for w in gold_words if w in text_lower]
            if matches:
                found_in.append((i, msg, matches))

        print(f"\nGold answer words found in {len(found_in)} messages:")
        for idx, msg, matches in found_in[:10]:
            print(f"\n  [{idx}] Session {msg['session']}, {msg['speaker']}:")
            print(f"      Matches: {matches}")
            print(f"      Text: {msg['text'][:200]}...")

    # Now test retrieval with different weight configurations
    print("\n\n" + "="*80)
    print("TESTING DIFFERENT WEIGHT CONFIGURATIONS")
    print("="*80)

    async with aiohttp.ClientSession() as session:
        # Get embeddings for messages (sample first 100 for speed)
        sample_messages = messages[:200]
        print(f"\nEmbedding {len(sample_messages)} messages...")

        for i, msg in enumerate(sample_messages):
            text = f"{msg['speaker']}: {msg['text']}"
            msg['embedding'] = await get_embedding(session, text)
            if (i + 1) % 50 == 0:
                print(f"  Embedded {i+1}/{len(sample_messages)}")

        # Test one question with different weights
        test_q = FAILING_SINGLE_HOP[0]  # "What do Melanie's kids like?"
        question = test_q["question"]
        gold = test_q["gold"]

        print(f"\n\nTesting: '{question}'")
        print(f"Gold: {gold}")

        question_embedding = await get_embedding(session, question)

        texts = [f"{m['speaker']}: {m['text']}" for m in sample_messages]
        bm25_scores = compute_bm25_scores(question, texts)
        max_bm25 = max(bm25_scores) if bm25_scores else 1
        if max_bm25 > 0:
            bm25_scores = [s / max_bm25 for s in bm25_scores]

        semantic_scores = []
        for msg in sample_messages:
            if msg.get('embedding') and question_embedding:
                sim = cosine_similarity(question_embedding, msg['embedding'])
                semantic_scores.append(sim)
            else:
                semantic_scores.append(0.0)

        max_sem = max(semantic_scores) if semantic_scores else 1
        if max_sem > 0:
            semantic_scores = [s / max_sem for s in semantic_scores]

        # Try different weight configurations
        weight_configs = [
            ("Current (0.35 semantic)", 0.35),
            ("Heavy BM25 (0.20 semantic)", 0.20),
            ("Very Heavy BM25 (0.10 semantic)", 0.10),
            ("Pure BM25 (0.00 semantic)", 0.00),
            ("Balanced (0.50 semantic)", 0.50),
            ("Heavy semantic (0.70)", 0.70),
        ]

        for name, semantic_weight in weight_configs:
            bm25_weight = 1 - semantic_weight
            combined = [
                bm25_weight * bm25 + semantic_weight * sem
                for bm25, sem in zip(bm25_scores, semantic_scores)
            ]

            # Get top 15
            ranked = sorted(enumerate(combined), key=lambda x: -x[1])[:15]

            # Check if gold is in top results
            gold_lower = gold.lower()
            found_rank = None
            for rank, (idx, score) in enumerate(ranked, 1):
                if any(w in sample_messages[idx]['text'].lower() for w in gold_lower.split(", ")):
                    found_rank = rank
                    break

            print(f"\n{name}:")
            print(f"  Gold found at rank: {found_rank if found_rank else 'NOT FOUND in top 15'}")
            print(f"  Top 3 results:")
            for rank, (idx, score) in enumerate(ranked[:3], 1):
                msg = sample_messages[idx]
                print(f"    [{rank}] score={score:.3f} | {msg['speaker']}: {msg['text'][:80]}...")

if __name__ == "__main__":
    asyncio.run(main())
