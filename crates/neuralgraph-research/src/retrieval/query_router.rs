//! Query analysis and pre-filtering (port of `retrieval/query_router.py`).
//!
//! [`QueryRouter::analyze`] classifies a question with regular expressions (entity, temporal, list and
//! open-domain patterns) and picks a retrieval strategy; [`FilteredRetriever`] applies the resulting entity or
//! temporal filter to candidate nodes in one of three modes (hard removal, soft penalties, boosts).
//!
//! Every pattern is the Python source pattern, compiled through [`py_regex`] so that `\w`, `\b` and `\s` keep
//! Python's Unicode definitions (Python's `\w` excludes combining marks and the zero-width joiner, which Rust's
//! `\w` includes; LoCoMo text contains both inside emoji sequences).
//!
//! Set iteration: Python kept `known_entities` (and the list behind `rare_terms`) in a `set`, so the order of
//! `QueryAnalysis.entities` when several known names occur, and the order of `rare_terms`, changed from process to
//! process. Here both are insertion-ordered (`IndexSet`); the fixture generator pins Python to the same order by
//! substituting an insertion-ordered set for `set` inside the module.

use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_len, py_split_whitespace, py_strip, py_truthy};
use serde_json::{json, Map, Value};
use std::sync::LazyLock;

use super::data_types::{NodeLayer, NodeRef};
use super::pytext::{py_capitalize, py_findall, py_max, py_min, py_regex, py_search, search_group};
use super::storage::NeuralGraphStorage;

// ============================================================================
// Data types
// ============================================================================

/// Classification of query intent (`QueryType`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum QueryType {
    EntityAttribute,
    EntityAction,
    EntityRelation,
    TemporalWhen,
    TemporalSequence,
    TemporalDuration,
    MultiHop,
    Adversarial,
    OpenDomain,
}

impl QueryType {
    /// The enum value (`QueryType.X.value`).
    pub fn value(self) -> &'static str {
        match self {
            QueryType::EntityAttribute => "entity_attribute",
            QueryType::EntityAction => "entity_action",
            QueryType::EntityRelation => "entity_relation",
            QueryType::TemporalWhen => "temporal_when",
            QueryType::TemporalSequence => "temporal_sequence",
            QueryType::TemporalDuration => "temporal_duration",
            QueryType::MultiHop => "multi_hop",
            QueryType::Adversarial => "adversarial",
            QueryType::OpenDomain => "open_domain",
        }
    }

    /// `QueryType[name.upper()]` for the pattern tables' type names.
    fn from_value(v: &str) -> Option<QueryType> {
        Some(match v {
            "entity_attribute" => QueryType::EntityAttribute,
            "entity_action" => QueryType::EntityAction,
            "entity_relation" => QueryType::EntityRelation,
            "temporal_when" => QueryType::TemporalWhen,
            "temporal_sequence" => QueryType::TemporalSequence,
            "temporal_duration" => QueryType::TemporalDuration,
            "multi_hop" => QueryType::MultiHop,
            "adversarial" => QueryType::Adversarial,
            "open_domain" => QueryType::OpenDomain,
            _ => return None,
        })
    }
}

/// Result of analysing a query (`QueryAnalysis`).
#[derive(Clone, Debug, PartialEq)]
pub struct QueryAnalysis {
    pub query_text: String,
    pub query_type: QueryType,
    pub entities: Vec<String>,
    pub primary_subject: Option<String>,
    pub speaker_filter: Option<String>,
    pub temporal_markers: Vec<String>,
    /// Never set by the router (kept for the dataclass field).
    pub temporal_direction: Option<String>,
    pub has_negation: bool,
    pub confidence: f64,
    pub strategy: String,
    /// `filter_params` dict (insertion ordered).
    pub filter_params: Map<String, Value>,
    pub rare_terms: Vec<String>,
    pub is_open_domain: bool,
    pub open_domain_confidence: f64,
    pub is_list_question: bool,
    pub list_question_type: Option<String>,
    pub suppress_multi_hop: bool,
}

impl QueryAnalysis {
    /// `QueryAnalysis(query_text, query_type)` with the dataclass defaults.
    pub fn new(query_text: impl Into<String>, query_type: QueryType) -> Self {
        QueryAnalysis {
            query_text: query_text.into(),
            query_type,
            entities: Vec::new(),
            primary_subject: None,
            speaker_filter: None,
            temporal_markers: Vec::new(),
            temporal_direction: None,
            has_negation: false,
            confidence: 0.5,
            strategy: "semantic".into(),
            filter_params: Map::new(),
            rare_terms: Vec::new(),
            is_open_domain: false,
            open_domain_confidence: 0.0,
            is_list_question: false,
            list_question_type: None,
            suppress_multi_hop: false,
        }
    }

    /// Every field, in dataclass order (`query_type` as its value).
    pub fn to_json(&self) -> Value {
        json!({
            "query_text": self.query_text,
            "query_type": self.query_type.value(),
            "entities": self.entities,
            "primary_subject": self.primary_subject,
            "speaker_filter": self.speaker_filter,
            "temporal_markers": self.temporal_markers,
            "temporal_direction": self.temporal_direction,
            "has_negation": self.has_negation,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "filter_params": self.filter_params,
            "rare_terms": self.rare_terms,
            "is_open_domain": self.is_open_domain,
            "open_domain_confidence": self.open_domain_confidence,
            "is_list_question": self.is_list_question,
            "list_question_type": self.list_question_type,
            "suppress_multi_hop": self.suppress_multi_hop,
        })
    }
}

/// Soft-filter verdict for one candidate (`SoftFilterResult`): a score multiplier instead of removal.
#[derive(Clone, Debug, PartialEq)]
pub struct SoftFilterResult {
    pub node_id: String,
    pub score_modifier: f64,
    pub match_reason: String,
    pub is_speaker_match: bool,
    pub is_content_match: bool,
    pub is_entity_id_match: bool,
    pub is_fuzzy_match: bool,
    pub boost_applied: f64,
}

impl SoftFilterResult {
    pub fn new(node_id: impl Into<String>, score_modifier: f64, match_reason: impl Into<String>) -> Self {
        SoftFilterResult {
            node_id: node_id.into(),
            score_modifier,
            match_reason: match_reason.into(),
            is_speaker_match: false,
            is_content_match: false,
            is_entity_id_match: false,
            is_fuzzy_match: false,
            boost_applied: 0.0,
        }
    }

    pub fn to_json(&self) -> Value {
        json!({
            "node_id": self.node_id,
            "score_modifier": self.score_modifier,
            "match_reason": self.match_reason,
            "is_speaker_match": self.is_speaker_match,
            "is_content_match": self.is_content_match,
            "is_entity_id_match": self.is_entity_id_match,
            "is_fuzzy_match": self.is_fuzzy_match,
            "boost_applied": self.boost_applied,
        })
    }
}

// ============================================================================
// Fuzzy matching utilities
// ============================================================================

/// Levenshtein distance over code points (`_compute_edit_distance`).
pub fn compute_edit_distance(s1: &str, s2: &str) -> usize {
    let a: Vec<char> = s1.chars().collect();
    let b: Vec<char> = s2.chars().collect();
    edit_distance_chars(&a, &b)
}

fn edit_distance_chars(s1: &[char], s2: &[char]) -> usize {
    if s1.len() < s2.len() {
        return edit_distance_chars(s2, s1);
    }
    if s2.is_empty() {
        return s1.len();
    }
    let mut previous_row: Vec<usize> = (0..=s2.len()).collect();
    for (i, c1) in s1.iter().enumerate() {
        let mut current_row = vec![i + 1];
        for (j, c2) in s2.iter().enumerate() {
            let insertions = previous_row[j + 1] + 1;
            let deletions = current_row[j] + 1;
            let substitutions = previous_row[j] + usize::from(c1 != c2);
            current_row.push(insertions.min(deletions).min(substitutions));
        }
        previous_row = current_row;
    }
    *previous_row.last().expect("non-empty row")
}

/// `_fuzzy_match(query_entity, candidate, threshold)`: substring, shared-prefix, then edit-distance similarity.
pub fn fuzzy_match(query_entity: &str, candidate: &str, threshold: f64) -> (bool, f64) {
    let query_lower = py_strip(&query_entity.to_lowercase()).to_string();
    let candidate_lower = py_strip(&candidate.to_lowercase()).to_string();

    if candidate_lower.contains(query_lower.as_str()) || query_lower.contains(candidate_lower.as_str()) {
        return (true, 1.0);
    }

    let q: Vec<char> = query_lower.chars().collect();
    let c: Vec<char> = candidate_lower.chars().collect();
    let min_prefix_len = 3.min(q.len()).min(c.len());
    if q[..min_prefix_len] == c[..min_prefix_len] {
        let mut shared_len = 0usize;
        for i in 0..q.len().min(c.len()) {
            if q[i] == c[i] {
                shared_len += 1;
            } else {
                break;
            }
        }
        let prefix_ratio = shared_len as f64 / q.len().max(c.len()) as f64;
        if prefix_ratio >= threshold {
            return (true, prefix_ratio);
        }
    }

    let max_len = q.len().max(c.len());
    if max_len == 0 {
        return (false, 0.0);
    }
    let distance = edit_distance_chars(&q, &c);
    let similarity = 1.0 - (distance as f64 / max_len as f64);
    (similarity >= threshold, similarity)
}

/// Common nickname/alias families (`NICKNAME_ALIASES`), canonical name first.
pub const NICKNAME_ALIASES: &[(&str, &[&str])] = &[
    ("william", &["will", "bill", "billy", "willy", "liam"]),
    ("james", &["jim", "jimmy", "jamie"]),
    ("robert", &["rob", "bob", "bobby", "robbie"]),
    ("richard", &["rick", "dick", "ricky", "rich"]),
    ("michael", &["mike", "mikey", "mick"]),
    ("elizabeth", &["liz", "beth", "lizzy", "betty", "eliza"]),
    ("jennifer", &["jen", "jenny"]),
    ("katherine", &["kate", "katie", "kathy", "kat", "kitty"]),
    ("patricia", &["pat", "patty", "tricia"]),
    ("margaret", &["maggie", "meg", "peggy", "marge"]),
    ("caroline", &["carol", "carrie", "lina"]),
    ("catherine", &["cathy", "kate", "katie", "cat"]),
    ("nicholas", &["nick", "nicky"]),
    ("christopher", &["chris", "topher"]),
    ("alexander", &["alex", "xander", "sandy"]),
    ("benjamin", &["ben", "benji", "benny"]),
    ("jonathan", &["jon", "johnny", "nathan"]),
    ("matthew", &["matt", "matty"]),
    ("anthony", &["tony", "ant"]),
    ("daniel", &["dan", "danny"]),
    ("david", &["dave", "davey"]),
    ("joseph", &["joe", "joey"]),
    ("thomas", &["tom", "tommy"]),
    ("edward", &["ed", "eddie", "ted", "teddy"]),
    ("samuel", &["sam", "sammy"]),
    ("steven", &["steve", "stevie"]),
    ("andrew", &["andy", "drew"]),
    ("joshua", &["josh"]),
    ("timothy", &["tim", "timmy"]),
];

/// `_is_alias_match(entity1, entity2)`: both names in the same nickname family → `(true, 0.85)`.
pub fn is_alias_match(entity1: &str, entity2: &str) -> (bool, f64) {
    let e1 = entity1.to_lowercase();
    let e2 = entity2.to_lowercase();
    let (e1, e2) = (py_strip(&e1), py_strip(&e2));
    for (canonical, aliases) in NICKNAME_ALIASES {
        let in_family = |e: &str| e == *canonical || aliases.contains(&e);
        if in_family(e1) && in_family(e2) {
            return (true, 0.85);
        }
    }
    (false, 0.0)
}

static CONTENT_WORDS: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b\w+\b"));

/// `_match_entity`: match a query entity against a node's speaker, entity ids and content, strongest first.
/// `speaker` and `entity_ids` follow Python truthiness (`None`/empty skip the stage).
pub fn match_entity(
    query_entity: &str,
    speaker: Option<&str>,
    content: &str,
    entity_ids: Option<&[String]>,
    fuzzy_threshold: f64,
) -> SoftFilterResult {
    let query_lower = py_strip(&query_entity.to_lowercase()).to_string();
    let mut result = SoftFilterResult::new("", 0.2, "no_match");
    let speaker = speaker.filter(|s| !s.is_empty());
    let entity_ids = entity_ids.filter(|ids| !ids.is_empty());

    // 1. Exact / substring speaker match
    if let Some(speaker) = speaker {
        let speaker_lower = py_strip(&speaker.to_lowercase()).to_string();
        if query_lower == speaker_lower {
            result.score_modifier = 1.0;
            result.match_reason = "exact_speaker_match".into();
            result.is_speaker_match = true;
            return result;
        }
        if speaker_lower.contains(query_lower.as_str()) || query_lower.contains(speaker_lower.as_str()) {
            result.score_modifier = 0.95;
            result.match_reason = "substring_speaker_match".into();
            result.is_speaker_match = true;
            return result;
        }
    }

    // 2. Alias speaker match
    if let Some(speaker) = speaker {
        if is_alias_match(query_entity, speaker).0 {
            result.score_modifier = 0.9;
            result.match_reason = "alias_speaker_match".into();
            result.is_speaker_match = true;
            result.is_fuzzy_match = true;
            return result;
        }
    }

    // 3. Fuzzy speaker match
    if let Some(speaker) = speaker {
        let (is_fuzzy, fuzzy_sim) = fuzzy_match(query_entity, speaker, fuzzy_threshold);
        if is_fuzzy {
            result.score_modifier = 0.75 + (0.15 * fuzzy_sim);
            result.match_reason = format!("fuzzy_speaker_match (sim={fuzzy_sim:.2})");
            result.is_speaker_match = true;
            result.is_fuzzy_match = true;
            return result;
        }
    }

    // 4. Entity id exact / substring match
    if let Some(ids) = entity_ids {
        for eid in ids {
            let eid_lower = eid.to_lowercase();
            if query_lower == eid_lower || eid_lower.contains(query_lower.as_str()) {
                result.score_modifier = 0.85;
                result.match_reason = format!("entity_id_match ({eid})");
                result.is_entity_id_match = true;
                return result;
            }
        }
    }

    // 5. Entity id alias / fuzzy match
    if let Some(ids) = entity_ids {
        for eid in ids {
            if is_alias_match(query_entity, eid).0 {
                result.score_modifier = 0.8;
                result.match_reason = format!("entity_id_alias_match ({eid})");
                result.is_entity_id_match = true;
                result.is_fuzzy_match = true;
                return result;
            }
            let (is_fuzzy, fuzzy_sim) = fuzzy_match(query_entity, eid, fuzzy_threshold);
            if is_fuzzy {
                result.score_modifier = 0.7 + (0.1 * fuzzy_sim);
                result.match_reason = format!("entity_id_fuzzy_match ({eid}, sim={fuzzy_sim:.2})");
                result.is_entity_id_match = true;
                result.is_fuzzy_match = true;
                return result;
            }
        }
    }

    // 6. Content mention (exact)
    let content_lower = content.to_lowercase();
    if content_lower.contains(query_lower.as_str()) {
        result.score_modifier = 0.7;
        result.match_reason = "content_exact_mention".into();
        result.is_content_match = true;
        return result;
    }

    // 7. Content mention (fuzzy), word by word
    if !content_lower.is_empty() {
        for word in py_findall(&CONTENT_WORDS, &content_lower) {
            if py_len(&word) < 3 {
                continue;
            }
            let (is_fuzzy, fuzzy_sim) = fuzzy_match(query_entity, &word, fuzzy_threshold);
            if is_fuzzy && fuzzy_sim >= 0.8 {
                result.score_modifier = 0.5 + (0.2 * fuzzy_sim);
                result.match_reason = format!("content_fuzzy_mention ({word}, sim={fuzzy_sim:.2})");
                result.is_content_match = true;
                result.is_fuzzy_match = true;
                return result;
            }
        }
    }

    // 8. No match
    result.score_modifier = 0.2;
    result.match_reason = "no_match".into();
    result
}

// ============================================================================
// Pattern tables
// ============================================================================

/// `QueryRouter.ENTITY_PATTERNS` (pattern, query type).
pub const ENTITY_PATTERNS: &[(&str, &str)] = &[
    // === POSSESSIVE PATTERNS ===
    (r"(?:what|where|who|how)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_attribute"),
    (r"(\w+)'s\s+(?:favorite|favourite|preferred|best|worst)\s+(\w+)", "entity_attribute"),
    (r"(\w+)'s\s+(?:first|last|new|old|current|previous|former)\s+(\w+)", "entity_attribute"),
    // === DIRECT ENTITY QUESTIONS ===
    (r"(?:what|where|who)\s+(?:did|does|do|is|was)\s+(\w+)\s+", "entity_action"),
    (r"(?:what|how)\s+(?:did|does|do)\s+(\w+)\s+(?:say|think|feel|believe)", "entity_action"),
    (r"(?:what|which)\s+(?:does|did)\s+(\w+)\s+(?:like|love|hate|prefer|enjoy)", "entity_action"),
    (r"(?:where)\s+(?:does|did|is|was)\s+(\w+)\s+(?:live|work|study|stay|go)", "entity_action"),
    (r"(?:how)\s+(?:does|did|is|was)\s+(\w+)\s+(?:feel|doing|feeling)", "entity_action"),
    // === RELATIONSHIP PATTERNS ===
    (r"(?:who)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_relation"),
    (r"(?:who)\s+(?:does|did)\s+(\w+)\s+(?:know|meet|date|marry|love)", "entity_relation"),
    (r"(?:what|who)\s+(?:is|are)\s+(\w+)\s+(?:related|connected|linked)\s+to", "entity_relation"),
    (r"(?:is|are|was|were)\s+(\w+)\s+(?:friends?|colleagues?|partners?)\s+with", "entity_relation"),
    (r"(?:how)\s+(?:does|did)\s+(\w+)\s+(?:know|meet)\s+(\w+)", "entity_relation"),
    // === ATTRIBUTE/PROPERTY PATTERNS ===
    (
        r"(?:what)\s+(?:is|are|was|were)\s+(\w+)'s\s+(?:name|age|job|occupation|profession|hobby|hobbies)",
        "entity_attribute",
    ),
    (r"(?:what)\s+(?:color|colour|size|type|kind|brand)\s+(?:is|was)\s+(\w+)'s", "entity_attribute"),
    (r"(?:how)\s+(?:old|tall|heavy|far|much)\s+(?:is|was)\s+(\w+)", "entity_attribute"),
    (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:look like|sound like|seem like)", "entity_attribute"),
    // === ORIGIN/LOCATION PATTERNS ===
    (r"(?:where)\s+(?:is|was|does|did)\s+(\w+)\s+(?:from|born|raised|grow up)", "entity_attribute"),
    (r"(?:where)\s+(?:does|did)\s+(\w+)\s+(?:come from|originate|hail)", "entity_attribute"),
    (r"(?:what)\s+(?:country|city|town|state|region)\s+(?:is|was)\s+(\w+)\s+from", "entity_attribute"),
    // === WORK/CAREER PATTERNS ===
    (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:do|work as|work at)", "entity_attribute"),
    (r"(?:where)\s+(?:does|did)\s+(\w+)\s+(?:work|study|teach|practice)", "entity_action"),
    (r"(?:what)\s+(?:is|was)\s+(\w+)'s\s+(?:job|occupation|profession|career|role|position)", "entity_attribute"),
    (r"(?:is|was)\s+(\w+)\s+(?:a|an)\s+(?:doctor|teacher|engineer|lawyer|nurse|student)", "entity_attribute"),
    // === PREFERENCE/OPINION PATTERNS ===
    (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:like|love|hate|enjoy|prefer|dislike)", "entity_action"),
    (r"(?:does|did)\s+(\w+)\s+(?:like|love|hate|enjoy|prefer)\s+(\w+)", "entity_action"),
    (r"(?:what)\s+(?:is|are|was|were)\s+(\w+)'s\s+(?:opinion|view|thought|feeling)\s+(?:on|about)", "entity_action"),
    // === ACTION/ACTIVITY PATTERNS ===
    (r"(?:what)\s+(?:did|does|has|had)\s+(\w+)\s+(?:do|done|doing|make|made|buy|bought)", "entity_action"),
    (r"(?:has|had|did|does)\s+(\w+)\s+(?:ever|always|never|often|usually)\s+(\w+)", "entity_action"),
    (r"(?:why)\s+(?:did|does|is|was)\s+(\w+)\s+", "entity_action"),
    (r"(?:how)\s+(?:did|does)\s+(\w+)\s+(?:manage|succeed|fail|handle|deal)", "entity_action"),
    // === COMMUNICATION PATTERNS ===
    (r"(?:what)\s+(?:did|does)\s+(\w+)\s+(?:say|tell|mention|explain|describe|ask)", "entity_action"),
    (r"(?:did|does|has)\s+(\w+)\s+(?:say|tell|mention|talk|speak)\s+about", "entity_action"),
    (r"(?:according to|as per)\s+(\w+)", "entity_action"),
    // === COMPARISON PATTERNS ===
    (r"(?:is|was)\s+(\w+)\s+(?:taller|shorter|older|younger|better|worse)\s+than\s+(\w+)", "entity_relation"),
    (r"(?:who|what)\s+(?:is|was)\s+(?:more|less|most|least)\s+(\w+)\s+(?:than)?\s*(\w+)?", "entity_relation"),
    (r"(?:between|among)\s+(\w+)\s+and\s+(\w+)", "entity_relation"),
    // === EXPERIENCE/EVENT PATTERNS ===
    (r"(?:has|had|did)\s+(\w+)\s+(?:been to|visited|seen|experienced|tried)", "entity_action"),
    (r"(?:what)\s+(?:happened|occurs|occurred)\s+(?:to|with)\s+(\w+)", "entity_action"),
    (r"(?:did|has|had)\s+(\w+)\s+(?:participate|attend|join|enter|win|lose)", "entity_action"),
];

/// `QueryRouter.TEMPORAL_PATTERNS` (pattern, type name).
pub const TEMPORAL_PATTERNS: &[(&str, &str)] = &[
    // === BASIC WHEN PATTERNS ===
    (r"when\s+did\s+(\w+)", "temporal_when"),
    (r"when\s+does\s+(\w+)", "temporal_when"),
    (r"when\s+is\s+(\w+)", "temporal_when"),
    (r"when\s+was\s+(\w+)", "temporal_when"),
    (r"when\s+will\s+(\w+)", "temporal_when"),
    (r"when\s+(?:was|is|will)\s+the\s+(?:last|next|first)\s+time", "temporal_when"),
    // === SEQUENCE PATTERNS ===
    (r"what\s+(?:happened|occurred)\s+(?:after|before|during)\s+", "temporal_sequence"),
    (r"what\s+(?:came|comes)\s+(?:before|after|next)", "temporal_sequence"),
    (r"(?:before|after|since|until)\s+(\w+)\s+(?:did|does|was|were)", "temporal_sequence"),
    (r"(?:following|preceding)\s+the\s+(\w+)", "temporal_sequence"),
    (r"(?:prior to|subsequent to|in the wake of)", "temporal_sequence"),
    // === DURATION PATTERNS ===
    (r"how\s+long\s+(?:did|does|was|were|has|had)", "temporal_duration"),
    (r"how\s+long\s+(?:ago|since|until|before|after)", "temporal_duration"),
    (r"for\s+how\s+(?:long|many|much)\s+(?:did|does|has|had)", "temporal_duration"),
    (r"(?:duration|length)\s+of\s+(?:the|this|that)", "temporal_duration"),
    // === RELATIVE TIME MARKERS ===
    (r"(?:yesterday|today|tomorrow)", "temporal_marker"),
    (r"(?:last|next|this|that)\s+(?:week|month|year|day|night|morning|evening|afternoon)", "temporal_marker"),
    (r"(?:last|next|this|past)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)", "temporal_marker"),
    (
        r"(?:last|next|this|past)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)",
        "temporal_marker",
    ),
    // === AGO PATTERNS ===
    (r"(\d+)\s+(?:days?|weeks?|months?|years?)\s+ago", "temporal_marker"),
    (
        r"(?:a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?|months?|years?)\s+ago",
        "temporal_marker",
    ),
    (r"(?:few|several|couple(?:\s+of)?|many)\s+(?:days?|weeks?|months?|years?)\s+ago", "temporal_marker"),
    (r"(?:recently|lately|just|just now|right now|currently)", "temporal_marker"),
    // === FUTURE PATTERNS ===
    (r"in\s+(\d+)\s+(?:days?|weeks?|months?|years?)", "temporal_marker"),
    (r"(?:soon|later|eventually|someday|one day|in the future)", "temporal_marker"),
    (r"(?:by|until|before)\s+(?:the\s+end\s+of|next)", "temporal_marker"),
    (r"(?:going to|about to|planning to|scheduled to)", "temporal_marker"),
    // === SPECIFIC TIME PATTERNS ===
    (r"(?:at|around|about)\s+(\d{1,2})\s*(?:am|pm|o'clock)", "temporal_marker"),
    (r"(?:in the|during the)\s+(?:morning|afternoon|evening|night)", "temporal_marker"),
    (r"(?:on|during)\s+the\s+(?:weekend|weekday|holiday)", "temporal_marker"),
    // === DATE PATTERNS ===
    (r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", "temporal_marker"),
    (r"\d{4}[/-]\d{2}[/-]\d{2}", "temporal_marker"),
    (
        r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?",
        "temporal_marker",
    ),
    (
        r"\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:january|february|march|april|may|june|july|august|september|october|november|december)",
        "temporal_marker",
    ),
    // === YEAR PATTERNS ===
    (r"(?:in|during|around|circa|by)\s+(?:19|20)\d{2}", "temporal_marker"),
    (r"(?:the\s+)?(?:year\s+)?(?:19|20)\d{2}", "temporal_marker"),
    (r"(?:early|mid|late)\s+(?:19|20)\d{2}s?", "temporal_marker"),
    // === SEASONAL PATTERNS ===
    (r"(?:in|during|last|next|this)\s+(?:spring|summer|fall|autumn|winter)", "temporal_marker"),
    (r"(?:spring|summer|fall|autumn|winter)\s+(?:of\s+)?(?:19|20)\d{2}", "temporal_marker"),
    // === FREQUENCY PATTERNS ===
    (r"how\s+(?:often|frequently|regularly|many times)", "temporal_duration"),
    (r"(?:every|each)\s+(?:day|week|month|year|time|other)", "temporal_marker"),
    (r"(?:daily|weekly|monthly|yearly|annually|hourly)", "temporal_marker"),
    (r"(?:once|twice|thrice)\s+(?:a|per|each)\s+(?:day|week|month|year)", "temporal_marker"),
    (r"(?:always|never|sometimes|often|rarely|seldom|usually|frequently)", "temporal_marker"),
    // === PERIOD PATTERNS ===
    (r"(?:throughout|during|over)\s+the\s+(?:course of|period|time)", "temporal_duration"),
    (r"(?:from|between)\s+(?:19|20)\d{2}\s+(?:to|and|until)\s+(?:19|20)\d{2}", "temporal_duration"),
    (r"(?:from|since)\s+(?:the\s+)?(?:beginning|start|end|middle)", "temporal_duration"),
    // === MILESTONE/EVENT TIME PATTERNS ===
    (
        r"(?:when|what time)\s+(?:did|does|will)\s+(?:the|this|that)\s+(\w+)\s+(?:start|begin|end|finish|happen|occur)",
        "temporal_when",
    ),
    (r"(?:at the time of|during|while)\s+the\s+(\w+)", "temporal_sequence"),
    (
        r"(?:before|after|since|during)\s+(?:the\s+)?(\w+)\s+(?:event|meeting|party|trip|wedding|birthday)",
        "temporal_sequence",
    ),
    // === ELAPSED TIME PATTERNS ===
    (r"how\s+(?:much|many)\s+time\s+(?:has|had|did)\s+(?:passed|elapsed|gone by)", "temporal_duration"),
    (r"(?:time|days?|weeks?|months?|years?)\s+(?:passed|elapsed|since)", "temporal_duration"),
    (
        r"(?:it's been|it has been|it was)\s+(?:\d+|a|an|one|two|three|few|several)\s+(?:days?|weeks?|months?|years?)",
        "temporal_duration",
    ),
];

/// `QueryRouter.NEGATION_WORDS`.
pub const NEGATION_WORDS: &[&str] = &[
    "not",
    "never",
    "no",
    "none",
    "neither",
    "nor",
    "didn't",
    "doesn't",
    "don't",
    "wasn't",
    "weren't",
    "isn't",
    "aren't",
    "hasn't",
    "haven't",
    "hadn't",
    "won't",
    "wouldn't",
    "couldn't",
    "shouldn't",
    "can't",
];

/// `QueryRouter.PERSON_INDICATORS` (declared, never read by the router).
pub const PERSON_INDICATORS: &[&str] = &["who", "person", "someone", "somebody", "they", "he", "she"];

/// `QueryRouter._COMMON_TITLE_WORDS`: words that do not continue a multi-word name.
const COMMON_TITLE_WORDS: &[&str] =
    &["The", "A", "An", "And", "Or", "But", "In", "On", "At", "To", "For", "With", "By"];

/// `_extract_rare_terms`'s `COMMON_WORDS`.
const COMMON_WORDS: &[&str] = &[
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "dare",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "under",
    "again",
    "further",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "and",
    "but",
    "if",
    "or",
    "because",
    "as",
    "until",
    "while",
    "about",
    "against",
    "between",
    "into",
    "through",
    "during",
    "what",
    "which",
    "who",
    "whom",
    "this",
    "that",
    "these",
    "those",
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "i",
    "me",
    "my",
    "myself",
    "we",
    "our",
    "ours",
    "ourselves",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    "say",
    "said",
    "tell",
    "told",
    "ask",
    "asked",
    "go",
    "went",
    "going",
    "get",
    "got",
    "make",
    "made",
    "know",
    "knew",
    "think",
    "thought",
    "take",
    "took",
    "see",
    "saw",
    "come",
    "came",
    "want",
    "wanted",
    "use",
    "used",
    "find",
    "found",
    "give",
    "gave",
    "work",
    "worked",
    "call",
    "called",
    "try",
    "tried",
    "feel",
    "felt",
    "become",
    "became",
    "leave",
    "left",
    "put",
    "mean",
    "meant",
    "keep",
    "kept",
    "let",
    "begin",
    "began",
    "seem",
    "seemed",
    "help",
    "helped",
    "show",
    "showed",
    "hear",
    "heard",
    "play",
    "played",
    "run",
    "ran",
    "move",
    "moved",
    "live",
    "lived",
    "did",
    "does",
    "doing",
    "done",
];

const TECHNICAL_SUFFIXES: &[&str] = &[
    "tion",
    "sion",
    "ment",
    "ness",
    "ity",
    "ology",
    "graphy",
    "metry",
    "scopy",
    "ism",
    "ist",
    "ize",
    "ification",
    "ation",
];

const TEMPORAL_WORDS: &[&str] = &[
    "yesterday",
    "today",
    "tomorrow",
    "morning",
    "afternoon",
    "evening",
    "night",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "last week",
    "next week",
    "last month",
    "next month",
    "last year",
    "next year",
    "ago",
    "later",
    "before",
    "after",
    "during",
    "while",
    "when",
];

const DEFINITIONAL_PATTERNS: &[(&str, f64)] = &[
    (r"^what is (?:a |an |the )?", 0.7),
    (r"^what are ", 0.6),
    (r"^who is ", 0.4),
    (r"^who was ", 0.5),
    (r"^define ", 0.8),
    (r"^explain (?:what|how|why)", 0.7),
    (r"^how does .* work", 0.7),
    (r"^why does ", 0.6),
    (r"^why do ", 0.6),
    (r"^what causes ", 0.8),
    (r"^what makes ", 0.6),
    (r"^what happens when ", 0.6),
    (r"^describe (?:what|how)", 0.6),
];

const FACTUAL_PATTERNS: &[(&str, f64)] = &[
    (r"how many .* (?:are there|exist)", 0.6),
    (r"how much ", 0.4),
    (r"when was .* (?:invented|discovered|founded|created)", 0.7),
    (r"where is .* located", 0.6),
    (r"what country ", 0.6),
    (r"what year ", 0.5),
    (r"capital of", 0.7),
    (r"president of", 0.6),
    (r"population of", 0.7),
    (r"(?:largest|smallest|oldest|youngest|tallest|shortest) .* in the world", 0.7),
];

/// `_detect_open_domain`'s scientific terms. A Python `set`, but only "does any occur" matters.
const SCIENTIFIC_TERMS: &[&str] = &[
    "photosynthesis",
    "evolution",
    "gravity",
    "atom",
    "molecule",
    "electron",
    "proton",
    "neutron",
    "cell",
    "dna",
    "rna",
    "gene",
    "chromosome",
    "protein",
    "enzyme",
    "virus",
    "bacteria",
    "planet",
    "star",
    "galaxy",
    "solar system",
    "orbit",
    "mass",
    "energy",
    "force",
    "momentum",
    "velocity",
    "acceleration",
    "temperature",
    "pressure",
    "volume",
    "density",
    "climate",
    "ecosystem",
    "species",
    "habitat",
    "genetics",
];

const CONVERSATIONAL_MARKERS: &[&str] = &[
    r"\bshe\b",
    r"\bhe\b",
    r"\bthey\b",
    r"\bwe\b",
    r"\byesterday\b",
    r"\blast week\b",
    r"\btoday\b",
    r"\bour\b",
    r"\bmy\b",
    r"\byour\b",
    r"\bthe meeting\b",
    r"\bthe conversation\b",
    r"\btold me\b",
    r"\bsaid\b",
    r"\basked\b",
];

const MULTI_HOP_PATTERNS: &[&str] = &[
    r"the\s+(?:person|one|man|woman)\s+who",
    r"(?:whose|which)\s+\w+\s+(?:is|was|did)",
    r"the\s+\w+\s+that\s+\w+\s+mentioned",
];

const SPEECH_INDICATORS: &[&str] = &[
    "say",
    "said",
    "tell",
    "told",
    "mention",
    "mentioned",
    "talk",
    "talked",
    "speak",
    "spoke",
    "discuss",
    "discussed",
    "explain",
    "explained",
    "describe",
    "described",
];

const PLURAL_PATTERNS: &[&str] = &[
    r"what\s+(?:are\s+)?(?:the\s+)?(\w+s)\b(?!\s+(?:his|her|their|is|was))",
    r"which\s+(\w+s)\b(?!\s+(?:his|her|their|is|was))",
    r"what\s+types?\s+of",
    r"what\s+kinds?\s+of",
    r"how\s+many\s+(\w+)",
];

const AGGREGATION_PATTERNS: &[&str] = &[
    r"\ball\s+(?:the\s+)?(?:of\s+)?",
    r"\bevery\s+",
    r"\beach\s+",
    r"\blist\s+(?:all\s+)?",
    r"\bname\s+(?:all\s+)?",
    r"\bwhat\s+are\s+all\s+",
];

const HAVE_DONE_PATTERNS: &[&str] = &[
    r"(?:has|have)\s+\w+\s+(?:read|watched|seen|done|made|created|written|visited|bought|painted)",
    r"(?:has|have)\s+\w+\s+(?:been\s+to|gone\s+to)",
    r"what\s+(?:has|have)\s+\w+\s+\w+ed\b",
];

const COMMON_PATTERN: &str = r"(?:have|do|did)\s+(?:\w+\s+)?(?:and\s+\w+\s+)?(?:have\s+)?in\s+common";

const MULTI_HOP_INDICATORS: &[&str] =
    &["the person who", "the one who", "whoever", "someone who", "after", "before", "because", "since", "then"];

type Compiled<T> = LazyLock<Vec<(fancy_regex::Regex, T)>>;

fn compile<T: Copy>(table: &[(&str, T)]) -> Vec<(fancy_regex::Regex, T)> {
    table.iter().map(|(p, t)| (py_regex(p), *t)).collect()
}

fn compile_all(table: &[&str]) -> Vec<fancy_regex::Regex> {
    table.iter().map(|p| py_regex(p)).collect()
}

static ENTITY_RES: Compiled<&'static str> = LazyLock::new(|| compile(ENTITY_PATTERNS));
static TEMPORAL_RES: Compiled<&'static str> = LazyLock::new(|| compile(TEMPORAL_PATTERNS));
static DEFINITIONAL_RES: Compiled<f64> = LazyLock::new(|| compile(DEFINITIONAL_PATTERNS));
static FACTUAL_RES: Compiled<f64> = LazyLock::new(|| compile(FACTUAL_PATTERNS));
static CONVERSATIONAL_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| compile_all(CONVERSATIONAL_MARKERS));
static MULTI_HOP_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| compile_all(MULTI_HOP_PATTERNS));
static PLURAL_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| compile_all(PLURAL_PATTERNS));
static AGGREGATION_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| compile_all(AGGREGATION_PATTERNS));
static HAVE_DONE_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| compile_all(HAVE_DONE_PATTERNS));
static COMMON_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(COMMON_PATTERN));
static NON_WORD_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"[^\w]"));
static POSSESSIVE_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"(\w+)'s\s+"));
static QUOTED_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r#""([^"]+)"|\'([^\']+)\'"#));
static INDICATOR_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| {
    compile_all(&[
        r"\b(?:named|called|known as)\s+(\w+)",
        r"\b(?:person|user|member|friend|colleague|partner)\s+(\w+)",
        r"\b(?:mr\.|mrs\.|ms\.|dr\.)\s*(\w+)",
    ])
});
static RARE_WORD_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[a-z]{3,}\b"));
static ACRONYM_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[A-Z]{2,6}\b"));
static QUANTITY_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| {
    py_regex(r"(?i)\b(\d+(?:\.\d+)?)\s*(mg|kg|lb|oz|ml|L|cm|m|km|mph|kph|%|dollars?|euros?|pounds?)\b")
});
static DATE_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"));

// ============================================================================
// QueryRouter
// ============================================================================

/// Analyses queries and routes them to a retrieval strategy (`QueryRouter`).
#[derive(Clone, Debug, Default)]
pub struct QueryRouter {
    /// Known entity names from the conversation (a Python `set`; insertion ordered here, see the module doc).
    pub known_entities: IndexSet<String>,
}

impl QueryRouter {
    pub fn new(known_entities: Option<IndexSet<String>>) -> Self {
        QueryRouter { known_entities: known_entities.unwrap_or_default() }
    }

    /// `update_known_entities`: set union, new names appended.
    pub fn update_known_entities(&mut self, entities: impl IntoIterator<Item = String>) {
        self.known_entities.extend(entities);
    }

    /// `analyze(query)`: extract entities, temporal markers, rare terms; classify; pick the strategy.
    pub fn analyze(&self, query: &str) -> QueryAnalysis {
        let query_lower = py_strip(&query.to_lowercase()).to_string();
        let mut analysis = QueryAnalysis::new(query, QueryType::OpenDomain);
        analysis.confidence = 0.5;
        analysis.strategy = "semantic".into();

        // 1. Negations
        let tokens = py_split_whitespace(&query_lower);
        analysis.has_negation = NEGATION_WORDS.iter().any(|neg| tokens.contains(neg));
        if analysis.has_negation {
            analysis.query_type = QueryType::Adversarial;
            analysis.confidence = 0.7;
        }

        // 2. Entities
        let entities = self.extract_entities(query);
        analysis.entities = entities.clone();

        // 3. Temporal markers
        let temporal_markers = self.extract_temporal_markers(&query_lower);
        analysis.temporal_markers = temporal_markers.clone();

        // 4. Rare terms
        let rare_terms = self.extract_rare_terms(query);
        analysis.rare_terms = rare_terms.clone();

        // 5. Classification
        let (query_type, subject, confidence) = self.classify_query(query, &query_lower, &entities, &temporal_markers);
        if confidence > analysis.confidence {
            analysis.query_type = query_type;
            analysis.confidence = confidence;
        }
        analysis.primary_subject = subject.clone();

        // 6. Speaker filter
        if subject.as_deref().is_some_and(|s| !s.is_empty()) && self.is_asking_about_speech(&query_lower) {
            analysis.speaker_filter = subject;
        }

        // 7. Open-domain detection
        let (is_open, open_conf) =
            self.detect_open_domain(query, &query_lower, &entities, &temporal_markers, analysis.query_type);
        analysis.is_open_domain = is_open;
        analysis.open_domain_confidence = open_conf;
        if analysis.is_open_domain && analysis.open_domain_confidence > 0.7 && entities.is_empty() {
            analysis.query_type = QueryType::OpenDomain;
            analysis.confidence = analysis.open_domain_confidence;
        }

        // 8. Strategy
        let (strategy, params) = self.determine_strategy(&analysis);
        analysis.strategy = strategy;
        analysis.filter_params = params;
        if !rare_terms.is_empty() {
            analysis.filter_params.insert("rare_terms".into(), json!(rare_terms));
            if analysis.strategy == "semantic" && rare_terms.len() >= 2 {
                analysis.strategy = "hybrid_keyword".into();
                tracing::debug!("Switching to hybrid_keyword strategy due to rare terms: {:?}", rare_terms);
            }
        }

        // 9. List questions
        let (is_list, list_type) = self.detect_list_question(query, &query_lower);
        analysis.is_list_question = is_list;
        analysis.list_question_type = list_type.map(str::to_string);
        if is_list {
            analysis.filter_params.insert("is_list_question".into(), Value::Bool(true));
            analysis.filter_params.insert("list_type".into(), json!(list_type));
        }

        // 10. Multi-hop suppression
        analysis.suppress_multi_hop = self.should_suppress_multi_hop(&analysis);
        if analysis.suppress_multi_hop {
            analysis.filter_params.insert("suppress_multi_hop".into(), Value::Bool(true));
        }
        analysis
    }

    /// `_detect_open_domain`: confidence that the question needs world knowledge (`query` and `current_type`
    /// were unused in Python too).
    pub fn detect_open_domain(
        &self,
        _query: &str,
        query_lower: &str,
        entities: &[String],
        temporal_markers: &[String],
        _current_type: QueryType,
    ) -> (bool, f64) {
        let mut confidence = 0.0f64;
        if !entities.is_empty() && entities.len() <= 2 {
            // Few entities might still be open-domain.
        } else if !entities.is_empty() {
            confidence -= 0.2;
        }
        if !temporal_markers.is_empty() {
            confidence -= 0.3;
        }
        for (re, weight) in DEFINITIONAL_RES.iter() {
            if py_search(re, query_lower) {
                confidence = py_max(confidence + weight, 0.0);
                break;
            }
        }
        for (re, weight) in FACTUAL_RES.iter() {
            if py_search(re, query_lower) {
                confidence = py_max(confidence + weight, 0.0);
                break;
            }
        }
        if SCIENTIFIC_TERMS.iter().any(|t| query_lower.contains(t)) {
            confidence += 0.3;
        }
        let has_conversational = CONVERSATIONAL_RES.iter().any(|re| py_search(re, query_lower));
        if !has_conversational {
            confidence += 0.2;
        }
        confidence = py_max(0.0, py_min(1.0, confidence));
        (confidence >= 0.5, confidence)
    }

    /// `_extract_entities`: known names, capitalised words and runs, possessives, quoted text, "named X".
    pub fn extract_entities(&self, query: &str) -> Vec<String> {
        let mut entities: Vec<String> = Vec::new();
        let mut seen_lower: IndexSet<String> = IndexSet::new();
        let query_lower = query.to_lowercase();

        // 1. Known entities (case-insensitive)
        for entity in &self.known_entities {
            let lower = entity.to_lowercase();
            if query_lower.contains(lower.as_str()) && !seen_lower.contains(&lower) {
                entities.push(entity.clone());
                seen_lower.insert(lower);
            }
        }

        // 2. Capitalised words, joining consecutive ones into full names
        let known_lower: IndexSet<String> = self.known_entities.iter().map(|e| e.to_lowercase()).collect();
        let words = py_split_whitespace(query);
        let clean = |w: &str| NON_WORD_RE.replace_all(w, "").into_owned();
        let starts_upper = |w: &str| w.chars().next().is_some_and(char::is_uppercase);
        let mut i = 0;
        while i < words.len() {
            let clean_word = clean(words[i]);
            if starts_upper(&clean_word) {
                let mut name_parts = vec![clean_word];
                let mut j = i + 1;
                while j < words.len() {
                    let next_clean = clean(words[j]);
                    if starts_upper(&next_clean) && !COMMON_TITLE_WORDS.contains(&next_clean.as_str()) {
                        name_parts.push(next_clean);
                        j += 1;
                    } else {
                        break;
                    }
                }
                let full_name = name_parts.join(" ");
                let full_lower = full_name.to_lowercase();
                if !seen_lower.contains(&full_lower) && (i > 0 || known_lower.contains(&full_lower)) {
                    entities.push(full_name);
                    seen_lower.insert(full_lower);
                    for part in name_parts {
                        let part_lower = part.to_lowercase();
                        if !seen_lower.contains(&part_lower) {
                            entities.push(part);
                            seen_lower.insert(part_lower);
                        }
                    }
                }
                i = j;
                continue;
            }
            i += 1;
        }

        // 3. Possessives ("X's job")
        for caps in POSSESSIVE_RE.captures_iter(query) {
            let caps = caps.expect("regex backtrack limit");
            let entity = caps.get(1).expect("group 1").as_str();
            let lower = entity.to_lowercase();
            if !seen_lower.contains(&lower) && !["what", "who", "it", "that", "this"].contains(&lower.as_str()) {
                entities.push(entity.to_string());
                seen_lower.insert(lower);
            }
        }

        // 4. Quoted text
        for caps in QUOTED_RE.captures_iter(query) {
            let caps = caps.expect("regex backtrack limit");
            let quoted = caps.get(1).or_else(|| caps.get(2)).map(|m| m.as_str()).unwrap_or("");
            let lower = quoted.to_lowercase();
            if !quoted.is_empty() && !seen_lower.contains(&lower) {
                entities.push(quoted.to_string());
                seen_lower.insert(lower);
            }
        }

        // 5. Terms after entity indicators ("named X", "friend X", "dr. X")
        for re in INDICATOR_RES.iter() {
            for caps in re.captures_iter(&query_lower) {
                let caps = caps.expect("regex backtrack limit");
                let entity = caps.get(1).expect("group 1").as_str();
                let lower = entity.to_lowercase();
                if !seen_lower.contains(&lower) {
                    entities.push(py_capitalize(entity));
                    seen_lower.insert(lower);
                }
            }
        }
        entities
    }

    /// `_extract_rare_terms`: technical-looking or long words, acronyms and quantities with units,
    /// de-duplicated (first occurrence order; Python's `list(set(...))` order was hash dependent).
    pub fn extract_rare_terms(&self, query: &str) -> Vec<String> {
        let mut rare_terms: Vec<String> = Vec::new();
        let query_lower = query.to_lowercase();

        for word in py_findall(&RARE_WORD_RE, &query_lower) {
            if COMMON_WORDS.contains(&word.as_str()) {
                continue;
            }
            let mut is_rare = false;
            if TECHNICAL_SUFFIXES.iter().any(|s| word.ends_with(s)) {
                is_rare = true;
            }
            // Never true for `[a-z]{3,}` words; kept as in Python.
            if word.contains('-') || word.contains('_') {
                is_rare = true;
            }
            if py_len(&word) > 8 {
                is_rare = true;
            }
            if is_rare {
                rare_terms.push(word);
            }
        }

        rare_terms.extend(py_findall(&ACRONYM_RE, query).into_iter().map(|a| a.to_lowercase()));

        for m in QUANTITY_RE.find_iter(query) {
            rare_terms.push(m.expect("regex backtrack limit").as_str().to_lowercase());
        }

        let unique: IndexSet<String> = rare_terms.into_iter().collect();
        unique.into_iter().collect()
    }

    /// `_extract_temporal_markers`: temporal words present as substrings, then date-like tokens.
    pub fn extract_temporal_markers(&self, query_lower: &str) -> Vec<String> {
        let mut markers: Vec<String> =
            TEMPORAL_WORDS.iter().filter(|w| query_lower.contains(*w)).map(|w| w.to_string()).collect();
        markers.extend(py_findall(&DATE_RE, query_lower));
        markers
    }

    /// `_classify_query` → (type, primary subject, confidence).
    pub fn classify_query(
        &self,
        _query: &str,
        query_lower: &str,
        entities: &[String],
        temporal_markers: &[String],
    ) -> (QueryType, Option<String>, f64) {
        for (re, q_type) in ENTITY_RES.iter() {
            let Some(caps) = re.captures(query_lower).expect("regex backtrack limit") else { continue };
            let mut subject = if re.captures_len() > 1 { caps.get(1).map(|m| m.as_str().to_string()) } else { None };
            if let Some(s) = subject.clone().filter(|s| !s.is_empty()) {
                let s_lower = s.to_lowercase();
                if let Some(e) = entities.iter().find(|e| e.to_lowercase() == s_lower) {
                    subject = Some(e.clone());
                }
            }
            let query_type = QueryType::from_value(q_type).expect("entity pattern type");
            return (query_type, subject, 0.8);
        }

        for (re, q_type) in TEMPORAL_RES.iter() {
            if py_search(re, query_lower) {
                let subject = entities.first().cloned();
                // Every non-"when" type (duration, marker) becomes TEMPORAL_SEQUENCE, as in Python.
                let query_type =
                    if q_type.contains("when") { QueryType::TemporalWhen } else { QueryType::TemporalSequence };
                return (query_type, subject, 0.75);
            }
        }

        if !temporal_markers.is_empty() && !entities.is_empty() {
            return (QueryType::TemporalWhen, Some(entities[0].clone()), 0.6);
        }

        if !entities.is_empty() && ["what", "where", "who", "how"].iter().any(|w| query_lower.contains(w)) {
            return (QueryType::EntityAction, Some(entities[0].clone()), 0.6);
        }

        for re in MULTI_HOP_RES.iter() {
            if py_search(re, query_lower) {
                return (QueryType::MultiHop, entities.first().cloned(), 0.7);
            }
        }

        (QueryType::OpenDomain, entities.first().cloned(), 0.5)
    }

    /// `_is_asking_about_speech`.
    pub fn is_asking_about_speech(&self, query_lower: &str) -> bool {
        SPEECH_INDICATORS.iter().any(|ind| query_lower.contains(ind))
    }

    /// `_detect_list_question` → (is list, "plural" | "aggregation").
    pub fn detect_list_question(&self, _query: &str, query_lower: &str) -> (bool, Option<&'static str>) {
        for re in PLURAL_RES.iter() {
            let Some(group) = search_group(re, query_lower, 1) else { continue };
            // `match.group(1) if match.groups() else ""`
            let word = if re.captures_len() > 1 { group.unwrap_or_default() } else { String::new() };
            if !word.is_empty()
                && word.ends_with('s')
                && !["is", "was", "has", "does", "his", "hers"].contains(&word.as_str())
            {
                return (true, Some("plural"));
            }
        }
        if AGGREGATION_RES.iter().any(|re| py_search(re, query_lower)) {
            return (true, Some("aggregation"));
        }
        if HAVE_DONE_RES.iter().any(|re| py_search(re, query_lower)) {
            return (true, Some("plural"));
        }
        if py_search(&COMMON_RE, query_lower) {
            return (true, Some("aggregation"));
        }
        if query_lower.contains("both") {
            return (true, Some("aggregation"));
        }
        (false, None)
    }

    /// `_should_suppress_multi_hop`.
    pub fn should_suppress_multi_hop(&self, analysis: &QueryAnalysis) -> bool {
        match analysis.query_type {
            QueryType::EntityAttribute => return true,
            QueryType::EntityAction if analysis.confidence >= 0.7 => return true,
            QueryType::TemporalWhen | QueryType::TemporalSequence => return true,
            QueryType::MultiHop => return false,
            _ => {}
        }
        if analysis.confidence < 0.6 {
            return false;
        }
        let query_lower = analysis.query_text.to_lowercase();
        !MULTI_HOP_INDICATORS.iter().any(|ind| query_lower.contains(ind))
    }

    /// `_determine_strategy` → (strategy, filter params).
    pub fn determine_strategy(&self, analysis: &QueryAnalysis) -> (String, Map<String, Value>) {
        let mut params = Map::new();
        let subject = analysis.primary_subject.as_ref().filter(|s| !s.is_empty());
        match analysis.query_type {
            QueryType::EntityAttribute | QueryType::EntityAction | QueryType::EntityRelation => {
                if let Some(subject) = subject {
                    params.insert("entity_filter".into(), json!(subject));
                    params.insert("include_speaker_messages".into(), Value::Bool(true));
                    if let Some(sf) = analysis.speaker_filter.as_ref().filter(|s| !s.is_empty()) {
                        params.insert("speaker_filter".into(), json!(sf));
                    }
                    return ("entity_filter".into(), params);
                }
            }
            _ => {}
        }
        match analysis.query_type {
            QueryType::TemporalWhen | QueryType::TemporalSequence | QueryType::TemporalDuration => {
                params.insert("temporal_markers".into(), json!(analysis.temporal_markers));
                if let Some(subject) = subject {
                    params.insert("entity_filter".into(), json!(subject));
                }
                ("temporal_filter".into(), params)
            }
            QueryType::MultiHop => {
                params.insert("multi_hop".into(), Value::Bool(true));
                params.insert("entities".into(), json!(analysis.entities));
                ("multi_hop".into(), params)
            }
            QueryType::Adversarial => {
                params.insert("verify_negation".into(), Value::Bool(analysis.has_negation));
                params.insert("entities".into(), json!(analysis.entities));
                ("adversarial".into(), params)
            }
            _ => ("semantic".into(), params),
        }
    }
}

// ============================================================================
// FilteredRetriever
// ============================================================================

/// `FilteredRetriever._filter_stats` (`get_filter_statistics` returns a copy).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FilterStats {
    pub total_queries: i64,
    pub entity_queries: i64,
    pub temporal_queries: i64,
    pub candidates_filtered: i64,
    pub candidates_retained: i64,
    pub fallback_triggered: i64,
    pub boost_mode_queries: i64,
    pub avg_boost_factor: f64,
}

impl FilterStats {
    pub fn to_json(&self) -> Value {
        json!({
            "total_queries": self.total_queries,
            "entity_queries": self.entity_queries,
            "temporal_queries": self.temporal_queries,
            "candidates_filtered": self.candidates_filtered,
            "candidates_retained": self.candidates_retained,
            "fallback_triggered": self.fallback_triggered,
            "boost_mode_queries": self.boost_mode_queries,
            "avg_boost_factor": self.avg_boost_factor,
        })
    }
}

/// Node id → filter verdict, in candidate order.
pub type SoftResults = IndexMap<String, SoftFilterResult>;

/// What `get_filtered_candidates` returns: (candidates, analysis, per-node verdicts in soft/boost modes).
pub type FilteredCandidates = (Vec<NodeRef>, QueryAnalysis, Option<SoftResults>);

/// Query-aware candidate filtering in hard, soft or boost mode (`FilteredRetriever`).
pub struct FilteredRetriever<'a> {
    pub storage: &'a dyn NeuralGraphStorage,
    pub router: QueryRouter,
    pub filter_mode: String,
    pub soft_fallback_threshold: f64,
    filter_stats: FilterStats,
}

/// `params.get(key, "")` for a string-valued filter parameter.
fn param_str<'p>(params: &'p Map<String, Value>, key: &str) -> &'p str {
    params.get(key).and_then(Value::as_str).unwrap_or("")
}

/// `params.get("temporal_markers", [])`.
fn param_markers(params: &Map<String, Value>) -> Vec<String> {
    params
        .get("temporal_markers")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

/// `speaker = node.speaker_id if node.metadata else None`.
fn node_speaker(node: &NodeRef) -> Option<String> {
    let n = node.borrow();
    if n.metadata.is_empty() {
        None
    } else {
        Some(n.speaker_id())
    }
}

/// `_match_entity(entity, speaker, node.content or "", node.entity_ids)` for one node.
fn match_node(entity: &str, node: &NodeRef) -> (Option<String>, SoftFilterResult) {
    let speaker = node_speaker(node);
    let n = node.borrow();
    let result = match_entity(entity, speaker.as_deref(), &n.content, Some(&n.entity_ids), 0.7);
    (speaker, result)
}

impl<'a> FilteredRetriever<'a> {
    pub const MODE_HARD: &'static str = "hard";
    pub const MODE_SOFT: &'static str = "soft";
    pub const MODE_BOOST: &'static str = "boost";

    pub const BOOST_EXACT_SPEAKER: f64 = 1.8;
    pub const BOOST_ALIAS_SPEAKER: f64 = 1.6;
    pub const BOOST_FUZZY_SPEAKER: f64 = 1.5;
    pub const BOOST_ENTITY_ID: f64 = 1.4;
    pub const BOOST_CONTENT_EXACT: f64 = 1.25;
    pub const BOOST_CONTENT_FUZZY: f64 = 1.15;
    pub const BOOST_TEMPORAL_MATCH: f64 = 1.3;
    pub const BOOST_NO_MATCH: f64 = 1.0;

    /// `FilteredRetriever(storage, filter_mode="boost", soft_fallback_threshold=0.2)`.
    pub fn new(storage: &'a dyn NeuralGraphStorage, filter_mode: &str, soft_fallback_threshold: f64) -> Self {
        FilteredRetriever {
            storage,
            router: QueryRouter::new(None),
            filter_mode: filter_mode.to_string(),
            soft_fallback_threshold,
            filter_stats: FilterStats::default(),
        }
    }

    /// `update_context(nodes)`: speakers and entity ids become known entities.
    pub fn update_context(&mut self, nodes: &[NodeRef]) {
        let mut entities: IndexSet<String> = IndexSet::new();
        for node in nodes {
            let n = node.borrow();
            if !n.metadata.is_empty() {
                let speaker = n.speaker_id();
                if !speaker.is_empty() {
                    entities.insert(speaker);
                }
            }
            if !n.entity_ids.is_empty() {
                entities.extend(n.entity_ids.iter().cloned());
            }
        }
        self.router.update_known_entities(entities);
    }

    /// `get_filtered_candidates(query, session_key, base_candidates)` (async in Python only for the storage
    /// call, which is synchronous here).
    pub fn get_filtered_candidates(
        &mut self,
        query: &str,
        session_key: &str,
        base_candidates: Option<Vec<NodeRef>>,
    ) -> FilteredCandidates {
        self.filter_stats.total_queries += 1;
        let analysis = self.router.analyze(query);
        let base_candidates = match base_candidates {
            Some(c) => c,
            None => self.get_session_nodes(session_key),
        };

        let mut soft_results: Option<SoftResults> = None;
        let mode = self.filter_mode.clone();
        let params = &analysis.filter_params;
        let filtered: Vec<NodeRef> = match analysis.strategy.as_str() {
            "entity_filter" | "multi_hop" => {
                if analysis.strategy == "entity_filter" {
                    self.filter_stats.entity_queries += 1;
                }
                if mode == Self::MODE_BOOST {
                    self.filter_stats.boost_mode_queries += 1;
                    let (f, r) = self.apply_entity_filter_boost(&base_candidates, params);
                    soft_results = Some(r);
                    f
                } else if mode == Self::MODE_SOFT {
                    let (f, r) = self.apply_entity_filter_soft(&base_candidates, params);
                    soft_results = Some(r);
                    f
                } else {
                    self.apply_entity_filter_hard(&base_candidates, params)
                }
            }
            "temporal_filter" => {
                self.filter_stats.temporal_queries += 1;
                if mode == Self::MODE_BOOST {
                    self.filter_stats.boost_mode_queries += 1;
                    let (f, r) = self.apply_temporal_filter_boost(&base_candidates, params);
                    soft_results = Some(r);
                    f
                } else if mode == Self::MODE_SOFT {
                    let (f, r) = self.apply_temporal_filter_soft(&base_candidates, params);
                    soft_results = Some(r);
                    f
                } else {
                    self.apply_temporal_filter_hard(&base_candidates, params)
                }
            }
            _ => base_candidates.clone(),
        };

        self.filter_stats.candidates_retained += filtered.len() as i64;
        self.filter_stats.candidates_filtered += base_candidates.len() as i64 - filtered.len() as i64;
        (filtered, analysis, soft_results)
    }

    pub fn get_filter_statistics(&self) -> FilterStats {
        self.filter_stats.clone()
    }

    /// `_get_session_nodes`: the session's MESSAGE-layer nodes.
    pub fn get_session_nodes(&self, session_key: &str) -> Vec<NodeRef> {
        self.storage.get_nodes_by_layer(session_key, NodeLayer::Message)
    }

    /// `_apply_entity_filter_soft`: every candidate kept, with a penalty-mode modifier.
    pub fn apply_entity_filter_soft(
        &self,
        candidates: &[NodeRef],
        params: &Map<String, Value>,
    ) -> (Vec<NodeRef>, SoftResults) {
        let entity = param_str(params, "entity_filter");
        let speaker_filter_param = param_str(params, "speaker_filter");
        if entity.is_empty() {
            return (candidates.to_vec(), neutral_results(candidates));
        }
        let mut soft_results = SoftResults::new();
        for node in candidates {
            let (speaker, mut result) = match_node(entity, node);
            let node_id = node.borrow().node_id.clone();
            result.node_id = node_id.clone();
            if let Some(speaker) = speaker.as_deref().filter(|s| !s.is_empty() && !speaker_filter_param.is_empty()) {
                let speaker_match = match_entity(speaker_filter_param, Some(speaker), "", None, 0.7);
                if speaker_match.is_speaker_match {
                    result.score_modifier = py_min(1.5, result.score_modifier * 1.2);
                    result.match_reason.push_str(" + speaker_filter_boost");
                }
            }
            soft_results.insert(node_id, result);
        }
        (candidates.to_vec(), soft_results)
    }

    /// `_apply_entity_filter_hard`: keep weak-or-better matches, best first; top up from the rest when fewer
    /// than 20% (and fewer than 20 nodes) matched; never return an empty list.
    pub fn apply_entity_filter_hard(&mut self, candidates: &[NodeRef], params: &Map<String, Value>) -> Vec<NodeRef> {
        let entity = param_str(params, "entity_filter");
        if entity.is_empty() {
            return candidates.to_vec();
        }
        let mut matches: Vec<(NodeRef, SoftFilterResult)> = Vec::new();
        for node in candidates {
            let (_, mut result) = match_node(entity, node);
            result.node_id = node.borrow().node_id.clone();
            if result.score_modifier >= 0.5 {
                matches.push((node.clone(), result));
            }
        }
        sort_desc_by(&mut matches, |m| m.1.score_modifier);
        let mut filtered: Vec<NodeRef> = matches.into_iter().map(|(n, _)| n).collect();

        let fallback_threshold = py_max(self.soft_fallback_threshold, 0.2);
        let min_candidates = 20usize;
        if (filtered.len() as f64) < candidates.len() as f64 * fallback_threshold && filtered.len() < min_candidates {
            self.filter_stats.fallback_triggered += 1;
            tracing::warn!(
                "Entity filter fallback triggered: only {}/{} matched '{}', adding more candidates",
                filtered.len(),
                candidates.len(),
                entity
            );
            let filtered_ids: IndexSet<String> = filtered.iter().map(|n| n.borrow().node_id.clone()).collect();
            let mut remaining: Vec<(NodeRef, f64)> = Vec::new();
            for node in candidates {
                if filtered_ids.contains(&node.borrow().node_id) {
                    continue;
                }
                let (_, result) = match_node(entity, node);
                remaining.push((node.clone(), result.score_modifier));
            }
            sort_desc_by(&mut remaining, |r| r.1);
            for (node, _) in remaining {
                if filtered.len() >= min_candidates {
                    break;
                }
                filtered.push(node);
            }
        }
        if filtered.is_empty() {
            candidates.to_vec()
        } else {
            filtered
        }
    }

    /// `_apply_temporal_filter_soft`.
    pub fn apply_temporal_filter_soft(
        &self,
        candidates: &[NodeRef],
        params: &Map<String, Value>,
    ) -> (Vec<NodeRef>, SoftResults) {
        let temporal_markers = param_markers(params);
        let entity = param_str(params, "entity_filter");
        let mut soft_results = SoftResults::new();
        for node in candidates {
            let (node_id, content_lower, has_timestamp) = {
                let n = node.borrow();
                let ts = !n.metadata.is_empty() && n.metadata.get("timestamp").is_some_and(py_truthy);
                (n.node_id.clone(), n.content.to_lowercase(), ts)
            };
            let mut score_modifier = 0.3;
            let mut match_reason = "no_temporal_match".to_string();
            let matched: Vec<&String> =
                temporal_markers.iter().filter(|m| content_lower.contains(m.as_str())).collect();
            if !matched.is_empty() {
                let marker_boost = py_min(0.5, 0.15 * matched.len() as f64);
                score_modifier = 0.7 + marker_boost;
                let joined: Vec<&str> = matched.iter().map(|s| s.as_str()).collect();
                match_reason = format!("temporal_marker_match ({})", joined.join(", "));
            }
            if has_timestamp {
                score_modifier = py_max(score_modifier, 0.6);
                if match_reason == "no_temporal_match" {
                    match_reason = "has_timestamp".into();
                }
            }
            if !entity.is_empty() {
                let (_, entity_result) = match_node(entity, node);
                if entity_result.score_modifier >= 0.5 {
                    let combined = (score_modifier + entity_result.score_modifier) / 2.0;
                    if combined > score_modifier {
                        score_modifier = combined;
                        match_reason.push_str(&format!(" + entity_match ({})", entity_result.match_reason));
                    }
                }
            }
            soft_results.insert(node_id.clone(), SoftFilterResult::new(node_id, score_modifier, match_reason));
        }
        (candidates.to_vec(), soft_results)
    }

    /// `_apply_temporal_filter_hard`: keep nodes with a temporal marker or a weak-or-better entity match.
    pub fn apply_temporal_filter_hard(&self, candidates: &[NodeRef], params: &Map<String, Value>) -> Vec<NodeRef> {
        let temporal_markers = param_markers(params);
        let entity = param_str(params, "entity_filter");
        if temporal_markers.is_empty() && entity.is_empty() {
            return candidates.to_vec();
        }
        let mut filtered = Vec::new();
        for node in candidates {
            let content_lower = node.borrow().content.to_lowercase();
            let has_temporal = temporal_markers.iter().any(|m| content_lower.contains(m.as_str()));
            let mut has_entity = true;
            if !entity.is_empty() {
                has_entity = match_node(entity, node).1.score_modifier >= 0.5;
            }
            if has_temporal || has_entity {
                filtered.push(node.clone());
            }
        }
        if filtered.is_empty() {
            candidates.to_vec()
        } else {
            filtered
        }
    }

    /// `_apply_entity_filter_boost`: matches get 1.15–1.8, non-matches stay at 1.0; +0.2 for a speaker-filter
    /// match.
    pub fn apply_entity_filter_boost(
        &mut self,
        candidates: &[NodeRef],
        params: &Map<String, Value>,
    ) -> (Vec<NodeRef>, SoftResults) {
        let entity = param_str(params, "entity_filter");
        let speaker_filter_param = param_str(params, "speaker_filter");
        if entity.is_empty() {
            return (candidates.to_vec(), neutral_results(candidates));
        }
        let mut boost_results = SoftResults::new();
        let mut boosted = 0i64;
        let mut total_boost = 0.0f64;
        for node in candidates {
            let (speaker, mut result) = match_node(entity, node);
            let node_id = node.borrow().node_id.clone();
            result.node_id = node_id.clone();
            let boost = if result.is_speaker_match {
                Some(if result.is_fuzzy_match {
                    if result.score_modifier < 0.9 {
                        Self::BOOST_FUZZY_SPEAKER
                    } else {
                        Self::BOOST_ALIAS_SPEAKER
                    }
                } else {
                    Self::BOOST_EXACT_SPEAKER
                })
            } else if result.is_entity_id_match {
                Some(Self::BOOST_ENTITY_ID)
            } else if result.is_content_match {
                Some(if result.is_fuzzy_match { Self::BOOST_CONTENT_FUZZY } else { Self::BOOST_CONTENT_EXACT })
            } else {
                None
            };
            match boost {
                Some(boost) => {
                    result.score_modifier = boost;
                    result.boost_applied = boost - 1.0;
                    result.match_reason = format!("boost:{}", result.match_reason);
                    boosted += 1;
                    total_boost += boost;
                }
                None => {
                    result.score_modifier = Self::BOOST_NO_MATCH;
                    result.boost_applied = 0.0;
                    result.match_reason = "no_boost".into();
                }
            }
            if let Some(speaker) = speaker.as_deref().filter(|s| !s.is_empty() && !speaker_filter_param.is_empty()) {
                let speaker_match = match_entity(speaker_filter_param, Some(speaker), "", None, 0.7);
                if speaker_match.is_speaker_match {
                    let additional_boost = 0.2;
                    result.score_modifier += additional_boost;
                    result.boost_applied += additional_boost;
                    result.match_reason.push_str(" + speaker_filter_stack");
                }
            }
            boost_results.insert(node_id, result);
        }
        if boosted > 0 {
            self.filter_stats.avg_boost_factor = total_boost / boosted as f64;
        }
        (candidates.to_vec(), boost_results)
    }

    /// `_apply_temporal_filter_boost`: marker matches 1.3 + up to 0.4, timestamped nodes 1.2, stacked with an
    /// entity boost by geometric mean (capped at 2.0).
    pub fn apply_temporal_filter_boost(
        &self,
        candidates: &[NodeRef],
        params: &Map<String, Value>,
    ) -> (Vec<NodeRef>, SoftResults) {
        let temporal_markers = param_markers(params);
        let entity = param_str(params, "entity_filter");
        let mut boost_results = SoftResults::new();
        for node in candidates {
            let (node_id, content_lower, has_timestamp) = {
                let n = node.borrow();
                let ts = !n.metadata.is_empty() && n.metadata.get("timestamp").is_some_and(py_truthy);
                (n.node_id.clone(), n.content.to_lowercase(), ts)
            };
            let mut score_modifier = 1.0;
            let mut match_reason = "no_temporal_boost".to_string();
            let mut boost_applied = 0.0;

            let matched: Vec<&str> =
                temporal_markers.iter().filter(|m| content_lower.contains(m.as_str())).map(|s| s.as_str()).collect();
            if !matched.is_empty() {
                let marker_boost = py_min(0.4, 0.12 * matched.len() as f64);
                score_modifier = Self::BOOST_TEMPORAL_MATCH + marker_boost;
                boost_applied = score_modifier - 1.0;
                let shown: Vec<&str> = matched.iter().take(3).copied().collect();
                match_reason = format!("temporal_boost ({})", shown.join(", "));
            }
            if has_timestamp && score_modifier < Self::BOOST_TEMPORAL_MATCH {
                score_modifier = Self::BOOST_TEMPORAL_MATCH - 0.1;
                boost_applied = score_modifier - 1.0;
                if match_reason == "no_temporal_boost" {
                    match_reason = "timestamp_boost".into();
                }
            }
            if !entity.is_empty() {
                let (_, entity_result) = match_node(entity, node);
                if entity_result.is_speaker_match || entity_result.is_entity_id_match {
                    let entity_boost = if entity_result.is_entity_id_match {
                        Self::BOOST_ENTITY_ID
                    } else {
                        Self::BOOST_EXACT_SPEAKER
                    };
                    let mut combined = (score_modifier * entity_boost).powf(0.5);
                    combined = py_max(py_max(combined, score_modifier), entity_boost);
                    score_modifier = py_min(2.0, combined);
                    boost_applied = score_modifier - 1.0;
                    match_reason.push_str(&format!(" + entity_boost ({})", entity_result.match_reason));
                }
            }
            let mut result = SoftFilterResult::new(node_id.clone(), score_modifier, match_reason);
            result.boost_applied = boost_applied;
            boost_results.insert(node_id, result);
        }
        (candidates.to_vec(), boost_results)
    }
}

/// Every candidate at 1.0 with reason "no_entity_filter".
fn neutral_results(candidates: &[NodeRef]) -> SoftResults {
    candidates
        .iter()
        .map(|n| {
            let id = n.borrow().node_id.clone();
            (id.clone(), SoftFilterResult::new(id, 1.0, "no_entity_filter"))
        })
        .collect()
}

/// `list.sort(key=..., reverse=True)` for float keys: stable, equal keys keep their order.
fn sort_desc_by<T>(items: &mut [T], key: impl Fn(&T) -> f64) {
    items.sort_by(|a, b| key(b).partial_cmp(&key(a)).unwrap_or(std::cmp::Ordering::Equal));
}

/// `create_query_router(known_entities)`.
pub fn create_query_router(known_entities: Option<IndexSet<String>>) -> QueryRouter {
    QueryRouter::new(known_entities)
}

/// `create_filtered_retriever(storage, filter_mode="boost")`.
pub fn create_filtered_retriever<'a>(storage: &'a dyn NeuralGraphStorage, filter_mode: &str) -> FilteredRetriever<'a> {
    FilteredRetriever::new(storage, filter_mode, 0.2)
}
