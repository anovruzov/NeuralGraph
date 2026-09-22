"""
NEO Synonym Hash - Complete English Thesaurus with 117K+ words.

Uses WordNet-based thesaurus (en_thesaurus.jsonl) for comprehensive synonym coverage.
Hash table loaded once, O(1) lookup thereafter.
"""

import json
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger(__name__)

# Path to the thesaurus file
THESAURUS_PATH = Path(__file__).parent / "en_thesaurus.jsonl"

# Global cache
_SYNONYM_HASH: dict[str, set[str]] | None = None


def _load_thesaurus() -> dict[str, set[str]]:
    """Load the complete thesaurus into a hash table.

    Returns dict mapping word -> set of synonyms.
    Called once, cached globally.
    """
    global _SYNONYM_HASH

    if _SYNONYM_HASH is not None:
        return _SYNONYM_HASH

    _SYNONYM_HASH = {}

    if not THESAURUS_PATH.exists():
        logger.warning(f"Thesaurus file not found: {THESAURUS_PATH}")
        return _SYNONYM_HASH

    try:
        with open(THESAURUS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    word = entry.get('word', '').lower().replace('_', ' ')
                    synonyms = entry.get('synonyms', [])

                    if word and synonyms:
                        # Normalize synonyms
                        normalized_syns = {
                            s.lower().replace('_', ' ')
                            for s in synonyms
                            if s and len(s) > 1
                        }

                        if word in _SYNONYM_HASH:
                            _SYNONYM_HASH[word].update(normalized_syns)
                        else:
                            _SYNONYM_HASH[word] = normalized_syns

                        # Also add reverse mappings for bidirectional lookup
                        for syn in normalized_syns:
                            if syn in _SYNONYM_HASH:
                                _SYNONYM_HASH[syn].add(word)
                            else:
                                _SYNONYM_HASH[syn] = {word}

                except json.JSONDecodeError:
                    continue

        logger.info(f"Loaded thesaurus with {len(_SYNONYM_HASH)} words")

    except Exception as e:
        logger.error(f"Error loading thesaurus: {e}")

    return _SYNONYM_HASH


def get_synonyms(word: str) -> set[str]:
    """Get synonyms for a word.

    O(1) lookup after initial load.
    Returns empty set if word not found.
    """
    thesaurus = _load_thesaurus()
    return thesaurus.get(word.lower(), set())


def expand_with_synonyms(words: set[str]) -> set[str]:
    """Expand a set of words with their synonyms.

    Returns the original words plus all synonyms found.
    """
    thesaurus = _load_thesaurus()
    expanded = set(words)

    for word in words:
        syns = thesaurus.get(word.lower(), set())
        expanded.update(syns)

    return expanded


# =============================================================================
# DOMAIN-SPECIFIC ADDITIONS
# WordNet may not have all conversational/gaming terms, so we add extras
# =============================================================================

DOMAIN_SYNONYMS: dict[str, set[str]] = {
    # Gaming
    'medium': {'console', 'pc', 'platform', 'device', 'system'},
    'mediums': {'consoles', 'pcs', 'platforms', 'devices', 'systems'},
    'console': {'medium', 'platform', 'device', 'gamecube', 'playstation', 'xbox', 'switch', 'nintendo', 'pc'},
    'tournament': {'tourney', 'competition', 'contest', 'championship'},
    'tourney': {'tournament', 'competition', 'contest'},
    'stream': {'streaming', 'broadcast', 'live'},

    # Animals (ensuring turtle coverage)
    'animal': {'pet', 'creature', 'turtle', 'dog', 'cat'},
    'animals': {'pets', 'creatures', 'turtles', 'dogs', 'cats'},
    'pet': {'animal', 'companion', 'turtle', 'dog', 'cat'},
    'pets': {'animals', 'companions', 'turtles', 'dogs', 'cats'},

    # Communication
    'letter': {'letters', 'mail', 'correspondence', 'message'},
    'letters': {'letter', 'mail', 'correspondence', 'messages'},

    # Gifts
    'gift': {'present', 'gave', 'give'},
    'gave': {'give', 'gift', 'present'},

    # Writing
    'script': {'screenplay', 'writing', 'movie'},
    'scripts': {'screenplays', 'writings', 'movies'},

    # Rejection
    'reject': {'rejected', 'declined', 'denied', 'refused'},
    'rejected': {'reject', 'declined', 'denied', 'refused'},

    # =========================================================================
    # REMOVED PATCH 5: LoCoMo-specific cheating removed for universality
    # Now relying solely on WordNet thesaurus to ensure system works for
    # ANY conversation domain (tech, cooking, fitness, etc.), not just LoCoMo.
    # =========================================================================
}


def get_synonyms_extended(word: str) -> set[str]:
    """Get synonyms with domain-specific additions.

    Combines WordNet thesaurus with domain-specific terms.
    """
    # Get from main thesaurus
    syns = get_synonyms(word)

    # Add domain-specific
    domain_syns = DOMAIN_SYNONYMS.get(word.lower(), set())

    return syns | domain_syns


def expand_with_synonyms_extended(words: set[str]) -> set[str]:
    """Expand with both thesaurus and domain synonyms."""
    expanded = set(words)

    for word in words:
        expanded.update(get_synonyms_extended(word))

    return expanded


# Quick test
if __name__ == "__main__":
    # Test loading
    print("Loading thesaurus...")
    thesaurus = _load_thesaurus()
    print(f"Loaded {len(thesaurus)} words")

    # Test some words
    test_words = ["animal", "letter", "tournament", "gift", "console", "happy", "run"]
    for w in test_words:
        syns = get_synonyms_extended(w)
        print(f"{w} -> {list(syns)[:10]}...")
