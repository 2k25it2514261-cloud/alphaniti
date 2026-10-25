"""
AlphaNiti v25.0 Institutional Classification Metrics Evaluator
Computes Precision, Recall, F1-Score, Confusion Matrix, and ROC-AUC for:
  1. Downside Tail Risk (>5% Drawdown Breach: target_breach_5pct)
  2. Directional Return Classification (Sign of 20-day return: y_reg > 0)
Evaluated across both Validation and Out-of-Sample Test partitions.
"""

import sys
from pathlib import Path
import numpy as np
import polars as pl
import torch
from sklearn.metrics import classification_report, f1_score, roc_auc_score, precision_score, recall_score, confusion_matrix

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble


def evaluate_split(split_name: str, loader, ensemble, device):
    y_breach_true, y_breach_prob = [], []
    y_ret_true, y_ret_pred = [], []

    with torch.no_grad():
        for X, y_reg, y_clf in loader:
            X = X.to(device)
            gamma, s_ale, s_epi, p_b = ensemble.predict(X)
            y_breach_true.extend(y_clf.numpy().flatten())
            y_breach_prob.extend(p_b.flatten())
            y_ret_true.extend(y_reg.numpy().flatten())
            y_ret_pred.extend(gamma.flatten())

    y_breach_true = np.array(y_breach_true, dtype=int)
    y_breach_prob = np.array(y_breach_prob, dtype=float)
    y_ret_true = np.array(y_ret_true, dtype=float)
    y_ret_pred = np.array(y_ret_pred, dtype=float)

    print("\n" + "=" * 70)
    print(f"EVALUATION RESULTS: {split_name.upper()} PARTITION ({len(y_breach_true):,} sequences)")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. Downside Tail Risk Breach (>5% Drawdown: target_breach_5pct)
    # -------------------------------------------------------------
    print("\n[Task 1] DOWNSIDE TAIL RISK BREACH CLASSIFICATION (>5% Drawdown)")
    print("-" * 70)
    prevalence = np.mean(y_breach_true)
    print(f"  Breach Base Rate (Prevalence): {prevalence:.2%} ({np.sum(y_breach_true):,} / {len(y_breach_true):,})")

    # Standard 0.50 threshold
    y_breach_pred_50 = (y_breach_prob >= 0.50).astype(int)
    f1_50 = f1_score(y_breach_true, y_breach_pred_50, zero_division=0)
    prec_50 = precision_score(y_breach_true, y_breach_pred_50, zero_division=0)
    rec_50 = recall_score(y_breach_true, y_breach_pred_50, zero_division=0)
    auc_breach = roc_auc_score(y_breach_true, y_breach_prob)

    print(f"\n  -- Default Threshold (tau = 0.50) --")
    print(f"    F1-Score:   {f1_50:.4f}")
    print(f"    Precision:  {prec_50:.4f}")
    print(f"    Recall:     {rec_50:.4f}")
    print(f"    ROC-AUC:    {auc_breach:.4f}")

    cm_50 = confusion_matrix(y_breach_true, y_breach_pred_50)
    print(f"    Confusion Matrix [TN, FP; FN, TP]:")
    print(f"      [TN={cm_50[0,0]:5d}, FP={cm_50[0,1]:5d}]")
    print(f"      [FN={cm_50[1,0]:5d}, TP={cm_50[1,1]:5d}]")

    # Optimal threshold scan for best F1
    thresholds = np.linspace(0.10, 0.90, 81)
    best_tau, best_f1, best_prec, best_rec = 0.50, 0.0, 0.0, 0.0
    for tau in thresholds:
        pred_tau = (y_breach_prob >= tau).astype(int)
        f1_t = f1_score(y_breach_true, pred_tau, zero_division=0)
        if f1_t > best_f1:
            best_f1 = f1_t
            best_tau = tau
            best_prec = precision_score(y_breach_true, pred_tau, zero_division=0)
            best_rec = recall_score(y_breach_true, pred_tau, zero_division=0)

    print(f"\n  -- Optimal F1 Threshold (tau = {best_tau:.2f}) --")
    print(f"    Optimal F1-Score:  {best_f1:.4f}")
    print(f"    Precision at tau:  {best_prec:.4f}")
    print(f"    Recall at tau:     {best_rec:.4f}")

    # -------------------------------------------------------------
    # 2. Directional Return Classification (Sign of 20d return > 0)
    # -------------------------------------------------------------
    print("\n[Task 2] DIRECTIONAL RETURN CLASSIFICATION (Sign of 20-Day Return > 0)")
    print("-" * 70)
    y_dir_true = (y_ret_true > 0).astype(int)
    y_dir_pred = (y_ret_pred > 0).astype(int)

    f1_dir = f1_score(y_dir_true, y_dir_pred, zero_division=0)
    prec_dir = precision_score(y_dir_true, y_dir_pred, zero_division=0)
    rec_dir = recall_score(y_dir_true, y_dir_pred, zero_division=0)
    acc_dir = np.mean(y_dir_true == y_dir_pred)

    print(f"  Positive Return Base Rate:     {np.mean(y_dir_true):.2%}")
    print(f"  Directional Accuracy (Hit Rate): {acc_dir:.2%}")
    print(f"  Directional F1-Score:          {f1_dir:.4f}")
    print(f"  Directional Precision:         {prec_dir:.4f}")
    print(f"  Directional Recall:            {rec_dir:.4f}")

    cm_dir = confusion_matrix(y_dir_true, y_dir_pred)
    print(f"  Confusion Matrix [TN, FP; FN, TP]:")
    print(f"    [TN={cm_dir[0,0]:5d}, FP={cm_dir[0,1]:5d}]")
    print(f"    [FN={cm_dir[1,0]:5d}, TP={cm_dir[1,1]:5d}]")

    return {
        "split": split_name,
        "f1_breach_default": f1_50,
        "f1_breach_optimal": best_f1,
        "optimal_threshold": best_tau,
        "roc_auc_breach": auc_breach,
        "f1_directional": f1_dir,
        "directional_accuracy": acc_dir,
    }


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading data loaders on {device.upper()}...")

    train_path = str(PROJECT_ROOT / "data" / "splits" / "train.parquet")
    val_path = str(PROJECT_ROOT / "data" / "splits" / "val.parquet")
    test_path = str(PROJECT_ROOT / "data" / "splits" / "test.parquet")

    _, val_loader, test_loader, pipeline, feature_names = create_dataloaders(
        train_parquet_path=train_path,
        val_parquet_path=val_path,
        test_parquet_path=test_path,
        lookback=60,
        batch_size=256,
        num_workers=0,
    )

    save_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    if not save_path.exists():
        print(f"Error: Model checkpoint not found at {save_path}")
        sys.exit(1)

    print(f"Loading trained Evidential Ensemble from {save_path.name}...")
    checkpoint = torch.load(save_path, weights_only=True, map_location=device)
    models = []
    for i in range(3):
        m = EvidentialGRUModel(in_features=len(feature_names), hidden_dim=64).to(device)
        m.load_state_dict(checkpoint[f"member_{i}"])
        m.eval()
        models.append(m)

    ensemble = EvidentialEnsemble(models)

    # Evaluate Validation and Out-of-Sample Test
    val_metrics = evaluate_split("Validation (2023-2025)", val_loader, ensemble, device)
    test_metrics = evaluate_split("Test Out-of-Sample (2025-2026)", test_loader, ensemble, device)

    print("\n" + "=" * 70)
    print("SUMMARY COMPARISON")
    print("=" * 70)
    print(f"{'Metric':<35} | {'Validation':<15} | {'Test (OOS)':<15}")
    print("-" * 70)
    print(f"{'Downside Breach F1 (tau=0.50)':<35} | {val_metrics['f1_breach_default']:<15.4f} | {test_metrics['f1_breach_default']:<15.4f}")
    print(f"{'Downside Breach F1 (Optimal tau)':<35} | {val_metrics['f1_breach_optimal']:<15.4f} | {test_metrics['f1_breach_optimal']:<15.4f}")
    print(f"{'Downside Breach ROC-AUC':<35} | {val_metrics['roc_auc_breach']:<15.4f} | {test_metrics['roc_auc_breach']:<15.4f}")
    print(f"{'Directional Return F1':<35} | {val_metrics['f1_directional']:<15.4f} | {test_metrics['f1_directional']:<15.4f}")
    print(f"{'Directional Accuracy':<35} | {val_metrics['directional_accuracy']:<15.2%} | {test_metrics['directional_accuracy']:<15.2%}")
    print("=" * 70)


if __name__ == "__main__":
    main()
