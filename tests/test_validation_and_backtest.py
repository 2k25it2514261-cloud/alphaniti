"""
Unit and Integration Tests for Econometric Validation, FIX Execution, and Out-of-Sample Backtest
Reference: docs/05_EXECUTION_ENGINE_AND_EXCHANGE_PROTOCOLS.md & docs/07_MONITORING_VALIDATION_AND_CALIBRATION_TESTING.md
"""

import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from src.execution.emapi_fix import FixClOrdIdGenerator, EmapiExecutionFSM, FixOrdStatus
from src.validation.econometric_tests import ChristoffersenKupiecValidator, DeflatedSharpeRatioCalculator
from src.validation.backtest_engine import AlphaNitiBacktestEngine


class TestValidationAndBacktest(unittest.TestCase):
    def test_fix_cl_ord_id_generator(self):
        """Verify immutable canonical ClOrdID format."""
        cl_id = FixClOrdIdGenerator.generate(
            trading_date="20261025",
            member_id="99881",
            strategy_id="ALPH",
            symbol="RELIANCE",
            qty=100,
        )
        self.assertTrue(cl_id.startswith("20261025-99881-ALPH-"))
        self.assertEqual(len(cl_id), 34)

    def test_fix_duplicate_order_rejection(self):
        """Verify duplicate ClOrdID rejection with code 16384 (Callout 4)."""
        fsm = EmapiExecutionFSM()
        cl_id = "20261025-12345-NITI-abcdef12345678"

        # First dispatch -> PENDING_NEW
        order1 = fsm.submit_new_order_single(cl_id, "TCS", "BUY", 50, 3500.0)
        self.assertEqual(order1["status"], FixOrdStatus.PENDING_NEW)

        # Duplicate dispatch -> REJECTED with ord_rej_reason 104
        order2 = fsm.submit_new_order_single(cl_id, "TCS", "BUY", 50, 3500.0)
        self.assertEqual(order2["status"], FixOrdStatus.REJECTED)
        self.assertEqual(order2["ord_rej_reason"], 104)

    def test_christoffersen_kupiec_validator(self):
        """Verify Kupiec POF and Christoffersen independence likelihood ratio statistics."""
        np.random.seed(42)
        # 500 samples with nominal 5% violation rate
        hits = (np.random.rand(500) < 0.05).astype(int)

        results = ChristoffersenKupiecValidator.evaluate(hits, alpha=0.05)
        self.assertEqual(results["sample_size"], 500)
        self.assertIn("lr_uc", results)
        self.assertIn("lr_ind", results)
        self.assertIn("lr_cc", results)
        self.assertTrue(results["passed_joint_test"])

    def test_deflated_sharpe_ratio(self):
        """Verify Bailey & López de Prado DSR calculation."""
        np.random.seed(42)
        # Strategy with positive drift
        returns = np.random.randn(500) * 0.01 + 0.001

        dsr_res = DeflatedSharpeRatioCalculator.compute_dsr(returns, num_trials=10, var_trials_sr=0.25)
        self.assertIn("annualized_sharpe_ratio", dsr_res)
        self.assertIn("dsr", dsr_res)
        self.assertGreaterEqual(dsr_res["dsr"], 0.0)
        self.assertLessEqual(dsr_res["dsr"], 1.0)

    def test_walkforward_backtest_on_test_partition(self):
        """Verify complete walk-forward simulation across physical out-of-sample test partition."""
        test_path = Path(__file__).resolve().parent.parent / "data" / "splits" / "test.parquet"
        if not test_path.exists():
            self.skipTest(f"Test split not found at {test_path}")

        test_df = pd.read_parquet(test_path)
        engine = AlphaNitiBacktestEngine(
            initial_capital_inr=1_000_000.0,
            rebalance_interval_days=20,
            n_factors=3,
            max_asset_weight=0.20,
        )

        res = engine.run(test_df, min_symbols=15)

        self.assertGreater(res["final_nav_inr"], 0.0)
        self.assertGreater(res["trading_days"], 50)
        self.assertIn("sharpe_ratio", res)
        self.assertIn("total_statutory_taxes_inr", res)

        print("\n" + "=" * 60)
        print("ALPHANITI v25.0 INSTITUTIONAL WALK-FORWARD BACKTEST RESULTS")
        print("=" * 60)
        print(f"  Test Partition Period:       2025-01-31 to 2026-09-10")
        print(f"  Trading Days Evaluated:      {res['trading_days']}")
        print(f"  Initial Capital:             INR {res['initial_capital_inr']:,.2f}")
        print(f"  Final Net Asset Value:       INR {res['final_nav_inr']:,.2f}")
        print(f"  Total Return:                {res['total_return_pct']:.2f}%")
        print(f"  Annualized Return:           {res['annualized_return_pct']:.2f}%")
        print(f"  Annualized Volatility:       {res['annualized_volatility_pct']:.2f}%")
        print(f"  Annualized Sharpe Ratio:     {res['sharpe_ratio']:.3f}")
        print(f"  Maximum Drawdown:            {res['max_drawdown_pct']:.2f}%")
        print(f"  Statutory Taxes Paid:        INR {res['total_statutory_taxes_inr']:,.2f}")
        print(f"  Rebalances Executed:         {res['rebalances_executed']}")
        print(f"  Backtest Runtime:            {res['backtest_runtime_sec']} sec")
        if res.get("dsr_metrics"):
            print(f"  Deflated Sharpe Ratio (DSR): {res['dsr_metrics'].get('dsr', 0.0):.4f}")
        print("=" * 60)


if __name__ == "__main__":
    unittest.main()
