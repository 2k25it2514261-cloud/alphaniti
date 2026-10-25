"""
AlphaNiti v25.0: Pipeline Module 1 - Corporate Action Normalization Engine
Formal Mathematical Formulations for Point-in-Time Corporate Action Adjustments

Corporate Action Multipliers:
  1. Stock Splits & Consolidations:
     f_{i,k} = A / B
     P_adj = P * f,  V_adj = V * (1 / f)
  2. Bonus Share Issues:
     f_{i,k} = A / (A + B)
     P_adj = P * f,  V_adj = V * (1 / f)
  3. Rights Offerings:
     P_ex_theo = (A * P_cum + B * P_rights) / (A + B)
     f_{i,k} = P_ex_theo / P_cum
  4. Ordinary Cash Dividends:
     f_{i,k} = 1 - (D / P_cum)
     (Prices scaled by f, volume invariant)

Cumulative Adjustment Factor:
  c_i(t | t_decision) = prod_{k: t < t_k <= t_decision} f_{i,k}
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import List, Optional, Union
import numpy as np
import pandas as pd
import polars as pl


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    CONSOLIDATION = "CONSOLIDATION"
    BONUS = "BONUS"
    RIGHTS = "RIGHTS"
    DIVIDEND = "DIVIDEND"


@dataclass(frozen=True)
class CorporateActionEvent:
    symbol: str
    ex_date: date
    action_type: ActionType
    # Parameters:
    # Splits/Consolidations/Bonus: A existing, B new/bonus
    # Rights: A existing, B rights shares, p_rights subscription price, p_cum cum-price
    # Dividend: div_amount, p_cum cum-price
    A: float = 1.0
    B: float = 1.0
    p_cum: Optional[float] = None
    p_rights: Optional[float] = None
    div_amount: Optional[float] = None

    def compute_multiplier(self) -> float:
        """
        Calculates canonical adjustment multiplier f_{i,k} in (0, 1].
        """
        if self.action_type in [ActionType.SPLIT, ActionType.CONSOLIDATION]:
            # A existing into B new shares (e.g. 10:1 split, A=1, B=10 => f = 0.1)
            if self.B <= 0 or self.A <= 0:
                raise ValueError(f"Invalid split ratio: {self.A}:{self.B}")
            return float(self.A / self.B)

        elif self.action_type == ActionType.BONUS:
            # B bonus shares for every A existing shares held (e.g. 1:1 bonus, A=1, B=1 => f = 0.5)
            if (self.A + self.B) <= 0 or self.A <= 0:
                raise ValueError(f"Invalid bonus ratio: {self.A}:{self.B}")
            return float(self.A / (self.A + self.B))

        elif self.action_type == ActionType.RIGHTS:
            # B rights shares at p_rights for every A shares held
            if self.p_cum is None or self.p_cum <= 0:
                raise ValueError("p_cum (cum-rights closing price) must be positive.")
            if self.p_rights is None or self.p_rights < 0:
                raise ValueError("p_rights (subscription price) must be non-negative.")
            p_ex_theo = (self.A * self.p_cum + self.B * self.p_rights) / (self.A + self.B)
            return float(p_ex_theo / self.p_cum)

        elif self.action_type == ActionType.DIVIDEND:
            # Ordinary cash dividend D with cum-dividend closing price p_cum
            if self.div_amount is None or self.div_amount <= 0:
                raise ValueError("div_amount must be positive.")
            if self.p_cum is None or self.p_cum <= 0:
                raise ValueError("p_cum must be positive.")
            if self.div_amount >= self.p_cum:
                raise ValueError(f"Dividend amount {self.div_amount} >= cum price {self.p_cum}")
            return float(1.0 - (self.div_amount / self.p_cum))

        else:
            raise NotImplementedError(f"Unsupported action type: {self.action_type}")


def compute_cumulative_adjustment_factor(
    events: List[CorporateActionEvent],
    history_date: date,
    decision_date: date,
    symbol: Optional[str] = None
) -> float:
    """
    Computes decision-time relative cumulative adjustment factor:
      c_i(t | t_decision) = prod_{k: t < t_k <= t_decision} f_{i,k}
    
    Enforces causal barrier: events with ex_date > t_decision are strictly ignored.
    """
    factor = 1.0
    for ev in events:
        if symbol is not None and ev.symbol != symbol:
            continue
        # Invariant: t < t_k <= t_decision
        if history_date < ev.ex_date <= decision_date:
            factor *= ev.compute_multiplier()
    return factor


def apply_causal_corporate_actions(
    df: pl.DataFrame,
    events: List[CorporateActionEvent],
    decision_date: date,
    col_date: str = "date",
    col_symbol: str = "symbol",
    col_price_prefix: List[str] = ["open", "high", "low", "close"],
    col_volume: str = "volume"
) -> pl.DataFrame:
    """
    Causally adjusts historical unadjusted price and volume series up to decision_date.
    Maintains both raw and adjusted fields:
      P_adj(t) = P(t) * c_i(t | t_decision)
      V_adj(t) = V(t) * (1 / c_i_non_div(t | t_decision))
    """
    # Pre-sort events
    ev_list = sorted([e for e in events if e.ex_date <= decision_date], key=lambda x: x.ex_date)
    
    records = []
    # Process per symbol
    for sym in df[col_symbol].unique().to_list():
        sym_df = df.filter(pl.col(col_symbol) == sym).sort(col_date)
        sym_events = [e for e in ev_list if e.symbol == sym]
        
        dates_list = sym_df[col_date].to_list()
        cum_factors_price = []
        cum_factors_vol = []
        
        for d in dates_list:
            d_val = d if isinstance(d, date) else d.date() if isinstance(d, datetime) else pd.to_datetime(d).date()
            f_px = 1.0
            f_vol = 1.0
            for ev in sym_events:
                if d_val < ev.ex_date <= decision_date:
                    m = ev.compute_multiplier()
                    f_px *= m
                    # Dividends do NOT adjust volume
                    if ev.action_type != ActionType.DIVIDEND:
                        f_vol *= m
            cum_factors_price.append(f_px)
            cum_factors_vol.append(1.0 / f_vol if f_vol > 0 else 1.0)
            
        sym_df = sym_df.with_columns([
            pl.Series("cum_factor_price", cum_factors_price, dtype=pl.Float32),
            pl.Series("cum_factor_volume", cum_factors_vol, dtype=pl.Float32)
        ])
        
        # Apply adjustments
        exprs = []
        for p_col in col_price_prefix:
            if p_col in sym_df.columns:
                exprs.append((pl.col(p_col) * pl.col("cum_factor_price")).alias(f"{p_col}_adj"))
        if col_volume in sym_df.columns:
            exprs.append((pl.col(col_volume) * pl.col("cum_factor_volume")).alias(f"{col_volume}_adj"))
            
        sym_df = sym_df.with_columns(exprs)
        records.append(sym_df)
        
    return pl.concat(records)
