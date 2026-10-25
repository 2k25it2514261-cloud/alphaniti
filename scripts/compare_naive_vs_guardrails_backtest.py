import sys
from pathlib import Path
import polars as pl
import pandas as pd
import numpy as np
import torch

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.portfolio.integer_sizer import LatticeBranchAndBoundSolver
from src.accounting.ledger import FiveBucketBalanceSheet

print("Loading data...")
train_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "train.parquet")
val_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "val.parquet")
test_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "test.parquet")

_, _, test_loader, pipeline, fcols = create_dataloaders(
    train_parquet_path=str(PROJECT_ROOT / "data" / "splits" / "train.parquet"),
    val_parquet_path=str(PROJECT_ROOT / "data" / "splits" / "val.parquet"),
    test_parquet_path=str(PROJECT_ROOT / "data" / "splits" / "test.parquet"),
    lookback=60,
    batch_size=256,
)

ckpt = torch.load(PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt", weights_only=True, map_location="cpu")
models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
for i, m in enumerate(models):
    m.load_state_dict(ckpt[f"member_{i}"])
    m.eval()
ens = EvidentialEnsemble(models)

# Reconstruct test sequences with symbol and date
# We slice test_df per symbol so we know exactly which symbol and date each prediction corresponds to!
from src.data.feature_engineering import compute_causal_features

test_feat, _ = compute_causal_features(test_df)
X_norm = pipeline.scaler.transform(test_feat.select(fcols))
norm_cols = [f"{c}_norm" for c in fcols]
test_scaled = test_feat.with_columns([
    pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
])

# For each symbol and date, compute predictions
symbols = sorted(test_df["symbol"].unique().to_list())
lookback = 60

records = []
print(f"Generating Evidential signals across {len(symbols)} symbols in out-of-sample test partition...")

with torch.no_grad():
    for sym in symbols:
        sub = test_scaled.filter(pl.col("symbol") == sym).sort("date")
        n_rows = len(sub)
        if n_rows >= lookback:
            mat = sub.select(norm_cols).to_numpy()
            dates = sub["date"].to_list()
            closes = sub["close"].to_list()
            y_rets = sub["target_ret_20d_net"].to_list() if "target_ret_20d_net" in sub.columns else [0.0]*n_rows
            y_breaches = sub["target_breach_5pct"].to_list() if "target_breach_5pct" in sub.columns else [0]*n_rows
            
            # Batch sequence creation for speed
            seqs = [mat[t - lookback + 1 : t + 1] for t in range(lookback - 1, n_rows)]
            t_dates = [dates[t] for t in range(lookback - 1, n_rows)]
            t_closes = [closes[t] for t in range(lookback - 1, n_rows)]
            t_y_rets = [y_rets[t] for t in range(lookback - 1, n_rows)]
            t_y_b = [y_breaches[t] for t in range(lookback - 1, n_rows)]
            
            batch_tensor = torch.tensor(np.array(seqs), dtype=torch.float32)
            gamma, aleatoric, epistemic, p_breach = ens.predict(batch_tensor)
            
            for i in range(len(seqs)):
                records.append({
                    "date": t_dates[i],
                    "symbol": sym,
                    "close": t_closes[i],
                    "gamma": float(gamma[i][0]),
                    "aleatoric": float(aleatoric[i][0]),
                    "epistemic": float(epistemic[i][0]),
                    "p_breach": float(p_breach[i][0]),
                    "realized_ret_20d": float(t_y_rets[i]),
                    "realized_breach": int(t_y_b[i]),
                })

pred_df = pl.DataFrame(records)
print(f"Total scored symbol-date signals: {len(pred_df):,}")

# Unique dates for walk-forward simulation
all_dates = sorted(pred_df["date"].unique().to_list())
rebalance_interval = 20
rebal_dates = all_dates[::rebalance_interval]
print(f"Total rebalance cycles: {len(rebal_dates)}")

cost_model = IndianStatutoryCostModel()

def simulate_strategy(strategy_name: str, use_guardrails: bool):
    ledger = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)
    sizer = LatticeBranchAndBoundSolver(cost_model=cost_model, max_asset_weight=0.25, max_nodes=150)
    
    total_trades = 0
    winning_trades = 0
    breached_trades = 0
    
    for r_idx, r_date in enumerate(rebal_dates):
        ledger.settle_t1_batch()
        date_candidates = pred_df.filter(pl.col("date") == r_date)
        if len(date_candidates) == 0:
            continue
            
        current_prices = {row["symbol"]: row["close"] for row in date_candidates.to_dicts()}
        current_nav = ledger.get_nav(current_prices)
        
        # Liquidate existing holdings on rebalance date
        for sym, qty in list(ledger.positions.items()):
            if qty > 0 and sym in current_prices:
                p = current_prices[sym]
                fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                ledger.fill_sell_order(sym, qty, p, fee)
                
        # Candidate filtering
        cands = date_candidates.to_dicts()
        selected = []
        
        if not use_guardrails:
            # Naive approach: anything with gamma > 0
            selected = [c for c in cands if c["gamma"] > 0]
        else:
            # Institutional Guardrails:
            # Guardrail 1: Veto crash-prone stocks (p_breach < 0.35)
            # Guardrail 2: Filter out high epistemic uncertainty (below 75th percentile)
            med_epi = np.median(pred_df["epistemic"])
            filtered = [c for c in cands if c["p_breach"] < 0.35 and c["epistemic"] <= med_epi * 1.5]
            # Guardrail 3: Rank by Sharpe ratio (gamma / sqrt(aleatoric)) and take Top-5
            filtered.sort(key=lambda x: x["gamma"] / (np.sqrt(x["aleatoric"]) + 1e-4), reverse=True)
            selected = filtered[:5]
            
        budget = ledger.get_buying_power() * 0.98
        if len(selected) > 0 and budget > 10_000:
            alloc_budget = budget / len(selected)
            for cand in selected:
                sym = cand["symbol"]
                p = cand["close"]
                max_sh = int(alloc_budget // (p * 1.002))
                if max_sh > 0:
                    fee = cost_model.compute_cost(shares=max_sh, price=p, is_buy=True)
                    if ledger.get_buying_power() >= (max_sh * p + fee):
                        oid = f"ORD_{r_idx}_{sym}"
                        ledger.dispatch_buy_order(oid, sym, max_sh, p, fee)
                        ledger.fill_buy_order(oid, p, fee)
                        total_trades += 1
                        if cand["realized_ret_20d"] > 0:
                            winning_trades += 1
                        if cand["realized_breach"] == 1:
                            breached_trades += 1
                            
    # Final liquidation at end of backtest period
    last_date = all_dates[-1]
    last_candidates = pred_df.filter(pl.col("date") == last_date).to_dicts()
    final_prices = {row["symbol"]: row["close"] for row in last_candidates}
    final_nav = ledger.get_nav(final_prices)
    ret_pct = (final_nav / 1_000_000.0 - 1.0) * 100.0
    win_rate = (winning_trades / max(1, total_trades)) * 100.0
    breach_rate = (breached_trades / max(1, total_trades)) * 100.0
    
    return {
        "Strategy": strategy_name,
        "Final NAV (INR)": final_nav,
        "Total Return": ret_pct,
        "Total Executions": total_trades,
        "Win Rate": win_rate,
        "Crash/Breach Rate": breach_rate,
    }

print("\nRunning Backtest for Strategy A (Naive Buy)...")
res_naive = simulate_strategy("Strategy A: Naive Model (Raw Return > 0)", use_guardrails=False)

print("Running Backtest for Strategy B (Institutional Guardrails)...")
res_guardrails = simulate_strategy("Strategy B: Evidential Guardrails (Tail Veto + Top-K)", use_guardrails=True)

print("\n" + "=" * 80)
print("OUT-OF-SAMPLE HEAD-TO-HEAD WALK-FORWARD BACKTEST RESULTS")
print("=" * 80)
print(f"{'Performance Metric':<28} | {'Strategy A (Naive)':<22} | {'Strategy B (Guardrails)':<24}")
print("-" * 80)
print(f"{'Starting Capital':<28} | {'INR 1,000,000.00':<22} | {'INR 1,000,000.00':<24}")
print(f"{'Ending Capital (NAV)':<28} | INR {res_naive['Final NAV (INR)']:>12,.2f}          | INR {res_guardrails['Final NAV (INR)']:>14,.2f}")
print(f"{'Net Cumulative Return':<28} | {res_naive['Total Return']:>17.2f}%          | {res_guardrails['Total Return']:>19.2f}%")
print(f"{'Total Executions':<28} | {res_naive['Total Executions']:>18}          | {res_guardrails['Total Executions']:>20}")
print(f"{'Trade Win Rate':<28} | {res_naive['Win Rate']:>17.2f}%          | {res_guardrails['Win Rate']:>19.2f}%")
print(f"{'Drawdown Crash Rate':<28} | {res_naive['Crash/Breach Rate']:>17.2f}%          | {res_guardrails['Crash/Breach Rate']:>19.2f}%")
print("=" * 80)
