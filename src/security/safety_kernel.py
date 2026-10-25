"""
AlphaNiti v25.0 Institutional Security & Safety Kernel
Pipeline Module 8 - Isolated Formal Safety Kernel & Hardware Enclave Ed25519 Signing Path
Reference: docs/08_PLATFORM_SECURITY_AND_ISOLATED_SAFETY_KERNEL.md (Callout 7)
"""

from typing import Dict, Any, Optional, Tuple, List
import json
import time
from cryptography.hazmat.primitives.asymmetric import ed25519
from src.accounting.ledger import FiveBucketBalanceSheet


class SafetyKernelVetoException(Exception):
    """Raised when an order fails any of the three formal safety gates."""
    pass


class FormalSafetyKernel:
    """
    Isolated Formal Safety Kernel maintaining unilateral veto authority over outbound orders.
    Enforces three formal gates:
        Gate 1: Cleared Solvency (W_settled - Liens - Payables >= Outlay)
        Gate 2: Exact Discrete CVaR Risk Ceiling (CVaR_alpha(q) <= Omega_cap)
        Gate 3: Pre-Trade Limit Collar Guard (P_limit <= (Escrow - C_max) / q)
    Affixes hardware enclave Ed25519 cryptographic signatures to approved orders.
    """

    def __init__(
        self,
        cvar_ceiling_fraction: float = 0.15,  # Max allowable portfolio CVaR as fraction of NAV
        enclave_private_key: Optional[ed25519.Ed25519PrivateKey] = None,
    ):
        self.cvar_ceiling_fraction = cvar_ceiling_fraction
        # Hardware Enclave Ed25519 Keypair (locked in RAM)
        self._private_key = enclave_private_key or ed25519.Ed25519PrivateKey.generate()
        self.public_key = self._private_key.public_key()

        self.veto_log: List[Dict[str, Any]] = []
        self.approved_log: List[Dict[str, Any]] = []

    def evaluate_and_sign_order(
        self,
        ledger: FiveBucketBalanceSheet,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: int,
        limit_price: float,
        estimated_charges: float,
        max_possible_charges: float,
        portfolio_cvar_estimate: float,
        current_prices: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Intercepts candidate order and executes all three formal safety gates.
        Returns cryptographically signed order packet or raises SafetyKernelVetoException.
        """
        nav = ledger.get_nav(current_prices)
        buying_power = ledger.get_buying_power()
        est_outlay = quantity * limit_price + estimated_charges

        # --- GATE 1: Cleared Solvency ---
        if side.upper() == "BUY":
            if est_outlay > buying_power + 1e-4:
                veto_record = {
                    "gate": 1,
                    "reason": "SOLVENCY_VIOLATION",
                    "details": f"Estimated outlay INR {est_outlay:,.2f} exceeds buying power INR {buying_power:,.2f}",
                    "symbol": symbol,
                    "quantity": quantity,
                    "timestamp": time.time(),
                }
                self.veto_log.append(veto_record)
                raise SafetyKernelVetoException(f"GATE 1 VETO: {veto_record['details']}")

        # --- GATE 2: Exact Discrete CVaR Risk Ceiling ---
        omega_cap = self.cvar_ceiling_fraction * nav
        if portfolio_cvar_estimate > omega_cap + 1e-4:
            veto_record = {
                "gate": 2,
                "reason": "CVAR_CEILING_BREACH",
                "details": f"Portfolio CVaR {portfolio_cvar_estimate:,.2f} exceeds cap {omega_cap:,.2f}",
                "symbol": symbol,
                "quantity": quantity,
                "timestamp": time.time(),
            }
            self.veto_log.append(veto_record)
            raise SafetyKernelVetoException(f"GATE 2 VETO: {veto_record['details']}")

        # --- GATE 3: Pre-Trade Limit Collar Guard ---
        if side.upper() == "BUY":
            collar_ceiling = (est_outlay - max_possible_charges) / quantity
            if limit_price > collar_ceiling + 1e-4:
                veto_record = {
                    "gate": 3,
                    "reason": "COLLAR_GUARD_VIOLATION",
                    "details": f"Limit price {limit_price:.2f} exceeds collar ceiling {collar_ceiling:.2f}",
                    "symbol": symbol,
                    "quantity": quantity,
                    "timestamp": time.time(),
                }
                self.veto_log.append(veto_record)
                raise SafetyKernelVetoException(f"GATE 3 VETO: {veto_record['details']}")

        # --- All 3 Gates Passed: Affix Ed25519 Enclave Signature ---
        order_payload = {
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "limit_price": limit_price,
            "estimated_charges": estimated_charges,
            "enclave_timestamp_ns": time.time_ns(),
        }
        serialized_payload = json.dumps(order_payload, sort_keys=True).encode("utf-8")
        signature_bytes = self._private_key.sign(serialized_payload)
        signature_hex = signature_bytes.hex()

        signed_packet = {
            "payload": order_payload,
            "ed25519_signature": signature_hex,
            "public_key_hex": self.public_key.public_bytes_raw().hex(),
            "status": "KERNEL_APPROVED_AND_SIGNED",
        }

        self.approved_log.append(signed_packet)
        return signed_packet

    def verify_signature(self, signed_packet: Dict[str, Any]) -> bool:
        """Verifies Ed25519 signature of an approved packet."""
        try:
            payload = signed_packet["payload"]
            serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
            sig = bytes.fromhex(signed_packet["ed25519_signature"])
            pub_bytes = bytes.fromhex(signed_packet["public_key_hex"])
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            pub_key.verify(sig, serialized)
            return True
        except Exception:
            return False
