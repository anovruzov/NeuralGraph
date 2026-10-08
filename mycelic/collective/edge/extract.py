"""The sense step inside a site: records become typed claims through two channels.

* **Channel S (codes, no model).** :func:`codes_channel` maps each structured code to its predicate and resolves each
  structured entity value with the canonicaliser (``resolve_exact``); the primary entity is the first resolved
  value of the mapping's ``primary_entity_type``.
* **In-boundary extraction from the narrative.** :class:`LexicalExtractor` (lexicon, negation cues, sentence
  pairing) or :class:`ModelExtractor` (a model behind the runtime, then deterministic post-processing). Both return
  :class:`TextClaim` s: ``(entity, predicate, negated)``, or an entity-only mention with predicate None.

Lexical semantics, shared by the lexical extractor and :func:`lexical_handler` (the fake model's handler):

* the lexicon and negation cues of the record's language; the union of all pack languages when the language is
  None; none when the language is not a pack language (``language_supported`` is then False);
* sentences split after ``[.!?;]`` followed by whitespace or the end, and at newlines;
* entity mentions from the canonicaliser; predicate mentions from a word-bounded, leftmost-longest lexicon regex
  over folded text; a boundary is required only at a term edge that is alphanumeric;
* negation applies to predicates only: a predicate is negated when a pre cue ends within ``negation_window``
  tokens (``\\w+`` runs of the folded sentence) before it, or a post cue starts within the window after it, with no
  terminator in between, all inside the sentence;
* pairing per sentence: each predicate with each entity mention of its sentence; a predicate in a sentence without
  entities attaches to the primary structured entity, or is dropped as ``no_entity``; an entity in a sentence
  without predicates becomes an entity-only claim. Pairs are formed per distinct entity and distinct
  (predicate, negated) with their multiplicities, so the work is linear in the narrative with a factor bounded by
  the pack (at most two per predicate), and the duplicate counts equal those of pairing every mention;
* after pairing, a claim whose entity span folds equal to a person value or the reporter is dropped as
  ``person_value`` (so a person-valued mention never causes re-attachment), then duplicates are dropped, keeping
  the maximum ``res_conf``.

The model sees exactly ``{language, text}`` (:func:`model_payload`): the narrative, truncated at a whitespace
boundary to ``max_input_chars``; never persons, the reporter, the record ref or structured values. Each reply item is
post-processed in order, and the first failing check is its drop reason: all-null (``empty``); only one of
entity_type and entity_text (``not_canonical``); entity_text not found word-bounded in the folded text that was sent
(``ungrounded``); entity_text equal to a person value (``person_value``); entity_text that does not canonicalise
(``not_canonical``); an id the canonicaliser does not read at any place where entity_text occurs in the sent text,
such as 'SD-9' taken from 'SD-9-B', which the scanner rejects outright (``ungrounded``); a predicate-only item
without a primary entity (``no_entity``); a repeat (``duplicate``). The claim's res_conf is that of the text's own
occurrence (written exactly as entity_text, else the best-written one), not of the model's spelling. An
out-of-enum type or predicate never reaches post-processing: the schema carries the enums and the runtime validates
replies locally, so it is ``schema_invalid`` (one repair, then the lexical fallback).

:func:`pair` turns the channels into claims for detection: S is ``pair(codes, None)``, X is ``pair(codes, text)``.
Negated claims are never output. Everything here is a pure function of (record, pack, canonicaliser, runtime);
nothing is stored.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from itertools import islice
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence

from ..inference.errors import InferenceBoundaryError, InferenceError
from ..inference.tasks import TaskSpec
from ..packs.canonical import (Canonicaliser, Mention, ScanResult, find_bounded, fold_phrase, folded,
                               split_sentences, term_regex)

if TYPE_CHECKING:
    from ..inference.runtime import Runtime
    from ..packs.loader import FrozenPack

TASK_NAME = "extract_claims"
DROP_REASONS = ("empty", "not_canonical", "ungrounded", "person_value", "duplicate", "no_entity")
ENTITY_TEXT_MAX = 64
TRUNCATE_BACKOFF = 200
CODES_EXTRACTOR = "codes"
LEXICAL_EXTRACTOR = "lexical"
FALLBACK_EXTRACTOR = "fallback"
# A pass over records (one extraction pass, one question's judging) stops calling the model server after this many
# consecutive failures of these kinds on the call's primary endpoint, each already after the client's own retries:
# the server is down, and every further record would wait out the same deadline (audit round 2). A failure on the
# escalation endpoint is not counted: the primary answered (its replies failed validation), so it is up (audit
# round 3; :func:`server_down`)
SERVER_DOWN_KINDS = ("timeout", "network", "http_5xx")
BREAKER_AFTER = 2
NOT_SENT = "not_sent"
_TOKEN = re.compile(r"\w+")


@dataclass(frozen=True)
class EntityRef:
    entity_type: str
    entity_id: str
    res_conf: float


@dataclass(frozen=True)
class CodesResult:
    predicates: tuple[str, ...]
    entities: tuple[EntityRef, ...]
    primary: EntityRef | None
    unknown_codes: int
    structured_unresolved: int


@dataclass(frozen=True)
class TextClaim:
    entity_type: str
    entity_id: str
    predicate: str | None
    negated: bool
    res_conf: float


@dataclass(frozen=True)
class ExtractionResult:
    extractor: str
    claims: tuple[TextClaim, ...]
    drops: Mapping[str, int]
    unresolved: Mapping[str, int]
    truncated: bool
    error_kind: str | None
    language_supported: bool
    server_down: bool = False            # the call's primary endpoint failed as down (:func:`server_down`)


@dataclass(frozen=True)
class Claim:
    entity_type: str
    entity_id: str
    predicate: str
    channel: str
    extractor: str
    res_conf: float


# --------------------------------------------------------------------------------------------------- channel S

def codes_channel(record: Mapping[str, Any], pack: "FrozenPack", canonicaliser: Canonicaliser) -> CodesResult:
    predicates: set[str] = set()
    unknown = 0
    for code in record["codes"]:
        found = pack.codes.get(code)
        if found is None:
            unknown += 1
        else:
            predicates.add(found.predicate)
    primary_type = pack.mapping()["primary_entity_type"]
    entities: dict[tuple[str, str], float] = {}
    primary = None
    unresolved = 0
    for t in sorted(record["entities"]):
        for value in record["entities"][t]:
            m = canonicaliser.resolve_exact(t, value)
            if m is None:
                unresolved += 1
                continue
            key = (t, m.entity_id)
            entities[key] = max(entities.get(key, 0.0), m.res_conf)
            if t == primary_type and primary is None:
                primary = EntityRef(t, m.entity_id, m.res_conf)
    return CodesResult(predicates=tuple(sorted(predicates)),
                       entities=tuple(EntityRef(t, i, c) for (t, i), c in sorted(entities.items())),
                       primary=primary, unknown_codes=unknown, structured_unresolved=unresolved)


# --------------------------------------------------------------------------------------------------- lexical analysis

@dataclass(frozen=True)
class _Lexicon:
    predicates: re.Pattern[str] | None
    predicate_of: Mapping[str, str]
    pre: re.Pattern[str] | None
    post: re.Pattern[str] | None
    terminators: re.Pattern[str] | None


@dataclass(frozen=True)
class _Sentence:
    mentions: tuple[Mention, ...]
    predicates: tuple[tuple[str, bool], ...]


def _spans(pattern: re.Pattern[str] | None, text: str) -> list[tuple[int, int]]:
    return [] if pattern is None else [m.span() for m in pattern.finditer(text)]


class _Analyser:
    """Sentence split, mentions, predicates and negation for one pack; lexicons are built per language set."""

    def __init__(self, pack: "FrozenPack", canonicaliser: Canonicaliser) -> None:
        self.pack = pack
        self.canonicaliser = canonicaliser
        self._cache: dict[tuple[str, ...], _Lexicon] = {}

    def languages(self, language: Any) -> tuple[tuple[str, ...], bool]:
        if language is None:
            return self.pack.languages, True
        if language in self.pack.languages:
            return (language,), True
        return (), False

    def lexicon(self, languages: tuple[str, ...]) -> _Lexicon:
        if languages not in self._cache:
            predicate_of: dict[str, str] = {}
            cues: dict[str, set[str]] = {"pre": set(), "post": set(), "terminators": set()}
            for lang in languages:                      # pack order: on a clash across languages the first wins
                for p in sorted(self.pack.predicates):
                    for term in self.pack.predicates[p].lexicon.get(lang, ()):
                        predicate_of.setdefault(folded(term), p)
                neg = self.pack.negation[lang]
                cues["pre"].update(folded(t) for t in neg.pre)
                cues["post"].update(folded(t) for t in neg.post)
                cues["terminators"].update(folded(t) for t in neg.terminators)
            self._cache[languages] = _Lexicon(predicates=term_regex(predicate_of),
                                              predicate_of=MappingProxyType(predicate_of),
                                              pre=term_regex(cues["pre"]), post=term_regex(cues["post"]),
                                              terminators=term_regex(cues["terminators"]))
        return self._cache[languages]

    def _negated(self, span: tuple[int, int], tokens: tuple[list[int], list[int]], pre_ends: list[int],
                 post_starts: list[int], stops: list[int]) -> bool:
        """Only the nearest cue on each side matters: a farther one has at least as many tokens and every
        terminator of the nearer one in between."""
        starts, ends = tokens
        window = self.pack.negation_window
        ps, pe = span

        def gap(a: int, b: int) -> int:
            return max(0, bisect_right(ends, b) - bisect_left(starts, a))

        def blocked(a: int, b: int) -> bool:
            i = bisect_left(stops, a)
            return i < len(stops) and stops[i] < b

        i = bisect_right(pre_ends, ps) - 1
        if i >= 0 and gap(pre_ends[i], ps) < window and not blocked(pre_ends[i], ps):
            return True
        j = bisect_left(post_starts, pe)
        return j < len(post_starts) and gap(pe, post_starts[j]) < window and not blocked(pe, post_starts[j])

    def analyse(self, text: str, language: Any) -> tuple[list[_Sentence], Mapping[str, int], bool]:
        languages, supported = self.languages(language)
        lex = self.lexicon(languages)
        scan = self.canonicaliser.scan(text)
        starts_at = [m.start for m in scan.mentions]
        sentences = []
        for s, e in split_sentences(text):
            mentions = scan.mentions[bisect_left(starts_at, s):bisect_left(starts_at, e)]
            predicates = []
            if lex.predicates is not None:
                sent, _ = fold_phrase(text[s:e])
                tokens = [m.span() for m in _TOKEN.finditer(sent)]
                starts, ends = [a for a, _ in tokens], [b for _, b in tokens]
                pre_ends = [b for _, b in _spans(lex.pre, sent)]
                post_starts = [a for a, _ in _spans(lex.post, sent)]
                stops = [a for a, _ in _spans(lex.terminators, sent)]
                for pm in lex.predicates.finditer(sent):
                    negated = self._negated(pm.span(), (starts, ends), pre_ends, post_starts, stops)
                    predicates.append((lex.predicate_of[pm.group()], negated))
            sentences.append(_Sentence(mentions=mentions, predicates=tuple(predicates)))
        return sentences, scan.unresolved, supported


def person_values(record: Mapping[str, Any]) -> frozenset[str]:
    """The record's person values and its reporter, folded: a mention written as one of them is person data."""
    values = [v for v in record["persons"].values() if isinstance(v, str)]
    if isinstance(record["reporter"], str):
        values.append(record["reporter"])
    return frozenset(f for f in (folded(v) for v in values) if f)


class _Claims:
    """Collects text claims: duplicates are dropped (keeping the maximum res_conf) and counted."""

    def __init__(self) -> None:
        self.conf: dict[tuple[str, str, str | None, bool], float] = {}
        self.drops = dict.fromkeys(DROP_REASONS, 0)

    def add(self, entity_type: str, entity_id: str, predicate: str | None, negated: bool, res_conf: float,
            times: int = 1) -> None:
        """Adds the claim ``times`` times: every addition after the first is a counted duplicate."""
        key = (entity_type, entity_id, predicate, bool(negated) and predicate is not None)
        if key in self.conf:
            self.drops["duplicate"] += times
            self.conf[key] = max(self.conf[key], res_conf)
        else:
            self.drops["duplicate"] += times - 1
            self.conf[key] = res_conf

    def result(self, extractor: str, unresolved: Mapping[str, int], *, truncated: bool, error_kind: str | None,
               language_supported: bool) -> ExtractionResult:
        keys = sorted(self.conf, key=lambda k: (k[0], k[1], k[2] or "", k[3]))
        claims = tuple(TextClaim(t, i, p, n, self.conf[(t, i, p, n)]) for t, i, p, n in keys)
        return ExtractionResult(extractor=extractor, claims=claims, drops=MappingProxyType(dict(self.drops)),
                                unresolved=MappingProxyType(dict(unresolved)), truncated=truncated,
                                error_kind=error_kind, language_supported=language_supported)


def _tally(items: Sequence[tuple[Any, float]]) -> dict[Any, list[float | int]]:
    """``{key: [max res_conf, count]}`` over ``(key, res_conf)`` pairs, in first-seen order."""
    out: dict[Any, list[float | int]] = {}
    for key, conf in items:
        if key in out:
            out[key][0] = max(out[key][0], conf)
            out[key][1] += 1
        else:
            out[key] = [conf, 1]
    return out


class LexicalExtractor:
    def __init__(self, pack: "FrozenPack", canonicaliser: Canonicaliser) -> None:
        self.pack = pack
        self.analyser = _Analyser(pack, canonicaliser)

    def extract(self, record: Mapping[str, Any], codes: CodesResult) -> ExtractionResult:
        """Linear in the narrative: within a sentence, P predicate mentions and M entity mentions make P x M pairs,
        but they are added per distinct entity and distinct (predicate, negated) with their multiplicities, so the
        work is bounded by the distinct pairs and the drop counts equal those of pairing every mention."""
        sentences, unresolved, supported = self.analyser.analyse(record["narrative"], record["language"])
        out = _Claims()
        persons = person_values(record)
        for sentence in sentences:
            kept = [m for m in sentence.mentions if folded(m.text) not in persons]
            person_mentions = len(sentence.mentions) - len(kept)
            entities = _tally([((m.entity_type, m.entity_id), m.res_conf) for m in kept])
            if not sentence.predicates:
                out.drops["person_value"] += person_mentions
                for (t, i), (conf, n) in entities.items():
                    out.add(t, i, None, False, conf, n)
                continue
            predicates = _tally([(p, 0.0) for p in sentence.predicates])
            if sentence.mentions:        # a person-valued mention still takes the predicate: no re-attachment
                out.drops["person_value"] += person_mentions * len(sentence.predicates)
                for (t, i), (conf, n) in entities.items():
                    for (predicate, negated), (_, k) in predicates.items():
                        out.add(t, i, predicate, negated, conf, n * k)
            elif codes.primary is not None:
                p = codes.primary
                for (predicate, negated), (_, k) in predicates.items():
                    out.add(p.entity_type, p.entity_id, predicate, negated, p.res_conf, k)
            else:
                out.drops["no_entity"] += len(sentence.predicates)
        return out.result(LEXICAL_EXTRACTOR, unresolved, truncated=False, error_kind=None,
                          language_supported=supported)


def lexical_handler(pack: "FrozenPack", canonicaliser: Canonicaliser) -> Callable[[Mapping[str, Any]],
                                                                                   dict[str, Any]]:
    """The FakeProvider handler: the lexical analysis of the payload, emitted as model reply items with the original
    span text. It sees no persons (they are never sent) and truncates nothing; post-processing then reproduces the
    lexical claims exactly, as long as the items fit ``max_claims``. Items are generated lazily and stop at
    ``max_claims``, so a sentence with many mentions and many predicates costs no more than the cap."""
    analyser = _Analyser(pack, canonicaliser)

    def items(sentences: Sequence[_Sentence]) -> Iterator[dict[str, Any]]:
        for sentence in sentences:
            if not sentence.predicates:
                for m in sentence.mentions:
                    yield {"entity_type": m.entity_type, "entity_text": m.text, "predicate": None, "negated": False}
                continue
            for predicate, negated in sentence.predicates:
                if not sentence.mentions:
                    yield {"entity_type": None, "entity_text": None, "predicate": predicate, "negated": negated}
                for m in sentence.mentions:
                    yield {"entity_type": m.entity_type, "entity_text": m.text, "predicate": predicate,
                           "negated": negated}

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        sentences, _, _ = analyser.analyse(payload["text"], payload["language"])
        return {"claims": list(islice(items(sentences), pack.extraction.max_claims))}

    return handler


# --------------------------------------------------------------------------------------------------- the model channel

def extraction_task(pack: "FrozenPack") -> TaskSpec:
    types = "; ".join(f"{t} ({pack.entity_types[t].label})" for t in sorted(pack.entity_types))
    predicates = "; ".join(f"{p} ({pack.predicates[p].label})" for p in sorted(pack.predicates))
    instructions = (
        "Extract claims from the narrative of one record. Entity types: " + types + ". Predicates: " + predicates
        + ". Give one item per entity and predicate stated together in a sentence. Copy entity_text exactly as it "
        "is written in the text. When a predicate names no listed entity, use null for both entity_type and "
        "entity_text. Use a null predicate for an entity that is mentioned without a predicate. Set negated to true "
        "when the text says the event did not happen, otherwise false. Use only the listed entity types and "
        "predicates."
    )
    return TaskSpec(TASK_NAME, "raw", instructions, pack.extraction.max_output_tokens)


def extraction_schema(pack: "FrozenPack") -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False,
            "required": ["entity_text", "entity_type", "negated", "predicate"],
            "properties": {
                "entity_type": {"type": ["string", "null"], "enum": [*sorted(pack.entity_types), None]},
                "entity_text": {"type": ["string", "null"], "maxLength": ENTITY_TEXT_MAX},
                "predicate": {"type": ["string", "null"], "enum": [*sorted(pack.predicates), None]},
                "negated": {"type": "boolean"}}}
    return {"type": "object", "additionalProperties": False, "required": ["claims"],
            "properties": {"claims": {"type": "array", "maxItems": pack.extraction.max_claims, "items": item}}}


def truncate(text: str, cap: int) -> tuple[str, bool]:
    """``text`` cut to at most ``cap`` characters at a whitespace boundary: if ``text[cap]`` is not whitespace, back
    off to the last whitespace in the final 200 characters, else cut hard."""
    if len(text) <= cap:
        return text, False
    if text[cap].isspace():
        return text[:cap], True
    for i in range(cap - 1, max(cap - TRUNCATE_BACKOFF, 0) - 1, -1):
        if text[i].isspace():
            return text[:i], True
    return text[:cap], True


def server_down(err: InferenceError, runtime: "Runtime", task: str, endpoint: str | None = None) -> bool:
    """The failure says the server first asked is down: a :data:`SERVER_DOWN_KINDS` kind on the call's primary
    endpoint (``endpoint`` when given, else the task's route). ``err.kind`` is the last attempt's, and the runtime
    escalates only after the primary's replies failed validation twice, so a timeout, network error or 5xx on the
    escalation endpoint means the primary is up and is not counted (audit round 3)."""
    if err.kind not in SERVER_DOWN_KINDS:
        return False
    if endpoint is None:
        route = runtime.config.routes.get(task)
        endpoint = route.endpoint if route is not None else None
    return err.endpoint == endpoint


def model_payload(record: Mapping[str, Any], pack: "FrozenPack") -> tuple[dict[str, Any], bool]:
    text, truncated = truncate(record["narrative"], pack.extraction.max_input_chars)
    return {"language": record["language"], "text": text}, truncated


class ModelExtractor:
    def __init__(self, pack: "FrozenPack", canonicaliser: Canonicaliser, runtime: "Runtime", *,
                 endpoint: str | None = None, fallback: bool = True) -> None:
        self.pack = pack
        self.canonicaliser = canonicaliser
        self.runtime = runtime
        self.endpoint = endpoint
        self.fallback = fallback
        self.task = extraction_task(pack)
        self.schema = extraction_schema(pack)
        self.lexical = LexicalExtractor(pack, canonicaliser)

    @property
    def name(self) -> str:
        route = self.runtime.config.routes.get(TASK_NAME)
        endpoint = self.endpoint if self.endpoint is not None else (route.endpoint if route is not None else "")
        return f"model:{endpoint}"

    def extract(self, record: Mapping[str, Any], codes: CodesResult, *, ref: str) -> ExtractionResult:
        payload, truncated = model_payload(record, self.pack)
        _, supported = self.lexical.analyser.languages(record["language"])
        scan = self.canonicaliser.scan(payload["text"])
        reply = kind = None
        down = False
        try:
            reply = self.runtime.run(self.task, payload, self.schema, ref=ref, endpoint=self.endpoint)
        except InferenceBoundaryError:
            raise
        except InferenceError as err:
            kind = err.kind
            down = server_down(err, self.runtime, TASK_NAME, self.endpoint)
        if reply is None:
            if self.fallback:
                return replace(self.lexical.extract(record, codes), extractor=FALLBACK_EXTRACTOR, error_kind=kind,
                               server_down=down)
            return replace(_Claims().result(self.name, scan.unresolved, truncated=truncated, error_kind=kind,
                                            language_supported=supported), server_down=down)
        return self.postprocess(reply["claims"], record, codes, payload["text"], scan, truncated=truncated,
                                language_supported=supported)

    def postprocess(self, items: Sequence[Mapping[str, Any]], record: Mapping[str, Any], codes: CodesResult,
                    sent_text: str, scan: ScanResult, *, truncated: bool,
                    language_supported: bool) -> ExtractionResult:
        """``scan`` is the canonicaliser's scan of ``sent_text``. An entity is grounded only where the canonicaliser
        reads that id in the sent text: a mention of the item's type and resolved id whose span folds equal to
        entity_text. So a model that trims 'SD-9-B' to 'SD-9' is ungrounded, as the scanner rejects 'SD-9-B'
        outright. res_conf is that of the occurrence written exactly as entity_text, else the best-written one."""
        out = _Claims()
        sent = fold_phrase(sent_text)[0]
        persons = person_values(record)
        occurrences: dict[tuple[str, str, str], dict[str, float]] = {}
        for m in scan.mentions:
            occurrences.setdefault((m.entity_type, m.entity_id, folded(m.text)), {})[m.text] = m.res_conf
        for item in items:
            entity_type, text, predicate = item["entity_type"], item["entity_text"], item["predicate"]
            if entity_type is None and text is None and predicate is None:
                out.drops["empty"] += 1
                continue
            if (entity_type is None) != (text is None):
                out.drops["not_canonical"] += 1
                continue
            if text is not None:
                needle = folded(text)
                if not find_bounded(needle, sent):
                    out.drops["ungrounded"] += 1
                    continue
                if needle in persons:
                    out.drops["person_value"] += 1
                    continue
                m = self.canonicaliser.resolve_exact(entity_type, text)
                if m is None:
                    out.drops["not_canonical"] += 1
                    continue
                found = occurrences.get((entity_type, m.entity_id, needle))
                if found is None:
                    out.drops["ungrounded"] += 1
                    continue
                conf = found.get(text.strip(), max(found.values()))
                entity = EntityRef(entity_type, m.entity_id, conf)
            elif codes.primary is None:
                out.drops["no_entity"] += 1
                continue
            else:
                entity = codes.primary
            out.add(entity.entity_type, entity.entity_id, predicate, item["negated"] and predicate is not None,
                    entity.res_conf)
        return out.result(self.name, scan.unresolved, truncated=truncated, error_kind=None,
                          language_supported=language_supported)


# --------------------------------------------------------------------------------------------------- pairing

def pair(codes: CodesResult, text: ExtractionResult | None = None) -> tuple[Claim, ...]:
    """Claims for detection. C_codes = structured entities x code predicates (channel ``codes``). With text,
    C_text = the extractor's own non-negated pairs, plus non-negated text predicates x structured entities, plus code
    predicates x entities named only in the text; a pair the text states only as negated is left out. ``text_only``
    = C_text minus C_codes. Each (entity, predicate) appears once; negated claims never appear; sorted."""
    structured = {(e.entity_type, e.entity_id): e.res_conf for e in codes.entities}
    out: dict[tuple[str, str, str], Claim] = {}
    for (t, i), conf in sorted(structured.items()):
        for p in codes.predicates:
            out[(t, i, p)] = Claim(t, i, p, "codes", CODES_EXTRACTOR, conf)
    if text is not None:
        text_conf: dict[tuple[str, str], float] = {}
        for c in text.claims:
            key = (c.entity_type, c.entity_id)
            text_conf[key] = max(text_conf.get(key, 0.0), c.res_conf)
        affirmed = {(c.entity_type, c.entity_id, c.predicate) for c in text.claims
                    if c.predicate is not None and not c.negated}
        negated = {(c.entity_type, c.entity_id, c.predicate) for c in text.claims
                   if c.predicate is not None and c.negated} - affirmed
        text_predicates = {p for _, _, p in affirmed}
        candidates = set(affirmed)
        candidates |= {(t, i, p) for p in text_predicates for (t, i) in structured}
        candidates |= {(t, i, p) for p in codes.predicates for (t, i) in text_conf if (t, i) not in structured}
        for t, i, p in sorted(candidates - negated):
            if (t, i, p) not in out:
                conf = max(v for v in (text_conf.get((t, i)), structured.get((t, i))) if v is not None)
                out[(t, i, p)] = Claim(t, i, p, "text_only", text.extractor, conf)
    return tuple(out[k] for k in sorted(out))


def sense(record: Mapping[str, Any], pack: "FrozenPack", canonicaliser: Canonicaliser,
          extractor: LexicalExtractor | ModelExtractor | None = None, *,
          ref: str | None = None) -> tuple[CodesResult, ExtractionResult | None, tuple[Claim, ...]]:
    codes = codes_channel(record, pack, canonicaliser)
    if extractor is None:
        text = None
    elif isinstance(extractor, ModelExtractor):
        text = extractor.extract(record, codes, ref=ref)
    else:
        text = extractor.extract(record, codes)
    return codes, text, pair(codes, text)
