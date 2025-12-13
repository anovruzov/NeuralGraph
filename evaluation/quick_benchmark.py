"""Quick benchmark to test single_hop improvements."""

import json
import asyncio
import aiohttp
import math
import re
from pathlib import Path
from collections import Counter
import numpy as np

OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"

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

SYNONYM_MAP = {
    "like": ["love", "enjoy", "prefer", "want", "stoked", "into", "fond"],
    "love": ["like", "enjoy", "adore", "prefer", "fond"],
    "enjoy": ["like", "love", "prefer"],
    "kids": ["children", "kid", "child", "younger", "sons", "daughters"],
    "children": ["kids", "child", "younger"],
    "child": ["kid", "children", "kids"],
    "paint": ["painted", "painting", "drew", "draw", "did", "made", "created"],
    "painted": ["paint", "painting", "drew", "did", "made"],
    "did": ["made", "created", "painted", "done"],
    "made": ["did", "created", "painted", "done"],
    "read": ["reading", "reads", "finished"],
    "run": ["running", "ran", "jog", "jogging"],
    "running": ["run", "ran", "jog"],
    "recently": ["lately", "last", "week", "yesterday", "today"],
    "lately": ["recently", "last", "week"],
    "pet": ["pets", "cat", "dog", "animal", "pup", "kitty"],
    "pets": ["pet", "cats", "dogs", "animals"],
    "name": ["names", "called"],
    "names": ["name", "called"],
    "symbol": ["symbols", "flag", "sign", "meaning"],
    "symbols": ["symbol", "flag", "signs"],
    "activities": ["hobbies", "partake", "enjoy", "doing"],
    "hobbies": ["activities", "interests", "enjoy"],
}

STEM_RULES = [
    (r"ing$", ""),
    (r"ed$", ""),
    (r"s$", ""),
    (r"ly$", ""),
    (r"ies$", "y"),
]

def simple_tokenize(text: str) -> list[str]:
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]

def stem_word(word: str) -> str:
    for pattern, replacement in STEM_RULES:
        if re.search(pattern, word) and not word.endswith("ss"):
            return re.sub(pattern, replacement, word)
    return word

def expand_query_tokens(tokens: list[str]) -> list[str]:
    expanded = set(tokens)
    for token in tokens:
        if token in SYNONYM_MAP:
            expanded.update(SYNONYM_MAP[token])
        stemmed = stem_word(token)
        if stemmed != token and len(stemmed) > 2:
            expanded.add(stemmed)
            if stemmed in SYNONYM_MAP:
                expanded.update(SYNONYM_MAP[stemmed])
    return list(expanded)

def compute_bm25_scores(query: str, documents: list[str], expand_query: bool = False, k1=1.5, b=0.75) -> list[float]:
    query_tokens = simple_tokenize(query)
    if not query_tokens:
        return [0.0] * len(documents)

    if expand_query:
        query_tokens = expand_query_tokens(query_tokens)

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
        if session_key not in conversation:
            break
        for msg in conversation[session_key]:
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": msg.get("text", ""),
            })
        session_idx += 1
    return messages

def gold_in_context(gold: str, context: list[str]) -> bool:
    """Check if gold answer appears in any of the retrieved context."""
    gold_lower = gold.lower()
    gold_words = set(gold_lower.replace(",", " ").replace('"', '').split())

    for text in context:
        text_lower = text.lower()
        # Check if all significant gold words appear
        found = sum(1 for w in gold_words if len(w) > 3 and w in text_lower)
        if found >= len([w for w in gold_words if len(w) > 3]) * 0.5:  # 50% match
            return True
    return False

async def main():
    print("=" * 80)
    print("QUICK BENCHMARK: SINGLE_HOP WITH QUERY EXPANSION")
    print("=" * 80)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    # Get single_hop questions from first conversation
    conv = data[0]
    qa_list = conv.get("qa", [])
    single_hop_qs = [q for q in qa_list if q.get("category") == 1]

    messages = extract_messages(conv)
    texts = [f"{m['speaker']}: {m['text']}" for m in messages]

    print(f"\nFound {len(single_hop_qs)} single_hop questions")
    print(f"Loaded {len(messages)} messages")

    # Get embeddings
    print("\nGetting embeddings...")
    async with aiohttp.ClientSession() as session:
        embeddings = []
        for i, msg in enumerate(messages):
            emb = await get_embedding(session, f"{msg['speaker']}: {msg['text']}")
            embeddings.append(emb)
            if (i + 1) % 100 == 0:
                print(f"  Embedded {i+1}/{len(messages)}")

        print("\nTesting retrieval (top-30)...")

        results_without = []
        results_with = []

        for q in single_hop_qs[:10]:  # Test first 10 single_hop questions
            question = q["question"]
            gold = q["answer"]

            # Get question embedding
            q_emb = await get_embedding(session, question)

            # BM25 scores
            bm25_orig = compute_bm25_scores(question, texts, expand_query=False)
            bm25_exp = compute_bm25_scores(question, texts, expand_query=True)

            # Semantic scores
            sem_scores = []
            for emb in embeddings:
                if emb and q_emb:
                    sem_scores.append(cosine_similarity(q_emb, emb))
                else:
                    sem_scores.append(0.0)

            # Normalize
            max_bm25_orig = max(bm25_orig) if bm25_orig else 1
            max_bm25_exp = max(bm25_exp) if bm25_exp else 1
            max_sem = max(sem_scores) if sem_scores else 1

            bm25_orig = [s / max_bm25_orig if max_bm25_orig > 0 else 0 for s in bm25_orig]
            bm25_exp = [s / max_bm25_exp if max_bm25_exp > 0 else 0 for s in bm25_exp]
            sem_scores = [s / max_sem if max_sem > 0 else 0 for s in sem_scores]

            # Combine (single_hop uses 0.35 semantic weight)
            semantic_weight = 0.35
            bm25_weight = 1 - semantic_weight

            combined_orig = [bm25_weight * b + semantic_weight * s for b, s in zip(bm25_orig, sem_scores)]
            combined_exp = [bm25_weight * b + semantic_weight * s for b, s in zip(bm25_exp, sem_scores)]

            # Get top 30
            ranked_orig = sorted(enumerate(combined_orig), key=lambda x: -x[1])[:30]
            ranked_exp = sorted(enumerate(combined_exp), key=lambda x: -x[1])[:30]

            context_orig = [messages[idx]["text"] for idx, _ in ranked_orig]
            context_exp = [messages[idx]["text"] for idx, _ in ranked_exp]

            found_orig = gold_in_context(str(gold), context_orig)
            found_exp = gold_in_context(str(gold), context_exp)

            results_without.append(found_orig)
            results_with.append(found_exp)

            status_orig = "Y" if found_orig else "N"
            status_exp = "Y" if found_exp else "N"
            improvement = "=" if found_orig == found_exp else ("+" if found_exp else "-")

            print(f"\n[{status_orig}->{status_exp}] {improvement} Q: {question[:50]}...")
            print(f"    Gold: {gold}")

        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Without expansion: {sum(results_without)}/{len(results_without)} gold answers in top-30")
        print(f"With expansion:    {sum(results_with)}/{len(results_with)} gold answers in top-30")
        print(f"Improvement: +{sum(results_with) - sum(results_without)} questions")

if __name__ == "__main__":
    asyncio.run(main())
