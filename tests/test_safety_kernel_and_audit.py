"""
Unit and Integration Tests for Formal Safety Kernel & Governance Audit Ledger
Reference: docs/08_PLATFORM_SECURITY_AND_ISOLATED_SAFETY_KERNEL.md & docs/09_RISK_REPORTING_AND_REGULATORY_AUDIT.md
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from src.accounting.ledger import FiveBucketBalanceSheet
from src.security.safety_kernel import FormalSafetyKernel, SafetyKernelVetoException, GateStatus, ExecutionDecision, authorize_order
from src.governance.audit_ledger import SebiOtrSurveillanceMonitor, GovernanceAuditLedger


class TestSafetyKernelAndAudit(unittest.TestCase):
    def setUp(self):
        self.ledger = FiveBucketBalanceSheet(initial_capital_inr=500_000.0)
        self.kernel = FormalSafetyKernel(cvar_ceiling_fraction=0.10)
        self.prices = {"RELIANCE": 2500.0}

    def test_gate_1_solvency_veto(self):
        """Gate 1: Rejects order exceeding available buying power."""
        with self.assertRaises(SafetyKernelVetoException) as ctx:
            self.kernel.evaluate_and_sign_order(
                ledger=self.ledger,
                symbol="RELIANCE",
                side="BUY",
                quantity=300,  # 300 * 2500 = 750,000 INR > 500,000 INR
                limit_price=2500.0,
                estimated_charges=1000.0,
                max_possible_charges=1200.0,
                portfolio_cvar_estimate=10_000.0,
                current_prices=self.prices,
            )
        self.assertIn("GATE 1 VETO", str(ctx.exception))

    def test_gate_2_cvar_ceiling_veto(self):
        """Gate 2: Rejects order breaching CVaR risk ceiling (10% of NAV = 50,000 INR)."""
        with self.assertRaises(SafetyKernelVetoException) as ctx:
            self.kernel.evaluate_and_sign_order(
                ledger=self.ledger,
                symbol="RELIANCE",
                side="BUY",
                quantity=50,
                limit_price=2500.0,
                estimated_charges=200.0,
                max_possible_charges=300.0,
                portfolio_cvar_estimate=65_000.0,  # Exceeds 50,000 INR ceiling
                current_prices=self.prices,
            )
        self.assertIn("GATE 2 VETO", str(ctx.exception))

    def test_gate_3_collar_guard_veto(self):
        """Gate 3: Rejects order where limit price exceeds collar ceiling."""
        with self.assertRaises(SafetyKernelVetoException) as ctx:
            self.kernel.evaluate_and_sign_order(
                ledger=self.ledger,
                symbol="RELIANCE",
                side="BUY",
                quantity=10,
                limit_price=2600.0,  # 2600 exceeds collar based on estimated charges
                estimated_charges=100.0,
                max_possible_charges=500.0,  # High max fee reduces collar ceiling
                portfolio_cvar_estimate=5_000.0,
                current_prices=self.prices,
            )
        self.assertIn("GATE 3 VETO", str(ctx.exception))

    def test_enclave_ed25519_signature_and_verification(self):
        """Verify successful 3-gate passage produces valid Ed25519 signature."""
        signed_pkt = self.kernel.evaluate_and_sign_order(
            ledger=self.ledger,
            symbol="RELIANCE",
            side="BUY",
            quantity=50,  # 125,000 INR
            limit_price=2500.0,
            estimated_charges=250.0,
            max_possible_charges=250.0,
            portfolio_cvar_estimate=15_000.0,
            current_prices=self.prices,
        )

        self.assertEqual(signed_pkt["status"], "KERNEL_APPROVED_AND_SIGNED")
        self.assertTrue(self.kernel.verify_signature(signed_pkt))

        # Tampering with payload fails verification
        tampered = dict(signed_pkt)
        tampered["payload"] = dict(signed_pkt["payload"])
        tampered["payload"]["quantity"] = 500  # Tampered
        self.assertFalse(self.kernel.verify_signature(tampered))

    def test_sebi_otr_surveillance(self):
        """Verify SEBI Order-to-Trade Ratio tracking, throttling, and penalty tiers."""
        monitor = SebiOtrSurveillanceMonitor(throttle_threshold=40.0)

        # 10 trades, 450 modifications, 50 cancellations -> (450 + 50) / 10 = 50.0 (Tier 0)
        for _ in range(10):
            monitor.record_event("RELIANCE", "TRADE")
        for _ in range(450):
            monitor.record_event("RELIANCE", "MODIFICATION")
        for _ in range(50):
            monitor.record_event("RELIANCE", "CANCELLATION")

        self.assertEqual(monitor.compute_otr("RELIANCE"), 50.0)
        self.assertEqual(monitor.calculate_penalty_inr("RELIANCE"), 0.0)
        self.assertTrue(monitor.is_throttling_required("RELIANCE"))

        # Add 100 more cancellations -> OTR = 600 / 10 = 60.0 (Tier 1: Rs 0.01 per excess order)
        for _ in range(100):
            monitor.record_event("RELIANCE", "CANCELLATION")

        self.assertEqual(monitor.compute_otr("RELIANCE"), 60.0)
        # Excess orders over 50 * 10 = 500 is 100 orders -> 100 * 0.01 = Rs 1.00
        self.assertAlmostEqual(monitor.calculate_penalty_inr("RELIANCE"), 1.00, places=2)

    def test_governance_audit_ledger_merkle_chain(self):
        """Verify append-only cryptographic block chain and integrity verification."""
        ledger = GovernanceAuditLedger()

        # Block 0
        b0 = ledger.append_block(
            decision_timestamp="2026-10-25T09:15:00Z",
            causal_data_root="0xdata0",
            allocation_vector={"RELIANCE": 50, "TCS": 30},
            optimality_certificate={"optimality_metrics": {"nodes_explored": 10}},
            enclave_signature="0xsig0",
        )
        # Block 1
        b1 = ledger.append_block(
            decision_timestamp="2026-10-25T09:30:00Z",
            causal_data_root="0xdata1",
            allocation_vector={"RELIANCE": 40, "TCS": 35},
            optimality_certificate={"optimality_metrics": {"nodes_explored": 12}},
            enclave_signature="0xsig1",
        )

        self.assertEqual(b1.prev_block_hash, b0.block_hash)
        self.assertTrue(ledger.verify_chain_integrity())



    def test_authorize_order_valid(self):
        """Verify valid ALLOW decision with compliant score passes authorization."""
        dec = ExecutionDecision(
            symbol="INFY",
            side="BUY",
            quantity=25,
            score=76.5,
            breach_probability=0.18,
            gate_status=GateStatus.ALLOW,
            reason="Passed all hurdles",
            tier=1,
        )
        is_auth, msg = authorize_order(dec, max_breach_probability=0.35, min_score=50.0)
        self.assertTrue(is_auth)
        self.assertIn("AUTHORIZATION_GRANTED", msg)

    def test_authorize_order_blocked_gate(self):
        """Verify GateStatus.BLOCK unconditionally vetoes order execution."""
        dec = ExecutionDecision(
            symbol="SBIN",
            side="BUY",
            quantity=50,
            score=45.0,
            breach_probability=0.28,
            gate_status=GateStatus.BLOCK,
            reason="Decision score is AVOID",
            tier=2,
        )
        is_auth, msg = authorize_order(dec)
        self.assertFalse(is_auth)
        self.assertIn("AUTHORIZATION_DENIED: Gate status is BLOCK", msg)

    def test_authorize_order_below_hurdle_score(self):
        """Verify score below 50.0 hurdle is rejected even if gate_status was mistakenly ALLOW."""
        dec = ExecutionDecision(
            symbol="TCS",
            side="BUY",
            quantity=10,
            score=48.2,
            breach_probability=0.20,
            gate_status=GateStatus.ALLOW,
            reason="Mistakenly marked ALLOW",
            tier=1,
        )
        is_auth, msg = authorize_order(dec, min_score=50.0)
        self.assertFalse(is_auth)
        self.assertIn("below minimum hurdle", msg)

    def test_authorize_order_excessive_breach_probability(self):
        """Verify breach probability > 35% is rejected."""
        dec = ExecutionDecision(
            symbol="TATAMOTORS",
            side="BUY",
            quantity=40,
            score=65.0,
            breach_probability=0.42,
            gate_status=GateStatus.ALLOW,
            reason="High score but high crash risk",
            tier=1,
        )
        is_auth, msg = authorize_order(dec, max_breach_probability=0.35)
        self.assertFalse(is_auth)
        self.assertIn("exceeds tolerance", msg)

    def test_authorize_order_tier_3_cash_preservation_veto(self):
        """Verify Tier 3 Cash Preservation regime vetoes all equity purchases."""
        dec = ExecutionDecision(
            symbol="RELIANCE",
            side="BUY",
            quantity=20,
            score=82.0,
            breach_probability=0.15,
            gate_status=GateStatus.ALLOW,
            reason="High score",
            tier=3,
        )
        is_auth, msg = authorize_order(dec)
        self.assertFalse(is_auth)
        self.assertIn("Macro Regime is Tier 3 (Cash Preservation Veto)", msg)


if __name__ == "__main__":
    unittest.main()
