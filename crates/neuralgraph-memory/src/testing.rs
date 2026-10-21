//! Scripted fake LLM used by the CLI `--fake-llm` flag, the demos and the tests.
//!
//! The responder recognises the three prompt kinds by their headings and answers with plausible JSON built from
//! the NEW MESSAGES block itself: first-person sentences are rewritten to the third person, a few keyword rules
//! pick the memory kind and some typed relations, and duplicates/contradictions are decided by simple text rules.
//! It is deliberately dumb and deterministic, so tests are stable and the demo runs without any model server; the
//! real pipeline uses a local model through [`crate::backend`]. Its outputs are pinned byte-for-byte against the
//! original Python responder by the golden fixtures.

use crate::error::{MemoryError, Result};
use crate::llm::{FakeLlmClient, Responder};
use crate::pycompat::{dumps, fnum, obj, py_expand_template, py_len, JsonFormat};
use regex::Regex;
use serde_json::Value;
use std::sync::{Arc, LazyLock};

static MSG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^(\d+)\. \[([^\]]+)\]: (.*)$").unwrap());
static CAP_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b([A-Z][a-zé]{2,})\b").unwrap());
static NEW_BLOCK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)NEW MESSAGES \(date: ([^)]*)\):\n(.*?)\n\nReturn ONLY JSON").unwrap());
static ANNOT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]*\[=[\s\x1c-\x1f]*[^\]]*\]").unwrap());
static WS_RUN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]+").unwrap());

#[rustfmt::skip]
const SKIP_CAPS: [&str; 34] = [
    "the", "yes", "last", "next", "this", "my", "i", "also", "quick", "good", "rough", "remind", "mostly", "perfect",
    "actually", "update", "trip", "nice", "hi", "hey", "congrats", "welcome", "five", "day", "sunday", "saturday",
    "september", "october", "june", "april", "march", "may", "july", "august",
];
const PLACES: [&str; 10] =
    ["Berlin", "Tokyo", "Paris", "London", "Baku", "Kreuzberg", "Hamburg", "Osaka", "Kyoto", "Prenzlauer"];
const ORGS: [&str; 4] = ["Charité", "Charite", "Lincoln", "TU"];
#[rustfmt::skip]
const MONTHS: [&str; 12] = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december",
];

/// (index, speaker, text) of each NEW MESSAGES line, plus the batch date.
fn new_messages(prompt: &str) -> (String, Vec<(i64, String, String)>) {
    let Some(m) = NEW_BLOCK_RE.captures(prompt) else { return (String::new(), Vec::new()) };
    let date = m[1].to_string();
    let out = MSG_RE
        .captures_iter(&m[2])
        .map(|c| {
            let idx: i64 = c[1].parse().unwrap_or(0);
            (idx, c[2].replace(" (assistant)", ""), ANNOT_RE.replace_all(&c[3], "").into_owned())
        })
        .collect();
    (date, out)
}

/// Rewrite a first-person sentence as a third-person statement about `name` (cheap heuristic).
pub fn third_person(text: &str, name: &str) -> Result<String> {
    let first = name.split(crate::pycompat::is_py_space).find(|p| !p.is_empty()).unwrap_or("").to_string();
    let mut s = crate::pycompat::py_strip(text).to_string();
    let subs: [(&str, String); 15] = [
        (r"\bI am\b", format!("{name} is")),
        (r"\bI'm\b", format!("{name} is")),
        (r"\bI've\b", format!("{name} has")),
        (r"\bI'd\b", format!("{name} would")),
        (r"\bI'll\b", format!("{name} will")),
        (r"\bI was\b", format!("{name} was")),
        (r"\bI have\b", format!("{name} has")),
        (r"\bI\b", name.to_string()),
        (r"\bmy\b", format!("{first}'s")),
        (r"\bMy\b", format!("{first}'s")),
        (r"\bme\b", first.clone()),
        (r"\bmine\b", format!("{first}'s")),
        (r"\bwe\b", first.clone()),
        (r"\bWe\b", first.clone()),
        (r"\bour\b", format!("{first}'s")),
    ];
    for (pat, rep) in subs.iter() {
        let re = Regex::new(pat).expect("static pattern");
        // Python treats the replacement as a template (backslash escapes are interpreted)
        let mut err = None;
        let replaced = re.replace_all(&s, |c: &regex::Captures| match py_expand_template(rep, c) {
            Ok(t) => t,
            Err(e) => {
                err.get_or_insert(e);
                String::new()
            }
        });
        if let Some(e) = err {
            return Err(MemoryError::Other("error", e));
        }
        s = replaced.into_owned();
    }
    // (the original also re-substituted "{name} (\w+)s" with itself, which changes nothing)
    if let Some(c) = s.chars().next() {
        s = c.to_uppercase().collect::<String>() + &s[c.len_utf8()..];
    }
    if !(s.ends_with('.') || s.ends_with('!') || s.ends_with('?')) {
        s.push('.');
    }
    Ok(s)
}

/// `re.split(r"(?<=[.!?])\s+", text.strip())`, keeping pieces longer than 12 characters.
fn split_sentences(text: &str) -> Vec<String> {
    let t = crate::pycompat::py_strip(text);
    let mut parts = Vec::new();
    let mut last = 0;
    for m in WS_RUN_RE.find_iter(t) {
        let prev = t[..m.start()].chars().next_back();
        if matches!(prev, Some('.' | '!' | '?')) {
            parts.push(&t[last..m.start()]);
            last = m.end();
        }
    }
    parts.push(&t[last..]);
    parts.into_iter().filter(|p| py_len(p) > 12).map(str::to_string).collect()
}

fn kind_of(low: &str) -> &'static str {
    let any = |words: &[&str]| words.iter().any(|w| low.contains(w));
    if any(&["plan", "signed up", "thinking about", "going to", "will ", "next ", "want to"]) {
        return "plan";
    }
    if any(&["love", "like", "prefer", "hate", "favorite", "favourite", "vegetarian", "vegan", "allergic"]) {
        return "preference";
    }
    if any(&["moved", "went", "visited", "started", "finished", "did it", "yesterday", "last ", "switching"]) {
        return "event";
    }
    if any(&["birthday", "is my", "i'm a", "work as", "nurse", "sister", "brother", "mother", "daughter", "cat", "dog"])
    {
        return if any(&["i'm a", "work as", "nurse"]) { "identity" } else { "fact" };
    }
    "fact"
}

fn entities(text: &str) -> Vec<Value> {
    let mut seen: Vec<String> = Vec::new();
    for c in CAP_RE.captures_iter(text) {
        let w = c[1].to_string();
        if !seen.contains(&w) {
            seen.push(w);
        }
    }
    let mut out = Vec::new();
    for c in seen {
        if SKIP_CAPS.contains(&c.to_lowercase().as_str()) {
            continue;
        }
        let t = if PLACES.contains(&c.as_str()) {
            "place"
        } else if ORGS.contains(&c.as_str()) {
            "org"
        } else {
            "person"
        };
        out.push(obj([("name", Value::String(c)), ("type", Value::String(t.into()))]));
    }
    out.truncate(5);
    out
}

static LEAD_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(?:hi!?|hello!?|also,?|quick one:|mostly|update:|actually,?|remind me later:|good to know\.?|perfect,?|thanks,?)[\s\x1c-\x1f]*").unwrap()
});
static ACK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:ok|thanks|thank you|perfect|good night|good to know)").unwrap());
static MARKER_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(?:i|i'm|i've|i'll|my|we)\b").unwrap());
static DAY_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(\d{1,2}) (january|february|march|april|may|june|july|august|september|october|november|december)\b")
        .unwrap()
});

fn upper_first(s: &str) -> String {
    match s.chars().next() {
        Some(c) => c.to_uppercase().collect::<String>() + &s[c.len_utf8()..],
        None => String::new(),
    }
}

fn memories_for(prompt: &str) -> Result<String> {
    let (date, msgs) = new_messages(prompt);
    let mut mems: Vec<Value> = Vec::new();
    for (idx, who, text) in msgs {
        let wl = who.to_lowercase();
        if wl == "assistant" || wl == "system" || py_len(&text) < 15 {
            continue;
        }
        for sent in split_sentences(&text) {
            let stripped = LEAD_RE.replace(&sent, "").into_owned();
            let sent = upper_first(crate::pycompat::py_strip(&stripped));
            let sl = sent.to_lowercase();
            if py_len(&sent) < 25 || ACK_RE.is_match(&sl) {
                continue;
            }
            let has_marker = MARKER_RE.is_match(&sl) || !entities(&sent).is_empty();
            if !has_marker {
                continue;
            }
            if sl.ends_with('?') && !["i'm", "i am", "my "].iter().any(|w| sl.contains(w)) {
                continue;
            }
            if ["can you", "could you", "suggest"].iter().any(|w| sl.contains(w)) && !sl.contains("birthday") {
                continue;
            }
            let kind = kind_of(&sl);
            let mut when = Value::Null;
            if let (Some(m), false) = (DAY_MONTH_RE.captures(&sl), date.is_empty()) {
                let month = MONTHS.iter().position(|x| *x == &m[2]).unwrap_or(0) + 1;
                let day: i64 = m[1].parse().unwrap_or(0);
                when = Value::String(format!("{}-{:02}-{:02}", crate::pycompat::py_prefix(&date, 4), month, day));
            } else if kind == "event" && !date.is_empty() {
                when = Value::String(date.clone());
            }
            mems.push(obj([
                ("text", Value::String(third_person(&sent, &who)?)),
                ("kind", Value::String(kind.into())),
                ("subject", Value::String(who.clone())),
                ("when", when),
                ("importance", fnum(if kind == "identity" { 0.9 } else { 0.7 })),
                ("confidence", fnum(0.9)),
                ("sources", Value::Array(vec![Value::from(idx)])),
                ("entities", Value::Array(entities(&sent))),
            ]));
        }
    }
    mems.truncate(8);
    Ok(dumps(&obj([("memories", Value::Array(mems))]), &JsonFormat::python_default()))
}

static REL_RULES: LazyLock<Vec<(Regex, &'static str)>> = LazyLock::new(|| {
    [
        (r"moved (?:from \w+ )?to (?P<o>[A-Z][a-zé]+)", "lives_in"),
        (r"live in (?P<o>[A-Z][a-zé]+)", "lives_in"),
        (r"now in (?P<o>[A-Z][a-zé]+)", "lives_in"),
        (r"moved from (?P<o>[A-Z][a-zé]+)", "moved_from"),
        (r"job at (?P<o>[A-Z][a-zé]+)", "works_at"),
        (r"work at (?P<o>[A-Z][a-zé]+)", "works_at"),
        (r"(?:my )?(?:cat|dog) (?:called |named )?(?P<o>[A-Z][a-z]+)", "has_pet"),
        (r"(?:my )?sister (?P<o>[A-Z][a-z]+)", "sibling_of"),
        (r"(?:my )?daughter (?P<o>[A-Z][a-z]+)", "parent_of"),
        (r"colleague (?P<o>[A-Z][a-z]+)", "colleague_of"),
        (r"allergic to (?P<o>\w+)", "allergic_to"),
        (r"master'?s in (?P<o>[a-z ]+?) at", "plans_to_study"),
        (r"at (?P<o>TU [A-Z][a-z]+)", "plans_to_study_at"),
        (r"(?P<o>[A-Z][a-z]+) marathon", "plans_to_run"),
    ]
    .into_iter()
    .map(|(p, r)| (Regex::new(p).expect("static pattern"), r))
    .collect()
});

fn relations_for(prompt: &str) -> String {
    let (_, msgs) = new_messages(prompt);
    let mut triples: Vec<Value> = Vec::new();
    let mut ents: Vec<Value> = Vec::new();
    for (idx, who, text) in msgs {
        let wl = who.to_lowercase();
        if wl == "assistant" || wl == "system" {
            continue;
        }
        for e in entities(&text) {
            if !ents.contains(&e) {
                ents.push(e);
            }
        }
        for (re, rel) in REL_RULES.iter() {
            for m in re.captures_iter(&text) {
                let o = crate::pycompat::py_strip(m.name("o").map(|x| x.as_str()).unwrap_or("")).to_string();
                let ol = o.to_lowercase();
                if SKIP_CAPS.contains(&ol.as_str()) || ol == wl {
                    continue;
                }
                triples.push(obj([
                    ("s", Value::String(who.clone())),
                    ("r", Value::String((*rel).into())),
                    ("o", Value::String(o)),
                    ("source", Value::from(idx)),
                    ("confidence", fnum(0.9)),
                ]));
            }
        }
        if text.to_lowercase().contains("vegetarian") {
            triples.push(obj([
                ("s", Value::String(who.clone())),
                ("r", Value::String("diet".into())),
                ("o", Value::String("vegetarian".into())),
                ("source", Value::from(idx)),
                ("confidence", fnum(0.9)),
            ]));
        }
    }
    ents.truncate(10);
    triples.truncate(12);
    dumps(&obj([("entities", Value::Array(ents)), ("triples", Value::Array(triples))]), &JsonFormat::python_default())
}

static NEW_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"NEW \(observed [^)]*\): "(.*)""#).unwrap());
static EXISTING_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?m)^([A-H])\. \(observed [^)]*\) "(.*)"$"#).unwrap());

fn reconcile_for(prompt: &str) -> String {
    let decision = |d: &str, target: Value, reason: &str| {
        dumps(
            &obj([
                ("decision", Value::String(d.into())),
                ("target", target),
                ("text", Value::Null),
                ("reason", Value::String(reason.into())),
            ]),
            &JsonFormat::python_default(),
        )
    };
    let new = NEW_RE.captures(prompt);
    let existing: Vec<(String, String)> =
        EXISTING_RE.captures_iter(prompt).map(|c| (c[1].to_string(), c[2].to_string())).collect();
    if let (Some(new), false) = (new, existing.is_empty()) {
        let nt = new[1].to_lowercase();
        for (letter, txt) in &existing {
            let t = txt.to_lowercase();
            if nt == t {
                return decision("DUPLICATE", Value::String(letter.clone()), "same");
            }
            let movey = |s: &str| ["moved", "now in", "lives in"].iter().any(|w| s.contains(w));
            if movey(&nt) && movey(&t) {
                return decision("CONTRADICT", Value::String(letter.clone()), "location changed");
            }
        }
    }
    decision("ADD", Value::Null, "different")
}

/// The scripted completion for one prompt.
pub fn scripted_responder(prompt: &str) -> Result<String> {
    if prompt.starts_with("You maintain the long-term memory") {
        return memories_for(prompt);
    }
    if prompt.starts_with("You are building a knowledge graph") {
        return Ok(relations_for(prompt));
    }
    if prompt.starts_with("You maintain a memory store") {
        return Ok(reconcile_for(prompt));
    }
    Ok("{}".into())
}

/// A [`FakeLlmClient`] driven by [`scripted_responder`].
pub fn scripted_fake_llm() -> FakeLlmClient {
    let responder: Responder = Arc::new(scripted_responder);
    FakeLlmClient { responder: Some(responder), ..Default::default() }
}
