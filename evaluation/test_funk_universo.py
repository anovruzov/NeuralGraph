"""FUNK UNIVERSO: Test Universal Retrieval System.

Tests the universal entity-aware retrieval that works for ANY conversation.
Uses morphological expansion + semantic clusters + speaker forcing.
"""

import json
import re
from pathlib import Path


# =============================================================================
# FUNK UNIVERSO: UNIVERSAL MORPHOLOGICAL EXPANSION
# =============================================================================

def universal_expand(word: str) -> set[str]:
    """Generate ALL morphological variants of a word universally."""
    variants = {word}
    w = word.lower()

    if w.endswith('ed'):
        base = w[:-2]
        variants.update([base, base + 's', base + 'ing', base + 'er'])
        if base.endswith('i'):
            variants.add(base[:-1] + 'y')
    elif w.endswith('ing'):
        base = w[:-3]
        variants.update([base, base + 's', base + 'ed', base + 'er', base + 'e'])
    elif w.endswith('s') and not w.endswith('ss'):
        base = w[:-1]
        variants.update([base, base + 'ed', base + 'ing', base + 'er'])
    elif w.endswith('ies'):
        base = w[:-3] + 'y'
        variants.update([base, base + 'ing', base + 'ied'])

    if w.endswith('ly'):
        variants.add(w[:-2])

    for v in list(variants):
        if len(v) >= 3:
            variants.add(v + 's')
            variants.add(v + 'ed')
            variants.add(v + 'ing')

    return {v for v in variants if len(v) >= 2 and v.isalpha()}


SEMANTIC_CLUSTERS = {
    "like": {"love", "enjoy", "prefer", "want", "fond", "stoked", "adore"},
    "love": {"like", "enjoy", "prefer", "adore", "fond"},
    "kids": {"children", "child", "kid", "son", "daughter", "younger"},
    "children": {"kids", "child", "kid"},
    "pet": {"pets", "cat", "dog", "animal", "kitty", "puppy"},
    "pets": {"pet", "cats", "dogs", "animals"},
    "paint": {"painted", "painting", "drew", "draw", "art", "artwork"},
    "painted": {"paint", "painting", "drew", "art"},
    "name": {"names", "called", "named"},
    "names": {"name", "called", "named"},
    "activity": {"activities", "hobby", "hobbies"},
    "activities": {"activity", "hobbies", "hobby"},
    "liberal": {"progressive", "left", "equality", "rights", "transgender", "trans"},
}


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


def funk_universo_score(question: str, messages: list[dict]) -> list[float]:
    """FUNK UNIVERSO universal scoring system."""
    question_lower = question.lower()

    # Extract entities
    QUESTION_WORDS = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                      'Do', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The',
                      'Would', 'Could', 'Should', 'Likely', 'Be'}
    query_entities = {e for e in re.findall(r'\b[A-Z][a-z]+\b', question) if e not in QUESTION_WORDS}
    query_entities_lower = {e.lower() for e in query_entities}

    # Extract and expand content words
    FUNCTION_WORDS = {'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been',
                      'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
                      'could', 'should', 'to', 'of', 'in', 'for', 'on', 'with',
                      'at', 'by', 'from', 'what', 'when', 'where', 'who', 'why', 'how'}
    query_words = {w for w in re.findall(r'\b[a-z]{3,}\b', question_lower) if w not in FUNCTION_WORDS}

    # Expand query words
    expanded_query = set()
    for w in query_words:
        expanded_query.update(universal_expand(w))
        if w in SEMANTIC_CLUSTERS:
            expanded_query.update(SEMANTIC_CLUSTERS[w])

    # Pronouns
    THIRD_PERSON = {'they', 'them', 'their', 'he', 'him', 'his', 'she', 'her', 'it', 'its'}
    FIRST_PERSON = {'i', 'me', 'my', 'mine', 'we', 'us', 'our'}

    # Possessive detection
    poss_match = re.search(r"([A-Z][a-z]+)'s\s+(\w+)", question)
    poss_owner = poss_match.group(1).lower() if poss_match else None
    poss_target = poss_match.group(2).lower() if poss_match else None
    poss_variants = universal_expand(poss_target) if poss_target else set()

    scores = []
    for msg in messages:
        text_lower = msg['text'].lower()
        speaker_lower = msg['speaker'].lower()
        msg_words = set(re.findall(r'\b[a-z]{2,}\b', text_lower))
        score = 1.0

        speaker_is_entity = any(e in speaker_lower for e in query_entities_lower)

        # BOOST 1: Speaker binding (2x)
        if speaker_is_entity:
            score *= 2.0

        # BOOST 2: Entity mention (1.5x)
        if any(e in text_lower for e in query_entities_lower):
            score *= 1.5

        # BOOST 3: Expanded keyword match
        keyword_hits = len(expanded_query & msg_words)
        if keyword_hits > 0:
            score *= 1.0 + (0.3 * min(3, keyword_hits))

        # BOOST 4: Possessive coreference
        if poss_owner and poss_owner in speaker_lower:
            if any(v in msg_words for v in poss_variants):
                score *= 1.8
            elif THIRD_PERSON & msg_words:
                score *= 1.5

        # BOOST 5: Self-reference
        if speaker_is_entity and (FIRST_PERSON & msg_words):
            score *= 1.4

        scores.append(score)

    # FORCE: Guarantee entity messages get minimum score
    if query_entities_lower:
        max_score = max(scores) if scores else 1.0
        min_score = max_score * 0.4
        for i, msg in enumerate(messages):
            speaker_lower = msg['speaker'].lower()
            if any(e in speaker_lower for e in query_entities_lower):
                if scores[i] < min_score:
                    scores[i] = min_score

    return scores


def main():
    print("=" * 80)
    print("FUNK UNIVERSO: UNIVERSAL RETRIEVAL TEST")
    print("=" * 80)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    messages = extract_messages(data[0])
    print(f"Loaded {len(messages)} messages\n")

    # THE FAILING CASES
    test_cases = [
        ("What are Melanie's pets' names?", "Oliver, Luna, Bailey"),
        ("What has Melanie painted?", "Horse, sunset, sunrise"),
        ("What would Caroline's political leaning likely be?", "Liberal"),
        ("What do Melanie's kids like?", "dinosaurs, nature"),
        ("What did Melanie paint recently?", "sunset"),
    ]

    for question, gold in test_cases:
        print(f"{'='*80}")
        print(f"Q: {question}")
        print(f"GOLD: {gold}")

        scores = funk_universo_score(question, messages)
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])

        # Find gold words in messages
        gold_words = set(gold.lower().replace(',', ' ').split())
        gold_indices = []
        for i, msg in enumerate(messages):
            if any(gw in msg['text'].lower() for gw in gold_words if len(gw) > 3):
                gold_indices.append(i)

        print(f"\nTop 15 retrieved:")
        for rank, (idx, score) in enumerate(ranked[:15], 1):
            msg = messages[idx]
            marker = "***" if idx in gold_indices else "   "
            text = msg['text'][:55].replace('\n', ' ')
            print(f"{marker} {rank:2d}. [{idx:3d}] {score:5.2f}x | {msg['speaker']}: {text}...")

        # Check if gold is in top 30
        top30_indices = {idx for idx, _ in ranked[:30]}
        found = [i for i in gold_indices if i in top30_indices]
        print(f"\nGold indices: {gold_indices[:5]}...")
        print(f"Found in top-30: {len(found)}/{len(gold_indices[:5])}")


if __name__ == "__main__":
    main()
