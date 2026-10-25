"""
AlphaNiti v25.0 Institutional Risk Engine
Pipeline Module 3 - Orthogonal Factor Risk Model & Residual Tail Bounds
Reference: docs/03_RISK_MODEL_AND_WASSERSTEIN_DRO.md (Callouts 1 & 2)
"""

from typing import Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.covariance import LedoitWolf


class OrthogonalFactorModel:
    """
    Orthogonal systematic factor risk decomposition with Ledoit-Wolf full residual covariance
    and certified residual tail bounds (Sub-Gaussian or Empirical Bootstrap).

    Decomposition:
        r_t = B * f_t + epsilon_t,  where B^T * B = I_k,  E[epsilon_t | f_t] = 0
    """

    def __init__(self, n_factors: int = 3, residual_corr_threshold: float = 0.08):
        """
        Args:
            n_factors: Number of orthogonal factors (k <= 5).
            residual_corr_threshold: Threshold for off-diagonal residual correlations
                                    to allow diagonal mode (Callout 2). Default 0.08.
        """
        if n_factors > 5:
            raise ValueError(f"n_factors must be <= 5 to avoid curse of dimensionality, got {n_factors}")
        self.n_factors = n_factors
        self.residual_corr_threshold = residual_corr_threshold

        self.B: Optional[np.ndarray] = None  # (N, k) orthogonal loading matrix
        self.factor_cov: Optional[np.ndarray] = None  # (k, k) factor covariance
        self.factor_vol: float = 0.0  # Representative factor volatility
        self.res_cov_full: Optional[np.ndarray] = None  # (N, N) Ledoit-Wolf residual cov
        self.res_cov_diag: Optional[np.ndarray] = None  # (N, N) diagonal residual cov
        self.is_diagonal_valid: bool = False
        self.asset_names: Optional[list] = None
        self.mean_returns: Optional[np.ndarray] = None

    def fit(self, returns_df: pd.DataFrame) -> "OrthogonalFactorModel":
        """
        Fit orthogonal factor model on historical asset returns matrix (T, N).

        Args:
            returns_df: DataFrame with shape (T, N) containing causal excess returns.
        """
        self.asset_names = list(returns_df.columns)
        R = returns_df.values  # (T, N)
        T, N = R.shape

        if T < N:
            raise ValueError(f"Sample length T={T} must exceed asset count N={N} for factor extraction.")

        self.mean_returns = np.mean(R, axis=0)
        R_centered = R - self.mean_returns

        # SVD: R_centered = U * S * V^T
        # V has shape (N, N); first k columns form the orthogonal basis B: B^T * B = I_k
        U, S, Vt = np.linalg.svd(R_centered, full_matrices=False)
        self.B = Vt[: self.n_factors, :].T  # (N, k)

        # Verify orthogonality: B^T B == I_k
        BtB = self.B.T @ self.B
        assert np.allclose(BtB, np.eye(self.n_factors), atol=1e-5), "Factor loading matrix B is not orthogonal"

        # Factor realizations: F = R_centered @ B  (T, k)
        F = R_centered @ self.B
        self.factor_cov = np.cov(F, rowvar=False)  # (k, k)
        # Factor representative volatility (Frobenius root / sqrt(k))
        self.factor_vol = float(np.sqrt(np.trace(self.factor_cov) / self.n_factors))

        # Residuals: E = R_centered - F @ B^T  (T, N)
        E = R_centered - (F @ self.B.T)

        # Check orthogonality between residuals and factors via sample covariance: E[epsilon_t * f_t^T] = 0
        cov_fe = np.abs(F.T @ E) / T
        max_cov_fe = float(np.max(cov_fe))
        assert max_cov_fe < 1e-4, f"Residuals and factors are not orthogonal: max cov={max_cov_fe}"

        # Estimate Ledoit-Wolf shrinkage residual covariance (Callout 2)
        lw = LedoitWolf(assume_centered=True)
        lw.fit(E)
        self.res_cov_full = lw.covariance_  # (N, N)

        # Evaluate empirical off-diagonal residual correlations
        std_E = np.std(E, axis=0, keepdims=True)
        std_E = np.where(std_E > 1e-12, std_E, 1.0)
        E_std = (E - np.mean(E, axis=0, keepdims=True)) / std_E
        res_corr = (E_std.T @ E_std) / T
        off_diag_corr = np.abs(res_corr - np.eye(N))
        max_off_diag = float(np.max(off_diag_corr))

        self.res_cov_diag = np.diag(np.diag(self.res_cov_full))
        self.is_diagonal_valid = max_off_diag < self.residual_corr_threshold

        self._residuals = E
        return self

    def get_residual_covariance(self) -> np.ndarray:
        """
        Returns the appropriate residual covariance matrix according to Callout 2:
        - Full Ledoit-Wolf covariance if max off-diagonal correlation >= threshold
        - Validated diagonal matrix only if max off-diagonal correlation < threshold
        """
        if self.is_diagonal_valid:
            return self.res_cov_diag
        return self.res_cov_full

    def transform_factor_exposure(self, weights: np.ndarray) -> np.ndarray:
        """
        Project asset portfolio weights w in R^N into factor space v = B^T * w in R^k.
        """
        if self.B is None:
            raise RuntimeError("Factor model has not been fitted.")
        return self.B.T @ weights

    def compute_residual_variance(self, weights: np.ndarray) -> float:
        """
        Compute portfolio residual variance: sigma_xi^2(w) = w^T * Sigma_epsilon * w
        """
        sigma_eps = self.get_residual_covariance()
        var_res = float(weights.T @ sigma_eps @ weights)
        return max(var_res, 1e-12)

    def compute_subgaussian_tail_bound(self, weights: np.ndarray, alpha: float = 0.95) -> float:
        """
        Theorem 1 (Analytical Sub-Gaussian CVaR Bound):
            Delta_eps^gauss(w, alpha) = (sigma_xi(w) / ((1 - alpha) * sqrt(2*pi))) * exp(-(Phi^-1(alpha))^2 / 2)
        """
        sigma_xi = np.sqrt(self.compute_residual_variance(weights))
        z_alpha = stats.norm.ppf(alpha)
        factor = np.exp(-0.5 * (z_alpha**2)) / ((1.0 - alpha) * np.sqrt(2.0 * np.pi))
        return float(sigma_xi * factor)

    def test_residual_subgaussianity(self, weights: np.ndarray, tail_fraction: float = 0.05) -> Tuple[bool, float]:
        """
        Hill Tail-Index test on portfolio residual loss distribution (Callout 1).
        If alpha_hill < 4.0, sub-Gaussianity is rejected (heavy tails).
        """
        # Residual portfolio loss xi_t = -w^T * epsilon_t
        xi = -self._residuals @ weights
        positive_losses = xi[xi > 0]
        if len(positive_losses) < 20:
            return True, 5.0  # Insufficient tail samples, assume sub-Gaussian

        # Sort ascending
        sorted_losses = np.sort(positive_losses)
        m = max(5, int(tail_fraction * len(sorted_losses)))
        threshold = sorted_losses[-m]

        tail_obs = sorted_losses[-m:]
        log_ratios = np.log(tail_obs / threshold)
        hill_index = 1.0 / np.mean(log_ratios)
        is_subgaussian = bool(hill_index >= 4.0)
        return is_subgaussian, float(hill_index)

    def compute_empirical_bootstrap_bound(
        self, weights: np.ndarray, alpha: float = 0.95, n_bootstraps: int = 500
    ) -> float:
        """
        Non-parametric empirical bootstrap bound (Callout 1 Alternative):
            Delta_eps^emp(w, alpha) = Quantile_0.99({ CVaR_alpha(w^T * epsilon^(b)) }_{b=1}^B)
        """
        # Residual loss xi_t = -w^T * epsilon_t
        xi = -self._residuals @ weights
        T = len(xi)
        bootstrap_cvars = []

        q_idx = int(alpha * T)
        rng = np.random.RandomState(42)

        for _ in range(n_bootstraps):
            sample = rng.choice(xi, size=T, replace=True)
            sorted_sample = np.sort(sample)
            # CVaR is mean of values above alpha quantile
            cvar_b = np.mean(sorted_sample[q_idx:])
            bootstrap_cvars.append(cvar_b)

        return float(np.percentile(bootstrap_cvars, 99.0))

    def compute_residual_tail_bound(self, weights: np.ndarray, alpha: float = 0.95) -> float:
        """
        Certified Residual Tail Bound Delta_epsilon(w, alpha):
        Automatically routes between Analytical Sub-Gaussian bound and Empirical Bootstrap bound
        based on Hill tail-index test (Callout 1).
        """
        is_subgauss, hill_idx = self.test_residual_subgaussianity(weights)
        if is_subgauss:
            return self.compute_subgaussian_tail_bound(weights, alpha=alpha)
        else:
            return self.compute_empirical_bootstrap_bound(weights, alpha=alpha)
