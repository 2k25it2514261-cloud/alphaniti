"""
AlphaNiti v25.0: Pipeline Models Module
"""

from src.models.evidential_nn import (
    EvidentialHead,
    EvidentialGRUModel,
    EvidentialLoss,
    EvidentialEnsemble,
    SplitConformalCalibrator,
    GaussianHead,
    GaussianGRUModel,
    PlainDeepEnsemble,
)

from src.models.trainer import (
    EvidentialTrainer,
    train_evidential_ensemble,
    GaussianTrainer,
    train_plain_deep_ensemble,
)

__all__ = [
    "EvidentialHead",
    "EvidentialGRUModel",
    "EvidentialLoss",
    "EvidentialEnsemble",
    "SplitConformalCalibrator",
    "GaussianHead",
    "GaussianGRUModel",
    "PlainDeepEnsemble",
    "EvidentialTrainer",
    "train_evidential_ensemble",
    "GaussianTrainer",
    "train_plain_deep_ensemble",
]
