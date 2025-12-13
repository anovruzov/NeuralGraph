"""Root cause analysis of single_hop retrieval failures."""

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

# The key failing questions with their evidence locations
FAILING_CASES = [
    {
        "question": "What do Melanie's kids like?",
        "gold": "dinosaurs, nature",
        "evidence": ["D6:6", "D4:8"],  # session_6[5], session_4[7]
        "target_indices": [97, 65],  # calculated flat indices
    },
    {
        "question": "What did Melanie paint recently?",
        "gold": "sunset",
        "evidence": ["D17:?"],
        "keywords_in_text": ["sunset", "sunsets", "painted"],
    },
    {
        "question": "What are Melanie's pets' names?",
        "gold": "Oliver, Luna, Bailey",
        "evidence": ["D7:?", "D13:?"],
        "keywords_in_text": ["oliver", "luna", "bailey", "pet", "cat", "dog"],
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

# Simple Porter-style stemmer for common suffixes
def stem(word: str) -> str:
    word = word.lower()
    if word.endswith('ing'):
        return word[:-3]
    if word.endswith('ed'):
        return word[:-2]
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    if word.endswith('ly'):
        return word[:-2]
    return word

def tokenize_with_stems(text: str) -> list[str]:
    tokens = simple_tokenize(text)
    # Add stems
    stems = [stem(t) for t in tokens]
    return list(set(tokens + stems))

def compute_bm25_scores_stemmed(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    """BM25 with stemming."""
    query_tokens = tokenize_with_stems(query)
    if not query_tokens:
        return [0.0] * len(documents)

    doc_tokens = [tokenize_with_stems(doc) for doc in documents]
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

def compute_bm25_scores(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    """Original BM25 without stemming."""
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

def extract_messages(item) -> list[dict]:
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

        for msg_idx, msg in enumerate(session_messages):
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": msg.get("text", ""),
                "datetime": datetime_str,
                "session": session_idx,
                "msg_idx": msg_idx,
            })
        session_idx += 1

    return messages

async def main():
    print("=" * 80)
    print("ROOT CAUSE ANALYSIS OF SINGLE-HOP FAILURES")
    print("=" * 80)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conversation = data[0]
    messages = extract_messages(conversation)

    print(f"\nLoaded {len(messages)} messages from conversation")

    # Test the first failing case in detail
    test_case = FAILING_CASES[0]  # "What do Melanie's kids like?"
    question = test_case["question"]
    gold = test_case["gold"]

    print(f"\n{'='*80}")
    print(f"DEEP ANALYSIS: {question}")
    print(f"GOLD: {gold}")
    print("="*80)

    # Find the target messages
    print("\n1. LOCATING TARGET MESSAGES:")
    for idx in [65, 97]:  # indices where nature and dinosaur appear
        msg = messages[idx]
        print(f"\n   [{idx}] Session {msg['session']}, msg {msg['msg_idx']}:")
        print(f"   Speaker: {msg['speaker']}")
        print(f"   Text: {msg['text'][:300]}")

    # Analyze BM25 scores for target messages
    print("\n\n2. BM25 ANALYSIS:")
    texts = [f"{m['speaker']}: {m['text']}" for m in messages]

    # Original BM25
    bm25_original = compute_bm25_scores(question, texts)
    # Stemmed BM25
    bm25_stemmed = compute_bm25_scores_stemmed(question, texts)

    print(f"\n   Question tokens (original): {simple_tokenize(question)}")
    print(f"   Question tokens (stemmed): {tokenize_with_stems(question)}")

    print(f"\n   Target message [65] tokens: {simple_tokenize(messages[65]['text'])[:20]}...")
    print(f"   Target message [97] tokens: {simple_tokenize(messages[97]['text'])[:20]}...")

    print(f"\n   BM25 scores for target messages:")
    print(f"   [65] Original: {bm25_original[65]:.4f}, Stemmed: {bm25_stemmed[65]:.4f}")
    print(f"   [97] Original: {bm25_original[97]:.4f}, Stemmed: {bm25_stemmed[97]:.4f}")

    # Find top BM25 results
    ranked_original = sorted(enumerate(bm25_original), key=lambda x: -x[1])[:10]
    ranked_stemmed = sorted(enumerate(bm25_stemmed), key=lambda x: -x[1])[:10]

    print(f"\n   Top 5 BM25 (original):")
    for idx, score in ranked_original[:5]:
        print(f"   [{idx}] {score:.4f}: {messages[idx]['text'][:80]}...")

    print(f"\n   Top 5 BM25 (stemmed):")
    for idx, score in ranked_stemmed[:5]:
        print(f"   [{idx}] {score:.4f}: {messages[idx]['text'][:80]}...")

    # Get embeddings and test semantic retrieval
    print("\n\n3. SEMANTIC ANALYSIS:")
    async with aiohttp.ClientSession() as session:
        print("   Getting embeddings for question and target messages...")
        question_emb = await get_embedding(session, question)
        target_65_emb = await get_embedding(session, f"{messages[65]['speaker']}: {messages[65]['text']}")
        target_97_emb = await get_embedding(session, f"{messages[97]['speaker']}: {messages[97]['text']}")

        if question_emb and target_65_emb and target_97_emb:
            sim_65 = cosine_similarity(question_emb, target_65_emb)
            sim_97 = cosine_similarity(question_emb, target_97_emb)
            print(f"\n   Semantic similarity scores:")
            print(f"   [65] (nature): {sim_65:.4f}")
            print(f"   [97] (dinosaur): {sim_97:.4f}")

            # Get embeddings for top BM25 results to compare
            print("\n   Embeddings for top BM25 results:")
            for idx, bm25_score in ranked_original[:5]:
                emb = await get_embedding(session, f"{messages[idx]['speaker']}: {messages[idx]['text']}")
                if emb:
                    sim = cosine_similarity(question_emb, emb)
                    print(f"   [{idx}] BM25={bm25_score:.4f}, Semantic={sim:.4f}")

    # Analyze what query reformulation would help
    print("\n\n4. QUERY REFORMULATION ANALYSIS:")
    reformulations = [
        "What do Melanie's kids like?",  # Original
        "What do Melanie kids like love enjoy",  # Add synonyms
        "Melanie children kids love like enjoy dinosaur nature",  # Add expected answers
        "What does Melanie's family enjoy doing together",  # Semantic variant
    ]

    for q in reformulations:
        scores = compute_bm25_scores(q, texts)
        s65, s97 = scores[65], scores[97]
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        rank_65 = next((i for i, (idx, _) in enumerate(ranked) if idx == 65), -1)
        rank_97 = next((i for i, (idx, _) in enumerate(ranked) if idx == 97), -1)
        print(f"\n   Query: '{q[:50]}...'")
        print(f"   [65] score={s65:.4f}, rank={rank_65 + 1}")
        print(f"   [97] score={s97:.4f}, rank={rank_97 + 1}")

    print("\n\n5. ROOT CAUSE SUMMARY:")
    print("   - BM25 fails because question 'What do Melanie's kids like?' has no")
    print("     direct lexical overlap with target messages:")
    print("     * [65] talks about 'younger kids love nature' (no 'like', no 'Melanie')")
    print("     * [97] talks about 'They were stoked for dinosaur' (no 'kids', no 'like')")
    print("   - Semantic retrieval may help if embeddings capture the relationship")
    print("   - Solution: Need entity-aware retrieval that links 'Melanie' -> 'they/kids'")

if __name__ == "__main__":
    asyncio.run(main())
