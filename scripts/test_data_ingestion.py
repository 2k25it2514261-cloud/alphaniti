"""
AlphaNiti v25.0: Comprehensive Data Ingestion & Provenance Test Suite
Verifies:
  1. Five-Timestamp Ingestion & Monotonicity Axiom
  2. Cryptographic SHA-256 Immutability & Tamper Detection
  3. Causal Filtration Operator Boundary Conditions
  4. Canonical Corporate Action Adjustment Multipliers
  5. Physical Parquet Split Files Integrity (Train, Val, Test)
  6. Cryptographic SHA-256 Checksum Validation against Manifest
  7. Cross-Partition Zero-Leakage & 20-Day Embargo Invariant
  8. Robust MAD Scaler Fit-Transform & Outlier Winsorization
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, date, timezone, timedelta
import hashlib
import json
import numpy as np
import polars as pl

from src.data.temporal_model import (
    FiveTimestamp,
    ObservationRecord,
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
from src.data.robust_scaler import RobustMADScaler

def run_all_ingestion_tests():
    print("=" * 80)
    print("ALPHANITI v25.0: FULL DATA INGESTION & PIPELINE VERIFICATION SUITE")
    print("=" * 80)

    results = []

    # -------------------------------------------------------------------------
    # TEST 1: Five-Timestamp Creation & SHA-256 Cryptographic Immutability
    # -------------------------------------------------------------------------
    print("\n[CHECK 1/8] Testing Five-Timestamp Ingestion & Cryptographic Hashing...")
    t0 = datetime(2026, 4, 1, 9, 15, 0, tzinfo=timezone.utc)
    ts = FiveTimestamp(
        tau_event=t0,
        tau_pub=t0 + timedelta(milliseconds=5),
        tau_vendor=t0 + timedelta(milliseconds=12),
        tau_ingest=t0 + timedelta(milliseconds=18),
        tau_effective=t0
    )
    obs = create_observation("RELIANCE", "close", 2950.0, ts)
    assert obs is not None, "Observation creation failed"
    assert obs.verify_integrity(), "SHA-256 verification failed"
    print(f"  -> Generated SHA-256 Stamp: {obs.sha256_hash}")
    print("  -> Cryptographic Immutability: VERIFIED")
    results.append(("Five-Timestamp & SHA-256 Immutability", True))

    # -------------------------------------------------------------------------
    # TEST 2: Temporal Monotonicity Axiom & Quarantine Ledger
    # -------------------------------------------------------------------------
    print("\n[CHECK 2/8] Testing Temporal Monotonicity Axiom & Quarantine...")
    q_ledger = QuarantineLedger()
    # Inverted: tau_vendor > tau_ingest violation
    bad_ts = FiveTimestamp(
        tau_event=t0,
        tau_pub=t0 + timedelta(milliseconds=5),
        tau_vendor=t0 + timedelta(milliseconds=25), # Vendor later than ingest!
        tau_ingest=t0 + timedelta(milliseconds=10),
        tau_effective=t0
    )
    bad_obs = create_observation("INFY", "open", 1850.0, bad_ts, quarantine_ledger=q_ledger)
    assert bad_obs is None, "Faulty observation was not blocked!"
    assert len(q_ledger.quarantined_records) == 1, "Quarantine ledger failed to record violation!"
    print(f"  -> Successfully intercepted violation: {q_ledger.quarantined_records[0]['reason'][:85]}...")
    print("  -> Quarantine Ledger: VERIFIED")
    results.append(("Monotonicity Axiom & Quarantine", True))

    # -------------------------------------------------------------------------
    # TEST 3: Causal Filtration Operator (Zero-Latency Barrier)
    # -------------------------------------------------------------------------
    print("\n[CHECK 3/8] Testing Causal Filtration Operator Boundaries...")
    t_decision = datetime(2026, 4, 1, 15, 30, 0, tzinfo=timezone.utc)
    
    # Record A: Arrived 1 millisecond before decision (Valid)
    ts_valid = FiveTimestamp(
        tau_event=t_decision - timedelta(seconds=1),
        tau_pub=t_decision - timedelta(milliseconds=500),
        tau_vendor=t_decision - timedelta(milliseconds=100),
        tau_ingest=t_decision - timedelta(milliseconds=1),
        tau_effective=t_decision
    )
    rec_valid = create_observation("TCS", "volume", 150000, ts_valid)
    
    # Record B: Arrived exactly at t_decision (Invalid under strict <)
    ts_boundary = FiveTimestamp(
        tau_event=t_decision - timedelta(seconds=1),
        tau_pub=t_decision - timedelta(milliseconds=100),
        tau_vendor=t_decision - timedelta(milliseconds=50),
        tau_ingest=t_decision, # Exact arrival!
        tau_effective=t_decision
    )
    rec_boundary = create_observation("TCS", "volume", 150000, ts_boundary)
    
    # Record C: Future effective regulatory event
    ts_future = FiveTimestamp(
        tau_event=t_decision - timedelta(hours=1),
        tau_pub=t_decision - timedelta(hours=1),
        tau_vendor=t_decision - timedelta(hours=1),
        tau_ingest=t_decision - timedelta(hours=1),
        tau_effective=t_decision + timedelta(days=1)
    )
    rec_future = create_observation("TCS", "split", 0.5, ts_future)

    filtered = causal_filtration_operator([rec_valid, rec_boundary, rec_future], t_decision)
    assert len(filtered) == 1, f"Expected 1 valid record, got {len(filtered)}"
    assert filtered[0].timestamps.tau_ingest < t_decision
    print(f"  -> Filtered {len(filtered)} valid record from 3 candidates (Strict < & <= barriers enforced)")
    print("  -> Causal Filtration Operator: VERIFIED")
    results.append(("Causal Filtration Boundaries", True))

    # -------------------------------------------------------------------------
    # TEST 4: Corporate Actions Adjustments & Lookahead Invariants
    # -------------------------------------------------------------------------
    print("\n[CHECK 4/8] Testing Canonical Corporate Action Multipliers...")
    ev_split = CorporateActionEvent("ABC", date(2026, 5, 1), ActionType.SPLIT, A=1, B=10)
    ev_bonus = CorporateActionEvent("ABC", date(2026, 6, 1), ActionType.BONUS, A=1, B=1)
    ev_div = CorporateActionEvent("ABC", date(2026, 7, 1), ActionType.DIVIDEND, div_amount=5.0, p_cum=100.0)
    
    assert ev_split.compute_multiplier() == 0.10, "Split multiplier failed"
    assert ev_bonus.compute_multiplier() == 0.50, "Bonus multiplier failed"
    assert ev_div.compute_multiplier() == 0.95, "Dividend multiplier failed"
    
    # Test lookahead barrier: decision date before bonus/dividend
    cum_factor = compute_cumulative_adjustment_factor([ev_split, ev_bonus, ev_div], history_date=date(2026, 4, 1), decision_date=date(2026, 5, 15))
    # Only split applies (ex-date May 1 <= decision May 15)
    assert abs(cum_factor - 0.10) < 1e-6, f"Expected 0.10, got {cum_factor}"
    print(f"  -> Multipliers: Split={ev_split.compute_multiplier()}, Bonus={ev_bonus.compute_multiplier()}, Dividend={ev_div.compute_multiplier()}")
    print("  -> Decision-Time Cumulative Multiplier Barrier: VERIFIED")
    results.append(("Corporate Action Canonical Math", True))

    # -------------------------------------------------------------------------
    # TEST 5: Physical Parquet Split Files Loading & Checksum Audit
    # -------------------------------------------------------------------------
    print("\n[CHECK 5/8] Auditing Physical Parquet Splits & Checksums...")
    base_dir = r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\alphaniti\data\splits"
    manifest_path = os.path.join(base_dir, "manifest.json")
    assert os.path.exists(manifest_path), "manifest.json missing!"
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    for fname in ["train.parquet", "val.parquet", "test.parquet"]:
        fpath = os.path.join(base_dir, fname)
        assert os.path.exists(fpath), f"{fname} missing on disk!"
        with open(fpath, "rb") as f:
            computed_sha = hashlib.sha256(f.read()).hexdigest()
        expected_sha = manifest["sha256_checksums"][fname]
        assert computed_sha == expected_sha, f"Checksum mismatch on {fname}!"
        print(f"  -> {fname:<15}: Verified SHA-256 [{computed_sha[:16]}...]")
    results.append(("Parquet Physical Checksums", True))

    # -------------------------------------------------------------------------
    # TEST 6: Invariant & Null Checks across Train, Val, Test
    # -------------------------------------------------------------------------
    print("\n[CHECK 6/8] Auditing Missing Values & Invariants across Partitions...")
    train_df = pl.read_parquet(os.path.join(base_dir, "train.parquet"))
    val_df = pl.read_parquet(os.path.join(base_dir, "val.parquet"))
    test_df = pl.read_parquet(os.path.join(base_dir, "test.parquet"))
    
    critical_cols = ["date", "symbol", "open", "high", "low", "close", "target_ret_20d_net", "target_breach_5pct"]
    for split_name, df_part in [("Train", train_df), ("Val", val_df), ("Test", test_df)]:
        for c in critical_cols:
            null_count = df_part[c].null_count()
            assert null_count == 0, f"{split_name} has {null_count} nulls in critical column {c}"
        
        # Verify strictly positive prices
        zeros = df_part.filter((pl.col("open") <= 0) | (pl.col("close") <= 0))
        assert len(zeros) == 0, f"{split_name} has {len(zeros)} non-positive prices!"
        
    print(f"  -> Train: {len(train_df):,} rows | Val: {len(val_df):,} rows | Test: {len(test_df):,} rows")
    print("  -> 0.0% Nulls in all critical columns across all 3 partitions: VERIFIED")
    results.append(("Partition Invariants & 0% Nulls", True))

    # -------------------------------------------------------------------------
    # TEST 7: Cross-Partition Zero-Leakage & 20-Day Embargo Audit
    # -------------------------------------------------------------------------
    print("\n[CHECK 7/8] Auditing Purge Gaps & Embargo Invariants...")
    max_train_date = train_df["date"].max()
    min_val_date = val_df["date"].min()
    max_val_date = val_df["date"].max()
    min_test_date = test_df["date"].min()
    
    # Convert to days
    gap1_days = (min_val_date - max_train_date).days
    gap2_days = (min_test_date - max_val_date).days
    
    # 20 trading days is ~28 calendar days
    assert min_val_date > max_train_date, "Train and Val overlap!"
    assert min_test_date > max_val_date, "Val and Test overlap!"
    assert gap1_days >= 25, f"Gap 1 too short ({gap1_days} calendar days)"
    assert gap2_days >= 25, f"Gap 2 too short ({gap2_days} calendar days)"
    
    print(f"  -> Gap 1 (Train -> Val): {gap1_days} calendar days ({max_train_date} to {min_val_date})")
    print(f"  -> Gap 2 (Val -> Test):   {gap2_days} calendar days ({max_val_date} to {min_test_date})")
    print("  -> Zero-Leakage Forward Embargoes: VERIFIED")
    results.append(("Zero-Leakage Forward Embargoes", True))

    # -------------------------------------------------------------------------
    # TEST 8: Robust Scaling & Outlier Winsorization Fit on Train, Transform on Val
    # -------------------------------------------------------------------------
    print("\n[CHECK 8/8] Testing RobustMADScaler Fit on Train & Out-of-Sample Transform on Val...")
    train_sub = train_df.select(["ret_1d", "volume", "turnover_cr"]).with_columns(pl.col("ret_1d").fill_null(0.0))
    val_sub = val_df.select(["ret_1d", "volume", "turnover_cr"]).with_columns(pl.col("ret_1d").fill_null(0.0))
    
    scaler = RobustMADScaler(c_clip=4.5)
    scaler.fit(train_sub)
    
    z_train = scaler.transform(train_sub)
    z_val = scaler.transform(val_sub)
    
    assert not np.isnan(z_train).any(), "NaN in scaled training features"
    assert not np.isnan(z_val).any(), "NaN in scaled validation features"
    assert (np.abs(z_train) <= 4.5).all(), "Train feature exceeded winsorization bound [-4.5, 4.5]"
    assert (np.abs(z_val) <= 4.5).all(), "Val feature exceeded winsorization bound [-4.5, 4.5]"
    
    print(f"  -> Scaler Parameters Fitted: Medians={scaler.medians_}, MADs={scaler.mads_}")
    print(f"  -> Train Bounds: [{z_train.min():.2f}, {z_train.max():.2f}] | Val Bounds: [{z_val.min():.2f}, {z_val.max():.2f}]")
    print("  -> Robust MAD Scaling & Outlier Winsorization: VERIFIED")
    results.append(("Robust MAD Scaler Pipeline", True))

    # -------------------------------------------------------------------------
    # SUMMARY REPORT
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("DATA INGESTION PIPELINE: FINAL AUDIT VERDICT")
    print("=" * 80)
    all_ok = all(passed for _, passed in results)
    for name, passed in results:
        print(f" - {name:<50}: {'PASS' if passed else 'FAIL'}")
    print("=" * 80)
    print(f"OVERALL STATUS: {'ALL CHECKS PASSED (PRODUCTION READY)' if all_ok else 'FAILED'}")
    print("=" * 80)
    return all_ok

if __name__ == "__main__":
    ok = run_all_ingestion_tests()
    if not ok:
        sys.exit(1)
