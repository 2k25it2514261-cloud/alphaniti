"""
AlphaNiti v25.0: Unit Test Suite for Unified Synthesis Engine & Decision Score
Tests defensive bounds, directional guards, tier gate enforcement, and inversion prevention.
"""

import pytest
from src.risk.decision_score import (
    compute_composite_decision_score,
    score_universe,
    DecisionScoreResult,
)


def test_alpha_directional_guard_on_negative_return():
    """Verify that negative or zero expected return triggers immediate AVOID and 0.0 score."""
    res_neg = compute_composite_decision_score(
        ticker="BEAR_STOCK",
        gamma_bar_horizon=-0.02,  # -2.0%
        sigma_alea_horizon=0.03,
        sigma_epis=0.0005,
        p_breach=0.05,
        gate_tier=0,
    )
    assert res_neg.final_score == 0.0
    assert res_neg.raw_score == 0.0
    assert "AVOID" in res_neg.verdict
    assert res_neg.s_alpha == 0.0
    assert res_neg.s_confidence == 0.0
    assert res_neg.s_tail == 0.0
    assert res_neg.s_friction == 0.0
    assert len(res_neg.rationale) == 3

    res_zero = compute_composite_decision_score(
        ticker="ZERO_STOCK",
        gamma_bar_horizon=0.0,
        sigma_alea_horizon=0.02,
        sigma_epis=0.001,
        p_breach=0.10,
        gate_tier=0,
    )
    assert res_zero.final_score == 0.0
    assert "AVOID" in res_zero.verdict


def test_strict_subscore_and_final_bounds():
    """Verify all sub-scores and final scores are strictly bounded in [0, 40], [0, 25], [0, 10], [0, 100]."""
    # Massive positive edge case
    res_huge = compute_composite_decision_score(
        ticker="ROCKET",
        gamma_bar_horizon=0.50,  # +50%
        sigma_alea_horizon=0.01,
        sigma_epis=0.0,
        p_breach=0.0,
        gate_tier=0,
    )
    assert res_huge.s_alpha == 40.0
    assert res_huge.s_confidence == 25.0
    assert res_huge.s_tail == 25.0
    assert 9.5 <= res_huge.s_friction <= 10.0
    assert res_huge.raw_score >= 99.0
    assert res_huge.final_score >= 99.0
    assert "BUY" in res_huge.verdict

    # Worst-case high risk asset
    res_toxic = compute_composite_decision_score(
        ticker="TOXIC",
        gamma_bar_horizon=0.005,  # Tiny positive return
        sigma_alea_horizon=0.10,   # High noise
        sigma_epis=0.02,          # High epistemic uncertainty
        p_breach=0.50,            # High crash probability
        gate_tier=0,
    )
    assert 0.0 <= res_toxic.s_alpha <= 40.0
    assert res_toxic.s_confidence == 0.0  # Fully penalized
    assert res_toxic.s_tail == 0.0        # Fully penalized
    assert 0.0 <= res_toxic.final_score <= 100.0
    assert "AVOID" in res_toxic.verdict


def test_alpha_dominance_prevents_inversion():
    """
    Verify that an asset with low alpha cannot get a BUY verdict purely from passive confidence,
    and higher alpha assets with sound risk outscore weaker ones.
    """
    # Asset with weak return (+0.5%) but great confidence and safety
    res_weak = compute_composite_decision_score(
        ticker="WEAK_SAFE",
        gamma_bar_horizon=0.005,  # +0.50%
        sigma_alea_horizon=0.015,
        sigma_epis=0.001,
        p_breach=0.05,
        gate_tier=0,
    )
    # Asset with strong return (+3.5%) and acceptable risk
    res_strong = compute_composite_decision_score(
        ticker="STRONG_GROWTH",
        gamma_bar_horizon=0.035,  # +3.50%
        sigma_alea_horizon=0.030,
        sigma_epis=0.002,
        p_breach=0.15,
        gate_tier=0,
    )
    # Weak asset must NOT be a BUY
    assert "BUY" not in res_weak.verdict
    # Strong asset must outscore weak asset
    assert res_strong.final_score > res_weak.final_score
    assert "BUY" in res_strong.verdict


def test_hysteresis_tier_policy_enforcement():
    """
    Verify that Tier 1 forbids BUYs, Tier 2 enforces TRIM, Tier 3 enforces CASH SHELTER.
    """
    # A stellar stock in Tier 1 (Soft Buy-Freeze)
    res_tier1 = compute_composite_decision_score(
        ticker="STELLAR_IN_FREEZE",
        gamma_bar_horizon=0.04,
        sigma_alea_horizon=0.02,
        sigma_epis=0.001,
        p_breach=0.10,
        gate_tier=1,
    )
    assert res_tier1.gate_multiplier == 0.75
    # Must NOT be BUY! Policy requires freeze on new long initiations
    assert "BUY" not in res_tier1.verdict
    assert "HOLD" in res_tier1.verdict or "DEFENSIVE" in res_tier1.verdict

    # In Tier 2 (De-Grossing)
    res_tier2 = compute_composite_decision_score(
        ticker="STELLAR_IN_DEGROSS",
        gamma_bar_horizon=0.04,
        sigma_alea_horizon=0.02,
        sigma_epis=0.001,
        p_breach=0.10,
        gate_tier=2,
    )
    assert res_tier2.gate_multiplier == 0.35
    assert "TRIM" in res_tier2.verdict or "DE-GROSS" in res_tier2.verdict

    # In Tier 3 (Systemic Freeze)
    res_tier3 = compute_composite_decision_score(
        ticker="STELLAR_IN_CRASH",
        gamma_bar_horizon=0.04,
        sigma_alea_horizon=0.02,
        sigma_epis=0.001,
        p_breach=0.10,
        gate_tier=3,
    )
    assert res_tier3.gate_multiplier == 0.00
    assert res_tier3.final_score == 0.0
    assert "CASH" in res_tier3.verdict or "AVOID" in res_tier3.verdict


def test_score_universe_simulation_matches_nifty_basket():
    """
    Simulate the exact 6 tickers from the user's uploaded table and verify:
    1. RELIANCE ranks properly relative to ICICIBANK.
    2. Proper Tier 1 verdict mapping prevents false buys.
    """
    signals = [
        {
            "symbol": "HDFCBANK",
            "predicted_return_pct": 3.52,
            "aleatoric_vol_pct": 3.2,
            "epistemic_uncertainty": 0.0025,
            "drawdown_crash_prob": 0.18,
        },
        {
            "symbol": "SBIN",
            "predicted_return_pct": 2.83,
            "aleatoric_vol_pct": 3.5,
            "epistemic_uncertainty": 0.0028,
            "drawdown_crash_prob": 0.20,
        },
        {
            "symbol": "RELIANCE",
            "predicted_return_pct": 2.34,
            "aleatoric_vol_pct": 3.0,
            "epistemic_uncertainty": 0.0030,
            "drawdown_crash_prob": 0.22,
        },
        {
            "symbol": "ICICIBANK",
            "predicted_return_pct": 1.85,
            "aleatoric_vol_pct": 3.1,
            "epistemic_uncertainty": 0.0022,
            "drawdown_crash_prob": 0.19,
        },
        {
            "symbol": "LT",
            "predicted_return_pct": 1.24,
            "aleatoric_vol_pct": 3.4,
            "epistemic_uncertainty": 0.0032,
            "drawdown_crash_prob": 0.24,
        },
        {
            "symbol": "AXISBANK",
            "predicted_return_pct": 0.85,
            "aleatoric_vol_pct": 3.3,
            "epistemic_uncertainty": 0.0035,
            "drawdown_crash_prob": 0.25,
        },
    ]

    # Test under Tier 0 (Normal)
    results_t0 = score_universe(signals, gate_tier=0)
    assert len(results_t0) == 6
    # RELIANCE (+2.34%) must have higher raw and final score than ICICIBANK (+1.85%)
    rel = next(r for r in results_t0 if r.ticker == "RELIANCE")
    icici = next(r for r in results_t0 if r.ticker == "ICICIBANK")
    assert rel.s_alpha > icici.s_alpha
    assert rel.final_score > icici.final_score

    # Test under Tier 1 (Soft Buy-Freeze, 0.75x)
    results_t1 = score_universe(signals, gate_tier=1)
    # Verify that NONE of them issue BUY in Tier 1
    for r in results_t1:
        assert "BUY" not in r.verdict, f"Violation: {r.ticker} issued BUY in Tier 1 freeze"
