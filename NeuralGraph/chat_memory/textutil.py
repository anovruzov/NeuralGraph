"""Small, dependency-free text helpers shared by extraction, storage and retrieval."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_STOP = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "with",
    "that", "this", "have", "from", "they", "what", "when", "where", "which", "who", "how", "did", "does", "has",
    "his", "she", "him", "them", "been", "were", "will", "would", "could", "should", "about", "into", "your",
    "just", "like", "some", "than", "then", "there", "their", "also", "very", "really", "much", "many", "more",
    "yeah", "yes", "hey", "thanks", "thank", "sounds", "sure", "good", "great", "awesome", "love", "know",
    "its", "it's", "i'm", "don't", "can't", "get", "got", "let", "lets", "let's", "okay", "well", "still",
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_WS_RE = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


_VOWELS = set("aeiou")


def _cvc_short(w: str) -> bool:
    return len(w) == 3 and w[0] not in _VOWELS and w[1] in _VOWELS and w[2] not in _VOWELS and w[2] not in "wxy"


def _after_strip(w: str) -> str:
    """Porter step-1b clean-up after removing -ed / -ing."""
    if w.endswith(("at", "bl", "iz")):
        return w + "e"
    if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "lsz" and w[-1] not in _VOWELS:
        return w[:-1]
    if _cvc_short(w):
        return w + "e"
    return w


def stem(word: str) -> str:
    """Small Porter-style stemmer (lives -> live, moved -> move, running -> run, cities -> city).
    The SQLite FTS5 index uses the real Porter stemmer; this keeps the in-process paths roughly aligned."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing") and any(ch in _VOWELS for ch in w[:-3]):
        return _after_strip(w[:-3])
    if len(w) > 4 and w.endswith("ed") and any(ch in _VOWELS for ch in w[:-2]):
        return _after_strip(w[:-2])
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxzh":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokenize(text: str, *, do_stem: bool = True) -> list[str]:
    """Lower-cased content tokens (>= 3 chars, stop-words removed, lightly stemmed). Used for BM25."""
    toks = _TOKEN_RE.findall((text or "").lower())
    out = []
    for t in toks:
        t = t.strip("'-")
        if len(t) < 3 or t in _STOP:
            continue
        out.append(stem(t) if do_stem else t)
    return out


def content_hash(*parts: str) -> str:
    """Stable 20-hex-char digest of the given strings (idempotency keys)."""
    h = hashlib.blake2b(digest_size=10)
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def norm_entity(name: str) -> str:
    """Canonical entity key: NFKC, lower, alnum/space/'/-, single spaces, possessive stripped."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9 '\-]", " ", s)
    s = normalize_ws(s)
    if s.endswith("'s"):
        s = s[:-2].strip()
    s = s.strip("'- ")
    if s.startswith("the "):
        s = s[4:]
    return s


def norm_relation(rel: str) -> str:
    """Canonical relation key: lower snake_case verb phrase."""
    s = re.sub(r"[^a-z0-9]+", "_", str(rel or "").lower()).strip("_")
    return s[:60]


# ---------------------------------------------------------------------------------------------
# Cheap "is this worth an LLM call?" gate. Conservative: it only skips text that is almost
# certainly chit-chat; everything else goes to Qwen, which makes the final call.
# ---------------------------------------------------------------------------------------------

_CHITCHAT_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|k|sure|yes|yeah|yep|nope?|no|thanks?|thank you|thx|ty|cool|nice|great|awesome|perfect|"
    r"lol|haha+|hmm+|hi|hello|hey|yo|good (?:morning|night|evening|afternoon)|bye|see you|cheers|"
    r"sounds good|got it|will do|makes sense|understood|noted|np|no problem|you're welcome|welcome|"
    r"same here|me too|right|exactly|true|indeed|alright|fine|good luck|take care|good to see you|"
    r"how are you|how have you been|what's up|whats up|long time no see)[\s!.,?:)]*)+$",
    re.IGNORECASE,
)
_ASSISTANT_BOILERPLATE_RE = re.compile(
    r"^(?:(?:sure|certainly|of course|absolutely|great question|happy to help|you're welcome|"
    r"let me know if (?:you|there)|is there anything else|i hope (?:this|that) helps|glad (?:i|to)|"
    r"feel free to ask)[^\n]{0,80})$",
    re.IGNORECASE,
)
_INFO_HINT_RE = re.compile(
    r"\d|\b(?:i|my|we|our|he|she|they|his|her|their|me)\b|[A-Z][a-z]+", re.IGNORECASE
)


def is_probable_chitchat(text: str, *, min_chars: int = 12) -> bool:
    """True when the message is almost certainly not worth remembering.

    Rules (all conservative):
    * empty / whitespace
    * shorter than ``min_chars`` AND carries no digit, pronoun or capitalised token
    * matches the greeting / acknowledgement pattern entirely
    * assistant boilerplate one-liners ("Sure! Let me know if ...")
    """
    t = normalize_ws(text)
    if not t:
        return True
    if _CHITCHAT_RE.match(t):
        return True
    if len(t) < min_chars and not _INFO_HINT_RE.search(t):
        return True
    if len(t) < 120 and _ASSISTANT_BOILERPLATE_RE.match(t):
        return True
    return False


def clip(text: str, limit: int) -> str:
    t = text or ""
    return t if len(t) <= limit else t[: max(0, limit - 1)].rstrip() + "…"
