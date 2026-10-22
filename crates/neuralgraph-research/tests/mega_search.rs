//! Parity of mega search with Python (`tests/golden/mega_search.json`, written by `gen_mega_search.py`): every
//! channel combination the harnesses use, for every LoCoMo question of conversation 1, with and without the LLM
//! entity graph. Vector scores are float32 BLAS results, bit-exact on macOS only (`numpy::bitwise_numpy()`); off
//! macOS only the BLAS-free pieces are compared.

mod common;

use common::{assert_json_eq, load, s};
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::fake_embedding;
use neuralgraph_memory::pycompat::{dumps, JsonFormat};
use neuralgraph_research::numpy::bitwise_numpy;
use neuralgraph_research::retrieval::mega_search::{entities_of, rrf, tokenize, MegaIndex};
use neuralgraph_research::retrieval::{node_ref, NeuralNode, NodeLayer, NodeRef};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn sha(v: &Value) -> String {
    let text = dumps(v, &JsonFormat::python_default());
    Sha256::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

fn locomo() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn build(conv: &Value, conv_idx: usize, dim: usize) -> (Vec<NodeRef>, Vec<NodeRef>) {
    let c = &conv["conversation"];
    let mut messages: Vec<(String, String, String)> = Vec::new();
    let mut i = 1;
    while let Some(session) = c.get(format!("session_{i}")) {
        let dt = c.get(format!("session_{i}_date_time")).and_then(Value::as_str).unwrap_or("").to_string();
        for m in session.as_array().unwrap() {
            messages.push((s(&m["speaker"]).to_string(), s(&m["text"]).to_string(), dt.clone()));
        }
        i += 1;
    }
    let node = |id: String, text: &str, speaker: &str, dt: &str| {
        let mut n = NeuralNode::new(id, NodeLayer::Message, text);
        n.session_key = format!("conv_{conv_idx}");
        n.embedding = Some(fake_embedding(text, dim));
        n.metadata.insert("speaker".into(), json!(speaker));
        n.metadata.insert("datetime".into(), json!(dt));
        n
    };
    let nodes: Vec<NodeRef> = messages
        .iter()
        .enumerate()
        .map(|(i, (sp, text, dt))| node_ref(node(format!("msg_{conv_idx}_{i}"), text, sp, dt)))
        .collect();
    let mut pairs = Vec::new();
    for (k, i) in (1..messages.len()).enumerate() {
        let text = format!("[{}] {}\n[{}] {}", messages[i - 1].0, messages[i - 1].1, messages[i].0, messages[i].1);
        let mut p = node(format!("pair2_{conv_idx}_{k}"), &text, &messages[i].0, &messages[i].2);
        p.metadata.insert(
            "pair_members".into(),
            json!([nodes[i].borrow().node_id.clone(), nodes[i - 1].borrow().node_id.clone()]),
        );
        pairs.push(node_ref(p));
    }
    (nodes, pairs)
}

fn ranked(res: Vec<(NodeRef, f64)>) -> Value {
    json!(res.into_iter().map(|(n, sc)| json!([n.borrow().node_id.clone(), sc])).collect::<Vec<_>>())
}

struct Setup {
    f: Value,
    nodes: Vec<NodeRef>,
    mega: MegaIndex,
    mega_kg: MegaIndex,
    mega_kgu: MegaIndex,
}

fn setup() -> Setup {
    let f = load("mega_search.json");
    let conv_idx = f["conv_idx"].as_u64().unwrap() as usize;
    let dim = f["dim"].as_u64().unwrap() as usize;
    let (nodes, pairs) = build(&locomo()[conv_idx], conv_idx, dim);
    let kg = f["kg"].clone();
    let mut kgu = kg.clone();
    kgu.as_object_mut().unwrap().insert("_union_regex".into(), json!(true));
    let mega = MegaIndex::new(&nodes, &pairs, None, 0.5).unwrap();
    let mega_kg = MegaIndex::new(&nodes, &pairs, Some(&kg), 0.5).unwrap();
    let mega_kgu = MegaIndex::new(&nodes, &pairs, Some(&kgu), 0.5).unwrap();
    Setup { f, nodes, mega, mega_kg, mega_kgu }
}

fn pool_for(st: &Setup, case: &Value) -> IndexSet<String> {
    let named = s(&case["pool"]);
    st.nodes
        .iter()
        .filter(|n| named == "all" || n.borrow().speaker_id().to_lowercase() == named)
        .map(|n| n.borrow().node_id.clone())
        .collect()
}

#[allow(clippy::type_complexity)]
fn variants(st: &Setup, q: &str, qvec: &[f64], pool: &IndexSet<String>, tess: &[String]) -> Vec<(&'static str, Value)> {
    let (w, top) = (80, 50);
    let extra = vec![tess.to_vec()];
    let g2: IndexMap<String, f64> = [("G".to_string(), 2.0)].into_iter().collect();
    let kx: IndexMap<String, f64> = [("K".to_string(), 0.5), ("X".to_string(), 3.0)].into_iter().collect();
    let rev: Vec<String> = tess.iter().rev().cloned().collect();
    let r = |idx: &MegaIndex, k, ch, weights: Option<&IndexMap<String, f64>>, ex: &[Vec<String>], depth| {
        ranked(idx.retrieve(q, qvec, Some(pool), k, ch, weights, ex, depth).unwrap())
    };
    vec![
        ("VK", r(&st.mega, w, "VK", None, &[], 100)),
        ("VKG", r(&st.mega, w, "VKG", None, &[], 100)),
        ("VKG_tess", r(&st.mega, w, "VKG", None, &extra, 100)),
        ("VG", r(&st.mega, w, "VG", None, &[], 100)),
        ("VKG_g2", r(&st.mega, w, "VKG", Some(&g2), &[], 100)),
        ("VG50", r(&st.mega, top, "VG", None, &[], 100)),
        ("VGT50", r(&st.mega, top, "VG", None, &extra, 100)),
        ("kg_VGT50", r(&st.mega_kg, top, "VG", None, &extra, 100)),
        ("kg_G", r(&st.mega_kg, w, "G", None, &[], 100)),
        ("kg_VG50", r(&st.mega_kg, top, "VG", None, &[], 100)),
        ("kgu_VGT50", r(&st.mega_kgu, top, "VG", None, &extra, 100)),
        ("all_default", ranked(st.mega.retrieve(q, qvec, None, 50, "VKG", None, &[], 100).unwrap())),
        ("K_weighted_X", r(&st.mega, 20, "KX", Some(&kx), &[tess.to_vec(), rev], 30)),
    ]
}

#[test]
fn every_question_and_channel_mix_matches() {
    let st = setup();
    let dim = st.f["dim"].as_u64().unwrap() as usize;
    for (qi, case) in st.f["cases"].as_array().unwrap().iter().enumerate() {
        let q = s(&case["question"]);
        let qvec = fake_embedding(q, dim);
        let pool = pool_for(&st, case);
        let tess = common::strings(&case["tess_ids"]);
        for (name, got) in variants(&st, q, &qvec, &pool, &tess) {
            // channels without V are BLAS-free and must match everywhere
            let blas_free = name == "kg_G" || name == "K_weighted_X";
            if !bitwise_numpy() && !blas_free {
                continue;
            }
            if let Some(full) = st.f["full"].get(qi) {
                assert_json_eq(&got, &full[name], &format!("q{qi} {name}"));
            }
            assert_eq!(sha(&got), s(&case["sha256"][name]), "q{qi} {name}: {q}");
        }
    }
}

#[test]
fn channels_match() {
    let st = setup();
    let dim = st.f["dim"].as_u64().unwrap() as usize;
    for (qi, (ch, case)) in
        st.f["channels"].as_array().unwrap().iter().zip(st.f["cases"].as_array().unwrap()).enumerate()
    {
        let q = s(&case["question"]);
        let qvec = fake_embedding(q, dim);
        let pool = pool_for(&st, case);
        let what = format!("q{qi}");
        assert_json_eq(&json!(st.mega.keyword_rank(q, &pool, 100)), &ch["keyword"], &what);
        if bitwise_numpy() {
            assert_json_eq(&json!(st.mega.vector_rank(&qvec, &pool, 100)), &ch["vector"], &what);
            let seeds: Vec<String> =
                rrf(&[st.mega.vector_rank(&qvec, &pool, 100), st.mega.keyword_rank(q, &pool, 100)], None, 60)
                    .into_iter()
                    .take(10)
                    .map(|(m, _)| m)
                    .collect();
            assert_json_eq(&json!(seeds), &ch["seeds"], &what);
        }
        let seeds = common::strings(&ch["seeds"]);
        assert_json_eq(&json!(st.mega.graph_rank(q, &seeds, &pool, 100, 0.5, 12, true)), &ch["graph"], &what);
        assert_json_eq(&json!(st.mega_kg.graph_rank(q, &seeds, &pool, 100, 0.5, 12, true)), &ch["kg_graph"], &what);
        assert_json_eq(
            &json!(st.mega.graph_rank(q, &seeds, &pool, 30, 0.3, 5, false)),
            &ch["graph_no_temporal"],
            &what,
        );
        assert_json_eq(&json!(st.mega.graph_scores(q, &seeds, &pool, 0.5, 12, true)), &ch["graph_scores"], &what);
        assert_json_eq(&json!(st.mega_kg.graph_scores(q, &seeds, &pool, 0.5, 12, true)), &ch["kg_graph_scores"], &what);
    }
}

#[test]
fn text_helpers_and_rrf_match() {
    let f = load("mega_search.json");
    let data = locomo();
    let conv_idx = f["conv_idx"].as_u64().unwrap() as usize;
    let (nodes, _) = build(&data[conv_idx], conv_idx, 8);
    for c in f["tokenize"].as_array().unwrap() {
        assert_json_eq(&json!(tokenize(s(&c[0]))), &c[1], "tokenize");
        assert_json_eq(&json!(entities_of(s(&c[0]))), &c[2], "entities_of");
    }
    let all: Vec<Value> = nodes
        .iter()
        .map(|n| {
            let t = n.borrow().content.clone();
            json!([tokenize(&t), entities_of(&t)])
        })
        .collect();
    assert_eq!(sha(&json!(all)), s(&f["tokenize_sha256"]));
    for c in f["entities_edge"].as_array().unwrap() {
        assert_json_eq(&json!(entities_of(s(&c[0]))), &c[1], "entities_of edge");
    }
    let v = |xs: &[&str]| xs.iter().map(|x| x.to_string()).collect::<Vec<_>>();
    let got = json!([
        rrf(&[v(&["a", "b", "c"]), v(&["c", "a"]), vec![]], None, 60),
        rrf(&[v(&["a", "b"]), v(&["b"])], Some(&[0.5, 2.0]), 1),
        rrf(&[v(&["x", "y"])], Some(&[]), 60),
        rrf(&[], None, 60),
    ]);
    assert_json_eq(&got, &f["rrf"], "rrf");
}

#[test]
fn errors_match() {
    let f = load("mega_search.json");
    assert_eq!(MegaIndex::new(&[], &[], None, 0.5).err().unwrap(), s(&f["errors"]["empty"]));
    let st = setup();
    let err = st
        .mega
        .retrieve("q", &fake_embedding("q", 256), None, 5, "", None, &[vec!["ghost".to_string()]], 100)
        .err()
        .unwrap();
    assert_eq!(err, s(&f["errors"]["unknown_extra"]));
}
