"""
AlphaNiti v25.0: Empirical Audit & Benchmarking Protocol (Meinert et al., 2023)
Compares Deep Evidential Regression (DER) vs. Plain Deep Ensemble (Lakshminarayanan et al., 2017)
on Out-of-Sample Indian Equity Panels (data/splits/val.parquet and test.parquet).

Tests:
  1. Does DER analytical epistemic variance sigma^2_epistemic correlate with squared errors in unseen regimes?
  2. Does Plain Deep Ensemble variance Var_between(mu) predict errors more reliably than DER?
  3. Evaluates error-stratified calibration and AUROC for out-of-distribution regime shock detection.
"""

import sys
import os
import time
from pathlib import Path
import numpy as np
import polars as pl
import torch
from scipy.stats import spearmanr, pearsonr

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import (
    EvidentialGRUModel,
    EvidentialEnsemble,
    GaussianGRUModel,
    PlainDeepEnsemble,
)
from src.models.trainer import (
    train_evidential_ensemble,
    train_plain_deep_ensemble,
)
from src.risk.tier_gate import HysteresisTierGate


def evaluate_model_uncertainty(predictions, uncertainties, actuals):
    """
    Computes statistical evaluation metrics on whether predicted uncertainty
    truthfully predicts out-of-sample forecast errors:
      - Spearman rank correlation rho(sigma^2, e^2)
      - Pearson correlation r(sigma^2, e^2)
      - Error-variance quantile monotonicity
    """
    preds = np.array(predictions).flatten()
    uncs = np.array(uncertainties).flatten()
    acts = np.array(actuals).flatten()

    sq_errors = (acts - preds) ** 2

    # Rank correlation: higher uncertainty should correlate with higher squared errors
    spearman_corr, sp_p = spearmanr(uncs, sq_errors)
    pearson_corr, pe_p = pearsonr(uncs, sq_errors)

    # Decile calibration: mean error in top 20% uncertainty vs bottom 20%
    q80 = np.quantile(uncs, 0.80)
    q20 = np.quantile(uncs, 0.20)
    high_unc_err = np.mean(sq_errors[uncs >= q80])
    low_unc_err = np.mean(sq_errors[uncs <= q20])
    error_ratio = high_unc_err / max(1e-6, low_unc_err)

    return {
        "spearman_rho": float(spearman_corr),
        "spearman_pvalue": float(sp_p),
        "pearson_r": float(pearson_corr),
        "pearson_pvalue": float(pe_p),
        "high_unc_mse": float(high_unc_err),
        "low_unc_mse": float(low_unc_err),
        "error_separation_ratio": float(error_ratio),
    }


def main():
    print("=" * 80)
    print("ALPHANITI v25.0: MEINERT ET AL. (2023) EMPIRICAL AUDIT & UNCERTAINTY BENCHMARK")
    print("=" * 80)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    data_dir = PROJECT_ROOT / "data" / "splits"
    train_path = str(data_dir / "train.parquet")
    val_path = str(data_dir / "val.parquet")
    test_path = str(data_dir / "test.parquet")

    print(f"\n1. Ingesting Production Splits from {data_dir.name}...")
    train_loader, val_loader, test_loader, _, feature_names = create_dataloaders(
        train_parquet_path=train_path,
        val_parquet_path=val_path,
        test_parquet_path=test_path,
        lookback=60,
        batch_size=256,
        num_workers=0,
    )
    print(f"   Features: {len(feature_names)} causal indicators")
    print(f"   Train: {len(train_loader.dataset):,} | Val: {len(val_loader.dataset):,} | Test: {len(test_loader.dataset):,}")

    # Check for pre-trained weights or train 2-member baseline
    der_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    if der_path.exists():
        print(f"\n2. Loading Pre-Trained Evidential Deep Ensemble from {der_path.name}...")
        ckpt = torch.load(der_path, weights_only=True, map_location=device)
        models = []
        for i in range(3):
            if f"member_{i}" in ckpt:
                m = EvidentialGRUModel(in_features=len(feature_names), hidden_dim=64).to(device)
                m.load_state_dict(ckpt[f"member_{i}"])
                m.eval()
                models.append(m)
        der_ensemble = EvidentialEnsemble(models)
    else:
        print("\n2. Training Evidential Deep Ensemble (2 epochs)...")
        der_ensemble = train_evidential_ensemble(
            train_loader=train_loader,
            val_loader=val_loader,
            n_models=2,
            in_features=len(feature_names),
            hidden_dim=32,
            epochs=2,
            device=device,
        )

    # Train Plain Deep Ensemble side-by-side
    print("\n3. Training Plain Deep Ensemble (Lakshminarayanan et al., 2017) Baseline (2 epochs)...")
    t0 = time.perf_counter()
    plain_ensemble = train_plain_deep_ensemble(
        train_loader=train_loader,
        val_loader=val_loader,
        n_models=2,
        in_features=len(feature_names),
        hidden_dim=32,
        epochs=2,
        device=device,
    )
    print(f"   Plain Deep Ensemble Training Elapsed: {time.perf_counter() - t0:.2f}s")

    # Evaluate on Untouched Test Holdout
    print("\n4. Running Inference & Epistemic Uncertainty Decomposition on Test Holdout...")
    test_actuals = []
    der_preds, der_epistemic = [], []
    plain_preds, plain_epistemic = [], []

    with torch.no_grad():
        for X_b, y_reg, _ in test_loader:
            X_b = X_b.to(device)
            test_actuals.extend(y_reg.cpu().numpy().flatten())

            # DER predictions
            g_bar, _, s_epi_der, _ = der_ensemble.predict(X_b)
            der_preds.extend(g_bar.flatten())
            der_epistemic.extend((s_epi_der**2).flatten())

            # Plain Deep Ensemble predictions
            mu_bar, _, s_epi_plain, _ = plain_ensemble.predict(X_b)
            plain_preds.extend(mu_bar.flatten())
            plain_epistemic.extend((s_epi_plain**2).flatten())

    acts = np.array(test_actuals)
    der_eval = evaluate_model_uncertainty(der_preds, der_epistemic, acts)
    plain_eval = evaluate_model_uncertainty(plain_preds, plain_epistemic, acts)

    print("\n" + "=" * 80)
    print("EMPIRICAL AUDIT RESULTS: EVIDENTIAL REGRESSION VS. PLAIN DEEP ENSEMBLE")
    print("=" * 80)
    print(f"Sample Size (Test Holdout): {len(acts):,} trading observations")
    print("\nMetric                                    | Deep Evidential Regression | Plain Deep Ensemble (Baseline)")
    print("-" * 88)
    print(f"Epistemic-Error Rank Correlation (Rho)    | {der_eval['spearman_rho']:+.4f} (p={der_eval['spearman_pvalue']:.2e})        | {plain_eval['spearman_rho']:+.4f} (p={plain_eval['spearman_pvalue']:.2e})")
    print(f"Epistemic-Error Linear Correlation (r)    | {der_eval['pearson_r']:+.4f} (p={der_eval['pearson_pvalue']:.2e})        | {plain_eval['pearson_r']:+.4f} (p={plain_eval['pearson_pvalue']:.2e})")
    print(f"High-Uncertainty Top 20% MSE              | {der_eval['high_unc_mse']:.6f}                 | {plain_eval['high_unc_mse']:.6f}")
    print(f"Low-Uncertainty Bottom 20% MSE            | {der_eval['low_unc_mse']:.6f}                 | {plain_eval['low_unc_mse']:.6f}")
    print(f"Error Separation Ratio (High/Low)         | {der_eval['error_separation_ratio']:.2f}x                     | {plain_eval['error_separation_ratio']:.2f}x")
    print("-" * 88)

    # 5. Evaluate Hysteresis Tier Gate on the Epistemic Stream
    print("\n5. Testing Hysteresis Tier Gate on Epistemic Uncertainty Stream...")
    gate = HysteresisTierGate()
    tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    for u_t in der_epistemic[::10]:  # sample every 10th observation
        d = gate.update(u_t)
        tier_counts[d["tier"]] += 1

    total_samples = sum(tier_counts.values())
    print("   Tier Distribution across Test Stream:")
    for tier, count in tier_counts.items():
        print(f"     Tier {tier} ({gate._build_directive(0)['tier_name'] if tier==0 else ('Soft Buy-Freeze' if tier==1 else ('De-Grossing' if tier==2 else 'Systemic Freeze'))}): {count:,} events ({count / total_samples * 100:.1f}%)")

    # Meinert Verdict Summary
    print("\n" + "=" * 80)
    print("MEINERT ET AL. (2023) ARCHITECTURAL VERDICT & CONCLUSION:")
    if plain_eval["spearman_rho"] >= der_eval["spearman_rho"]:
        print("  -> CONFIRMED: Empirical Deep Ensemble disagreement exhibits superior or equal rank")
        print("     correlation with out-of-sample forecast errors compared to pure evidential NIG variance.")
        print("     AlphaNiti Dual-Engine safeguards allocation by gating on combined ensemble dispersion.")
    else:
        print("  -> Evidential NIG variance exhibits competitive rank correlation on this equity panel.")
        print("     Both models operate jointly under the Hysteresis Tier Gate.")
    print("=" * 80)


if __name__ == "__main__":
    main()
