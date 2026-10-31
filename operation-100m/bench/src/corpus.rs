//! Deterministic, counter-based memory corpus.
//!
//! Every record is a pure function of `(seed, bank, index)`: there is no sequential RNG state, so any record or
//! query can be recomputed on its own. `critic/spec.py` reimplements these rules independently from the same
//! `spec/corpus_spec.json`; if the two disagree the Critic fails the run.
//!
//! Record layout of a bank of `n` memories (fractions from the spec):
//! `[0, slots)` first version of a fact slot (entity, attribute); `[slots, +updates)` a second version of slot
//! `i - slots`, which supersedes the first; `[.., +duplicates)` paraphrases restating a slot's current value;
//! `[.., +contradictions)` a conflicting value for slot `updates + j` from a second source, both left active;
//! the rest are distractors that name an entity and an attribute but state no value (hard negatives).
//! Entity `e` and `e ^ 1` are twins whose names differ in one syllable.

use serde::Deserialize;
use std::collections::HashMap;

pub const SPEC_JSON: &str = include_str!("../spec/corpus_spec.json");

#[derive(Deserialize, Clone)]
pub struct Attr {
    pub key: String,
    pub phrase: String,
    pub values: Vec<String>,
    pub memory: Vec<String>,
    pub query: Vec<String>,
}

#[derive(Deserialize, Clone)]
pub struct Fractions {
    pub slots: u64,
    pub updates: u64,
    pub duplicates: u64,
    pub contradictions: u64,
}

#[derive(Deserialize, Clone)]
pub struct Spec {
    pub version: u32,
    pub syllables: Vec<String>,
    pub fractions_percent: Fractions,
    pub entities_per_memory_divisor: u64,
    pub min_entities: u64,
    pub filler: Vec<String>,
    pub attributes: Vec<Attr>,
    pub distractor: Vec<String>,
    pub historical_query: String,
    pub time_base: String,
}

impl Spec {
    pub fn load() -> Spec {
        serde_json::from_str(SPEC_JSON).expect("corpus spec parses")
    }
}

// field ids for the counter hash (mirrored in critic/spec.py)
pub const F_ATTR_OFF: u64 = 1;
pub const F_VAL: u64 = 2; // +0 first value, +1 update, +2 contradiction
pub const F_TMPL: u64 = 6;
pub const F_FILLN: u64 = 7;
pub const F_DAY: u64 = 8;
pub const F_IMP: u64 = 9;
pub const F_DUP: u64 = 10;
pub const F_DIS_E: u64 = 11;
pub const F_DIS_A: u64 = 12;
pub const F_CHAT: u64 = 13;
pub const F_FILL0: u64 = 20; // +k for the k-th filler sentence
pub const F_Q_TYPE: u64 = 40;
pub const F_Q_SLOT: u64 = 41;
pub const F_Q_TMPL: u64 = 42;
pub const F_Q_ENT: u64 = 43;
pub const F_Q_ATTR: u64 = 44;
// vector components
pub const V_ENT_BASE: u64 = 101;
pub const V_ENT_TWIN: u64 = 102;
pub const V_ATTR: u64 = 103;
pub const V_VAL: u64 = 104;
pub const V_NOISE: u64 = 105;
pub const V_QNOISE: u64 = 106;
pub const V_OTHER_ATTR: u64 = 107;

const GOLDEN: u64 = 0x9E37_79B9_7F4A_7C15;
const K2: u64 = 0xD1B5_4A32_D192_ED03;

/// splitmix64 finaliser
pub fn mix(z: u64) -> u64 {
    let mut z = z.wrapping_add(GOLDEN);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

pub fn h(seed: u64, i: u64, f: u64) -> u64 {
    mix(seed ^ mix(i.wrapping_mul(K2) ^ f))
}

pub fn bank_seed(global_seed: u64, bank: u32) -> u64 {
    mix(global_seed ^ (bank as u64).wrapping_mul(GOLDEN))
}

pub fn query_seed(bank_seed: u64) -> u64 {
    mix(bank_seed ^ 0x5157)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Fact,
    Update,
    Duplicate,
    Contradiction,
    Distractor,
}

impl Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Kind::Fact => "fact",
            Kind::Update => "update",
            Kind::Duplicate => "duplicate",
            Kind::Contradiction => "contradiction",
            Kind::Distractor => "distractor",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Layout {
    pub n: u64,
    pub slots: u64,
    pub updates: u64,
    pub dups: u64,
    pub contras: u64,
    pub distractors: u64,
    pub entities: u64,
}

impl Layout {
    pub fn new(n: u64, spec: &Spec) -> Layout {
        let f = &spec.fractions_percent;
        let slots = n * f.slots / 100;
        let updates = n * f.updates / 100;
        let dups = n * f.duplicates / 100;
        let contras = n * f.contradictions / 100;
        let distractors = n - slots - updates - dups - contras;
        let entities = (n / spec.entities_per_memory_divisor).max(spec.min_entities);
        Layout { n, slots, updates, dups, contras, distractors, entities }
    }

    pub fn kind(&self, i: u64) -> (Kind, u64) {
        let mut base = self.slots;
        if i < base {
            return (Kind::Fact, i);
        }
        if i < base + self.updates {
            return (Kind::Update, i - base);
        }
        base += self.updates;
        if i < base + self.dups {
            return (Kind::Duplicate, i - base);
        }
        base += self.dups;
        if i < base + self.contras {
            return (Kind::Contradiction, i - base);
        }
        base += self.contras;
        (Kind::Distractor, i - base)
    }

    pub fn update_index(&self, slot: u64) -> u64 {
        self.slots + slot
    }

    pub fn contra_index(&self, slot: u64) -> u64 {
        self.slots + self.updates + self.dups + (slot - self.updates)
    }

    pub fn is_contradicted(&self, slot: u64) -> bool {
        slot >= self.updates && slot < self.updates + self.contras
    }
}

pub fn memory_id(bank: u32, i: u64) -> String {
    format!("b{bank:02}m{i:09}")
}

/// One generated memory, before it becomes an engine `Memory`.
#[derive(Clone, Debug)]
pub struct Record {
    pub index: u64,
    pub id: String,
    pub kind: Kind,
    pub slot: Option<u64>,
    pub entity: u64,
    pub attr: usize,
    pub value: Option<usize>,
    pub text: String,
    pub observed_day: u64,
    pub importance: f64,
    pub chat: u64,
    pub status: &'static str,
    pub superseded_by: Option<String>,
    pub speaker: &'static str,
}

pub struct Corpus {
    pub spec: Spec,
    pub bank: u32,
    pub seed: u64,
    pub layout: Layout,
}

fn capitalise(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

impl Corpus {
    pub fn new(global_seed: u64, bank: u32, n: u64) -> Corpus {
        let spec = Spec::load();
        let layout = Layout::new(n, &spec);
        Corpus { spec, bank, seed: bank_seed(global_seed, bank), layout }
    }

    /// Unique display name; twins `e` and `e ^ 1` differ only in the first syllable.
    pub fn entity_name(&self, e: u64) -> String {
        const M29: u64 = (1 << 29) - 1;
        let perm = ((e >> 1).wrapping_mul(0x2545_F491).wrapping_add(self.seed)) & M29;
        let p = (perm << 1) | (e & 1);
        let syl = |k: u32| self.spec.syllables[((p >> (5 * k)) & 31) as usize].as_str();
        let first = format!("{}{}{}", syl(0), syl(1), syl(2));
        let last = format!("{}{}{}", syl(3), syl(4), syl(5));
        format!("{} {}", capitalise(&first), capitalise(&last))
    }

    pub fn entity_id(&self, e: u64) -> String {
        self.entity_name(e).to_lowercase()
    }

    pub fn slot_entity(&self, s: u64) -> u64 {
        s % self.layout.entities
    }

    pub fn slot_attr(&self, s: u64) -> usize {
        let e = self.slot_entity(s);
        (((s / self.layout.entities) + h(self.seed, e, F_ATTR_OFF)) % self.spec.attributes.len() as u64) as usize
    }

    fn n_values(&self, a: usize) -> u64 {
        self.spec.attributes[a].values.len() as u64
    }

    pub fn value_v0(&self, s: u64) -> usize {
        (h(self.seed, s, F_VAL) % self.n_values(self.slot_attr(s))) as usize
    }

    fn other_value(&self, s: u64, field: u64) -> usize {
        let len = self.n_values(self.slot_attr(s));
        ((self.value_v0(s) as u64 + 1 + h(self.seed, s, field) % (len - 1)) % len) as usize
    }

    pub fn value_v1(&self, s: u64) -> usize {
        self.other_value(s, F_VAL + 1)
    }

    pub fn value_contra(&self, s: u64) -> usize {
        self.other_value(s, F_VAL + 2)
    }

    /// The value a slot holds now (the first source's, for a contradicted slot).
    pub fn current_value(&self, s: u64) -> usize {
        if s < self.layout.updates {
            self.value_v1(s)
        } else {
            self.value_v0(s)
        }
    }

    pub fn dup_slot(&self, j: u64) -> u64 {
        h(self.seed, j, F_DUP) % self.layout.slots
    }

    /// Attributes the entity has a slot for.
    pub fn entity_attrs(&self, e: u64) -> Vec<usize> {
        let mut out = Vec::new();
        let mut s = e;
        while s < self.layout.slots {
            out.push(self.slot_attr(s));
            s += self.layout.entities;
        }
        out
    }

    fn fill(&self, i: u64, base: String) -> String {
        let k = h(self.seed, i, F_FILLN) % 4;
        let mut out = base;
        for j in 0..k {
            let f = &self.spec.filler[(h(self.seed, i, F_FILL0 + j) % self.spec.filler.len() as u64) as usize];
            out.push(' ');
            out.push_str(f);
        }
        out
    }

    fn statement(&self, i: u64, e: u64, a: usize, v: usize) -> String {
        let attr = &self.spec.attributes[a];
        let t = &attr.memory[(h(self.seed, i, F_TMPL) % attr.memory.len() as u64) as usize];
        self.fill(i, t.replace("{name}", &self.entity_name(e)).replace("{value}", &attr.values[v]))
    }

    pub fn record(&self, i: u64) -> Record {
        let l = &self.layout;
        let (kind, j) = l.kind(i);
        let day_h = h(self.seed, i, F_DAY);
        let (slot, entity, attr, value, day) = match kind {
            Kind::Fact => (Some(j), self.slot_entity(j), self.slot_attr(j), Some(self.value_v0(j)), day_h % 365),
            Kind::Update => (Some(j), self.slot_entity(j), self.slot_attr(j), Some(self.value_v1(j)), 365 + day_h % 180),
            Kind::Duplicate => {
                let s = self.dup_slot(j);
                (Some(s), self.slot_entity(s), self.slot_attr(s), Some(self.current_value(s)), 545 + day_h % 30)
            }
            Kind::Contradiction => {
                let s = l.updates + j;
                let d0 = h(self.seed, s, F_DAY) % 365;
                (Some(s), self.slot_entity(s), self.slot_attr(s), Some(self.value_contra(s)), d0 + 1 + day_h % 10)
            }
            Kind::Distractor => {
                let e = h(self.seed, j, F_DIS_E) % l.entities;
                let a = (h(self.seed, j, F_DIS_A) % self.spec.attributes.len() as u64) as usize;
                (None, e, a, None, day_h % 575)
            }
        };
        let text = match (kind, value) {
            (Kind::Distractor, _) | (_, None) => {
                let t = &self.spec.distractor[(h(self.seed, i, F_TMPL) % self.spec.distractor.len() as u64) as usize];
                self.fill(i, t.replace("{name}", &self.entity_name(entity)).replace("{phrase}", &self.spec.attributes[attr].phrase))
            }
            (_, Some(v)) => self.statement(i, entity, attr, v),
        };
        let superseded = kind == Kind::Fact && j < l.updates;
        Record {
            index: i,
            id: memory_id(self.bank, i),
            kind,
            slot,
            entity,
            attr,
            value,
            text,
            observed_day: day,
            importance: 0.3 + (h(self.seed, i, F_IMP) % 60) as f64 / 100.0,
            chat: h(self.seed, i, F_CHAT) % (l.n / 200).max(1),
            status: if superseded { "superseded" } else { "active" },
            superseded_by: superseded.then(|| memory_id(self.bank, l.update_index(j))),
            speaker: if kind == Kind::Contradiction { "second-source" } else { "user" },
        }
    }

    // ------------------------------------------------------------------ synthetic latent vectors
    fn component(&self, field: u64, key: u64, dim: usize, out: &mut [f32], w: f32) {
        let s = mix(self.seed ^ field.wrapping_mul(GOLDEN));
        for (j, o) in out.iter_mut().enumerate().take(dim) {
            let u = (h(s, key, j as u64) >> 11) as f64 * (1.0 / (1u64 << 53) as f64);
            *o += w * (2.0 * u as f32 - 1.0);
        }
    }

    fn entity_vec(&self, e: u64, dim: usize, out: &mut [f32]) {
        self.component(V_ENT_BASE, e >> 1, dim, out, 1.0);
        self.component(V_ENT_TWIN, e, dim, out, 0.6);
    }

    /// Synthetic latent embedding of a record (not a language-model embedding): facts mix entity, attribute,
    /// value and noise directions; distractors mix entity, a weak attribute direction and another attribute.
    pub fn synthetic_vector(&self, r: &Record, dim: usize) -> Vec<f32> {
        let mut v = vec![0f32; dim];
        self.entity_vec(r.entity, dim, &mut v);
        match r.value {
            Some(val) => {
                self.component(V_ATTR, r.attr as u64, dim, &mut v, 0.8);
                self.component(V_VAL, (r.attr as u64) * 1000 + val as u64, dim, &mut v, 0.7);
            }
            None => {
                self.component(V_ATTR, r.attr as u64, dim, &mut v, 0.5);
                self.component(V_OTHER_ATTR, r.index, dim, &mut v, 0.5);
            }
        }
        self.component(V_NOISE, r.index, dim, &mut v, 0.5);
        v
    }

    pub fn synthetic_query_vector(&self, q: &Query, dim: usize) -> Vec<f32> {
        let mut v = vec![0f32; dim];
        self.entity_vec(q.entity, dim, &mut v);
        self.component(V_ATTR, q.attr as u64, dim, &mut v, 0.8);
        self.component(V_QNOISE, q.qid, dim, &mut v, 0.5);
        v
    }

    // ------------------------------------------------------------------ queries + ground truth
    pub fn queries(&self, n_q: u64) -> Vec<Query> {
        let qs = query_seed(self.seed);
        let l = self.layout;
        let mut out = Vec::with_capacity(n_q as usize);
        for q in 0..n_q {
            let t = h(qs, q, F_Q_TYPE) % 100;
            let pick = h(qs, q, F_Q_SLOT);
            let (qtype, slot) = if t < 60 || (t < 70 && l.updates == 0) || (t < 80 && l.contras == 0) {
                ("current", Some(pick % l.slots))
            } else if t < 70 {
                ("historical", Some(pick % l.updates))
            } else if t < 80 {
                ("contradiction", Some(l.updates + pick % l.contras))
            } else {
                ("unanswerable", None)
            };
            let (entity, attr) = match slot {
                Some(s) => (self.slot_entity(s), self.slot_attr(s)),
                None => {
                    let e = h(qs, q, F_Q_ENT) % l.entities;
                    let have = self.entity_attrs(e);
                    let n_attr = self.spec.attributes.len();
                    let start = (h(qs, q, F_Q_ATTR) % n_attr as u64) as usize;
                    let a = (0..n_attr).map(|k| (start + k) % n_attr).find(|a| !have.contains(a)).expect("free attribute");
                    (e, a)
                }
            };
            let spec_attr = &self.spec.attributes[attr];
            let name = self.entity_name(entity);
            let text = if qtype == "historical" {
                self.spec.historical_query.replace("{name}", &name).replace("{phrase}", &spec_attr.phrase)
            } else {
                spec_attr.query[(h(qs, q, F_Q_TMPL) % spec_attr.query.len() as u64) as usize].replace("{name}", &name)
            };
            out.push(Query {
                qid: q,
                qtype,
                slot,
                entity,
                attr,
                text,
                include_superseded: qtype == "historical",
                relevant: Vec::new(),
                expected_values: Vec::new(),
            });
        }
        // duplicates restating each queried slot
        let wanted: std::collections::HashSet<u64> = out.iter().filter_map(|q| q.slot).collect();
        let mut dups: HashMap<u64, Vec<u64>> = HashMap::new();
        for j in 0..l.dups {
            let s = self.dup_slot(j);
            if wanted.contains(&s) {
                dups.entry(s).or_default().push(l.slots + l.updates + j);
            }
        }
        for q in out.iter_mut() {
            let Some(s) = q.slot else { continue };
            let vals = &self.spec.attributes[q.attr].values;
            let mut rel: Vec<u64> = Vec::new();
            match q.qtype {
                "historical" => {
                    rel.push(s);
                    q.expected_values.push(vals[self.value_v0(s)].clone());
                }
                _ => {
                    rel.push(if s < l.updates { l.update_index(s) } else { s });
                    q.expected_values.push(vals[self.current_value(s)].clone());
                    if l.is_contradicted(s) {
                        rel.push(l.contra_index(s));
                        q.expected_values.push(vals[self.value_contra(s)].clone());
                    }
                    rel.extend(dups.get(&s).into_iter().flatten().copied());
                }
            }
            rel.sort_unstable();
            rel.dedup();
            q.relevant = rel.into_iter().map(|i| memory_id(self.bank, i)).collect();
        }
        out
    }
}

#[derive(Clone, Debug)]
pub struct Query {
    pub qid: u64,
    pub qtype: &'static str,
    pub slot: Option<u64>,
    pub entity: u64,
    pub attr: usize,
    pub text: String,
    pub include_superseded: bool,
    pub relevant: Vec<String>,
    pub expected_values: Vec<String>,
}

/// ISO date `days` after the spec's time base, at noon UTC, in the store's format.
pub fn day_iso(base: &str, days: u64) -> String {
    let d = chrono::NaiveDate::parse_from_str(base, "%Y-%m-%d").expect("time base") + chrono::Days::new(days);
    format!("{}T12:00:00+00:00", d.format("%Y-%m-%d"))
}
