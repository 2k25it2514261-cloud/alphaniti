"""
AlphaNiti v25.0: Pipeline Module 1 - Temporal Model & Ingestion Engine
Implementation of the Five-Timestamp Causal Data Model (Architectural Callout 6)

Mathematical Model:
  X = (EntityID, Field, Value, tau_vec, Hash_SHA256)
  tau_vec = (tau_event, tau_pub, tau_vendor, tau_ingest, tau_effective)

Invariants:
  1. Temporal Monotonicity Axiom:
     tau_event <= tau_pub <= tau_vendor <= tau_ingest
  2. Causal Eligibility Operator F(t_decision):
     max(tau_pub, tau_vendor, tau_ingest) < t_decision AND tau_effective <= t_decision
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, List, Optional, Tuple, Dict
import polars as pl
import pandas as pd


class IngestionMonotonicityViolation(ValueError):
    """Raised when an ingested record violates the physical temporal monotonicity axiom."""
    def __init__(self, record_id: str, tau_event, tau_pub, tau_vendor, tau_ingest):
        msg = (
            f"Temporal Monotonicity Violation for record '{record_id}': "
            f"Expected tau_event <= tau_pub <= tau_vendor <= tau_ingest, but got: "
            f"tau_event={tau_event}, tau_pub={tau_pub}, tau_vendor={tau_vendor}, tau_ingest={tau_ingest}"
        )
        super().__init__(msg)
        self.record_id = record_id
        self.tau_event = tau_event
        self.tau_pub = tau_pub
        self.tau_vendor = tau_vendor
        self.tau_ingest = tau_ingest


@dataclass(frozen=True)
class FiveTimestamp:
    """
    The formal 5-timestamp provenance vector tau_vec in R_>=0^5:
      tau_event:     Physical instant phenomenon occurred in reality
      tau_pub:       Instant officially broadcast to public domain (e.g. NEAPS/BSE)
      tau_vendor:    Instant data vendor packaged and stamped packet
      tau_ingest:    Local NIC arrival and cryptographic commit timestamp
      tau_effective: Regulatory or operational market effective instant (e.g. ex-date)
    """
    tau_event: datetime
    tau_pub: datetime
    tau_vendor: datetime
    tau_ingest: datetime
    tau_effective: datetime

    def validate_monotonicity(self, entity_id: str = "") -> None:
        """
        Enforces: tau_event <= tau_pub <= tau_vendor <= tau_ingest
        """
        if not (self.tau_event <= self.tau_pub <= self.tau_vendor <= self.tau_ingest):
            raise IngestionMonotonicityViolation(
                record_id=entity_id,
                tau_event=self.tau_event,
                tau_pub=self.tau_pub,
                tau_vendor=self.tau_vendor,
                tau_ingest=self.tau_ingest,
            )

    def to_dict(self) -> Dict[str, str]:
        return {
            "tau_event": self.tau_event.isoformat(),
            "tau_pub": self.tau_pub.isoformat(),
            "tau_vendor": self.tau_vendor.isoformat(),
            "tau_ingest": self.tau_ingest.isoformat(),
            "tau_effective": self.tau_effective.isoformat(),
        }


@dataclass(frozen=True)
class ObservationRecord:
    """
    Immutable 6-tuple: X = (EntityID, Field, Value, tau_vec, Hash_SHA256)
    """
    entity_id: str
    field_name: str
    value: Any
    timestamps: FiveTimestamp
    sha256_hash: str

    def verify_integrity(self) -> bool:
        """Verifies cryptographic hash against content."""
        computed = compute_record_hash(self.entity_id, self.field_name, self.value, self.timestamps)
        return computed == self.sha256_hash


def compute_record_hash(entity_id: str, field_name: str, value: Any, timestamps: FiveTimestamp) -> str:
    """Computes SHA-256 hash over canonical representation of record."""
    payload = {
        "entity_id": str(entity_id),
        "field": str(field_name),
        "value": str(value),
        "timestamps": timestamps.to_dict()
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class QuarantineLedger:
    """Append-only audit ledger for records failing validation checks."""
    def __init__(self):
        self.quarantined_records: List[Dict[str, Any]] = []

    def record_violation(self, entity_id: str, field_name: str, value: Any, timestamps: FiveTimestamp, reason: str):
        entry = {
            "quarantine_time": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "field": field_name,
            "value": str(value),
            "timestamps": timestamps.to_dict(),
            "reason": reason
        }
        self.quarantined_records.append(entry)


# Global quarantine ledger instance
GLOBAL_QUARANTINE_LEDGER = QuarantineLedger()


def create_observation(
    entity_id: str,
    field_name: str,
    value: Any,
    timestamps: FiveTimestamp,
    quarantine_ledger: Optional[QuarantineLedger] = None
) -> Optional[ObservationRecord]:
    """
    Factory creating an immutable ObservationRecord.
    Enforces Temporal Monotonicity Axiom. If violated, quarantines entry and returns None.
    """
    ledger = quarantine_ledger or GLOBAL_QUARANTINE_LEDGER
    try:
        timestamps.validate_monotonicity(entity_id=entity_id)
    except IngestionMonotonicityViolation as e:
        ledger.record_violation(
            entity_id=entity_id,
            field_name=field_name,
            value=value,
            timestamps=timestamps,
            reason=str(e)
        )
        return None

    h = compute_record_hash(entity_id, field_name, value, timestamps)
    return ObservationRecord(
        entity_id=entity_id,
        field_name=field_name,
        value=value,
        timestamps=timestamps,
        sha256_hash=h
    )


def causal_filtration_operator(records: List[ObservationRecord], t_decision: datetime) -> List[ObservationRecord]:
    """
    Formal Causal Eligibility Operator F(t_decision):
      F(t_decision) = { X in D | max(tau_pub, tau_vendor, tau_ingest) < t_decision AND tau_effective <= t_decision }
    
    Invariants:
      1. Strict inequality (<): Precludes zero-latency lookahead at decision instant.
      2. Effective date barrier (<=): Precludes trading on future-dated regulatory events.
    """
    filtered = []
    for r in records:
        ts = r.timestamps
        max_knowledge_time = max(ts.tau_pub, ts.tau_vendor, ts.tau_ingest)
        if (max_knowledge_time < t_decision) and (ts.tau_effective <= t_decision):
            filtered.append(r)
    return filtered


def causal_filtration_polars(
    df: pl.DataFrame,
    t_decision: datetime,
    col_pub: str = "tau_pub",
    col_vendor: str = "tau_vendor",
    col_ingest: str = "tau_ingest",
    col_effective: str = "tau_effective"
) -> pl.DataFrame:
    """
    Vectorized Polars implementation of Causal Filtration Operator.
    """
    t_dec_lit = pl.lit(t_decision)
    
    # max(tau_pub, tau_vendor, tau_ingest) < t_decision
    # AND tau_effective <= t_decision
    return df.filter(
        (pl.col(col_pub) < t_dec_lit) &
        (pl.col(col_vendor) < t_dec_lit) &
        (pl.col(col_ingest) < t_dec_lit) &
        (pl.col(col_effective) <= t_dec_lit)
    )
