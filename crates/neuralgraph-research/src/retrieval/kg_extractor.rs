//! LLM-built entity graph of a conversation, Graphiti / HippoRAG style (port of `retrieval/kg_extractor.py`).
//!
//! For every message the model returns entities (with a type) and subject-relation-object triples. The prompt
//! carries the conversation's running entity registry, so mentions resolve to existing nodes ("Mel" -> "melanie")
//! instead of creating duplicates, and first-person references resolve to the speaker. The result is cached per
//! conversation and feeds the KG variant of the mega-search graph channel ([`crate::retrieval::mega_search`]):
//!
//! ```text
//! {"messages": [{"idx": i, "entities": ["melanie", "art show"], "triples": [["melanie", "attended", "art show"]]}],
//!  "registry": {"melanie": {"type": "person", "count": 120}}}
//! ```
//!
//! The model's JSON is untrusted and the Python handled odd shapes by Python's own rules (a string where a list
//! was expected is iterated character by character, `str()` of a list is its repr); [`py_iter`] and
//! [`py_str_value`] reproduce that. Where Python raised outside its `try` (a number where a list was expected),
//! the functions return `Err` with the Python message. The binary is `src/bin/kg-extractor.rs`.

use crate::llm_backend::{GenerateOptions, Llm};
use indexmap::IndexMap;
use neuralgraph_memory::difflib::get_close_matches;
use neuralgraph_memory::pycompat::{dumps, py_float_repr, py_prefix, py_repr_str, JsonFormat};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::path::Path;
use std::sync::LazyLock;

/// The extraction prompt (`PROMPT`), a `str.format` template.
pub const PROMPT: &str = r#"You are building a knowledge graph from a chat between {speakers}.
Extract from the MESSAGE below:
1. entities: people, places, organisations, pets, objects, events, activities, media titles, dates, health conditions, jobs, hobbies. Short canonical names, lowercase, singular. Resolve first person ("I", "my", "me") to the speaker "{speaker}". Resolve nicknames and pronouns to the KNOWN ENTITIES list when they refer to the same thing; reuse the exact known name.
2. triples: [subject, relation, object] facts stated or clearly implied by the message. Subject and object must be entities from your list. Relation is a short verb phrase (has_pet, lives_in, attended, likes, works_as, plans, bought, read, moved_from, ...).

KNOWN ENTITIES (reuse these names when they match): {registry}

SPEAKER: {speaker}
MESSAGE: {text}

Return ONLY JSON: {{"entities": [{{"name": "...", "type": "..."}}], "triples": [["subject", "relation", "object"]]}}"#;

static JSON_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)\{.*\}").unwrap());
static NON_NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9 '\-]").unwrap());
static WS_RUN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]+").unwrap());

/// `str.format(**fields)` for templates whose fields are plain names: one pass, so braces inside the values
/// stay literal.
pub fn format_template(template: &str, fields: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(i) = rest.find(['{', '}']) {
        out.push_str(&rest[..i]);
        let tail = &rest[i..];
        if tail.starts_with("{{") || tail.starts_with("}}") {
            out.push_str(&tail[..1]);
            rest = &tail[2..];
        } else if tail.starts_with('{') {
            let end = tail.find('}').expect("unclosed field in template");
            let name = &tail[1..end];
            let value = fields.iter().find(|(k, _)| *k == name).unwrap_or_else(|| panic!("KeyError: '{name}'")).1;
            out.push_str(value);
            rest = &tail[end + 1..];
        } else {
            panic!("Single '}}' encountered in format string");
        }
    }
    out.push_str(rest);
    out
}

/// `repr(v)` of a JSON value as the Python object `json.loads` made of it.
fn py_repr_value(v: &Value) -> String {
    match v {
        Value::String(s) => py_repr_str(s),
        other => py_str_value(other),
    }
}

/// `str(v)` of a JSON value as the Python object `json.loads` made of it (lists and dicts print their repr).
pub fn py_str_value(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => i.to_string(),
            (_, Some(u)) => u.to_string(),
            _ => py_float_repr(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => s.clone(),
        Value::Array(a) => format!("[{}]", a.iter().map(py_repr_value).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter().map(|(k, v)| format!("{}: {}", py_repr_str(k), py_repr_value(v))).collect::<Vec<_>>().join(", ")
        ),
    }
}

/// Python's type name of a JSON value (for `TypeError` messages).
fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// `iter(v)`: list items, the characters of a string, the keys of a dict; `TypeError` otherwise.
pub fn py_iter(v: &Value) -> Result<Vec<Value>, String> {
    match v {
        Value::Array(a) => Ok(a.clone()),
        Value::String(s) => Ok(s.chars().map(|c| json!(c.to_string())).collect()),
        Value::Object(o) => Ok(o.keys().map(|k| json!(k)).collect()),
        other => Err(format!("TypeError: '{}' object is not iterable", py_type_name(other))),
    }
}

fn truthy(v: Option<&Value>) -> Option<&Value> {
    v.filter(|v| neuralgraph_memory::pycompat::py_truthy(v))
}

/// `_norm`: lowercase, anything but `[a-z0-9 '-]` to spaces, collapse spaces, drop a trailing `'s`.
pub fn norm(name: &Value) -> String {
    let lower = py_str_value(name).to_lowercase();
    let spaced = NON_NAME.replace_all(&lower, " ");
    let stripped = neuralgraph_memory::pycompat::py_strip(&spaced);
    let mut out = WS_RUN.replace_all(stripped, " ").into_owned();
    if out.ends_with("'s") {
        out.truncate(out.len() - 2);
    }
    out
}

/// One registry entry (`{"type": ..., "count": ...}`).
#[derive(Clone, Debug, PartialEq)]
pub struct RegistryEntry {
    pub entity_type: String,
    pub count: i64,
}

/// The running entity registry, in insertion order.
pub type Registry = IndexMap<String, RegistryEntry>;

/// `_resolve`: a mention mapped onto a registry entity when it clearly is one (first person -> speaker, exact,
/// person-name prefix either way, then a 0.88 fuzzy match); otherwise its normalised form ("" for nothing).
pub fn resolve(name: &Value, registry: &Registry, speaker: &str) -> String {
    let n = norm(name);
    if n.is_empty() {
        return String::new();
    }
    if ["i", "me", "my", "myself", "mine"].contains(&n.as_str()) {
        return speaker.to_lowercase();
    }
    if registry.contains_key(&n) {
        return n;
    }
    let nlen = n.chars().count();
    for (known, meta) in registry {
        if meta.entity_type == "person"
            && (known.starts_with(&n) || n.starts_with(known.as_str()))
            && nlen.min(known.chars().count()) >= 3
        {
            return known.clone();
        }
    }
    get_close_matches(&n, registry.keys().map(String::as_str), 1, 0.88).into_iter().next().unwrap_or(n)
}

/// What `_parse` keeps of a model response: entity objects and three-element triples.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Parsed {
    /// Each entity as a dict (`{"name": str(e), "type": ""}` for non-dict items).
    pub entities: Vec<Map<String, Value>>,
    pub triples: Vec<Vec<Value>>,
}

/// `_parse`: the outermost `{...}` of the response as JSON; nothing when absent or invalid. `Err` where Python
/// raised past its `try` (e.g. `"entities": 5`).
pub fn parse(resp: &str) -> Result<Parsed, String> {
    let Some(m) = JSON_RE.find(resp) else { return Ok(Parsed::default()) };
    let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(m.as_str()) else { return Ok(Parsed::default()) };
    let mut parsed = Parsed::default();
    if let Some(ents) = truthy(obj.get("entities")) {
        for e in py_iter(ents)? {
            parsed.entities.push(match e {
                Value::Object(o) => o,
                other => {
                    let mut o = Map::new();
                    o.insert("name".into(), json!(py_str_value(&other)));
                    o.insert("type".into(), json!(""));
                    o
                }
            });
        }
    }
    if let Some(trips) = truthy(obj.get("triples")) {
        for t in py_iter(trips)? {
            if let Value::Array(a) = t {
                if a.len() == 3 {
                    parsed.triples.push(a);
                }
            }
        }
    }
    Ok(parsed)
}

/// `{"messages": ..., "registry": ...}` as the Python dict (and cache file) has it.
fn result_json(out_msgs: &[Value], registry: &Registry) -> Value {
    let mut reg = Map::new();
    for (k, v) in registry {
        reg.insert(k.clone(), json!({"type": v.entity_type, "count": v.count}));
    }
    json!({"messages": out_msgs, "registry": reg})
}

/// The prompt for one message given the registry so far (`top` entities by count, at most `max_registry`).
pub fn build_prompt(
    speakers: &[String],
    speaker: &str,
    text: &str,
    registry: &Registry,
    max_registry: usize,
) -> String {
    let mut top: Vec<(&String, &RegistryEntry)> = registry.iter().collect();
    top.sort_by_key(|(_, v)| -v.count);
    top.truncate(max_registry);
    let reg_txt = top
        .iter()
        .map(|(k, v)| if v.entity_type.is_empty() { k.to_string() } else { format!("{k} ({})", v.entity_type) })
        .collect::<Vec<_>>()
        .join(", ");
    format_template(
        PROMPT,
        &[
            ("speakers", &speakers.join(" and ")),
            ("speaker", speaker),
            ("text", py_prefix(text, 800)),
            ("registry", &reg_txt),
        ],
    )
}

/// One conversation message (`{"speaker": ..., "text": ...}`).
#[derive(Clone, Debug, PartialEq)]
pub struct KgMessage {
    pub speaker: String,
    pub text: String,
}

/// `extract_conversation`: entities and triples for every message, in order (sequential: the registry grows).
/// A cache file with as many messages is returned as is; otherwise the result is written there.
pub async fn extract_conversation(
    llm: &dyn Llm,
    messages: &[KgMessage],
    speakers: &[String],
    cache_path: Option<&Path>,
    max_registry: usize,
    log_every: usize,
) -> Result<Value, String> {
    if let Some(path) = cache_path.filter(|p| p.exists()) {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let cached: Value = serde_json::from_str(&text).map_err(|e| format!("JSONDecodeError: {e}"))?;
        if !cached.is_object() {
            return Err(format!("AttributeError: '{}' object has no attribute 'get'", py_type_name(&cached)));
        }
        let n = match cached.get("messages") {
            Some(Value::Array(a)) => a.len(),
            Some(other) => py_iter(other)?.len(),
            None => 0,
        };
        if n == messages.len() {
            return Ok(cached);
        }
    }
    let mut registry: Registry = IndexMap::new();
    for sp in speakers {
        registry.insert(sp.to_lowercase(), RegistryEntry { entity_type: "person".into(), count: 1 });
    }
    let opts = GenerateOptions::default().temperature(0.0).max_tokens(300).timeout(60.0);
    let mut out_msgs = Vec::new();
    for (i, msg) in messages.iter().enumerate() {
        let speaker = msg.speaker.as_str();
        let prompt = build_prompt(speakers, speaker, &msg.text, &registry, max_registry);
        let resp = llm.generate(&prompt, &opts).await.unwrap_or_default();
        let parsed = parse(&resp)?;
        let mut ents: Vec<String> = Vec::new();
        let mut types: IndexMap<String, String> = IndexMap::new();
        for e in &parsed.entities {
            let name = resolve(e.get("name").unwrap_or(&json!("")), &registry, speaker);
            if name.is_empty() || name.chars().count() > 60 {
                continue;
            }
            let mut t = norm(e.get("type").unwrap_or(&json!("")));
            if t.is_empty() {
                t = registry.get(&name).map(|r| r.entity_type.clone()).unwrap_or_default();
            }
            types.insert(name.clone(), t);
            if !ents.contains(&name) {
                ents.push(name);
            }
        }
        let mut triples = Vec::new();
        for t in &parsed.triples {
            let (s2, o2) = (resolve(&t[0], &registry, speaker), resolve(&t[2], &registry, speaker));
            if !s2.is_empty() && !o2.is_empty() && s2 != o2 {
                triples.push(json!([s2, norm(&t[1]).replace(' ', "_"), o2]));
                for x in [s2, o2] {
                    if !ents.contains(&x) {
                        ents.push(x);
                    }
                }
            }
        }
        let sp_lower = speaker.to_lowercase();
        if !ents.contains(&sp_lower) {
            ents.push(sp_lower);
        }
        for name in &ents {
            let typed = types.get(name).cloned().unwrap_or_default();
            let reg =
                registry.entry(name.clone()).or_insert_with(|| RegistryEntry { entity_type: typed.clone(), count: 0 });
            reg.count += 1;
            if reg.entity_type.is_empty() && !typed.is_empty() {
                reg.entity_type = typed;
            }
        }
        out_msgs.push(json!({"idx": i, "entities": ents, "triples": triples}));
        if log_every > 0 && (i + 1) % log_every == 0 {
            println!("    kg: {}/{} messages, {} entities", i + 1, messages.len(), registry.len());
        }
    }
    let result = result_json(&out_msgs, &registry);
    if let Some(path) = cache_path {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        std::fs::write(path, dumps(&result, &JsonFormat::python_default()))
            .map_err(|e| format!("{}: {e}", path.display()))?;
    }
    Ok(result)
}

/// `flatten(conv)` of `_main`: every session's messages in order.
pub fn flatten(conv: &Value) -> Vec<KgMessage> {
    let c = conv.get("conversation").unwrap_or(conv);
    let mut msgs = Vec::new();
    let mut i = 1;
    while let Some(session) = c.get(format!("session_{i}")) {
        for m in session.as_array().map(Vec::as_slice).unwrap_or_default() {
            msgs.push(KgMessage {
                speaker: m.get("speaker").and_then(Value::as_str).unwrap_or("Unknown").to_string(),
                text: m.get("text").and_then(Value::as_str).unwrap_or("").to_string(),
            });
        }
        i += 1;
    }
    msgs
}
