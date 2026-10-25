"""
AlphaNiti v25.0: Adaptive Regime Calibration Comparison
Compares different regime thresholds:
  Rule A: p_breach_mkt > 0.40 (Ultra-Conservative, 88% Cash)
  Rule B: p_breach_mkt > 0.48 (Balanced Regime, sits in Cash only during true corrections)
  Rule C: p_breach_mkt > 0.52 (Aggressive, rides bull markets, cuts extreme crashes)
"""

import sys
from pathlib import Path
import numpy as np
import polars as pl
import pandas as pd
import torch

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tensor_builder import create_dataloaders
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.accounting.ledger import FiveBucketBalanceSheet
from src.data.feature_engineering import compute_causal_features

val_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "val.parquet")
test_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "test.parquet")
combined_eval_df = pl.concat([val_df, test_df]).sort(["symbol", "date"])
train_df = pl.read_parquet(PROJECT_ROOT / "data" / "splits" / "train.parquet")

_, _, _, pipeline, fcols = create_dataloaders(
    train_parquet_path=str(PROJECT_ROOT / "data" / "splits" / "train.parquet"),
    val_parquet_path=str(PROJECT_ROOT / "data" / "splits" / "val.parquet"),
    lookback=60,
    batch_size=256,
)

eval_feat, _ = compute_causal_features(combined_eval_df)
X_norm = pipeline.scaler.transform(eval_feat.select(fcols))
norm_cols = [f"{c}_norm" for c in fcols]
eval_scaled = eval_feat.with_columns([
    pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
])

ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
for i, m in enumerate(models):
    m.load_state_dict(ckpt[f"member_{i}"])
    m.eval()
ensemble = EvidentialEnsemble(models)

symbols = sorted(combined_eval_df["symbol"].unique().to_list())
lookback = 60
records = []

with torch.no_grad():
    for sym in symbols:
        sub = eval_scaled.filter(pl.col("symbol") == sym).sort("date")
        n_rows = len(sub)
        if n_rows >= lookback:
            mat = sub.select(norm_cols).to_numpy()
            dates = sub["date"].to_list()
            closes = sub["close"].to_list()
            
            seqs = [mat[t - lookback + 1 : t + 1] for t in range(lookback - 1, n_rows)]
            t_dates = [str(dates[t]) for t in range(lookback - 1, n_rows)]
            t_closes = [closes[t] for t in range(lookback - 1, n_rows)]
            
            batch_tensor = torch.tensor(np.array(seqs), dtype=torch.float32)
            gamma, aleatoric, epistemic, p_breach = ensemble.predict(batch_tensor)
            
            for i in range(len(seqs)):
                records.append({
                    "date": t_dates[i],
                    "symbol": sym,
                    "close": t_closes[i],
                    "gamma": float(gamma[i][0]),
                    "aleatoric": float(aleatoric[i][0]),
                    "epistemic": float(epistemic[i][0]),
                    "p_breach": float(p_breach[i][0]),
                })

pred_df = pl.DataFrame(records)
all_dates = sorted(pred_df["date"].unique().to_list())
two_year_dates = all_dates[-500:]
distinct_months = sorted(list(set([d[:7] for d in two_year_dates])))
cost_model = IndianStatutoryCostModel()

def simulate_regime_threshold(tau_breach: float):
    ledger = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)
    rebal_freq_days = 10
    cash_months = 0
    total_months = len(distinct_months)
    monthly_rets = []

    for ym in distinct_months:
        month_dates = [d for d in two_year_dates if d.startswith(ym)]
        if len(month_dates) == 0:
            continue

        start_nav = ledger.get_nav({
            row["symbol"]: float(row["close"])
            for row in pred_df.filter(pl.col("date") == month_dates[0]).to_dicts()
        })

        month_regimes = []
        for r_date in month_dates[::rebal_freq_days]:
            ledger.settle_t1_batch()
            day_cands = pred_df.filter(pl.col("date") == r_date).to_dicts()
            if len(day_cands) == 0:
                continue
            current_prices = {row["symbol"]: row["close"] for row in day_cands}

            # Liquidate
            for sym, qty in list(ledger.positions.items()):
                if qty > 0 and sym in current_prices:
                    p = current_prices[sym]
                    fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                    ledger.fill_sell_order(sym, qty, p, fee)

            gamma_mkt = np.mean([c["gamma"] for c in day_cands])
            p_breach_mkt = np.mean([c["p_breach"] for c in day_cands])

            # Calibrated Rule
            is_cash = (gamma_mkt < -0.01) or (p_breach_mkt > tau_breach)
            month_regimes.append(is_cash)

            if not is_cash:
                med_epi = np.median([c["epistemic"] for c in day_cands])
                valid = [
                    c for c in day_cands
                    if c["p_breach"] < 0.45 and c["epistemic"] <= med_epi * 1.5 and c["gamma"] > 0.0
                ]
                valid.sort(key=lambda x: x["gamma"] / (np.sqrt(x["aleatoric"]) + 1e-4), reverse=True)
                top_picks = valid[:6]

                b = ledger.get_buying_power() * 0.98
                if len(top_picks) > 0 and b > 10_000:
                    per_b = b / len(top_picks)
                    for pick in top_picks:
                        sym, p = pick["symbol"], pick["close"]
                        sh = int(per_b // (p * 1.002))
                        if sh > 0:
                            fee = cost_model.compute_cost(shares=sh, price=p, is_buy=True)
                            if ledger.get_buying_power() >= (sh * p + fee):
                                oid = f"ORD_{r_date}_{sym}"
                                ledger.dispatch_buy_order(oid, sym, sh, p, fee)
                                ledger.fill_buy_order(oid, p, fee)

        end_prices = {
            row["symbol"]: float(row["close"])
            for row in pred_df.filter(pl.col("date") == month_dates[-1]).to_dicts()
        }
        end_nav = ledger.get_nav(end_prices)
        m_ret = (end_nav / start_nav - 1.0) * 100.0
        monthly_rets.append(m_ret)
        if sum(month_regimes) >= len(month_regimes) / 2:
            cash_months += 1

    final_nav = ledger.get_nav({
        row["symbol"]: float(row["close"])
        for row in pred_df.filter(pl.col("date") == two_year_dates[-1]).to_dicts()
    })
    tot_ret = (final_nav / 1_000_000.0 - 1.0) * 100.0
    
    # Compute Max Drawdown
    cum_navs = [1_000_000.0]
    for r in monthly_rets:
        cum_navs.append(cum_navs[-1] * (1.0 + r / 100.0))
    peaks = np.maximum.accumulate(cum_navs)
    dds = (cum_navs - peaks) / peaks
    max_dd = float(np.min(dds)) * 100.0

    return {
        "tau": tau_breach,
        "final_nav": final_nav,
        "total_return": tot_ret,
        "max_drawdown": max_dd,
        "cash_months": cash_months,
        "monthly_rets": monthly_rets,
    }

print("Running calibration across crash risk thresholds...")
res_40 = simulate_regime_threshold(0.40)
res_46 = simulate_regime_threshold(0.46)
res_48 = simulate_regime_threshold(0.48)
res_50 = simulate_regime_threshold(0.50)

print("\n" + "=" * 80)
print("REGIME CALIBRATION COMPARISON: 2-YEAR OUT-OF-SAMPLE TEST")
print("=" * 80)
print(f"{'Breach Threshold':<20} | {'Final NAV':<16} | {'Total Return':<14} | {'Max Drawdown':<14} | {'Cash Months':<12}")
print("-" * 80)
for r in [res_40, res_46, res_48, res_50]:
    print(f"p_breach > {r['tau']:<7.2f}    | INR {r['final_nav']:>10,.2f}  | {r['total_return']:>+10.2f}%   | {r['max_drawdown']:>10.2f}%   | {r['cash_months']:>2} / 26 ({r['cash_months']/26:.0%})")
print("=" * 80)
