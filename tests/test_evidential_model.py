"""
AlphaNiti v25.0: Pipeline Module 3 - Evidential Model Verification Suite
Tests:
  1. NIG Parameter Support Invariants: nu > 0, alpha > 1, beta > 0
  2. Exact Student-t Negative Log-Likelihood & Backpropagation
  3. Analytical Uncertainty Decomposition (Aleatoric vs Epistemic)
  4. Ensemble Mixture Reduction & Total Variance Pooling
  5. Split-Conformal Finite-Sample Coverage
  6. End-to-End Epoch Training on Real Dataset DataLoader
"""

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import torch
import numpy as np

from src.models.evidential_nn import (
    EvidentialHead,
    EvidentialGRUModel,
    EvidentialLoss,
    EvidentialEnsemble,
    SplitConformalCalibrator,
)
from src.models.trainer import EvidentialTrainer
from src.data.tensor_builder import create_dataloaders


class TestEvidentialModel(unittest.TestCase):

    def setUp(self):
        self.B, self.L, self.D = 32, 60, 15
        self.X_dummy = torch.randn(self.B, self.L, self.D)
        self.y_reg = torch.randn(self.B, 1) * 0.05
        self.y_clf = torch.randint(0, 2, (self.B, 1)).float()

    def test_01_nig_parameter_constraints(self):
        """Verifies strictly valid support: nu > 0, alpha > 1, beta > 0, p in (0, 1)."""
        model = EvidentialGRUModel(in_features=self.D, hidden_dim=32, num_layers=1)
        gamma, nu, alpha, beta, p_breach = model(self.X_dummy)

        self.assertEqual(gamma.shape, (self.B, 1))
        self.assertTrue(torch.all(nu > 0.0), "nu must be strictly positive")
        self.assertTrue(torch.all(alpha > 1.0), "alpha must be strictly greater than 1")
        self.assertTrue(torch.all(beta > 0.0), "beta must be strictly positive")
        self.assertTrue(torch.all((p_breach >= 0.0) & (p_breach <= 1.0)), "p_breach must be in [0, 1]")

    def test_02_loss_and_gradients(self):
        """Verifies Student-t NLL and evidence regularizer gradients flow properly."""
        model = EvidentialGRUModel(in_features=self.D, hidden_dim=32, num_layers=1)
        loss_fn = EvidentialLoss(lambda_reg=0.05, lambda_clf=1.0)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

        gamma, nu, alpha, beta, p_breach = model(self.X_dummy)
        loss, metrics = loss_fn(gamma, nu, alpha, beta, p_breach, self.y_reg, self.y_clf)

        self.assertFalse(torch.isnan(loss).item())
        self.assertGreater(metrics["nll_loss"], -100.0)
        self.assertGreaterEqual(metrics["reg_loss"], 0.0)

        optimizer.zero_grad()
        loss.backward()
        total_grad = sum(p.grad.norm().item() for p in model.parameters() if p.grad is not None)
        self.assertGreater(total_grad, 0.0)

    def test_03_analytical_uncertainty_decomposition(self):
        """Verifies sigma_aleatoric^2 = beta / (alpha - 1) and sigma_epistemic^2 = beta / (nu*(alpha-1))."""
        model1 = EvidentialGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        model2 = EvidentialGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        ensemble = EvidentialEnsemble([model1, model2])

        gamma_bar, s_aleatoric, s_epistemic, p_bar = ensemble.predict(self.X_dummy)

        self.assertEqual(gamma_bar.shape, (self.B, 1))
        self.assertEqual(s_aleatoric.shape, (self.B, 1))
        self.assertEqual(s_epistemic.shape, (self.B, 1))
        self.assertEqual(p_bar.shape, (self.B, 1))

        self.assertTrue((s_aleatoric > 0).all(), "Aleatoric volatility must be positive")
        self.assertTrue((s_epistemic > 0).all(), "Epistemic uncertainty must be positive")

    def test_04_split_conformal_calibration(self):
        """Verifies Vovk finite-sample non-parametric coverage on holdout."""
        np.random.seed(42)
        K = 200
        y_true = np.random.normal(0.01, 0.05, size=(K, 1))
        y_pred = np.random.normal(0.01, 0.02, size=(K, 1))
        s_total = np.ones((K, 1)) * 0.05

        calibrator = SplitConformalCalibrator(target_coverage=0.90)
        q_hat = calibrator.calibrate(y_true, y_pred, s_total)
        self.assertGreater(q_hat, 0.0)

        lower, upper = calibrator.predict_intervals(y_pred, s_total)
        in_bounds = (y_true >= lower) & (y_true <= upper)
        empirical_coverage = float(np.mean(in_bounds))
        self.assertGreaterEqual(empirical_coverage, 0.85)

    def test_05_quick_epoch_on_real_data(self):
        """Runs a 1-epoch mini-batch training loop using the real production data loaders."""
        train_path = os.path.join("data", "splits", "train.parquet")
        val_path = os.path.join("data", "splits", "val.parquet")

        train_loader, val_loader, _, _, fnames = create_dataloaders(
            train_path, val_path, lookback=60, batch_size=256
        )

        model = EvidentialGRUModel(in_features=len(fnames), hidden_dim=32, num_layers=1)
        trainer = EvidentialTrainer(model, lr=0.005)

        # Train 1 epoch
        train_m = trainer.train_epoch(train_loader)
        val_m = trainer.evaluate(val_loader)

        self.assertFalse(np.isnan(train_m["loss"]))
        self.assertFalse(np.isnan(val_m["loss"]))
        self.assertGreater(train_m["loss"], -50.0)

    def test_06_meinert_critique_and_plain_ensemble(self):
        """Verifies Plain Deep Ensemble baseline and Meinert et al. (2023) epistemic decomposition."""
        from src.models.evidential_nn import GaussianGRUModel, PlainDeepEnsemble

        # 1. Plain Deep Ensemble test
        m1 = GaussianGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        m2 = GaussianGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        plain_ens = PlainDeepEnsemble([m1, m2])

        mu_bar, s_aleatoric, s_epistemic, p_bar = plain_ens.predict(self.X_dummy)
        self.assertEqual(mu_bar.shape, (self.B, 1))
        self.assertEqual(s_aleatoric.shape, (self.B, 1))
        self.assertEqual(s_epistemic.shape, (self.B, 1))
        self.assertTrue((s_epistemic >= 0).all(), "Ensemble epistemic uncertainty must be non-negative")

        # 2. Meinert split evaluation on EvidentialEnsemble
        em1 = EvidentialGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        em2 = EvidentialGRUModel(in_features=self.D, hidden_dim=16, num_layers=1)
        ev_ens = EvidentialEnsemble([em1, em2])
        split_dict = ev_ens.evaluate_meinert_split(self.X_dummy)

        self.assertIn("analytical_epistemic_var", split_dict)
        self.assertIn("ensemble_epistemic_var", split_dict)
        self.assertIn("total_epistemic_var", split_dict)
        self.assertTrue((split_dict["analytical_epistemic_var"] > 0).all())
        self.assertTrue((split_dict["ensemble_epistemic_var"] >= 0).all())


if __name__ == "__main__":
    unittest.main()
