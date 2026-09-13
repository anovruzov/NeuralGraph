"""Lexical preprocessing for the memory server: thesaurus, spelling, names.

Three deterministic, offline components, each switchable so its effect can be
measured on its own (see ``evaluation.py``):

- **Thesaurus** (``en_thesaurus.jsonl``, WordNet-derived, 87k words): a query
  token is expanded with synonyms, but only synonyms that actually occur in
  the session's memories.  Expanding into words no memory contains adds noise
  and nothing else; restricting to the corpus vocabulary is the precision
  guard.
- **Spelling detector** (Norvig-style edit distance over two vocabularies):
  a query token absent from both the corpus vocabulary and the dictionary is
  corrected to the closest corpus word first, dictionary word second.  Words
  the corpus has seen are never "corrected": a project's own jargon and names
  are valid by definition.  Stored memory text is never rewritten.
- **Name extraction**: capitalised runs ("Nurman Mahammadov", "New York"),
  handles, and capitalised words the dictionary does not know.  Names are
  kept separately from generic entities and weigh more at recall.

The thesaurus is loaded lazily on first use and shared per process.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

_ALNUM = re.compile(r"^[a-z0-9]+$")
_CAP_RUN = re.compile(r"\b([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)*)\b")
_HANDLE = re.compile(r"(?<![\w.])@([A-Za-z0-9_][A-Za-z0-9_.-]{1,38})")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"

COMMON_STARTERS = {
    "the", "what", "this", "these", "those", "after", "before", "use", "using", "add", "run", "note", "todo",
    "remember", "always", "never", "every", "each", "please", "when", "where", "while", "if", "it", "its", "we",
    "our", "you", "your", "they", "there", "here", "also", "make", "keep", "set", "do", "don", "let", "check",
    "yes", "no", "ok", "okay", "hi", "hello", "thanks", "and", "but", "for", "with", "from", "that", "then",
}


@lru_cache(maxsize=1)
def _thesaurus() -> dict[str, set[str]]:
    from ..synonym_hash import _load_thesaurus

    return _load_thesaurus()


@lru_cache(maxsize=1)
def dictionary() -> frozenset[str]:
    """Every single-token headword or synonym in the thesaurus file.

    Read from the file itself, not the synonym hash: the hash keeps only words
    that have synonyms, and a spelling dictionary needs the ones without
    ("deployment", "database") just as much.
    """
    import json

    from ..synonym_hash import THESAURUS_PATH

    words: set[str] = set()
    if THESAURUS_PATH.exists():
        with open(THESAURUS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                for w in [entry.get("word", "")] + list(entry.get("synonyms", [])):
                    w = (w or "").lower()
                    if _ALNUM.match(w):
                        words.add(w)
    return frozenset(words)


#: Software vocabulary WordNet lacks.  Curated, bidirectional, deliberately
#: small: every pair here is a real paraphrase an agent uses, not a guess.
SOFTWARE_SYNONYMS: dict[str, set[str]] = {
    "deploy": {"release", "ship", "rollout", "deployment"},
    "release": {"ship", "rollout"},
    "database": {"db", "datastore"},
    "postgres": {"db", "database"},
    "sqlite": {"db", "database"},
    "editor": {"ide"},
    "bug": {"defect", "issue", "regression", "glitch"},
    "error": {"exception", "failure", "fault"},
    "config": {"configuration", "settings", "setup"},
    "repo": {"repository", "codebase"},
    "secret": {"token", "credential", "credentials", "password"},
    "test": {"tests", "suite"},
    "merge": {"integrate", "land"},
    "fast": {"quick", "speedy"},
    "slow": {"latency", "lag"},
    "preference": {"prefers", "likes", "favourite", "favorite"},
    "decision": {"decided", "chose", "choice"},
    "meeting": {"standup", "sync"},
    "deadline": {"due", "cutoff"},
    "environment": {"env"},
    "prod": {"production"},
    "endpoint": {"api", "route"},
    "model": {"llm"},
    "container": {"docker", "image", "pod"},
    "pipeline": {"ci", "workflow", "actions"},
    "kubernetes": {"k8s", "orchestration"},
}


@lru_cache(maxsize=1)
def _software_bidirectional() -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    # Head word <-> each synonym only.  Members of one group are NOT linked to
    # each other: "staging" and "production" are both environments and are
    # not synonyms, and an earlier sibling link made them so.
    for word, syns in SOFTWARE_SYNONYMS.items():
        table.setdefault(word, set()).update(syns)
        for s in syns:
            table.setdefault(s, set()).add(word)
    return table


def synonyms(word: str, limit: int = 8) -> tuple[str, ...]:
    """Single-token synonyms: curated software table first, then WordNet."""
    w = word.lower()
    curated = sorted(_software_bidirectional().get(w, set()) - {w})
    wordnet = sorted(s for s in _thesaurus().get(w, ()) if _ALNUM.match(s) and s != w and s not in curated)
    return tuple(curated + wordnet)[:limit]


def expand(tokens: Iterable[str], corpus_vocab: set[str], per_token: int = 3) -> dict[str, tuple[str, ...]]:
    """Synonyms per query token, restricted to words some memory contains."""
    out: dict[str, tuple[str, ...]] = {}
    for token in dict.fromkeys(tokens):
        hits = tuple(s for s in synonyms(token, limit=32) if s in corpus_vocab)[:per_token]
        if hits:
            out[token] = hits
    return out


def _edits1(word: str) -> set[str]:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [a + b[1:] for a, b in splits if b]
    transposes = [a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1]
    replaces = [a + c + b[1:] for a, b in splits if b for c in _LETTERS]
    inserts = [a + c + b for a, b in splits for c in _LETTERS]
    return set(deletes + transposes + replaces + inserts)


def correct(token: str, corpus_vocab: dict[str, int] | set[str], protected: Iterable[str] = ()) -> tuple[str, bool]:
    """Return (corrected_token, changed).

    Never changes: numbers, tokens under 4 characters, capitalised tokens (a
    name is not a misspelling), protected tokens (names the session knows),
    tokens the corpus has seen, tokens the dictionary knows.  Otherwise the
    closest corpus word wins (most frequent among ties), then the closest
    dictionary word.  Edit distance 2 is tried only against the corpus and
    only for longer tokens.
    """
    t = token.lower()
    if len(t) < 4 or t.isdigit() or not _ALNUM.match(t) or token[:1].isupper() or t in set(protected):
        return token, False
    freq = corpus_vocab if isinstance(corpus_vocab, dict) else {w: 1 for w in corpus_vocab}
    if t in freq or t in dictionary():
        return token, False
    e1 = _edits1(t)
    corpus_hits = [w for w in e1 if w in freq]
    if corpus_hits:
        return max(corpus_hits, key=lambda w: (freq[w], -abs(len(w) - len(t)), w)), True
    if len(t) >= 6:
        e2 = {e for w in e1 for e in _edits1(w) if e in freq}
        if e2:
            return max(e2, key=lambda w: (freq[w], -abs(len(w) - len(t)), w)), True
    dict_hits = sorted(w for w in e1 if w in dictionary())
    if dict_hits:
        # prefer dictionary words with more synonyms: a rough commonness prior
        return max(dict_hits, key=lambda w: (len(_thesaurus().get(w, ())), -abs(len(w) - len(t)), w)), True
    return token, False


def correct_tokens(tokens: Iterable[str], corpus_vocab: dict[str, int] | set[str], protected: Iterable[str] = ()) -> tuple[list[str], dict[str, str]]:
    corrected, changes = [], {}
    protected = set(protected)
    for token in tokens:
        new, changed = correct(token, corpus_vocab, protected)
        corrected.append(new.lower())
        if changed:
            changes[token] = new
    return corrected, changes


def query_words(text: str) -> list[str]:
    """Original-case word tokens of a query, so capitalisation can protect names."""
    return re.findall(r"\w+", text, re.UNICODE)


def names(text: str, explicit: Iterable[str] = ()) -> tuple[str, ...]:
    """Proper names in ``text``: capitalised runs, handles, unknown capitalised words.

    A capitalised run of two or more words is a name ("Nurman Mahammadov").
    A single capitalised word is a name when it is not a common sentence
    starter and either sits mid-sentence or is unknown to the dictionary.
    Every name is lower-cased; multi-word names also contribute their parts.
    """
    found: set[str] = set()
    text = re.sub(r"(\w)'s\b", r"\1", text)  # possessives: "Nurman's" -> "Nurman"
    for run in _CAP_RUN.findall(text):
        words = run.split()
        lowered = [w.lower().strip("'-") for w in words]
        if len(words) >= 2:
            if all(w in COMMON_STARTERS for w in lowered):
                continue
            found.add(" ".join(lowered))
            # contiguous sub-runs of a longer run ("Nurman LM Studio" -> "lm studio")
            for size in range(2, len(lowered)):
                for i in range(len(lowered) - size + 1):
                    found.add(" ".join(lowered[i:i + size]))
            found.update(w for w in lowered if w not in COMMON_STARTERS and w not in dictionary())
            continue
        w = lowered[0]
        if w in COMMON_STARTERS or len(w) < 2:
            continue
        start = text.find(run)
        sentence_initial = start == 0 or text[:start].rstrip().endswith((".", "!", "?", "\n"))
        if not sentence_initial or w not in dictionary():
            found.add(w)
    found.update(h.lower().rstrip(".") for h in _HANDLE.findall(text))
    found.update(e.strip().lower() for e in explicit if e and e.strip())
    return tuple(sorted(found))


def sentence_initial_candidates(text: str) -> tuple[str, ...]:
    """Capitalised, non-starter words at sentence starts that the dictionary knows.

    They are not names on their own evidence; the engine promotes them at
    recall time when the same word is a confirmed name elsewhere in the session.
    """
    text = re.sub(r"(\w)'s\b", r"\1", text)
    out: set[str] = set()
    for run in _CAP_RUN.findall(text):
        words = run.split()
        if len(words) != 1:
            continue
        w = words[0].lower().strip("'-")
        start = text.find(run)
        sentence_initial = start == 0 or text[:start].rstrip().endswith((".", "!", "?", "\n"))
        if sentence_initial and w not in COMMON_STARTERS and len(w) >= 2 and w in dictionary():
            out.add(w)
    return tuple(sorted(out))
