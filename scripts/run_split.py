import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import polars as pl
from src.data.dataset_splitter import PurgedWalkForwardSplitter

parquet_path = r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\kaggle_out\alphaniti_master_combined_dataset.parquet"
output_dir = r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\alphaniti\data\splits"

df = pl.read_parquet(parquet_path)
splitter = PurgedWalkForwardSplitter(
    train_ratio=0.70,
    val_ratio=0.15,
    test_ratio=0.15,
    purge_horizon_days=20,
    date_col="date"
)

manifest = splitter.save_splits(df, output_dir=output_dir)
print("\nSplit Manifest Summary:")
print(f"Total Rows:     {manifest.total_rows:,}")
print(f"Train Rows:     {manifest.train_rows:,} ({manifest.train_rows/manifest.total_rows*100:.1f}%) | Range: {manifest.train_dates[0]} to {manifest.train_dates[1]}")
print(f"Val Rows:       {manifest.val_rows:,} ({manifest.val_rows/manifest.total_rows*100:.1f}%) | Range: {manifest.val_dates[0]} to {manifest.val_dates[1]}")
print(f"Test Rows:      {manifest.test_rows:,} ({manifest.test_rows/manifest.total_rows*100:.1f}%) | Range: {manifest.test_dates[0]} to {manifest.test_dates[1]}")
print(f"Purged Rows:    {manifest.purged_rows:,} ({manifest.purged_rows/manifest.total_rows*100:.1f}%)")
