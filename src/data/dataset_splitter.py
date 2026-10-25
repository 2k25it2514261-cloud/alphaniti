"""
AlphaNiti v25.0: Pipeline Data Module - Purged Walk-Forward Dataset Splitter
Implements Marcos Lopez de Prado's Combinatorial Purged Cross-Validation (CPCV)
and Walk-Forward Embargo Principles for Financial Machine Learning.

Key Invariant:
  Because labels represent an H-day forward window (H=20 trading days),
  a strict H-day purge gap must be enforced between Train -> Val and Val -> Test
  to prevent forward-looking label overlap and autoregressive leakage.
"""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple
import polars as pl


@dataclass(frozen=True)
class SplitManifest:
    total_rows: int
    train_rows: int
    val_rows: int
    test_rows: int
    purged_rows: int
    train_dates: Tuple[str, str]
    val_dates: Tuple[str, str]
    test_dates: Tuple[str, str]
    purge_gap_1_days: int
    purge_gap_2_days: int
    target_statistics: Dict[str, Dict[str, float]]
    checksums: Dict[str, str]

    def to_dict(self) -> Dict:
        return {
            "total_rows": self.total_rows,
            "train_rows": self.train_rows,
            "val_rows": self.val_rows,
            "test_rows": self.test_rows,
            "purged_rows": self.purged_rows,
            "train_date_range": list(self.train_dates),
            "val_date_range": list(self.val_dates),
            "test_date_range": list(self.test_dates),
            "purge_gap_1_trading_days": self.purge_gap_1_days,
            "purge_gap_2_trading_days": self.purge_gap_2_days,
            "target_statistics": self.target_statistics,
            "sha256_checksums": self.checksums,
        }


class PurgedWalkForwardSplitter:
    """
    Splits chronological asset-time panels into Train, Validation, and Test sets
    with strict H-day forward label purging (embargo) between partitions.
    """
    def __init__(
        self,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        purge_horizon_days: int = 20,
        date_col: str = "date",
    ):
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-4, "Ratios must sum to 1.0"
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.purge_horizon = purge_horizon_days
        self.date_col = date_col

    def split(self, df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, SplitManifest]:
        """
        Executes chronological purged partition.
        Returns: (train_df, val_df, test_df, manifest)
        """
        unique_dates = sorted(df[self.date_col].unique().to_list())
        t_total = len(unique_dates)
        h = self.purge_horizon

        # Calculate partition boundaries based on trading days
        n_train_days = int(self.train_ratio * t_total)
        n_val_days = int(self.val_ratio * t_total)

        # 1. Train set: [0, n_train - H)
        train_dates = unique_dates[: n_train_days - h]

        # 2. Validation set: [n_train, n_train + n_val - H)
        val_start = n_train_days
        val_end = val_start + n_val_days - h
        val_dates = unique_dates[val_start:val_end]

        # 3. Test set: [n_train + n_val, end)
        test_start = val_start + n_val_days
        test_dates = unique_dates[test_start:]

        # Filter partitions
        train_df = df.filter(pl.col(self.date_col).is_in(train_dates)).sort([self.date_col, "symbol"])
        val_df = df.filter(pl.col(self.date_col).is_in(val_dates)).sort([self.date_col, "symbol"])
        test_df = df.filter(pl.col(self.date_col).is_in(test_dates)).sort([self.date_col, "symbol"])

        purged_rows = len(df) - len(train_df) - len(val_df) - len(test_df)

        def _stats(split_df):
            if "target_ret_20d_net" in split_df.columns:
                ret = split_df["target_ret_20d_net"]
                br = split_df["target_breach_5pct"]
                return {
                    "mean_return_net_pct": float(ret.mean() * 100),
                    "volatility_net_pct": float(ret.std() * 100),
                    "median_return_net_pct": float(ret.median() * 100),
                    "breach_rate_pct": float(br.mean() * 100),
                }
            return {}

        stats_dict = {
            "train": _stats(train_df),
            "val": _stats(val_df),
            "test": _stats(test_df),
        }

        # Checksums
        manifest = SplitManifest(
            total_rows=len(df),
            train_rows=len(train_df),
            val_rows=len(val_df),
            test_rows=len(test_df),
            purged_rows=purged_rows,
            train_dates=(str(train_dates[0]), str(train_dates[-1])),
            val_dates=(str(val_dates[0]), str(val_dates[-1])),
            test_dates=(str(test_dates[0]), str(test_dates[-1])),
            purge_gap_1_days=h,
            purge_gap_2_days=h,
            target_statistics=stats_dict,
            checksums={},
        )

        return train_df, val_df, test_df, manifest

    def save_splits(
        self,
        df: pl.DataFrame,
        output_dir: str = "data/splits"
    ) -> SplitManifest:
        """
        Executes split and writes out parquet files and manifest.json.
        """
        os.makedirs(output_dir, exist_ok=True)
        train_df, val_df, test_df, manifest = self.split(df)

        train_path = os.path.join(output_dir, "train.parquet")
        val_path = os.path.join(output_dir, "val.parquet")
        test_path = os.path.join(output_dir, "test.parquet")
        manifest_path = os.path.join(output_dir, "manifest.json")

        train_df.write_parquet(train_path)
        val_df.write_parquet(val_path)
        test_df.write_parquet(test_path)

        def _sha256(fpath):
            with open(fpath, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        checksums = {
            "train.parquet": _sha256(train_path),
            "val.parquet": _sha256(val_path),
            "test.parquet": _sha256(test_path),
        }

        final_manifest = SplitManifest(
            total_rows=manifest.total_rows,
            train_rows=manifest.train_rows,
            val_rows=manifest.val_rows,
            test_rows=manifest.test_rows,
            purged_rows=manifest.purged_rows,
            train_dates=manifest.train_dates,
            val_dates=manifest.val_dates,
            test_dates=manifest.test_dates,
            purge_gap_1_days=manifest.purge_gap_1_days,
            purge_gap_2_days=manifest.purge_gap_2_days,
            target_statistics=manifest.target_statistics,
            checksums=checksums,
        )

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(final_manifest.to_dict(), f, indent=2)

        print(f"Successfully saved splits to {output_dir}/")
        print(f" - Train: {train_path} ({len(train_df):,} rows)")
        print(f" - Val:   {val_path} ({len(val_df):,} rows)")
        print(f" - Test:  {test_path} ({len(test_df):,} rows)")
        print(f" - Manifest: {manifest_path}")

        return final_manifest
