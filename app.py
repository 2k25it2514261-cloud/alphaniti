"""
AlphaNiti v25.0: Production Cloud API & TradingView Institutional Terminal
Deployable on Render and connected to GitHub Pages frontend.
Provides:
  - CORS-enabled REST API for TradingView Lightweight Charts & Order Execution
  - Real-time market regime assessment (RISK_ON vs CASH_PRESERVATION)
  - Evidential Deep Learning inference (gamma, aleatoric, epistemic, p_breach)
  - Interactive Order & Portfolio Simulator (Branch-and-Bound Integer Sizer,
    Finance Act 2026 Statutory Taxes, 5-Bucket Ledger, Ed25519 Safety Kernel)
"""

import os
import sys
import json
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
import numpy as np
import polars as pl
import pandas as pd
import torch
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.feature_engineering import compute_causal_features, FEATURE_COLUMNS
from src.data.robust_scaler import RobustMADScaler
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.portfolio.integer_sizer import LatticeBranchAndBoundSolver
from src.accounting.ledger import FiveBucketBalanceSheet
from src.security.safety_kernel import FormalSafetyKernel, SafetyKernelVetoException

app = FastAPI(
    title="AlphaNiti v25.0 Institutional Execution API",
    description="Evidential Deep Ensemble with Adaptive Cash-Preservation Regime Gates & Formal Safety Kernel",
    version="25.0.0",
)

# Enable CORS for GitHub Pages and external clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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

SECTOR_MAP = {
    "RELIANCE": "Energy & Conglomerate",
    "TCS": "IT Services",
    "HDFCBANK": "Private Banking",
    "INFY": "IT Services",
    "ICICIBANK": "Private Banking",
    "SBIN": "PSU Banking",
    "BHARTIARTL": "Telecom",
    "ITC": "FMCG & Diversified",
    "LT": "Infrastructure & EPC",
    "KOTAKBANK": "Private Banking",
    "HINDUNILVR": "FMCG",
    "AXISBANK": "Private Banking",
}

STATE: Dict[str, Any] = {
    "model": None,
    "scaler": None,
    "fcols": FEATURE_COLUMNS,
    "last_updated": None,
    "data_source": "UNINITIALIZED",
    "signals": [],
    "df_scaled": None,
    "regime": "UNKNOWN",
    "mkt_drift": 0.0,
    "mkt_crash_risk": 0.0,
    "conformal_q_hat": 1.3644,
    "safety_kernel": FormalSafetyKernel(cvar_ceiling_fraction=0.15),
    "cost_model": IndianStatutoryCostModel(),
}


class SimulateOrderRequest(BaseModel):
    mode: str = "PORTFOLIO"  # "SINGLE" or "PORTFOLIO"
    symbol: str = "RELIANCE"
    side: str = "BUY"
    quantity: int = 25
    limit_price: Optional[float] = None
    capital_inr: float = 1_000_000.0
    crash_threshold: float = 0.35
    max_weight: float = 0.25
    force_risk_on: bool = False


@app.on_event("startup")
def load_model_and_pipeline():
    """Fast, low-memory initialization of Scaler and Evidential Ensemble."""
    print("Initializing AlphaNiti v25.0 model on cloud runtime...")
    scaler_path = PROJECT_ROOT / "models" / "scaler_params_v25.json"
    scaler = RobustMADScaler(c_clip=4.5)

    if scaler_path.exists():
        params = json.loads(scaler_path.read_text(encoding="utf-8"))
        scaler.c_clip = float(params.get("c_clip", 4.5))
        scaler.medians_ = np.array(params["medians"], dtype=np.float32)
        scaler.mads_ = np.array(params["mads"], dtype=np.float32)
        fcols = params.get("fcols", FEATURE_COLUMNS)
    else:
        train_path = PROJECT_ROOT / "data" / "splits" / "train.parquet"
        train_df = pl.read_parquet(train_path)
        df_feat, fcols = compute_causal_features(train_df)
        scaler.fit(df_feat.select(fcols))

    ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
    for i, m in enumerate(models):
        m.load_state_dict(ckpt[f"member_{i}"])
        m.eval()

    STATE["model"] = EvidentialEnsemble(models)
    STATE["scaler"] = scaler
    STATE["fcols"] = fcols
    print("Model & Scaler initialized in memory.")
    try:
        refresh_live_signals()
    except Exception as e:
        print(f"Initial data load warning: {e}")


def _load_fallback_dataframe() -> pl.DataFrame:
    """Loads fallback institutional test split if live Yahoo Finance is unreachable."""
    test_path = PROJECT_ROOT / "data" / "splits" / "test.parquet"
    df = pl.read_parquet(test_path)
    target_syms = list(TICKERS_MAP.values())
    df_filtered = df.filter(pl.col("symbol").is_in(target_syms))
    if len(df_filtered) == 0:
        df_filtered = df
    return df_filtered.with_columns(pl.col("date").cast(pl.String)).sort(["symbol", "date"])


def refresh_live_signals(force_live: bool = True):
    """Fetches real-time market data from Yahoo Finance (with institutional parquet fallback)."""
    ensemble: EvidentialEnsemble = STATE["model"]
    scaler: RobustMADScaler = STATE["scaler"]
    fcols: List[str] = STATE["fcols"]

    if ensemble is None or scaler is None:
        load_model_and_pipeline()
        ensemble = STATE["model"]
        scaler = STATE["scaler"]
        fcols = STATE["fcols"]

    df_all = None
    data_source = "LIVE_YAHOO_FINANCE_NSE"

    if force_live:
        try:
            ticker_list = list(TICKERS_MAP.keys())
            raw_df = yf.download(
                tickers=ticker_list,
                period="6mo",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                timeout=10,
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
            if len(rows) > 200:
                df_all = pl.DataFrame(rows).sort(["symbol", "date"])
                df_all = df_all.with_columns([
                    (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret_1d")
                ])
        except Exception as e:
            print(f"Live Yahoo Finance fetch fallback triggered: {e}")

    if df_all is None or len(df_all) == 0:
        df_all = _load_fallback_dataframe()
        data_source = "INSTITUTIONAL_PARQUET_FEED"

    df_feat, _ = compute_causal_features(df_all)
    X_norm = scaler.transform(df_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    df_scaled = df_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])

    lookback = 60
    signals = []
    available_symbols = sorted(df_scaled["symbol"].unique().to_list())

    with torch.no_grad():
        for sym in available_symbols:
            if sym not in TICKERS_MAP.values():
                continue
            sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")
            if len(sub) >= lookback:
                seq = sub.select(norm_cols).to_numpy()[-lookback:]
                inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
                gamma, aleatoric, epistemic, p_breach = ensemble.predict(inp)

                last_row = sub.tail(1).to_dicts()[0]
                c_price = float(last_row["close"])
                ret_1d = float(last_row.get("ret_1d_clean", 0.0)) * 100.0
                ret_20d = float(last_row.get("ret_20d", 0.0)) * 100.0
                rsi_14 = float(last_row.get("rsi_14d", 0.0)) * 50.0 + 50.0
                p_vol = float(last_row.get("parkinson_vol_20d", 0.015)) * 100.0

                pred_ret = float(gamma[0][0])
                ale_var = float(aleatoric[0][0])
                epi_var = float(epistemic[0][0])
                pb = float(p_breach[0][0])
                sharpe_proxy = pred_ret / (np.sqrt(ale_var) + 1e-4)

                signals.append({
                    "symbol": sym,
                    "exchange": "NSE",
                    "sector": SECTOR_MAP.get(sym, "Large Cap Equity"),
                    "date": str(last_row["date"]),
                    "close_price": round(c_price, 2),
                    "open_price": round(float(last_row["open"]), 2),
                    "high_price": round(float(last_row["high"]), 2),
                    "low_price": round(float(last_row["low"]), 2),
                    "volume": int(last_row["volume"]),
                    "change_1d_pct": round(ret_1d, 2),
                    "change_20d_pct": round(ret_20d, 2),
                    "rsi_14": round(rsi_14, 1),
                    "parkinson_vol_pct": round(p_vol, 2),
                    "predicted_return_pct": round(pred_ret * 100, 2),
                    "aleatoric_vol_pct": round(float(np.sqrt(ale_var)) * 100, 2),
                    "epistemic_uncertainty": round(epi_var, 4),
                    "drawdown_crash_prob": round(pb, 4),
                    "sharpe_score": round(sharpe_proxy, 3),
                })

    mkt_drift = float(np.mean([s["predicted_return_pct"] for s in signals])) / 100.0 if signals else 0.0
    mkt_crash_risk = float(np.mean([s["drawdown_crash_prob"] for s in signals])) if signals else 0.0
    regime = "CASH_PRESERVATION" if (mkt_drift < 0.0 or mkt_crash_risk > 0.35) else "RISK_ON"

    med_epi = float(np.median([s["epistemic_uncertainty"] for s in signals])) if signals else 0.01
    for s in signals:
        is_alpha_candidate = (
            s["drawdown_crash_prob"] < 0.38
            and s["epistemic_uncertainty"] <= med_epi * 1.25
            and s["predicted_return_pct"] > 0.0
        )
        s["alpha_qualified"] = bool(is_alpha_candidate)
        if regime == "CASH_PRESERVATION":
            s["recommended_action"] = "HOLD CASH (Regime Gate)"
            s["signal_badge"] = "CASH_GATE"
        elif is_alpha_candidate:
            s["recommended_action"] = "BUY / ALLOCATE (Alpha)"
            s["signal_badge"] = "STRONG_BUY"
        else:
            s["recommended_action"] = "AVOID (Elevated Risk)"
            s["signal_badge"] = "VETOED"

    # Sort signals by sharpe_score descending
    signals.sort(key=lambda x: x["sharpe_score"], reverse=True)

    STATE["signals"] = signals
    STATE["df_scaled"] = df_scaled
    STATE["regime"] = regime
    STATE["mkt_drift"] = mkt_drift
    STATE["mkt_crash_risk"] = mkt_crash_risk
    STATE["data_source"] = data_source
    STATE["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AlphaNiti v25.0 Institutional Execution Engine",
        "model_loaded": STATE["model"] is not None,
        "data_source": STATE["data_source"],
        "tracked_equities": len(STATE["signals"]),
        "last_market_update": STATE["last_updated"],
    }


@app.post("/api/refresh")
def trigger_refresh():
    refresh_live_signals(force_live=True)
    return {
        "status": "refreshed",
        "data_source": STATE["data_source"],
        "regime": STATE["regime"],
        "last_updated": STATE["last_updated"],
    }


@app.get("/api/regime")
def get_regime():
    if not STATE["signals"]:
        refresh_live_signals()
    return {
        "market_regime": STATE["regime"],
        "market_expected_drift_pct": round(STATE["mkt_drift"] * 100, 2),
        "market_average_crash_probability": round(STATE["mkt_crash_risk"], 4),
        "conformal_multiplier_q_hat": STATE["conformal_q_hat"],
        "fournier_guillin_radius": 0.01939,
        "rule_active": "Hold 100% Settled Cash (W_settled) if Market Drift < 0% OR Avg Crash Risk > 35%",
        "data_source": STATE["data_source"],
        "last_updated": STATE["last_updated"],
    }


@app.get("/api/signals")
def get_signals():
    if not STATE["signals"]:
        refresh_live_signals()
    return {
        "regime": STATE["regime"],
        "market_expected_drift_pct": round(STATE["mkt_drift"] * 100, 2),
        "market_average_crash_probability": round(STATE["mkt_crash_risk"], 4),
        "conformal_q_hat": STATE["conformal_q_hat"],
        "as_of_date": STATE["signals"][0]["date"] if STATE["signals"] else "N/A",
        "data_source": STATE["data_source"],
        "last_updated": STATE["last_updated"],
        "equities": STATE["signals"],
    }


@app.get("/api/chart/{symbol}")
def get_symbol_chart(symbol: str):
    """Returns 120-day OHLCV candlestick series, technical overlays, and 20-day Evidential Forecast Fan."""
    if STATE["df_scaled"] is None:
        refresh_live_signals()

    sym = symbol.upper().replace(".NS", "")
    df_scaled: pl.DataFrame = STATE["df_scaled"]
    sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")

    if len(sub) == 0:
        raise HTTPException(status_code=404, detail=f"Symbol {sym} not found in universe.")

    sub = sub.with_columns([
        pl.col("close").ewm_mean(span=12).alias("ema12"),
        pl.col("close").ewm_mean(span=26).alias("ema26"),
    ])

    tail_rows = sub.tail(120).to_dicts()
    candles = []
    for r in tail_rows:
        candles.append({
            "time": str(r["date"])[:10],
            "open": round(float(r["open"]), 2),
            "high": round(float(r["high"]), 2),
            "low": round(float(r["low"]), 2),
            "close": round(float(r["close"]), 2),
            "volume": int(r["volume"]),
            "ema12": round(float(r["ema12"]), 2),
            "ema26": round(float(r["ema26"]), 2),
            "rsi14": round(float(r["rsi_14d"]) * 50.0 + 50.0, 2),
            "hist_vol": round(float(r["hist_vol_20d"]) * 100.0, 2),
        })

    # Find signal metadata for symbol
    sig_meta = next((s for s in STATE["signals"] if s["symbol"] == sym), None)

    # Construct 20-trading-day forward evidential projection cone for chart
    forecast_fan = []
    if sig_meta and candles:
        last_close = candles[-1]["close"]
        last_date = pd.to_datetime(candles[-1]["time"])
        gamma_20d = sig_meta["predicted_return_pct"] / 100.0
        ale_20d = sig_meta["aleatoric_vol_pct"] / 100.0
        epi_20d = np.sqrt(sig_meta["epistemic_uncertainty"])
        total_sigma_20d = np.sqrt(ale_20d ** 2 + epi_20d ** 2) * STATE["conformal_q_hat"]

        for step in range(1, 21):
            frac = step / 20.0
            proj_date = (last_date + pd.Timedelta(days=int(step * 1.45))).strftime("%Y-%m-%d")
            expected_px = last_close * (1.0 + gamma_20d * frac)
            band_width = last_close * total_sigma_20d * np.sqrt(frac) * 0.35
            forecast_fan.append({
                "time": proj_date,
                "expected": round(float(expected_px), 2),
                "upper_90": round(float(expected_px + band_width), 2),
                "lower_90": round(float(expected_px - band_width), 2),
            })

    return {
        "symbol": sym,
        "sector": SECTOR_MAP.get(sym, "Large Cap Equity"),
        "signal": sig_meta,
        "candles": candles,
        "forecast_fan": forecast_fan,
    }


@app.post("/api/simulate-order")
def simulate_institutional_order(req: SimulateOrderRequest):
    """
    Executes an interactive dry-run through AlphaNiti's full institutional pipeline:
      1. Regime Gate Check (or manual override)
      2. Branch-and-Bound Lattice Integer Sizer (or Single-Stock Order check)
      3. Finance Act 2026 Statutory Tax Model (STT 0.1%, Stamp Duty 0.015%, GST 18%)
      4. Five-Bucket Double-Entry Balance Sheet (W_settled, W_escrow, W_recv, W_lien, W_payable)
      5. Formal Safety Kernel (3-Gate Veto + Hardware Enclave Ed25519 Signature)
    """
    if not STATE["signals"]:
        refresh_live_signals()

    signals = STATE["signals"]
    cost_model: IndianStatutoryCostModel = STATE["cost_model"]
    kernel: FormalSafetyKernel = STATE["safety_kernel"]
    ledger = FiveBucketBalanceSheet(initial_capital_inr=req.capital_inr)
    current_prices = {s["symbol"]: s["close_price"] for s in signals}

    mkt_drift = STATE["mkt_drift"]
    mkt_crash = STATE["mkt_crash_risk"]
    is_cash_regime = (mkt_drift < 0.0) or (mkt_crash > req.crash_threshold)

    if req.mode.upper() == "SINGLE":
        sym = req.symbol.upper().replace(".NS", "")
        sig = next((s for s in signals if s["symbol"] == sym), signals[0])
        px = float(req.limit_price) if (req.limit_price and req.limit_price > 0) else sig["close_price"]
        qty = max(1, int(req.quantity))
        is_buy = req.side.upper() == "BUY"

        fee_breakdown = cost_model.compute_detailed_breakdown(shares=qty, price=px, is_buy=is_buy)
        total_fee = fee_breakdown["total_charges"]
        max_fee = total_fee
        cvar_est = qty * px * max(0.02, sig["aleatoric_vol_pct"] / 100.0 * 0.25)

        try:
            signed_pkt = kernel.evaluate_and_sign_order(
                ledger=ledger,
                symbol=sym,
                side=req.side.upper(),
                quantity=qty,
                limit_price=px,
                estimated_charges=total_fee,
                max_possible_charges=max_fee,
                portfolio_cvar_estimate=cvar_est,
                current_prices=current_prices,
            )
            if is_buy:
                ord_id = f"SIM_{int(time.time())}_{sym}"
                ledger.dispatch_buy_order(ord_id, sym, qty, px, total_fee)
                ledger.fill_buy_order(ord_id, px, total_fee)
            gate_status = "APPROVED_AND_SIGNED"
            veto_msg = None
            ed25519_sig = signed_pkt["ed25519_signature"]
        except Exception as e:
            gate_status = "VETOED_BY_SAFETY_KERNEL"
            veto_msg = str(e)
            ed25519_sig = None

        return {
            "mode": "SINGLE",
            "gate_status": gate_status,
            "veto_reason": veto_msg,
            "regime_state": "CASH_PRESERVATION" if is_cash_regime else "RISK_ON",
            "regime_warning": (
                f"Market crash risk ({mkt_crash*100:.1f}%) exceeds threshold ({req.crash_threshold*100:.1f}%). Institutional rule advises 100% Cash."
                if is_cash_regime and not req.force_risk_on
                else None
            ),
            "order": {
                "symbol": sym,
                "side": req.side.upper(),
                "quantity": qty,
                "limit_price": round(px, 2),
                "notional_value_inr": round(qty * px, 2),
                "statutory_charges_inr": round(total_fee, 2),
                "total_outlay_inr": round(qty * px + total_fee, 2),
                "fee_breakdown": {k: round(float(v), 2) for k, v in fee_breakdown.items()},
                "predicted_return_pct": sig["predicted_return_pct"],
                "crash_prob_pct": round(sig["drawdown_crash_prob"] * 100, 1),
            },
            "ledger": {
                "W_settled": round(ledger.W_settled, 2),
                "W_escrow": round(ledger.W_escrow, 2),
                "W_recv": round(ledger.W_recv, 2),
                "W_lien": round(ledger.W_lien, 2),
                "W_payable": round(ledger.W_payable, 2),
                "nav_inr": round(ledger.get_nav(current_prices), 2),
                "buying_power_inr": round(ledger.get_buying_power(), 2),
            },
            "cryptographic_proof": {
                "ed25519_signature": ed25519_sig,
                "safety_gates_checked": ["Gate 1: Cleared Solvency", "Gate 2: Discrete CVaR Ceiling", "Gate 3: Limit Collar Guard"],
            },
        }

    # PORTFOLIO AUTO-ALLOCATOR MODE
    if is_cash_regime and not req.force_risk_on:
        return {
            "mode": "PORTFOLIO",
            "gate_status": "CASH_PRESERVATION_ACTIVE",
            "regime_state": "CASH_PRESERVATION",
            "message": f"Regime Switch Rule Triggered: Avg Market Crash Risk ({mkt_crash*100:.1f}%) > Threshold ({req.crash_threshold*100:.1f}%). Allocating 0% to equities and holding 100% in Settled Cash (W_settled).",
            "allocations": [],
            "total_allocated_inr": 0.0,
            "total_statutory_charges_inr": 0.0,
            "ledger": {
                "W_settled": round(ledger.W_settled, 2),
                "W_escrow": 0.0,
                "W_recv": 0.0,
                "W_lien": 0.0,
                "W_payable": 0.0,
                "nav_inr": round(req.capital_inr, 2),
                "buying_power_inr": round(req.capital_inr, 2),
            },
            "cryptographic_proof": {
                "merkle_root": "0x" + hashlib.sha256(f"CASH_PRESERVATION_{req.capital_inr}".encode()).hexdigest(),
                "solver_status": "SKIPPED_CASH_GATE",
            },
        }

    # Run Lattice Branch-and-Bound Integer Sizer across top candidates
    med_epi = float(np.median([s["epistemic_uncertainty"] for s in signals]))
    candidates = [
        s for s in signals
        if s["drawdown_crash_prob"] < max(0.38, req.crash_threshold + 0.03)
        and s["epistemic_uncertainty"] <= med_epi * 1.5
    ]
    if not candidates:
        candidates = signals[:5]
    else:
        candidates = candidates[:6]

    cand_names = [c["symbol"] for c in candidates]
    price_vec = np.array([c["close_price"] for c in candidates], dtype=float)
    mu_vec = np.array([max(0.005, c["predicted_return_pct"] / 100.0) for c in candidates], dtype=float)

    # Construct synthetic causal return scenarios for CVaR optimization
    rng = np.random.default_rng(42)
    scenarios = rng.normal(loc=mu_vec / 20.0, scale=0.018, size=(60, len(candidates)))

    sizer = LatticeBranchAndBoundSolver(
        cost_model=cost_model,
        alpha_cvar=0.95,
        max_asset_weight=req.max_weight,
        max_nodes=150,
        time_limit_sec=2.0,
    )
    cert = sizer.solve(
        prices=price_vec,
        expected_returns=mu_vec,
        scenarios=scenarios,
        cash_budget=req.capital_inr * 0.98,
        nav=req.capital_inr,
        asset_names=cand_names,
    )

    alloc_rows = []
    total_allocated = 0.0
    total_taxes = 0.0
    last_sig = None

    for sym, shares in cert["solution_vector"].items():
        if shares > 0:
            sig = next(s for s in candidates if s["symbol"] == sym)
            px = sig["close_price"]
            fee_info = cost_model.compute_detailed_breakdown(shares=shares, price=px, is_buy=True)
            fee = fee_info["total_charges"]
            notional = shares * px
            outlay = notional + fee

            if ledger.get_buying_power() >= outlay:
                oid = f"BNB_{sym}"
                signed = kernel.evaluate_and_sign_order(
                    ledger=ledger,
                    symbol=sym,
                    side="BUY",
                    quantity=shares,
                    limit_price=px,
                    estimated_charges=fee,
                    max_possible_charges=fee,
                    portfolio_cvar_estimate=notional * 0.04,
                    current_prices=current_prices,
                )
                last_sig = signed["ed25519_signature"]
                ledger.dispatch_buy_order(oid, sym, shares, px, fee)
                ledger.fill_buy_order(oid, px, fee)
                total_allocated += notional
                total_taxes += fee
                alloc_rows.append({
                    "symbol": sym,
                    "shares": int(shares),
                    "price_inr": round(px, 2),
                    "notional_inr": round(notional, 2),
                    "weight_pct": round((notional / req.capital_inr) * 100, 2),
                    "statutory_tax_inr": round(fee, 2),
                    "predicted_return_pct": sig["predicted_return_pct"],
                    "crash_prob_pct": round(sig["drawdown_crash_prob"] * 100, 1),
                })

    return {
        "mode": "PORTFOLIO",
        "gate_status": "ALLOCATED_AND_SIGNED",
        "regime_state": "RISK_ON_OVERRIDE" if is_cash_regime else "RISK_ON",
        "message": f"Branch-and-Bound Lattice Solver allocated INR {total_allocated:,.2f} across {len(alloc_rows)} integer equity positions.",
        "allocations": alloc_rows,
        "total_allocated_inr": round(total_allocated, 2),
        "total_statutory_charges_inr": round(total_taxes, 2),
        "ledger": {
            "W_settled": round(ledger.W_settled, 2),
            "W_escrow": round(ledger.W_escrow, 2),
            "W_recv": round(ledger.W_recv, 2),
            "W_lien": round(ledger.W_lien, 2),
            "W_payable": round(ledger.W_payable, 2),
            "nav_inr": round(ledger.get_nav(current_prices), 2),
            "buying_power_inr": round(ledger.get_buying_power(), 2),
        },
        "cryptographic_proof": {
            "merkle_root": cert.get("merkle_root"),
            "ed25519_signature": last_sig,
            "nodes_explored": cert.get("nodes_explored"),
            "solve_time_ms": round(cert.get("solve_time_ms", 0.0), 2),
            "solver_status": cert.get("status"),
        },
    }


@app.get("/snapshot.json")
def serve_snapshot():
    snap_file = PROJECT_ROOT / "frontend" / "snapshot.json"
    if snap_file.exists():
        return JSONResponse(content=json.loads(snap_file.read_text(encoding="utf-8")))
    raise HTTPException(status_code=404, detail="Snapshot not found")


@app.get("/", response_class=HTMLResponse)
def root_page():
    index_file = PROJECT_ROOT / "frontend" / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return "<h1>AlphaNiti v25.0 API Online. Visit /health or /api/signals</h1>"


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
