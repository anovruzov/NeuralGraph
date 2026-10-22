//! Per-speaker profiles: raw messages plus LLM-extracted facts (port of `retrieval/speaker_profiles.py`).
//!
//! Python bug kept: `SpeakerProfile.query` imported `query_profile_with_llm` from `NeuralGraph.llm_profile_query`,
//! a module that does not exist (it lives at `NeuralGraph.research.retrieval.llm_profile_query`). The import ran
//! inside `query`, so every profile query — and every `query_single_hop` that found a target speaker — raised
//! `ModuleNotFoundError`; the runner did not catch it, so `USE_SPEAKER_PROFILES=1` aborted the run at the first
//! single-hop question naming a speaker. Here those calls return that error as `Err`. The working query
//! function is [`super::llm_profile_query::query_profile_with_llm`].

use async_trait::async_trait;
use indexmap::IndexMap;
use serde_json::{json, Map, Value};

use super::llm_profile_extractor::extract_speaker_facts_llm;
use crate::llm_backend::Llm;

/// The `ModuleNotFoundError` message every profile query raised.
pub const PROFILE_QUERY_IMPORT_ERROR: &str = "No module named 'NeuralGraph.llm_profile_query'";

/// The optional extractor of `add_message`: `async fn(speaker_name, text) -> list[str]` (an `Err` is an
/// exception, which propagated out of `add_message`).
#[async_trait(?Send)]
pub trait FactExtractor {
    async fn extract(&self, speaker_name: &str, text: &str) -> Result<Vec<String>, String>;
}

/// [`extract_speaker_facts_llm`] as an extractor.
pub struct LlmFactExtractor<'a>(pub &'a dyn Llm);

#[async_trait(?Send)]
impl FactExtractor for LlmFactExtractor<'_> {
    async fn extract(&self, speaker_name: &str, text: &str) -> Result<Vec<String>, String> {
        Ok(extract_speaker_facts_llm(self.0, speaker_name, text).await)
    }
}

/// One speaker's profile (`SpeakerProfile`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SpeakerProfile {
    pub name: String,
    pub all_messages: Vec<String>,
    pub extracted_facts: Vec<String>,
}

impl SpeakerProfile {
    pub fn new(name: impl Into<String>) -> Self {
        SpeakerProfile { name: name.into(), all_messages: Vec::new(), extracted_facts: Vec::new() }
    }

    /// `add_message(text, llm_extractor=None)`: keep the text; with an extractor, append its facts.
    pub async fn add_message(&mut self, text: &str, llm_extractor: Option<&dyn FactExtractor>) -> Result<(), String> {
        self.all_messages.push(text.to_string());
        if let Some(extractor) = llm_extractor {
            let facts = extractor.extract(&self.name, text).await?;
            self.extracted_facts.extend(facts);
        }
        Ok(())
    }

    /// `query(session, question, gold_answer=None)`: always the import error (see the module doc).
    pub async fn query(
        &self,
        _llm: &dyn Llm,
        _question: &str,
        _gold_answer: Option<&Value>,
    ) -> Result<Map<String, Value>, String> {
        Err(PROFILE_QUERY_IMPORT_ERROR.to_string())
    }

    /// `to_dict()`.
    pub fn to_dict(&self) -> Map<String, Value> {
        let v = json!({
            "name": self.name,
            "all_messages": self.all_messages,
            "extracted_facts": self.extracted_facts,
            "message_count": self.all_messages.len(),
            "fact_count": self.extracted_facts.len(),
        });
        v.as_object().expect("object").clone()
    }
}

/// Profiles for every speaker of a conversation (`UniversalSpeakerProfiler`), in first-seen order.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct UniversalSpeakerProfiler {
    pub profiles: IndexMap<String, SpeakerProfile>,
}

impl UniversalSpeakerProfiler {
    pub fn new() -> Self {
        Self::default()
    }

    /// `add_message(speaker, text, llm_extractor=None)`.
    pub async fn add_message(
        &mut self,
        speaker: &str,
        text: &str,
        llm_extractor: Option<&dyn FactExtractor>,
    ) -> Result<(), String> {
        let profile = self.profiles.entry(speaker.to_string()).or_insert_with(|| SpeakerProfile::new(speaker));
        profile.add_message(text, llm_extractor).await
    }

    /// `query_single_hop(session, question, gold_answer=None)`: the first speaker whose name occurs in the
    /// question (case-insensitive) is queried — which raised the import error; with no such speaker the
    /// "Could not determine target speaker" result.
    pub async fn query_single_hop(
        &self,
        llm: &dyn Llm,
        question: &str,
        gold_answer: Option<&Value>,
    ) -> Result<Map<String, Value>, String> {
        let question_lower = question.to_lowercase();
        let target = self.profiles.keys().find(|name| question_lower.contains(name.to_lowercase().as_str()));
        let Some(target) = target else {
            let v = json!({
                "speaker": null,
                "found": false,
                "answer": null,
                "confidence": 0.0,
                "source": null,
                "error": "Could not determine target speaker",
            });
            return Ok(v.as_object().expect("object").clone());
        };
        let mut result = self.profiles[target].query(llm, question, gold_answer).await?;
        result.insert("source".into(), json!("llm_profile_query"));
        result.insert("speaker".into(), json!(target));
        Ok(result)
    }

    /// `get_all_profiles()`.
    pub fn get_all_profiles(&self) -> Map<String, Value> {
        self.profiles.iter().map(|(name, p)| (name.clone(), Value::Object(p.to_dict()))).collect()
    }
}

/// The module's `__main__` demo. In Python it called the async methods without `await` (the coroutines never
/// ran) and then `query_single_hop` without its `question` argument, so it stopped with this `TypeError`.
pub fn run_example() -> Result<(), String> {
    Err("UniversalSpeakerProfiler.query_single_hop() missing 1 required positional argument: 'question'".into())
}
