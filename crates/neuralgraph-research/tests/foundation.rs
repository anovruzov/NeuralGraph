//! Parity of the compatibility layer and the storage core with Python (`tests/golden/foundation.json`, written
//! by `gen_foundation.py` from the Python reference).

mod common;

use common::{assert_json_eq, f32_bits, f64_bits, f64s, load, pin_now, s, unpin_now};
use neuralgraph_research::numpy::{
    norm_f32, normalized_f32_eps, pairwise_sum_f32, row_norms_f32, sdot, sgemv, BITWISE_NUMPY,
};
use neuralgraph_research::pyrandom::{NumpyRandomState, PyRandom};
use neuralgraph_research::retrieval::lsh::{LshConfig, RandomProjectionLsh, SimHash};
use neuralgraph_research::retrieval::{
    cosine_similarity, edge_ref, node_ref, Direction, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge,
    NeuralGraphStorage, NeuralNode, NodeLayer,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn vecs(v: &Value) -> Vec<Vec<f64>> {
    v.as_array().unwrap().iter().map(f64s).collect()
}

#[test]
fn python_random_matches() {
    let f = load("foundation.json");
    for c in f["py_random"].as_array().unwrap() {
        let seed: i128 = s(&c["seed"]).parse().unwrap();
        let mut r = PyRandom::new(seed);
        let got_random: Vec<f64> = (0..5).map(|_| r.random()).collect();
        assert_eq!(got_random, f64s(&c["random"]), "random() seed {seed}");
        let bits: Vec<String> = [1, 5, 31, 32, 33, 64, 100].iter().map(|k| r.getrandbits(*k).to_string()).collect();
        assert_eq!(json!(bits), c["getrandbits"], "getrandbits seed {seed}");
        let below: Vec<u128> = [1u128, 2, 3, 7, 36, 1000, (1u128 << 31) + 11].iter().map(|n| r.randbelow(*n)).collect();
        assert_eq!(json!(below), c["randbelow"], "randbelow seed {seed}");
        let alphabet: Vec<char> = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".chars().collect();
        let choice: String = (0..14).map(|_| *r.choice(&alphabet)).collect();
        assert_eq!(choice, s(&c["choice"]), "choice seed {seed}");
        let mut xs: Vec<i64> = (0..20).collect();
        r.shuffle(&mut xs);
        assert_eq!(json!(xs), c["shuffle"], "shuffle seed {seed}");
        assert_eq!(json!(r.sample(&(0..10).collect::<Vec<i64>>(), 5)), c["sample_small"], "sample small {seed}");
        assert_eq!(json!(r.sample(&(0..100).collect::<Vec<i64>>(), 10)), c["sample_mid"], "sample mid {seed}");
        assert_eq!(json!(r.sample(&(0..1000).collect::<Vec<i64>>(), 60)), c["sample_large"], "sample large {seed}");
        let ints: Vec<i64> = (0..10).map(|_| r.randint(1, 6)).collect();
        assert_eq!(json!(ints), c["randint"], "randint {seed}");
    }
}

#[test]
fn numpy_legacy_random_matches() {
    let f = load("foundation.json");
    for c in f["np_random"]["cases"].as_array().unwrap() {
        let mut st = NumpyRandomState::new(c["seed"].as_u64().unwrap() as u32);
        assert_eq!(st.randn(3, 4), f64_bits(&c["randn_3x4"]), "randn seed {}", c["seed"]);
        let rs: Vec<f64> = (0..3).map(|_| st.random_sample()).collect();
        assert_eq!(rs, f64_bits(&c["random_sample"]));
    }
    let mut st = NumpyRandomState::new(42);
    let mut h = Sha256::new();
    for _ in 0..8 {
        for v in st.randn(12, 768) {
            h.update(v.to_le_bytes());
        }
    }
    assert_eq!(format!("{:x}", h.finalize()), s(&f["np_random"]["lsh_draw_sha256"]));
}

#[test]
fn numpy_pairwise_sums_match_everywhere() {
    let f = load("foundation.json");
    for c in f["blas"]["vectors"].as_array().unwrap() {
        let (a, b) = (f32_bits(&c["a"]), f32_bits(&c["b"]));
        let prods: Vec<f32> = a.iter().zip(&b).map(|(x, y)| x * y).collect();
        assert_eq!(pairwise_sum_f32(&prods).to_bits(), c["pairwise"].as_u64().unwrap() as u32, "dim {}", a.len());
    }
    let m = f32_bits(&f["blas"]["matrix"]);
    assert_eq!(row_norms_f32(&m, 768), f32_bits(&f["blas"]["row_norms"]));
}

#[test]
fn numpy_blas_matches_on_macos() {
    if !BITWISE_NUMPY {
        eprintln!("skipped: float32 BLAS parity is only defined against Accelerate (macOS)");
        return;
    }
    let f = load("foundation.json");
    for c in f["blas"]["vectors"].as_array().unwrap() {
        let (a, b) = (f32_bits(&c["a"]), f32_bits(&c["b"]));
        assert_eq!(sdot(&a, &b).to_bits(), c["dot"].as_u64().unwrap() as u32, "sdot dim {}", a.len());
        assert_eq!(norm_f32(&a).to_bits(), c["norm"].as_u64().unwrap() as u32, "norm dim {}", a.len());
    }
    let m = f32_bits(&f["blas"]["matrix"]);
    let q = f32_bits(&f["blas"]["query"]);
    let qn = normalized_f32_eps(&q, 1e-9);
    assert_eq!(qn, f32_bits(&f["blas"]["normalized_query"]));
    let norms = row_norms_f32(&m, 768);
    let mut mn = m.clone();
    for (row, n) in mn.chunks_mut(768).zip(norms) {
        for v in row {
            *v /= n + 1e-9f32;
        }
    }
    assert_eq!(sgemv(&mn, 40, &qn), f32_bits(&f["blas"]["sims"]));
    let p = f64_bits(&f["blas"]["proj64"]);
    let q64: Vec<f64> = q.iter().map(|v| *v as f64).collect();
    assert_eq!(neuralgraph_research::numpy::dgemv(&p, 12, &q64), f64_bits(&f["blas"]["proj64_times_q"]));
}

fn sorted_keys(v: &Value) -> Value {
    let mut entries: Vec<(String, Value)> =
        v.as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    Value::Object(entries.into_iter().collect())
}

fn pairs(v: &[(String, f64)]) -> Value {
    json!(v.iter().map(|(id, s)| json!([id, s])).collect::<Vec<_>>())
}

#[test]
fn lsh_matches() {
    let f = load("foundation.json");
    let embs = vecs(&f["inputs"]["embeddings"]);
    let queries = vecs(&f["inputs"]["queries"]);
    let want = &f["lsh"];
    let mut lsh = RandomProjectionLsh::new(LshConfig { num_tables: 8, num_hashes: 12, ..LshConfig::default() });
    for (i, e) in embs.iter().enumerate() {
        lsh.index(&format!("n{i}"), e);
    }
    let mut h = Sha256::new();
    for t in 0..8 {
        for v in lsh.projection(t).unwrap() {
            h.update(v.to_le_bytes());
        }
    }
    assert_eq!(format!("{:x}", h.finalize()), s(&want["projections_sha256"]), "projection matrices");
    assert_eq!(json!([lsh.remove("n3"), lsh.remove("missing")]), want["removed"]);
    if BITWISE_NUMPY {
        for (i, q) in queries.iter().enumerate() {
            assert_json_eq(&pairs(&lsh.query(q, 15, 0.0)), &want["query"][i], &format!("lsh query {i}"));
            assert_json_eq(
                &pairs(&lsh.query_with_probing(q, 30, 0.0, 3)),
                &want["probe"][i],
                &format!("lsh probe {i}"),
            );
        }
        assert_json_eq(&lsh.get_stats(), &want["stats"], "lsh stats");
    }
    let sim = SimHash::new(48, 64, 42);
    let sigs: Vec<String> = embs[..20].iter().map(|e| sim.compute_signature(e).to_string()).collect();
    if BITWISE_NUMPY {
        assert_eq!(json!(sigs), want["simhash"]);
        let map = embs[..40].iter().enumerate().map(|(i, e)| (format!("n{i}"), e.clone())).collect();
        let dups: Vec<Value> =
            sim.find_near_duplicates(&map, 0.6).into_iter().map(|(a, b, s)| json!([a, b, s])).collect();
        assert_json_eq(&json!(dups), &want["near_duplicates"], "simhash duplicates");
    }
}

#[test]
fn in_memory_storage_matches() {
    pin_now(common::FIXED_NOW);
    let f = load("foundation.json");
    let embs = vecs(&f["inputs"]["embeddings"]);
    let queries = vecs(&f["inputs"]["queries"]);
    let want = &f["storage"];
    let st = InMemoryNeuralGraphStorage::default();
    for (i, e) in embs.iter().enumerate() {
        let mut n = NeuralNode::new(
            format!("n{i}"),
            if i % 5 != 0 { NodeLayer::Message } else { NodeLayer::Episode },
            format!("message {i}"),
        );
        n.embedding = Some(e.clone());
        n.session_key = "big".into();
        n.entity_ids = vec![format!("ent{}", i % 7)];
        n.metadata.insert("speaker".into(), json!(if i % 2 == 1 { "A" } else { "B" }));
        st.save_node(&node_ref(n));
    }
    for (i, e) in embs[..30].iter().enumerate() {
        let mut n = NeuralNode::new(format!("s{i}"), NodeLayer::Message, format!("small {i}"));
        n.embedding = Some(e.clone());
        n.session_key = "small".into();
        n.heat_score = 0.1 * i as f64;
        st.save_node(&node_ref(n));
    }
    for i in (0..60).step_by(3) {
        let t = if i % 2 == 1 { EdgeType::Temporal } else { EdgeType::Entity };
        let e = NeuralEdge::new(format!("e{i}"), format!("n{i}"), format!("n{}", i + 1), t)
            .with_weight(0.5 + i as f64 / 100.0);
        st.save_edge(&edge_ref(e));
    }
    st.save_edge(&edge_ref(NeuralEdge::new("e3", "n3", "n9", EdgeType::Semantic)));
    let ids = |v: Vec<(neuralgraph_research::retrieval::NodeRef, f64)>| -> Value {
        json!(v.iter().map(|(n, s)| json!([n.borrow().node_id, s])).collect::<Vec<_>>())
    };
    for (i, q) in queries.iter().enumerate() {
        if BITWISE_NUMPY {
            assert_json_eq(
                &ids(st.vector_search(q, "big", 20, None)),
                &want["vector_big"][i],
                &format!("vector big {i}"),
            );
        }
        assert_json_eq(
            &ids(st.vector_search(q, "small", 10, None)),
            &want["vector_small"][i],
            &format!("vector small {i}"),
        );
        assert_json_eq(
            &ids(st.vector_search(q, "big", 10, Some(&[NodeLayer::Episode]))),
            &want["vector_filtered"][i],
            &format!("vector filtered {i}"),
        );
    }
    let n0 = st.get_node("n0").unwrap();
    assert_json_eq(&json!(n0.borrow().edge_type_counts), &want["n0_edge_counts"], "n0 edge counts");
    assert_eq!(json!(n0.borrow().diversity_score), want["n0_diversity"]);
    assert_json_eq(&json!(st.get_node("n3").unwrap().borrow().edge_type_counts), &want["n3_edge_counts"], "n3 counts");
    let mut nb: Vec<Value> = st
        .get_neighbors("n3", None, Direction::Both)
        .iter()
        .map(|(n, e)| json!([n.borrow().node_id, e.borrow().edge_id]))
        .collect();
    nb.sort_by_key(|v| v.to_string());
    assert_json_eq(&json!(nb), &want["neighbors_n3"], "neighbours");
    let mut stats = st.get_session_statistics("big");
    let mut want_stats = want["stats_big"].clone();
    if !BITWISE_NUMPY {
        stats["lsh_stats"] = Value::Null;
        want_stats["lsh_stats"] = Value::Null;
    }
    // Python counted while iterating a set of node ids, so these dicts' key order was hash-dependent
    for key in ["nodes_by_layer", "edges_by_type", "consolidation_states"] {
        stats[key] = sorted_keys(&stats[key]);
        want_stats[key] = sorted_keys(&want_stats[key]);
    }
    assert_json_eq(&stats, &want_stats, "session statistics");
    let hot: Vec<String> = st.get_hot_nodes("small", 1.5, 5).iter().map(|n| n.borrow().node_id.clone()).collect();
    assert_eq!(json!(hot), want["hot"]);
    let mut ents: Vec<String> = st
        .find_nodes_by_entities(&["ent1".into(), "ent3".into()], "big")
        .iter()
        .map(|n| n.borrow().node_id.clone())
        .collect();
    ents.sort();
    assert_eq!(json!(ents), want["entities"]);
    let mut dup = NeuralNode::new("dup", NodeLayer::Message, "dup");
    dup.embedding = Some(embs[10].clone());
    dup.session_key = "big".into();
    let err = st.save_node_with_dedup(&node_ref(dup), false).unwrap_err();
    assert_eq!(err, s(&want["dedup"]["error"]));
    let fresh = InMemoryNeuralGraphStorage::default();
    let mut plain = NeuralNode::new("plain", NodeLayer::Message, "no embedding");
    plain.session_key = "z".into();
    fresh.save_node(&node_ref(plain));
    let mut dup2 = NeuralNode::new("dup2", NodeLayer::Message, "d");
    dup2.embedding = Some(embs[1].clone());
    dup2.session_key = "z".into();
    let (saved, dup_id) = fresh.save_node_with_dedup(&node_ref(dup2), false).unwrap();
    assert_eq!(json!([saved, dup_id]), want["dedup_no_lsh"]);
    assert_eq!(json!([st.delete_node("n6"), st.delete_node("n6")]), want["deleted"]);
    let mut edges: Vec<String> = st.get_all_edges("big").iter().map(|e| e.borrow().edge_id.clone()).collect();
    edges.sort();
    assert_eq!(json!(edges), want["edges_after_delete"]);
    assert_eq!(json!(st.clear_session("small")), want["cleared"]);
    unpin_now();
}

#[test]
fn data_types_match() {
    pin_now(common::FIXED_NOW);
    let f = load("foundation.json");
    let want = &f["data_types"];
    let mut n = NeuralNode::new("x", NodeLayer::Topic, "c");
    n.session_key = "s".into();
    n.wave_amplitudes =
        [("temporal", 0.1), ("entity", 0.7), ("causal", 0.7)].iter().map(|(k, v)| (k.to_string(), *v)).collect();
    n.metadata.insert("producer_id".into(), json!("Bob"));
    assert_eq!(n.speaker_id(), s(&want["speaker"]));
    for t in ["entity", "temporal", "entity", "semantic"] {
        n.update_edge_counts(t);
    }
    n.update_dominant_dimension();
    assert_eq!(json!(n.diversity_score), want["diversity"]);
    assert_eq!(n.dominant_dimension, s(&want["dominant"]));
    let q = |pairs: &[(&str, f64)]| pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect();
    let fairness = [
        n.get_fairness_weight(&q(&[("entity", 0.9)])),
        n.get_fairness_weight(&q(&[("temporal", 0.5)])),
        n.get_fairness_weight(&q(&[("temporal", 0.1)])),
    ];
    assert_eq!(json!(fairness), want["fairness"]);
    let levels: Vec<f64> = (0..12).map(|_| n.activate(0.4, None)).collect();
    assert_eq!(json!(levels), want["activate"]["levels"]);
    assert_eq!(json!(n.heat_score), want["activate"]["heat"]);
    assert_eq!(n.refractory_until.unwrap().isoformat(), s(&want["activate"]["refractory_until"]));
    assert_json_eq(&n.to_dict(), &want["node_dict"], "node to_dict");
    let mut e = NeuralEdge::new("e", "a", "b", EdgeType::Causal).with_weight(0.7);
    e.sign = -1.0;
    for _ in 0..3 {
        e.activate();
    }
    assert_eq!(json!(e.ltp_boost), want["edge"]["ltp"]);
    assert_eq!(json!(e.effective_weight()), want["edge"]["weight"]);
    assert_eq!(json!(e.gate(0.8, 5.0)), want["edge"]["gate"]);
    assert_json_eq(&e.to_dict(), &want["edge"]["dict"], "edge to_dict");
    let rn = NeuralNode::from_dict(n.to_dict().as_object().unwrap()).unwrap();
    assert_json_eq(&rn.to_dict(), &want["roundtrip_node"], "node roundtrip");
    let re = NeuralEdge::from_dict(e.to_dict().as_object().unwrap()).unwrap();
    assert_json_eq(&re.to_dict(), &want["roundtrip_edge"], "edge roundtrip");
    let a = vecs(&want["cosine"]["a"]);
    let cos = [cosine_similarity(&a[0], &a[1]), cosine_similarity(&a[1], &a[2]), cosine_similarity(&a[0], &[])];
    assert_eq!(json!(cos), want["cosine"]["values"]);
    unpin_now();
}
