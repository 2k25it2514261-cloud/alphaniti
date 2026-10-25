"""
AlphaNiti v25.0: Adaptive Regime Switch & Cash Preservation 2-Year Backtest
Tests the explicit rule:
  If gamma_market < 0 OR p_breach_market > 0.40:
      Hold 100% Cash (W_settled)
  Else:
      Allocate capital to top-conviction alpha stocks

Evaluated month-by-month across 2 full years of out-of-training market data.
"""

import sys
import time
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

def main():
    print("=" * 85)
    print("ALPHANITI v25.0: 2-YEAR MONTH-BY-MONTH REGIME SWITCH & CASH PRESERVATION AUDIT")
    print("=" * 85)

    train_path = str(PROJECT_ROOT / "data" / "splits" / "train.parquet")
    val_path = str(PROJECT_ROOT / "data" / "splits" / "val.parquet")
    test_path = str(PROJECT_ROOT / "data" / "splits" / "test.parquet")

    # Load 2-year out-of-training dataset (Val + Test)
    val_df = pl.read_parquet(val_path)
    test_df = pl.read_parquet(test_path)
    combined_eval_df = pl.concat([val_df, test_df]).sort(["symbol", "date"])

    train_df = pl.read_parquet(train_path)

    # 1. Pipeline Feature Transformation
    print("\n[Stage 1/4] Engineering 20 causal features & normalizing via frozen train scaler...")
    _, _, _, pipeline, fcols = create_dataloaders(
        train_parquet_path=train_path,
        val_parquet_path=val_path,
        lookback=60,
        batch_size=256,
    )

    eval_feat, _ = compute_causal_features(combined_eval_df)
    X_norm = pipeline.scaler.transform(eval_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    eval_scaled = eval_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])

    # 2. Load Evidential Deep Ensemble
    print("[Stage 2/4] Loading pre-trained Evidential Ensemble...")
    ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
    for i, m in enumerate(models):
        m.load_state_dict(ckpt[f"member_{i}"])
        m.eval()
    ensemble = EvidentialEnsemble(models)

    # 3. Generate Predictions per symbol-date
    print("[Stage 3/4] Generating evidential inference across the 2-year evaluation horizon...")
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
                t_dates = [dates[t] for t in range(lookback - 1, n_rows)]
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
    
    # Filter for the last 2 years (e.g., last ~500 trading days)
    # 500 trading days ~ 2 calendar years
    two_year_dates = all_dates[-500:] if len(all_dates) >= 500 else all_dates
    print(f"  Evaluation Window: {two_year_dates[0]} to {two_year_dates[-1]} ({len(two_year_dates)} trading days)")

    # Convert dates to string format for consistent handling
    two_year_dates = [str(d) for d in two_year_dates]
    all_dates = [str(d) for d in all_dates]
    pred_df = pred_df.with_columns(pl.col("date").cast(pl.String))

    # 4. Group by Calendar Months
    distinct_months = sorted(list(set([d[:7] for d in two_year_dates])))
    print(f"  Total Distinct Calendar Months: {len(distinct_months)} months")

    # 5. Simulate Both Strategies Month-by-Month
    cost_model = IndianStatutoryCostModel()

    # Trackers for Strategy 1: Always Invested (Naive)
    ledger_naive = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)
    # Trackers for Strategy 2: Adaptive Regime Switch (Cash Preservation)
    ledger_adaptive = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)

    month_results = []
    
    rebal_freq_days = 10 # Rebalance every 10 trading days (~bi-weekly)

    for ym in distinct_months:
        month_dates = [d for d in two_year_dates if d.startswith(ym)]
        if len(month_dates) == 0:
            continue

        start_nav_naive = ledger_naive.get_nav({
            row["symbol"]: float(row["close"])
            for row in pred_df.filter(pl.col("date") == month_dates[0]).to_dicts()
        })
        start_nav_adaptive = ledger_adaptive.get_nav({
            row["symbol"]: float(row["close"])
            for row in pred_df.filter(pl.col("date") == month_dates[0]).to_dicts()
        })

        month_regimes = []

        # Run rebalances within this month
        rebal_days_month = month_dates[::rebal_freq_days]

        for r_date in rebal_days_month:
            ledger_naive.settle_t1_batch()
            ledger_adaptive.settle_t1_batch()

            day_cands = pred_df.filter(pl.col("date") == r_date).to_dicts()
            if len(day_cands) == 0:
                continue

            current_prices = {row["symbol"]: row["close"] for row in day_cands}

            # -----------------------------------------------------------------
            # 1. Update Strategy 1 (Naive Always-Invested)
            # -----------------------------------------------------------------
            for sym, qty in list(ledger_naive.positions.items()):
                if qty > 0 and sym in current_prices:
                    p = current_prices[sym]
                    fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                    ledger_naive.fill_sell_order(sym, qty, p, fee)

            naive_picks = [c for c in day_cands if c["gamma"] > 0][:5]
            naive_budget = ledger_naive.get_buying_power() * 0.98
            if len(naive_picks) > 0 and naive_budget > 10_000:
                per_b = naive_budget / len(naive_picks)
                for pick in naive_picks:
                    sym, p = pick["symbol"], pick["close"]
                    sh = int(per_b // (p * 1.002))
                    if sh > 0:
                        fee = cost_model.compute_cost(shares=sh, price=p, is_buy=True)
                        if ledger_naive.get_buying_power() >= (sh * p + fee):
                            oid = f"NAIVE_{r_date}_{sym}"
                            ledger_naive.dispatch_buy_order(oid, sym, sh, p, fee)
                            ledger_naive.fill_buy_order(oid, p, fee)

            # -----------------------------------------------------------------
            # 2. Update Strategy 2 (Adaptive Regime Switch / Cash Preservation)
            # -----------------------------------------------------------------
            for sym, qty in list(ledger_adaptive.positions.items()):
                if qty > 0 and sym in current_prices:
                    p = current_prices[sym]
                    fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                    ledger_adaptive.fill_sell_order(sym, qty, p, fee)

            # Evaluate Rule:
            gamma_mkt = np.mean([c["gamma"] for c in day_cands])
            p_breach_mkt = np.mean([c["p_breach"] for c in day_cands])

            is_cash_preservation = (gamma_mkt < 0.0) or (p_breach_mkt > 0.40)
            month_regimes.append("CASH" if is_cash_preservation else "INVEST")

            if is_cash_preservation:
                # Hold 100% Cash (W_settled) - zero stock buys!
                pass
            else:
                # Safe Bullish / Benign Regime: pick top 5 quality stocks
                med_epi = np.median([c["epistemic"] for c in day_cands])
                valid_stocks = [
                    c for c in day_cands
                    if c["p_breach"] < 0.38 and c["epistemic"] <= med_epi and c["gamma"] > 0.0
                ]
                valid_stocks.sort(key=lambda x: x["gamma"] / (np.sqrt(x["aleatoric"]) + 1e-4), reverse=True)
                top_stocks = valid_stocks[:5]

                adapt_budget = ledger_adaptive.get_buying_power() * 0.98
                if len(top_stocks) > 0 and adapt_budget > 10_000:
                    per_b = adapt_budget / len(top_stocks)
                    for pick in top_stocks:
                        sym, p = pick["symbol"], pick["close"]
                        sh = int(per_b // (p * 1.002))
                        if sh > 0:
                            fee = cost_model.compute_cost(shares=sh, price=p, is_buy=True)
                            if ledger_adaptive.get_buying_power() >= (sh * p + fee):
                                oid = f"ADAPT_{r_date}_{sym}"
                                ledger_adaptive.dispatch_buy_order(oid, sym, sh, p, fee)
                                ledger_adaptive.fill_buy_order(oid, p, fee)

        # End of month evaluation
        end_prices = {
            row["symbol"]: float(row["close"])
            for row in pred_df.filter(pl.col("date") == month_dates[-1]).to_dicts()
        }
        end_nav_naive = ledger_naive.get_nav(end_prices)
        end_nav_adaptive = ledger_adaptive.get_nav(end_prices)

        ret_naive_m = (end_nav_naive / start_nav_naive - 1.0) * 100.0
        ret_adaptive_m = (end_nav_adaptive / start_nav_adaptive - 1.0) * 100.0

        dominant_mode = "100% CASH" if month_regimes.count("CASH") >= month_regimes.count("INVEST") else "INVESTED"
        alpha_m = ret_adaptive_m - ret_naive_m

        month_results.append({
            "Month": ym,
            "Mode": dominant_mode,
            "Naive Ret %": ret_naive_m,
            "Adaptive Ret %": ret_adaptive_m,
            "Monthly Alpha %": alpha_m,
            "Naive NAV": end_nav_naive,
            "Adaptive NAV": end_nav_adaptive,
        })

    # Print Full Month-by-Month Audited Scorecard
    print("\n" + "=" * 90)
    print("2-YEAR MONTH-BY-MONTH REGIME SWITCH PERFORMANCE BREAKDOWN")
    print("=" * 90)
    print(f"{'Month':<8} | {'Strategy Mode':<13} | {'Naive (Always)':<16} | {'Adaptive Cash':<16} | {'Alpha Outperformance':<20}")
    print("-" * 90)

    cash_months_count = 0
    alpha_positive_count = 0

    for r in month_results:
        m_str = r["Month"]
        mode_str = r["Mode"]
        n_ret = r["Naive Ret %"]
        a_ret = r["Adaptive Ret %"]
        alpha = r["Monthly Alpha %"]
        
        if mode_str == "100% CASH":
            cash_months_count += 1
        if alpha >= 0:
            alpha_positive_count += 1

        print(f"{m_str:<8} | {mode_str:<13} | {n_ret:>13.2f}%   | {a_ret:>13.2f}%   | {alpha:>+17.2f}%")

    print("=" * 90)
    
    final_naive_nav = month_results[-1]["Naive NAV"]
    final_adapt_nav = month_results[-1]["Adaptive NAV"]
    total_naive_ret = (final_naive_nav / 1_000_000.0 - 1.0) * 100.0
    total_adapt_ret = (final_adapt_nav / 1_000_000.0 - 1.0) * 100.0
    net_alpha = total_adapt_ret - total_naive_ret

    print("\n" + "=" * 90)
    print("2-YEAR CUMULATIVE SCORECARD SUMMARY")
    print("=" * 90)
    print(f"  Initial Portfolio Capital:       INR 1,000,000.00")
    print(f"  Total Months Evaluated:          {len(month_results)} Months")
    print(f"  Months Held 100% in Cash:        {cash_months_count} / {len(month_results)} ({cash_months_count/len(month_results):.1%})")
    print(f"  Win Rate vs Naive (Alpha > 0):   {alpha_positive_count} / {len(month_results)} ({alpha_positive_count/len(month_results):.1%})")
    print("-" * 90)
    print(f"  Naive Always-Invested Final NAV: INR {final_naive_nav:>12,.2f}  ({total_naive_ret:>+6.2f}%)")
    print(f"  Adaptive Cash Engine Final NAV:  INR {final_adapt_nav:>12,.2f}  ({total_adapt_ret:>+6.2f}%)")
    print(f"  Net Strategy Alpha Added:        {net_alpha:>+18.2f}% (INR {final_adapt_nav - final_naive_nav:+,.2f})")
    print("=" * 90)

if __name__ == "__main__":
    main()
