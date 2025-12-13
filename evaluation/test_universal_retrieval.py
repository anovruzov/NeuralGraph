"""Test Universal Entity-Aware Retrieval (Bob's Algorithm).

Tests the universal single_hop retrieval on any conversation.
No hardcoded word lists - uses linguistic patterns.
"""

import json
import re
from pathlib import Path
from collections import Counter
import math

# Copy the universal algorithm inline for testing
FUNCTION_WORDS = {'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been',
                  'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                  'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                  'can', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
                  'from', 'as', 'into', 'through', 'during', 'before', 'after',
                  'above', 'below', 'between', 'under', 'again', 'further',
                  'then', 'once', 'here', 'there', 'when', 'where', 'why',
                  'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
                  'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
                  'than', 'too', 'very', 'just', 'also', 'now', 'what', 'who'}

QUESTION_WORDS = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Do', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The',
                  'And', 'But', 'Or', 'From', 'To', 'In', 'On', 'At', 'For', 'With'}

THIRD_PERSON_PRONOUNS = {'they', 'them', 'their', 'theirs', 'he', 'him', 'his',
                          'she', 'her', 'hers', 'it', 'its'}
FIRST_PERSON_PRONOUNS = {'i', 'me', 'my', 'mine', 'we', 'us', 'our', 'ours'}


def get_variants(word):
    """Generate morphological variants of a word."""
    variants = {word}
    if word.endswith('s') and len(word) > 3:
        variants.add(word[:-1])
    else:
        variants.add(word + 's')
    if word.endswith('ies'):
        variants.add(word[:-3] + 'y')
    elif word.endswith('y') and len(word) > 2:
        variants.add(word[:-1] + 'ies')
    variants.add(word + 'ing')
    variants.add(word + 'ed')
    if word.endswith('e'):
        variants.add(word[:-1] + 'ing')
    return variants


def compute_entity_boost(question: str, messages: list[dict]) -> list[float]:
    """Compute entity-aware boost for each message."""
    question_lower = question.lower()

    # Extract entities
    query_entities = {e for e in re.findall(r'\b[A-Z][a-z]+\b', question) if e not in QUESTION_WORDS}
    query_entities_lower = {e.lower() for e in query_entities}

    # Extract content words
    query_content_words = {w for w in re.findall(r'\b[a-z]{3,}\b', question_lower)
                           if w not in FUNCTION_WORDS}

    # Detect possessive
    possessive_match = re.search(r"([A-Z][a-z]+)'s\s+(\w+)", question)
    possessive_owner = possessive_match.group(1).lower() if possessive_match else None
    possessive_target = possessive_match.group(2).lower() if possessive_match else None
    possessive_variants = get_variants(possessive_target) if possessive_target else set()

    boosts = []
    for msg in messages:
        text_lower = msg['text'].lower()
        speaker_lower = msg['speaker'].lower()
        msg_words = set(re.findall(r'\b[a-z]{2,}\b', text_lower))
        boost = 1.0

        speaker_is_entity = any(e in speaker_lower for e in query_entities_lower)

        # BOOST 1: Entity binding
        entity_hits = sum(1 for e in query_entities_lower if e in text_lower)
        if entity_hits > 0:
            boost *= 1.0 + (0.5 * entity_hits / max(1, len(query_entities_lower)))

        # BOOST 2: Speaker binding
        if speaker_is_entity:
            boost *= 1.8

        # BOOST 3: Content word overlap
        content_overlap = len(query_content_words & msg_words)
        if content_overlap > 0:
            boost *= 1.0 + (0.4 * content_overlap / max(1, len(query_content_words)))

        # BOOST 4: Possessive coreference
        if possessive_owner and possessive_target:
            if possessive_owner in speaker_lower:
                has_target = any(v in msg_words for v in possessive_variants)
                has_pronoun = bool(THIRD_PERSON_PRONOUNS & msg_words)
                if has_target:
                    boost *= 1.6
                elif has_pronoun:
                    boost *= 1.4

        # BOOST 5: Self-reference
        if speaker_is_entity and (FIRST_PERSON_PRONOUNS & msg_words):
            boost *= 1.3

        # BOOST 6: Verb overlap
        VERB_PATTERNS = r'\b(\w+(?:ing|ed|es|en|s))\b'
        question_verbs = set(re.findall(VERB_PATTERNS, question_lower)) - FUNCTION_WORDS
        msg_verbs = set(re.findall(VERB_PATTERNS, text_lower)) - FUNCTION_WORDS
        verb_overlap = len(question_verbs & msg_verbs)
        if verb_overlap > 0:
            boost *= 1.0 + (0.3 * verb_overlap / max(1, len(question_verbs)))

        boosts.append(boost)

    return boosts


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
    print("UNIVERSAL ENTITY-AWARE RETRIEVAL TEST")
    print("=" * 80)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    messages = extract_messages(data[0])
    print(f"Loaded {len(messages)} messages")

    # Test cases - these were failing before
    test_cases = [
        ("What do Melanie's kids like?", "dinosaurs, nature", [65, 97]),
        ("What did Melanie paint recently?", "sunset", [365]),
        ("What are Melanie's pets' names?", "Oliver, Luna, Bailey", [125, 256]),
        ("Where did Caroline move from 4 years ago?", "Sweden", []),  # multi-hop
        ("What activities does Melanie partake in?", "pottery, camping, painting, swimming", []),
    ]

    for question, gold, target_indices in test_cases:
        print(f"\n{'='*80}")
        print(f"Q: {question}")
        print(f"Gold: {gold}")

        # Compute boosts
        boosts = compute_entity_boost(question, messages)

        # Get top 10 by boost
        ranked = sorted(enumerate(boosts), key=lambda x: -x[1])

        print(f"\nTop 10 by entity boost:")
        for rank, (idx, boost) in enumerate(ranked[:10], 1):
            msg = messages[idx]
            text_preview = msg['text'][:60].replace('\n', ' ')
            marker = "*" if idx in target_indices else " "
            print(f"  {marker}{rank}. [{idx}] {boost:.2f}x {msg['speaker']}: {text_preview}...")

        # Show where gold targets ranked
        if target_indices:
            print(f"\nTarget message rankings:")
            for target_idx in target_indices:
                rank = next((r+1 for r, (i, _) in enumerate(ranked) if i == target_idx), 999)
                print(f"  [{target_idx}] rank={rank} boost={boosts[target_idx]:.2f}x")
                print(f"      {messages[target_idx]['speaker']}: {messages[target_idx]['text'][:80]}...")


if __name__ == "__main__":
    main()
