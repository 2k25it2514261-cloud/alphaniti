"""
AlphaNiti v25.0 Institutional Risk Engine
Pipeline Module 3 - Wasserstein Distributionally Robust Optimization (DRO)
Reference: docs/03_RISK_MODEL_AND_WASSERSTEIN_DRO.md (Callouts 1, 2, & 8)
"""

from typing import Tuple, Dict, Any, Optional
import numpy as np
from scipy import optimize
from src.risk.factor_model import OrthogonalFactorModel


class FournierGuillinCertifier:
    """
    Computes finite-sample certified 1-Wasserstein ambiguity radius epsilon_f(S, delta)
    via the Fournier & Guillin (2015) measure concentration theorem.
    """

    @staticmethod
    def compute_radius(
        n_factors: int,
        sample_size: int,
        delta: float = 0.01,
        factor_vol: float = 0.025,
        epistemic_uncertainty: float = 0.0,
        kappa: float = 1.0,
    ) -> float:
        """
        Theorem 2 (Fournier & Guillin 2015):
            C1 = 2 * (1 + sqrt(k / 2))
            C2 = 2
            epsilon_f(S, delta) = C1 * sigma_f * (ln(C2 / delta) / S)^(1/k)

        Optionally scaled by evidential epistemic uncertainty: (1 + kappa * sigma_epistemic).
        """
        k = max(1, n_factors)
        S = max(1, sample_size)

        c1 = 2.0 * (1.0 + np.sqrt(k / 2.0))
        c2 = 2.0

        log_term = np.log(c2 / delta)
        ratio_term = (log_term / S) ** (1.0 / k)

        base_radius = c1 * factor_vol * ratio_term

        # Scale adaptively if epistemic uncertainty from Evidential Ensemble is provided
        if epistemic_uncertainty > 0.0:
            scale_factor = 1.0 + kappa * float(epistemic_uncertainty)
            base_radius *= scale_factor

        return float(base_radius)


class EsfahaniKuhnWassersteinDRO:
    """
    Exact Esfahani-Kuhn (2018) Convex Dual Second-Order Cone / Linear Program
    for worst-case Factor Conditional Value-at-Risk (CVaR) under 1-Wasserstein ambiguity.
    """

    def __init__(
        self,
        factor_model: OrthogonalFactorModel,
        confidence_alpha: float = 0.95,
        delta: float = 0.01,
        kappa_epistemic: float = 1.0,
    ):
        """
        Args:
            factor_model: Fitted OrthogonalFactorModel instance.
            confidence_alpha: Tail confidence for CVaR (e.g. 0.95).
            delta: Probability bound for ambiguity ball (e.g. 0.01 for 99% confidence).
            kappa_epistemic: Scaling factor for evidential epistemic uncertainty.
        """
        self.factor_model = factor_model
        self.alpha = confidence_alpha
        self.delta = delta
        self.kappa = kappa_epistemic

    def get_ambiguity_radius(
        self,
        sample_size: int,
        epistemic_uncertainty: float = 0.0,
    ) -> float:
        """
        Compute certified Wasserstein radius for the current factor model.
        """
        return FournierGuillinCertifier.compute_radius(
            n_factors=self.factor_model.n_factors,
            sample_size=sample_size,
            delta=self.delta,
            factor_vol=self.factor_model.factor_vol,
            epistemic_uncertainty=epistemic_uncertainty,
            kappa=self.kappa,
        )

    def evaluate_worst_case_factor_cvar(
        self,
        v_factor_weights: np.ndarray,
        factor_scenarios: np.ndarray,
        epsilon_f: float,
        use_lp_solver: bool = False,
    ) -> Dict[str, float]:
        """
        Evaluate worst-case factor CVaR under Wasserstein ambiguity:
            min_{gamma, lambda >= 0, s >= 0} gamma + lambda * epsilon_f + (1 / ((1 - alpha) * S)) * sum(s)
            subject to:
                s_s >= -v^T f_s - gamma
                ||v||_2 <= lambda * (1 - alpha)

        Args:
            v_factor_weights: Factor exposure vector v = B^T * w, shape (k,).
            factor_scenarios: Historical factor realizations (S, k).
            epsilon_f: Certified Wasserstein ambiguity radius.
            use_lp_solver: If True, executes full SciPy linprog dual LP for audit.
        """
        S, k = factor_scenarios.shape
        v_norm2 = float(np.linalg.norm(v_factor_weights, 2))
        one_minus_alpha = 1.0 - self.alpha

        # Optimal lambda is exactly ||v||_2 / (1 - alpha)
        lambda_opt = v_norm2 / one_minus_alpha
        robust_penalty = lambda_opt * epsilon_f

        # Factor losses: L_s = -v^T f_s
        losses = -factor_scenarios @ v_factor_weights

        # Rockafellar-Uryasev 1D convex minimization over gamma
        # gamma* is the alpha-quantile (VaR)
        sorted_losses = np.sort(losses)
        var_idx = int(np.ceil(self.alpha * S)) - 1
        var_idx = min(max(0, var_idx), S - 1)
        gamma_empirical = sorted_losses[var_idx]

        # Empirical CVaR
        tail_losses = sorted_losses[var_idx:]
        empirical_cvar = float(np.mean(tail_losses)) if len(tail_losses) > 0 else float(gamma_empirical)

        worst_case_factor_cvar = empirical_cvar + robust_penalty

        if use_lp_solver:
            # Full SciPy linprog verification
            # Variables x = [gamma, lambda, s_1, ..., s_S] in R^(2 + S)
            # Minimize: gamma + epsilon_f * lambda + (1 / ((1 - alpha)*S)) * sum(s_s)
            c = np.zeros(2 + S)
            c[0] = 1.0
            c[1] = epsilon_f
            c[2:] = 1.0 / (one_minus_alpha * S)

            # Constraint 1: s_s >= -v^T f_s - gamma  ==>  -gamma - s_s <= v^T f_s
            A_ub = np.zeros((S + 1, 2 + S))
            b_ub = np.zeros(S + 1)

            for s in range(S):
                A_ub[s, 0] = -1.0
                A_ub[s, 2 + s] = -1.0
                b_ub[s] = float(losses[s])

            # Constraint 2: ||v||_2 <= lambda * (1 - alpha)  ==> -(1 - alpha) * lambda <= -||v||_2
            A_ub[S, 1] = -one_minus_alpha
            b_ub[S] = -v_norm2

            bounds = [(None, None)] + [(0, None)] * (1 + S)
            res = optimize.linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
            if res.success:
                lp_cvar = float(res.fun)
                assert np.isclose(lp_cvar, worst_case_factor_cvar, rtol=1e-3, atol=1e-4), (
                    f"LP solution {lp_cvar} diverges from analytical dual {worst_case_factor_cvar}"
                )

        return {
            "empirical_factor_cvar": empirical_cvar,
            "wasserstein_penalty": robust_penalty,
            "worst_case_factor_cvar": worst_case_factor_cvar,
            "lambda_dual": lambda_opt,
            "v_norm2": v_norm2,
            "epsilon_f": epsilon_f,
        }

    def compute_total_portfolio_cvar_bound(
        self,
        weights: np.ndarray,
        factor_scenarios: np.ndarray,
        epistemic_uncertainty: float = 0.0,
    ) -> Dict[str, float]:
        """
        Computes the complete out-of-sample portfolio tail guarantee (Callout 8):
            CVaR_alpha^true(w) <= sup_{Q in B_epsilon} CVaR_alpha^Q(v^T f) + Delta_epsilon(w, alpha)
        """
        S = len(factor_scenarios)
        v = self.factor_model.transform_factor_exposure(weights)
        eps_f = self.get_ambiguity_radius(
            sample_size=S,
            epistemic_uncertainty=epistemic_uncertainty,
        )

        factor_res = self.evaluate_worst_case_factor_cvar(
            v_factor_weights=v,
            factor_scenarios=factor_scenarios,
            epsilon_f=eps_f,
            use_lp_solver=False,
        )

        delta_res = self.factor_model.compute_residual_tail_bound(weights, alpha=self.alpha)
        total_bound = factor_res["worst_case_factor_cvar"] + delta_res

        return {
            "total_certified_cvar": total_bound,
            "worst_case_factor_cvar": factor_res["worst_case_factor_cvar"],
            "empirical_factor_cvar": factor_res["empirical_factor_cvar"],
            "wasserstein_penalty": factor_res["wasserstein_penalty"],
            "residual_tail_bound": delta_res,
            "epsilon_radius": eps_f,
            "is_subgaussian": self.factor_model.test_residual_subgaussianity(weights)[0],
        }
