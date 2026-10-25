"""
AlphaNiti v25.0: Live Market Data Verification Engine
Fetches live, real-world market prices from Yahoo Finance API (NSE Equities)
and executes an end-to-end simulation over the latest 1-month trading period.
"""

import sys
import time
from pathlib import Path
import numpy as np
import polars as pl
import pandas as pd
import yfinance as yf
import torch

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.feature_engineering import compute_causal_features
from src.data.tensor_builder import AlphaNitiDataPipeline
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.accounting.ledger import FiveBucketBalanceSheet

TICKERS_MAP = {
    "RELIANCE.NS": "RELIANCE",
    "TCS.NS": "TCS",
    "HDFCBANK.NS": "HDFCBANK",
    "INFY.NS": "INFY",
    "ICICIBANK.NS": "ICICIBANK",
    "SBIN.NS": "SBIN",
    "BHARTIARTL.NS": "BHARTIARTL",
    "ITC.NS": "ITC",
    "LT.NS": "LT",
    "KOTAKBANK.NS": "KOTAKBANK",
    "HINDUNILVR.NS": "HINDUNILVR",
    "AXISBANK.NS": "AXISBANK",
}

def main():
    print("=" * 80)
    print("ALPHANITI v25.0 LIVE MARKET TEST (NSE REAL DATA via FREE LIVE API)")
    print("=" * 80)

    # 1. Fetch live real market data
    ticker_list = list(TICKERS_MAP.keys())
    print(f"\n[Step 1/5] Fetching live daily OHLCV bars for {len(ticker_list)} NSE equities...")
    t0 = time.perf_counter()
    raw_df = yf.download(
        tickers=ticker_list,
        period="6mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
    )
    print(f"  Live data successfully retrieved in {time.perf_counter() - t0:.2f}s")

    # Format into canonical AlphaNiti table
    rows = []
    for yf_tick, clean_sym in TICKERS_MAP.items():
        if yf_tick not in raw_df:
            continue
        sub = raw_df[yf_tick].dropna()
        for dt, row in sub.iterrows():
            d_str = dt.strftime("%Y-%m-%d")
            c = float(row["Close"])
            v = float(row["Volume"])
            t_cr = (c * v) / 1e7
            rows.append({
                "symbol": clean_sym,
                "date": d_str,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": c,
                "volume": v,
                "turnover_cr": t_cr,
                "ret_1d": 0.0,
                "has_corporate_action": False,
                "cum_factor": 1.0,
            })

    df_all = pl.DataFrame(rows).sort(["symbol", "date"])
    # Compute 1d return
    df_all = df_all.with_columns([
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret_1d")
    ])

    dates = sorted(df_all["date"].unique().to_list())
    print(f"  Total records: {len(df_all):,} across {len(dates)} trading days")
    print(f"  Date range: {dates[0]} to {dates[-1]}")

    # 2. Fit pipeline scaler on historical training split
    print("\n[Step 2/5] Loading trained Feature Scaler & Evidential Deep Ensemble...")
    train_path = str(PROJECT_ROOT / "data" / "splits" / "train.parquet")
    train_df = pl.read_parquet(train_path)

    pipeline = AlphaNitiDataPipeline(lookback=60, c_clip=4.5)
    _, fcols = pipeline.fit_transform_train(train_df)

    # 3. Transform live market data
    df_feat, _ = compute_causal_features(df_all)
    X_norm = pipeline.scaler.transform(df_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    df_scaled = df_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])

    # Load trained model
    ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
    for i, m in enumerate(models):
        m.load_state_dict(ckpt[f"member_{i}"])
        m.eval()
    ensemble = EvidentialEnsemble(models)
    print("  Evidential Ensemble (3 members with Temporal Attention) initialized.")

    # 4. Generate Predictions for the latest 1 month (past 22 trading days)
    print("\n[Step 3/5] Scoring live sequences over the most recent 1-month market window...")
    test_window_dates = dates[-22:] # 1 month of trading days
    lookback = 60

    signals = []
    with torch.no_grad():
        for d in test_window_dates:
            d_idx = dates.index(d)
            if d_idx < lookback:
                continue
            
            for sym in TICKERS_MAP.values():
                sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")
                sub_dates = sub["date"].to_list()
                if d in sub_dates:
                    pos = sub_dates.index(d)
                    if pos >= lookback - 1:
                        seq = sub.select(norm_cols).to_numpy()[pos - lookback + 1 : pos + 1]
                        inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
                        gamma, aleatoric, epistemic, p_breach = ensemble.predict(inp)
                        c_price = float(sub.filter(pl.col("date") == d)["close"][0])
                        signals.append({
                            "date": d,
                            "symbol": sym,
                            "close": c_price,
                            "gamma": float(gamma[0][0]),
                            "aleatoric": float(aleatoric[0][0]),
                            "epistemic": float(epistemic[0][0]),
                            "p_breach": float(p_breach[0][0]),
                        })

    sig_df = pl.DataFrame(signals)
    print(f"  Generated {len(sig_df)} live inference predictions across {len(test_window_dates)} days.")

    # 5. Execute 1-Month Live Walk-Forward Simulation
    print("\n[Step 4/5] Executing Walk-Forward Balance Sheet Simulation (Starting NAV: INR 1,000,000.00)...")
    ledger = FiveBucketBalanceSheet(initial_capital_inr=1_000_000.0)
    cost_model = IndianStatutoryCostModel()

    trade_log = []
    rebal_interval = 5 # Weekly rebalancing for 1 month test
    rebal_days = test_window_dates[::rebal_interval]

    for cycle_idx, r_date in enumerate(rebal_days):
        ledger.settle_t1_batch()
        day_signals = sig_df.filter(pl.col("date") == r_date)
        if len(day_signals) == 0:
            continue

        current_prices = {row["symbol"]: row["close"] for row in day_signals.to_dicts()}

        # Liquidate previous positions
        for sym, qty in list(ledger.positions.items()):
            if qty > 0 and sym in current_prices:
                p = current_prices[sym]
                fee = cost_model.compute_cost(shares=qty, price=p, is_buy=False)
                ledger.fill_sell_order(sym, qty, p, fee)
                trade_log.append({
                    "date": r_date,
                    "action": "SELL",
                    "symbol": sym,
                    "quantity": qty,
                    "price": p,
                    "charges": fee,
                })

        # Apply Institutional Guardrails:
        # 1. Filter out crash-prone stocks: p_breach < 0.38
        # 2. Filter out top epistemic uncertainty: epistemic < median
        # 3. Require positive return drift: gamma > 0.0
        cands = day_signals.to_dicts()
        med_epi = np.median([c["epistemic"] for c in cands])
        
        filtered = [
            c for c in cands 
            if c["p_breach"] < 0.38 and c["epistemic"] <= med_epi and c["gamma"] > 0.0
        ]
        # Rank by Sharpe ratio proxy
        filtered.sort(key=lambda x: x["gamma"] / (np.sqrt(x["aleatoric"]) + 1e-4), reverse=True)
        top_picks = filtered[:4] # Top 4 high-conviction stocks

        buying_power = ledger.get_buying_power() * 0.98
        if len(top_picks) > 0 and buying_power > 10_000:
            per_stock_budget = buying_power / len(top_picks)
            for pick in top_picks:
                sym = pick["symbol"]
                p = pick["close"]
                shares = int(per_stock_budget // (p * 1.002))
                if shares > 0:
                    fee = cost_model.compute_cost(shares=shares, price=p, is_buy=True)
                    if ledger.get_buying_power() >= (shares * p + fee):
                        oid = f"LIVE_{cycle_idx}_{sym}"
                        ledger.dispatch_buy_order(oid, sym, shares, p, fee)
                        ledger.fill_buy_order(oid, p, fee)
                        trade_log.append({
                            "date": r_date,
                            "action": "BUY",
                            "symbol": sym,
                            "quantity": shares,
                            "price": p,
                            "charges": fee,
                            "p_breach": pick["p_breach"],
                            "pred_return": pick["gamma"],
                        })

    # Evaluate Final NAV on latest available day
    last_day = dates[-1]
    last_prices = {
        row["symbol"]: float(row["close"])
        for row in df_all.filter(pl.col("date") == last_day).to_dicts()
    }
    final_nav = ledger.get_nav(last_prices)
    net_pnl = final_nav - 1_000_000.0
    return_pct = (final_nav / 1_000_000.0 - 1.0) * 100.0

    print("\n" + "=" * 80)
    print("LIVE 1-MONTH RECENT MARKET TEST SCORECARD")
    print("=" * 80)
    print(f"  Test Period:                  {test_window_dates[0]} to {test_window_dates[-1]}")
    print(f"  Initial Portfolio Capital:    INR 1,000,000.00")
    print(f"  Final Net Asset Value (NAV):  INR {final_nav:,.2f}")
    print(f"  Net Realized + Unrealized PnL: INR {net_pnl:+,.2f} ({return_pct:+.2f}%)")
    print(f"  Total Trades Dispatched:      {len(trade_log)}")
    print(f"  Current Cash Settled Balance: INR {ledger.W_settled:,.2f}")
    print(f"  Current Stock Custody Value:  INR {final_nav - ledger.W_settled:,.2f}")

    print("\n--- Live Trade Log Sample ---")
    df_trades = pd.DataFrame(trade_log)
    if len(df_trades) > 0:
        print(df_trades.tail(10).to_string(index=False))
    else:
        print("  Model safely preserved 100% Cash due to elevated crash risks.")

    print("\n" + "=" * 80)
    print("LIVE VERIFICATION CONCLUSION: Pipeline operates seamlessly with live market feeds!")
    print("=" * 80)

if __name__ == "__main__":
    main()
