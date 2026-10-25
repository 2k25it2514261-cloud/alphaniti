"""
AlphaNiti v25.0 Institutional Execution Engine
Pipeline Module 4 - Indian Statutory Costs & Non-Convex Fee Relaxations
Reference: docs/04_PORTFOLIO_OPTIMIZATION_AND_DISCRETE_SIZING.md & Finance Act 2026
"""

from typing import Dict, Any, Tuple
import numpy as np


class IndianStatutoryCostModel:
    """
    Statutory taxes, exchange transaction costs, non-convex brokerage fees,
    and market impact schedules under the Indian regulatory framework (Finance Act 2026).
    """

    def __init__(
        self,
        stt_rate: float = 0.001,  # 0.1% on delivery equity buy & sell
        stamp_duty_rate: float = 0.00015,  # 0.015% on buy turnover
        nse_turnover_rate: float = 0.0000325,  # 0.00325% NSE cash segment
        sebi_turnover_rate: float = 0.000001,  # Rs 10 per crore (0.0001%)
        brokerage_rate: float = 0.0003,  # 3 bps or flat fee
        brokerage_min_inr: float = 20.0,  # Rs 20 fixed minimum per executed order
        gst_rate: float = 0.18,  # 18% GST on brokerage + exchange + sebi charges
        impact_eta: float = 0.1,  # Market impact coefficient
    ):
        self.stt_rate = stt_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.nse_turnover_rate = nse_turnover_rate
        self.sebi_turnover_rate = sebi_turnover_rate
        self.brokerage_rate = brokerage_rate
        self.brokerage_min_inr = brokerage_min_inr
        self.gst_rate = gst_rate
        self.impact_eta = impact_eta

    def compute_cost(
        self,
        shares: float,
        price: float,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> float:
        """
        Compute total execution friction:
            Cost(q) = SpreadCost(q) + ImpactCost(q) + Brokerage(q) + StatutoryTaxes(q)
        """
        if shares <= 0:
            return 0.0

        turnover = shares * price

        # 1. Spread and Quadratic Market Impact
        spread_cost = shares * (spread / 2.0)
        participation = np.sqrt(shares / max(1.0, adv))
        impact_cost = self.impact_eta * volatility * participation * turnover

        # 2. Brokerage with downward non-convex kink at q=0
        brokerage = max(self.brokerage_min_inr, self.brokerage_rate * turnover)

        # 3. Exchange and SEBI charges
        exchange_charges = self.nse_turnover_rate * turnover
        sebi_charges = self.sebi_turnover_rate * turnover

        # 4. GST on (brokerage + exchange + sebi)
        gst = self.gst_rate * (brokerage + exchange_charges + sebi_charges)

        # 5. STT (Securities Transaction Tax)
        stt = self.stt_rate * turnover

        # 6. Stamp duty (payable on buy transactions)
        stamp_duty = self.stamp_duty_rate * turnover if is_buy else 0.0

        total_statutory = stt + stamp_duty + exchange_charges + sebi_charges + gst
        return float(spread_cost + impact_cost + brokerage + total_statutory)

    def compute_detailed_breakdown(
        self,
        shares: float,
        price: float,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> Dict[str, float]:
        """Returns itemised Finance Act 2026 statutory tax & friction breakdown."""
        if shares <= 0:
            return {
                "stt": 0.0,
                "stamp_duty": 0.0,
                "exchange_nse": 0.0,
                "sebi_fee": 0.0,
                "brokerage": 0.0,
                "gst_18pct": 0.0,
                "spread_and_impact": 0.0,
                "total_charges": 0.0,
            }
        turnover = shares * price
        spread_cost = shares * (spread / 2.0)
        participation = np.sqrt(shares / max(1.0, adv))
        impact_cost = self.impact_eta * volatility * participation * turnover
        brokerage = max(self.brokerage_min_inr, self.brokerage_rate * turnover)
        exchange_charges = self.nse_turnover_rate * turnover
        sebi_charges = self.sebi_turnover_rate * turnover
        gst = self.gst_rate * (brokerage + exchange_charges + sebi_charges)
        stt = self.stt_rate * turnover
        stamp_duty = self.stamp_duty_rate * turnover if is_buy else 0.0
        total = spread_cost + impact_cost + brokerage + stt + stamp_duty + exchange_charges + sebi_charges + gst
        return {
            "stt": float(stt),
            "stamp_duty": float(stamp_duty),
            "exchange_nse": float(exchange_charges),
            "sebi_fee": float(sebi_charges),
            "brokerage": float(brokerage),
            "gst_18pct": float(gst),
            "spread_and_impact": float(spread_cost + impact_cost),
            "total_charges": float(total),
        }

    def lower_convex_envelope_slope(
        self,
        price: float,
        u_upper_bound: float = 1.0,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> float:
        """
        Constructs the valid lower convex envelope Cost_i^cvx(q) on R_>=0 (Theorem 1, Section 3).
        Since Market Impact is convex (>= 0) and Brokerage(q) = max(Fee_min, c_rate * turnover) >= c_rate * turnover,
        the marginal affine underestimator:
            c_slope = spread/2 + (brokerage_rate + statutory_rates) * price
        strictly satisfies Cost_i^cvx(q) = c_slope * q <= Cost_i(q) for all q >= 0.
        """
        spread_slope = spread / 2.0
        brokerage_slope = self.brokerage_rate * price

        exchange_slope = self.nse_turnover_rate * price
        sebi_slope = self.sebi_turnover_rate * price
        gst_slope = self.gst_rate * (brokerage_slope + exchange_slope + sebi_slope)
        stt_slope = self.stt_rate * price
        stamp_slope = (self.stamp_duty_rate * price) if is_buy else 0.0

        statutory_slope = stt_slope + stamp_slope + exchange_slope + sebi_slope + gst_slope
        return float(spread_slope + brokerage_slope + statutory_slope)
