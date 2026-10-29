import sys
import json
import time
from pathlib import Path
import numpy as np
import polars as pl
import pandas as pd
import yfinance as yf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.feature_engineering import compute_causal_features, FEATURE_COLUMNS
from src.data.robust_scaler import RobustMADScaler
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.risk.decision_score import compute_composite_decision_score
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.portfolio.integer_sizer import LatticeBranchAndBoundSolver
from src.accounting.ledger import FiveBucketBalanceSheet

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Complete NIFTY 50 Universe (NSE Tickers & Sectors)
NIFTY_50_TICKERS = {
    "ADANIENT.NS": "ADANIENT",
    "ADANIPORTS.NS": "ADANIPORTS",
    "APOLLOHOSP.NS": "APOLLOHOSP",
    "ASIANPAINT.NS": "ASIANPAINT",
    "AXISBANK.NS": "AXISBANK",
    "BAJAJ-AUTO.NS": "BAJAJ-AUTO",
    "BAJFINANCE.NS": "BAJFINANCE",
    "BAJAJFINSV.NS": "BAJAJFINSV",
    "BEL.NS": "BEL",
    "BPCL.NS": "BPCL",
    "BHARTIARTL.NS": "BHARTIARTL",
    "BRITANNIA.NS": "BRITANNIA",
    "CIPLA.NS": "CIPLA",
    "COALINDIA.NS": "COALINDIA",
    "DRREDDY.NS": "DRREDDY",
    "EICHERMOT.NS": "EICHERMOT",
    "GRASIM.NS": "GRASIM",
    "HCLTECH.NS": "HCLTECH",
    "HDFCBANK.NS": "HDFCBANK",
    "HDFCLIFE.NS": "HDFCLIFE",
    "HEROMOTOCO.NS": "HEROMOTOCO",
    "HINDALCO.NS": "HINDALCO",
    "HINDUNILVR.NS": "HINDUNILVR",
    "ICICIBANK.NS": "ICICIBANK",
    "INDUSINDBK.NS": "INDUSINDBK",
    "INFY.NS": "INFY",
    "ITC.NS": "ITC",
    "JSWSTEEL.NS": "JSWSTEEL",
    "KOTAKBANK.NS": "KOTAKBANK",
    "LT.NS": "LT",
    "M&M.NS": "M&M",
    "MARUTI.NS": "MARUTI",
    "NESTLEIND.NS": "NESTLEIND",
    "NTPC.NS": "NTPC",
    "ONGC.NS": "ONGC",
    "POWERGRID.NS": "POWERGRID",
    "RELIANCE.NS": "RELIANCE",
    "SBILIFE.NS": "SBILIFE",
    "SBIN.NS": "SBIN",
    "SHRIRAMFIN.NS": "SHRIRAMFIN",
    "SUNPHARMA.NS": "SUNPHARMA",
    "TATACONSUM.NS": "TATACONSUM",
    "TATASTEEL.NS": "TATASTEEL",
    "TCS.NS": "TCS",
    "TECHM.NS": "TECHM",
    "TITAN.NS": "TITAN",
    "TRENT.NS": "TRENT",
    "ULTRACEMCO.NS": "ULTRACEMCO",
    "WIPRO.NS": "WIPRO",
    "TMCV.NS": "TMCV",
    "TMPV.NS": "TMPV",
}

SECTOR_MAP = {
    "ADANIENT": "Metals & Mining / Conglomerate",
    "ADANIPORTS": "Services / Infrastructure & Ports",
    "APOLLOHOSP": "Healthcare Services / Hospitals",
    "ASIANPAINT": "Consumer Durables / Paints",
    "AXISBANK": "Financial Services / Private Banking",
    "BAJAJ-AUTO": "Automobile / 2-Wheelers",
    "BAJFINANCE": "Financial Services / NBFC",
    "BAJAJFINSV": "Financial Services / Insurance & NBFC",
    "BEL": "Capital Goods / Defence Electronics",
    "BPCL": "Oil, Gas & Consumable Fuels / Refining",
    "BHARTIARTL": "Telecommunication / Cellular",
    "BRITANNIA": "Fast Moving Consumer Goods / Foods",
    "CIPLA": "Healthcare / Pharmaceuticals",
    "COALINDIA": "Oil, Gas & Consumable Fuels / Mining",
    "DRREDDY": "Healthcare / Pharmaceuticals",
    "EICHERMOT": "Automobile / Motorcycles & CV",
    "GRASIM": "Construction Materials / Diversified",
    "HCLTECH": "Information Technology / Software",
    "HDFCBANK": "Financial Services / Private Banking",
    "HDFCLIFE": "Financial Services / Life Insurance",
    "HEROMOTOCO": "Automobile / 2-Wheelers",
    "HINDALCO": "Metals & Mining / Aluminium & Copper",
    "HINDUNILVR": "Fast Moving Consumer Goods / Household",
    "ICICIBANK": "Financial Services / Private Banking",
    "INDUSINDBK": "Financial Services / Private Banking",
    "INFY": "Information Technology / Software",
    "ITC": "FMCG / Cigarettes & Diversified",
    "JSWSTEEL": "Metals & Mining / Steel",
    "KOTAKBANK": "Financial Services / Private Banking",
    "LT": "Construction / Infrastructure & EPC",
    "M&M": "Automobile / UVs & Tractors",
    "MARUTI": "Automobile / Passenger Cars",
    "NESTLEIND": "Fast Moving Consumer Goods / Foods",
    "NTPC": "Power / Thermal & Renewables",
    "ONGC": "Oil, Gas & Consumable Fuels / Exploration",
    "POWERGRID": "Power / Transmission Network",
    "RELIANCE": "Energy & Conglomerate / Telecom & Retail",
    "SBILIFE": "Financial Services / Life Insurance",
    "SBIN": "Financial Services / Public Sector Banking",
    "SHRIRAMFIN": "Financial Services / NBFC & CV Financing",
    "SUNPHARMA": "Healthcare / Pharmaceuticals",
    "TATACONSUM": "Fast Moving Consumer Goods / Beverages",
    "TATAMOTORS": "Automobile / Commercial & Passenger Vehicles",
    "TATASTEEL": "Metals & Mining / Steel",
    "TCS": "Information Technology / Software",
    "TECHM": "Information Technology / Telecom Services",
    "TITAN": "Consumer Durables / Jewellery & Watches",
    "TRENT": "Consumer Services / Fashion Retail",
    "ULTRACEMCO": "Construction Materials / Cement",
    "WIPRO": "Information Technology / Software",
    "TMCV": "Automobile / Commercial Vehicles",
    "TMPV": "Automobile / Passenger Electric Vehicles",
}

def generate_nifty50_universe():
    print(f"=== Generating AlphaNiti Nifty 50 Full Universe Snapshot ({len(NIFTY_50_TICKERS)} tickers) ===")
    
    # 1. Load scaler & ensemble
    scaler_path = PROJECT_ROOT / "models" / "scaler_params_v25.json"
    scaler = RobustMADScaler(c_clip=4.5)
    params = json.loads(scaler_path.read_text(encoding="utf-8"))
    scaler.c_clip = float(params.get("c_clip", 4.5))
    scaler.medians_ = np.array(params["medians"], dtype=np.float32)
    scaler.mads_ = np.array(params["mads"], dtype=np.float32)
    fcols = params.get("fcols", FEATURE_COLUMNS)

    ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v25.pt"
    ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
    for i, m in enumerate(models):
        m.load_state_dict(ckpt[f"member_{i}"])
        m.eval()
    ensemble = EvidentialEnsemble(models)
    print("Ensemble and Scaler loaded.")

    # 2. Download live 6mo data for all Nifty 50 stocks
    print("Downloading live Yahoo Finance data for all tickers...")
    t0 = time.time()
    raw = yf.download(list(NIFTY_50_TICKERS.keys()), period="6mo", interval="1d", group_by="ticker", progress=False)
    print(f"Downloaded in {time.time() - t0:.2f}s")

    rows = []
    for yf_t, clean in NIFTY_50_TICKERS.items():
        if yf_t not in raw:
            continue
        sub = raw[yf_t].dropna()
        if len(sub) < 60:
            continue
        for dt, r in sub.iterrows():
            c = float(r["Close"])
            v = float(r["Volume"])
            rows.append({
                "symbol": clean,
                "date": dt.strftime("%Y-%m-%d"),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": c,
                "volume": v,
                "turnover_cr": (c * v) / 1e7,
                "ret_1d": 0.0,
                "has_corporate_action": False,
                "cum_factor": 1.0,
            })

    # Also map TATAMOTORS to TMCV data so legacy queries work seamlessly
    tmcv_rows = [dict(r, symbol="TATAMOTORS") for r in rows if r["symbol"] == "TMCV"]
    rows.extend(tmcv_rows)

    df_all = pl.DataFrame(rows).sort(["symbol", "date"])
    df_all = df_all.with_columns([
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret_1d")
    ])

    print(f"Computing causal features across {len(df_all['symbol'].unique())} symbols ({len(df_all)} rows)...")
    df_feat, _ = compute_causal_features(df_all)
    X_norm = scaler.transform(df_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    df_scaled = df_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])

    lookback = 60
    signals = []
    charts = {}
    conformal_q_hat = 1.3644

    with torch.no_grad():
        for sym in sorted(df_scaled["symbol"].unique().to_list()):
            sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")
            if len(sub) < lookback:
                continue

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
            sigma_alea = float(aleatoric[0][0])
            sigma_epis = float(epistemic[0][0])
            pb = float(p_breach[0][0])
            sharpe_proxy = pred_ret / (sigma_alea + 1e-6)
            epi_var = sigma_epis ** 2

            sig_dict = {
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
                "aleatoric_vol_pct": round(sigma_alea * 100, 2),
                "epistemic_uncertainty": round(epi_var, 6),
                "drawdown_crash_prob": round(pb, 4),
                "sharpe_score": round(sharpe_proxy, 3),
            }
            signals.append(sig_dict)

            # Generate chart payload for symbol (candles + forecast cone)
            sub_candles = sub.tail(120).with_columns([
                pl.col("close").ewm_mean(span=12).alias("ema12"),
                pl.col("close").ewm_mean(span=26).alias("ema26"),
            ]).to_dicts()

            candle_list = []
            for r in sub_candles:
                candle_list.append({
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

            forecast_fan = []
            if candle_list:
                last_dt = pd.to_datetime(candle_list[-1]["time"])
                total_sigma_20d = np.sqrt(sigma_alea**2 + epi_var) * conformal_q_hat
                for step in range(1, 21):
                    frac = step / 20.0
                    proj_date = (last_dt + pd.Timedelta(days=int(step * 1.45))).strftime("%Y-%m-%d")
                    expected_px = c_price * (1.0 + pred_ret * frac)
                    band_width = c_price * total_sigma_20d * np.sqrt(frac) * 0.35
                    forecast_fan.append({
                        "time": proj_date,
                        "expected": round(float(expected_px), 2),
                        "upper_90": round(float(expected_px + band_width), 2),
                        "lower_90": round(float(expected_px - band_width), 2),
                    })

            charts[sym] = {
                "symbol": sym,
                "sector": SECTOR_MAP.get(sym, "Large Cap Equity"),
                "signal": sig_dict,
                "candles": candle_list,
                "forecast_fan": forecast_fan,
            }

    mkt_drift = float(np.mean([s["predicted_return_pct"] for s in signals])) / 100.0 if signals else 0.0
    mkt_crash_risk = float(np.mean([s["drawdown_crash_prob"] for s in signals])) if signals else 0.0

    # Resolve 4-Tier Hysteresis Gate state with calibrated empirical thresholds
    if mkt_drift < 0.0 or mkt_crash_risk >= 0.48:
        gate_tier = 3
        regime = "CASH_PRESERVATION"
    elif mkt_crash_risk >= 0.44:
        gate_tier = 2
        regime = "DE_GROSSING"
    elif mkt_crash_risk >= 0.40:
        gate_tier = 1
        regime = "SOFT_BUY_FREEZE"
    else:
        gate_tier = 0
        regime = "RISK_ON"

    # Compute composite decision scores
    for s in signals:
        score_res = compute_composite_decision_score(
            ticker=s["symbol"],
            gamma_bar_horizon=s["predicted_return_pct"] / 100.0,
            sigma_alea_horizon=s["aleatoric_vol_pct"] / 100.0,
            sigma_epis=s["epistemic_uncertainty"],
            p_breach=s["drawdown_crash_prob"],
            gate_tier=gate_tier,
            breach_ceiling=0.48,
        )
        s["decision_score"] = score_res.to_dict()
        s["composite_score"] = score_res.raw_score
        s["final_composite_score"] = score_res.final_score
        s["raw_decision_score"] = score_res.raw_score
        s["score_alpha"] = score_res.s_alpha
        s["score_confidence"] = score_res.s_confidence
        s["score_tail"] = score_res.s_tail
        s["score_friction"] = score_res.s_friction
        s["gate_multiplier"] = score_res.gate_multiplier
        s["gate_tier"] = score_res.gate_tier
        s["recommended_action"] = score_res.verdict
        s["signal_badge"] = score_res.verdict_badge
        s["rationale"] = score_res.rationale
        s["alpha_qualified"] = bool("BUY" in score_res.verdict or "ACCUMULATE" in score_res.verdict)
        # Update inside chart object
        if s["symbol"] in charts:
            charts[s["symbol"]]["signal"] = s

    # Sort signals descending by composite score, tie-breaking by sharpe
    signals.sort(key=lambda x: (x.get("composite_score", 0.0), x["sharpe_score"]), reverse=True)

    snapshot_payload = {
        "signals": {
            "regime": regime,
            "market_expected_drift_pct": round(mkt_drift * 100, 2),
            "market_average_crash_probability": round(mkt_crash_risk, 4),
            "conformal_q_hat": conformal_q_hat,
            "as_of_date": signals[0]["date"] if signals else "N/A",
            "data_source": "LIVE_YAHOO_FINANCE_NSE",
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "equities": signals,
        },
        "charts": charts,
    }

    # Save snapshot
    snap_p1 = PROJECT_ROOT / "snapshot.json"
    snap_p2 = PROJECT_ROOT / "frontend" / "snapshot.json"
    snap_p1.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")
    snap_p2.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")

    print(f"SUCCESS: Generated Nifty 50 snapshot with {len(signals)} equities and {len(charts)} chart histories!")
    print(f"Top 5 Ranked Equities:")
    for s in signals[:5]:
        print(f"  {s['symbol']:12} | Score: {s['composite_score']:5.1f} | Pred: {s['predicted_return_pct']:+5.2f}% | P_breach: {s['drawdown_crash_prob']:.2f} | Action: {s['recommended_action']}")

if __name__ == "__main__":
    generate_nifty50_universe()
