"""
AlphaNiti v25.0: Pipeline Module 1 - Verification & Test Suite
Covers:
  1. Five-Timestamp Ingestion & Monotonicity Axiom
  2. SHA-256 Cryptographic Record Integrity
  3. Causal Filtration Operator Invariants (< and <=)
  4. Canonical Corporate Action Multipliers & Lookahead Barrier
  5. Robust Scale & Outlier Winsorization (MAD)
  6. Integration Test on Production Combined Parquet
"""

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import datetime, date, timezone, timedelta
import numpy as np
import polars as pl
import unittest

from src.data.temporal_model import (
    FiveTimestamp,
    ObservationRecord,
    IngestionMonotonicityViolation,
    QuarantineLedger,
    create_observation,
    causal_filtration_operator,
    causal_filtration_polars,
)

from src.data.corporate_actions import (
    ActionType,
    CorporateActionEvent,
    compute_cumulative_adjustment_factor,
    apply_causal_corporate_actions,
)

from src.data.robust_scaler import (
    RobustMADScaler,
)


class TestPipelineModule1(unittest.TestCase):

    def setUp(self):
        self.t0 = datetime(2026, 4, 1, 9, 15, 0, tzinfo=timezone.utc)
        self.t_event = self.t0
        self.t_pub = self.t0 + timedelta(milliseconds=10)
        self.t_vendor = self.t0 + timedelta(milliseconds=20)
        self.t_ingest = self.t0 + timedelta(milliseconds=25)
        self.t_effective = self.t0

    def test_01_monotonicity_valid(self):
        """Valid timestamps should pass and generate SHA-256 hash."""
        ts = FiveTimestamp(
            tau_event=self.t_event,
            tau_pub=self.t_pub,
            tau_vendor=self.t_vendor,
            tau_ingest=self.t_ingest,
            tau_effective=self.t_effective
        )
        rec = create_observation("RELIANCE", "close", 2850.50, ts)
        self.assertIsNotNone(rec)
        self.assertTrue(rec.verify_integrity())
        self.assertEqual(len(rec.sha256_hash), 64)

    def test_02_monotonicity_violation_and_quarantine(self):
        """Violation of tau_event <= tau_pub <= tau_vendor <= tau_ingest must trigger quarantine."""
        bad_ledger = QuarantineLedger()
        # Create non-monotonic timestamp: tau_pub < tau_event
        ts_bad = FiveTimestamp(
            tau_event=self.t_pub,
            tau_pub=self.t_event, # Inverted!
            tau_vendor=self.t_vendor,
            tau_ingest=self.t_ingest,
            tau_effective=self.t_effective
        )
        rec = create_observation("INFY", "close", 1820.00, ts_bad, quarantine_ledger=bad_ledger)
        self.assertIsNone(rec)
        self.assertEqual(len(bad_ledger.quarantined_records), 1)
        self.assertEqual(bad_ledger.quarantined_records[0]["entity_id"], "INFY")
        self.assertIn("Monotonicity Violation", bad_ledger.quarantined_records[0]["reason"])

    def test_03_causal_filtration_operator(self):
        """
        F(t_decision) = { X in D | max(tau_pub, tau_vendor, tau_ingest) < t_decision AND tau_effective <= t_decision }
        """
        t_decision = datetime(2026, 4, 1, 15, 30, 0, tzinfo=timezone.utc)
        
        # 1. Past observation (Should PASS)
        ts_past = FiveTimestamp(
            tau_event=t_decision - timedelta(minutes=5),
            tau_pub=t_decision - timedelta(minutes=4),
            tau_vendor=t_decision - timedelta(minutes=3),
            tau_ingest=t_decision - timedelta(minutes=2),
            tau_effective=t_decision - timedelta(hours=1)
        )
        r_past = create_observation("TCS", "price", 3500.0, ts_past)

        # 2. Exact millisecond observation (Must be EXCLUDED by strict inequality <)
        ts_exact = FiveTimestamp(
            tau_event=t_decision - timedelta(seconds=1),
            tau_pub=t_decision - timedelta(seconds=1),
            tau_vendor=t_decision,
            tau_ingest=t_decision, # Ingestion at decision instant
            tau_effective=t_decision
        )
        r_exact = create_observation("TCS", "price", 3505.0, ts_exact)

        # 3. Future effective date (Must be EXCLUDED by tau_effective <= t_decision)
        ts_future_eff = FiveTimestamp(
            tau_event=t_decision - timedelta(days=1),
            tau_pub=t_decision - timedelta(days=1),
            tau_vendor=t_decision - timedelta(days=1),
            tau_ingest=t_decision - timedelta(days=1),
            tau_effective=t_decision + timedelta(days=1) # Ex-date tomorrow!
        )
        r_future_eff = create_observation("TCS", "split", 0.1, ts_future_eff)

        filtered = causal_filtration_operator([r_past, r_exact, r_future_eff], t_decision)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].value, 3500.0)

    def test_04_canonical_corporate_actions(self):
        """Verifies exact canonical mathematical formulas for corporate actions."""
        # 1. 10:1 Stock Split (A=1, B=10 => f = 0.1)
        ev_split = CorporateActionEvent(
            symbol="TATASTEEL", ex_date=date(2022, 7, 28),
            action_type=ActionType.SPLIT, A=1, B=10
        )
        self.assertAlmostEqual(ev_split.compute_multiplier(), 0.1, places=6)

        # 2. 1:1 Bonus Issue (A=1, B=1 => f = 0.5)
        ev_bonus = CorporateActionEvent(
            symbol="RELIANCE", ex_date=date(2024, 10, 28),
            action_type=ActionType.BONUS, A=1, B=1
        )
        self.assertAlmostEqual(ev_bonus.compute_multiplier(), 0.5, places=6)

        # 3. Rights Offering (1:4 at INR 50 with cum-price INR 100)
        # P_ex_theo = (4*100 + 1*50) / 5 = 450 / 5 = 90. f = 90 / 100 = 0.90
        ev_rights = CorporateActionEvent(
            symbol="BHARTIARTL", ex_date=date(2021, 10, 5),
            action_type=ActionType.RIGHTS, A=4, B=1, p_cum=100.0, p_rights=50.0
        )
        self.assertAlmostEqual(ev_rights.compute_multiplier(), 0.90, places=6)

        # 4. Cash Dividend (D = INR 10 on P_cum = INR 200 => f = 1 - 10/200 = 0.95)
        ev_div = CorporateActionEvent(
            symbol="INFY", ex_date=date(2023, 6, 2),
            action_type=ActionType.DIVIDEND, div_amount=10.0, p_cum=200.0
        )
        self.assertAlmostEqual(ev_div.compute_multiplier(), 0.95, places=6)

    def test_05_cumulative_factor_lookahead_barrier(self):
        """
        Verifies c_i(t | t_decision) = prod_{k: t < t_k <= t_decision} f_{i,k}
        Events after t_decision MUST NOT affect history!
        """
        ev1 = CorporateActionEvent(
            symbol="XYZ", ex_date=date(2023, 6, 1),
            action_type=ActionType.SPLIT, A=1, B=2 # f = 0.5
        )
        ev2 = CorporateActionEvent(
            symbol="XYZ", ex_date=date(2023, 12, 1),
            action_type=ActionType.BONUS, A=1, B=1 # f = 0.5
        )
        events = [ev1, ev2]

        # Case A: Decision date in July 2023 (ev1 has occurred, ev2 has NOT)
        # Factor for May 2023: only ev1 applies => c = 0.5
        c_july = compute_cumulative_adjustment_factor(events, history_date=date(2023, 5, 1), decision_date=date(2023, 7, 1))
        self.assertAlmostEqual(c_july, 0.5, places=6)

        # Case B: Decision date in Jan 2024 (both ev1 and ev2 have occurred)
        # Factor for May 2023: both ev1 and ev2 apply => c = 0.5 * 0.5 = 0.25
        c_jan = compute_cumulative_adjustment_factor(events, history_date=date(2023, 5, 1), decision_date=date(2024, 1, 1))
        self.assertAlmostEqual(c_jan, 0.25, places=6)

        # Invariant: If history is after ev1, only ev2 applies
        c_post_ev1 = compute_cumulative_adjustment_factor(events, history_date=date(2023, 7, 1), decision_date=date(2024, 1, 1))
        self.assertAlmostEqual(c_post_ev1, 0.5, places=6)

    def test_06_robust_mad_scaler(self):
        """
        Tests Robust Scale & Outlier Winsorization:
          Median_i = median(x_i)
          MAD_i = 1.4826 * median(|x_i - Median_i|)
          z_{i,t} = (x_{i,t} - Median_i) / max(MAD_i, 1e-6)
          z_tilde_{i,t} = sign(z_{i,t}) * min(|z_{i,t}|, 4.5)
        """
        # Synthetic data with extreme fat-tail outliers
        np.random.seed(42)
        clean = np.array([10.0, 11.0, 12.0, 13.0, 14.0]) # Median = 12, abs_dev = [2, 1, 0, 1, 2], median(abs_dev) = 1.0 => MAD = 1.4826
        scaler = RobustMADScaler(c_clip=4.5)
        scaler.fit(clean.reshape(-1, 1))

        self.assertAlmostEqual(scaler.medians_[0], 12.0, places=4)
        self.assertAlmostEqual(scaler.mads_[0], 1.4826, places=4)

        # Test outlier clipping
        outliers = np.array([12.0, 1000.0, -1000.0]).reshape(-1, 1)
        z = scaler.transform(outliers)
        
        self.assertAlmostEqual(z[0, 0], 0.0, places=4)
        self.assertEqual(z[1, 0], 4.5)  # Clipped at +4.5
        self.assertEqual(z[2, 0], -4.5) # Clipped at -4.5

    def test_07_production_dataset_integration(self):
        """Tests the full pipeline on a slice of the real master dataset."""
        parquet_path = r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\kaggle_out\alphaniti_master_combined_dataset.parquet"
        if os.path.exists(parquet_path):
            df = pl.read_parquet(parquet_path).filter(pl.col("symbol") == "RELIANCE").sort("date")
            self.assertGreater(len(df), 1000)

            # Fit RobustMADScaler on returns and volume
            sub = df.select(["ret_1d", "volume", "turnover_cr"]).with_columns(pl.col("ret_1d").fill_null(0.0))
            scaler = RobustMADScaler(c_clip=4.5)
            z_feats = scaler.fit_transform(sub)

            self.assertFalse(np.isnan(z_feats).any())
            self.assertTrue((np.abs(z_feats) <= 4.5).all())


if __name__ == "__main__":
    unittest.main()
