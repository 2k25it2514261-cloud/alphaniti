"""
AlphaNiti v25.0 Institutional Execution & Risk Management Engine
Pipeline Module: src/risk/decision_score.py

Unified Synthesis Engine: Composite Decision Score (S in [0, 100])
Incorporating:
1. Alpha Directional Guard (Kills Confident Bear Flaw)
2. Bounded & Protected Sub-Scores (Alpha 40, Confidence 25, Tail 25, Friction 10)
3. Alpha Dominance Hurdle (Prevents Passive Safety Inversion / Low-Beta Trap)
4. Canonical 4-Tier Hysteresis Gate Policy Enforcement (No BUYs during Tier 1 Freeze)
5. Horizon-Matched Friction Buffer (T=20D vs Post-Oct 2024 Statutory Taxes)
"""

import math
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple


@dataclass
class DecisionScoreResult:
    ticker: str
    predicted_return_pct: float       # Horizon return e.g. 20D %
    final_score: float                # S in [0, 100]
    raw_score: float                  # S_raw in [0, 100]
    verdict: str                      # Institutional action verdict
    verdict_badge: str                # UI badge: "STRONG_BUY", "HOLD_DEFENSIVE", "AVOID_GATE", etc.
    s_alpha: float                    # [0, 40]
    s_confidence: float               # [0, 25]
    s_tail: float                     # [0, 25]
    s_friction: float                 # [0, 10]
    gate_multiplier: float            # In {1.0, 0.75, 0.35, 0.0}
    gate_tier: int                    # 0, 1, 2, or 3
    rationale: List[str]              # 3-Bullet Institutional Rationale

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "predicted_return_pct": self.predicted_return_pct,
            "final_score": self.final_score,
            "raw_score": self.raw_score,
            "verdict": self.verdict,
            "verdict_badge": self.verdict_badge,
            "s_alpha": self.s_alpha,
            "s_confidence": self.s_confidence,
            "s_tail": self.s_tail,
            "s_friction": self.s_friction,
            "gate_multiplier": self.gate_multiplier,
            "gate_tier": self.gate_tier,
            "rationale": self.rationale,
        }


def compute_composite_decision_score(
    ticker: str,
    gamma_bar_horizon: float,          # Expected return over horizon (e.g. +0.0352 for +3.52%)
    sigma_alea_horizon: float,         # Aleatoric volatility over horizon (e.g. 0.035)
    sigma_epis: float,                 # Epistemic uncertainty (std dev or variance)
    p_breach: float,                   # Tail barrier breach probability (e.g. 0.15)
    gate_tier: int = 0,                # Current HysteresisTierGate tier (0, 1, 2, or 3)
    gate_multiplier: Optional[float] = None, # Optional override; defaults to tier multiplier
    theta_extreme: float = 0.00780,    # 99th percentile epistemic threshold from config
    breach_ceiling: float = 0.35,      # Tail barrier breach ceiling (35%)
    c_tax: Optional[float] = None,     # Total rupee statutory taxes (STT + GST + Stamp)
    pi_gross: Optional[float] = None,  # Total gross expected profit in rupees
    tau_statutory: float = 0.0025,     # Round-trip statutory delivery friction (25 bps)
    alpha_hurdle_pct: float = 1.0,     # Min horizon return required for BUY (e.g. +1.0%)
) -> DecisionScoreResult:
    """
    Computes institutional Composite Decision Score S in [0, 100] with full defensive guards.
    """
    # 0. Resolve Gate Multiplier from Tier
    tier_multipliers = {
        0: 1.00,  # Tier 0: Normal Execution
        1: 0.75,  # Tier 1: Soft Buy-Freeze (Zero new longs, defensive trail)
        2: 0.35,  # Tier 2: De-Grossing (Linear 50% de-leverage)
        3: 0.00,  # Tier 3: Systemic Freeze / Cash Shelter
    }
    if gate_multiplier is None:
        effective_multiplier = tier_multipliers.get(gate_tier, 1.00)
    else:
        effective_multiplier = float(gate_multiplier)

    pred_ret_pct = round(gamma_bar_horizon * 100.0, 2)

    # 1. Alpha Directional Guard (Kills Confident Bear Flaw)
    # If horizon drift is <= 0, immediate Hard AVOID (Score 0.0)
    if gamma_bar_horizon <= 0.0:
        return DecisionScoreResult(
            ticker=ticker,
            predicted_return_pct=pred_ret_pct,
            final_score=0.0,
            raw_score=0.0,
            verdict="AVOID (Negative Drift)",
            verdict_badge="AVOID_NEGATIVE_ALPHA",
            s_alpha=0.0,
            s_confidence=0.0,
            s_tail=0.0,
            s_friction=0.0,
            gate_multiplier=effective_multiplier,
            gate_tier=gate_tier,
            rationale=[
                f"Negative expected drift (\u03b3\u0304 = {pred_ret_pct:+.2f}%): Capital preservation triggered.",
                "Alpha Directional Guard active: Immediate AVOID on zero/negative return expectation.",
                "Risk gate overrides all lower-level confidence signals."
            ]
        )

    # 2. Bounded & Protected Sub-Scores
    # S_alpha (0 to 40 pts)
    # For a 20-day horizon, a return-to-volatility ratio (Sharpe proxy) of 0.8 to 1.0 is exceptional.
    # We calibrate benchmark to 0.75 horizon information ratio:
    effective_vol = max(float(sigma_alea_horizon), 1e-6)
    sr_horizon = gamma_bar_horizon / effective_vol
    sr_benchmark = 0.75  # Target horizon Sharpe
    s_alpha = 40.0 * min(1.0, max(0.0, sr_horizon / sr_benchmark))

    # S_confidence (0 to 25 pts)
    # If sigma_epis is variance vs std dev, handle cleanly:
    clean_epis = max(0.0, float(sigma_epis))
    # If clean_epis exceeds theta_extreme, s_confidence drops linearly to 0
    s_confidence = 25.0 * min(1.0, max(0.0, 1.0 - (clean_epis / theta_extreme)))

    # S_tail (0 to 25 pts)
    # Calibrated against breach_ceiling (0.35)
    clean_breach = max(0.0, float(p_breach))
    s_tail = 25.0 * min(1.0, max(0.0, 1.0 - (clean_breach / breach_ceiling)))

    # S_friction (0 to 10 pts)
    # Evaluates whether expected return comfortably covers statutory transaction drag
    if c_tax is not None and pi_gross is not None and pi_gross > 0:
        if pi_gross > c_tax:
            s_friction = 10.0 * min(1.0, max(0.0, 1.0 - (c_tax / pi_gross)))
        else:
            s_friction = 0.0
    else:
        # Screening mode: compare horizon return to round-trip statutory rate
        if gamma_bar_horizon > tau_statutory:
            s_friction = 10.0 * min(1.0, max(0.0, 1.0 - (tau_statutory / gamma_bar_horizon)))
        else:
            s_friction = 0.0

    # 3. Raw Score & Hysteresis Scaling
    s_raw = s_alpha + s_confidence + s_tail + s_friction
    final_score = min(100.0, max(0.0, s_raw * effective_multiplier))

    # 4. Action Verdict Engine with Alpha Dominance Hurdle & Tier Policy
    # Rule 1: Tier 3 Systemic Freeze -> Strictly AVOID / CASH SHELTER
    if gate_tier == 3 or effective_multiplier == 0.0:
        verdict = "AVOID / CASH SHELTER (Tier 3 Systemic Gate)"
        verdict_badge = "CASH_GATE"
    
    # Rule 2: Tier 2 De-Grossing -> Strictly TRIM / DE-GROSS
    elif gate_tier == 2 or effective_multiplier <= 0.35:
        verdict = "TRIM / DE-GROSS (Tier 2 Elevated Risk)"
        verdict_badge = "DE_GROSS"

    # Rule 3: Tier 1 Soft Buy-Freeze -> FORBIDS new long buys!
    # Even if score is high, it can only be HOLD / DEFENSIVE TRAIL
    elif gate_tier == 1 or effective_multiplier == 0.75:
        if final_score >= 35.0 and pred_ret_pct >= alpha_hurdle_pct:
            verdict = "HOLD / DEFENSIVE TRAIL (Tier 1 Buy-Freeze)"
            verdict_badge = "HOLD_DEFENSIVE"
        else:
            verdict = "AVOID / LIGHTEN (Tier 1 Elevated Noise)"
            verdict_badge = "AVOID_GATE"

    # Rule 4: Tier 0 Unconstrained Execution (Normal Operation)
    else:
        # Alpha Dominance Hurdle: To qualify for BUY, alpha must clear hurdle
        has_alpha_dominance = (pred_ret_pct >= alpha_hurdle_pct) and (s_alpha >= 18.0)
        
        if final_score >= 70.0 and has_alpha_dominance:
            verdict = "ACCUMULATE / STRONG BUY"
            verdict_badge = "STRONG_BUY"
        elif final_score >= 50.0 and pred_ret_pct > 0.0:
            verdict = "HOLD / ACCUMULATE ON DIPS"
            verdict_badge = "ACCUMULATE_DIPS"
        elif final_score >= 40.0:
            verdict = "HOLD CUSTODIAL"
            verdict_badge = "HOLD_CUSTODIAL"
        else:
            verdict = "AVOID / HARVEST"
            verdict_badge = "AVOID_SUB_THRESHOLD"

    # 5. 3-Bullet Institutional Rationale
    bullet_1 = (
        f"Return & Noise: 20D Drift {pred_ret_pct:+.2f}% vs Vol {sigma_alea_horizon*100:.2f}% "
        f"\u2192 Alpha Score {s_alpha:.1f}/40 pts."
    )
    bullet_2 = (
        f"Model & Tail: Trust {s_confidence:.1f}/25 pts | Tail Safety {s_tail:.1f}/25 pts "
        f"(Barrier Breach Likelihood: {clean_breach*100:.1f}% vs {breach_ceiling*100:.0f}% cap)."
    )
    bullet_3 = (
        f"Gate & Execution: Friction Buffer {s_friction:.1f}/10 pts | "
        f"Tier {gate_tier} Gate ({effective_multiplier:.2f}x) \u2192 Final Composite Score {final_score:.1f}/100."
    )

    return DecisionScoreResult(
        ticker=ticker,
        predicted_return_pct=pred_ret_pct,
        final_score=round(final_score, 1),
        raw_score=round(s_raw, 1),
        verdict=verdict,
        verdict_badge=verdict_badge,
        s_alpha=round(s_alpha, 1),
        s_confidence=round(s_confidence, 1),
        s_tail=round(s_tail, 1),
        s_friction=round(s_friction, 1),
        gate_multiplier=effective_multiplier,
        gate_tier=gate_tier,
        rationale=[bullet_1, bullet_2, bullet_3],
    )


def score_universe(
    signals: List[Dict[str, Any]],
    gate_tier: int = 0,
    gate_multiplier: Optional[float] = None,
    theta_extreme: float = 0.00780,
    breach_ceiling: float = 0.35,
) -> List[DecisionScoreResult]:
    """
    Scores an entire universe of asset signals, enforces cross-sectional ranking consistency,
    and returns results sorted by Final Score descending (with Alpha tie-breaking).
    """
    results: List[DecisionScoreResult] = []
    
    for sig in signals:
        ticker = sig.get("symbol", sig.get("ticker", "UNKNOWN"))
        pred_ret = sig.get("predicted_return_pct", 0.0) / 100.0
        ale_vol = sig.get("aleatoric_vol_pct", 3.0) / 100.0
        epi_unc = sig.get("epistemic_uncertainty", 0.002)
        p_breach = sig.get("drawdown_crash_prob", 0.15)

        res = compute_composite_decision_score(
            ticker=ticker,
            gamma_bar_horizon=pred_ret,
            sigma_alea_horizon=ale_vol,
            sigma_epis=epi_unc,
            p_breach=p_breach,
            gate_tier=gate_tier,
            gate_multiplier=gate_multiplier,
            theta_extreme=theta_extreme,
            breach_ceiling=breach_ceiling,
        )
        results.append(res)

    # Sort primarily by Final Score descending, tie-break by predicted return descending
    results.sort(key=lambda r: (r.final_score, r.predicted_return_pct), reverse=True)
    return results
