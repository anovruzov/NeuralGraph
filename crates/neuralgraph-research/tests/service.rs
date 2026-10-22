//! Parity of `NeuralGraphService` and its configurations with Python (`tests/golden/service.json`, written by
//! `gen_service.py`): the default and preset configurations; two-batch ingestion of LoCoMo conversation 0 and
//! `retrieve` for LoCoMo questions under the default, single-hop and multi-hop configurations (ranked ids and
//! scores, stages, the query wave dict after the call, the logged gate profile, the graph and its dynamic state
//! afterwards, statistics); component variants; ingestion shapes and error paths; consolidation, heat, STDP,
//! refractory state and gate tuning; wave memories and knowledge-graph extractions; external retrieval blending
//! with scripted sources and the open-domain tracer; the background consolidation task; the seeded LTP decay.
//!
//! The fixture was recorded with the clock frozen, uuids counting up, insertion-ordered sets and a seeded
//! `random.Random` in place of the global `random` module (see the generator); the Rust service takes the
//! equivalent `PyRandom`. Node and edge dumps are the `to_dict()` values in order, with a MESSAGE node's
//! embedding replaced by `"fake"` when it is `fake_embedding(content)`.

#![recursion_limit = "256"]

mod common;

use async_trait::async_trait;
use common::{assert_json_close, assert_json_eq, load, pin_now, FIXED_NOW};
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::numpy::bitwise_numpy;
use neuralgraph_research::pyrandom::PyRandom;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::consolidation::ConsolidationConfig;
use neuralgraph_research::retrieval::dialogue_linker::set_deterministic_hex_ids;
use neuralgraph_research::retrieval::external_retriever::{
    get_open_domain_tracer, ExternalResult, ExternalRetriever, WikipediaConfig,
};
use neuralgraph_research::retrieval::flash_retriever::FlashConfig;
use neuralgraph_research::retrieval::hierarchy::HierarchyConfig;
use neuralgraph_research::retrieval::query_router::QueryType;
use neuralgraph_research::retrieval::retriever::{RetrieverConfig, WaveAmplitudes};
use neuralgraph_research::retrieval::service::{
    create_multi_hop_optimized_config, create_single_hop_optimized_config, gate_profile_for_query_type, Entity,
    Episode, EpisodeTime, ExtractionResult, MemoryWave, NeuralGraphService, NeuralGraphServiceConfig,
    NeuralIngestionResult, Relation, SignalType, WaveSignal,
};
use neuralgraph_research::retrieval::temporal::TemporalConfig;
use neuralgraph_research::retrieval::{
    set_deterministic_uuids, EdgeType, InMemoryNeuralGraphStorage, NeuralGraphStorage, NodeRef, RetrievalResult,
};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::io::Write;
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use tracing_subscriber::fmt::MakeWriter;

const DIM: usize = 32;
const SK: &str = "conv0";

// ---------------------------------------------------------------------------------------------------------
// harness
// ---------------------------------------------------------------------------------------------------------

/// Log lines captured at DEBUG (the gate profile the service chose was only ever logged).
#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for Capture {
    type Writer = Capture;

    fn make_writer(&'a self) -> Capture {
        self.clone()
    }
}

impl Capture {
    fn take_gate_lines(&self) -> Vec<String> {
        let text = String::from_utf8(std::mem::take(&mut *self.0.lock().unwrap())).unwrap();
        text.lines().filter_map(|l| l.find("Gate profile selected").map(|i| l[i..].trim().to_string())).collect()
    }
}

fn capture_logs() -> (Capture, tracing::subscriber::DefaultGuard) {
    let capture = Capture::default();
    let subscriber = tracing_subscriber::fmt()
        .with_writer(capture.clone())
        .with_max_level(tracing::Level::DEBUG)
        .with_ansi(false)
        .without_time()
        .with_target(false)
        .finish();
    (capture, tracing::subscriber::set_default(subscriber))
}

/// Fresh counters, the frozen clock and a service over a new in-memory store (`gen_service.make`).
fn make(cfg: NeuralGraphServiceConfig, seed: i128) -> NeuralGraphService {
    set_deterministic_uuids(Some(0));
    set_deterministic_hex_ids(Some(0));
    pin_now(FIXED_NOW);
    let storage: Rc<dyn NeuralGraphStorage> = Rc::new(InMemoryNeuralGraphStorage::default());
    NeuralGraphService::with_rng(Some(storage), Some(cfg), PyRandom::new(seed))
}

/// The external blend (float32 `np.dot`) and the pattern completer behind the sequential retriever (float64
/// `np.dot`) are BLAS results: bit-exact where numpy used Accelerate (macOS), within rounding elsewhere.
#[track_caller]
fn assert_blas(actual: &Value, expected: &Value, what: &str) {
    if bitwise_numpy() {
        assert_json_eq(actual, expected, what);
    } else {
        assert_json_close(actual, expected, 1e-5, what);
    }
}

fn fixture() -> Value {
    load("service.json")
}

fn specs_of(v: &Value) -> Vec<Value> {
    v.as_array().expect("specs").clone()
}

fn episode(spec: &Value) -> Episode {
    let created_at = match spec.get("created_at") {
        None | Some(Value::Null) => None,
        Some(c) => Some(match c.get("dt") {
            Some(dt) => EpisodeTime::DateTime(PyDateTime::fromisoformat(dt.as_str().unwrap()).expect("isoformat")),
            None => EpisodeTime::Text(c["text"].as_str().unwrap().to_string()),
        }),
    };
    let filterable_metadata = match spec.get("entity_ids") {
        Some(ids @ Value::Array(_)) => {
            let mut m = Map::new();
            m.insert("entity_ids".into(), ids.clone());
            Some(m)
        }
        _ => None,
    };
    Episode {
        uid: spec["uid"].as_str().unwrap().to_string(),
        content: spec["content"].as_str().unwrap().to_string(),
        created_at,
        filterable_metadata,
        producer_id: spec.get("producer_id").and_then(Value::as_str).map(str::to_string),
        importance_score: spec.get("importance_score").and_then(Value::as_f64).unwrap_or(0.5),
    }
}

fn episodes(specs: &[Value]) -> Vec<Episode> {
    specs.iter().map(episode).collect()
}

fn embeddings_for(specs: &[Value]) -> Vec<Vec<f64>> {
    specs.iter().map(|s| fake_embedding(s["content"].as_str().unwrap(), DIM)).collect()
}

fn waves_of(v: &Value) -> WaveAmplitudes {
    v.as_object().map(|o| o.iter().map(|(k, x)| (k.clone(), x.as_f64().expect("number"))).collect()).unwrap_or_default()
}

fn waves_for(specs: &[Value]) -> Vec<WaveAmplitudes> {
    specs.iter().map(|s| waves_of(s.get("wave").unwrap_or(&Value::Null))).collect()
}

fn waves_json(w: &Option<WaveAmplitudes>) -> Value {
    match w {
        None => Value::Null,
        Some(w) => Value::Object(w.iter().map(|(k, v)| (k.clone(), json!(v))).collect()),
    }
}

fn process(service: &NeuralGraphService, specs: &[Value], sk: &str) -> NeuralIngestionResult {
    service.process_episodes(&episodes(specs), sk, Some(&embeddings_for(specs)), Some(&waves_for(specs)))
}

fn ingest_rec(r: &NeuralIngestionResult) -> Value {
    json!({
        "nodes_created": r.nodes_created,
        "temporal_edges_created": r.temporal_edges_created,
        "entity_edges_created": r.entity_edges_created,
        "semantic_edges_created": r.semantic_edges_created,
        "errors": r.errors,
        "node_ids": r.nodes.iter().map(|n| n.borrow().node_id.clone()).collect::<Vec<_>>(),
    })
}

fn result_rec(r: &RetrievalResult) -> Value {
    json!({
        "nodes": r.nodes.iter().map(|(n, s)| json!([n.borrow().node_id, s])).collect::<Vec<_>>(),
        "stages": r.stages_executed,
        "seen": r.total_candidates_seen,
        "co": r.co_activations_recorded,
    })
}

fn slim_node(n: &NodeRef) -> Value {
    let n = n.borrow();
    let mut d = n.to_dict();
    let fake = n.embedding.as_ref().is_some_and(|e| *e == fake_embedding(&n.content, DIM));
    if n.layer.value() == 0 && fake {
        d["embedding"] = json!("fake");
    }
    Value::Array(d.as_object().unwrap().values().cloned().collect())
}

fn graph(service: &NeuralGraphService, sk: &str) -> Value {
    let storage = service.storage();
    json!({
        "nodes": storage.get_nodes_by_session(sk, None).iter().map(slim_node).collect::<Vec<_>>(),
        "edges": storage.get_all_edges(sk).iter().map(|e| Value::Array(e.borrow().to_dict().as_object().unwrap().values().cloned().collect())).collect::<Vec<_>>(),
    })
}

fn edges_only(g: Value) -> Value {
    json!({"edges": g["edges"]})
}

fn digest(service: &NeuralGraphService, sk: &str, full: bool) -> Value {
    let storage = service.storage();
    let nodes = storage.get_nodes_by_session(sk, None);
    let edges = storage.get_all_edges(sk);
    if !full {
        return json!({
            "nodes": nodes.iter().map(|n| { let n = n.borrow(); json!([n.node_id, n.activation_level, n.heat_score, n.activation_count, n.consolidation_state.value()]) }).collect::<Vec<_>>(),
            "edges": edges.iter().map(|e| { let e = e.borrow(); json!([e.edge_id, e.base_weight, e.ltp_boost, e.activation_count]) }).collect::<Vec<_>>(),
        });
    }
    json!({
        "nodes": nodes.iter().map(|n| { let n = n.borrow(); json!([n.node_id, n.activation_level, n.heat_score, n.last_activated.isoformat(), n.activation_count, n.edge_type_counts, n.diversity_score, n.consolidation_state.value(), n.parent_id, n.child_ids]) }).collect::<Vec<_>>(),
        "edges": edges.iter().map(|e| { let e = e.borrow(); json!([e.edge_id, e.base_weight, e.confidence, e.ltp_boost, e.activation_count, e.last_activated.isoformat()]) }).collect::<Vec<_>>(),
    })
}

fn linker_summary(service: &NeuralGraphService) -> Value {
    match service.dialogue_linker(SK) {
        Some(l) => json!({"aggregators": l.aggregators().len(), "bindings": l.bindings().len()}),
        None => Value::Null,
    }
}

fn totals(service: &NeuralGraphService) -> Value {
    let (nodes, edges, retrievals) = service.totals();
    json!([nodes, edges, retrievals])
}

fn ingest_two_batches(service: &NeuralGraphService, specs: &[Value], split: usize) -> Value {
    let r1 = process(service, &specs[..split], SK);
    let r2 = process(service, &specs[split..], SK);
    json!([ingest_rec(&r1), ingest_rec(&r2)])
}

fn query_type_of(value: &str) -> QueryType {
    match value {
        "entity_attribute" => QueryType::EntityAttribute,
        "entity_action" => QueryType::EntityAction,
        "entity_relation" => QueryType::EntityRelation,
        "temporal_when" => QueryType::TemporalWhen,
        "temporal_sequence" => QueryType::TemporalSequence,
        "temporal_duration" => QueryType::TemporalDuration,
        "multi_hop" => QueryType::MultiHop,
        "adversarial" => QueryType::Adversarial,
        "open_domain" => QueryType::OpenDomain,
        other => panic!("unknown query type {other}"),
    }
}

/// The query type in a logged "Gate profile selected: X for query type Y" must map to X with this config.
fn check_gate_lines(gate: &Value, configured: &str) {
    for line in gate.as_array().unwrap() {
        let line = line.as_str().unwrap();
        let rest = line.strip_prefix("Gate profile selected: ").unwrap();
        let (name, qtype) = rest.split_once(" for query type ").unwrap();
        assert_eq!(gate_profile_for_query_type(configured, query_type_of(qtype)), name, "{line}");
    }
}

async fn run_questions(
    service: &NeuralGraphService,
    capture: &Capture,
    questions: &[String],
    limit: usize,
    variants: &[Option<WaveAmplitudes>],
    expected: Option<&Value>,
) -> Value {
    let mut rows = Vec::new();
    for (i, q) in questions.iter().enumerate() {
        let mut waves = variants[i % variants.len()].clone();
        let mut row = Map::new();
        row.insert("q".into(), json!(q));
        row.insert("waves_in".into(), waves_json(&waves));
        capture.take_gate_lines();
        match service.retrieve(q, &fake_embedding(q, DIM), SK, limit, None, waves.as_mut()).await {
            Ok(r) => row.insert("result".into(), result_rec(&r)),
            Err(e) => row.insert("error".into(), json!(e.to_string())),
        };
        row.insert("waves_out".into(), waves_json(&waves));
        let gate = capture.take_gate_lines();
        let expected_gate = expected.map(|e| e[i]["gate"].clone());
        if let Some(expected_gate) = &expected_gate {
            check_gate_lines(expected_gate, &service.config().gate_profile);
            assert_eq!(json!(gate), *expected_gate, "logged gate profile of {q:?}");
        }
        row.insert("gate".into(), json!(gate));
        rows.push(Value::Object(row));
    }
    Value::Array(rows)
}

fn wave_variants(f: &Value) -> Vec<Option<WaveAmplitudes>> {
    f["wave_variants"].as_array().unwrap().iter().map(|v| if v.is_null() { None } else { Some(waves_of(v)) }).collect()
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

fn config_json(c: &NeuralGraphServiceConfig) -> Value {
    json!({
        "enabled": c.enabled,
        "hierarchy_enabled": c.hierarchy_enabled,
        "temporal_enabled": c.temporal_enabled,
        "consolidation_enabled": c.consolidation_enabled,
        "auto_consolidation_interval_seconds": c.auto_consolidation_interval_seconds,
        "auto_temporal_linking": c.auto_temporal_linking,
        "auto_episode_segmentation": c.auto_episode_segmentation,
        "auto_consolidation_on_ingestion": c.auto_consolidation_on_ingestion,
        "apply_temporal_dynamics": c.apply_temporal_dynamics,
        "temporal_dynamics_probability": c.temporal_dynamics_probability,
        "hierarchy_config": c.hierarchy_config.as_ref().map(|_| "set"),
        "temporal_config": c.temporal_config.as_ref().map(|_| "set"),
        "consolidation_config": c.consolidation_config.as_ref().map(|_| "set"),
        "retriever_config": c.retriever_config.as_ref().map(|_| "set"),
        "use_flash_retriever": c.use_flash_retriever,
        "flash_config": c.flash_config.as_ref().map(|_| "set"),
        "use_electron_retriever": c.use_electron_retriever,
        "electron_config": c.electron_config.as_ref().map(|_| "set"),
        "dialogue_linking_enabled": c.dialogue_linking_enabled,
        "query_routing_enabled": c.query_routing_enabled,
        "query_routing_mode": c.query_routing_mode,
        "reranker_enabled": c.reranker_enabled,
        "reranker_type": c.reranker_type,
        "reranker_top_k": c.reranker_top_k,
        "gate_profile": c.gate_profile,
        "node_dedup_enabled": c.node_dedup_enabled,
        "node_dedup_threshold": c.node_dedup_threshold,
        "recency_retention_enabled": c.recency_retention_enabled,
        "temporal_consistency_enabled": c.temporal_consistency_enabled,
        "query_rewriting_enabled": c.query_rewriting_enabled,
        "query_rewriting_num_rewrites": c.query_rewriting_num_rewrites,
        "external_retrieval_enabled": c.external_retrieval_enabled,
        "external_retrieval_confidence_threshold": c.external_retrieval_confidence_threshold,
        "external_retrieval_min_session_hits": c.external_retrieval_min_session_hits,
        "external_retrieval_timeout_ms": c.external_retrieval_timeout_ms,
        "external_result_weight": c.external_result_weight,
        "external_max_results": c.external_max_results,
        "wikipedia_enabled": c.wikipedia_enabled,
        "wikipedia_extract_chars": c.wikipedia_extract_chars,
        "open_domain_tracing_enabled": c.open_domain_tracing_enabled,
        "attribution_logging_enabled": c.attribution_logging_enabled,
        "attribution_log_path": c.attribution_log_path,
        "attribution_buffer_size": c.attribution_buffer_size,
        "attribution_include_breakdowns": c.attribution_include_breakdowns,
        "use_neural_retrieval": c.use_neural_retrieval,
        "neural_retrieval_weight": c.neural_retrieval_weight,
        "max_retrieval_latency_ms": c.max_retrieval_latency_ms,
        "max_rerank_latency_ms": c.max_rerank_latency_ms,
        "fallback_on_timeout": c.fallback_on_timeout,
    })
}

// ---------------------------------------------------------------------------------------------------------
// configurations
// ---------------------------------------------------------------------------------------------------------

#[test]
fn configurations_match() {
    let f = fixture();
    // the nested component configs are `None` in all three (the generator's `asdict` records them as null)
    for (name, cfg) in [
        ("default", NeuralGraphServiceConfig::default()),
        ("single_hop", create_single_hop_optimized_config()),
        ("multi_hop", create_multi_hop_optimized_config()),
    ] {
        assert_json_eq(&config_json(&cfg), &f["configs"][name], name);
    }
}

// ---------------------------------------------------------------------------------------------------------
// the main scenarios
// ---------------------------------------------------------------------------------------------------------

async fn scenario_main(f: &Value, cfg: NeuralGraphServiceConfig, full_graph: bool, expected: &Value) {
    let (capture, _guard) = capture_logs();
    let specs = specs_of(&f["episodes"]);
    let questions = strings(&f["questions"]);
    let service = make(cfg, 1234);
    let mut out = Map::new();
    out.insert("ingest".into(), ingest_two_batches(&service, &specs, 24));
    if full_graph {
        out.insert("graph".into(), graph(&service, SK));
    }
    out.insert("stats_ingested".into(), service.get_statistics(SK));
    out.insert(
        "questions".into(),
        run_questions(&service, &capture, &questions, 10, &wave_variants(f), Some(&expected["questions"])).await,
    );
    out.insert("digest".into(), digest(&service, SK, full_graph));
    out.insert("stats_retrieved".into(), service.get_statistics(SK));
    out.insert("linker".into(), linker_summary(&service));
    assert_json_eq(&Value::Object(out), expected, "scenario");
}

#[tokio::test(flavor = "current_thread")]
async fn default_config_matches() {
    let f = fixture();
    scenario_main(&f, NeuralGraphServiceConfig::default(), true, &f["default"]).await;
}

#[tokio::test(flavor = "current_thread")]
async fn single_hop_config_matches() {
    let f = fixture();
    scenario_main(&f, create_single_hop_optimized_config(), false, &f["single_hop"]).await;
}

#[tokio::test(flavor = "current_thread")]
async fn multi_hop_config_matches() {
    let f = fixture();
    scenario_main(&f, create_multi_hop_optimized_config(), false, &f["multi_hop"]).await;
}

#[tokio::test(flavor = "current_thread")]
async fn component_variants_match() {
    let f = fixture();
    let (capture, _guard) = capture_logs();
    let specs = specs_of(&f["episodes"])[..30].to_vec();
    let questions: Vec<String> = strings(&f["questions"]).into_iter().step_by(3).collect();
    let d = NeuralGraphServiceConfig::default;
    let variants: Vec<(&str, NeuralGraphServiceConfig)> = vec![
        ("electron", NeuralGraphServiceConfig { use_electron_retriever: true, ..d() }),
        (
            "electron_no_dialogue",
            NeuralGraphServiceConfig { use_electron_retriever: true, dialogue_linking_enabled: false, ..d() },
        ),
        ("sequential", NeuralGraphServiceConfig { use_flash_retriever: false, ..d() }),
        ("no_dialogue", NeuralGraphServiceConfig { dialogue_linking_enabled: false, ..d() }),
        ("routing_hard", NeuralGraphServiceConfig { query_routing_mode: "hard".into(), ..d() }),
        ("routing_soft", NeuralGraphServiceConfig { query_routing_mode: "soft".into(), ..d() }),
        ("routing_off", NeuralGraphServiceConfig { query_routing_enabled: false, ..d() }),
        ("dynamics_always", NeuralGraphServiceConfig { temporal_dynamics_probability: 1.0, ..d() }),
        ("dynamics_never", NeuralGraphServiceConfig { apply_temporal_dynamics: false, ..d() }),
        ("no_hierarchy", NeuralGraphServiceConfig { hierarchy_enabled: false, ..d() }),
        ("no_temporal", NeuralGraphServiceConfig { temporal_enabled: false, ..d() }),
        ("no_consolidation", NeuralGraphServiceConfig { consolidation_enabled: false, ..d() }),
        (
            "no_segmentation",
            NeuralGraphServiceConfig { auto_episode_segmentation: false, auto_temporal_linking: false, ..d() },
        ),
        ("disabled", NeuralGraphServiceConfig { enabled: false, ..d() }),
        ("no_neural_retrieval", NeuralGraphServiceConfig { use_neural_retrieval: false, ..d() }),
        (
            "custom_components",
            NeuralGraphServiceConfig {
                flash_config: Some(FlashConfig { final_limit: 7, min_score: 0.2, ..FlashConfig::default() }),
                hierarchy_config: Some(HierarchyConfig {
                    min_messages_per_episode: 3,
                    max_messages_per_episode: 8,
                    ..HierarchyConfig::default()
                }),
                temporal_config: Some(TemporalConfig { max_time_gap_hours: 48.0, ..TemporalConfig::default() }),
                retriever_config: Some(RetrieverConfig { final_limit: 9, ..RetrieverConfig::default() }),
                ..d()
            },
        ),
        (
            "sequential_custom",
            NeuralGraphServiceConfig {
                use_flash_retriever: false,
                retriever_config: Some(RetrieverConfig { final_limit: 9, ..RetrieverConfig::default() }),
                ..d()
            },
        ),
    ];
    let expected = &f["variants"];
    assert_eq!(variants.len(), expected.as_object().unwrap().len());
    for (name, cfg) in variants {
        let service = make(cfg, 1234);
        let mut row = Map::new();
        row.insert("ingest".into(), ingest_two_batches(&service, &specs, 18));
        row.insert(
            "questions".into(),
            run_questions(&service, &capture, &questions, 8, &wave_variants(&f), Some(&expected[name]["questions"]))
                .await,
        );
        row.insert("digest".into(), digest(&service, SK, false));
        row.insert("stats".into(), service.get_statistics(SK));
        row.insert("linker".into(), linker_summary(&service));
        if name.contains("sequential") {
            assert_blas(&Value::Object(row), &expected[name], name);
        } else {
            assert_json_eq(&Value::Object(row), &expected[name], name);
        }
    }
}

#[tokio::test(flavor = "current_thread")]
async fn routing_modes_match() {
    let f = fixture();
    let (capture, _guard) = capture_logs();
    let specs = specs_of(&f["episodes"])[..30].to_vec();
    let all_questions = strings(&f["questions"]);
    // the generator's ROUTING_QUESTIONS: the recorded rows carry them
    let questions: Vec<String> = f["routing"]["boost"]["questions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["q"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(questions.len(), 10);
    assert!(!all_questions.contains(&questions[0]));
    let d = NeuralGraphServiceConfig::default;
    let variants: Vec<(&str, NeuralGraphServiceConfig)> = vec![
        ("boost", d()),
        ("hard", NeuralGraphServiceConfig { query_routing_mode: "hard".into(), ..d() }),
        ("soft", NeuralGraphServiceConfig { query_routing_mode: "soft".into(), ..d() }),
        ("electron", NeuralGraphServiceConfig { use_electron_retriever: true, ..d() }),
        ("sequential", NeuralGraphServiceConfig { use_flash_retriever: false, ..d() }),
    ];
    let waves = vec![None, Some(WaveAmplitudes::new()), Some(waves_of(&json!({"entity": 0.7, "temporal": 0.4})))];
    for (name, cfg) in variants {
        let service = make(cfg, 1234);
        ingest_two_batches(&service, &specs, 18);
        let expected = &f["routing"][name];
        let mut row = Map::new();
        row.insert(
            "questions".into(),
            run_questions(&service, &capture, &questions, 8, &waves, Some(&expected["questions"])).await,
        );
        row.insert("digest".into(), digest(&service, SK, false));
        row.insert("stats".into(), service.get_statistics(SK)["query_routing"].clone());
        if name == "sequential" {
            assert_blas(&Value::Object(row), expected, name);
        } else {
            assert_json_eq(&Value::Object(row), expected, name);
        }
    }
    let gates: Vec<&str> = f["routing"]["boost"]["questions"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|r| r["gate"].as_array().unwrap())
        .map(|g| g.as_str().unwrap())
        .collect();
    for wanted in ["multi_hop", "open_domain", "single_hop", "temporal"] {
        assert!(gates.iter().any(|g| g.contains(&format!("selected: {wanted} "))), "no {wanted} profile in {gates:?}");
    }
}

// ---------------------------------------------------------------------------------------------------------
// ingestion
// ---------------------------------------------------------------------------------------------------------

fn with_created(spec: &Value, created: Value) -> Value {
    let mut s = spec.clone();
    s["created_at"] = created;
    s
}

fn ingest_case(
    name: &str,
    cfg: NeuralGraphServiceConfig,
    specs: &[Value],
    embeddings: Option<&[Vec<f64>]>,
    wave_list: Option<&[WaveAmplitudes]>,
    only_edges: bool,
    out: &mut Map<String, Value>,
) {
    let service = make(cfg, 1234);
    let r = service.process_episodes(&episodes(specs), SK, embeddings, wave_list);
    let mut row = Map::new();
    row.insert("result".into(), ingest_rec(&r));
    let g = graph(&service, SK);
    row.insert("graph".into(), if only_edges { edges_only(g) } else { g });
    row.insert("totals".into(), totals(&service));
    row.insert("linker".into(), linker_summary(&service));
    out.insert(name.into(), Value::Object(row));
}

#[test]
fn ingestion_matches() {
    let f = fixture();
    let all = specs_of(&f["episodes"]);
    let base = all[..9].to_vec();
    let (emb, waves) = (embeddings_for(&base), waves_for(&base));
    let default = NeuralGraphServiceConfig::default;
    let mut out = Map::new();
    let mut case = |name: &str, specs: &[Value], emb: Option<&[Vec<f64>]>, w: Option<&[WaveAmplitudes]>| {
        ingest_case(name, default(), specs, emb, w, false, &mut out)
    };

    // created_at shapes
    let locomo_text = ["1:56 pm on 8 May, 2023", "1:57 pm on 8 May, 2023", "8 May 2023", "May 8, 2023"];
    let shapes: Vec<Value> = base
        .iter()
        .enumerate()
        .map(|(i, s)| match i % 6 {
            0 => with_created(s, json!({"text": format!("2023-05-08T13:{}:00", 56 + i % 3)})),
            1 => with_created(s, json!({"text": locomo_text[i % 4]})),
            2 => with_created(s, json!({"text": "yesterday-ish"})),
            3 => with_created(s, json!({"text": ""})),
            4 => with_created(s, json!({"dt": format!("2023-05-08T13:{:02}:00", (56 + i) % 60)})),
            _ => with_created(s, Value::Null),
        })
        .collect();
    case("created_at_shapes", &shapes, Some(&embeddings_for(&shapes)), Some(&waves_for(&shapes)));

    let locomo_all: Vec<Value> = base
        .iter()
        .enumerate()
        .map(|(i, s)| with_created(s, json!({"text": format!("1:{:02} pm on 8 May, 2023", i + 10)})))
        .collect();
    case("locomo_strings", &locomo_all, Some(&embeddings_for(&locomo_all)), Some(&waves_for(&locomo_all)));

    let iso_all: Vec<Value> = base
        .iter()
        .enumerate()
        .map(|(i, s)| with_created(s, json!({"text": format!("2023-05-08T{:02}:30:00+02:00", 8 + i)})))
        .collect();
    case("iso_strings", &iso_all, Some(&embeddings_for(&iso_all)), Some(&waves_for(&iso_all)));

    let none_all: Vec<Value> = base.iter().map(|s| with_created(s, Value::Null)).collect();
    case("no_created_at", &none_all, Some(&embeddings_for(&none_all)), Some(&waves_for(&none_all)));

    let mut mixed = base.clone();
    mixed[5] = with_created(&mixed[5], json!({"dt": "2023-05-08T13:59:00"}));
    case("naive_among_aware", &mixed, Some(&embeddings_for(&mixed)), Some(&waves_for(&mixed)));

    let mut bad = base.clone();
    bad[3] = with_created(&bad[3], json!({"text": "31 February 2023"}));
    bad[7] = with_created(&bad[7], json!({"text": "2023-05-08T25:00:00"}));
    case("bad_dates", &bad, Some(&embeddings_for(&bad)), Some(&waves_for(&bad)));

    case("no_embeddings", &base, None, None);
    case("short_embeddings", &base, Some(&emb[..5]), Some(&waves[..3]));
    let mut odd = emb.clone();
    odd[2] = Vec::new();
    case("empty_embedding", &base, Some(&odd), Some(&waves));
    case("empty_batch", &[], None, None);

    let mut meta = base.clone();
    meta[0]["entity_ids"] = Value::Null;
    meta[1]["entity_ids"] = json!([]);
    meta[2]["entity_ids"] = json!(["Caroline", "Caroline", "ent:caroline"]);
    meta[3]["producer_id"] = Value::Null;
    meta[4]["importance_score"] = json!(0.95);
    meta[5]["importance_score"] = json!(0.0);
    meta[6]["entity_ids"] = json!(["Caroline", "Melanie"]);
    meta[7]["entity_ids"] = json!(["Caroline"]);
    case("metadata_shapes", &meta, Some(&embeddings_for(&meta)), Some(&waves_for(&meta)));

    let mut blank = base.clone();
    blank[1]["content"] = json!("   ");
    blank[1]["producer_id"] = json!("Melanie");
    blank[0]["producer_id"] = json!("Caroline");
    case("whitespace_message", &blank, Some(&embeddings_for(&blank)), Some(&waves_for(&blank)));

    case("single_episode", &base[..1], Some(&emb[..1]), Some(&waves[..1]));

    let mut long = all.clone();
    for (i, s) in long.iter_mut().enumerate() {
        if i % 4 == 0 {
            s["entity_ids"] = json!(["ent:shared"]);
        }
    }
    ingest_case(
        "entity_decay",
        NeuralGraphServiceConfig { auto_episode_segmentation: false, ..default() },
        &long,
        Some(&embeddings_for(&long)),
        Some(&waves_for(&long)),
        true,
        &mut out,
    );

    let twins: Vec<Value> = (0..7)
        .map(|i| {
            let mut s = base[i % 3].clone();
            s["uid"] = json!(format!("twin-{i}"));
            s["created_at"] = json!({"dt": format!("2023-05-08T13:{}:00+00:00", 10 + i)});
            s
        })
        .collect();
    ingest_case(
        "semantic_twins",
        default(),
        &twins,
        Some(&embeddings_for(&twins)),
        Some(&waves_for(&twins)),
        false,
        &mut out,
    );

    // the same episodes twice: existing entity / semantic edges are strengthened
    let quiet = || NeuralGraphServiceConfig {
        dialogue_linking_enabled: false,
        auto_consolidation_on_ingestion: false,
        ..default()
    };
    let service = make(quiet(), 1234);
    let first = process(&service, &base, SK);
    let second = process(&service, &base, SK);
    let (total_nodes, total_edges, _) = service.totals();
    out.insert(
        "ingest_twice".into(),
        json!({
            "first": ingest_rec(&first),
            "second": ingest_rec(&second),
            "graph": graph(&service, SK),
            "digest": digest(&service, SK, false),
            "totals": [total_nodes, total_edges],
        }),
    );

    // the helpers called directly
    let service = make(NeuralGraphServiceConfig { auto_consolidation_on_ingestion: false, ..default() }, 1234);
    let r = process(&service, &base, SK);
    let nodes = r.nodes;
    let mut direct = Map::new();
    direct.insert("semantic_default".into(), json!(service.create_semantic_edges_batch(&nodes, SK, 0.7, 5)));
    direct.insert("semantic_low".into(), json!(service.create_semantic_edges_batch(&nodes, SK, 0.2, 2)));
    direct.insert("semantic_none".into(), json!(service.create_semantic_edges_batch(&nodes[..1], SK, 0.7, 5)));
    direct.insert(
        "entity_edges".into(),
        json!(nodes.iter().map(|n| service.create_entity_edges(n, SK)).collect::<Vec<_>>()),
    );
    direct.insert("graph".into(), graph(&service, SK));
    out.insert("direct".into(), Value::Object(direct));

    assert_json_eq(&Value::Object(out), &f["ingestion"], "ingestion");
}

// ---------------------------------------------------------------------------------------------------------
// consolidation, heat, STDP, refractory state, gate tuning
// ---------------------------------------------------------------------------------------------------------

fn gate_config_json(c: &neuralgraph_research::retrieval::gating::GateConfig) -> Value {
    json!({
        "threshold": c.threshold,
        "steepness": c.steepness,
        "recency_half_life_days": c.recency_half_life_days,
        "ltp_weight": c.ltp_weight,
        "confidence_weight": c.confidence_weight,
    })
}

fn cycle_json(r: &neuralgraph_research::retrieval::ConsolidationResult) -> Value {
    json!({"promoted": r.nodes_promoted, "evicted": r.nodes_evicted, "pruned": r.edges_pruned, "merged": r.nodes_merged, "hubs": r.hubs_created, "session": r.session_key})
}

fn result_dict(r: &neuralgraph_research::retrieval::ConsolidationResult, time: Value) -> Value {
    let mut d = r.to_dict();
    d["total_time_ms"] = time;
    d
}

#[tokio::test(flavor = "current_thread")]
async fn consolidation_and_dynamics_match() {
    let f = fixture();
    let d = NeuralGraphServiceConfig::default;
    let specs = specs_of(&f["spec_sets"]["s12_14"]);
    let questions = strings(&f["questions"]);
    let mut out = Map::new();

    // post-ingestion consolidation, then manual cycles
    let cons = ConsolidationConfig {
        promotion_heat_threshold: 0.55,
        eviction_heat_threshold: 0.35,
        min_cycle_interval_seconds: 0.0,
        ..ConsolidationConfig::default()
    };
    let service = make(NeuralGraphServiceConfig { consolidation_config: Some(cons), ..d() }, 1234);
    out.insert("ingest".into(), ingest_two_batches(&service, &specs, 14));
    out.insert("graph_after_ingest".into(), graph(&service, SK));
    out.insert("stats_after_ingest".into(), service.get_statistics(SK));
    let cycles: Vec<Value> = (0..2).map(|_| cycle_json(&service.run_consolidation(SK).unwrap())).collect();
    out.insert("cycles".into(), json!(cycles));
    out.insert("digest_after_cycles".into(), digest(&service, SK, false));

    // the default interval allows one cycle per minute
    let quiet = || NeuralGraphServiceConfig { auto_consolidation_on_ingestion: false, ..d() };
    let service = make(quiet(), 1234);
    process(&service, &specs, SK);
    let first = service.run_consolidation(SK).unwrap();
    let second = service.run_consolidation(SK).unwrap();
    out.insert("interval".into(), json!([result_dict(&first, Value::Null), result_dict(&second, Value::Null)]));

    // consolidation disabled
    let service = make(NeuralGraphServiceConfig { consolidation_enabled: false, ..d() }, 1234);
    process(&service, &specs, SK);
    let r = service.run_consolidation(SK).unwrap();
    out.insert(
        "disabled".into(),
        json!({
            "result": result_dict(&r, json!(r.total_time_ms == 0.0)),
            "boost": service.boost_node_heat("x", 0.5),
            "decay": service.decay_all_heat(SK),
            "stats": service.get_statistics(SK),
        }),
    );

    // exposed dynamics, heat and tuning
    let service = make(quiet(), 1234);
    let nodes = process(&service, &specs, SK).nodes;
    let ids: Vec<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
    let mut dynamics = Map::new();
    let deltas = [5.0, -5.0, 0.0, 30.0, -30.0];
    dynamics.insert(
        "stdp".into(),
        json!(deltas.iter().enumerate().map(|(i, d)| service.apply_stdp(&ids[i], &ids[i + 1], *d)).collect::<Vec<_>>()),
    );
    dynamics.insert(
        "stdp_missing".into(),
        json!([service.apply_stdp(&ids[0], &ids[5], 5.0), service.apply_stdp("nope", "none", 1.0)]),
    );
    dynamics.insert("summation_unknown".into(), json!(service.get_temporal_summation("nope")));
    dynamics.insert(
        "summation".into(),
        json!(ids[..6].iter().map(|i| service.get_temporal_summation(i)).collect::<Vec<_>>()),
    );
    dynamics.insert("patterns".into(), json!(service.analyze_temporal_patterns(SK)));
    dynamics.insert("refractory_unknown".into(), service.get_refractory_state("nope"));
    dynamics.insert("refractory_idle".into(), service.get_refractory_state(&ids[0]));
    nodes[1].borrow_mut().activate(1.0, None);
    nodes[2].borrow_mut().activate(0.5, Some(5000.0));
    service.storage().update_node(&nodes[1]);
    dynamics.insert(
        "refractory_active".into(),
        json!([service.get_refractory_state(&ids[1]), service.get_refractory_state(&ids[2])]),
    );
    dynamics.insert(
        "boost".into(),
        json!([
            service.boost_node_heat(&ids[0], 0.5),
            service.boost_node_heat(&ids[0], 20.0),
            service.boost_node_heat("nope", 1.0),
            service.boost_node_heat(&ids[3], 0.5)
        ]),
    );
    dynamics.insert("decay_all".into(), json!(service.decay_all_heat(SK)));
    let heat_args: [(f64, f64, usize); 5] =
        [(0.0, 10.0, 50), (0.0, 10.0, 5), (0.4, 0.6, 50), (3.0, 10.0, 50), (-1.0, 100.0, 0)];
    dynamics.insert(
        "by_heat".into(),
        json!(heat_args
            .iter()
            .map(|(lo, hi, limit)| service
                .get_nodes_by_heat(SK, *lo, *hi, *limit)
                .iter()
                .map(|(n, h)| json!([n.borrow().node_id, h]))
                .collect::<Vec<_>>())
            .collect::<Vec<_>>()),
    );
    dynamics.insert(
        "link".into(),
        json!([
            service.link_to_existing_memories(
                &ids[0],
                &[ids[5].clone(), "nope".into(), ids[6].clone()],
                EdgeType::Semantic
            ),
            service.link_to_existing_memories(&ids[1], &[ids[7].clone()], EdgeType::Causal),
            service.link_to_existing_memories(&ids[1], &[], EdgeType::Semantic)
        ]),
    );
    dynamics.insert("episode_ids".into(), json!(service.get_episode_node_ids(SK)));
    let mut registry = Map::new();
    registry.insert("initial".into(), gate_config_json(service.gate_registry().config()));
    for strictness in ["strict", "permissive", "balanced", "other"] {
        service.set_gate_strictness(strictness);
        registry.insert(strictness.into(), gate_config_json(service.gate_registry().config()));
    }
    service.adapt_gate_threshold(100, 50);
    service.adapt_gate_threshold(10, 50);
    registry.insert("after_adapt".into(), gate_config_json(service.gate_registry().config()));
    let untouched = {
        let retriever = service.retriever().borrow();
        Rc::as_ptr(&service.gate_registry()) as *const () != Rc::as_ptr(retriever.gates()) as *const ()
    };
    registry.insert("retriever_registry_untouched".into(), json!(untouched));
    dynamics.insert("gate".into(), Value::Object(registry));
    dynamics.insert("digest".into(), digest(&service, SK, false));
    dynamics.insert("stats".into(), service.get_statistics(SK));
    out.insert("dynamics".into(), Value::Object(dynamics));

    // no temporal manager: the helpers fall back
    let service = make(NeuralGraphServiceConfig { temporal_enabled: false, ..quiet() }, 1234);
    process(&service, &specs, SK);
    let nid = service.storage().get_nodes_by_session(SK, None)[0].borrow().node_id.clone();
    out.insert(
        "no_temporal".into(),
        json!({
            "stdp": service.apply_stdp(&nid, &nid, 1.0),
            "summation": service.get_temporal_summation(&nid),
            "patterns": service.analyze_temporal_patterns(SK),
            "refractory": service.get_refractory_state(&nid),
        }),
    );

    // clear and close
    let service = make(quiet(), 1234);
    process(&service, &specs, SK);
    service.retrieve(&questions[0], &fake_embedding(&questions[0], DIM), SK, 20, None, None).await.unwrap();
    out.insert("cleared".into(), json!(service.clear_session(SK)));
    out.insert("stats_cleared".into(), service.get_statistics(SK));
    out.insert("cleared_again".into(), json!(service.clear_session(SK)));
    service.close().await;
    out.insert("closed".into(), json!([service.closed(), service.consolidation_task_finished().is_none()]));
    service.close().await;

    assert_json_eq(&Value::Object(out), &f["consolidation"], "consolidation");
}

// ---------------------------------------------------------------------------------------------------------
// wave memories and knowledge-graph extraction
// ---------------------------------------------------------------------------------------------------------

fn opt_vec(v: &Value) -> Option<Vec<f64>> {
    v.as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap()).collect())
}

fn signal(spec: &Value) -> WaveSignal {
    let kind = match spec["type"].as_str().unwrap() {
        "temporal" => SignalType::Temporal,
        "entity" => SignalType::Entity,
        "relation" => SignalType::Relation,
        "causal" => SignalType::Causal,
        other => SignalType::Other(other.to_string()),
    };
    let mut s = WaveSignal::new(kind);
    let text = |k: &str| spec.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    s.raw_expression = text("raw_expression");
    s.entity_name = text("entity_name");
    s.cause = text("cause");
    s.effect = text("effect");
    s.amplitude = spec.get("amplitude").and_then(Value::as_f64).unwrap_or(0.5);
    s.embedding = spec.get("embedding").and_then(opt_vec);
    s.resolved_date = spec.get("resolved_date").and_then(Value::as_str).map(|d| PyDateTime::fromisoformat(d).unwrap());
    s
}

fn wave(spec: &Value) -> MemoryWave {
    MemoryWave {
        wave_id: spec["wave_id"].as_str().unwrap().to_string(),
        content: spec["content"].as_str().unwrap().to_string(),
        embedding: opt_vec(&spec["embedding"]),
        amplitudes: if spec["amplitudes"].is_null() { None } else { Some(waves_of(&spec["amplitudes"])) },
        signals: spec["signals"].as_array().unwrap().iter().map(signal).collect(),
    }
}

fn extraction(spec: &Value) -> ExtractionResult {
    ExtractionResult {
        entities: spec["entities"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| Entity {
                uid: e["uid"].as_str().unwrap().into(),
                name: e["name"].as_str().unwrap().into(),
                entity_type: e["entity_type"].as_str().unwrap().into(),
                mekb_score: e["mekb_score"].as_f64().unwrap(),
            })
            .collect(),
        relations: spec["relations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| Relation {
                uid: r["uid"].as_str().unwrap().into(),
                source_entity_uid: r["source_entity_uid"].as_str().unwrap().into(),
                target_entity_uid: r["target_entity_uid"].as_str().unwrap().into(),
                relation_type: r["relation_type"].as_str().unwrap().into(),
                weight: r["weight"].as_f64().unwrap(),
                confidence: r["confidence"].as_f64().unwrap(),
            })
            .collect(),
        confidence: spec["confidence"].as_f64().unwrap(),
    }
}

#[test]
fn wave_memory_and_extraction_match() {
    let f = fixture();
    let expected = &f["wave_memory"];
    let specs = specs_of(&f["spec_sets"]["s1_12"]);
    let service = make(NeuralGraphServiceConfig { auto_consolidation_on_ingestion: false, ..Default::default() }, 1234);
    let nodes = process(&service, &specs, SK).nodes;
    let mut out = Map::new();
    out.insert("wave_specs".into(), expected["wave_specs"].clone());
    let uids = [Some("ep-1"), None, Some("ep-3"), None, Some("ep-5")];
    let waves: Vec<Value> = expected["wave_specs"]
        .as_array()
        .unwrap()
        .iter()
        .zip(uids)
        .map(|(spec, uid)| match service.process_wave_memory(&wave(spec), SK, uid) {
            Some(node) => slim_node(&node),
            None => Value::Null,
        })
        .collect();
    out.insert("waves".into(), json!(waves));
    out.insert("extraction_spec".into(), expected["extraction_spec"].clone());
    let ex = extraction(&expected["extraction_spec"]);
    let first = nodes[0].borrow().node_id.clone();
    let counts: Vec<usize> = [None, Some(first.as_str()), Some("")]
        .into_iter()
        .map(|source| service.process_extraction_result(&ex, SK, source))
        .collect();
    out.insert("extraction".into(), json!(counts));
    out.insert("graph".into(), graph(&service, SK));
    let (n, e, _) = service.totals();
    out.insert("totals".into(), json!([n, e]));

    let disabled = make(NeuralGraphServiceConfig { enabled: false, ..Default::default() }, 1234);
    let w0 = wave(&expected["wave_specs"][0]);
    out.insert(
        "disabled".into(),
        json!([
            disabled.process_wave_memory(&w0, SK, None).map(|n| slim_node(&n)),
            disabled.process_extraction_result(&ex, SK, None)
        ]),
    );
    let bare = make(NeuralGraphServiceConfig { hierarchy_enabled: false, ..Default::default() }, 1234);
    out.insert(
        "no_hierarchy".into(),
        json!([
            bare.process_wave_memory(&w0, SK, None).map(|n| slim_node(&n)),
            bare.process_extraction_result(&ex, SK, Some("src")),
            graph(&bare, SK)
        ]),
    );
    assert_json_eq(&Value::Object(out), expected, "wave_memory");
}

// ---------------------------------------------------------------------------------------------------------
// external retrieval
// ---------------------------------------------------------------------------------------------------------

/// A source returning scripted results, or failing.
struct Scripted {
    name: String,
    results: Vec<ExternalResult>,
    error: Option<String>,
    calls: RefCell<Vec<Value>>,
}

impl Scripted {
    fn new(name: &str, results: Vec<ExternalResult>, error: Option<&str>) -> Rc<Self> {
        Rc::new(Scripted {
            name: name.into(),
            results,
            error: error.map(str::to_string),
            calls: RefCell::new(Vec::new()),
        })
    }
}

/// The registry owns its sources; the test keeps the other handle to read the recorded calls.
struct Handle(Rc<Scripted>);

#[async_trait(?Send)]
impl ExternalRetriever for Handle {
    fn source_name(&self) -> String {
        self.0.name.clone()
    }

    async fn retrieve(
        &self,
        query: &str,
        query_embedding: Option<&[f64]>,
        limit: usize,
        timeout_ms: f64,
    ) -> Result<Vec<ExternalResult>, String> {
        self.0.calls.borrow_mut().push(json!([query, query_embedding.map(|e| e.len()), limit, timeout_ms]));
        match &self.0.error {
            Some(e) => Err(e.clone()),
            None => Ok(self.0.results.clone()),
        }
    }

    async fn is_available(&self) -> bool {
        true
    }
}

fn ext(content: &str, source: &str, relevance: f64, embedding: Option<Vec<f64>>) -> ExternalResult {
    let mut r = ExternalResult::new(content, source);
    r.relevance_score = relevance;
    r.embedding = embedding;
    r
}

fn ext_results(duplicate_of: &str) -> Vec<ExternalResult> {
    let mut paris = ext(
        "Paris is the capital and largest city of France.",
        "scripted",
        0.9,
        Some(fake_embedding("What is the capital of France?", DIM)),
    );
    paris.source_url = Some("https://example.org/paris".into());
    paris.title = Some("Paris".into());
    let mut france = ext(
        "France is a country in Western Europe.",
        "scripted",
        0.6,
        Some(fake_embedding("France country Europe", DIM)),
    );
    france.title = Some("France".into());
    let mut duplicate = ext(duplicate_of, "scripted", 0.99, Some(fake_embedding(duplicate_of, DIM)));
    duplicate.title = Some("duplicate".into());
    vec![
        paris,
        france,
        ext("No embedding for this one, so only relevance counts.", "scripted", 0.5, None),
        ext("A zero embedding is never normalised.", "scripted", 0.7, Some(vec![0.0; DIM])),
        duplicate,
        ext("Tiny relevance.", "scripted", 0.0, Some(fake_embedding("tiny", DIM))),
    ]
}

fn ext_rec(r: &RetrievalResult) -> Value {
    json!({
        "nodes": r.nodes.iter().map(|(n, s)| {
            let id = n.borrow().node_id.clone();
            let slim = if id.starts_with("external_") { slim_node(n) } else { Value::Null };
            json!([id, s, slim])
        }).collect::<Vec<_>>(),
        "stages": r.stages_executed,
        "seen": r.total_candidates_seen,
        "co": r.co_activations_recorded,
    })
}

const TRACER_KEYS: [&str; 5] =
    ["total_queries", "open_domain_queries", "zero_session_hits", "external_called", "external_improved_results"];

fn tracer_counts() -> Vec<i64> {
    let stats = get_open_domain_tracer().get_stats();
    TRACER_KEYS.iter().map(|k| stats[*k].as_i64().unwrap()).collect()
}

type SourceMaker = Box<dyn Fn(&str) -> Rc<Scripted>>;

/// A named external scenario: configuration, sources and an optional query embedding.
type ExternalCase = (&'static str, NeuralGraphServiceConfig, Vec<SourceMaker>, Option<Vec<f64>>);

fn scripted_maker() -> SourceMaker {
    Box::new(|dup| Scripted::new("scripted", ext_results(dup), None))
}

async fn run_external(
    f: &Value,
    name: &str,
    cfg: NeuralGraphServiceConfig,
    sources: Vec<SourceMaker>,
    qvec: Option<Vec<f64>>,
) -> (NeuralGraphService, Value) {
    let specs = specs_of(&f["spec_sets"]["s12_14"]);
    let questions = [
        "What is the capital of France?",
        "Who wrote Pride and Prejudice?",
        f["questions"][0].as_str().unwrap(),
        f["questions"][3].as_str().unwrap(),
        "Define photosynthesis.",
        "What does Caroline think about Sweden and LGBTQ pride?",
    ];
    let tracing_enabled = cfg.open_domain_tracing_enabled;
    let service = make(cfg, 1234);
    process(&service, &specs, SK);
    let dup = specs[3]["content"].as_str().unwrap().to_string();
    let instances: Vec<Rc<Scripted>> = sources.iter().map(|s| s(&dup)).collect();
    for inst in &instances {
        service
            .external_registry()
            .expect("external retrieval enabled")
            .borrow_mut()
            .register(Rc::new(Handle(inst.clone())), true);
    }
    let before = tracer_counts();
    let mut rows = Vec::new();
    for q in questions {
        let qe = qvec.clone().unwrap_or_else(|| fake_embedding(q, DIM));
        match service.retrieve(q, &qe, SK, 6, None, None).await {
            Ok(r) => rows.push(ext_rec(&r)),
            Err(e) => rows.push(json!({"error": e.to_string()})),
        }
    }
    let after = tracer_counts();
    let delta: Map<String, Value> =
        TRACER_KEYS.iter().zip(after.iter().zip(&before)).map(|(k, (a, b))| (k.to_string(), json!(a - b))).collect();
    let traces = if tracing_enabled {
        let mut traces = get_open_domain_tracer().get_recent_traces(questions.len());
        for t in &mut traces {
            t["external_retrieval_latency_ms"] = Value::Null;
        }
        json!(traces)
    } else {
        Value::Null
    };
    let registry = service.external_registry().map(|r| {
        let mut names: Vec<String> = r.borrow().get_enabled_retrievers().iter().map(|x| x.source_name()).collect();
        names.sort();
        names
    });
    let row = json!({
        "rows": rows,
        "stats_delta": delta,
        "traces": traces,
        "registry": registry,
        "calls": instances.iter().map(|i| json!(i.calls.borrow().clone())).collect::<Vec<_>>(),
    });
    let _ = name;
    (service, row)
}

#[tokio::test(flavor = "current_thread")]
async fn external_retrieval_matches() {
    let f = fixture();
    let expected = &f["external"];
    let mut out = Map::new();
    let d = NeuralGraphServiceConfig::default;
    let forced = || NeuralGraphServiceConfig {
        external_retrieval_enabled: true,
        external_retrieval_min_session_hits: 100,
        ..d()
    };
    let traced = || NeuralGraphServiceConfig { open_domain_tracing_enabled: true, ..forced() };
    let other: SourceMaker =
        Box::new(|_| Scripted::new("other", vec![ext("Other source text.", "other", 0.8, None)], None));
    let broken: SourceMaker = Box::new(|_| Scripted::new("broken", vec![], Some("boom")));
    let empty: SourceMaker = Box::new(|_| Scripted::new("empty", vec![], None));

    let cases: Vec<ExternalCase> = vec![
        ("blend", traced(), vec![scripted_maker()], None),
        (
            "weight",
            NeuralGraphServiceConfig { external_result_weight: 1.5, external_max_results: 2, ..forced() },
            vec![scripted_maker()],
            None,
        ),
        (
            "only_open_domain",
            NeuralGraphServiceConfig {
                external_retrieval_enabled: true,
                external_retrieval_min_session_hits: 0,
                open_domain_tracing_enabled: true,
                ..d()
            },
            vec![scripted_maker()],
            None,
        ),
        (
            "confidence",
            NeuralGraphServiceConfig {
                external_retrieval_enabled: true,
                external_retrieval_min_session_hits: 0,
                external_retrieval_confidence_threshold: 0.0,
                open_domain_tracing_enabled: true,
                ..d()
            },
            vec![scripted_maker()],
            None,
        ),
        ("two_sources", traced(), vec![scripted_maker(), other], None),
        ("failing_source", traced(), vec![broken], None),
        ("empty_source", traced(), vec![empty], None),
        ("no_sources", traced(), vec![], None),
        ("untraced", forced(), vec![scripted_maker()], None),
        ("zero_query_embedding", forced(), vec![scripted_maker()], Some(vec![0.0; DIM])),
        ("wrong_size_query", forced(), vec![scripted_maker()], Some(fake_embedding("x", 16))),
        (
            "electron",
            NeuralGraphServiceConfig { use_electron_retriever: true, ..forced() },
            vec![scripted_maker()],
            None,
        ),
        (
            "wikipedia_registry",
            NeuralGraphServiceConfig {
                external_retrieval_enabled: true,
                wikipedia_enabled: true,
                wikipedia_extract_chars: 300,
                external_retrieval_timeout_ms: 500.0,
                external_retrieval_min_session_hits: -1,
                external_retrieval_confidence_threshold: 2.0,
                ..d()
            },
            vec![],
            None,
        ),
    ];
    for (name, cfg, sources, qvec) in cases {
        let (service, mut row) = run_external(&f, name, cfg, sources, qvec).await;
        if name == "wikipedia_registry" {
            let w: &WikipediaConfig = service.wikipedia_retriever().expect("wikipedia source").config();
            row["wikipedia_config"] = json!({
                "api_url": w.api_url,
                "search_limit": w.search_limit,
                "extract_chars": w.extract_chars,
                "timeout_seconds": w.timeout_seconds,
                "cache_ttl_hours": w.cache_ttl_hours,
                "user_agent": w.user_agent,
            });
        }
        out.insert(name.into(), row);
    }

    // the Jaccard duplicate check, called directly
    let service = make(d(), 1234);
    let pairs = [
        ("", "x"),
        ("x", ""),
        ("   ", "a"),
        ("a b c d e", "a b c d e"),
        ("a b c d e", "A B C D E"),
        ("a b c d e", "a b c d f"),
        ("a b c d e", "a b c d e f"),
        ("a b c d e f g h i j", "a b c d e f g h i k"),
        ("Hello, world", "hello world"),
        ("x y", "y x"),
        ("a a a", "a"),
        ("a b c d", "a b c d e"),
        ("a b", "a b c d"),
    ];
    out.insert(
        "duplicates".into(),
        json!(pairs
            .iter()
            .map(|(a, b)| json!([
                a,
                b,
                service.is_content_duplicate(a, b, 0.8),
                service.is_content_duplicate(a, b, 0.5)
            ]))
            .collect::<Vec<_>>()),
    );
    assert_blas(&Value::Object(out), expected, "external");
}

// ---------------------------------------------------------------------------------------------------------
// the background task, the seeded decay, retrieval errors and contexts
// ---------------------------------------------------------------------------------------------------------

#[tokio::test(flavor = "current_thread")]
async fn auto_consolidation_matches() {
    let f = fixture();
    let expected = &f["auto_consolidation"];
    let specs = specs_of(&f["spec_sets"]["s12_14"]);
    tokio::task::LocalSet::new()
        .run_until(async {
            let cons = ConsolidationConfig {
                promotion_heat_threshold: 5.0,
                eviction_heat_threshold: 0.99,
                eviction_age_threshold_days: 0.0,
                eviction_recency_threshold_days: 0.0,
                min_cycle_interval_seconds: 0.0,
                recency_aware_retention: false,
                factual_node_protection: false,
                ..ConsolidationConfig::default()
            };
            let node_count =
                |s: &NeuralGraphService| s.get_statistics(SK)["consolidation"]["node_count"].as_i64().unwrap();
            let service = make(
                NeuralGraphServiceConfig {
                    consolidation_config: Some(cons),
                    auto_consolidation_interval_seconds: 0.02,
                    auto_consolidation_on_ingestion: false,
                    ..Default::default()
                },
                1234,
            );
            process(&service, &specs, SK);
            let before = node_count(&service);
            service.start_auto_consolidation(SK);
            let running = service.consolidation_task_finished().is_some();
            service.start_auto_consolidation(SK); // a second start is a no-op
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            let after = node_count(&service);
            service.stop_auto_consolidation().await;
            let mut out = Map::new();
            out.insert(
                "task".into(),
                json!({
                    "running_after_start": running,
                    "task_after_stop": service.consolidation_task_finished().is_none(),
                    "nodes_before": before,
                    "evicted_something": after < before,
                    "nodes_after_positive": after >= 0,
                }),
            );
            service.stop_auto_consolidation().await; // nothing to stop
            service.close().await;
            out.insert("closed".into(), json!(service.closed()));
            // starting after close: the loop ends at once
            service.start_auto_consolidation(SK);
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            out.insert("after_close_done".into(), json!(service.consolidation_task_finished() == Some(true)));
            service.stop_auto_consolidation().await;
            // no consolidation manager: the loop idles
            let idle = make(
                NeuralGraphServiceConfig {
                    consolidation_enabled: false,
                    auto_consolidation_interval_seconds: 0.01,
                    ..Default::default()
                },
                1234,
            );
            idle.start_auto_consolidation(SK);
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            out.insert("idle_running".into(), json!(idle.consolidation_task_finished() == Some(false)));
            idle.close().await;
            out.insert("idle_closed".into(), json!(idle.consolidation_task_finished().is_none()));
            assert_json_eq(&Value::Object(out), expected, "auto_consolidation");
        })
        .await;
}

#[tokio::test(flavor = "current_thread")]
async fn seeded_ltp_decay_matches() {
    let f = fixture();
    let expected = &f["rng"];
    let specs = specs_of(&f["spec_sets"]["s12_14"]);
    let questions = strings(&f["questions"]);
    let mut out = Map::new();
    for (name, p, seed) in
        [("p05_seed7", 0.5, 7), ("p05_seed99", 0.5, 99), ("p01_seed3", 0.1, 3), ("p1", 1.0, 5), ("p0", 0.0, 5)]
    {
        let service = make(
            NeuralGraphServiceConfig {
                temporal_dynamics_probability: p,
                query_routing_enabled: false,
                dialogue_linking_enabled: false,
                ..Default::default()
            },
            seed,
        );
        process(&service, &specs, SK);
        let mut sums = Vec::new();
        for q in &questions[..12] {
            service.retrieve(q, &fake_embedding(q, DIM), SK, 6, None, None).await.unwrap();
            let edges = service.storage().get_all_edges(SK);
            let mut total = 0.0;
            for e in &edges {
                total += e.borrow().ltp_boost;
            }
            let boosted = edges.iter().filter(|e| e.borrow().ltp_boost > 1.0).count();
            sums.push(json!([total, boosted]));
        }
        out.insert(name.into(), json!({"ltp": sums}));
    }
    assert_json_eq(&Value::Object(out), expected, "rng");

    // the generator is injectable and reseedable
    let a = make(NeuralGraphServiceConfig::default(), 1);
    a.seed_rng(7);
    let b = make(NeuralGraphServiceConfig::default(), 7);
    b.set_rng(PyRandom::new(7));
    let _ = (a, b);
}

fn ctx_json(c: &neuralgraph_research::retrieval::service::RetrievalContext) -> Value {
    let scored =
        |v: &[(NodeRef, f64)]| -> Vec<Value> { v.iter().map(|(n, s)| json!([n.borrow().node_id, s])).collect() };
    json!({
        "nodes": scored(&c.nodes),
        "stages": c.stages_executed,
        "temporal": match &c.temporal_context {
            Some(t) => json!({"before": scored(&t.before), "after": scored(&t.after)}),
            None => json!({}),
        },
        "hierarchy": c.hierarchy_summary.clone().unwrap_or_else(|| json!({})),
    })
}

#[tokio::test(flavor = "current_thread")]
async fn stdp_on_close_creation_times_matches() {
    let f = fixture();
    let expected = &f["stdp"];
    let (capture, _guard) = capture_logs();
    let specs = specs_of(&f["spec_sets"]["s1_12"]);
    let questions = strings(&f["questions"]);
    let fast: Vec<Value> = specs
        .iter()
        .enumerate()
        .map(|(i, s)| with_created(s, json!({"dt": format!("2023-05-08T13:56:00.{:06}+00:00", i * 25000)})))
        .collect();
    assert_eq!(json!(fast), expected["fast"]);
    let service = make(
        NeuralGraphServiceConfig {
            temporal_dynamics_probability: 0.5,
            query_routing_enabled: false,
            dialogue_linking_enabled: false,
            auto_consolidation_on_ingestion: false,
            ..Default::default()
        },
        11,
    );
    let r = process(&service, &fast, SK);
    let mut out = Map::new();
    out.insert("ingest".into(), ingest_rec(&r));
    out.insert("fast".into(), json!(fast));
    out.insert(
        "questions".into(),
        run_questions(&service, &capture, &questions[..8], 6, &[None], Some(&expected["questions"])).await,
    );
    out.insert("digest".into(), digest(&service, SK, false));
    assert_json_eq(&Value::Object(out), expected, "stdp");
}

#[tokio::test(flavor = "current_thread")]
async fn retrieval_errors_and_contexts_match() {
    let f = fixture();
    let expected = &f["errors"];
    let (capture, _guard) = capture_logs();
    let specs = specs_of(&f["spec_sets"]["s1_12"]);
    let questions = strings(&f["questions"]);
    let none_variant: Vec<Option<WaveAmplitudes>> = vec![None];
    let mut out = Map::new();

    // naive and aware creation times among the results: STDP subtracts them outside any `try`
    for (key, totals_key, dynamics) in [("rows", Some("totals"), true), ("rows_no_dynamics", None, false)] {
        let service = make(
            NeuralGraphServiceConfig {
                query_routing_enabled: false,
                dialogue_linking_enabled: false,
                auto_temporal_linking: false,
                apply_temporal_dynamics: dynamics,
                ..Default::default()
            },
            1234,
        );
        let r = process(&service, &specs, SK);
        let naive = PyDateTime::naive(r.nodes[2].borrow().created_at.naive);
        r.nodes[2].borrow_mut().created_at = naive;
        service.storage().update_node(&r.nodes[2]);
        out.insert(
            key.into(),
            run_questions(&service, &capture, &questions[..6], 8, &none_variant, Some(&expected[key])).await,
        );
        if totals_key.is_some() {
            let (_, _, retrievals) = service.totals();
            out.insert("totals".into(), json!([retrievals]));
        }
    }

    let service = make(NeuralGraphServiceConfig::default(), 1234);
    process(&service, &specs, SK);
    let mut contexts = Vec::new();
    for q in &questions[..5] {
        contexts.push(ctx_json(&service.retrieve_with_context(q, &fake_embedding(q, DIM), SK, 5).await.unwrap()));
    }
    out.insert("context".into(), json!(contexts));
    let empty = make(NeuralGraphServiceConfig::default(), 1234);
    let c = empty.retrieve_with_context("anything", &fake_embedding("anything", DIM), SK, 5).await.unwrap();
    out.insert("context_empty".into(), ctx_json(&c));
    let bare = make(
        NeuralGraphServiceConfig { hierarchy_enabled: false, temporal_enabled: false, ..Default::default() },
        1234,
    );
    process(&bare, &specs, SK);
    let c = bare.retrieve_with_context("anything", &fake_embedding("anything", DIM), SK, 5).await.unwrap();
    out.insert("context_bare".into(), ctx_json(&c));
    assert_json_eq(&Value::Object(out), expected, "errors");
}
