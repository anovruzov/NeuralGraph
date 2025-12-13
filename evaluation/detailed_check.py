"""Detailed check on specific failing cases."""

import json
import math
import re
from pathlib import Path
from collections import Counter

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
    # NEW: location/movement
    "move": ["moved", "moving", "came", "from", "left"],
    "moved": ["move", "moving", "came", "from", "left"],
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

def main():
    print("=" * 80)
    print("DETAILED CHECK ON FAILING CASES")
    print("=" * 80)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    messages = extract_messages(data[0])
    texts = [f"{m['speaker']}: {m['text']}" for m in messages]

    # Check specific failing cases
    failing = [
        ("Where did Caroline move from 4 years ago?", "Sweden"),
        ("What activities does Melanie partake in?", "pottery, camping, painting, swimming"),
        ("Where has Melanie camped?", "beach, mountains, forest"),
        ("What books has Melanie read?", '"Nothing is Impossible", "Charlotte\'s Web"'),
    ]

    for question, gold in failing:
        print(f"\n{'='*80}")
        print(f"Q: {question}")
        print(f"Gold: {gold}")
        print("="*80)

        # Find where gold appears
        gold_words = [w.lower().strip('"') for w in gold.replace(",", " ").split() if len(w) > 3]
        print(f"\nGold words to find: {gold_words}")

        found_msgs = []
        for i, msg in enumerate(messages):
            text_lower = msg['text'].lower()
            matches = [w for w in gold_words if w in text_lower]
            if matches:
                found_msgs.append((i, msg, matches))

        print(f"\nMessages containing gold words:")
        for idx, msg, matches in found_msgs[:5]:
            print(f"  [{idx}] {matches}: {msg['text'][:100]}...")

        # BM25 ranking
        bm25_orig = compute_bm25_scores(question, texts, expand_query=False)
        bm25_exp = compute_bm25_scores(question, texts, expand_query=True)

        orig_tokens = simple_tokenize(question)
        exp_tokens = expand_query_tokens(orig_tokens)
        print(f"\nQuery tokens: {orig_tokens} -> {exp_tokens}")

        # Check ranking of gold messages
        ranked_orig = sorted(enumerate(bm25_orig), key=lambda x: -x[1])
        ranked_exp = sorted(enumerate(bm25_exp), key=lambda x: -x[1])

        print(f"\nGold message rankings:")
        for idx, msg, matches in found_msgs[:3]:
            rank_orig = next((i for i, (j, _) in enumerate(ranked_orig) if j == idx), -1) + 1
            rank_exp = next((i for i, (j, _) in enumerate(ranked_exp) if j == idx), -1) + 1
            change = rank_orig - rank_exp
            sign = "+" if change > 0 else ""
            print(f"  [{idx}] Without: {rank_orig:3d} | With: {rank_exp:3d} | {sign}{change}")

if __name__ == "__main__":
    main()
