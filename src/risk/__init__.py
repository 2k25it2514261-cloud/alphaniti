"""
AlphaNiti v25.0: Risk Engine Module
"""

from src.risk.factor_model import OrthogonalFactorModel
from src.risk.wasserstein_dro import EsfahaniKuhnWassersteinDRO
from src.risk.tier_gate import HysteresisTierGate
from src.risk.decision_score import (
    compute_composite_decision_score,
    score_universe,
    DecisionScoreResult,
)

__all__ = [
    "OrthogonalFactorModel",
    "EsfahaniKuhnWassersteinDRO",
    "HysteresisTierGate",
    "compute_composite_decision_score",
    "score_universe",
    "DecisionScoreResult",
]
