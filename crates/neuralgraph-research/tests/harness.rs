//! Parity of the benchmark harnesses with Python (`tests/golden/harness.json`, written by `gen_harness.py`):
//! the LoCoMo and LongMemEval helpers on the real datasets, both chat_memory harnesses end to end on the product's
//! scripted fake model, and rescore on the committed answer files (including the published judge-sensitivity
//! numbers).

mod common;

use clap::Parser;
use common::{assert_json_eq, load, pin_now, s, strings, unpin_now, FIXED_NOW};
use neuralgraph_memory::models::set_deterministic_ids;
use neuralgraph_memory::pycompat::{dumps, py_round, py_str, py_strip, JsonFormat};
use neuralgraph_memory::testing::scripted_fake_llm;
use neuralgraph_memory::LlmClient;
use neuralgraph_research::benchmarks::chat_memory_locomo::{
    self as locomo, eligible_qa, evidence_ids, gold_parts, load_conv, messages_for, sess_of,
};
use neuralgraph_research::benchmarks::chat_memory_longmemeval::{
    self as lme, parse_lme_date, stratified_sample, summarize,
};
use neuralgraph_research::benchmarks::pyharness::{repo_root, set_fixed_perf_counter};
use neuralgraph_research::benchmarks::rescore::{
    self, count, count_line, j3_substring, judge_prompt, key, norm_bool, parse_judge_label, request_body, Rescore,
    RescoreClock, RunPaths, ACCURACY_PROMPT, STRICT_PROMPT,
};
use serde_json::Value;
use sha1::{Digest, Sha1};
use std::path::Path;
use std::sync::{Arc, LazyLock};

static FIXTURE: LazyLock<Value> = LazyLock::new(|| load("harness.json"));

fn sha1_hex(bytes: &[u8]) -> String {
    Sha1::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

/// `{"ok": value}` or `{"error": "Type: message"}`, as the generator's `err()` records a call.
fn outcome<T: Into<Value>>(r: Result<T, neuralgraph_research::benchmarks::pyharness::HarnessError>) -> Value {
    match r {
        Ok(v) => serde_json::json!({ "ok": v.into() }),
        Err(e) => serde_json::json!({ "error": e.0 }),
    }
}

fn locomo_data() -> Vec<Value> {
    let text = std::fs::read_to_string(locomo::dataset()).unwrap();
    serde_json::from_str::<Value>(&text).unwrap().as_array().unwrap().clone()
}

fn lme_data() -> &'static Vec<Value> {
    static DATA: LazyLock<Vec<Value>> = LazyLock::new(|| {
        let text = std::fs::read_to_string(lme::default_file()).unwrap();
        serde_json::from_str::<Value>(&text).unwrap().as_array().unwrap().clone()
    });
    &DATA
}

// ------------------------------------------------------------------------------------------- LoCoMo helpers

#[test]
fn locomo_helpers_match_python_on_every_conversation() {
    let f = &FIXTURE["locomo"];
    let data = locomo_data();
    for (i, c) in data.iter().enumerate() {
        let exp = &f["convs"][i];
        let conv = &c["conversation"];
        let qa = c["qa"].as_array().unwrap();
        for (captions, field) in [(true, "all_sessions"), (false, "all_sessions_no_captions")] {
            let msgs = messages_for(conv, i as i64, 99, captions).unwrap();
            let text = dumps(&Value::Array(msgs.clone()), &JsonFormat::python_default());
            let words: usize = msgs
                .iter()
                .map(|m| neuralgraph_memory::pycompat::py_split_whitespace(m["text"].as_str().unwrap()).len())
                .sum();
            assert_eq!(msgs.len() as u64, exp[field]["n"].as_u64().unwrap(), "conv {i} {field} n");
            assert_eq!(sha1_hex(text.as_bytes()), s(&exp[field]["sha1"]), "conv {i} {field} messages");
            assert_eq!(words as u64, exp[field]["words"].as_u64().unwrap(), "conv {i} words");
        }
        let ev: Vec<Value> = qa.iter().map(|q| Value::from(evidence_ids(q).unwrap())).collect();
        assert_json_eq(&Value::Array(ev), &exp["evidence_ids"], &format!("conv {i} evidence ids"));
        for (sessions, want) in exp["eligible"].as_object().unwrap() {
            let got = eligible_qa(qa, sessions.parse().unwrap())
                .map(|qs| qs.iter().map(|q| qa.iter().position(|x| x == q).unwrap()).collect::<Vec<_>>());
            assert_json_eq(&outcome(got), want, &format!("conv {i} eligible ({sessions} sessions)"));
        }
        let parts: Vec<Value> =
            qa.iter().map(|q| q.get("answer").map(|a| Value::from(gold_parts(a))).unwrap_or(Value::Null)).collect();
        assert_json_eq(&Value::Array(parts), &exp["gold_parts"], &format!("conv {i} gold parts"));
    }
    let (conv0, _) = load_conv(0).unwrap();
    assert_json_eq(&Value::from(messages_for(&conv0, 0, 2, true).unwrap()), &f["conv0_sessions2"], "conv 0 s1-2");
    let (conv7, _) = load_conv(7).unwrap();
    assert_json_eq(
        &Value::from(messages_for(&conv7, 7, 1, false).unwrap()),
        &f["conv7_sessions1_no_captions"],
        "conv 7 s1 without captions",
    );
    let (last, _) = load_conv(-1).unwrap();
    let msgs = messages_for(&last, -1, 1, true).unwrap();
    assert_json_eq(&Value::from(msgs[..3].to_vec()), &f["negative_index"], "conv -1");
    assert!(load_conv(10).unwrap_err().0.starts_with("IndexError"));
}

#[test]
fn locomo_edge_cases_match_python() {
    let f = &FIXTURE["locomo"];
    for (ev, want) in f["sess_of"].as_object().unwrap() {
        assert_json_eq(&outcome(sess_of(ev)), want, &format!("sess_of({ev:?})"));
    }
    for c in f["evidence_edge"].as_array().unwrap() {
        assert_json_eq(&Value::from(evidence_ids(&c["q"]).unwrap()), &c["out"], &format!("evidence_ids {}", c["q"]));
    }
    for c in f["gold_edge"].as_array().unwrap() {
        assert_json_eq(&Value::from(gold_parts(&c["a"])), &c["out"], &format!("gold_parts {}", c["a"]));
    }
}

// ------------------------------------------------------------------------------------------- LongMemEval helpers

#[test]
fn lme_dates_match_python() {
    let f = &FIXTURE["lme"];
    for (raw, want) in f["dates"].as_object().unwrap() {
        assert_json_eq(&Value::from(parse_lme_date(Some(raw)).unwrap()), want, raw);
    }
    for c in f["date_edge"].as_array().unwrap() {
        let got = outcome(parse_lme_date(c["s"].as_str()).map(Value::from));
        let mut want = c.clone();
        want.as_object_mut().unwrap().remove("s");
        assert_json_eq(&got, &want, &format!("parse_lme_date({})", c["s"]));
    }
}

#[test]
fn lme_stratified_sample_matches_python() {
    let f = &FIXTURE["lme"];
    let data = lme_data();
    for c in f["samples"].as_array().unwrap() {
        let (n, seed) = (c["n"].as_i64().unwrap(), c["seed"].as_i64().unwrap() as i128);
        let got = lme::question_ids(&stratified_sample(data, n, seed, 3).unwrap());
        assert_eq!(got, strings(&c["ids"]), "stratified_sample(n={n}, seed={seed})");
    }
    for c in f["samples_small"].as_array().unwrap() {
        let (n, seed, m) = (c["n"].as_i64().unwrap(), c["seed"].as_i64().unwrap(), c["min_per_type"].as_i64().unwrap());
        let got = lme::question_ids(&stratified_sample(&data[..40], n, seed as i128, m).unwrap());
        assert_eq!(got, strings(&c["ids"]), "small sample n={n} seed={seed} min={m}");
    }
}

/// The published Track D sample (`--n 50 --seed 1`): 50 questions, 4 of them abstention questions, which is what
/// makes the reported 93.5 / 87.0 turn@15 equal 43/46 and 40/46.
#[test]
fn lme_published_track_d_sample() {
    let sample = stratified_sample(lme_data(), 50, 1, 3).unwrap();
    let ids = lme::question_ids(&sample);
    assert_eq!(ids, strings(&FIXTURE["lme"]["samples"][0]["ids"]));
    assert_eq!(ids.len(), 50);
    assert_eq!(ids.iter().filter(|i| i.ends_with("_abs")).count(), 4);
    assert_eq!(&ids[..3], ["cc6d1ec1", "a2f3aa27", "9aaed6a3"]);
}

#[test]
fn lme_summaries_match_python() {
    for c in FIXTURE["lme_summaries"].as_array().unwrap() {
        let rows = c["rows"].as_array().unwrap();
        assert_json_eq(&summarize(rows).unwrap(), &c["summary"], &format!("summarize({} rows)", rows.len()));
    }
}

// ------------------------------------------------------------------------------------------- end to end

fn fake_llm() -> Option<Arc<dyn LlmClient>> {
    Some(Arc::new(scripted_fake_llm()))
}

/// Pin what the generator pinned (clock, id counters, `perf_counter`) for one harness run.
fn pin_run() {
    pin_now(FIXED_NOW);
    set_deterministic_ids(Some(0));
    set_fixed_perf_counter(Some(0.0));
}

fn unpin_run() {
    unpin_now();
    set_deterministic_ids(None);
    set_fixed_perf_counter(None);
}

fn read_tmp(path: &Path, tmp: &str) -> Value {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap().replace(tmp, "<TMP>")).unwrap()
}

#[tokio::test]
async fn locomo_harness_end_to_end_matches_python() {
    let dir = tempfile::tempdir().unwrap();
    let tmp = dir.path().to_string_lossy().into_owned();
    for case in FIXTURE["locomo_e2e"].as_array().unwrap() {
        let name = s(&case["name"]);
        let db = format!("{tmp}/{name}.db");
        let (ingest_out, eval_out) = (format!("{tmp}/{name}_ingest.json"), format!("{tmp}/{name}_eval.json"));
        let mut argv = vec!["chat-memory-locomo".to_string(), "ingest".into(), "--db".into(), db.clone()];
        argv.extend(strings(&case["sessions"]));
        argv.extend(["--out".to_string(), ingest_out.clone()]);
        argv.extend(strings(&case["ingest_args"]));
        let locomo::Command::Ingest(args) = locomo::Cli::try_parse_from(&argv).unwrap().cmd else { unreachable!() };
        pin_run();
        let mut stdout = Vec::new();
        locomo::do_ingest(&args, fake_llm(), &mut stdout).await.unwrap();
        unpin_run();
        let stdout = String::from_utf8(stdout).unwrap().replace(&tmp, "<TMP>");
        assert_eq!(stdout, s(&case["ingest_stdout"]), "{name}: ingest stdout");
        assert_json_eq(&read_tmp(Path::new(&ingest_out), &tmp), &case["ingest_out"], &format!("{name}: ingest --out"));

        let mut argv = vec!["chat-memory-locomo".to_string(), "eval".into(), "--db".into(), db.clone()];
        argv.extend(strings(&case["eval_sessions"]));
        argv.extend(["--out".to_string(), eval_out.clone()]);
        let locomo::Command::Eval(args) = locomo::Cli::try_parse_from(&argv).unwrap().cmd else { unreachable!() };
        pin_run();
        let mut stdout = Vec::new();
        locomo::do_eval(&args, fake_llm(), &mut stdout).await.unwrap();
        unpin_run();
        let stdout = String::from_utf8(stdout).unwrap().replace(&tmp, "<TMP>");
        assert_eq!(stdout, s(&case["eval_stdout"]), "{name}: eval stdout");
        assert_json_eq(&read_tmp(Path::new(&eval_out), &tmp), &case["eval_out"], &format!("{name}: eval --out"));
    }
}

#[tokio::test]
async fn lme_harness_end_to_end_matches_python() {
    let f = &FIXTURE["lme_e2e"];
    let dir = tempfile::tempdir().unwrap();
    let tmp = dir.path().to_string_lossy().into_owned();
    let ids = strings(&f["question_ids"]);
    let picks: Vec<Value> =
        ids.iter().map(|id| lme_data().iter().find(|q| q["question_id"] == id.as_str()).unwrap().clone()).collect();
    let file = format!("{tmp}/questions.json");
    std::fs::write(&file, serde_json::to_string(&picks).unwrap()).unwrap();
    for run in f["runs"].as_array().unwrap() {
        let name = s(&run["name"]);
        let (db_dir, out) = if name == "resume" {
            (format!("{tmp}/all"), format!("{tmp}/all.json"))
        } else {
            (format!("{tmp}/{name}"), format!("{tmp}/{name}.json"))
        };
        let mut argv = vec!["chat-memory-longmemeval".to_string(), "--file".into(), file.clone()];
        argv.extend(["--db-dir".to_string(), db_dir, "--out".into(), out.clone()]);
        argv.extend(strings(&run["argv"]));
        let args = lme::Args::try_parse_from(&argv).unwrap();
        pin_run();
        let mut stdout = Vec::new();
        lme::main_async(&args, &fake_llm, &mut stdout).await.unwrap();
        unpin_run();
        let stdout = String::from_utf8(stdout).unwrap().replace(&tmp, "<TMP>");
        assert_eq!(stdout, s(&run["stdout"]), "{name}: stdout");
        if !run["out"].is_null() {
            assert_json_eq(&read_tmp(Path::new(&out), &tmp), &run["out"], &format!("{name}: --out"));
        }
    }
}

#[test]
fn harness_clis_keep_the_python_flags_and_defaults() {
    let a = lme::Args::try_parse_from(["x"]).unwrap();
    assert_eq!((a.n, a.seed, a.db_dir.as_str(), a.concurrency, a.batch_size), (50, 1, "/tmp/lme", 2, 0));
    assert_eq!(a.extract_assistant(), None);
    assert_eq!(a.file, repo_root().join("research/datasets/longmemeval/longmemeval_oracle.json").to_string_lossy());
    let a = lme::Args::try_parse_from(["x", "--no-extract-assistant", "--extract-assistant", "--n", " 7 "]).unwrap();
    assert_eq!((a.extract_assistant(), a.n), (Some(true), 7));
    assert!(lme::Args::try_parse_from(["x", "--n", "seven"]).is_err());
    let c = locomo::Cli::try_parse_from(["x", "ingest", "--db", "/tmp/a.db"]).unwrap();
    let locomo::Command::Ingest(i) = c.cmd else { panic!("ingest") };
    assert_eq!((i.common.conv, i.common.sessions, i.batch_size, i.max_memories, i.gen_tokens), (0, 6, 6, 8, 3500));
    assert_eq!((i.concurrency, i.parallel, i.min_importance, i.common.model.as_str()), (1, 4, None, "qwen3.8-9b-mlx"));
    assert!(locomo::Cli::try_parse_from(["x", "eval"]).is_err(), "--db is required");
}

// ------------------------------------------------------------------------------------------- rescore

fn committed_files() -> Vec<(String, String)> {
    let dr = rescore::results_dir();
    vec![
        ("flat_single_hop".to_string(), format!("{dr}flat_single_hop.json")),
        ("local_pairs_single_hop".to_string(), format!("{dr}local_pairs_single_hop.json")),
    ]
}

/// The judge-sensitivity table of docs/BENCHMARKS.md and the N5 report: Gemma-lenient and substring columns.
#[test]
fn rescore_reproduces_the_published_judge_sensitivity_numbers() {
    let published = [("flat_single_hop", 282, 64.9, 13.5), ("local_pairs_single_hop", 282, 73.8, 18.1)];
    for ((name, path), (pname, pn, gemma_pct, j3_pct)) in committed_files().iter().zip(published) {
        assert_eq!(name, pname);
        let rows = rescore::load_results(path).unwrap();
        let exp = FIXTURE["rescore"]["files"].as_array().unwrap().iter().find(|f| f["name"] == name.as_str()).unwrap();
        let n = rows.len();
        let gemma = rows.iter().filter(|r| norm_bool(r.get("correct"))).count();
        let j3: Vec<bool> =
            rows.iter().map(|r| j3_substring(&py_str(&r["gold_answer"]), &py_str(&r["generated_answer"]))).collect();
        let j3_n = j3.iter().filter(|x| **x).count();
        let pct = |k: usize| py_round(100.0 * k as f64 / n as f64, 1);
        assert_eq!((n, pct(gemma), pct(j3_n)), (pn, gemma_pct, j3_pct), "{name}: published numbers");
        assert_eq!(Value::from(j3), exp["j3_flags"], "{name}: J3 per row");
        assert_eq!((gemma as u64, j3_n as u64), (exp["gemma"].as_u64().unwrap(), exp["j3"].as_u64().unwrap()));
        let (mut auto, mut exact, mut nogold) = (0u64, 0u64, 0u64);
        for r in &rows {
            let gl = py_strip(&py_str(&r["gold_answer"]).to_lowercase()).to_string();
            let ge = py_strip(&py_str(&r["generated_answer"]).to_lowercase()).to_string();
            auto += (!gl.is_empty() && (ge == gl || ge.contains(&gl))) as u64;
            exact += (!gl.is_empty() && ge == gl) as u64;
            nogold += gl.is_empty() as u64;
        }
        assert_eq!(
            (auto, exact, nogold),
            (exp["j1_autopass"].as_u64().unwrap(), exp["j2_exact"].as_u64().unwrap(), exp["nogold"].as_u64().unwrap()),
            "{name}: auto-pass counts"
        );
    }
    let (j1, j2) = count(&committed_files()).unwrap();
    assert_eq!(format!("{}\n", count_line(j1, j2)), s(&FIXTURE["rescore"]["count_stdout"]));
}

/// The generator's stand-in judge: sha1 of the prompt picks one of its canned raw replies.
fn fake_judge(prompt: &str) -> String {
    let replies = strings(&FIXTURE["rescore"]["judge_replies"]);
    let n = replies.len() as u32;
    let idx = sha1_hex(prompt.as_bytes()).chars().fold(0u32, |acc, c| (acc * 16 + c.to_digit(16).unwrap()) % n);
    replies[idx as usize].clone()
}

struct FixedClock;

impl RescoreClock for FixedClock {
    fn time(&self) -> f64 {
        1000.0
    }
    fn hms(&self) -> String {
        "12:34:56".into()
    }
}

#[test]
fn rescore_run_matches_python_with_a_stand_in_judge() {
    let f = &FIXTURE["rescore"];
    let dir = tempfile::tempdir().unwrap();
    let ov = format!("{}/", dir.path().to_string_lossy());
    let paths = RunPaths::in_scratch(&ov);
    for (i, want) in f["runs"].as_array().unwrap().iter().enumerate() {
        let mut prompts: Vec<String> = Vec::new();
        let mut judge = |p: &str| {
            prompts.push(p.to_string());
            Ok(fake_judge(p))
        };
        let mut stdout = Vec::new();
        let out = Rescore {
            files: committed_files(),
            paths: paths.clone(),
            judge: &mut judge,
            clock: &FixedClock,
            stdout: &mut stdout,
        }
        .run()
        .unwrap();
        assert_eq!(String::from_utf8(stdout).unwrap(), s(&want["stdout"]), "run {i}: stdout");
        assert_eq!(prompts.len() as u64, want["calls"].as_u64().unwrap(), "run {i}: judge calls");
        assert_eq!(sha1_hex(prompts.join("\x00").as_bytes()), s(&want["prompts_sha1"]), "run {i}: prompts");
        assert_eq!(Value::from(prompts.iter().take(2).cloned().collect::<Vec<_>>()), want["first_prompts"]);
        assert_eq!(sha1_hex(&std::fs::read(&paths.out).unwrap()), s(&want["out_sha1"]), "run {i}: n5_rescored.json");
        assert_eq!(sha1_hex(&std::fs::read(&paths.cache).unwrap()), s(&want["cache_sha1"]), "run {i}: judge cache");
        assert_eq!(std::fs::read_to_string(&paths.log).unwrap(), s(&want["log"]), "run {i}: progress log");
        for (name, rows) in f["rescored_first_rows"].as_object().unwrap() {
            assert_json_eq(&Value::from(out[name].as_array().unwrap()[..3].to_vec()), rows, name);
        }
    }
}

#[test]
fn rescore_helpers_match_python() {
    let f = &FIXTURE["rescore"];
    for c in f["keys"].as_array().unwrap() {
        let a = strings(&c["args"]);
        assert_eq!(key(&a[0], &a[1], &a[2], &a[3]), s(&c["key"]));
    }
    for c in f["norm_bool"].as_array().unwrap() {
        assert_eq!(norm_bool(Some(&c["v"])), c["out"].as_bool().unwrap(), "norm_bool({})", c["v"]);
    }
    assert!(!norm_bool(None));
    for c in f["j3"].as_array().unwrap() {
        assert_eq!(j3_substring(s(&c["gold"]), s(&c["gen"])), c["out"].as_bool().unwrap(), "j3 {c}");
    }
    for c in f["parse_judge_label"].as_array().unwrap() {
        assert_eq!(parse_judge_label(s(&c["resp"])), c["out"].as_bool().unwrap(), "parse_judge_label({})", c["resp"]);
    }
    assert_eq!(ACCURACY_PROMPT, s(&f["prompt_templates"]["accuracy"]));
    assert_eq!(STRICT_PROMPT, s(&f["prompt_templates"]["strict"]));
    assert_eq!(request_body("Zürich {x}"), s(&f["request_body"]));
    assert!(
        judge_prompt(STRICT_PROMPT, "Q {a}", "G", "A").contains("Question: Q {a}\nGold answer: G\nGenerated answer: A")
    );
    // N5_SCRATCH_DIR keeps exactly one trailing slash (the generator set it with two)
    std::env::set_var("N5_SCRATCH_DIR", s(&f["scratch_env"]));
    assert!(rescore::scratch_dir().ends_with(s(&f["ov_suffix"])));
    assert!(!rescore::scratch_dir().ends_with("//"));
    std::env::remove_var("N5_SCRATCH_DIR");
    assert_eq!(rescore::scratch_dir(), format!("{}/", repo_root().join("research/results/scratch").to_string_lossy()));
}
