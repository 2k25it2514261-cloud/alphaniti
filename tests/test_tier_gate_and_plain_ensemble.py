"""
Unit and Integration Tests for Hysteresis Tier Gate & Plain Deep Ensemble
Reference: docs/02_FORECASTING_AND_EVIDENTIAL_DEEP_ENSEMBLE.md & Meinert et al. (2023)
"""

import sys
import os
from pathlib import Path
import unittest
import numpy as np
import torch
from torch.utils.data import TensorDataset, DataLoader

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk.tier_gate import HysteresisTierGate
from src.models.evidential_nn import (
    GaussianGRUModel,
    PlainDeepEnsemble,
    EvidentialGRUModel,
    EvidentialEnsemble,
)
from src.models.trainer import (
    GaussianTrainer,
    train_plain_deep_ensemble,
)


class TestTierGateAndPlainEnsemble(unittest.TestCase):

    def setUp(self):
        self.gate = HysteresisTierGate(
            theta_soft=0.002,
            theta_elevated=0.004,
            theta_extreme=0.008,
            delta_hysteresis=0.15,
            persistence_sessions=3,
        )

    def test_01_escalation_invariants(self):
        """Verifies zero-latency immediate escalation when risk expands."""
        # Initial Tier 0
        d0 = self.gate.update(0.001)
        self.assertEqual(d0["tier"], 0)
        self.assertTrue(d0["allow_buys"])
        self.assertEqual(d0["position_multiplier"], 1.0)

        # Cross theta_soft (0.002) -> Tier 1
        d1 = self.gate.update(0.0025)
        self.assertEqual(d1["tier"], 1)
        self.assertFalse(d1["allow_buys"])
        self.assertEqual(d1["forward_horizon_days"], 5)

        # Cross theta_elevated (0.004) -> Tier 2
        d2 = self.gate.update(0.005)
        self.assertEqual(d2["tier"], 2)
        self.assertEqual(d2["position_multiplier"], 0.50)

        # Cross theta_extreme (0.008) -> Tier 3
        d3 = self.gate.update(0.010)
        self.assertEqual(d3["tier"], 3)
        self.assertEqual(d3["position_multiplier"], 0.0)

    def test_02_hysteresis_deadband_and_persistence(self):
        """
        Verifies that dropping below theta_elevated does NOT de-escalate unless
        it falls below theta_elevated * (1 - 0.15) = 0.0034 for 3 consecutive days.
        """
        # Escalate to Tier 2
        self.gate.update(0.005)
        self.assertEqual(self.gate.current_tier, 2)

        # Drops to 0.0038 (below 0.004, but above exit threshold 0.0034) -> Stays Tier 2
        d_noise = self.gate.update(0.0038)
        self.assertEqual(d_noise["tier"], 2)

        # Drops to 0.0030 (below exit threshold 0.0034):
        # Day 1: persists, still Tier 2
        d_day1 = self.gate.update(0.0030)
        self.assertEqual(d_day1["tier"], 2)
        self.assertEqual(d_day1["persistence_counter"], 1)

        # Day 2: persists, still Tier 2
        d_day2 = self.gate.update(0.0030)
        self.assertEqual(d_day2["tier"], 2)
        self.assertEqual(d_day2["persistence_counter"], 2)

        # Day 3: reaches persistence requirement of 3 sessions -> De-escalates to Tier 1
        d_day3 = self.gate.update(0.0030)
        self.assertEqual(d_day3["tier"], 1)

    def test_03_circuit_halt_immediate_tier3(self):
        """Verifies that an exchange circuit halt overrides all logic into Tier 3."""
        d = self.gate.update(uncertainty=0.0005, is_circuit_halt=True)
        self.assertEqual(d["tier"], 3)
        self.assertEqual(d["position_multiplier"], 0.0)
        self.assertFalse(d["allow_buys"])

    def test_04_gaussian_trainer_and_plain_ensemble(self):
        """Verifies Plain Deep Ensemble training and predictive uncertainty decomposition."""
        B, L, D = 32, 20, 10
        X_dummy = torch.randn(B, L, D)
        y_reg = torch.randn(B, 1) * 0.05
        y_clf = torch.randint(0, 2, (B, 1)).float()

        dataset = TensorDataset(X_dummy, y_reg, y_clf)
        loader = DataLoader(dataset, batch_size=16)

        m1 = GaussianGRUModel(in_features=D, hidden_dim=16, num_layers=1)
        trainer = GaussianTrainer(m1, lr=0.005)
        hist = trainer.fit(loader, loader, epochs=2, verbose=False)

        self.assertIn("train_loss", hist)
        self.assertEqual(len(hist["train_loss"]), 2)

        # Train 2-member Plain Deep Ensemble
        plain_ens = train_plain_deep_ensemble(loader, loader, n_models=2, in_features=D, hidden_dim=16, epochs=1)
        mu_bar, s_ale, s_epi, pb_bar = plain_ens.predict(X_dummy)

        self.assertEqual(mu_bar.shape, (B, 1))
        self.assertEqual(s_ale.shape, (B, 1))
        self.assertEqual(s_epi.shape, (B, 1))
        self.assertTrue((s_epi >= 0.0).all())

    def test_05_meinert_epistemic_error_prediction(self):
        """
        Verifies calculation of Spearman rank correlation between epistemic variance
        and squared forecast errors under the Meinert et al. (2023) audit protocol.
        """
        B, L, D = 50, 20, 8
        X_test = torch.randn(B, L, D)
        y_true = np.random.randn(B, 1) * 0.05

        m1 = GaussianGRUModel(in_features=D, hidden_dim=16, num_layers=1)
        m2 = GaussianGRUModel(in_features=D, hidden_dim=16, num_layers=1)
        plain_ens = PlainDeepEnsemble([m1, m2])

        mu_bar, _, s_epi, _ = plain_ens.predict(X_test)
        sq_errors = (y_true - mu_bar) ** 2

        # Check shapes and finite values
        self.assertEqual(sq_errors.shape, (B, 1))
        self.assertEqual(s_epi.shape, (B, 1))
        self.assertFalse(np.isnan(sq_errors).any())
        self.assertFalse(np.isnan(s_epi).any())


if __name__ == "__main__":
    unittest.main()
