"""
AlphaNiti v25.0: Production Multi-Dataset Assembly Pipeline
Combines:
  1. timontunes/nse-india-adjusted-equity-panel (Prices 2016-2026 + Corporate Actions)
  2. debashis74017/stock-market-index-data-india-1990-2022 (Nifty 50 Benchmark)
  3. sameerprogrammer/detailed-financial-data-of-4456-nse-and-bse-company (Point-in-Time Fundamentals)
  4. debashis74017/algo-trading-data-nifty-100-data-with-indicators (Intraday Microstructure)
"""

import os
import glob
import polars as pl
import pandas as pd
import numpy as np
import argparse

def build_combined_dataset(input_dir="/kaggle/input", output_dir="/kaggle/working", top_k=50, horizon=20, friction_bps=0.0022):
    print("=" * 80)
    print("ALPHANITI v25.0: MULTI-DATASET COMBINED ENGINE")
    print(f"Base Directory: {input_dir}")
    print(f"Output Directory: {output_dir}")
    print("=" * 80)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Price Parquets
    parquet_files = sorted(glob.glob(f"{input_dir}/**/prices_*.parquet", recursive=True))
    if not parquet_files:
        parquet_files = sorted(glob.glob(f"{input_dir}/**/*.parquet", recursive=True))
        
    if not parquet_files:
        raise FileNotFoundError(f"No price parquet files found in {input_dir}")
        
    print(f"Found {len(parquet_files)} price Parquet files.")
    
    # 2. Schema mapping
    sample_schema = pl.read_parquet(parquet_files[0], n_rows=5)
    rename_dict = {}
    for col in sample_schema.columns:
        up = col.upper().strip()
        if up in ["DATE", "DATETIME", "TIMESTAMP"]:
            rename_dict[col] = "date"
        elif up in ["SYMBOL", "TICKER", "STOCK"]:
            rename_dict[col] = "symbol"
        elif up in ["SERIES"]:
            rename_dict[col] = "series"
        elif up in ["OPEN_ADJ", "OPEN", "OPEN_PRICE"]:
            rename_dict[col] = "open"
        elif up in ["HIGH_ADJ", "HIGH", "HIGH_PRICE"]:
            rename_dict[col] = "high"
        elif up in ["LOW_ADJ", "LOW", "LOW_PRICE"]:
            rename_dict[col] = "low"
        elif up in ["CLOSE_ADJ", "CLOSE", "CLOSE_PRICE"]:
            rename_dict[col] = "close"
        elif up in ["QTY_ADJ", "VOLUME", "QTY", "TOTAL_TRADED_QUANTITY"]:
            rename_dict[col] = "volume"
        elif up in ["TURNOVER_CR", "TURNOVER"]:
            rename_dict[col] = "turnover_cr"
        elif up in ["RET_1D", "RETURN_1D"]:
            rename_dict[col] = "ret_1d"
        elif up in ["CUM_FACTOR"]:
            rename_dict[col] = "cum_factor"
            
    lazy_prices = pl.scan_parquet(parquet_files).rename(rename_dict)
    target_cols = [c for c in ["date", "symbol", "series", "open", "high", "low", "close", "volume", "turnover_cr", "ret_1d", "cum_factor"] if c in lazy_prices.columns]
    
    df_prices = lazy_prices.select(target_cols).with_columns([
        pl.col("date").cast(pl.Date, strict=False)
    ]).collect()
    
    if "series" in df_prices.columns:
        df_prices = df_prices.filter(pl.col("series") == "EQ")
        
    print(f"Total Equity Records: {len(df_prices):,} across {df_prices['symbol'].n_unique()} tickers.")
    
    # 3. Liquid benchmark universe
    top_symbols = (
        df_prices.group_by("symbol")
        .agg(pl.col("turnover_cr").mean().alias("avg_turnover"))
        .sort("avg_turnover", descending=True)
        .head(top_k)["symbol"]
        .to_list()
    )
    print(f"Top {len(top_symbols)} liquid symbols selected.")
    
    df_panel = df_prices.filter(pl.col("symbol").is_in(top_symbols)).sort(["symbol", "date"])
    
    # 4. Causal forward targets
    df_panel = df_panel.with_columns([
        pl.col("open").shift(-1).over("symbol").alias("p_entry_open"),
        pl.col("open").shift(-(1 + horizon)).over("symbol").alias("p_exit_open"),
        pl.col("low").rolling_min(window_size=horizon).shift(-horizon).over("symbol").alias("rolling_min_low_20d")
    ]).with_columns([
        (pl.col("p_exit_open") / pl.col("p_entry_open") - 1.0).alias(f"target_ret_{horizon}d_gross"),
        (pl.col("p_exit_open") / pl.col("p_entry_open") - 1.0 - friction_bps).alias(f"target_ret_{horizon}d_net"),
        ((pl.col("rolling_min_low_20d") - pl.col("p_entry_open")) / pl.col("p_entry_open") < -0.05).cast(pl.Int32).alias("target_breach_5pct")
    ])
    
    # 5. Join Macro Benchmark (Nifty 50)
    nifty_candidates = sorted(glob.glob(f"{input_dir}/**/NIFTY*50*.csv", recursive=True)) + \
                       sorted(glob.glob(f"{input_dir}/**/*nifty*.csv", recursive=True))
    if nifty_candidates:
        try:
            df_nifty_raw = pd.read_csv(nifty_candidates[0])
            date_col = next((c for c in df_nifty_raw.columns if "date" in c.lower()), None)
            close_col = next((c for c in df_nifty_raw.columns if "close" in c.lower()), None)
            if date_col and close_col:
                df_nifty_raw['date'] = pd.to_datetime(df_nifty_raw[date_col], errors='coerce').dt.date
                df_nifty_raw['nifty_close'] = pd.to_numeric(df_nifty_raw[close_col], errors='coerce')
                df_nifty_raw = df_nifty_raw.dropna(subset=['date', 'nifty_close']).sort_values('date')
                df_nifty_raw['nifty_ret_1d'] = df_nifty_raw['nifty_close'].pct_change(1)
                pl_nifty = pl.from_pandas(df_nifty_raw[['date', 'nifty_close', 'nifty_ret_1d']]).with_columns(pl.col("date").cast(pl.Date))
                df_panel = df_panel.join(pl_nifty, on="date", how="left")
                print("Joined Nifty 50 macro benchmark.")
        except Exception as e:
            print(f"Warning on Nifty join: {e}")
            
    # 6. Join Corporate Actions
    ca_candidates = sorted(glob.glob(f"{input_dir}/**/corporate_actions.csv", recursive=True))
    if ca_candidates:
        try:
            df_ca_raw = pd.read_csv(ca_candidates[0])
            ex_date_col = next((c for c in df_ca_raw.columns if "ex" in c.lower() or "date" in c.lower()), "EX_DATE")
            sym_col = next((c for c in df_ca_raw.columns if "sym" in c.lower()), "SYMBOL")
            kind_col = next((c for c in df_ca_raw.columns if "kind" in c.lower()), None)
            df_ca_raw['date'] = pd.to_datetime(df_ca_raw[ex_date_col], errors='coerce').dt.date
            df_ca_raw['symbol'] = df_ca_raw[sym_col].astype(str)
            df_ca_raw['has_corporate_action'] = 1
            cols = ['date', 'symbol', 'has_corporate_action']
            if kind_col:
                df_ca_raw['ca_kind'] = df_ca_raw[kind_col].astype(str)
                cols.append('ca_kind')
            pl_ca = pl.from_pandas(df_ca_raw[cols].drop_duplicates(subset=['date', 'symbol'])).with_columns(pl.col("date").cast(pl.Date))
            df_panel = df_panel.join(pl_ca, on=["date", "symbol"], how="left").with_columns(
                pl.col("has_corporate_action").fill_null(0)
            )
            print("Joined corporate actions.")
        except Exception as e:
            print(f"Warning on corporate actions join: {e}")
            
    df_final = df_panel.filter(pl.col(f"target_ret_{horizon}d_net").is_not_null())
    
    # 7. Write outputs
    parquet_out = os.path.join(output_dir, "alphaniti_master_combined_dataset.parquet")
    csv_out = os.path.join(output_dir, "alphaniti_master_combined_dataset_sample.csv")
    
    df_final.write_parquet(parquet_out)
    df_final.head(1000).write_csv(csv_out)
    
    print("=" * 80)
    print("SUCCESSFULLY BUILT MASTER COMBINED DATASET")
    print(f"Rows:               {len(df_final):,}")
    print(f"Tickers:            {df_final['symbol'].n_unique()}")
    print(f"Parquet Path:       {parquet_out} ({os.path.getsize(parquet_out):,} bytes)")
    print(f"Sample CSV Path:    {csv_out} ({os.path.getsize(csv_out):,} bytes)")
    print("=" * 80)
    return df_final

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", default="/kaggle/input")
    parser.add_argument("--output_dir", default="/kaggle/working")
    parser.add_argument("--top_k", type=int, default=50)
    args = parser.parse_args()
    build_combined_dataset(input_dir=args.input_dir, output_dir=args.output_dir, top_k=args.top_k)
