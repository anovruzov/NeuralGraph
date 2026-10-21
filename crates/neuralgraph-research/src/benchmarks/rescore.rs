//! Port of `research/benchmarks/rescore.py` (binary `rescore`), N5: re-score existing LoCoMo answer files under
//! three judges.
//!
//! * J1 lenient   = runner.py ACCURACY_PROMPT verbatim + runner's exact/substring auto-pass, judge = qwen3.6-35b-a3b
//! * J2 strict    = strict prompt (same specific fact), exact-match auto-pass only, judge = qwen3.6-35b-a3b
//! * J3 substring = gold parts (split on ',' and ' and ', parts > 2 chars) all in generated answer, no LLM
//!
//! Sequential calls only (the capstone run shared LM Studio). Judge outputs are cached in `n5_judge_cache.json`
//! keyed by `sha1(judge, question, gold, generated)` so a crash resumes and duplicate answers across files are judged
//! once; the key and the file format are unchanged, so a cache written by the Python is reused as is. Per-row labels
//! go to `n5_rescored.json`, progress lines to stdout and `n5_progress.log`.
//!
//! `N5_SCRATCH_DIR` overrides the scratch directory (default `research/results/scratch/`). The Python said "edit
//! FILES at the top" to change the answer files; the binary takes `--file name=path` instead (repeatable; when
//! given, the list replaces the default [`default_files`]). `rescore count` is the dry run: how many judge calls are
//! needed.

use super::pyharness::{py_getitem, repo_root, HResult, HarnessError};
use neuralgraph_memory::extraction::py_format;
use neuralgraph_memory::pycompat::{dumps, obj, py_str, py_strip, JsonFormat};
use regex::Regex;
use serde_json::{Map, Value};
use sha1::{Digest, Sha1};
use std::collections::HashSet;
use std::io::Write;
use std::sync::LazyLock;
use std::time::Duration;

pub const URL: &str = "http://127.0.0.1:1234/v1/chat/completions";
pub const MODEL: &str = "qwen/qwen3.6-35b-a3b";

/// Verbatim copy of research/benchmarks/runner.py ACCURACY_PROMPT (campaign judge prompt).
pub const ACCURACY_PROMPT: &str = r#"
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"#;

pub const STRICT_PROMPT: &str = r#"
You are a STRICT grader. Decide whether a generated answer states the same specific fact as the gold answer.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

Rules:
- CORRECT only if the generated answer states the SAME SPECIFIC FACT as the gold answer: the same entity, number, date, place, or item. Wording, format and length may differ (e.g. "May 7th" vs "7 May", "Sweden" vs "she moved from Sweden") as long as the fact is identical.
- If the gold answer is a list, CORRECT only if EVERY gold item is present in the generated answer. Extra items are tolerated.
- Topical overlap is NOT enough. An answer that is vaguer or more general than the gold (e.g. "home country" for "Sweden", "pets" for "turtles", "a book" for "Becoming Nicole") is WRONG.
- An answer that gives a related but different fact, or contradicts the gold, is WRONG.
- Abstentions and hedges such as "Not found", "Unclear", "No evidence", "I don't know", "the memories do not mention" are WRONG, even if they mention the topic.

Return only a JSON object with the key "label" and the value "CORRECT" or "WRONG".
"#;

/// Scratch directory for run files that are not committed (judge cache, rescored output, progress log) and for any
/// result file kept outside the repo: `N5_SCRATCH_DIR`, else `research/results/scratch/`; always ends in `/`.
pub fn scratch_dir() -> String {
    let dir = std::env::var("N5_SCRATCH_DIR")
        .unwrap_or_else(|_| repo_root().join("research/results/scratch").to_string_lossy().into_owned());
    format!("{}/", dir.trim_end_matches('/'))
}

/// `research/results/` (with the trailing slash the Python concatenated onto).
pub fn results_dir() -> String {
    format!("{}/", repo_root().join("research/results").to_string_lossy())
}

/// The Python's `FILES`: the two committed answer files and four scratch-only ones.
pub fn default_files(ov: &str, dr: &str) -> Vec<(String, String)> {
    [
        ("flat_single_hop", format!("{dr}flat_single_hop.json")),
        ("local_pairs_single_hop", format!("{dr}local_pairs_single_hop.json")),
        ("H8_control", format!("{ov}H8_control.json")),
        ("H8_treatment", format!("{ov}H8_treatment.json")),
        ("H5_control", format!("{ov}H5_control.json")),
        ("H5_T2_full", format!("{ov}H5_T2_full.json")),
    ]
    .into_iter()
    .map(|(n, p)| (n.to_string(), p))
    .collect()
}

/// Where a run reads and writes, all inside the scratch directory.
#[derive(Clone, Debug)]
pub struct RunPaths {
    pub cache: String,
    pub out: String,
    pub log: String,
}

impl RunPaths {
    pub fn in_scratch(ov: &str) -> Self {
        RunPaths {
            cache: format!("{ov}n5_judge_cache.json"),
            out: format!("{ov}n5_rescored.json"),
            log: format!("{ov}n5_progress.log"),
        }
    }
}

static BRACED_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{[^}]+\}").unwrap());
static LABEL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?i)"label"[\s\x1c-\x1f]*:[\s\x1c-\x1f]*"(CORRECT|WRONG)""#).unwrap());
static J3_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r",| and ").unwrap());

/// `str(obj.get("label", "")).strip().upper() == "CORRECT"`; `None` when `obj` is not a dict (Python's
/// `AttributeError`, swallowed by the caller).
fn label_of(obj: &Value) -> Option<bool> {
    let m = obj.as_object()?;
    let label = m.get("label").map(py_str).unwrap_or_default();
    Some(py_strip(&label).to_uppercase() == "CORRECT")
}

/// Copy of runner.parse_judge_label: True only if label is exactly CORRECT.
pub fn parse_judge_label(resp: &str) -> bool {
    if let Ok(v) = serde_json::from_str::<Value>(py_strip(resp)) {
        if let Some(b) = label_of(&v) {
            return b;
        }
    }
    if let Some(m) = BRACED_RE.find(resp) {
        if let Ok(v) = serde_json::from_str::<Value>(m.as_str()) {
            if let Some(b) = label_of(&v) {
                return b;
            }
        }
    }
    if let Some(c) = LABEL_RE.captures(resp) {
        return c[1].to_uppercase() == "CORRECT";
    }
    false
}

/// Cache key: `sha1("\x1f".join([judge, q, gold, gen]))` as hex.
pub fn key(judge: &str, q: &str, gold: &str, gen: &str) -> String {
    let joined = [judge, q, gold, gen].join("\x1f");
    Sha1::digest(joined.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// The runner's `correct` flag as a bool (`True`, or any value whose `str()` is "true" in any case).
pub fn norm_bool(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(true)) => true,
        Some(v) => py_strip(&py_str(v)).to_lowercase() == "true",
        None => false,
    }
}

/// J3: every gold part longer than two characters is a substring of the generated answer (or, with no such part,
/// the whole non-empty gold is).
pub fn j3_substring(gold: &str, gen: &str) -> bool {
    let g = gen.to_lowercase();
    let parts: Vec<String> =
        J3_SPLIT_RE.split(gold).map(|p| py_strip(p).to_lowercase()).filter(|p| p.chars().count() > 2).collect();
    if parts.is_empty() {
        return g.contains(py_strip(&gold.to_lowercase())) && !py_strip(gold).is_empty();
    }
    parts.iter().all(|p| g.contains(p.as_str()))
}

/// The judge prompt of `template` for one answer (`str.format` with the three fields).
pub fn judge_prompt(template: &str, q: &str, gold: &str, gen: &str) -> String {
    py_format(template, &[("question", q), ("gold_answer", gold), ("generated_answer", gen)])
}

/// The HTTP body of one judge call, byte-identical to the Python's `json.dumps(...)`.
pub fn request_body(prompt: &str) -> String {
    let body = obj([
        ("model", Value::from(MODEL)),
        ("messages", Value::Array(vec![obj([("role", Value::from("user")), ("content", Value::from(prompt))])])),
        ("temperature", Value::from(0)),
        ("max_tokens", Value::from(150)),
        ("stream", Value::Bool(false)),
        ("reasoning_effort", Value::from("none")),
    ]);
    dumps(&body, &JsonFormat::python_default())
}

async fn judge_once(client: &reqwest::Client, body: &str) -> Result<String, String> {
    let resp = client
        .post(URL)
        .header("Content-Type", "application/json")
        .body(body.to_string())
        .timeout(Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("<urlopen error {e}>"))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(format!("HTTP Error {}: {}", status.as_u16(), status.canonical_reason().unwrap_or("")));
    }
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    let obj: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let content = obj
        .get("choices")
        .and_then(|c| c.get(0))
        .and_then(|c| c.get("message"))
        .and_then(|m| m.get("content"))
        .ok_or_else(|| "'choices'".to_string())?;
    let text = content.as_str().ok_or_else(|| "'NoneType' object has no attribute 'strip'".to_string())?;
    Ok(py_strip(text).to_string())
}

/// One judge call to LM Studio: four attempts, sleeping 5, 10, 15, 20 s after each failure (also after the last,
/// as the Python did).
pub async fn llm(client: &reqwest::Client, prompt: &str) -> HResult<String> {
    let body = request_body(prompt);
    let mut last = String::new();
    for attempt in 0..4u64 {
        match judge_once(client, &body).await {
            Ok(text) => return Ok(text),
            Err(e) => {
                last = e;
                tokio::time::sleep(Duration::from_secs(5 * (attempt + 1))).await;
            }
        }
    }
    Err(HarnessError::new("RuntimeError", format!("judge call failed after retries: {last}")))
}

/// `json.load` of the cache, or an empty cache when the file does not exist.
pub fn load_cache(path: &str) -> HResult<Map<String, Value>> {
    if !std::path::Path::new(path).exists() {
        return Ok(Map::new());
    }
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    match v {
        Value::Object(m) => Ok(m),
        _ => Err(HarnessError::new("TypeError", "judge cache is not a JSON object")),
    }
}

/// `json.dump(cache, f)` to a temporary file, then an atomic rename.
pub fn save_cache(path: &str, cache: &Map<String, Value>) -> HResult<()> {
    let tmp = format!("{path}.tmp");
    std::fs::write(&tmp, dumps(&Value::Object(cache.clone()), &JsonFormat::python_default()))?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Wall clock of a run: `time.time()` for the elapsed seconds and `time.strftime('%H:%M:%S')` for log lines.
pub trait RescoreClock {
    fn time(&self) -> f64;
    fn hms(&self) -> String;
}

/// The real clock (local time, like `time.strftime`).
pub struct SystemClock;

impl RescoreClock for SystemClock {
    fn time(&self) -> f64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
    }
    fn hms(&self) -> String {
        chrono::Local::now().format("%H:%M:%S").to_string()
    }
}

/// The answer rows of one file (`json.load(f)['results']`).
pub fn load_results(path: &str) -> HResult<Vec<Value>> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        HarnessError::new("FileNotFoundError", format!("{e}: {}", neuralgraph_memory::pycompat::py_repr_str(path)))
    })?;
    let v: Value = serde_json::from_str(&text)?;
    Ok(py_getitem(&v, "results")?.as_array().cloned().unwrap_or_default())
}

/// `str(r[k])` of an answer row.
fn field(r: &Value, k: &str) -> HResult<String> {
    Ok(py_str(py_getitem(r, k)?))
}

/// One re-scoring run over `files` (the Python `main()`): judge calls go through `judge`, which is called only for
/// keys missing from the cache. Returns the rescored document that was written to `paths.out`.
pub struct Rescore<'a> {
    pub files: Vec<(String, String)>,
    pub paths: RunPaths,
    pub judge: &'a mut dyn FnMut(&str) -> HResult<String>,
    pub clock: &'a dyn RescoreClock,
    pub stdout: &'a mut dyn Write,
}

impl Rescore<'_> {
    fn log(&mut self, msg: &str) -> HResult<()> {
        let line = format!("[{}] {msg}", self.clock.hms());
        writeln!(self.stdout, "{line}")?;
        self.stdout.flush()?;
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&self.paths.log)?;
        writeln!(f, "{line}")?;
        Ok(())
    }

    /// The cached label of `(judge, q, gold, gen)`, asking the model first when it is not cached yet.
    fn judged(
        &mut self,
        cache: &mut Map<String, Value>,
        n_calls: &mut i64,
        judge: &str,
        template: &str,
        (q, gold, gen): (&str, &str, &str),
    ) -> HResult<(Value, Value)> {
        let k = key(judge, q, gold, gen);
        if !cache.contains_key(&k) {
            let raw = (self.judge)(&judge_prompt(template, q, gold, gen))?;
            let label = parse_judge_label(&raw);
            cache.insert(k.clone(), obj([("raw", Value::String(raw)), ("label", Value::Bool(label))]));
            *n_calls += 1;
            if *n_calls % 10 == 0 {
                save_cache(&self.paths.cache, cache)?;
            }
        }
        let entry = &cache[&k];
        Ok((py_getitem(entry, "label")?.clone(), py_getitem(entry, "raw")?.clone()))
    }

    pub fn run(&mut self) -> HResult<Value> {
        let mut cache = load_cache(&self.paths.cache)?;
        let mut out = Map::new();
        let mut n_calls: i64 = 0;
        let t0 = self.clock.time();
        let files = self.files.clone();
        for (name, path) in &files {
            let rows = load_results(path)?;
            let mut scored: Vec<Value> = Vec::new();
            for (i, r) in rows.iter().enumerate() {
                let (q, gold, gen) = (field(r, "question")?, field(r, "gold_answer")?, field(r, "generated_answer")?);
                let gl = py_strip(&gold.to_lowercase()).to_string();
                let ge = py_strip(&gen.to_lowercase()).to_string();
                let mut rec = Map::new();
                rec.insert("id".into(), r.get("id").cloned().unwrap_or(Value::Null));
                rec.insert("question".into(), Value::String(q.clone()));
                rec.insert("gold".into(), Value::String(gold.clone()));
                rec.insert("generated".into(), Value::String(gen.clone()));
                rec.insert("gemma".into(), Value::Bool(norm_bool(r.get("correct"))));
                rec.insert("j3".into(), Value::Bool(j3_substring(&gold, &gen)));
                // J1: runner auto-pass (exact or substring), else lenient LLM judge
                if !gl.is_empty() && (ge == gl || ge.contains(&gl)) {
                    rec.insert("j1".into(), Value::Bool(true));
                    rec.insert("j1_src".into(), Value::from("autopass"));
                } else if gl.is_empty() {
                    rec.insert("j1".into(), Value::Bool(false));
                    rec.insert("j1_src".into(), Value::from("nogold"));
                } else {
                    let (label, raw) =
                        self.judged(&mut cache, &mut n_calls, "J1", ACCURACY_PROMPT, (&q, &gold, &gen))?;
                    rec.insert("j1".into(), label);
                    rec.insert("j1_src".into(), Value::from("llm"));
                    rec.insert("j1_raw".into(), raw);
                }
                // J2: exact-match auto-pass only, else strict LLM judge
                if !gl.is_empty() && ge == gl {
                    rec.insert("j2".into(), Value::Bool(true));
                    rec.insert("j2_src".into(), Value::from("exact"));
                } else if gl.is_empty() {
                    rec.insert("j2".into(), Value::Bool(false));
                    rec.insert("j2_src".into(), Value::from("nogold"));
                } else {
                    let (label, raw) = self.judged(&mut cache, &mut n_calls, "J2", STRICT_PROMPT, (&q, &gold, &gen))?;
                    rec.insert("j2".into(), label);
                    rec.insert("j2_src".into(), Value::from("llm"));
                    rec.insert("j2_raw".into(), raw);
                }
                scored.push(Value::Object(rec));
                if (i + 1) % 25 == 0 {
                    let elapsed = self.clock.time() - t0;
                    self.log(&format!("{name}: {}/{} rows, {n_calls} llm calls, {elapsed:.0}s", i + 1, rows.len()))?;
                }
            }
            let n = scored.len();
            let total = |k: &str| scored.iter().filter(|x| neuralgraph_memory::pycompat::py_truthy(&x[k])).count();
            let done = format!(
                "DONE {name}: n={n} gemma={} J1={} J2={} J3={}",
                total("gemma"),
                total("j1"),
                total("j2"),
                total("j3")
            );
            out.insert(name.clone(), Value::Array(scored));
            save_cache(&self.paths.cache, &cache)?;
            self.log(&done)?;
            let fmt =
                JsonFormat { ensure_ascii: true, sort_keys: false, indent: Some(1), item_sep: ",", key_sep: ": " };
            std::fs::write(&self.paths.out, dumps(&Value::Object(out.clone()), &fmt))?;
        }
        save_cache(&self.paths.cache, &cache)?;
        let elapsed = self.clock.time() - t0;
        self.log(&format!("ALL DONE: {n_calls} llm calls in {elapsed:.0}s"))?;
        Ok(Value::Object(out))
    }
}

/// The `count` dry run: distinct J1 and J2 cache keys that would need a judge call (cached or not).
pub fn count(files: &[(String, String)]) -> HResult<(usize, usize)> {
    let mut j1: HashSet<String> = HashSet::new();
    let mut j2: HashSet<String> = HashSet::new();
    for (_name, path) in files {
        for r in load_results(path)? {
            let (q, gold, gen) = (field(&r, "question")?, field(&r, "gold_answer")?, field(&r, "generated_answer")?);
            let gl = py_strip(&gold.to_lowercase()).to_string();
            let ge = py_strip(&gen.to_lowercase()).to_string();
            if !gl.is_empty() && !(ge == gl || ge.contains(&gl)) {
                j1.insert(key("J1", &q, &gold, &gen));
            }
            if !gl.is_empty() && ge != gl {
                j2.insert(key("J2", &q, &gold, &gen));
            }
        }
    }
    Ok((j1.len(), j2.len()))
}

/// The line `count` prints: `print({k: len(v) for k, v in need.items()})`.
pub fn count_line(j1: usize, j2: usize) -> String {
    format!("{{'J1': {j1}, 'J2': {j2}}}")
}

/// Parse `--file name=path` (the replacement for editing `FILES`).
pub fn parse_file_arg(text: &str) -> Result<(String, String), String> {
    text.split_once('=')
        .map(|(n, p)| (n.to_string(), p.to_string()))
        .ok_or_else(|| format!("expected name=path, got {text:?}"))
}
