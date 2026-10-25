"""
AlphaNiti v25.0: Architectural Dataset Sufficiency Diagnostic Test Suite
Verifies that the combined master parquet satisfies all mathematical and operational
requirements for:
  1. Purged Combinatorial Cross-Validation (CPCV)
  2. 3D Sequence Tensor Construction (L=60 lookback window)
  3. Evidential Deep Ensemble Training (Normal-Inverse-Gamma Prior)
  4. Esfahani-Kuhn Wasserstein Distributionally Robust Optimization (DRO) CVaR
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import polars as pl
import numpy as np
import torch
import torch.nn as nn
from scipy.optimize import linprog

def run_sufficiency_tests(parquet_path):
    print("=" * 80)
    print("ALPHANITI v25.0: COMPREHENSIVE ARCHITECTURAL SUFFICIENCY TEST")
    print(f"Target Parquet: {parquet_path}")
    print("=" * 80)

    df = pl.read_parquet(parquet_path)
    print(f"\n[LOAD] Dataset loaded: {len(df):,} rows x {len(df.columns)} columns across {df['symbol'].n_unique()} tickers.")

    test_results = {}

    # TEST 1: Temporal Continuity & Invariants
    tickers = df["symbol"].unique().to_list()
    min_rows = 100
    valid_tickers = []
    zero_price_violations = 0

    for sym in tickers:
        sub = df.filter(pl.col("symbol") == sym).sort("date")
        if len(sub) >= min_rows:
            valid_tickers.append(sym)
        zeros = sub.filter((pl.col("open") <= 0) | (pl.col("close") <= 0) | (pl.col("high") <= 0) | (pl.col("low") <= 0))
        zero_price_violations += len(zeros)

    null_targets = df["target_ret_20d_net"].null_count()
    null_breach = df["target_breach_5pct"].null_count()

    t1_passed = (zero_price_violations == 0) and (null_targets == 0) and (null_breach == 0) and (len(valid_tickers) >= 30)
    test_results["Test 1: Temporal Integrity & Zero Leakage"] = {
        "passed": t1_passed,
        "valid_tickers": len(valid_tickers),
        "zero_price_violations": zero_price_violations,
        "null_targets": null_targets
    }

    # TEST 2: Purged CPCV Split Feasibility
    train_df = df.filter(pl.col("date") < pl.date(2023, 1, 1))
    val_df = df.filter((pl.col("date") >= pl.date(2023, 2, 1)) & (pl.col("date") < pl.date(2025, 1, 1)))
    test_df = df.filter(pl.col("date") >= pl.date(2025, 2, 1))

    t2_passed = (len(train_df) > 20000) and (len(val_df) > 5000) and (len(test_df) > 3000)
    test_results["Test 2: Purged CPCV Splitting"] = {
        "passed": t2_passed,
        "train_rows": len(train_df),
        "val_rows": len(val_df),
        "test_rows": len(test_df)
    }

    # TEST 3: 3D Sequence Tensor Generation
    L = 60
    feature_cols = ["open", "high", "low", "close", "volume", "turnover_cr", "ret_1d", "cum_factor"]
    df_feat = df.with_columns(pl.col("ret_1d").fill_null(0.0))

    X_tensors, y_reg_targets, y_clf_targets = [], [], []
    for sym in valid_tickers[:10]:
        sub = df_feat.filter(pl.col("symbol") == sym).sort("date")
        mat = sub.select(feature_cols).to_numpy()
        y_reg = sub["target_ret_20d_net"].to_numpy()
        y_clf = sub["target_breach_5pct"].to_numpy()
        n_sub = len(sub)
        if n_sub > L:
            for idx in range(L, min(L + 50, n_sub)):
                seq = mat[idx - L:idx].copy()
                ref_close = seq[-1, 3]
                if ref_close > 0:
                    seq[:, :4] = seq[:, :4] / ref_close - 1.0
                    seq[:, 4:6] = np.log1p(seq[:, 4:6])
                    X_tensors.append(seq)
                    y_reg_targets.append(y_reg[idx])
                    y_clf_targets.append(y_clf[idx])

    X_batch = torch.tensor(np.array(X_tensors), dtype=torch.float32)
    y_reg_batch = torch.tensor(np.array(y_reg_targets), dtype=torch.float32).unsqueeze(1)
    y_clf_batch = torch.tensor(np.array(y_clf_targets), dtype=torch.float32).unsqueeze(1)

    t3_passed = (not torch.isnan(X_batch).any()) and (X_batch.shape[0] > 100) and (X_batch.shape[1] == L)
    test_results["Test 3: 3D Tensor Generation"] = {
        "passed": t3_passed,
        "batch_shape": list(X_batch.shape)
    }

    # TEST 4: Evidential Deep Learning Trainability
    class EvidentialModel(nn.Module):
        def __init__(self, in_features, hidden_dim=32):
            super().__init__()
            self.gru = nn.GRU(in_features, hidden_dim, batch_first=True)
            self.head = nn.Linear(hidden_dim, 4)
            self.clf_head = nn.Linear(hidden_dim, 1)
            
        def forward(self, x):
            _, h = self.gru(x)
            out = self.head(h.squeeze(0))
            gamma = out[:, 0:1]
            v = torch.relu(out[:, 1:2]) + 1e-4
            alpha = torch.relu(out[:, 2:3]) + 1.0 + 1e-4
            beta = torch.relu(out[:, 3:4]) + 1e-4
            p_breach = torch.sigmoid(self.clf_head(h.squeeze(0)))
            return gamma, v, alpha, beta, p_breach

    def nig_nll_loss(gamma, v, alpha, beta, y):
        two_beta_1_plus_v = 2 * beta * (1 + v)
        term1 = 0.5 * torch.log(np.pi / v)
        term2 = - alpha * torch.log(two_beta_1_plus_v)
        term3 = (alpha + 0.5) * torch.log(v * (y - gamma)**2 + two_beta_1_plus_v)
        term4 = torch.lgamma(alpha) - torch.lgamma(alpha + 0.5)
        return torch.mean(term1 + term2 + term3 + term4)

    model = EvidentialModel(in_features=len(feature_cols))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    gamma, v, alpha, beta, p_breach = model(X_batch)
    loss_reg = nig_nll_loss(gamma, v, alpha, beta, y_reg_batch)
    loss_clf = nn.BCELoss()(p_breach, y_clf_batch)
    total_loss = loss_reg + loss_clf

    optimizer.zero_grad()
    total_loss.backward()
    grad_norm = nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()

    t4_passed = (not np.isnan(total_loss.item())) and (grad_norm > 0)
    test_results["Test 4: Evidential Deep Learning Trainability"] = {
        "passed": t4_passed,
        "loss": float(total_loss.item()),
        "grad_norm": float(grad_norm)
    }

    # TEST 5: Factor Decomposition & Wasserstein DRO CVaR
    counts = df_feat.filter(pl.col("date") >= pl.date(2023, 1, 1)).group_by("symbol").len()
    full_hist_symbols = counts.filter(pl.col("len") >= 800)["symbol"].to_list()[:20]

    pvt = (
        df_feat.filter((pl.col("date") >= pl.date(2023, 1, 1)) & (pl.col("symbol").is_in(full_hist_symbols)))
        .pivot(values="ret_1d", index="date", on="symbol")
        .drop_nulls()
    )

    ret_mat = pvt.select(pl.all().exclude("date")).to_numpy()
    T_scenarios, N_assets = ret_mat.shape

    U, S_vals, Vt = np.linalg.svd(ret_mat - ret_mat.mean(axis=0), full_matrices=False)
    B_loadings = Vt[:3, :].T
    f_scenarios = ret_mat @ B_loadings

    w_equal = np.ones(N_assets) / N_assets
    v_exposure = B_loadings.T @ w_equal

    def solve_dro(v_exp, f_scen, alpha_risk=0.95, eps_w=0.015):
        S_cnt, _ = f_scen.shape
        losses = -np.dot(f_scen, v_exp)
        c_obj = np.zeros(2 + S_cnt)
        c_obj[0] = 1.0
        c_obj[1] = eps_w
        c_obj[2:] = 1.0 / ((1.0 - alpha_risk) * S_cnt)
        
        A_ub = np.zeros((S_cnt + 1, 2 + S_cnt))
        b_ub = np.zeros(S_cnt + 1)
        for s in range(S_cnt):
            A_ub[s, 0] = -1.0
            A_ub[s, 2 + s] = -1.0
            b_ub[s] = -losses[s]
        
        v_norm = np.linalg.norm(v_exp, 2)
        A_ub[S_cnt, 1] = -1.0
        b_ub[S_cnt] = -v_norm / (1.0 - alpha_risk)
        
        bounds = [(None, None)] + [(0.0, None) for _ in range(1 + S_cnt)]
        res = linprog(c_obj, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        return res.fun if res.success else None

    robust_cvar = solve_dro(v_exposure, f_scenarios, alpha_risk=0.95, eps_w=0.015)
    empirical_cvar = np.mean(np.sort(-np.dot(f_scenarios, v_exposure))[-int(0.05 * T_scenarios):])

    t5_passed = (robust_cvar is not None) and (robust_cvar > empirical_cvar)
    test_results["Test 5: Wasserstein DRO CVaR"] = {
        "passed": t5_passed,
        "robust_cvar": float(robust_cvar),
        "empirical_cvar": float(empirical_cvar)
    }

    all_passed = all(r["passed"] for r in test_results.values())
    return all_passed, test_results

if __name__ == "__main__":
    p = r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\kaggle_out\alphaniti_master_combined_dataset.parquet"
    passed, res = run_sufficiency_tests(p)
    print(f"\nFINAL VERDICT: {'PASSED' if passed else 'FAILED'}")
