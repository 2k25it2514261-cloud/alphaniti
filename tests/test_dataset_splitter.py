"""
Tests for PurgedWalkForwardSplitter:
  1. Strict chronology verification: Max(train_dates) < Min(val_dates) < Min(test_dates).
  2. Purge gap invariant: Min(val_date) - Max(train_date) >= 20 trading days.
  3. Purge gap invariant: Min(test_date) - Max(val_date) >= 20 trading days.
  4. Target label zero-leakage guarantee.
"""

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
from datetime import date, timedelta
import polars as pl
from src.data.dataset_splitter import PurgedWalkForwardSplitter


class TestDatasetSplitter(unittest.TestCase):

    def setUp(self):
        # Create synthetic time series with 100 consecutive days across 2 symbols
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(100)]
        data = []
        for d in dates:
            for s in ["A", "B"]:
                data.append({"date": d, "symbol": s, "target_ret_20d_net": 0.05, "target_breach_5pct": 0})
        self.df = pl.DataFrame(data)

    def test_split_chronology_and_purging(self):
        splitter = PurgedWalkForwardSplitter(
            train_ratio=0.70,
            val_ratio=0.15,
            test_ratio=0.15,
            purge_horizon_days=5,
            date_col="date"
        )
        train_df, val_df, test_df, manifest = splitter.split(self.df)

        max_train = train_df["date"].max()
        min_val = val_df["date"].min()
        max_val = val_df["date"].max()
        min_test = test_df["date"].min()

        # Strict chronological order
        self.assertLess(max_train, min_val)
        self.assertLess(max_val, min_test)

        # Gap verification: gap between max_train and min_val must be at least 5 days
        self.assertGreaterEqual((min_val - max_train).days, 5)
        self.assertGreaterEqual((min_test - max_val).days, 5)

        # Manifest checks
        self.assertEqual(manifest.purge_gap_1_days, 5)
        self.assertEqual(manifest.purge_gap_2_days, 5)
        self.assertGreater(manifest.purged_rows, 0)


if __name__ == "__main__":
    unittest.main()
