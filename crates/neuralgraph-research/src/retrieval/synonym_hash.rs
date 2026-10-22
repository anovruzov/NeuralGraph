//! Port of `retrieval/synonym_hash.py`: the WordNet thesaurus (117K entries) as a word -> synonyms hash, plus a
//! few conversational/gaming additions. Tesseract expands query keywords through it.
//!
//! The thesaurus is data, not code: `data/en_thesaurus.jsonl` in this crate (moved from the Python package), or
//! the file named by `NG_THESAURUS`. It is loaded once per process on first use, like the Python module-level
//! cache (`functools.lru_cache` was imported there but never applied; the global did the caching). A missing or
//! unreadable file yields an empty table, as in Python, and is logged.
//!
//! Loading semantics are Python's exactly: words and synonyms are lower-cased with `_` turned into spaces,
//! one-character synonyms are dropped, entries for the same word merge, and every synonym also maps back to the
//! word (bidirectional lookup). Python returned `set`s; here they are insertion-ordered [`IndexSet`]s. Nothing
//! downstream depended on the order (Tesseract only tests membership and counts).
//!
//! Not ported: the `if __name__ == "__main__"` block, a demo that printed a few lookups.

use indexmap::{IndexMap, IndexSet};
use serde_json::Value;
use std::path::PathBuf;
use std::sync::OnceLock;

/// word -> synonyms.
pub type SynonymHash = IndexMap<String, IndexSet<String>>;

static SYNONYM_HASH: OnceLock<SynonymHash> = OnceLock::new();

/// `THESAURUS_PATH`: `$NG_THESAURUS`, else `data/en_thesaurus.jsonl` of this crate.
pub fn thesaurus_path() -> PathBuf {
    match std::env::var_os("NG_THESAURUS") {
        Some(p) if !p.is_empty() => PathBuf::from(p),
        _ => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("data").join("en_thesaurus.jsonl"),
    }
}

/// `s.lower().replace('_', ' ')`.
fn normalize(s: &str) -> String {
    s.to_lowercase().replace('_', " ")
}

/// Parse thesaurus lines into the hash. Lines that are not JSON are skipped (`JSONDecodeError: continue`); any
/// other malformed entry (not an object, a non-string word or synonym) raised inside Python's outer `try`, which
/// stopped loading and kept what was loaded so far, so it stops here too.
pub fn build_synonym_hash<'a>(lines: impl IntoIterator<Item = &'a str>) -> SynonymHash {
    let mut hash: SynonymHash = IndexMap::new();
    for line in lines {
        let Ok(entry) = serde_json::from_str::<Value>(neuralgraph_memory::pycompat::py_strip(line)) else {
            continue;
        };
        let Value::Object(entry) = entry else {
            tracing::error!("Error loading thesaurus: entry is not an object");
            break;
        };
        let word = match entry.get("word") {
            None => String::new(),
            Some(Value::String(w)) => normalize(w),
            Some(_) => {
                tracing::error!("Error loading thesaurus: word is not a string");
                break;
            }
        };
        let synonyms = entry.get("synonyms");
        let synonyms_truthy = synonyms.is_some_and(neuralgraph_memory::pycompat::py_truthy);
        if word.is_empty() || !synonyms_truthy {
            continue;
        }
        let items: Vec<Value> = match synonyms {
            Some(Value::Array(items)) => items.clone(),
            // a string iterates as one-character strings, all of which the length filter drops
            Some(Value::String(_)) => Vec::new(),
            _ => {
                tracing::error!("Error loading thesaurus: synonyms is not a list");
                break;
            }
        };
        let mut normalized: IndexSet<String> = IndexSet::new();
        let mut malformed = false;
        for s in &items {
            match s {
                Value::String(s) if s.chars().count() > 1 => {
                    normalized.insert(normalize(s));
                }
                Value::String(_) | Value::Null => {}
                v if !neuralgraph_memory::pycompat::py_truthy(v) => {}
                _ => malformed = true,
            }
        }
        if malformed {
            tracing::error!("Error loading thesaurus: synonym is not a string");
            break;
        }
        hash.entry(word.clone()).or_default().extend(normalized.iter().cloned());
        for syn in normalized {
            hash.entry(syn).or_default().insert(word.clone());
        }
    }
    hash
}

/// `_load_thesaurus()`: the process-wide table, loaded on first use.
pub fn load_thesaurus() -> &'static SynonymHash {
    SYNONYM_HASH.get_or_init(|| {
        let path = thesaurus_path();
        let text = match std::fs::read_to_string(&path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                tracing::warn!("Thesaurus file not found: {}", path.display());
                return IndexMap::new();
            }
            Err(e) => {
                tracing::error!("Error loading thesaurus: {e}");
                return IndexMap::new();
            }
        };
        // Python iterated the file's lines (`\n`-terminated, the terminator removed by `strip()`).
        let hash = build_synonym_hash(text.split('\n'));
        tracing::info!("Loaded thesaurus with {} words", hash.len());
        hash
    })
}

/// `get_synonyms(word)`: the thesaurus synonyms of `word.lower()` (empty when unknown).
pub fn get_synonyms(word: &str) -> IndexSet<String> {
    load_thesaurus().get(&word.to_lowercase()).cloned().unwrap_or_default()
}

/// `expand_with_synonyms(words)`: the words plus all their thesaurus synonyms.
pub fn expand_with_synonyms(words: &IndexSet<String>) -> IndexSet<String> {
    let thesaurus = load_thesaurus();
    let mut expanded = words.clone();
    for w in words {
        if let Some(syns) = thesaurus.get(&w.to_lowercase()) {
            expanded.extend(syns.iter().cloned());
        }
    }
    expanded
}

/// `DOMAIN_SYNONYMS`: additions for conversational terms WordNet lacks, in dict order.
#[rustfmt::skip]
pub const DOMAIN_SYNONYMS: &[(&str, &[&str])] = &[
    ("medium", &["console", "pc", "platform", "device", "system"]),
    ("mediums", &["consoles", "pcs", "platforms", "devices", "systems"]),
    ("console", &["medium", "platform", "device", "gamecube", "playstation", "xbox", "switch", "nintendo", "pc"]),
    ("tournament", &["tourney", "competition", "contest", "championship"]),
    ("tourney", &["tournament", "competition", "contest"]),
    ("stream", &["streaming", "broadcast", "live"]),
    ("animal", &["pet", "creature", "turtle", "dog", "cat"]),
    ("animals", &["pets", "creatures", "turtles", "dogs", "cats"]),
    ("pet", &["animal", "companion", "turtle", "dog", "cat"]),
    ("pets", &["animals", "companions", "turtles", "dogs", "cats"]),
    ("letter", &["letters", "mail", "correspondence", "message"]),
    ("letters", &["letter", "mail", "correspondence", "messages"]),
    ("gift", &["present", "gave", "give"]),
    ("gave", &["give", "gift", "present"]),
    ("script", &["screenplay", "writing", "movie"]),
    ("scripts", &["screenplays", "writings", "movies"]),
    ("reject", &["rejected", "declined", "denied", "refused"]),
    ("rejected", &["reject", "declined", "denied", "refused"]),
];

fn domain_synonyms(word_lower: &str) -> &'static [&'static str] {
    DOMAIN_SYNONYMS.iter().find(|(w, _)| *w == word_lower).map(|(_, s)| *s).unwrap_or(&[])
}

/// `get_synonyms_extended(word)`: thesaurus synonyms plus the domain additions.
pub fn get_synonyms_extended(word: &str) -> IndexSet<String> {
    let lower = word.to_lowercase();
    let mut syns = load_thesaurus().get(&lower).cloned().unwrap_or_default();
    syns.extend(domain_synonyms(&lower).iter().map(|s| s.to_string()));
    syns
}

/// `expand_with_synonyms_extended(words)`: the words plus thesaurus and domain synonyms of each.
pub fn expand_with_synonyms_extended(words: &IndexSet<String>) -> IndexSet<String> {
    let thesaurus = load_thesaurus();
    let mut expanded = words.clone();
    for w in words {
        let lower = w.to_lowercase();
        if let Some(syns) = thesaurus.get(&lower) {
            expanded.extend(syns.iter().cloned());
        }
        expanded.extend(domain_synonyms(&lower).iter().map(|s| s.to_string()));
    }
    expanded
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loading_merges_and_maps_back() {
        let lines = [
            r#"{"word": "Big_Cat", "synonyms": ["Lion", "x", "", "tiger_cub"]}"#,
            "not json",
            r#"{"word": "big_cat", "synonyms": ["panther"]}"#,
            r#"{"word": "empty", "synonyms": []}"#,
            r#"[1, 2]"#,
            r#"{"word": "after", "synonyms": ["stop"]}"#,
        ];
        let h = build_synonym_hash(lines);
        let big: Vec<&str> = h["big cat"].iter().map(String::as_str).collect();
        assert_eq!(big, ["lion", "tiger cub", "panther"]);
        assert!(h["lion"].contains("big cat"));
        assert!(!h.contains_key("empty"));
        assert!(!h.contains_key("after"), "a non-object entry stops loading, as Python's outer except did");
    }
}
