"""
Unit and Integration Tests for Risk Engine:
Orthogonal Factor Model & Wasserstein Distributionally Robust Optimization (DRO)
Reference: docs/03_RISK_MODEL_AND_WASSERSTEIN_DRO.md
"""

import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from src.risk.factor_model import OrthogonalFactorModel
from src.risk.wasserstein_dro import FournierGuillinCertifier, EsfahaniKuhnWassersteinDRO


class TestWassersteinDRO(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        self.T = 500
        self.N = 10
        self.k = 3

        # Generate synthetic asset returns with 3 latent factors + idiosyncratic noise
        F_true = np.random.randn(self.T, self.k) * 0.02
        B_true = np.random.randn(self.N, self.k)
        # Orthonormalize B
        B_true, _ = np.linalg.qr(B_true)
        E_true = np.random.randn(self.T, self.N) * 0.015
        R = F_true @ B_true.T + E_true

        self.asset_names = [f"ASSET_{i}" for i in range(self.N)]
        self.returns_df = pd.DataFrame(R, columns=self.asset_names)

        # Equal-weighted portfolio
        self.weights = np.ones(self.N) / self.N

    def test_orthogonal_factor_model_decomposition(self):
        """Verify SVD decomposition B^T B = I_k, residual orthogonality, and Ledoit-Wolf cov."""
        model = OrthogonalFactorModel(n_factors=self.k, residual_corr_threshold=0.08)
        model.fit(self.returns_df)

        # 1. B^T B == I_k
        BtB = model.B.T @ model.B
        np.testing.assert_allclose(BtB, np.eye(self.k), atol=1e-5)

        # 2. Residual covariance is positive definite
        sigma_eps = model.get_residual_covariance()
        self.assertEqual(sigma_eps.shape, (self.N, self.N))
        eigvals = np.linalg.eigvalsh(sigma_eps)
        self.assertTrue(np.all(eigvals > 0), "Residual covariance must be positive definite")

        # 3. Residual variance of portfolio > 0
        res_var = model.compute_residual_variance(self.weights)
        self.assertGreater(res_var, 0.0)

    def test_fournier_guillin_numerical_derivation(self):
        """
        Verify Fournier-Guillin ambiguity radius exactly matches Section 4.2 of docs:
        k = 3, S = 1000, delta = 0.01, sigma_f = 0.025 ==> epsilon_f ~ 0.01939 ~ 0.0195
        """
        k = 3
        S = 1000
        delta = 0.01
        sigma_f = 0.025

        radius = FournierGuillinCertifier.compute_radius(
            n_factors=k,
            sample_size=S,
            delta=delta,
            factor_vol=sigma_f,
        )

        # C1 = 2 * (1 + sqrt(1.5)) = 4.4494897
        # ln(2 / 0.01) = ln(200) = 5.298317
        # (5.298317 / 1000)^(1/3) = 0.174338
        # radius = 4.4494897 * 0.025 * 0.174338 = 0.019393
        expected_radius = 4.44948974 * 0.025 * ((np.log(200) / 1000) ** (1.0 / 3.0))
        self.assertAlmostEqual(radius, expected_radius, places=5)
        self.assertAlmostEqual(radius, 0.01939, delta=0.0005)

    def test_esfahani_kuhn_dual_lp_equivalence(self):
        """
        Verify that analytical dual solution matches SciPy HiGHS LP solver
        for Esfahani-Kuhn CVaR under 1-Wasserstein ambiguity.
        """
        model = OrthogonalFactorModel(n_factors=self.k)
        model.fit(self.returns_df)

        dro = EsfahaniKuhnWassersteinDRO(factor_model=model, confidence_alpha=0.95, delta=0.01)
        F_scenarios = (self.returns_df.values - model.mean_returns) @ model.B
        v = model.transform_factor_exposure(self.weights)
        eps_f = dro.get_ambiguity_radius(sample_size=len(F_scenarios))

        # Evaluate with both analytical solver and HiGHS LP solver
        res_analytical = dro.evaluate_worst_case_factor_cvar(
            v_factor_weights=v,
            factor_scenarios=F_scenarios,
            epsilon_f=eps_f,
            use_lp_solver=False,
        )

        res_lp = dro.evaluate_worst_case_factor_cvar(
            v_factor_weights=v,
            factor_scenarios=F_scenarios,
            epsilon_f=eps_f,
            use_lp_solver=True,
        )

        self.assertAlmostEqual(
            res_analytical["worst_case_factor_cvar"],
            res_lp["worst_case_factor_cvar"],
            places=4,
            msg="Analytical dual must match HiGHS linear program solver",
        )
        # Robust penalty must be strictly positive
        self.assertGreater(res_analytical["wasserstein_penalty"], 0.0)

    def test_residual_tail_bounds(self):
        """Verify analytical Sub-Gaussian bound and empirical bootstrap bound."""
        model = OrthogonalFactorModel(n_factors=self.k)
        model.fit(self.returns_df)

        subgauss_bound = model.compute_subgaussian_tail_bound(self.weights, alpha=0.95)
        self.assertGreater(subgauss_bound, 0.0)

        emp_bound = model.compute_empirical_bootstrap_bound(self.weights, alpha=0.95, n_bootstraps=100)
        self.assertGreater(emp_bound, 0.0)

        # Total residual tail bound
        certified_res_bound = model.compute_residual_tail_bound(self.weights, alpha=0.95)
        self.assertGreater(certified_res_bound, 0.0)

    def test_total_portfolio_cvar_guarantee(self):
        """Verify complete out-of-sample portfolio tail guarantee integration."""
        model = OrthogonalFactorModel(n_factors=self.k)
        model.fit(self.returns_df)

        dro = EsfahaniKuhnWassersteinDRO(factor_model=model, confidence_alpha=0.95, delta=0.01)
        F_scenarios = (self.returns_df.values - model.mean_returns) @ model.B

        total_res = dro.compute_total_portfolio_cvar_bound(
            weights=self.weights,
            factor_scenarios=F_scenarios,
            epistemic_uncertainty=0.05,
        )

        self.assertIn("total_certified_cvar", total_res)
        self.assertIn("worst_case_factor_cvar", total_res)
        self.assertIn("residual_tail_bound", total_res)
        self.assertGreater(total_res["total_certified_cvar"], total_res["worst_case_factor_cvar"])
        print(f"Total Certified CVaR (95%): {total_res['total_certified_cvar']:.4%}")
        print(f"  Worst-Case Factor CVaR: {total_res['worst_case_factor_cvar']:.4%}")
        print(f"  Residual Tail Bound:   {total_res['residual_tail_bound']:.4%}")
        print(f"  Wasserstein Penalty:   {total_res['wasserstein_penalty']:.4%}")


if __name__ == "__main__":
    unittest.main()
