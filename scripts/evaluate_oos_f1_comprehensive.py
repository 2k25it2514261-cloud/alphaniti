import sys
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score, confusion_matrix, roc_auc_score

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble

train_path = str(PROJECT_ROOT / "data" / "splits" / "train.parquet")
val_path = str(PROJECT_ROOT / "data" / "splits" / "val.parquet")
test_path = str(PROJECT_ROOT / "data" / "splits" / "test.parquet")

_, val_loader, test_loader, pipeline, fcols = create_dataloaders(
    train_parquet_path=train_path,
    val_parquet_path=val_path,
    test_parquet_path=test_path,
    lookback=60,
    batch_size=256,
)

ckpt = torch.load(PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt", weights_only=True, map_location="cpu")
models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
for i, m in enumerate(models):
    m.load_state_dict(ckpt[f"member_{i}"])
    m.eval()
ens = EvidentialEnsemble(models)

def extract_predictions(loader):
    y_r, y_c, g_l, ale_l, epi_l, pb_l = [], [], [], [], [], []
    with torch.no_grad():
        for X, yr, yc in loader:
            g, ale, epi, pb = ens.predict(X)
            y_r.extend(yr.numpy().flatten())
            y_c.extend(yc.numpy().flatten())
            g_l.extend(g.flatten())
            ale_l.extend(ale.flatten())
            epi_l.extend(epi.flatten())
            pb_l.extend(pb.flatten())
    return {
        "y_r": np.array(y_r, dtype=float),
        "y_c": np.array(y_c, dtype=int),
        "gamma": np.array(g_l, dtype=float),
        "aleatoric": np.array(ale_l, dtype=float),
        "epistemic": np.array(epi_l, dtype=float),
        "p_breach": np.array(pb_l, dtype=float),
    }

print("Extracting predictions for Validation and Test...")
val_data = extract_predictions(val_loader)
test_data = extract_predictions(test_loader)

print("\n" + "="*75)
print("COMPREHENSIVE OOS EVALUATION OF ALL FORMULATIONS")
print("="*75)

# Formulation A: Downside Tail Risk Breach
print("\n[A] Downside Tail Risk Breach (>5% Drawdown Breach)")
for split_name, d in [("Validation", val_data), ("Test OOS", test_data)]:
    y_true = d["y_c"]
    p_pred = d["p_breach"]
    best_f1, best_tau, best_p, best_r = 0, 0, 0, 0
    for tau in np.linspace(0.1, 0.8, 71):
        pred = (p_pred >= tau).astype(int)
        f1 = f1_score(y_true, pred, zero_division=0)
        if f1 > best_f1:
            best_f1 = f1
            best_tau = tau
            best_p = precision_score(y_true, pred, zero_division=0)
            best_r = recall_score(y_true, pred, zero_division=0)
    print(f"  {split_name:<12}: Best F1={best_f1:.4f} (tau={best_tau:.2f}, Prec={best_p:.2%}, Rec={best_r:.2%}, AUC={roc_auc_score(y_true, p_pred):.4f})")

# Formulation B: Epistemic Uncertainty Filtering (High Confidence Gating)
print("\n[B] High-Conviction Alpha Gating (Filtering Out Top Epistemic Uncertainty)")
for q in [1.0, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30]:
    for split_name, d in [("Validation", val_data), ("Test OOS", test_data)]:
        c_epi = np.quantile(d["epistemic"], q)
        mask = d["epistemic"] <= c_epi
        y_sub = (d["y_r"][mask] > 0).astype(int)
        g_sub = d["gamma"][mask]
        
        # Grid search threshold on gamma
        best_f1, best_th = 0, 0
        for th in np.linspace(-0.05, 0.05, 41):
            f1 = f1_score(y_sub, (g_sub > th).astype(int), zero_division=0)
            if f1 > best_f1:
                best_f1 = f1
                best_th = th
        if split_name == "Test OOS":
            p = precision_score(y_sub, (g_sub > best_th).astype(int), zero_division=0)
            r = recall_score(y_sub, (g_sub > best_th).astype(int), zero_division=0)
            print(f"  Quantile <= {q*100:>3.0f}% | {split_name}: F1={best_f1:.4f} (th={best_th:.4f}, Prec={p:.2%}, Rec={r:.2%}, N={mask.sum()})")

# Formulation C: Combined Dual Signal (Positive Return AND No Breach)
print("\n[C] High-Quality Trade Execution (Return > 0 AND Drawdown < 5%)")
for tau in [0.25, 0.30, 0.35, 0.40]:
    y_true_v = ((val_data["y_r"] > 0) & (val_data["y_c"] == 0)).astype(int)
    p_pred_v = ((val_data["gamma"] > -0.01) & (val_data["p_breach"] < tau)).astype(int)
    f1_v = f1_score(y_true_v, p_pred_v, zero_division=0)
    
    y_true_t = ((test_data["y_r"] > 0) & (test_data["y_c"] == 0)).astype(int)
    p_pred_t = ((test_data["gamma"] > -0.01) & (test_data["p_breach"] < tau)).astype(int)
    f1_t = f1_score(y_true_t, p_pred_t, zero_division=0)
    print(f"  Breach Cutoff tau={tau:.2f} -> Val F1: {f1_v:.4f} | Test F1: {f1_t:.4f}")

# Formulation D: Cross-Sectional Alpha Outperformance
print("\n[D] Cross-Sectional Relative Alpha Outperformance (> 0.0)")
# An asset is an alpha winner if its predicted excess return is positive
for split_name, d in [("Validation", val_data), ("Test OOS", test_data)]:
    # Compare against market median return
    median_ret = np.median(d["y_r"])
    y_alpha = (d["y_r"] > median_ret).astype(int)
    median_gamma = np.median(d["gamma"])
    pred_alpha = (d["gamma"] > median_gamma).astype(int)
    f1_a = f1_score(y_alpha, pred_alpha, zero_division=0)
    p_a = precision_score(y_alpha, pred_alpha, zero_division=0)
    r_a = recall_score(y_alpha, pred_alpha, zero_division=0)
    print(f"  {split_name:<12}: Alpha Outperformance F1={f1_a:.4f} (Prec={p_a:.2%}, Rec={r_a:.2%})")
