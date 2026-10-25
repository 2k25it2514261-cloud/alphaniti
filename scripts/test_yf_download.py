import sys
import yfinance as yf
import polars as pl
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

tickers = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "SBIN.NS", "ITC.NS", "LT.NS"]
print(f"Fetching real market data for {len(tickers)} tickers via live Yahoo Finance API...")

df_raw = yf.download(tickers, period="6mo", interval="1d", group_by="ticker", auto_adjust=False)
print("Download complete. Columns:", type(df_raw))
print("Sample index dates:", df_raw.index[-5:])
