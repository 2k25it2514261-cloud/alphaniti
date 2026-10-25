"""
Unit and Integration Tests for Portfolio Optimization & Discrete Integer Sizer
Reference: docs/04_PORTFOLIO_OPTIMIZATION_AND_DISCRETE_SIZING.md
"""

import sys
import unittest
from pathlib import Path
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.portfolio.integer_sizer import LatticeBranchAndBoundSolver


class TestPortfolioSizer(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        self.cost_model = IndianStatutoryCostModel()
        self.N = 4
        self.S = 50
        self.prices = np.array([2500.0, 1800.0, 3200.0, 950.0])  # RELIANCE, HDFCBANK, TCS, INFY
        self.expected_returns = np.array([0.08, 0.06, 0.07, 0.05])
        self.scenarios = np.random.randn(self.S, self.N) * 0.02
        self.cash_budget = 500_000.0  # 5 Lakh INR
        self.nav = 500_000.0
        self.asset_names = ["RELIANCE", "HDFCBANK", "TCS", "INFY"]

    def test_indian_statutory_costs(self):
        """Verify STT, Stamp Duty, GST, and non-convex brokerage fee calculation."""
        shares = 100
        price = 2000.0
        turnover = shares * price  # 200,000 INR

        cost = self.cost_model.compute_cost(
            shares=shares,
            price=price,
            spread=0.10,
            adv=500_000.0,
            volatility=0.015,
            is_buy=True,
        )

        # STT = 0.1% = 200 INR
        # Stamp Duty = 0.015% = 30 INR
        # Brokerage = max(20, 0.0003 * 200000) = 60 INR
        # GST = 18% of brokerage + exchange charges ~ 11-12 INR
        # Total statutory + friction should be strictly positive and > STT (200 INR)
        self.assertGreater(cost, 250.0)
        self.assertLess(cost, 1000.0)

    def test_lower_convex_envelope_underestimation(self):
        """Verify Cost^cvx(q) <= Cost(q) across entire domain [0, u_i] (Theorem 1)."""
        price = 1500.0
        u_max = 500.0

        slope = self.cost_model.lower_convex_envelope_slope(price=price, u_upper_bound=u_max)

        test_points = np.linspace(1, u_max, 25)
        for q in test_points:
            exact_cost = self.cost_model.compute_cost(shares=q, price=price)
            relaxed_cost = slope * q
            self.assertLessEqual(
                relaxed_cost,
                exact_cost + 1e-4,
                f"Convex envelope must underestimate exact cost at q={q}",
            )

    def test_branch_and_bound_lattice_solver(self):
        """Verify integer shares, cash solvency constraint, and cryptographic certificate."""
        solver = LatticeBranchAndBoundSolver(
            cost_model=self.cost_model,
            alpha_cvar=0.95,
            max_asset_weight=0.35,
            max_nodes=300,
            time_limit_sec=5.0,
        )

        cert = solver.solve(
            prices=self.prices,
            expected_returns=self.expected_returns,
            scenarios=self.scenarios,
            cash_budget=self.cash_budget,
            nav=self.nav,
            asset_names=self.asset_names,
        )

        # 1. Output verification
        self.assertEqual(cert["certificate_type"], "TOTAL_OBJECTIVE_GLOBAL_OPTIMALITY")
        self.assertTrue(cert["constraints_certified"]["cleared_cash_solvency_verified"])
        self.assertTrue(cert["search_tree_merkle_root"].startswith("0x"))

        # 2. Check solution integrality and budget adherence
        total_expenditure = 0.0
        for name, shares in cert["solution_vector"].items():
            self.assertIsInstance(shares, int)
            self.assertGreaterEqual(shares, 0)
            idx = self.asset_names.index(name)
            total_expenditure += shares * self.prices[idx]

        self.assertLessEqual(total_expenditure, self.cash_budget + 1e-4)

        print("\nBranch-and-Bound Integer Allocation Certificate:")
        print(f"  Status: {cert['status']}")
        print(f"  Allocation: {cert['solution_vector']}")
        print(f"  Total Expenditure: INR {total_expenditure:,.2f} / {self.cash_budget:,.2f}")
        print(f"  Nodes Explored: {cert['optimality_metrics']['nodes_explored']}")
        print(f"  Nodes Pruned: {cert['optimality_metrics']['nodes_pruned']}")
        print(f"  Solve Time: {cert['optimality_metrics']['solve_time_ms']} ms")
        print(f"  Merkle Root: {cert['search_tree_merkle_root']}")


if __name__ == "__main__":
    unittest.main()
