"""
Unit and Integration Tests for Conserved Five-Bucket Double-Entry Ledger
Reference: docs/06_ACCOUNTING_NAV_AND_DOUBLE_ENTRY_LEDGER.md (Callout 3)
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from src.accounting.ledger import FiveBucketBalanceSheet, DoubleEntryLedgerViolation


class TestDoubleEntryLedger(unittest.TestCase):
    def setUp(self):
        self.initial_capital = 1_000_000.0  # 10 Lakh INR
        self.ledger = FiveBucketBalanceSheet(initial_capital_inr=self.initial_capital)
        self.prices = {"RELIANCE": 2500.0, "TCS": 3500.0}

    def test_initial_state(self):
        """Verify initial balance sheet state, NAV, and buying power."""
        self.assertEqual(self.ledger.W_settled, self.initial_capital)
        self.assertEqual(self.ledger.W_escrow, 0.0)
        self.assertEqual(self.ledger.W_recv, 0.0)
        self.assertEqual(self.ledger.W_lien, 0.0)
        self.assertEqual(self.ledger.W_payable, 0.0)

        nav = self.ledger.get_nav(self.prices)
        bp = self.ledger.get_buying_power()
        self.assertEqual(nav, self.initial_capital)
        self.assertEqual(bp, self.initial_capital)

    def test_case_1_buy_dispatch_conservation(self):
        """Case 1: Buy Order Dispatch preserves exact NAV (Delta NAV = 0)."""
        nav_before = self.ledger.get_nav(self.prices)
        bp_before = self.ledger.get_buying_power()

        qty = 100
        limit_p = 2500.0
        est_fees = 350.0

        self.ledger.dispatch_buy_order("ORD_001", "RELIANCE", qty, limit_p, est_fees)

        nav_after = self.ledger.get_nav(self.prices)
        bp_after = self.ledger.get_buying_power()

        # Invariant 1: Delta NAV == 0
        self.assertAlmostEqual(nav_before, nav_after, places=4)
        # Buying power decreases by exactly quarantined escrow
        self.assertAlmostEqual(bp_before - bp_after, qty * limit_p + est_fees, places=4)
        # Escrow contains quarantined cash
        self.assertAlmostEqual(self.ledger.W_escrow, qty * limit_p + est_fees, places=4)

    def test_case_2_buy_fill_conservation(self):
        """Case 2: Buy Order Fill decreases NAV by exactly incurred charges (Delta NAV = -Charges)."""
        qty = 100
        limit_p = 2500.0
        est_fees = 500.0
        self.ledger.dispatch_buy_order("ORD_001", "RELIANCE", qty, limit_p, est_fees)

        nav_pre_fill = self.ledger.get_nav(self.prices)
        fill_p = 2490.0  # Favorable fill with slippage refund
        actual_fees = 420.0

        # Mark price at fill price
        current_prices = {"RELIANCE": fill_p}
        self.ledger.fill_buy_order("ORD_001", fill_p, actual_fees)

        nav_post_fill = self.ledger.get_nav(current_prices)

        # Invariant 2: Delta NAV == -actual_charges
        delta_nav = nav_post_fill - nav_pre_fill
        self.assertAlmostEqual(delta_nav, -actual_fees, places=4)
        # Escrow is fully released
        self.assertEqual(self.ledger.W_escrow, 0.0)
        # Custody position reflects new shares
        self.assertEqual(self.ledger.positions["RELIANCE"], 100)

    def test_case_3_sell_fill_conservation(self):
        """Case 3: Sell Order Fill results in Delta NAV = RealizedPnL - Charges."""
        # 1. Buy 100 shares at 2500
        self.ledger.dispatch_buy_order("ORD_001", "RELIANCE", 100, 2500.0, 500.0)
        self.ledger.fill_buy_order("ORD_001", 2500.0, 500.0)

        nav_pre_sell = self.ledger.get_nav({"RELIANCE": 2500.0})

        # 2. Sell at 2600 (+100 INR gain per share = 10,000 INR gross profit)
        sell_p = 2600.0
        actual_sell_fees = 380.0
        self.ledger.fill_sell_order("RELIANCE", 100, sell_p, actual_sell_fees)

        # Evaluation post-sell (no remaining custody shares)
        nav_post_sell = self.ledger.get_nav({"RELIANCE": sell_p})

        expected_realized_pnl = 100 * (2600.0 - 2500.0)  # 10,000 INR
        expected_delta_nav = expected_realized_pnl - actual_sell_fees  # 9,620 INR

        delta_nav = nav_post_sell - nav_pre_sell
        self.assertAlmostEqual(delta_nav, expected_delta_nav, places=4)
        # Receivables should contain net proceeds
        expected_receivables = 100 * sell_p - actual_sell_fees
        self.assertAlmostEqual(self.ledger.W_recv, expected_receivables, places=4)

    def test_case_4_t1_settlement_conservation(self):
        """Case 4: NSCCL T+1 Settlement clears receivables to cash (Delta NAV = 0)."""
        self.ledger.W_recv = 250_000.0
        nav_before = self.ledger.get_nav(self.prices)

        settled = self.ledger.settle_t1_batch()

        nav_after = self.ledger.get_nav(self.prices)
        self.assertEqual(settled, 250_000.0)
        self.assertEqual(self.ledger.W_recv, 0.0)
        self.assertAlmostEqual(nav_before, nav_after, places=4)

    def test_case_5_cash_dividend_conservation(self):
        """Case 5: Cash dividend with ex-date price drop preserves exact NAV (Delta NAV = 0)."""
        # Own 100 shares of TCS
        self.ledger.positions["TCS"] = 100
        p_cum = 3500.0
        dividend = 50.0  # Rs 50 per share
        p_ex = p_cum - dividend  # Ex-date theoretical price drop

        nav_before = self.ledger.get_nav({"TCS": p_cum})

        # Record dividend and mark ex-date price
        self.ledger.record_cash_dividend("TCS", dividend)
        nav_after = self.ledger.get_nav({"TCS": p_ex})

        # Delta NAV must be exactly 0
        self.assertAlmostEqual(nav_before, nav_after, places=4)

    def test_solvency_and_short_sale_guards(self):
        """Verify pre-trade solvency gate and short-sale rejection."""
        # 1. Attempt buy order exceeding buying power
        with self.assertRaises(DoubleEntryLedgerViolation):
            self.ledger.dispatch_buy_order("ORD_FAIL", "RELIANCE", 10_000, 2500.0, 1000.0)

        # 2. Attempt short sale
        with self.assertRaises(DoubleEntryLedgerViolation):
            self.ledger.fill_sell_order("TCS", 10, 3500.0, 50.0)


if __name__ == "__main__":
    unittest.main()
