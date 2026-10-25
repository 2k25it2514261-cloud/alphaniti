"""
AlphaNiti v25.0 Institutional Risk Reporting & Regulatory Audit
Pipeline Module 9 - SEBI OTR Surveillance & Cryptographic Merkle Decision Blocks
Reference: docs/09_RISK_REPORTING_AND_REGULATORY_AUDIT.md
"""

from typing import Dict, Any, List, Optional
import hashlib
import json
import time


class SebiOtrSurveillanceMonitor:
    """
    SEBI Algorithmic Trading Order-to-Trade Ratio (OTR) Compliance Monitor:
        OTR_i = (Modifications_i + Cancellations_i) / max(1, ExecutedTrades_i)
    Imposes escalating SEBI penalty tiers and triggers passive order throttling when OTR > 40.
    """

    def __init__(self, throttle_threshold: float = 40.0):
        self.throttle_threshold = throttle_threshold
        self.stats: Dict[str, Dict[str, int]] = {}

    def record_event(self, symbol: str, event_type: str):
        """
        Record order event: 'MODIFICATION', 'CANCELLATION', or 'TRADE'.
        """
        if symbol not in self.stats:
            self.stats[symbol] = {"modifications": 0, "cancellations": 0, "trades": 0}

        if event_type.upper() == "MODIFICATION":
            self.stats[symbol]["modifications"] += 1
        elif event_type.upper() == "CANCELLATION":
            self.stats[symbol]["cancellations"] += 1
        elif event_type.upper() == "TRADE":
            self.stats[symbol]["trades"] += 1

    def compute_otr(self, symbol: str) -> float:
        """Compute current OTR for symbol."""
        if symbol not in self.stats:
            return 0.0
        data = self.stats[symbol]
        numerator = data["modifications"] + data["cancellations"]
        denominator = max(1, data["trades"])
        return float(numerator / denominator)

    def calculate_penalty_inr(self, symbol: str) -> float:
        """
        Calculate escalating SEBI penalty tier:
            OTR <= 50: 0
            50 < OTR <= 100: Rs 0.01 per excess order
            100 < OTR <= 250: Rs 0.05 per excess order
            OTR > 250: Rs 0.10 per excess order + throttling
        """
        otr = self.compute_otr(symbol)
        if otr <= 50.0:
            return 0.0

        data = self.stats[symbol]
        trades = max(1, data["trades"])
        excess_orders = (data["modifications"] + data["cancellations"]) - (50 * trades)

        if otr <= 100.0:
            return float(excess_orders * 0.01)
        elif otr <= 250.0:
            return float(excess_orders * 0.05)
        else:
            return float(excess_orders * 0.10)

    def is_throttling_required(self, symbol: str) -> bool:
        """Check if passive order throttling should be engaged (OTR > 40)."""
        return self.compute_otr(symbol) > self.throttle_threshold


class MerkleAuditBlock:
    """
    Immutable Cryptographic Audit Block committing rebalancing decisions to a SHA-256 chain:
        Block_k = (k, t_decision, PrevHash, DataRoot, q*, Cert_optimality, Sig_enclave)
    """

    def __init__(
        self,
        block_height: int,
        prev_block_hash: str,
        decision_timestamp: str,
        causal_data_root: str,
        allocation_vector: Dict[str, int],
        optimality_certificate: Dict[str, Any],
        enclave_signature: str,
    ):
        self.block_height = block_height
        self.prev_block_hash = prev_block_hash
        self.decision_timestamp = decision_timestamp
        self.causal_data_root = causal_data_root
        self.allocation_vector = allocation_vector
        self.optimality_certificate = optimality_certificate
        self.enclave_signature = enclave_signature

        self.block_data = {
            "block_height": self.block_height,
            "prev_block_hash": self.prev_block_hash,
            "decision_timestamp": self.decision_timestamp,
            "causal_data_root": self.causal_data_root,
            "allocation_vector": self.allocation_vector,
            "optimality_metrics": self.optimality_certificate.get("optimality_metrics", {}),
            "enclave_signature": self.enclave_signature,
        }

        serialized = json.dumps(self.block_data, sort_keys=True).encode("utf-8")
        self.block_hash = "0x" + hashlib.sha256(serialized).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.block_data)
        d["block_hash"] = self.block_hash
        return d


class GovernanceAuditLedger:
    """Maintains the append-only cryptographic chain of decision blocks."""

    def __init__(self):
        self.chain: List[MerkleAuditBlock] = []

    def append_block(
        self,
        decision_timestamp: str,
        causal_data_root: str,
        allocation_vector: Dict[str, int],
        optimality_certificate: Dict[str, Any],
        enclave_signature: str,
    ) -> MerkleAuditBlock:
        prev_hash = self.chain[-1].block_hash if self.chain else "0x0000000000000000000000000000000000000000000000000000000000000000"
        height = len(self.chain)

        block = MerkleAuditBlock(
            block_height=height,
            prev_block_hash=prev_hash,
            decision_timestamp=decision_timestamp,
            causal_data_root=causal_data_root,
            allocation_vector=allocation_vector,
            optimality_certificate=optimality_certificate,
            enclave_signature=enclave_signature,
        )
        self.chain.append(block)
        return block

    def verify_chain_integrity(self) -> bool:
        """Verify hash linkages across all blocks in the chain."""
        if not self.chain:
            return True

        for i in range(1, len(self.chain)):
            curr = self.chain[i]
            prev = self.chain[i - 1]
            if curr.prev_block_hash != prev.block_hash:
                return False
        return True
