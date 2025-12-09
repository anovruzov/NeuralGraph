"""Wave Memory System - Resonance-based memory retrieval.

No classification. No routing. Only resonance.
Every memory is a wave. Every query is a wave.
Retrieval = finding memories that resonate with the query.
"""

from .data_types import (
    WaveAmplitudes,
    SignalType,
    WaveSignal,
    TemporalSignal,
    EntitySignal,
    RelationSignal,
    ActionSignal,
    StateSignal,
    SpatialSignal,
    CausalSignal,
    EmotionalSignal,
    QuantitativeSignal,
    MemoryWave,
    DIMENSION_INDEX,
    wave_combine,
)

from .wave_encoder import WaveEncoder
from .wave_resonance import (
    WaveResonanceRetriever,
    create_wave_retriever,
    retrieve_by_resonance,
)

from .temporal_calculator import (
    TemporalCalculator,
    TemporalContextEnhancer,
    TemporalResult,
    calculate_date,
    calculate_duration,
)

from .theta_gamma_coupling import (
    ThetaPhase,
    GammaBurst,
    OscillationState,
    ThetaGammaCoupledRetriever,
    enhance_retrieval_with_coupling,
    THETA_FREQUENCY_HZ,
    GAMMA_CYCLES_PER_THETA,
)

__all__ = [
    # Data types
    "WaveAmplitudes",
    "SignalType",
    "WaveSignal",
    "TemporalSignal",
    "EntitySignal",
    "RelationSignal",
    "ActionSignal",
    "StateSignal",
    "SpatialSignal",
    "CausalSignal",
    "EmotionalSignal",
    "QuantitativeSignal",
    "MemoryWave",
    "DIMENSION_INDEX",
    "wave_combine",
    # Encoder
    "WaveEncoder",
    # Retriever
    "WaveResonanceRetriever",
    "create_wave_retriever",
    "retrieve_by_resonance",
    # Temporal Calculator
    "TemporalCalculator",
    "TemporalContextEnhancer",
    "TemporalResult",
    "calculate_date",
    "calculate_duration",
    # Theta-Gamma Coupling (brain-inspired)
    "ThetaPhase",
    "GammaBurst",
    "OscillationState",
    "ThetaGammaCoupledRetriever",
    "enhance_retrieval_with_coupling",
    "THETA_FREQUENCY_HZ",
    "GAMMA_CYCLES_PER_THETA",
]
