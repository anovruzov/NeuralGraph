//! Parity of the thesaurus loader and synonym expansion with Python (`tests/golden/synonym_hash.json`, written by
//! `gen_synonym_hash.py`). Python sets are compared sorted.

mod common;

use common::{load, strings};
use indexmap::IndexSet;
use neuralgraph_research::retrieval::synonym_hash::{
    expand_with_synonyms, expand_with_synonyms_extended, get_synonyms, get_synonyms_extended, load_thesaurus,
    DOMAIN_SYNONYMS,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn sorted(set: IndexSet<String>) -> Vec<String> {
    let mut v: Vec<String> = set.into_iter().collect();
    v.sort();
    v
}

#[test]
fn whole_table_matches() {
    let f = load("synonym_hash.json");
    let table = load_thesaurus();
    assert_eq!(json!(table.len()), f["table_size"]);
    let mut keys: Vec<&String> = table.keys().collect();
    keys.sort();
    let lines: Vec<String> = keys
        .iter()
        .map(|k| {
            let mut syns: Vec<&str> = table[*k].iter().map(String::as_str).collect();
            syns.sort_unstable();
            format!("{k}\t{}", syns.join("|"))
        })
        .collect();
    let digest = Sha256::digest(lines.join("\n").as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    assert_eq!(hex, f["table_sha256"].as_str().unwrap());
}

#[test]
fn lookups_match() {
    let f = load("synonym_hash.json");
    for (w, expected) in f["extended"].as_object().unwrap() {
        assert_eq!(sorted(get_synonyms_extended(w)), strings(expected), "extended {w:?}");
    }
    for (w, expected) in f["plain"].as_object().unwrap() {
        assert_eq!(sorted(get_synonyms(w)), strings(expected), "plain {w:?}");
    }
    for c in f["expanded"].as_array().unwrap() {
        let words: IndexSet<String> = strings(&c["words"]).into_iter().collect();
        assert_eq!(sorted(expand_with_synonyms(&words)), strings(&c["plain"]), "expand {words:?}");
        assert_eq!(sorted(expand_with_synonyms_extended(&words)), strings(&c["extended"]), "expand_ext {words:?}");
    }
    let domain: serde_json::Map<String, Value> = DOMAIN_SYNONYMS
        .iter()
        .map(|(k, v)| {
            let mut v: Vec<&str> = v.to_vec();
            v.sort_unstable();
            (k.to_string(), json!(v))
        })
        .collect();
    assert_eq!(Value::Object(domain), f["domain"]);
}
