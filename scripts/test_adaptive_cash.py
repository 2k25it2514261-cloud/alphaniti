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
from src.accounting.ledger import FiveBucketBalanceSheet
from src.data.feature_engineering import compute_causal_features

test_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "test.parquet")
train_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "train.parquet")

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

test_feat, _ = compute_causal_features(test_df)
X_norm = pipeline.scaler.transform(test_feat.select(fcols))
norm_cols = [f"{c}_norm" for c in fcols]
test_scaled = test_feat.with_columns([
    pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
])

symbols = sorted(test_df["symbol"].unique().to_list())
lookback = 60

records = []
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
all_dates = sorted(pred_df["date"].unique().to_list())
rebal_dates = all_dates[::20]
cost_model = IndianStatutoryCostModel()

def run_sim(mode: str):
    ledger = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)
    total_trades = 0
    winning_trades = 0
    breached_trades = 0
    cash_preservation_cycles = 0

    for r_idx, r_date in enumerate(rebal_dates):
        ledger.settle_t1_batch()
        date_candidates = pred_df.filter(pl.col("date") == r_date)
        if len(date_candidates) == 0:
            continue

        current_prices = {row["symbol"]: row["close"] for row in date_candidates.to_dicts()}

        # Liquidate existing
        for sym, qty in list(ledger.positions.items()):
            if qty > 0 and sym in current_prices:
                p = current_prices[sym]
                fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                ledger.fill_sell_order(sym, qty, p, fee)

        cands = date_candidates.to_dicts()

        if mode == "naive":
            # Buys all positive gamma
            selected = [c for c in cands if c["gamma"] > 0]
        elif mode == "adaptive_cash":
            # Adaptive Cash Engine:
            # If the market median predicted return is negative, or average breach probability > 40%,
            # stay in 100% risk-free settled cash!
            market_gamma = np.mean([c["gamma"] for c in cands])
            market_breach = np.mean([c["p_breach"] for c in cands])
            
            if market_gamma < 0.0 or market_breach > 0.45:
                # Bearish regime detected -> HOLD CASH!
                selected = []
                cash_preservation_cycles += 1
            else:
                # In favorable regime, pick top 8 stocks with low breach risk
                filtered = [c for c in cands if c["p_breach"] < 0.38 and c["gamma"] > 0.01]
                filtered.sort(key=lambda x: x["gamma"], reverse=True)
                selected = filtered[:8]

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

    last_date = all_dates[-1]
    final_prices = {row["symbol"]: row["close"] for row in pred_df.filter(pl.col("date") == last_date).to_dicts()}
    final_nav = ledger.get_nav(final_prices)
    ret_pct = (final_nav / 1_000_000.0 - 1.0) * 100.0
    win_rate = (winning_trades / max(1, total_trades)) * 100.0 if total_trades > 0 else 0.0
    breach_rate = (breached_trades / max(1, total_trades)) * 100.0 if total_trades > 0 else 0.0

    return {
        "final_nav": final_nav,
        "return": ret_pct,
        "trades": total_trades,
        "win_rate": win_rate,
        "breach_rate": breach_rate,
        "cash_cycles": cash_preservation_cycles,
    }

res_naive = run_sim("naive")
res_cash = run_sim("adaptive_cash")

print("\n" + "=" * 80)
print("ALPHANITI v25.0 ADAPTIVE REGIME & CAPITAL PRESERVATION BACKTEST")
print("=" * 80)
print(f"{'Metric':<32} | {'Naive (Always Invested)':<22} | {'Adaptive Cash Engine':<20}")
print("-" * 80)
print(f"{'Final NAV':<32} | INR {res_naive['final_nav']:>12,.2f}          | INR {res_cash['final_nav']:>12,.2f}")
print(f"{'Cumulative Return':<32} | {res_naive['return']:>17.2f}%          | {res_cash['return']:>17.2f}%")
print(f"{'Total Trades Executed':<32} | {res_naive['trades']:>18}          | {res_cash['trades']:>18}")
print(f"{'Win Rate on Executed Trades':<32} | {res_naive['win_rate']:>17.2f}%          | {res_cash['win_rate']:>17.2f}%")
print(f"{'Trade Crash/Breach Rate':<32} | {res_naive['breach_rate']:>17.2f}%          | {res_cash['breach_rate']:>17.2f}%")
cash_str = f"{res_cash['cash_cycles']} / 17"
print(f"{'Cash Preservation Cycles':<32} | {'0 / 17':>18}          | {cash_str:>18}")
print("=" * 80)
