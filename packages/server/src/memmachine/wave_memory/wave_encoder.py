"""Wave Encoder - Measures amplitudes and extracts signals from text.

Encodes any text as a multi-dimensional wave.
No classification. Measures continuous amplitudes across all dimensions.
Extracts signals where amplitude is strong enough.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import spacy
    from spacy.tokens import Doc, Token
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

from .data_types import (
    WaveAmplitudes,
    MemoryWave,
    WaveSignal,
    SignalType,
    TemporalSignal,
    EntitySignal,
    RelationSignal,
    ActionSignal,
    StateSignal,
    SpatialSignal,
    CausalSignal,
    EmotionalSignal,
    QuantitativeSignal,
    wave_combine,
)


class WaveEncoder:
    """Encode any text as a multi-dimensional wave.

    No classification. Measures continuous amplitudes across all dimensions.
    Extracts signals where amplitude is strong enough.
    """

    SIGNAL_THRESHOLD = 0.25  # Extract signals above this amplitude

    def __init__(
        self,
        nlp: Any = None,
        signal_threshold: float = 0.25,
    ):
        """Initialize the encoder.

        Args:
            nlp: spaCy language model. If None, loads en_core_web_sm.
            signal_threshold: Minimum amplitude to extract signals.
        """
        self.signal_threshold = signal_threshold

        if nlp is not None:
            self.nlp = nlp
        elif SPACY_AVAILABLE:
            self.nlp = spacy.load("en_core_web_sm")
        else:
            self.nlp = None

    def encode(
        self,
        content: str,
        speaker: str = "",
        timestamp: datetime | None = None,
        session_key: str = "",
        msg_idx: int = 0,
        reference_date: datetime | None = None,
    ) -> MemoryWave:
        """Encode text into a MemoryWave.

        Args:
            content: The text content to encode.
            speaker: Speaker name (for entity resolution).
            timestamp: Message timestamp.
            session_key: Session identifier.
            msg_idx: Message index in session.
            reference_date: Reference date for temporal resolution.

        Returns:
            MemoryWave with amplitudes and extracted signals.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        if reference_date is None:
            reference_date = timestamp

        # Process with spaCy if available
        doc = self.nlp(content) if self.nlp else None
        text_lower = content.lower()

        # Measure all amplitudes
        amplitudes = self._measure_amplitudes(doc, content, text_lower)

        # Create the wave
        wave = MemoryWave(
            wave_id=str(uuid.uuid4()),
            content=content,
            speaker=speaker,
            timestamp=timestamp,
            session_key=session_key,
            msg_idx=msg_idx,
            amplitudes=amplitudes,
            signals=[],
        )

        # Extract signals where amplitude is strong
        if amplitudes.temporal > self.signal_threshold:
            wave.signals.extend(
                self._extract_temporal_signals(doc, text_lower, reference_date, amplitudes.temporal)
            )

        if amplitudes.entity > self.signal_threshold:
            wave.signals.extend(
                self._extract_entity_signals(doc, speaker, amplitudes.entity)
            )

        if amplitudes.relational > self.signal_threshold:
            wave.signals.extend(
                self._extract_relation_signals(doc, text_lower, speaker, amplitudes.relational)
            )

        if amplitudes.action > self.signal_threshold:
            wave.signals.extend(
                self._extract_action_signals(doc, speaker, amplitudes.action)
            )

        if amplitudes.state > self.signal_threshold:
            wave.signals.extend(
                self._extract_state_signals(doc, text_lower, speaker, amplitudes.state)
            )

        if amplitudes.spatial > self.signal_threshold:
            wave.signals.extend(
                self._extract_spatial_signals(doc, text_lower, amplitudes.spatial)
            )

        if amplitudes.causal > self.signal_threshold:
            wave.signals.extend(
                self._extract_causal_signals(doc, text_lower, amplitudes.causal)
            )

        if amplitudes.emotional > self.signal_threshold:
            wave.signals.extend(
                self._extract_emotional_signals(doc, text_lower, speaker, amplitudes.emotional)
            )

        if amplitudes.quantitative > self.signal_threshold:
            wave.signals.extend(
                self._extract_quantitative_signals(doc, text_lower, amplitudes.quantitative)
            )

        return wave

    def _measure_amplitudes(
        self,
        doc: Any,
        content: str,
        text_lower: str
    ) -> WaveAmplitudes:
        """Measure all 9 dimensional amplitudes from text."""
        return WaveAmplitudes(
            temporal=self._measure_temporal(doc, text_lower),
            entity=self._measure_entity(doc, text_lower),
            relational=self._measure_relational(doc, text_lower),
            action=self._measure_action(doc, text_lower),
            state=self._measure_state(doc, text_lower),
            spatial=self._measure_spatial(doc, text_lower),
            causal=self._measure_causal(doc, text_lower),
            emotional=self._measure_emotional(doc, text_lower),
            quantitative=self._measure_quantitative(doc, text_lower),
        )

    # =========================================================================
    # AMPLITUDE MEASUREMENT FUNCTIONS
    # =========================================================================

    def _measure_temporal(self, doc: Any, text_lower: str) -> float:
        """Measure temporal amplitude - how much 'when-ness'."""
        signals = []

        # Signal: Named temporal entities (spaCy)
        if doc:
            temporal_ents = sum(1 for ent in doc.ents if ent.label_ in ("DATE", "TIME"))
            signals.append(min(temporal_ents * 0.4, 1.0))

        # Signal: Temporal prepositions
        temporal_preps = {"on", "at", "during", "before", "after", "since", "until", "when", "while"}
        if doc:
            prep_hits = sum(1 for t in doc if t.text.lower() in temporal_preps)
        else:
            prep_hits = sum(1 for word in temporal_preps if word in text_lower)
        signals.append(min(prep_hits * 0.2, 1.0))

        # Signal: Relative time expressions
        relative_time = [
            "yesterday", "tomorrow", "today", "tonight",
            "last week", "next week", "this week",
            "last month", "next month", "this month",
            "last year", "next year", "this year",
            "ago", "later", "soon", "recently", "earlier",
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "morning", "afternoon", "evening", "night", "noon", "midnight",
            "the other day", "a while back", "couple weeks", "few days",
            "last time", "next time", "every week", "every month",
        ]
        relative_hits = sum(1 for expr in relative_time if expr in text_lower)
        signals.append(min(relative_hits * 0.35, 1.0))

        # Signal: Tense marking (past/future stronger than present)
        tense_signal = 0.0
        if doc:
            for token in doc:
                if token.pos_ == "VERB":
                    if token.tag_ in ("VBD", "VBN"):  # Past
                        tense_signal += 0.15
                    elif any(c.text.lower() == "will" for c in token.children):  # Future
                        tense_signal += 0.2
        signals.append(min(tense_signal, 1.0))

        # Signal: Direct question words
        if any(q in text_lower for q in ["when did", "when was", "when will", "what time", "what date", "how long"]):
            signals.append(0.6)

        return wave_combine(signals)

    def _measure_entity(self, doc: Any, text_lower: str) -> float:
        """Measure entity amplitude - how much 'who/what-ness'."""
        signals = []

        if doc:
            # Signal: Named entities
            ent_count = len(doc.ents)
            signals.append(min(ent_count * 0.25, 1.0))

            # Signal: Proper nouns
            proper_nouns = sum(1 for t in doc if t.pos_ == "PROPN")
            signals.append(min(proper_nouns * 0.2, 1.0))

            # Signal: Possessive structures ("my X", "X's Y")
            possessives = sum(1 for t in doc if t.dep_ == "poss")
            signals.append(min(possessives * 0.25, 1.0))

            # Signal: Pronouns (reference existing entities)
            pronouns = sum(1 for t in doc if t.pos_ == "PRON")
            signals.append(min(pronouns * 0.15, 1.0))

        # Signal: First/second person references
        first_person = any(p in text_lower for p in ["i ", "i'm", "my ", "me ", "we ", "our "])
        if first_person:
            signals.append(0.3)

        # Signal: Question about who/what
        if any(q in text_lower for q in ["who ", "whose ", "what is", "what was", "what does"]):
            signals.append(0.5)

        return wave_combine(signals)

    def _measure_relational(self, doc: Any, text_lower: str) -> float:
        """Measure relational amplitude - how much 'connection-ness'."""
        signals = []

        # Signal: Relationship words
        relation_words = {
            "friend", "friends", "family", "mother", "father", "sister", "brother",
            "husband", "wife", "partner", "boyfriend", "girlfriend", "spouse",
            "colleague", "coworker", "boss", "employee", "neighbor",
            "knows", "know", "met", "introduced",
            "married", "dating", "engaged", "divorced", "related",
            "works with", "works for", "lives with", "together",
            "daughter", "son", "aunt", "uncle", "cousin", "grandmother", "grandfather",
        }
        rel_hits = sum(1 for word in relation_words if word in text_lower)
        signals.append(min(rel_hits * 0.35, 1.0))

        # Signal: Multiple person entities (implies relation between them)
        if doc:
            person_ents = [ent for ent in doc.ents if ent.label_ == "PERSON"]
            if len(person_ents) >= 2:
                signals.append(0.6)
            elif len(person_ents) == 1:
                signals.append(0.2)

        # Signal: Conjunction of entities ("X and Y")
        if " and " in text_lower:
            if doc:
                person_ents = [ent for ent in doc.ents if ent.label_ == "PERSON"]
                if len(person_ents) >= 1:
                    signals.append(0.4)

        # Signal: Possessive relations ("X's friend", "my sister")
        poss_rel = bool(re.search(
            r"\b(my|his|her|their|our)\s+(friend|family|sister|brother|mother|father|husband|wife|partner|colleague)",
            text_lower
        ))
        if poss_rel:
            signals.append(0.5)

        return wave_combine(signals)

    def _measure_action(self, doc: Any, text_lower: str) -> float:
        """Measure action amplitude - how much 'doing-ness'."""
        signals = []

        if doc:
            # Signal: Verb presence and density
            verbs = [t for t in doc if t.pos_ == "VERB"]
            verb_density = len(verbs) / max(len(doc), 1)
            signals.append(min(verb_density * 4, 1.0))

            # Signal: Active vs stative verbs
            active_verbs = {
                "go", "run", "walk", "make", "create", "build", "paint",
                "write", "send", "buy", "sell", "meet", "visit", "attend",
                "start", "finish", "complete", "sign", "join", "leave",
                "play", "watch", "read", "eat", "drink", "cook", "clean",
                "work", "travel", "exercise", "study", "practice", "learn",
            }
            stative_verbs = {
                "be", "is", "was", "were", "am", "are", "been",
                "have", "has", "had", "seem", "appear", "feel", "think", "know",
            }

            active_count = sum(1 for v in verbs if v.lemma_ in active_verbs)
            stative_count = sum(1 for v in verbs if v.lemma_ in stative_verbs)

            if active_count + stative_count > 0:
                action_ratio = active_count / (active_count + stative_count)
                signals.append(action_ratio)

            # Signal: Subjects performing actions
            agent_count = sum(1 for v in verbs for c in v.children if c.dep_ == "nsubj")
            signals.append(min(agent_count * 0.25, 1.0))

            # Signal: Objects receiving action
            patient_count = sum(1 for v in verbs for c in v.children if c.dep_ == "dobj")
            signals.append(min(patient_count * 0.2, 1.0))

        # Signal: Action question words
        if any(q in text_lower for q in ["what did", "did you", "did she", "did he", "have you"]):
            signals.append(0.4)

        return wave_combine(signals)

    def _measure_state(self, doc: Any, text_lower: str) -> float:
        """Measure state amplitude - how much 'being-ness'."""
        signals = []

        if doc:
            # Signal: Copular constructions ("X is Y")
            copulas = sum(1 for t in doc if t.dep_ == "ROOT" and t.lemma_ in ("be", "seem", "appear", "become"))
            signals.append(min(copulas * 0.4, 1.0))

            # Signal: Adjectives (describe states)
            adj_count = sum(1 for t in doc if t.pos_ == "ADJ")
            adj_density = adj_count / max(len(doc), 1)
            signals.append(min(adj_density * 5, 1.0))

        # Signal: State/identity patterns
        state_patterns = [
            r"\bi am\b", r"\bi'm\b", r"\bshe is\b", r"\bhe is\b",
            r"\bthey are\b", r"\bwe are\b", r"\bit is\b",
            r"\bi was\b", r"\bshe was\b", r"\bhe was\b",
            r"\bmy .+ is\b", r"\bhis .+ is\b", r"\bher .+ is\b",
        ]
        state_hits = sum(1 for p in state_patterns if re.search(p, text_lower))
        signals.append(min(state_hits * 0.25, 1.0))

        # Signal: State verbs
        state_verbs = {
            "have", "has", "had", "own", "like", "love",
            "want", "need", "believe", "think", "prefer",
        }
        state_verb_hits = sum(1 for word in state_verbs if word in text_lower)
        signals.append(min(state_verb_hits * 0.25, 1.0))

        # Signal: Ongoing markers
        ongoing_markers = {"still", "currently", "always", "usually", "normally", "typically"}
        ongoing_hits = sum(1 for marker in ongoing_markers if marker in text_lower)
        signals.append(min(ongoing_hits * 0.35, 1.0))

        return wave_combine(signals)

    def _measure_spatial(self, doc: Any, text_lower: str) -> float:
        """Measure spatial amplitude - how much 'where-ness'."""
        signals = []

        if doc:
            # Signal: Location entities
            loc_ents = sum(1 for ent in doc.ents if ent.label_ in ("GPE", "LOC", "FAC"))
            signals.append(min(loc_ents * 0.4, 1.0))

            # Signal: Spatial prepositions
            spatial_preps = {
                "in", "at", "on", "near", "by", "beside", "behind",
                "above", "below", "under", "over", "between", "among",
                "inside", "outside", "through", "across", "around",
                "to", "from", "toward", "into", "onto",
            }
            prep_hits = sum(1 for t in doc if t.text.lower() in spatial_preps and t.dep_ == "prep")
            signals.append(min(prep_hits * 0.2, 1.0))

        # Signal: Location words
        location_words = {
            "home", "house", "apartment", "office", "work", "school",
            "hospital", "store", "shop", "restaurant", "park", "beach",
            "city", "town", "country", "state", "street", "road",
            "here", "there", "somewhere", "anywhere", "everywhere",
            "downtown", "uptown", "neighborhood", "area", "region",
        }
        loc_hits = sum(1 for word in location_words if word in text_lower)
        signals.append(min(loc_hits * 0.3, 1.0))

        # Signal: Movement verbs (imply spatial)
        movement_verbs = {
            "go", "went", "come", "came", "move", "moved",
            "travel", "traveled", "visit", "visited", "leave", "left",
            "arrive", "arrived", "return", "returned", "drive", "drove",
        }
        move_hits = sum(1 for word in movement_verbs if word in text_lower)
        signals.append(min(move_hits * 0.25, 1.0))

        # Signal: Where questions
        if any(q in text_lower for q in ["where ", "where's", "where did", "where is"]):
            signals.append(0.6)

        return wave_combine(signals)

    def _measure_causal(self, doc: Any, text_lower: str) -> float:
        """Measure causal amplitude - how much 'why/because-ness'."""
        signals = []

        # Signal: Causal connectors
        causal_connectors = {
            "because", "since", "therefore", "thus", "hence", "so",
            "due to", "owing to", "as a result", "consequently",
            "caused", "causes", "led to", "leads to",
            "resulted in", "results in", "reason", "why",
            "in order to", "so that", "for this reason",
        }
        causal_hits = sum(1 for conn in causal_connectors if conn in text_lower)
        signals.append(min(causal_hits * 0.4, 1.0))

        # Signal: Conditional structures
        if doc:
            conditionals = {"if", "unless", "whether", "when", "whenever", "would"}
            cond_hits = sum(1 for t in doc if t.text.lower() in conditionals)
            signals.append(min(cond_hits * 0.25, 1.0))

        # Signal: Purpose clauses
        purpose_pattern = bool(re.search(r"\b(to|for)\s+\w+ing?\b", text_lower))
        if purpose_pattern:
            signals.append(0.3)

        # Signal: Why questions
        if any(q in text_lower for q in ["why ", "why's", "why did", "why is", "how come"]):
            signals.append(0.6)

        return wave_combine(signals)

    def _measure_emotional(self, doc: Any, text_lower: str) -> float:
        """Measure emotional amplitude - how much 'feeling-ness'."""
        signals = []

        # Signal: Emotion words
        emotion_words = {
            # Positive
            "happy", "happiness", "joy", "joyful", "excited", "excitement",
            "love", "loved", "loving", "grateful", "thankful", "proud",
            "pleased", "delighted", "glad", "cheerful", "hopeful",
            # Negative
            "sad", "sadness", "angry", "anger", "upset", "frustrated",
            "worried", "anxious", "scared", "afraid", "fear", "fearful",
            "disappointed", "hurt", "lonely", "depressed", "stressed",
            # Neutral-intense
            "surprised", "shocked", "amazed", "confused", "curious",
        }
        emotion_hits = sum(1 for word in emotion_words if word in text_lower)
        signals.append(min(emotion_hits * 0.35, 1.0))

        # Signal: Feeling verbs
        feeling_verbs = {"feel", "feels", "felt", "feeling"}
        feel_hits = sum(1 for word in feeling_verbs if word in text_lower)
        signals.append(min(feel_hits * 0.4, 1.0))

        # Signal: Exclamation marks (intensity)
        exclamations = text_lower.count("!")
        signals.append(min(exclamations * 0.2, 1.0))

        # Signal: Intensifiers
        intensifiers = {"very", "really", "so", "extremely", "incredibly", "absolutely"}
        intens_hits = sum(1 for word in intensifiers if word in text_lower)
        signals.append(min(intens_hits * 0.2, 1.0))

        # Signal: First person emotional expression
        first_person_emotion = bool(re.search(
            r"\bi\s+(feel|felt|am|was)\s+(happy|sad|angry|excited|worried|scared|grateful|proud)",
            text_lower
        ))
        if first_person_emotion:
            signals.append(0.5)

        return wave_combine(signals)

    def _measure_quantitative(self, doc: Any, text_lower: str) -> float:
        """Measure quantitative amplitude - how much 'how-much-ness'."""
        signals = []

        if doc:
            # Signal: Numbers
            numbers = sum(1 for t in doc if t.like_num or t.pos_ == "NUM")
            signals.append(min(numbers * 0.35, 1.0))

        # Signal: Quantifiers
        quantifiers = {
            "many", "much", "few", "several", "some", "all", "most",
            "every", "each", "both", "none", "any", "enough",
            "more", "less", "fewer", "twice", "half", "double",
        }
        quant_hits = sum(1 for word in quantifiers if word in text_lower)
        signals.append(min(quant_hits * 0.3, 1.0))

        # Signal: Measurement units
        units = {
            "percent", "%", "dollar", "dollars", "$",
            "mile", "miles", "kilometer", "km",
            "hour", "hours", "minute", "minutes",
            "pound", "pounds", "kg",
            "year", "years", "month", "months",
            "week", "weeks", "day", "days",
            "time", "times",
        }
        unit_hits = sum(1 for unit in units if unit in text_lower)
        signals.append(min(unit_hits * 0.25, 1.0))

        # Signal: Comparison structures
        comparisons = {"more than", "less than", "as much as", "as many as",
                       "bigger", "smaller", "larger", "higher", "lower"}
        comp_hits = sum(1 for comp in comparisons if comp in text_lower)
        signals.append(min(comp_hits * 0.35, 1.0))

        # Signal: How many/much questions
        if any(q in text_lower for q in ["how many", "how much", "how often", "how long"]):
            signals.append(0.6)

        return wave_combine(signals)

    # =========================================================================
    # SIGNAL EXTRACTION FUNCTIONS
    # =========================================================================

    def _extract_temporal_signals(
        self,
        doc: Any,
        text_lower: str,
        reference_date: datetime,
        amplitude: float
    ) -> list[TemporalSignal]:
        """Extract structured temporal signals."""
        signals = []

        # Extract DATE/TIME entities from spaCy
        if doc:
            for ent in doc.ents:
                if ent.label_ in ("DATE", "TIME"):
                    resolved = self._resolve_temporal_expression(ent.text, reference_date)
                    signals.append(TemporalSignal(
                        amplitude=amplitude,
                        raw_expression=ent.text,
                        resolved_date=resolved.get("date"),
                        grain=resolved.get("grain", "day"),
                        is_relative=resolved.get("is_relative", False),
                        confidence=resolved.get("confidence", 0.7),
                    ))

        # Extract relative time expressions
        relative_patterns = [
            (r"yesterday", -1, "day"),
            (r"today", 0, "day"),
            (r"tomorrow", 1, "day"),
            (r"last week", -7, "week"),
            (r"next week", 7, "week"),
            (r"last month", -30, "month"),
            (r"next month", 30, "month"),
            (r"(\d+)\s+days?\s+ago", None, "day"),
            (r"(\d+)\s+weeks?\s+ago", None, "week"),
            (r"(\d+)\s+months?\s+ago", None, "month"),
            (r"(\d+)\s+years?\s+ago", None, "year"),
        ]

        for pattern, offset, grain in relative_patterns:
            match = re.search(pattern, text_lower)
            if match:
                if offset is None:  # Variable offset
                    num = int(match.group(1))
                    if grain == "day":
                        offset = -num
                    elif grain == "week":
                        offset = -num * 7
                    elif grain == "month":
                        offset = -num * 30
                    elif grain == "year":
                        offset = -num * 365

                resolved_date = reference_date + timedelta(days=offset) if offset is not None else None
                signals.append(TemporalSignal(
                    amplitude=amplitude,
                    raw_expression=match.group(0),
                    resolved_date=resolved_date,
                    grain=grain,
                    is_relative=True,
                    confidence=0.85,
                ))

        # Extract weekday references
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        for day_name, day_num in weekdays.items():
            if day_name in text_lower:
                is_past = self._is_past_tense(doc) if doc else True
                resolved = self._resolve_weekday(day_num, reference_date, is_past)
                signals.append(TemporalSignal(
                    amplitude=amplitude,
                    raw_expression=day_name,
                    resolved_date=resolved,
                    grain="day",
                    is_relative=True,
                    confidence=0.75,
                ))

        return signals

    def _extract_entity_signals(
        self,
        doc: Any,
        speaker: str,
        amplitude: float
    ) -> list[EntitySignal]:
        """Extract structured entity signals."""
        signals = []

        # Named entities from spaCy
        if doc:
            for ent in doc.ents:
                signals.append(EntitySignal(
                    amplitude=amplitude,
                    entity_name=ent.text,
                    entity_type=ent.label_,
                    confidence=0.9,
                ))

        # First person references -> speaker entity
        if doc:
            has_first_person = any(
                t.text.lower() in ("i", "me", "my", "mine", "myself")
                for t in doc
            )
            if has_first_person and speaker:
                signals.append(EntitySignal(
                    amplitude=amplitude,
                    entity_name=speaker,
                    entity_type="PERSON",
                    is_speaker=True,
                    confidence=0.95,
                ))

        return signals

    def _extract_relation_signals(
        self,
        doc: Any,
        text_lower: str,
        speaker: str,
        amplitude: float
    ) -> list[RelationSignal]:
        """Extract structured relationship signals."""
        signals = []

        # Pattern: "X is my [relation]"
        relation_patterns = [
            (r"(\w+)\s+is\s+my\s+(friend|sister|brother|mother|father|husband|wife|partner|boss|colleague)",
             lambda m: (speaker, m.group(1), m.group(2))),
            (r"my\s+(friend|sister|brother|mother|father|husband|wife|partner)\s+(\w+)",
             lambda m: (speaker, m.group(2), m.group(1))),
            (r"(\w+)\s+and\s+i\s+are\s+(friends|married|dating|colleagues)",
             lambda m: (speaker, m.group(1), m.group(2))),
        ]

        for pattern, extractor in relation_patterns:
            match = re.search(pattern, text_lower)
            if match:
                source, target, rel_type = extractor(match)
                signals.append(RelationSignal(
                    amplitude=amplitude,
                    source_entity=source,
                    target_entity=target,
                    relation_type=rel_type,
                    strength=0.8,
                    bidirectional=rel_type in ("friends", "married", "colleagues"),
                    confidence=0.8,
                ))

        return signals

    def _extract_action_signals(
        self,
        doc: Any,
        speaker: str,
        amplitude: float
    ) -> list[ActionSignal]:
        """Extract structured action signals."""
        signals = []

        if doc:
            for token in doc:
                if token.pos_ == "VERB" and token.dep_ in ("ROOT", "conj"):
                    # Skip auxiliary verbs
                    if token.lemma_ in ("be", "have", "do", "will", "would", "could", "should"):
                        continue

                    # Get agent
                    agent = None
                    for child in token.children:
                        if child.dep_ == "nsubj":
                            agent = speaker if child.text.lower() == "i" else child.text
                            break

                    # Get patient
                    patient = None
                    for child in token.children:
                        if child.dep_ in ("dobj", "attr"):
                            patient = child.text
                            break

                    # Determine tense
                    tense = "present"
                    if token.tag_ in ("VBD", "VBN"):
                        tense = "past"
                    elif any(c.text.lower() == "will" for c in token.children):
                        tense = "future"

                    signals.append(ActionSignal(
                        amplitude=amplitude,
                        verb_lemma=token.lemma_,
                        verb_tense=tense,
                        agent=agent,
                        patient=patient,
                        confidence=0.75,
                    ))

        return signals

    def _extract_state_signals(
        self,
        doc: Any,
        text_lower: str,
        speaker: str,
        amplitude: float
    ) -> list[StateSignal]:
        """Extract structured state signals."""
        signals = []

        # "I am X" patterns
        identity_patterns = [
            (r"i\s+am\s+(?:a\s+)?(\w+)", speaker, "identity"),
            (r"i\s+have\s+(?:a\s+)?(\w+)", speaker, "possession"),
            (r"i\s+work\s+(?:at|for)\s+(\w+)", speaker, "employer"),
            (r"i\s+live\s+in\s+(\w+)", speaker, "location"),
            (r"i\s+like\s+(\w+)", speaker, "preference"),
            (r"i\s+love\s+(\w+)", speaker, "preference"),
        ]

        for pattern, entity, attr in identity_patterns:
            match = re.search(pattern, text_lower)
            if match:
                value = match.group(1)

                # Check if ongoing
                is_current = "still" in text_lower or "currently" in text_lower or \
                    not self._is_past_tense(doc) if doc else True

                signals.append(StateSignal(
                    amplitude=amplitude,
                    entity=entity,
                    attribute=attr,
                    value=value,
                    is_current=is_current,
                    confidence=0.8,
                ))

        return signals

    def _extract_spatial_signals(
        self,
        doc: Any,
        text_lower: str,
        amplitude: float
    ) -> list[SpatialSignal]:
        """Extract structured spatial signals."""
        signals = []

        # Location entities from spaCy
        if doc:
            for ent in doc.ents:
                if ent.label_ in ("GPE", "LOC", "FAC"):
                    signals.append(SpatialSignal(
                        amplitude=amplitude,
                        location_name=ent.text,
                        location_type=ent.label_.lower(),
                        confidence=0.9,
                    ))

        # Location word patterns
        location_patterns = [
            (r"(?:at|in|to)\s+(?:the\s+)?(\w+(?:\s+\w+)?)", "place"),
        ]

        for pattern, loc_type in location_patterns:
            matches = re.finditer(pattern, text_lower)
            for match in matches:
                loc_name = match.group(1)
                # Skip common non-location words
                if loc_name not in ("the", "a", "an", "my", "your"):
                    signals.append(SpatialSignal(
                        amplitude=amplitude,
                        location_name=loc_name,
                        location_type=loc_type,
                        confidence=0.6,
                    ))

        return signals

    def _extract_causal_signals(
        self,
        doc: Any,
        text_lower: str,
        amplitude: float
    ) -> list[CausalSignal]:
        """Extract structured causal signals."""
        signals = []

        # "X because Y" pattern
        because_match = re.search(r"(.+?)\s+because\s+(.+)", text_lower)
        if because_match:
            signals.append(CausalSignal(
                amplitude=amplitude,
                cause=because_match.group(2).strip(),
                effect=because_match.group(1).strip(),
                causal_strength=0.9,
                temporal_order="cause_first",
                connector="because",
                confidence=0.85,
            ))

        # "X so Y" pattern
        so_match = re.search(r"(.+?)\s+so\s+(.+)", text_lower)
        if so_match:
            signals.append(CausalSignal(
                amplitude=amplitude,
                cause=so_match.group(1).strip(),
                effect=so_match.group(2).strip(),
                causal_strength=0.7,
                temporal_order="cause_first",
                connector="so",
                confidence=0.75,
            ))

        return signals

    def _extract_emotional_signals(
        self,
        doc: Any,
        text_lower: str,
        speaker: str,
        amplitude: float
    ) -> list[EmotionalSignal]:
        """Extract structured emotional signals."""
        signals = []

        # Emotion lexicon with valence/arousal
        emotion_lexicon = {
            "happy": (0.8, 0.6), "sad": (-0.7, 0.4), "angry": (-0.6, 0.9),
            "excited": (0.7, 0.9), "worried": (-0.5, 0.6), "scared": (-0.6, 0.8),
            "love": (0.9, 0.6), "hate": (-0.8, 0.8), "grateful": (0.7, 0.4),
            "frustrated": (-0.5, 0.7), "proud": (0.6, 0.5), "ashamed": (-0.6, 0.5),
            "surprised": (0.2, 0.8), "confused": (-0.2, 0.5), "hopeful": (0.6, 0.5),
            "anxious": (-0.5, 0.7), "calm": (0.3, 0.2), "nervous": (-0.4, 0.6),
        }

        for emotion, (valence, arousal) in emotion_lexicon.items():
            if emotion in text_lower:
                signals.append(EmotionalSignal(
                    amplitude=amplitude,
                    emotion=emotion,
                    valence=valence,
                    arousal=arousal,
                    source_entity=speaker,
                    confidence=0.8,
                ))

        return signals

    def _extract_quantitative_signals(
        self,
        doc: Any,
        text_lower: str,
        amplitude: float
    ) -> list[QuantitativeSignal]:
        """Extract structured quantitative signals."""
        signals = []

        # Numbers with context
        if doc:
            for token in doc:
                if token.like_num or token.pos_ == "NUM":
                    try:
                        value = float(token.text.replace(",", ""))
                    except ValueError:
                        value = None

                    # Look for unit following the number
                    unit = None
                    for child in token.head.children:
                        if child.dep_ in ("prep", "npadvmod") and child.i > token.i:
                            unit = child.text
                            break

                    signals.append(QuantitativeSignal(
                        amplitude=amplitude,
                        value=value,
                        unit=unit,
                        confidence=0.8,
                    ))

        return signals

    # =========================================================================
    # HELPER FUNCTIONS
    # =========================================================================

    def _is_past_tense(self, doc: Any) -> bool:
        """Detect if the main clause is past tense."""
        if not doc:
            return False
        for token in doc:
            if token.dep_ == "ROOT" and token.pos_ == "VERB":
                return token.tag_ in ("VBD", "VBN")
        return False

    def _resolve_temporal_expression(self, expr: str, ref_time: datetime) -> dict:
        """Resolve a temporal expression to a date."""
        # Basic resolution - can be enhanced with HeidelTime/SUTime
        expr_lower = expr.lower()

        if "yesterday" in expr_lower:
            return {"date": ref_time - timedelta(days=1), "confidence": 0.9, "grain": "day", "is_relative": True}
        if "today" in expr_lower:
            return {"date": ref_time, "confidence": 0.9, "grain": "day", "is_relative": True}
        if "tomorrow" in expr_lower:
            return {"date": ref_time + timedelta(days=1), "confidence": 0.9, "grain": "day", "is_relative": True}

        return {"date": ref_time, "confidence": 0.5, "grain": "day", "is_relative": False}

    def _resolve_weekday(self, target_weekday: int, ref_time: datetime, is_past: bool) -> datetime:
        """Resolve weekday reference based on tense."""
        current_weekday = ref_time.weekday()

        if is_past:
            days_back = (current_weekday - target_weekday) % 7
            if days_back == 0:
                days_back = 7  # Last week's occurrence
            return ref_time - timedelta(days=days_back)
        else:
            days_forward = (target_weekday - current_weekday) % 7
            if days_forward == 0:
                days_forward = 7  # Next week's occurrence
            return ref_time + timedelta(days=days_forward)
