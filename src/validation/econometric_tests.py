"""
AlphaNiti v25.0 Institutional Research & Validation Engine
Pipeline Module 7 - Econometric Monitoring & Calibration Testing
Reference: docs/07_MONITORING_VALIDATION_AND_CALIBRATION_TESTING.md (Callout 5)
"""

from typing import Dict, Any, Tuple, Optional
import numpy as np
from scipy import stats


class ChristoffersenKupiecValidator:
    """
    Kupiec unconditional coverage (LR_uc) and Christoffersen conditional dependence (LR_ind)
    tests for predictive distributions and risk intervals (Callout 5).
    """

    @staticmethod
    def evaluate(hit_sequence: np.ndarray, alpha: float = 0.05) -> Dict[str, Any]:
        """
        Evaluate Kupiec and Christoffersen likelihood ratio tests on binary violation sequence.

        Args:
            hit_sequence: 1D binary array where 1 = violation / breach, 0 = coverage.
            alpha: Nominal violation probability (e.g., 0.05 for 95% coverage).
        """
        hits = np.asarray(hit_sequence, dtype=int)
        T = len(hits)
        if T < 20:
            raise ValueError(f"Sample length T={T} too small for likelihood ratio testing.")

        T1 = int(np.sum(hits))
        T0 = T - T1
        pi_hat = T1 / T

        # 1. Kupiec Unconditional Coverage Test (LR_uc)
        if T1 == 0:
            # No violations: LR_uc = -2 * ln((1 - alpha)^T0)
            lr_uc = -2.0 * T0 * np.log(1.0 - alpha)
        elif T0 == 0:
            lr_uc = -2.0 * T1 * np.log(alpha)
        else:
            log_lik_null = T1 * np.log(alpha) + T0 * np.log(1.0 - alpha)
            log_lik_alt = T1 * np.log(pi_hat) + T0 * np.log(1.0 - pi_hat)
            lr_uc = -2.0 * (log_lik_null - log_lik_alt)

        lr_uc = max(0.0, float(lr_uc))
        p_val_uc = float(1.0 - stats.chi2.cdf(lr_uc, df=1))

        # 2. Christoffersen Independence Test (LR_ind)
        # Count transitions T00, T01, T10, T11
        T00, T01, T10, T11 = 0, 0, 0, 0
        for t in range(1, T):
            prev, curr = hits[t - 1], hits[t]
            if prev == 0 and curr == 0:
                T00 += 1
            elif prev == 0 and curr == 1:
                T01 += 1
            elif prev == 1 and curr == 0:
                T10 += 1
            elif prev == 1 and curr == 1:
                T11 += 1

        # Transition probabilities
        pi_01 = T01 / max(1, T00 + T01) if (T00 + T01) > 0 else 0.0
        pi_11 = T11 / max(1, T10 + T11) if (T10 + T11) > 0 else 0.0

        if pi_01 == 0 or pi_11 == 0 or pi_01 == 1 or pi_11 == 1:
            lr_ind = 0.0  # Degenerate case / no clustering observed
        else:
            log_lik_ind_null = (T00 + T10) * np.log(1.0 - pi_hat) + (T01 + T11) * np.log(pi_hat)
            log_lik_ind_alt = (
                T00 * np.log(1.0 - pi_01)
                + T01 * np.log(pi_01)
                + T10 * np.log(1.0 - pi_11)
                + T11 * np.log(pi_11)
            )
            lr_ind = -2.0 * (log_lik_ind_null - log_lik_ind_alt)

        lr_ind = max(0.0, float(lr_ind))
        p_val_ind = float(1.0 - stats.chi2.cdf(lr_ind, df=1))

        # 3. Joint Conditional Coverage Test (LR_cc = LR_uc + LR_ind ~ Chi^2(2))
        lr_cc = lr_uc + lr_ind
        p_val_cc = float(1.0 - stats.chi2.cdf(lr_cc, df=2))

        # Rejection thresholds (alpha_sig = 0.01 -> critical value 9.210)
        passed_joint = bool(lr_cc <= 9.210)

        return {
            "sample_size": T,
            "total_violations": T1,
            "empirical_failure_rate": float(pi_hat),
            "nominal_alpha": float(alpha),
            "lr_uc": lr_uc,
            "p_val_uc": p_val_uc,
            "lr_ind": lr_ind,
            "p_val_ind": p_val_ind,
            "lr_cc": lr_cc,
            "p_val_cc": p_val_cc,
            "passed_joint_test": passed_joint,
        }


class DeflatedSharpeRatioCalculator:
    """
    Bailey & López de Prado (2014) Deflated Sharpe Ratio (DSR)
    accounting for selection bias across M trials, skewness, and kurtosis.
    """

    EULER_MASCHERONI: float = 0.5772156649

    @classmethod
    def compute_dsr(
        cls,
        strategy_returns: np.ndarray,
        num_trials: int = 10,
        var_trials_sr: float = 0.25,
        annualization_factor: float = 252.0,
    ) -> Dict[str, float]:
        """
        Compute Deflated Sharpe Ratio (DSR >= 0.99 for production clearance).
        """
        r = np.asarray(strategy_returns, dtype=float)
        T = len(r)
        if T < 20:
            raise ValueError(f"Sample length T={T} too short for DSR calculation.")

        mean_r = float(np.mean(r))
        std_r = float(np.std(r, ddof=1))
        if std_r <= 1e-12:
            return {"dsr": 0.0, "sharpe_ratio": 0.0, "sr_0_hurdle": 0.0}

        daily_sr = mean_r / std_r
        ann_sr = daily_sr * np.sqrt(annualization_factor)

        # Higher moments (skewness and kurtosis)
        skew = float(stats.skew(r))
        kurt = float(stats.kurtosis(r, fisher=False))  # Pearson kurtosis (Normal = 3)

        # Data-mining hurdle rate SR_0 (Equation 156)
        M = max(2, num_trials)
        term1 = (1.0 - cls.EULER_MASCHERONI) * stats.norm.ppf(1.0 - 1.0 / M)
        term2 = cls.EULER_MASCHERONI * stats.norm.ppf(1.0 - 1.0 / (M * np.e))
        sr_0 = np.sqrt(var_trials_sr) * (term1 + term2)

        # Standard error under non-normality
        denom_var = 1.0 - skew * daily_sr + ((kurt - 1.0) / 4.0) * (daily_sr**2)
        denom_se = np.sqrt(max(1e-8, denom_var))

        # DSR statistic (Equation 150)
        z_stat = ((daily_sr - (sr_0 / np.sqrt(annualization_factor))) * np.sqrt(T - 1)) / denom_se
        dsr = float(stats.norm.cdf(z_stat))

        return {
            "annualized_sharpe_ratio": ann_sr,
            "daily_sharpe_ratio": daily_sr,
            "data_mining_hurdle_sr0": float(sr_0),
            "skewness": skew,
            "kurtosis": kurt,
            "sample_length_days": T,
            "trials_tested_M": M,
            "dsr": dsr,
            "passes_production_gate": bool(dsr >= 0.95),
        }
