//! Universal message linking layer, "the dialogue fabric" (port of `retrieval/dialogue_linker.py`).
//!
//! Messages do not carry their meaning alone: "It was beautiful at sunset" means something only next to "How
//! was the Eiffel Tower?". After ingestion every LoCoMo harness runs [`DialogueLinker::process_dialogue_sequence`]
//! over the conversation, which builds hidden *aggregators* above the messages (question/answer exchanges, topic
//! threads over shared capitalised entities, temporal anchors such as "yesterday") and *bindings* between them
//! (pronoun -> antecedent coreference, response -> trigger). The graph retrieval mode then widens a hit with
//! [`DialogueLinker::get_linked_messages`].
//!
//! Notes for readers comparing with the Python:
//! - The Python methods were `async` without awaiting anything; these are synchronous.
//! - Entity sets were Python `set`s, and several results depend on their iteration order (which antecedent wins
//!   a tie of equal confidence, the order of names in an exchange topic, `get_topic_context`). That order came
//!   from per-process string hashing and was not reproducible in Python. Here every set is insertion-ordered
//!   ([`IndexSet`]); set intersection keeps the left operand's order. The golden fixture pins this against the
//!   reference run with insertion-ordered sets (`tests/golden/_ordered_sets.py`).
//! - Ids are `f"exch-{uuid.uuid4().hex[:8]}"` and friends. Under the shared deterministic uuid hook every such
//!   prefix would be `00000000` (its counter sits in the low bits), collapsing all aggregators onto one id, so
//!   tests use [`set_deterministic_hex_ids`] instead: a counter in the leading hex digits, exactly what the
//!   generator patches into the Python module.
//! - `re.sub(r"[^\w]", "", word)` uses Python's `\w` ([`crate::pyre`]), which differs from Rust's on the
//!   zero-width joiners inside LoCoMo's emoji.

use crate::pyre::strip_non_word;
use crate::retrieval::data_types::{cosine_similarity, uuid4, NodeRef};
use crate::retrieval::storage::NeuralGraphStorage;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_prefix, py_split_whitespace, py_strip};
use std::cell::Cell;

/// Thresholds of the linking layer (`DialogueLinkingConfig`, class attributes only).
pub struct DialogueLinkingConfig;

impl DialogueLinkingConfig {
    pub const EXCHANGE_COHESION_QUESTION: f64 = 0.95;
    pub const EXCHANGE_COHESION_DEFAULT: f64 = 0.8;
    pub const TOPIC_MIN_MESSAGES: usize = 2;
    pub const TOPIC_COHESION_BASE: f64 = 0.25;
    pub const TOPIC_COHESION_MAX: f64 = 1.0;
    pub const COREF_CONFIDENCE: f64 = 0.75;
    pub const COREF_HIGH_CONFIDENCE: f64 = 0.85;
    pub const RESPONSE_BASE_CONFIDENCE: f64 = 0.65;
    pub const RESPONSE_EXPLICIT_CONFIDENCE: f64 = 0.95;
    pub const RESPONSE_CONTINUES_CONFIDENCE: f64 = 0.85;
    pub const RESPONSE_CONTRASTS_CONFIDENCE: f64 = 0.9;
    pub const TEMPORAL_COHESION: f64 = 0.95;
    pub const LINK_BOOST: f64 = 0.85;
    pub const ATOMIC_PAIR_BOOST: f64 = 1.2;
    pub const HIGH_CONFIDENCE_MODE: bool = true;
    pub const MIN_BINDING_CONFIDENCE: f64 = 0.5;
    pub const MIN_AGGREGATOR_COHESION: f64 = 0.4;
    pub const SINGLE_HOP_COREF_BOOST: f64 = 1.15;
    pub const DROP_LOW_SIMILARITY_LINKS: bool = true;
    pub const SIMILARITY_DROP_THRESHOLD: f64 = 0.3;
    pub const MAX_COREF_CHAIN_LENGTH: usize = 5;
    pub const ENTITY_MENTION_DECAY: f64 = 0.95;
}

/// Kinds of dialogue aggregator.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum AggregatorType {
    Exchange,
    TopicThread,
    CorefChain,
    TemporalAnchor,
    SpeakerTurn,
    Reaction,
}

impl AggregatorType {
    pub fn value(self) -> &'static str {
        match self {
            AggregatorType::Exchange => "exchange",
            AggregatorType::TopicThread => "topic_thread",
            AggregatorType::CorefChain => "coref_chain",
            AggregatorType::TemporalAnchor => "temporal",
            AggregatorType::SpeakerTurn => "speaker_turn",
            AggregatorType::Reaction => "reaction",
        }
    }
}

/// Kinds of cross-message binding.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum BindingType {
    RespondsTo,
    RefersTo,
    Continues,
    Elaborates,
    Contrasts,
    TemporalLink,
}

impl BindingType {
    pub fn value(self) -> &'static str {
        match self {
            BindingType::RespondsTo => "responds_to",
            BindingType::RefersTo => "refers_to",
            BindingType::Continues => "continues",
            BindingType::Elaborates => "elaborates",
            BindingType::Contrasts => "contrasts",
            BindingType::TemporalLink => "temporal",
        }
    }
}

/// Hidden node binding related messages (never retrieved itself; it activates with its members).
#[derive(Clone, Debug, PartialEq)]
pub struct DialogueAggregator {
    pub aggregator_id: String,
    pub aggregator_type: AggregatorType,
    pub session_key: String,
    pub message_ids: Vec<String>,
    pub topic: String,
    pub referent: String,
    pub mentions: Vec<String>,
    pub temporal_expression: String,
    pub resolved_date: String,
    /// Centroid of the member embeddings (exchanges only).
    pub embedding: Option<Vec<f64>>,
    pub binding_weights: IndexMap<String, f64>,
    pub cohesion_score: f64,
}

impl DialogueAggregator {
    /// The dataclass constructor with defaults; an empty id becomes `agg-<12 hex>` (`__post_init__`).
    pub fn new(
        aggregator_id: impl Into<String>,
        aggregator_type: AggregatorType,
        session_key: impl Into<String>,
    ) -> Self {
        let mut aggregator_id = aggregator_id.into();
        if aggregator_id.is_empty() {
            aggregator_id = format!("agg-{}", &uuid4_hex()[..12]);
        }
        DialogueAggregator {
            aggregator_id,
            aggregator_type,
            session_key: session_key.into(),
            message_ids: Vec::new(),
            topic: String::new(),
            referent: String::new(),
            mentions: Vec::new(),
            temporal_expression: String::new(),
            resolved_date: String::new(),
            embedding: None,
            binding_weights: IndexMap::new(),
            cohesion_score: 0.5,
        }
    }
}

/// Edge between two messages through dialogue structure.
#[derive(Clone, Debug, PartialEq)]
pub struct CrossMessageBinding {
    pub binding_id: String,
    pub binding_type: BindingType,
    /// The message that refers / responds.
    pub source_id: String,
    /// The message referred / responded to.
    pub target_id: String,
    pub source_span: String,
    pub target_span: String,
    pub confidence: f64,
    pub temporal_relation: String,
}

impl CrossMessageBinding {
    /// The dataclass constructor with defaults; an empty id becomes `bind-<12 hex>` (`__post_init__`).
    pub fn new(
        binding_id: impl Into<String>,
        binding_type: BindingType,
        source_id: impl Into<String>,
        target_id: impl Into<String>,
    ) -> Self {
        let mut binding_id = binding_id.into();
        if binding_id.is_empty() {
            binding_id = format!("bind-{}", &uuid4_hex()[..12]);
        }
        CrossMessageBinding {
            binding_id,
            binding_type,
            source_id: source_id.into(),
            target_id: target_id.into(),
            source_span: String::new(),
            target_span: String::new(),
            confidence: 0.8,
            temporal_relation: String::new(),
        }
    }
}

/// Pronouns and the antecedent category they need (`PRONOUN_PATTERNS`, in Python's dict order).
pub const PRONOUN_PATTERNS: [(&str, &str); 17] = [
    ("he", "person_male"),
    ("him", "person_male"),
    ("his", "person_male"),
    ("she", "person_female"),
    ("her", "person_female"),
    ("hers", "person_female"),
    ("they", "person_plural"),
    ("them", "person_plural"),
    ("their", "person_plural"),
    ("it", "thing"),
    ("this", "thing_near"),
    ("that", "thing_far"),
    ("these", "things_near"),
    ("those", "things_far"),
    ("there", "place"),
    ("here", "place"),
    ("then", "time"),
];

/// `PRONOUN_PATTERNS.get(word)`.
fn pronoun_category(word: &str) -> Option<&'static str> {
    PRONOUN_PATTERNS.iter().find(|(p, _)| *p == word).map(|(_, c)| *c)
}

/// Relative time expressions and their day offsets (`TEMPORAL_MARKERS`, in dict order).
pub const TEMPORAL_MARKERS: [(&str, i64); 9] = [
    ("yesterday", -1),
    ("today", 0),
    ("tomorrow", 1),
    ("last week", -7),
    ("next week", 7),
    ("last month", -30),
    ("next month", 30),
    ("last year", -365),
    ("next year", 365),
];

thread_local! {
    static HEX_IDS: Cell<Option<u64>> = const { Cell::new(None) };
}

/// Test hook: make the ids this module mints count `00000001…`, `00000002…` in their leading hex digits
/// (`Some(0)`), or random again (`None`). `gen_dialogue_linker.py` patches the Python module's `uuid` the same way.
pub fn set_deterministic_hex_ids(start: Option<u64>) {
    HEX_IDS.with(|c| c.set(start));
}

/// `uuid.uuid4().hex`.
fn uuid4_hex() -> String {
    let next = HEX_IDS.with(|c| {
        c.get().map(|n| {
            c.set(Some(n + 1));
            n + 1
        })
    });
    match next {
        Some(n) => format!("{:08x}{}", n, "0".repeat(24)),
        None => uuid4().replace('-', ""),
    }
}

/// Error Python raised from inside the linker (only `IndexError` is reachable).
pub type LinkerError = String;

/// Creates and keeps dialogue aggregators and cross-message bindings.
#[derive(Default)]
pub struct DialogueLinker {
    aggregators: IndexMap<String, DialogueAggregator>,
    bindings: IndexMap<String, CrossMessageBinding>,
    /// message id -> aggregator ids
    message_to_aggregators: IndexMap<String, Vec<String>>,
    /// message id -> ids of the bindings it is the source of
    message_to_bindings: IndexMap<String, Vec<String>>,
}

/// What the linker reads from a node, copied out so no borrow is held.
struct Msg {
    node_id: String,
    content: String,
    speaker_id: String,
    embedding: Option<Vec<f64>>,
    entity_ids: Vec<String>,
}

fn snapshot(messages: &[NodeRef]) -> Vec<Msg> {
    messages
        .iter()
        .map(|m| {
            let n = m.borrow();
            Msg {
                node_id: n.node_id.clone(),
                content: n.content.clone(),
                speaker_id: n.speaker_id(),
                embedding: n.embedding.clone(),
                entity_ids: n.entity_ids.clone(),
            }
        })
        .collect()
}

/// Python truthiness of an optional embedding list.
fn has_embedding(e: &Option<Vec<f64>>) -> bool {
    e.as_ref().is_some_and(|v| !v.is_empty())
}

/// `str.isupper()` of the first character.
fn first_is_upper(s: &str) -> bool {
    s.chars().next().is_some_and(char::is_uppercase)
}

impl DialogueLinker {
    /// `DialogueLinker(storage)`: the Python kept the storage reference but never used it.
    pub fn new<S: NeuralGraphStorage + ?Sized>(_storage: &S) -> Self {
        DialogueLinker::default()
    }

    /// `_aggregators`, keyed by id in creation order.
    pub fn aggregators(&self) -> &IndexMap<String, DialogueAggregator> {
        &self.aggregators
    }

    /// `_bindings`, keyed by id in creation order.
    pub fn bindings(&self) -> &IndexMap<String, CrossMessageBinding> {
        &self.bindings
    }

    /// Main entry point, called after ingestion: build every aggregator and binding for an ordered message
    /// sequence, index them, and return what was created. `Err` where Python raised (a whitespace-only message
    /// answering another speaker: `_is_reaction` indexes the empty `split()`).
    pub fn process_dialogue_sequence(
        &mut self,
        messages: &[NodeRef],
        session_key: &str,
    ) -> Result<(Vec<DialogueAggregator>, Vec<CrossMessageBinding>), LinkerError> {
        let msgs = snapshot(messages);
        let mut aggregators = Vec::new();
        let mut bindings = Vec::new();

        aggregators.extend(self.create_exchange_aggregators(&msgs, session_key)?);
        aggregators.extend(self.create_topic_aggregators(&msgs, session_key));
        bindings.extend(self.create_coreference_bindings(&msgs));
        bindings.extend(self.create_response_bindings(&msgs));
        aggregators.extend(self.create_temporal_anchors(&msgs, session_key));

        for agg in &aggregators {
            // dict assignment: a repeated id keeps its first position and takes the new value
            self.aggregators.insert(agg.aggregator_id.clone(), agg.clone());
            for msg_id in &agg.message_ids {
                self.message_to_aggregators.entry(msg_id.clone()).or_default().push(agg.aggregator_id.clone());
            }
        }
        for binding in &bindings {
            self.bindings.insert(binding.binding_id.clone(), binding.clone());
            self.message_to_bindings.entry(binding.source_id.clone()).or_default().push(binding.binding_id.clone());
        }
        Ok((aggregators, bindings))
    }

    /// Adjacent messages by different speakers form an EXCHANGE.
    fn create_exchange_aggregators(
        &self,
        messages: &[Msg],
        session_key: &str,
    ) -> Result<Vec<DialogueAggregator>, LinkerError> {
        let mut aggregators = Vec::new();
        for pair in messages.windows(2) {
            let (msg_a, msg_b) = (&pair[0], &pair[1]);
            let speaker_a = self.get_speaker(msg_a);
            let speaker_b = self.get_speaker(msg_b);
            if speaker_a == speaker_b {
                continue;
            }
            let is_question = self.is_question(&msg_a.content);
            let is_reaction = self.is_reaction(&msg_b.content)?;
            let topic = self.extract_topic(&msg_a.content, &msg_b.content);
            let mut cohesion = if is_question {
                DialogueLinkingConfig::EXCHANGE_COHESION_QUESTION
            } else {
                DialogueLinkingConfig::EXCHANGE_COHESION_DEFAULT
            };
            if is_question && is_reaction {
                cohesion = f64::min(1.0, cohesion * 1.05);
            }
            let mut agg =
                DialogueAggregator::new(format!("exch-{}", &uuid4_hex()[..8]), AggregatorType::Exchange, session_key);
            agg.message_ids = vec![msg_a.node_id.clone(), msg_b.node_id.clone()];
            agg.topic = topic.clone();
            agg.binding_weights.insert(msg_a.node_id.clone(), 1.0);
            agg.binding_weights.insert(msg_b.node_id.clone(), 1.0);
            agg.cohesion_score = cohesion;
            if has_embedding(&msg_a.embedding) && has_embedding(&msg_b.embedding) {
                let (ea, eb) = (msg_a.embedding.as_ref().unwrap(), msg_b.embedding.as_ref().unwrap());
                agg.embedding = Some(ea.iter().zip(eb).map(|(a, b)| (a + b) / 2.0).collect());
            }
            tracing::debug!(
                "Created EXCHANGE aggregator: {}, speakers={}->{}, is_question={}, cohesion={:.2}, topic={}",
                agg.aggregator_id,
                speaker_a,
                speaker_b,
                if is_question { "True" } else { "False" },
                cohesion,
                topic
            );
            aggregators.push(agg);
        }
        tracing::info!("Created {} EXCHANGE aggregators for session {}", aggregators.len(), session_key);
        Ok(aggregators)
    }

    /// One TOPIC_THREAD per entity mentioned by two or more (embedding-coherent) messages.
    fn create_topic_aggregators(&self, messages: &[Msg], session_key: &str) -> Vec<DialogueAggregator> {
        let mut aggregators = Vec::new();
        let mut msg_by_id: IndexMap<&str, &Msg> = IndexMap::new();
        for m in messages {
            msg_by_id.insert(&m.node_id, m);
        }
        let mut msg_entities: IndexMap<&str, IndexSet<String>> = IndexMap::new();
        for msg in messages {
            let mut entities = self.extract_entities(&msg.content);
            entities.extend(msg.entity_ids.iter().cloned());
            msg_entities.insert(&msg.node_id, entities);
        }
        let mut entity_to_messages: IndexMap<String, Vec<String>> = IndexMap::new();
        for (msg_id, entities) in &msg_entities {
            for entity in entities {
                entity_to_messages.entry(entity.to_lowercase()).or_default().push(msg_id.to_string());
            }
        }

        let mut dropped_count = 0usize;
        for (entity, ids) in &entity_to_messages {
            if ids.len() < DialogueLinkingConfig::TOPIC_MIN_MESSAGES {
                continue;
            }
            let mut msg_ids = ids.clone();
            if DialogueLinkingConfig::DROP_LOW_SIMILARITY_LINKS && msg_ids.len() >= 2 {
                let filtered = self.filter_by_embedding_similarity(
                    &msg_ids,
                    &msg_by_id,
                    DialogueLinkingConfig::SIMILARITY_DROP_THRESHOLD,
                );
                if filtered.len() < DialogueLinkingConfig::TOPIC_MIN_MESSAGES {
                    dropped_count += 1;
                    tracing::debug!(
                        "Dropped low-similarity TOPIC aggregator: {}, original={}, filtered={}",
                        entity,
                        msg_ids.len(),
                        filtered.len()
                    );
                    continue;
                }
                msg_ids = filtered;
            }
            let cohesion = f64::min(
                DialogueLinkingConfig::TOPIC_COHESION_MAX,
                msg_ids.len() as f64 * DialogueLinkingConfig::TOPIC_COHESION_BASE,
            );
            if DialogueLinkingConfig::HIGH_CONFIDENCE_MODE && cohesion < DialogueLinkingConfig::MIN_AGGREGATOR_COHESION
            {
                dropped_count += 1;
                tracing::debug!(
                    "Dropped low-cohesion TOPIC aggregator: {}, cohesion={:.2} < {}",
                    entity,
                    cohesion,
                    DialogueLinkingConfig::MIN_AGGREGATOR_COHESION
                );
                continue;
            }
            let mut agg = DialogueAggregator::new(
                format!("topic-{}", &uuid4_hex()[..8]),
                AggregatorType::TopicThread,
                session_key,
            );
            agg.message_ids = msg_ids;
            agg.topic = entity.clone();
            agg.cohesion_score = cohesion;
            tracing::debug!(
                "Created TOPIC aggregator: {}, topic={}, messages={}, cohesion={:.2}",
                agg.aggregator_id,
                entity,
                agg.message_ids.len(),
                cohesion
            );
            aggregators.push(agg);
        }
        if dropped_count > 0 {
            tracing::info!("Dropped {} low-quality TOPIC aggregators", dropped_count);
        }
        tracing::info!("Created {} TOPIC aggregators for session {}", aggregators.len(), session_key);
        aggregators
    }

    /// Pronoun -> most confident earlier entity of a compatible category, with distance decay.
    fn create_coreference_bindings(&self, messages: &[Msg]) -> Vec<CrossMessageBinding> {
        let mut bindings = Vec::new();
        // (entity, message id, category, message index)
        let mut entity_history: Vec<(String, String, &'static str, usize)> = Vec::new();

        for (msg_idx, msg) in messages.iter().enumerate() {
            for (pronoun, category) in self.find_pronouns(&msg.content) {
                let mut best_match: Option<(String, String, f64)> = None;
                let mut best_confidence = 0.0;
                for (entity, entity_msg_id, entity_category, entity_idx) in entity_history.iter().rev() {
                    if !self.categories_match(category, entity_category) {
                        continue;
                    }
                    let distance = msg_idx - entity_idx;
                    let base_conf = DialogueLinkingConfig::COREF_CONFIDENCE;
                    let confidence = if DialogueLinkingConfig::HIGH_CONFIDENCE_MODE {
                        // float ** int is C pow() in CPython
                        let decay = DialogueLinkingConfig::ENTITY_MENTION_DECAY.powf(distance as f64);
                        let mut c = base_conf * decay;
                        if distance <= 2 {
                            c = f64::max(c, DialogueLinkingConfig::COREF_HIGH_CONFIDENCE);
                        }
                        c
                    } else {
                        base_conf * 0.98f64.powf(distance as f64)
                    };
                    if confidence > best_confidence {
                        best_confidence = confidence;
                        best_match = Some((entity.clone(), entity_msg_id.clone(), confidence));
                    }
                    if !DialogueLinkingConfig::HIGH_CONFIDENCE_MODE {
                        break;
                    }
                }
                let Some((entity, entity_msg_id, confidence)) = best_match else { continue };
                if DialogueLinkingConfig::HIGH_CONFIDENCE_MODE
                    && confidence < DialogueLinkingConfig::MIN_BINDING_CONFIDENCE
                {
                    tracing::debug!(
                        "Dropped low-confidence COREF: '{}' -> '{}', confidence={:.2} < {}",
                        pronoun,
                        entity,
                        confidence,
                        DialogueLinkingConfig::MIN_BINDING_CONFIDENCE
                    );
                    continue;
                }
                let mut binding = CrossMessageBinding::new(
                    format!("coref-{}", &uuid4_hex()[..8]),
                    BindingType::RefersTo,
                    msg.node_id.clone(),
                    entity_msg_id,
                );
                binding.source_span = pronoun.clone();
                binding.target_span = entity.clone();
                binding.confidence = confidence;
                tracing::debug!("Created COREF binding: '{}' -> '{}', confidence={:.2}", pronoun, entity, confidence);
                bindings.push(binding);
            }

            for entity in self.extract_entities(&msg.content) {
                let category = self.categorize_entity(&entity);
                entity_history.push((entity, msg.node_id.clone(), category, msg_idx));
            }
            if entity_history.len() > DialogueLinkingConfig::MAX_COREF_CHAIN_LENGTH * 3 {
                let keep = DialogueLinkingConfig::MAX_COREF_CHAIN_LENGTH * 2;
                entity_history.drain(..entity_history.len() - keep);
            }
        }
        tracing::info!("Created {} COREFERENCE bindings", bindings.len());
        bindings
    }

    /// Every message after the first responds to its predecessor; markers pick the kind and confidence.
    fn create_response_bindings(&self, messages: &[Msg]) -> Vec<CrossMessageBinding> {
        let mut bindings = Vec::new();
        for pair in messages.windows(2) {
            let (previous, current) = (&pair[0], &pair[1]);
            let mut binding_type = BindingType::RespondsTo;
            let mut confidence = DialogueLinkingConfig::RESPONSE_BASE_CONFIDENCE;
            let content_lower = current.content.to_lowercase();
            let has_any = |markers: &[&str]| markers.iter().any(|m| content_lower.contains(m));
            if has_any(&["yes", "no", "yeah", "sure", "thanks", "wow", "that's"]) {
                confidence = DialogueLinkingConfig::RESPONSE_EXPLICIT_CONFIDENCE;
                binding_type = BindingType::RespondsTo;
            }
            if has_any(&["also", "and", "plus", "too"]) {
                binding_type = BindingType::Continues;
                confidence = DialogueLinkingConfig::RESPONSE_CONTINUES_CONFIDENCE;
            }
            if has_any(&["but", "however", "actually", "no,"]) {
                binding_type = BindingType::Contrasts;
                confidence = DialogueLinkingConfig::RESPONSE_CONTRASTS_CONFIDENCE;
            }
            let mut binding = CrossMessageBinding::new(
                format!("resp-{}", &uuid4_hex()[..8]),
                binding_type,
                current.node_id.clone(),
                previous.node_id.clone(),
            );
            binding.confidence = confidence;
            tracing::debug!("Created RESPONSE binding: {}, confidence={:.2}", binding_type.value(), confidence);
            bindings.push(binding);
        }
        tracing::info!("Created {} RESPONSE bindings", bindings.len());
        bindings
    }

    /// One TEMPORAL_ANCHOR per relative time marker, over every message containing it.
    fn create_temporal_anchors(&self, messages: &[Msg], session_key: &str) -> Vec<DialogueAggregator> {
        let mut temporal_messages: IndexMap<&'static str, Vec<String>> = IndexMap::new();
        for msg in messages {
            let content_lower = msg.content.to_lowercase();
            for (marker, _offset) in TEMPORAL_MARKERS {
                if content_lower.contains(marker) {
                    temporal_messages.entry(marker).or_default().push(msg.node_id.clone());
                }
            }
        }
        let mut aggregators = Vec::new();
        for (marker, msg_ids) in temporal_messages {
            let mut agg = DialogueAggregator::new(
                format!("temp-{}", &uuid4_hex()[..8]),
                AggregatorType::TemporalAnchor,
                session_key,
            );
            agg.message_ids = msg_ids;
            agg.temporal_expression = marker.to_string();
            agg.cohesion_score = 0.95;
            aggregators.push(agg);
        }
        aggregators
    }

    // ---- retrieval ----------------------------------------------------------------------------------------

    /// Every message linked to this one: `(linked id, strength, link type)` through aggregators, then bindings it
    /// is the source of, then (as `reverse_<type>`) bindings pointing at it.
    pub fn get_linked_messages(&self, message_id: &str) -> Vec<(String, f64, String)> {
        let mut linked = Vec::new();
        if let Some(agg_ids) = self.message_to_aggregators.get(message_id) {
            for agg_id in agg_ids {
                let Some(agg) = self.aggregators.get(agg_id) else { continue };
                for other_id in &agg.message_ids {
                    if other_id == message_id {
                        continue;
                    }
                    let weight = agg.binding_weights.get(other_id).copied().unwrap_or(0.5);
                    let mut link_strength = weight * agg.cohesion_score;
                    if agg.aggregator_type == AggregatorType::Exchange && agg.message_ids.len() == 2 {
                        link_strength *= DialogueLinkingConfig::ATOMIC_PAIR_BOOST;
                    }
                    linked.push((other_id.clone(), link_strength, agg.aggregator_type.value().to_string()));
                }
            }
        }
        if let Some(binding_ids) = self.message_to_bindings.get(message_id) {
            for binding_id in binding_ids {
                if let Some(b) = self.bindings.get(binding_id) {
                    linked.push((b.target_id.clone(), b.confidence, b.binding_type.value().to_string()));
                }
            }
        }
        for b in self.bindings.values() {
            if b.target_id == message_id {
                linked.push((b.source_id.clone(), b.confidence, format!("reverse_{}", b.binding_type.value())));
            }
        }
        linked
    }

    /// The other half of the message's question/answer exchange, if any.
    pub fn get_exchange_pair(&self, message_id: &str) -> Option<String> {
        for agg_id in self.message_to_aggregators.get(message_id)? {
            let Some(agg) = self.aggregators.get(agg_id) else { continue };
            if agg.aggregator_type == AggregatorType::Exchange && agg.message_ids.len() == 2 {
                if let Some(other) = agg.message_ids.iter().find(|o| *o != message_id) {
                    return Some(other.clone());
                }
            }
        }
        None
    }

    /// Message ids with each one's exchange partner inserted right after it (deduplicated).
    pub fn get_atomic_retrieval_units(&self, message_ids: &[String]) -> Vec<String> {
        let mut seen: IndexSet<String> = IndexSet::new();
        let mut result = Vec::new();
        for msg_id in message_ids {
            if !seen.insert(msg_id.clone()) {
                continue;
            }
            result.push(msg_id.clone());
            if let Some(pair_id) = self.get_exchange_pair(msg_id) {
                if !pair_id.is_empty() && seen.insert(pair_id.clone()) {
                    result.push(pair_id);
                }
            }
        }
        result
    }

    pub fn get_aggregators_for_message(&self, message_id: &str) -> Vec<&DialogueAggregator> {
        self.message_to_aggregators
            .get(message_id)
            .map(|ids| ids.iter().filter_map(|a| self.aggregators.get(a)).collect())
            .unwrap_or_default()
    }

    /// Message ids of every topic thread whose topic contains `topic` (case-insensitive). Python returned
    /// `list(set(ids))`, in hash order; this is first-occurrence order.
    pub fn get_topic_context(&self, topic: &str) -> Vec<String> {
        let needle = topic.to_lowercase();
        let mut ids: IndexSet<String> = IndexSet::new();
        for agg in self.aggregators.values() {
            if agg.aggregator_type == AggregatorType::TopicThread && agg.topic.to_lowercase().contains(&needle) {
                ids.extend(agg.message_ids.iter().cloned());
            }
        }
        ids.into_iter().collect()
    }

    /// The antecedent a pronoun of this message was bound to.
    pub fn resolve_coreference(&self, message_id: &str, pronoun: &str) -> Option<String> {
        let wanted = pronoun.to_lowercase();
        for binding_id in self.message_to_bindings.get(message_id)? {
            if let Some(b) = self.bindings.get(binding_id) {
                if b.binding_type == BindingType::RefersTo && b.source_span.to_lowercase() == wanted {
                    return Some(b.target_span.clone());
                }
            }
        }
        None
    }

    // ---- helpers (public so the golden tests can pin them one by one) -------------------------------------

    /// `_get_speaker`: the node's speaker, else a `"Speaker: text"` prefix within the first 50 characters, else
    /// `"unknown"`.
    fn get_speaker(&self, msg: &Msg) -> String {
        Self::speaker_of(&msg.speaker_id, &msg.content)
    }

    /// `_get_speaker` over its two inputs (`node.speaker_id`, `node.content`).
    pub fn speaker_of(speaker_id: &str, content: &str) -> String {
        if !speaker_id.is_empty() {
            return speaker_id.to_string();
        }
        if py_prefix(content, 50).contains(':') {
            return py_strip(content.split(':').next().unwrap_or("")).to_string();
        }
        "unknown".to_string()
    }

    /// `_is_question`
    pub fn is_question(&self, content: &str) -> bool {
        let content = py_strip(content);
        if content.ends_with('?') {
            return true;
        }
        const STARTERS: [&str; 13] =
            ["what", "when", "where", "who", "why", "how", "is", "are", "do", "does", "can", "could", "would"];
        let lower = content.to_lowercase();
        // `content` is stripped, so a non-empty one always has a first word
        let first_word = py_split_whitespace(&lower).first().map(|s| s.to_string()).unwrap_or_default();
        STARTERS.contains(&first_word.as_str())
    }

    /// `_is_reaction`; `Err` for a non-empty, whitespace-only message (`"".split()[0]` raised `IndexError`).
    pub fn is_reaction(&self, content: &str) -> Result<bool, LinkerError> {
        const REACTIONS: [&str; 12] =
            ["wow", "oh", "nice", "great", "cool", "awesome", "thanks", "yeah", "yes", "no", "sure", "ok"];
        if content.is_empty() {
            return Ok(false);
        }
        let lower = content.to_lowercase();
        let words = py_split_whitespace(&lower);
        let first = words.first().ok_or_else(|| "IndexError: list index out of range".to_string())?;
        Ok(REACTIONS.contains(first))
    }

    /// `_extract_topic`: capitalised words (longer than two characters) both messages share, joined in the first
    /// message's order; else the first such word of the first message; else "".
    pub fn extract_topic(&self, content_a: &str, content_b: &str) -> String {
        let caps = |s: &str| -> IndexSet<String> {
            py_split_whitespace(s)
                .into_iter()
                .filter(|w| first_is_upper(w) && w.chars().count() > 2)
                .map(str::to_string)
                .collect()
        };
        let words_a = caps(content_a);
        let words_b = caps(content_b);
        let shared: Vec<&str> = words_a.iter().filter(|w| words_b.contains(*w)).map(String::as_str).collect();
        if !shared.is_empty() {
            return shared.join(", ");
        }
        for word in py_split_whitespace(content_a) {
            if word.chars().count() > 2 && first_is_upper(word) {
                return word.to_string();
            }
        }
        String::new()
    }

    /// `_extract_entities`: capitalised words (punctuation stripped, longer than two characters) other than a
    /// few function words, in first-occurrence order.
    pub fn extract_entities(&self, content: &str) -> IndexSet<String> {
        const SKIP: [&str; 7] = ["the", "and", "but", "for", "with", "this", "that"];
        let mut entities = IndexSet::new();
        for word in py_split_whitespace(content) {
            let clean = strip_non_word(word);
            if clean.chars().count() > 2 && first_is_upper(&clean) && !SKIP.contains(&clean.to_lowercase().as_str()) {
                entities.insert(clean);
            }
        }
        entities
    }

    /// `_find_pronouns`: `(pronoun, category)` for every pronoun word, in order.
    pub fn find_pronouns(&self, content: &str) -> Vec<(String, &'static str)> {
        let lower = content.to_lowercase();
        py_split_whitespace(&lower)
            .into_iter()
            .filter_map(|word| {
                let clean = strip_non_word(word);
                pronoun_category(&clean).map(|c| (clean, c))
            })
            .collect()
    }

    /// `_categorize_entity`
    pub fn categorize_entity(&self, entity: &str) -> &'static str {
        let lower = entity.to_lowercase();
        if ["city", "street", "park", "tower", "building"].iter().any(|p| lower.contains(p)) {
            return "place";
        }
        if first_is_upper(entity) && entity.chars().count() > 2 {
            return "person";
        }
        "thing"
    }

    /// `_categories_match`
    pub fn categories_match(&self, pronoun_cat: &str, entity_cat: &str) -> bool {
        if pronoun_cat == "thing" {
            return entity_cat == "thing" || entity_cat == "place";
        }
        if pronoun_cat == "place" {
            return entity_cat == "place";
        }
        if pronoun_cat.starts_with("person") {
            return entity_cat == "person";
        }
        true
    }

    /// `_filter_by_embedding_similarity`: drop topic members that are not, on average, similar to the others.
    fn filter_by_embedding_similarity(
        &self,
        msg_ids: &[String],
        msg_by_id: &IndexMap<&str, &Msg>,
        threshold: f64,
    ) -> Vec<String> {
        if msg_ids.len() < 2 {
            return msg_ids.to_vec();
        }
        let mut embeddings: Vec<(&str, &[f64])> = Vec::new();
        let mut valid_ids = Vec::new();
        for msg_id in msg_ids {
            if let Some(msg) = msg_by_id.get(msg_id.as_str()) {
                if let Some(e) = msg.embedding.as_ref().filter(|e| !e.is_empty()) {
                    embeddings.push((msg_id, e));
                    valid_ids.push(msg_id.clone());
                }
            }
        }
        if embeddings.len() < 2 {
            return valid_ids;
        }
        let mut similarities = Vec::new();
        for i in 0..embeddings.len() {
            for j in i + 1..embeddings.len() {
                similarities.push(cosine_similarity(embeddings[i].1, embeddings[j].1));
            }
        }
        let avg_similarity = similarities.iter().fold(0.0, |s, x| s + x) / similarities.len() as f64;
        if avg_similarity < threshold {
            let mut filtered = Vec::new();
            for (msg_id, emb) in &embeddings {
                let sims: Vec<f64> = embeddings
                    .iter()
                    .filter(|(other_id, _)| other_id != msg_id)
                    .map(|(_, other)| cosine_similarity(emb, other))
                    .collect();
                if !sims.is_empty() {
                    let avg = sims.iter().fold(0.0, |s, x| s + x) / sims.len() as f64;
                    if avg >= threshold {
                        filtered.push(msg_id.to_string());
                    }
                }
            }
            return if filtered.is_empty() { vec![embeddings[0].0.to_string()] } else { filtered };
        }
        valid_ids
    }
}

/// `create_dialogue_links(messages, session_key, storage)`: a new linker over one message sequence.
pub fn create_dialogue_links<S: NeuralGraphStorage + ?Sized>(
    messages: &[NodeRef],
    session_key: &str,
    storage: &S,
) -> Result<DialogueLinker, LinkerError> {
    let mut linker = DialogueLinker::new(storage);
    linker.process_dialogue_sequence(messages, session_key)?;
    Ok(linker)
}
