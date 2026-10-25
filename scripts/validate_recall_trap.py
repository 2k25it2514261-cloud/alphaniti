import sys
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import (
    confusion_matrix,
    classification_report,
    matthews_corrcoef,
    balanced_accuracy_score,
    roc_auc_score,
    precision_recall_curve,
    f1_score,
    precision_score,
    recall_score
)

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

def collect(loader):
    y_r, y_c, g, pb = [], [], [], []
    with torch.no_grad():
        for X, yr, yc in loader:
            gam, _, _, p = ens.predict(X)
            y_r.extend(yr.numpy().flatten())
            y_c.extend(yc.numpy().flatten())
            g.extend(gam.flatten())
            pb.extend(p.flatten())
    return np.array(y_r), np.array(y_c), np.array(g), np.array(pb)

print("Extracting test set predictions...")
y_r, y_c, g, pb = collect(test_loader)

# Look at Directional Return: y_true = 1 if return > 0
y_true_dir = (y_r > 0).astype(int)
print(f"Test Set Total Samples: {len(y_true_dir)}")
print(f"Actual Positive Count:  {y_true_dir.sum()} ({y_true_dir.mean():.2%})")
print(f"Actual Negative Count:  {(1 - y_true_dir).sum()} ({(1 - y_true_dir).mean():.2%})")

print("\n" + "=" * 75)
print("CONFUSION MATRIX ACROSS DIFFERENT PREDICTION THRESHOLDS ON TEST SET")
print("=" * 75)
print(f"{'Threshold (gamma)':<18} | {'TP':<6} {'FP':<6} {'TN':<6} {'FN':<6} | {'Prec':<7} {'Rec':<7} {'F1':<7} | {'Bal Acc':<7} {'MCC':<7}")
print("-" * 75)

for th in [-0.04, -0.02, -0.01, 0.00, 0.005, 0.01, 0.015, 0.02, 0.03]:
    pred = (g > th).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true_dir, pred).ravel()
    prec = precision_score(y_true_dir, pred, zero_division=0)
    rec = recall_score(y_true_dir, pred, zero_division=0)
    f1 = f1_score(y_true_dir, pred, zero_division=0)
    b_acc = balanced_accuracy_score(y_true_dir, pred)
    mcc = matthews_corrcoef(y_true_dir, pred)
    print(f"gamma > {th:>6.4f}     | {tp:<6} {fp:<6} {tn:<6} {fn:<6} | {prec:.2%}  {rec:.2%}  {f1:.4f}  | {b_acc:.2%}   {mcc:+.4f}")

print("\n" + "=" * 75)
print("DOWNSIDE TAIL BREACH (>5% DRAWDOWN) CONFUSION MATRIX")
print("=" * 75)
print(f"{'Threshold (pb)':<18} | {'TP':<6} {'FP':<6} {'TN':<6} {'FN':<6} | {'Prec':<7} {'Rec':<7} {'F1':<7} | {'Bal Acc':<7} {'MCC':<7}")
print("-" * 75)

for tau in [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]:
    pred_b = (pb >= tau).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_c, pred_b).ravel()
    prec = precision_score(y_c, pred_b, zero_division=0)
    rec = recall_score(y_c, pred_b, zero_division=0)
    f1 = f1_score(y_c, pred_b, zero_division=0)
    b_acc = balanced_accuracy_score(y_c, pred_b)
    mcc = matthews_corrcoef(y_c, pred_b)
    print(f"pb >= {tau:>6.2f}       | {tp:<6} {fp:<6} {tn:<6} {fn:<6} | {prec:.2%}  {rec:.2%}  {f1:.4f}  | {b_acc:.2%}   {mcc:+.4f}")
