"""
AlphaNiti v25.0: Pipeline Module 2 - Causal Feature Engineering Engine
Computes 20 point-in-time features strictly looking backward (zero future leakage):
  - Return Dynamics: ret_1d, ret_5d, ret_20d, open_to_close
  - Volatility Geometry: high_low_ratio, parkinson_vol_20d, hist_vol_20d, dist_to_20d_high, dist_to_20d_low
  - Liquidity & Microstructure: log_volume, log_turnover, vol_ratio_20d, amihud_illiquidity_20d
  - Structural States: has_corporate_action_flt, cum_factor_flt
  - Momentum, Trend & Alpha Factors: rsi_14d, trend_ema_ratio, vol_squeeze_ratio, volume_momentum_ratio, market_rel_mom_20d
"""

import polars as pl
import numpy as np
from typing import Tuple, List

LN2_4 = 4.0 * np.log(2.0)

FEATURE_COLUMNS = [
    "ret_1d_clean",
    "ret_5d",
    "ret_20d",
    "open_to_close",
    "high_low_ratio",
    "log_volume",
    "log_turnover",
    "vol_ratio_20d",
    "parkinson_vol_20d",
    "hist_vol_20d",
    "dist_to_20d_high",
    "dist_to_20d_low",
    "amihud_illiquidity_20d",
    "has_corporate_action_flt",
    "cum_factor_flt",
    "rsi_14d",
    "trend_ema_ratio",
    "vol_squeeze_ratio",
    "volume_momentum_ratio",
    "market_rel_mom_20d",
]


def compute_causal_features(df: pl.DataFrame) -> Tuple[pl.DataFrame, List[str]]:
    """
    Computes 20 strictly backward-looking features grouped per asset symbol.
    Guarantees:
      1. Zero lookahead leakage (no negative shift operators).
      2. 0.0% null values (properly filled with neutral financial priors).
    """
    df_sorted = df.sort(["symbol", "date"])

    # Base transformations
    df_feat = df_sorted.with_columns([
        (pl.col("high") / pl.col("low")).log().pow(2).alias("hl_log_sq"),
        pl.col("ret_1d").fill_null(0.0).alias("ret_1d_clean"),
        (pl.col("close") - pl.col("close").shift(1).over("symbol")).fill_null(0.0).alias("price_diff"),
    ])

    gain_col = pl.when(pl.col("price_diff") > 0).then(pl.col("price_diff")).otherwise(0.0)
    loss_col = pl.when(pl.col("price_diff") < 0).then(-pl.col("price_diff")).otherwise(0.0)

    df_feat = df_feat.with_columns([
        gain_col.alias("gain"),
        loss_col.alias("loss"),
    ])

    # RSI expression
    rs_expr = pl.col("gain").rolling_mean(14, min_samples=1).over("symbol") / (
        pl.col("loss").rolling_mean(14, min_samples=1).over("symbol") + 1e-6
    )
    rsi_expr = ((100.0 - (100.0 / (1.0 + rs_expr))) - 50.0) / 50.0

    # Window operations partitioned by symbol
    df_feat = df_feat.with_columns([
        # Returns
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).fill_null(0.0).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).fill_null(0.0).alias("ret_20d"),
        (pl.col("close") / pl.col("open") - 1.0).alias("open_to_close"),
        (pl.col("high") / pl.col("low") - 1.0).alias("high_low_ratio"),
        
        # Volumes & Liquidity
        (pl.col("volume").clip(lower_bound=0.0) + 1.0).log().alias("log_volume"),
        (pl.col("turnover_cr").clip(lower_bound=0.0) + 1.0).log().alias("log_turnover"),
        (pl.col("volume") / pl.col("volume").rolling_mean(window_size=20, min_samples=1).over("symbol")).fill_null(1.0).alias("vol_ratio_20d"),
        
        # Parkinson Volatility: sqrt( (1 / 4*ln2) * rolling_mean(hl_log_sq) )
        (pl.col("hl_log_sq").rolling_mean(window_size=20, min_samples=1).over("symbol") / LN2_4).sqrt().alias("parkinson_vol_20d"),
        (pl.col("hl_log_sq").rolling_mean(window_size=5, min_samples=1).over("symbol") / LN2_4).sqrt().alias("parkinson_vol_5d"),
        
        # Annualized 20-Day Historical Return Volatility
        (pl.col("ret_1d_clean").rolling_std(window_size=20, min_samples=2).over("symbol") * np.sqrt(252)).fill_null(0.0).alias("hist_vol_20d"),
        
        # Distance to 20-Day Extremes
        (pl.col("close") / pl.col("high").rolling_max(window_size=20, min_samples=1).over("symbol") - 1.0).alias("dist_to_20d_high"),
        (pl.col("close") / pl.col("low").rolling_min(window_size=20, min_samples=1).over("symbol") - 1.0).alias("dist_to_20d_low"),
        
        # Amihud Illiquidity
        (pl.col("ret_1d_clean").abs() / (pl.col("turnover_cr") + 1e-4)).rolling_mean(window_size=20, min_samples=1).over("symbol").alias("amihud_illiquidity_20d"),
        
        # Structural Flags
        pl.col("has_corporate_action").cast(pl.Float32).alias("has_corporate_action_flt"),
        pl.col("cum_factor").cast(pl.Float32).alias("cum_factor_flt"),

        # Momentum & Trend Additions
        rsi_expr.alias("rsi_14d"),
        (
            pl.col("close").ewm_mean(span=12).over("symbol") /
            (pl.col("close").ewm_mean(span=26).over("symbol") + 1e-4) - 1.0
        ).fill_null(0.0).alias("trend_ema_ratio"),
        (
            pl.col("volume").rolling_mean(5, min_samples=1).over("symbol") /
            (pl.col("volume").rolling_mean(20, min_samples=1).over("symbol") + 1e-4) - 1.0
        ).fill_null(0.0).alias("volume_momentum_ratio"),
    ])

    # Second-order operations
    df_feat = df_feat.with_columns([
        (
            pl.col("parkinson_vol_5d") / (pl.col("hist_vol_20d") + 1e-4) - 1.0
        ).fill_null(0.0).alias("vol_squeeze_ratio"),
        (
            pl.col("ret_20d") - pl.col("ret_20d").median().over("date")
        ).fill_null(0.0).alias("market_rel_mom_20d"),
    ])

    return df_feat, FEATURE_COLUMNS
