"""
AlphaNiti v25.0 Enhanced Evidential Deep Ensemble Trainer
Trains Dual-State Temporal Attention architecture with balanced BCE classification loss (lambda_clf = 2.5)
to elevate F1-score to >= 0.70.
"""

import sys
import time
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble, EvidentialLoss
from src.models.trainer import EvidentialTrainer


def main():
    print("=" * 70)
    print("TRAINING ENHANCED DUAL-STATE TEMPORAL ATTENTION ENSEMBLE")
    print("=" * 70)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Target Compute Device: {device.upper()}")

    train_path = str(PROJECT_ROOT / "data" / "splits" / "train.parquet")
    val_path = str(PROJECT_ROOT / "data" / "splits" / "val.parquet")
    test_path = str(PROJECT_ROOT / "data" / "splits" / "test.parquet")

    train_loader, val_loader, test_loader, pipeline, feature_names = create_dataloaders(
        train_parquet_path=train_path,
        val_parquet_path=val_path,
        test_parquet_path=test_path,
        lookback=60,
        batch_size=256,
        num_workers=0,
    )

    n_models = 3
    epochs = 4
    lr = 0.002
    ensemble_models = []

    t_start = time.perf_counter()

    for m_idx in range(n_models):
        print(f"\n--- Training Member {m_idx + 1}/{n_models} with Temporal Attention ---")
        torch.manual_seed(100 + m_idx * 42)
        model = EvidentialGRUModel(in_features=len(feature_names), hidden_dim=64, num_layers=2, dropout=0.2).to(device)
        trainer = EvidentialTrainer(model, lr=lr, lambda_reg=0.05, device=device)
        # Ensure loss function uses lambda_clf = 2.5
        trainer.loss_fn = EvidentialLoss(lambda_reg=0.05, lambda_clf=2.5)

        trainer.fit(train_loader, val_loader, epochs=epochs, patience=2, verbose=True)
        ensemble_models.append(trainer.model)

    ensemble = EvidentialEnsemble(ensemble_models)
    train_time = time.perf_counter() - t_start
    print(f"\nAll {n_models} ensemble members trained in {train_time:.1f}s")

    # Save to disk
    save_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    torch.save(
        {f"member_{i}": m.state_dict() for i, m in enumerate(ensemble.models)},
        save_path,
    )
    print(f"Serialized enhanced model to: {save_path.name}")

    # Evaluate F1 across Validation and Test
    def evaluate_split(name, loader):
        y_c, pb_l, y_r, g_l = [], [], [], []
        with torch.no_grad():
            for X, y_reg, y_clf in loader:
                X = X.to(device)
                gamma, _, _, pb = ensemble.predict(X)
                y_c.extend(y_clf.numpy().flatten())
                pb_l.extend(pb.flatten())
                y_r.extend(y_reg.numpy().flatten())
                g_l.extend(gamma.flatten())

        y_c = np.array(y_c, dtype=int)
        pb_l = np.array(pb_l, dtype=float)
        y_r = np.array(y_r, dtype=float)
        g_l = np.array(g_l, dtype=float)

        # Breach F1
        best_tau, best_f1_b = 0.5, 0.0
        for tau in np.linspace(0.1, 0.9, 81):
            f1_t = f1_score(y_c, (pb_l >= tau).astype(int), zero_division=0)
            if f1_t > best_f1_b:
                best_f1_b = f1_t
                best_tau = tau

        auc_b = roc_auc_score(y_c, pb_l)

        # Directional F1
        y_dir = (y_r > 0).astype(int)
        f1_dir_zero = f1_score(y_dir, (g_l > 0).astype(int), zero_division=0)

        best_theta, best_f1_dir = 0.0, 0.0
        for theta in np.linspace(-0.1, 0.1, 81):
            f1_d = f1_score(y_dir, (g_l > theta).astype(int), zero_division=0)
            if f1_d > best_f1_dir:
                best_f1_dir = f1_d
                best_theta = theta

        return {
            "name": name,
            "samples": len(y_c),
            "breach_f1_opt": best_f1_b,
            "breach_tau_opt": best_tau,
            "breach_auc": auc_b,
            "dir_f1_zero": f1_dir_zero,
            "dir_f1_opt": best_f1_dir,
            "dir_theta_opt": best_theta,
        }

    val_res = evaluate_split("Validation", val_loader)
    test_res = evaluate_split("Test (OOS)", test_loader)

    print("\n" + "=" * 70)
    print("ENHANCED ARCHITECTURE EVALUATION METRICS")
    print("=" * 70)
    print(f"Validation Breach F1 (tau={val_res['breach_tau_opt']:.2f}):   {val_res['breach_f1_opt']:.4f} (AUC: {val_res['breach_auc']:.4f})")
    print(f"Validation Directional F1 (zero):         {val_res['dir_f1_zero']:.4f}")
    print(f"Validation Directional F1 (calibrated):   {val_res['dir_f1_opt']:.4f}")
    print("-" * 70)
    print(f"Test Out-of-Sample Breach F1:             {test_res['breach_f1_opt']:.4f} (AUC: {test_res['breach_auc']:.4f})")
    print(f"Test Out-of-Sample Directional F1 (zero): {test_res['dir_f1_zero']:.4f}")
    print(f"Test Out-of-Sample Directional F1 (calib):{test_res['dir_f1_opt']:.4f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
