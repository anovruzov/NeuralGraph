//! Parity of `retrieval::retriever` and `retrieval::flash_retriever` with the Python
//! (`tests/golden/retrievers.json`, written by `gen_retrievers.py`). The retrieval runs mutate the graph (edge
//! activations, co-activation edges), so they replay in the generator's order inside one test and the final
//! graph is compared whole.

mod common;
mod engine_common;
mod retrievers_common;

use common::{assert_json_eq, load, FIXED_NOW};
use engine_common::{dump_graph, ids, install, scored, strings, DIM};
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::flash_retriever::{FlashConfig, FlashRetriever, HybridFlashRetriever};
use neuralgraph_research::retrieval::gating::{create_permissive_registry, GateRegistry};
use neuralgraph_research::retrieval::hierarchy::{HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::retriever::{
    compute_keyword_score, compute_wave_alignment, extract_entities, extract_keywords_with, extract_temporal_tokens,
    parse_node_date, NeuralRetriever, RetrieverConfig, RETRIEVER_STOP_WORDS,
};
use neuralgraph_research::retrieval::{
    EdgeType, InMemoryNeuralGraphStorage, NeuralGraphStorage, NeuralNode, NodeLayer, NodeRef, RetrievalStageConfig,
};
use retrievers_common::{build_graph, opt_waves, result_json};
use serde_json::{json, Map, Value};
use std::rc::Rc;

type Waves = IndexMap<String, f64>;

fn iso(d: Option<PyDateTime>) -> Value {
    d.map(|d| json!(d.isoformat())).unwrap_or(Value::Null)
}

fn sorted(set: &IndexSet<String>) -> Vec<String> {
    let mut v: Vec<String> = set.iter().cloned().collect();
    v.sort();
    v
}

fn wave_list(v: &Value) -> Vec<Option<Waves>> {
    v.as_array().unwrap().iter().map(opt_waves).collect()
}

fn check_analysis(f: &Value, msgs: &[NodeRef]) {
    let a = &f["analysis"];
    let storage: Rc<dyn NeuralGraphStorage> = Rc::new(InMemoryNeuralGraphStorage::default());
    let flash = FlashRetriever::new(storage, None);
    let sample: Vec<&NodeRef> = msgs.iter().step_by(5).collect();
    for (text, row) in a["texts"].as_array().unwrap().iter().zip(a["rows"].as_array().unwrap()) {
        let t = text.as_str().unwrap();
        let (tokens, target) = extract_temporal_tokens(t);
        let kw = extract_keywords_with(t, RETRIEVER_STOP_WORDS);
        let specifics = flash.extract_specifics(t);
        let got = json!({
            "entities": extract_entities(t),
            "keywords": sorted(&kw),
            "flash_keywords": sorted(&flash.extract_keywords(t)),
            "specifics": sorted(&specifics),
            "temporal_tokens": sorted(&tokens),
            "target_date": iso(target),
            "keyword_scores": sample.iter().map(|n| compute_keyword_score(&n.borrow().content, &kw)).collect::<Vec<_>>(),
            "specificity": sample.iter().map(|n| FlashRetriever::compute_specificity_bonus(&specifics, &n.borrow().content)).collect::<Vec<_>>(),
        });
        assert_json_eq(&got, row, &format!("analysis of {t:?}"));
    }
    let list: Vec<Waves> = wave_list(&a["wave_list"]).into_iter().map(Option::unwrap).collect();
    let alignment: Vec<Vec<f64>> =
        list.iter().map(|x| list.iter().map(|y| compute_wave_alignment(x, y)).collect()).collect();
    assert_json_eq(&json!(alignment), &a["alignment"], "wave alignment");
    let flash_alignment: Vec<Vec<f64>> =
        list.iter().map(|x| list[..6].iter().map(|y| compute_wave_alignment(x, y)).collect()).collect();
    assert_json_eq(&json!(flash_alignment), &a["flash_alignment"], "flash wave alignment");
    let factual: Vec<bool> =
        wave_list(&a["waves"]).iter().map(|w| NeuralRetriever::is_factual_query(w.as_ref())).collect();
    assert_json_eq(&json!(factual), &a["factual"], "factual");
    let created = PyDateTime::fromisoformat("2023-05-08T13:56:00+00:00").unwrap();
    let dates: Vec<Value> = a["resolved"]
        .as_array()
        .unwrap()
        .iter()
        .map(|resolved| {
            let mut n = NeuralNode::new("d", NodeLayer::Message, "x");
            n.created_at = created;
            if !resolved.is_null() {
                n.metadata.insert("resolved_date".into(), resolved.clone());
            }
            match parse_node_date(&n) {
                Ok(d) => json!({"ok": [iso(d), iso(d)]}),
                Err(e) => json!({"error": e.to_string()}),
            }
        })
        .collect();
    assert_json_eq(&json!(dates), &a["parse_node_date"], "parse_node_date");
}

fn custom_stages() -> Vec<RetrievalStageConfig> {
    let stage = |name: &str, max_candidates, edge_types, layer_filter, max_hops, score_threshold, vector| {
        RetrievalStageConfig {
            name: name.into(),
            max_candidates,
            edge_types,
            layer_filter,
            max_hops,
            score_threshold,
            use_vector_search: vector,
        }
    };
    vec![
        stage("recall", 40, vec![], Some(vec![NodeLayer::Message]), 0, 0.1, true),
        stage("everything", 60, vec![], None, 2, 0.05, false),
        stage("semantic_only", 7, vec![EdgeType::Semantic], Some(vec![NodeLayer::Episode]), 4, 0.01, false),
    ]
}

fn layers(v: &Value) -> Option<Vec<NodeLayer>> {
    match v.as_str() {
        None => None,
        Some("msg") => Some(vec![NodeLayer::Message]),
        Some("ep_topic") => Some(vec![NodeLayer::Episode, NodeLayer::Topic]),
        Some(other) => panic!("layer filter {other}"),
    }
}

fn opt_limit(v: &Value) -> Option<usize> {
    v.as_u64().map(|x| x as usize)
}

#[test]
fn retrievers_match_python() {
    install(FIXED_NOW);
    let f = load("retrievers.json");
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let st: Rc<dyn NeuralGraphStorage> = storage.clone();
    let g = build_graph(st.clone(), &f["specs"], "r1");
    assert_json_eq(&ids(&g.episodes), &f["episodes"], "episodes");
    assert_json_eq(&ids(&g.topics), &f["topics"], "topics");
    check_analysis(&f, &g.msgs);

    let questions: Vec<String> =
        f["analysis"]["texts"].as_array().unwrap()[..9].iter().map(|t| t.as_str().unwrap().to_string()).collect();
    let waves = wave_list(&f["analysis"]["waves"]);
    let emb = |q: &str| fake_embedding(q, DIM);

    let permissive: Rc<dyn GateRegistry> = Rc::new(create_permissive_registry());
    let mut retrievers: IndexMap<&str, NeuralRetriever> = IndexMap::new();
    retrievers.insert("default", NeuralRetriever::new(st.clone(), None, None, None));
    retrievers.insert(
        "temporal",
        NeuralRetriever::new(
            st.clone(),
            None,
            Some(g.temporal.clone()),
            Some(RetrieverConfig { final_limit: 12, ..Default::default() }),
        ),
    );
    retrievers.insert(
        "plain",
        NeuralRetriever::new(
            st.clone(),
            None,
            None,
            Some(RetrieverConfig {
                use_pattern_completion: false,
                use_wavefront_propagation: false,
                use_keyword_boost: false,
                record_co_activations: false,
                ..Default::default()
            }),
        ),
    );
    retrievers.insert(
        "custom",
        NeuralRetriever::new(
            st.clone(),
            Some(permissive),
            None,
            Some(RetrieverConfig {
                stages: custom_stages(),
                hop_decay_factor: 0.7,
                min_score_threshold: 0.5,
                final_limit: 15,
                ..Default::default()
            }),
        ),
    );
    retrievers.insert(
        "empty_stages",
        NeuralRetriever::new(
            st.clone(),
            None,
            None,
            Some(RetrieverConfig {
                stages: vec![],
                rerank_by_heat: false,
                use_wave_amplitudes: false,
                ..Default::default()
            }),
        ),
    );
    let stage_dicts = |r: &NeuralRetriever| json!(r.config().stages.iter().map(|s| s.to_dict()).collect::<Vec<_>>());
    assert_json_eq(&stage_dicts(&retrievers["custom"]), &f["custom_config_stages"], "custom stages");
    assert_json_eq(&stage_dicts(&retrievers["empty_stages"]), &f["default_config_stages"], "default stages");

    for c in f["calls"].as_array().unwrap() {
        let r = retrievers.get_mut(c["r"].as_str().unwrap()).unwrap();
        let q = &questions[c["q"].as_u64().unwrap() as usize];
        let lf = layers(&c["layers"]);
        let w = &waves[c["w"].as_u64().unwrap() as usize];
        let got = result_json(r.retrieve(q, &emb(q), "r1", opt_limit(&c["limit"]), lf.as_deref(), w.as_ref()));
        assert_json_eq(&got, &c["result"], &format!("retrieve {} q{} w{}", c["r"], c["q"], c["w"]));
    }

    let mut current_text = String::new();
    for run in f["names_runs"].as_array().unwrap() {
        let text = run["text"].as_str().unwrap();
        if text != current_text {
            current_text = text.to_string();
            for name in ["default", "temporal", "custom"] {
                retrievers.get_mut(name).unwrap().fast_recall_temporal_names = Some(extract_temporal_tokens(text));
            }
        }
        let r = retrievers.get_mut(run["r"].as_str().unwrap()).unwrap();
        let q = &questions[run["q"].as_u64().unwrap() as usize];
        let w = &waves[run["w"].as_u64().unwrap() as usize];
        let got = result_json(r.retrieve(q, &emb(q), "r1", None, None, w.as_ref()));
        assert_json_eq(
            &got,
            &run["result"],
            &format!("temporal names {text:?} {} q{} w{}", run["r"], run["q"], run["w"]),
        );
    }
    for r in retrievers.values_mut() {
        r.fast_recall_temporal_names = None;
    }

    let d = &retrievers["default"];
    let cases: Vec<(Vec<&str>, usize)> = vec![
        (vec!["Caroline"], 20),
        (vec!["Caroline", "ent:art"], 20),
        (vec!["ent:kids", "ent:family", "ent:kids"], 5),
        (vec![], 20),
        (vec!["Nobody"], 20),
        (vec!["Melanie", "ent:support", "Caroline"], 8),
    ];
    let by_entities: Vec<Value> = cases
        .iter()
        .map(|(e, lim)| {
            let e: Vec<String> = e.iter().map(|s| s.to_string()).collect();
            scored(&d.retrieve_by_entities(&e, "r1", *lim))
        })
        .collect();
    assert_json_eq(&json!(by_entities), &f["by_entities"], "retrieve_by_entities");

    let mut ctx = Vec::new();
    for node in [&g.msgs[5], &g.msgs[0], g.msgs.last().unwrap(), &g.episodes[0]] {
        for (b, a) in [(3, 3), (1, 5), (0, 0)] {
            for name in ["default", "temporal"] {
                let c = retrievers[name].retrieve_temporal_context(node, b, a);
                ctx.push(json!({"before": scored(&c.before), "after": scored(&c.after)}));
            }
        }
    }
    assert_json_eq(&json!(ctx), &f["temporal_context"], "temporal context");

    let d = &retrievers["default"];
    let mut hier = Vec::new();
    for qi in [0usize, 3, 8] {
        for layer in [NodeLayer::Message, NodeLayer::Episode, NodeLayer::Topic, NodeLayer::Persona] {
            for lim in [4, 10] {
                hier.push(scored(&d.retrieve_with_hierarchy(&emb(&questions[qi]), "r1", layer, lim)));
            }
        }
    }
    assert_json_eq(&json!(hier), &f["hierarchy"], "retrieve_with_hierarchy");

    let d = retrievers.get_mut("default").unwrap();
    let mut exp = Vec::new();
    for (qi, rounds, lim) in [(0usize, 0i64, 5usize), (1, 1, 8), (8, 2, 6), (4, 3, 4), (2, -1, 3)] {
        let q = &questions[qi];
        exp.push(result_json(d.retrieve_with_expansion(q, &emb(q), "r1", rounds, lim)));
    }
    assert_json_eq(&json!(exp), &f["expansion"], "retrieve_with_expansion");
    assert_json_eq(&json!(d.record_co_activations(&g.msgs[..1])), &f["record_one"], "record one");

    let every = FlashRetriever::new(st.clone(), Some(FlashConfig { min_score: -1.0, ..Default::default() }));
    let flash = FlashRetriever::new(st.clone(), None);
    for run in f["flash_runs"].as_array().unwrap() {
        let q = &questions[run["q"].as_u64().unwrap() as usize];
        let w = &waves[run["w"].as_u64().unwrap() as usize];
        let what = format!("flash q{} w{}", run["q"], run["w"]);
        assert_json_eq(&result_json(every.retrieve(q, &emb(q), "r1", Some(1000), w.as_ref())), &run["every"], &what);
        assert_json_eq(&result_json(flash.retrieve(q, &emb(q), "r1", None, w.as_ref())), &run["default"], &what);
    }
    assert_json_eq(
        &result_json(every.retrieve(&questions[0], &[], "r1", Some(1000), None)),
        &f["flash_no_embedding"],
        "flash without embedding",
    );

    let mut hybrids: IndexMap<&str, HybridFlashRetriever> = IndexMap::new();
    hybrids.insert("default", HybridFlashRetriever::new(st.clone(), None));
    hybrids.insert(
        "custom",
        HybridFlashRetriever::new(
            st.clone(),
            Some(FlashConfig {
                final_limit: 7,
                semantic_weight: 0.6,
                min_score: 0.3,
                speaker_match_boost: 0.5,
                ..Default::default()
            }),
        ),
    );
    for run in f["hybrid_runs"].as_array().unwrap() {
        let h = &hybrids[run["h"].as_str().unwrap()];
        let w = &waves[run["w"].as_u64().unwrap() as usize];
        let got = match run["q"].as_i64().unwrap() {
            -1 => h.retrieve("what happened yesterday?", &[], "r1", None, None),
            -2 => h.retrieve("what happened yesterday?", &emb("yesterday"), "r1", Some(3), w.as_ref()),
            qi => {
                let q = &questions[qi as usize];
                h.retrieve(q, &emb(q), "r1", None, w.as_ref())
            }
        };
        assert_json_eq(&result_json(got), &run["result"], &format!("hybrid {} q{} w{}", run["h"], run["q"], run["w"]));
    }

    // error paths
    let st2: Rc<dyn NeuralGraphStorage> = Rc::new(InMemoryNeuralGraphStorage::default());
    let h2 = HierarchyManager::new(st2.clone(), None);
    let aware = PyDateTime::fromisoformat("2023-05-08T13:00:00+00:00").unwrap();
    let mut bad = Vec::new();
    for (c, key, v) in [
        ("Caroline went to the support group", "producer_id", json!(7)),
        ("Caroline painted a lake", "speaker", json!(7)),
        ("Melanie likes pottery", "speaker", json!("Melanie")),
    ] {
        let mut metadata = Map::new();
        metadata.insert(key.into(), v);
        bad.push(h2.create_message_node(
            c,
            "bad",
            MessageNodeArgs {
                embedding: Some(emb(c)),
                wave_amplitudes: Some(IndexMap::from([("entity".to_string(), 0.7)])),
                entity_ids: Some(vec!["Caroline".into()]),
                created_at: Some(aware),
                metadata,
                ..Default::default()
            },
        ));
    }
    let mut naive = Vec::new();
    for (i, c) in ["Caroline went camping", "Melanie went camping too", "camping is fun"].iter().enumerate() {
        let created = PyDateTime::fromisoformat(&format!("2023-05-08T13:{i:02}:00")).unwrap();
        naive.push(h2.create_message_node(
            c,
            "naive",
            MessageNodeArgs {
                embedding: Some(emb(c)),
                wave_amplitudes: Some(IndexMap::from([("temporal".to_string(), 0.5)])),
                created_at: Some(created),
                ..Default::default()
            },
        ));
    }
    let q = "What did Caroline do at the support group camping?";
    let new_r = || NeuralRetriever::new(st2.clone(), None, None, None);
    let new_f = || FlashRetriever::new(st2.clone(), None);
    let e = &f["errors"];
    let check = |name: &str, got: Value| assert_json_eq(&got, &e[name], name);
    check("retrieve_bad", result_json(new_r().retrieve(q, &emb(q), "bad", None, None, None)));
    check("retrieve_bad_waves", result_json(new_r().retrieve(q, &emb(q), "bad", None, None, waves[2].as_ref())));
    check("retrieve_naive", result_json(new_r().retrieve(q, &emb(q), "naive", None, None, waves[2].as_ref())));
    check("retrieve_naive_nowave", result_json(new_r().retrieve(q, &emb(q), "naive", None, None, None)));
    check("flash_bad", result_json(new_f().retrieve(q, &emb(q), "bad", None, None)));
    check("flash_bad_noentity", result_json(new_f().retrieve("what about pottery", &emb(q), "bad", None, None)));
    check("flash_naive", result_json(new_f().retrieve(q, &emb(q), "naive", None, waves[5].as_ref())));
    check("flash_naive_nowave", result_json(new_f().retrieve(q, &emb(q), "naive", None, None)));
    check(
        "hybrid_bad",
        result_json(HybridFlashRetriever::new(st2.clone(), None).retrieve(
            "Tell me about Caroline",
            &emb("lake"),
            "bad",
            None,
            None,
        )),
    );
    assert_json_eq(&ids(&bad), &f["bad_ids"], "bad ids");
    assert_json_eq(&ids(&naive), &f["naive_ids"], "naive ids");

    assert_json_eq(&dump_graph(st.as_ref(), "r1"), &f["graph"], "graph after retrieval");
    assert_json_eq(&dump_graph(st2.as_ref(), "bad"), &f["graph_bad"], "error-session graph");
    let _ = strings;
}
