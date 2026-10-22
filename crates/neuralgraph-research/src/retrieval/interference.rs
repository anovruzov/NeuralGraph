//! Wave-interference memory scoring (port of `retrieval/interference.py`).
//!
//! A memory's activation is the weighted product of its wave amplitudes with the query's, modulated by the phase
//! difference: in phase (< 45°) constructive, out of phase (> 135°) destructive, partial in between. Phases come
//! from timestamps on a ~6 Hz theta rhythm. Python's float `%` (sign of the divisor) is reproduced by
//! [`py_mod`]; subtracting a naive from an aware datetime is the `TypeError` Python raised
//! ([`NaiveAwareMismatch`]).

use crate::pytime::{total_seconds, NaiveAwareMismatch, PyDateTime};
use crate::retrieval::data_types::NeuralNode;
use chrono::{NaiveDate, NaiveDateTime};
use indexmap::IndexMap;
use std::f64::consts::PI;

/// Python's float `a % b`: the result takes the sign of `b` (CPython `float_rem`).
pub fn py_mod(a: f64, b: f64) -> f64 {
    let m = a % b;
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m + b
        } else {
            m
        }
    } else {
        0.0f64.copysign(b)
    }
}

/// `max(a, b)`: the first argument unless the second is strictly greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `min(a, b)`: the first argument unless the second is strictly smaller.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Type of wave interference.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum InterferenceType {
    Constructive,
    Partial,
    Destructive,
}

impl InterferenceType {
    pub fn value(self) -> &'static str {
        match self {
            InterferenceType::Constructive => "constructive",
            InterferenceType::Partial => "partial",
            InterferenceType::Destructive => "destructive",
        }
    }
}

/// Result of an interference computation.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct InterferenceResult {
    pub activation: f64,
    pub interference_type: InterferenceType,
    pub phase_diff: f64,
    pub amplitude_product: f64,
}

/// Theta rhythm (~6 Hz) used to turn time into phase.
pub const THETA_FREQUENCY_HZ: f64 = 6.0;
pub const THETA_PERIOD_MS: f64 = 1000.0 / THETA_FREQUENCY_HZ;

/// Wave-interference scoring (`InterferenceScorer`, all class/static methods in Python).
pub struct InterferenceScorer;

impl InterferenceScorer {
    /// Phase differences below this (45°) are constructive.
    pub const CONSTRUCTIVE_THRESHOLD: f64 = PI / 4.0;
    /// Phase differences above this (135°) are destructive.
    pub const DESTRUCTIVE_THRESHOLD: f64 = 3.0 * PI / 4.0;

    /// `DIMENSION_WEIGHTS`, in declaration order: trunk dimensions weigh more than support ones.
    pub const DIMENSION_WEIGHTS: [(&'static str, f64); 9] = [
        ("entity", 1.0),
        ("temporal", 0.9),
        ("action", 0.8),
        ("relational", 0.6),
        ("state", 0.5),
        ("spatial", 0.4),
        ("causal", 0.3),
        ("emotional", 0.2),
        ("quantitative", 0.2),
    ];

    /// The weight of a dimension, 0.3 for dimensions not in the table.
    pub fn dimension_weight(dim: &str) -> f64 {
        Self::DIMENSION_WEIGHTS.iter().find(|(d, _)| *d == dim).map(|(_, w)| *w).unwrap_or(0.3)
    }

    /// Interference between a query wave and a memory wave (Python defaults: both phases 0.0).
    pub fn compute_interference(
        query_amps: &IndexMap<String, f64>,
        memory_amps: &IndexMap<String, f64>,
        query_phase: f64,
        memory_phase: f64,
    ) -> InterferenceResult {
        let phase_diff = py_mod((query_phase - memory_phase).abs(), 2.0 * PI);
        let mut amp_product = 0.0;
        let mut total_weight = 0.0;
        for (dim, q_amp) in query_amps {
            let m_amp = memory_amps.get(dim).copied().unwrap_or(0.0);
            let weight = Self::dimension_weight(dim);
            amp_product += q_amp * m_amp * weight;
            total_weight += weight * py_max(*q_amp, 0.01);
        }
        if total_weight > 0.0 {
            amp_product /= total_weight;
        }
        let (activation, interference_type) = if phase_diff < Self::CONSTRUCTIVE_THRESHOLD {
            (amp_product * (1.0 + phase_diff.cos()), InterferenceType::Constructive)
        } else if phase_diff > Self::DESTRUCTIVE_THRESHOLD {
            (amp_product * (1.0 - phase_diff.cos()) * 0.3, InterferenceType::Destructive)
        } else {
            (amp_product * 0.5 * (1.0 + phase_diff.cos()), InterferenceType::Partial)
        };
        InterferenceResult { activation, interference_type, phase_diff, amplitude_product: amp_product }
    }

    /// The fixed reference `datetime(2020, 1, 1, tzinfo=timezone.utc)`.
    pub fn default_reference() -> PyDateTime {
        let naive: NaiveDateTime =
            NaiveDate::from_ymd_opt(2020, 1, 1).expect("date").and_hms_opt(0, 0, 0).expect("time");
        PyDateTime { naive, offset_seconds: Some(0) }
    }

    /// Phase of a timestamp on the theta rhythm, in [0, 2π), relative to `reference` (default 2020-01-01 UTC).
    pub fn compute_temporal_phase(
        timestamp: &PyDateTime,
        reference: Option<&PyDateTime>,
    ) -> Result<f64, NaiveAwareMismatch> {
        let reference = reference.copied().unwrap_or_else(Self::default_reference);
        let delta_ms = total_seconds(timestamp.sub(&reference)?) * 1000.0;
        let phase = (delta_ms / THETA_PERIOD_MS) * 2.0 * PI;
        Ok(py_mod(phase, 2.0 * PI))
    }

    /// `(activation, type)` of a memory node against the query wave; the node's phase comes from `created_at`.
    pub fn score_memory_match(
        query_amps: &IndexMap<String, f64>,
        memory_node: &NeuralNode,
        reference_time: Option<&PyDateTime>,
    ) -> Result<(f64, InterferenceType), NaiveAwareMismatch> {
        let memory_phase = Self::compute_temporal_phase(&memory_node.created_at, reference_time)?;
        let result = Self::compute_interference(query_amps, &memory_node.wave_amplitudes, 0.0, memory_phase);
        Ok((result.activation, result.interference_type))
    }
}

/// Compression of the nine wave dimensions into the three trunks (entity, temporal, action).
pub struct ResonanceAmplifier;

impl ResonanceAmplifier {
    /// `TRUNK_COMPRESSION`, in declaration order.
    pub const TRUNK_COMPRESSION: [(&'static str, &'static [(&'static str, f64)]); 3] = [
        ("entity_trunk", &[("entity", 0.7), ("relational", 0.3)]),
        ("temporal_trunk", &[("temporal", 0.8), ("causal", 0.2)]),
        ("action_trunk", &[("action", 0.5), ("state", 0.25), ("spatial", 0.15), ("quantitative", 0.1)]),
    ];

    /// 9D amplitudes -> 3D trunk, each trunk capped at 1.0.
    pub fn compress_to_trunk(amplitudes: &IndexMap<String, f64>) -> IndexMap<String, f64> {
        let mut trunk = IndexMap::new();
        for (trunk_name, compression) in Self::TRUNK_COMPRESSION {
            let mut trunk_value = 0.0;
            for (dim, weight) in compression {
                trunk_value += amplitudes.get(*dim).copied().unwrap_or(0.0) * weight;
            }
            trunk.insert(trunk_name.to_string(), py_min(1.0, trunk_value));
        }
        trunk
    }

    /// Query-weighted ratio similarity of two trunks, in [0, 1].
    pub fn compute_trunk_alignment(query_trunk: &IndexMap<String, f64>, memory_trunk: &IndexMap<String, f64>) -> f64 {
        let mut alignment = 0.0;
        let mut total_weight = 0.0;
        for trunk_dim in ["entity_trunk", "temporal_trunk", "action_trunk"] {
            let q_val = query_trunk.get(trunk_dim).copied().unwrap_or(0.0);
            let m_val = memory_trunk.get(trunk_dim).copied().unwrap_or(0.0);
            if q_val > 0.0 && m_val > 0.0 {
                let similarity = py_min(q_val, m_val) / py_max(q_val, m_val);
                alignment += similarity * q_val;
                total_weight += q_val;
            }
        }
        if total_weight > 0.0 {
            alignment / total_weight
        } else {
            0.0
        }
    }
}
