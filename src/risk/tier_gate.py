"""
AlphaNiti v25.0 Institutional Execution & Risk Management Engine
Pipeline Module 2 & 3 - Canonical 4-Tier Operating Gate with Dual-Threshold Hysteresis
Reference: docs/02_FORECASTING_AND_EVIDENTIAL_DEEP_ENSEMBLE.md & config/uncertainty_thresholds.json
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, List


class HysteresisTierGate:
    """
    State machine implementing the Canonical 4-Tier Operating Architecture (Tiers 0-3)
    with dual-threshold hysteresis and session persistence guards to prevent daily flip-flopping.

    Tiers:
      Tier 0: Normal Execution (u_t < theta_soft)
      Tier 1: Soft Buy-Freeze (theta_soft <= u_t < theta_elevated)
      Tier 2: De-Grossing (theta_elevated <= u_t < theta_extreme)
      Tier 3: Systemic Freeze (u_t >= theta_extreme OR Circuit Halt)
    """

    def __init__(
        self,
        theta_soft: Optional[float] = None,
        theta_elevated: Optional[float] = None,
        theta_extreme: Optional[float] = None,
        delta_hysteresis: Optional[float] = None,
        persistence_sessions: Optional[int] = None,
        config_path: Optional[str] = None,
    ):
        def_soft = 0.00185
        def_elevated = 0.00342
        def_extreme = 0.00780
        def_hys = 0.15
        def_persist = 3

        if config_path is None:
            candidate = Path(__file__).resolve().parent.parent.parent / "config" / "uncertainty_thresholds.json"
            if candidate.exists():
                config_path = str(candidate)

        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            quantiles = cfg.get("quantiles", {})
            hys = cfg.get("hysteresis", {})
            cfg_soft = quantiles.get("theta_soft", def_soft)
            cfg_elevated = quantiles.get("theta_elevated", def_elevated)
            cfg_extreme = quantiles.get("theta_extreme", def_extreme)
            cfg_hys = hys.get("delta_hysteresis", def_hys)
            cfg_persist = hys.get("persistence_sessions_required", def_persist)
        else:
            cfg_soft, cfg_elevated, cfg_extreme, cfg_hys, cfg_persist = (
                def_soft,
                def_elevated,
                def_extreme,
                def_hys,
                def_persist,
            )

        self.theta_soft = theta_soft if theta_soft is not None else cfg_soft
        self.theta_elevated = theta_elevated if theta_elevated is not None else cfg_elevated
        self.theta_extreme = theta_extreme if theta_extreme is not None else cfg_extreme
        self.delta_hys = delta_hysteresis if delta_hysteresis is not None else cfg_hys
        self.persistence_sessions = persistence_sessions if persistence_sessions is not None else cfg_persist

        # Exit thresholds with hysteresis deadband: theta * (1 - delta_hys)
        self.theta_soft_exit = self.theta_soft * (1.0 - self.delta_hys)
        self.theta_elevated_exit = self.theta_elevated * (1.0 - self.delta_hys)
        self.theta_extreme_exit = self.theta_extreme * (1.0 - self.delta_hys)

        self.current_tier: int = 0
        self.consecutive_sub_threshold_days: int = 0
        self.history: List[Dict[str, Any]] = []

    def reset(self):
        """Reset gate state to initial Tier 0."""
        self.current_tier = 0
        self.consecutive_sub_threshold_days = 0
        self.history = []

    def update(self, uncertainty: float, is_circuit_halt: bool = False) -> Dict[str, Any]:
        """
        Evaluate current epistemic uncertainty and determine active operating tier.
        Returns detailed action directives for portfolio rebalancing.
        """
        u = float(uncertainty)

        # 1. Structural Out-of-Band Breach or Exchange Circuit Halt -> Tier 3
        if is_circuit_halt:
            self.current_tier = 3
            self.consecutive_sub_threshold_days = 0
            return self._build_directive(u, reason="EXCHANGE_CIRCUIT_HALT")

        # 2. Immediate Upward Escalation (zero delay on risk expansion)
        if u >= self.theta_extreme:
            if self.current_tier != 3:
                self.current_tier = 3
                self.consecutive_sub_threshold_days = 0
        elif u >= self.theta_elevated and self.current_tier < 2:
            self.current_tier = 2
            self.consecutive_sub_threshold_days = 0
        elif u >= self.theta_soft and self.current_tier < 1:
            self.current_tier = 1
            self.consecutive_sub_threshold_days = 0

        # 3. Controlled Downward De-escalation with Hysteresis & Persistence
        else:
            if self.current_tier == 3:
                if u <= self.theta_extreme_exit:
                    self.consecutive_sub_threshold_days += 1
                    if self.consecutive_sub_threshold_days >= self.persistence_sessions:
                        self.current_tier = 2
                        self.consecutive_sub_threshold_days = 0
                else:
                    self.consecutive_sub_threshold_days = 0

            elif self.current_tier == 2:
                if u <= self.theta_elevated_exit:
                    self.consecutive_sub_threshold_days += 1
                    if self.consecutive_sub_threshold_days >= self.persistence_sessions:
                        self.current_tier = 1
                        self.consecutive_sub_threshold_days = 0
                else:
                    self.consecutive_sub_threshold_days = 0

            elif self.current_tier == 1:
                if u <= self.theta_soft_exit:
                    self.consecutive_sub_threshold_days += 1
                    if self.consecutive_sub_threshold_days >= self.persistence_sessions:
                        self.current_tier = 0
                        self.consecutive_sub_threshold_days = 0
                else:
                    self.consecutive_sub_threshold_days = 0

        return self._build_directive(u)

    def _build_directive(self, u: float, reason: Optional[str] = None) -> Dict[str, Any]:
        """Maps active operating tier to execution constraints."""
        tier_names = {
            0: "Normal Execution",
            1: "Soft Buy-Freeze",
            2: "De-Grossing",
            3: "Systemic Freeze",
        }

        # Action parameters per tier
        allow_buys = (self.current_tier == 0)
        position_multiplier = {0: 1.0, 1: 1.0, 2: 0.50, 3: 0.0}[self.current_tier]
        horizon_days = {0: 20, 1: 5, 2: 1, 3: 0}[self.current_tier]
        turnover_cap = {0: 1.0, 1: 0.0, 2: 0.05, 3: 0.0}[self.current_tier]

        directive = {
            "tier": self.current_tier,
            "tier_name": tier_names[self.current_tier],
            "uncertainty_value": u,
            "allow_buys": allow_buys,
            "position_multiplier": position_multiplier,
            "forward_horizon_days": horizon_days,
            "turnover_cap_adv": turnover_cap,
            "persistence_counter": self.consecutive_sub_threshold_days,
            "reason": reason or f"TIER_{self.current_tier}_UNCERTAINTY",
        }
        self.history.append(directive)
        return directive
