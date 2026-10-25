"""
AlphaNiti v25.0: Production Cloud API & Real-Time Trading Dashboard
Deployable directly on Render, Fly.io, or Railway.
Provides:
  - Real-time market regime assessment (RISK_ON vs CASH_PRESERVATION)
  - Evidential Deep Learning inference (gamma, aleatoric, epistemic, p_breach)
  - Live NSE market feed integration (Yahoo Finance)
  - Interactive HTML Dashboard for institutional monitoring
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import numpy as np
import polars as pl
import pandas as pd
import torch
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.feature_engineering import compute_causal_features
from src.data.tensor_builder import AlphaNitiDataPipeline
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble

app = FastAPI(
    title="AlphaNiti v25.0 Institutional Execution API",
    description="Evidential Deep Ensemble with Adaptive Cash-Preservation Regime Gates",
    version="25.0",
)

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

# Global State Cache
STATE: Dict[str, Any] = {
    "model": None,
    "pipeline": None,
    "fcols": [],
    "last_updated": None,
    "signals": [],
    "regime": "UNKNOWN",
    "mkt_drift": 0.0,
    "mkt_crash_risk": 0.0,
}


@app.on_event("startup")
def load_model_and_pipeline():
    """Initializes the pipeline scaler and loads ensemble weights into memory."""
    print("Initializing AlphaNiti v25.0 model on Render runtime...")
    train_path = PROJECT_ROOT / "data" / "splits" / "train.parquet"
    if not train_path.exists():
        print(f"Warning: {train_path} not found locally.")
        return

    train_df = pl.read_parquet(train_path)
    pipeline = AlphaNitiDataPipeline(lookback=60, c_clip=4.5)
    _, fcols = pipeline.fit_transform_train(train_df)

    ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    if not ckpt_path.exists():
        print(f"Warning: Model checkpoint {ckpt_path} not found.")
        return

    ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
    for i, m in enumerate(models):
        m.load_state_dict(ckpt[f"member_{i}"])
        m.eval()

    STATE["model"] = EvidentialEnsemble(models)
    STATE["pipeline"] = pipeline
    STATE["fcols"] = fcols
    print("Model & Scaler initialized successfully.")


def refresh_live_signals():
    """Fetches real-time market data, generates inference, and updates state."""
    ensemble = STATE["model"]
    pipeline = STATE["pipeline"]
    fcols = STATE["fcols"]

    if ensemble is None or pipeline is None:
        raise RuntimeError("Model or pipeline not loaded.")

    ticker_list = list(TICKERS_MAP.keys())
    raw_df = yf.download(
        tickers=ticker_list,
        period="6mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
    )

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
    df_all = df_all.with_columns([
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret_1d")
    ])

    dates = sorted(df_all["date"].unique().to_list())
    last_date = dates[-1]

    df_feat, _ = compute_causal_features(df_all)
    X_norm = pipeline.scaler.transform(df_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    df_scaled = df_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])

    lookback = 60
    signals = []

    with torch.no_grad():
        for sym in TICKERS_MAP.values():
            sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")
            sub_dates = sub["date"].to_list()
            if last_date in sub_dates:
                pos = sub_dates.index(last_date)
                if pos >= lookback - 1:
                    seq = sub.select(norm_cols).to_numpy()[pos - lookback + 1 : pos + 1]
                    inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
                    gamma, aleatoric, epistemic, p_breach = ensemble.predict(inp)
                    c_price = float(sub.filter(pl.col("date") == last_date)["close"][0])
                    signals.append({
                        "symbol": sym,
                        "date": last_date,
                        "close_price": round(c_price, 2),
                        "predicted_return_pct": round(float(gamma[0][0]) * 100, 2),
                        "aleatoric_vol_pct": round(float(np.sqrt(aleatoric[0][0])) * 100, 2),
                        "epistemic_uncertainty": round(float(epistemic[0][0]), 4),
                        "drawdown_crash_prob": round(float(p_breach[0][0]), 4),
                    })

    mkt_drift = float(np.mean([s["predicted_return_pct"] for s in signals])) / 100.0
    mkt_crash_risk = float(np.mean([s["drawdown_crash_prob"] for s in signals]))
    regime = "CASH_PRESERVATION" if (mkt_drift < 0.0 or mkt_crash_risk > 0.35) else "RISK_ON"

    # Add allocation action
    med_epi = np.median([s["epistemic_uncertainty"] for s in signals])
    for s in signals:
        if regime == "CASH_PRESERVATION":
            s["recommended_action"] = "HOLD CASH (100% W_settled)"
        else:
            if s["drawdown_crash_prob"] < 0.38 and s["epistemic_uncertainty"] <= med_epi and s["predicted_return_pct"] > 0.0:
                s["recommended_action"] = "BUY / ALLOCATE (Alpha Candidate)"
            else:
                s["recommended_action"] = "AVOID / FILTERED (Elevated Risk)"

    STATE["signals"] = signals
    STATE["regime"] = regime
    STATE["mkt_drift"] = mkt_drift
    STATE["mkt_crash_risk"] = mkt_crash_risk
    STATE["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AlphaNiti v25.0 Execution Engine",
        "model_loaded": STATE["model"] is not None,
        "runtime": "Render Cloud Container",
        "last_market_update": STATE["last_updated"],
    }


@app.get("/api/regime")
def get_regime():
    if not STATE["signals"]:
        refresh_live_signals()
    return {
        "market_regime": STATE["regime"],
        "market_expected_drift_pct": round(STATE["mkt_drift"] * 100, 2),
        "market_average_crash_probability": round(STATE["mkt_crash_risk"], 4),
        "rule_active": "Hold 100% Cash if drift < 0 or crash_prob > 35%",
        "last_updated": STATE["last_updated"],
    }


@app.get("/api/signals")
def get_signals():
    if not STATE["signals"]:
        refresh_live_signals()
    return {
        "regime": STATE["regime"],
        "as_of_date": STATE["signals"][0]["date"] if STATE["signals"] else "N/A",
        "last_updated": STATE["last_updated"],
        "equities": STATE["signals"],
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    if not STATE["signals"]:
        try:
            refresh_live_signals()
        except Exception as e:
            return f"<h1>Error loading live market data: {e}</h1>"

    regime = STATE["regime"]
    regime_color = "#10b981" if regime == "RISK_ON" else "#ef4444"
    regime_badge = "🟢 RISK-ON / ALLOCATING" if regime == "RISK_ON" else "🛡️ CASH PRESERVATION (100% CASH)"

    rows_html = ""
    for s in STATE["signals"]:
        action_color = "#10b981" if "BUY" in s["recommended_action"] else ("#f59e0b" if "CASH" in s["recommended_action"] else "#6b7280")
        rows_html += f"""
        <tr>
            <td style="font-weight: 600; color: #1e293b;">{s['symbol']}</td>
            <td>INR {s['close_price']:,.2f}</td>
            <td style="color: {'#10b981' if s['predicted_return_pct'] > 0 else '#ef4444'}; font-weight: 600;">{s['predicted_return_pct']:+.2f}%</td>
            <td>{s['aleatoric_vol_pct']:.2f}%</td>
            <td>{s['epistemic_uncertainty']:.4f}</td>
            <td style="color: {'#ef4444' if s['drawdown_crash_prob'] > 0.35 else '#10b981'}; font-weight: 600;">{s['drawdown_crash_prob'] * 100:.1f}%</td>
            <td style="color: {action_color}; font-weight: 700;">{s['recommended_action']}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>AlphaNiti v25.0 Live Trading Terminal</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 16px; margin-bottom: 24px; }}
            .badge {{ background: {regime_color}; color: #ffffff; padding: 6px 14px; border-radius: 9999px; font-weight: 700; font-size: 0.9rem; letter-spacing: 0.05em; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .card {{ background: #1e293b; padding: 20px; border-radius: 12px; border: 1px solid #334155; }}
            .card-title {{ color: #94a3b8; font-size: 0.85rem; text-transform: uppercase; margin-bottom: 8px; letter-spacing: 0.05em; }}
            .card-val {{ font-size: 1.8rem; font-weight: 700; color: #ffffff; }}
            table {{ width: 100%; border-collapse: collapse; background: #ffffff; color: #0f172a; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3); }}
            th, td {{ padding: 14px 16px; text-align: left; border-bottom: 1px solid #e2e8f0; font-size: 0.95rem; }}
            th {{ background: #f1f5f9; color: #475569; font-weight: 700; text-transform: uppercase; font-size: 0.8rem; letter-spacing: 0.05em; }}
            tr:hover {{ background: #f8fafc; }}
            .footer {{ margin-top: 24px; color: #64748b; font-size: 0.85rem; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1 style="margin: 0; font-size: 1.75rem;">ALPHANITI v25.0</h1>
                    <div style="color: #94a3b8; font-size: 0.9rem; margin-top: 4px;">Institutional Quant Execution & Evidential Risk Terminal</div>
                </div>
                <div class="badge">{regime_badge}</div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="card-title">Market Drift (Avg Gamma)</div>
                    <div class="card-val" style="color: {'#10b981' if STATE['mkt_drift'] > 0 else '#ef4444'}">{STATE['mkt_drift'] * 100:+.2f}%</div>
                </div>
                <div class="card">
                    <div class="card-title">Market Crash Risk (P_breach)</div>
                    <div class="card-val" style="color: {'#ef4444' if STATE['mkt_crash_risk'] > 0.35 else '#10b981'}">{STATE['mkt_crash_risk'] * 100:.1f}%</div>
                </div>
                <div class="card">
                    <div class="card-title">Active Regime Gate</div>
                    <div class="card-val" style="font-size: 1.25rem; color: #38bdf8;">{'🛡️ 100% CASH' if regime == 'CASH_PRESERVATION' else '🚀 CAPITAL ACTIVE'}</div>
                </div>
                <div class="card">
                    <div class="card-title">As-Of Date (Live Feed)</div>
                    <div class="card-val" style="font-size: 1.2rem; color: #cbd5e1;">{STATE['signals'][0]['date'] if STATE['signals'] else 'Live'}</div>
                </div>
            </div>

            <h3 style="margin-bottom: 12px; color: #cbd5e1;">Live NSE Bluechip Evidential Signals</h3>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>Pred Return</th>
                        <th>Aleatoric Vol</th>
                        <th>Epistemic Uncert</th>
                        <th>Crash Risk</th>
                        <th>Recommended Action</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <div class="footer">
                AlphaNiti v25.0 Core Engine | Last Updated: {STATE['last_updated']} | Ready for Render & Webhooks
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
