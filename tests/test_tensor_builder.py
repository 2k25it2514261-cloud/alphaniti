"""
AlphaNiti v25.0: Pipeline Module 2 - Tensor Constructor Verification Suite
Tests:
  1. Causal Feature Engineering (15 columns, zero nulls)
  2. Rolling Lookback Windowing [N, L=60, D=15]
  3. Out-of-Sample Scaler Parameter Freezing
  4. PyTorch Dataset & DataLoader Iteration
  5. Zero-NaN & Zero-Inf Invariants
"""

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import torch
import polars as pl
import numpy as np

from src.data.feature_engineering import compute_causal_features, FEATURE_COLUMNS
from src.data.tensor_builder import (
    AlphaNitiDataPipeline,
    AlphaNitiSequenceDataset,
    create_dataloaders,
)


class TestTensorBuilder(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.train_path = os.path.join("data", "splits", "train.parquet")
        cls.val_path = os.path.join("data", "splits", "val.parquet")

    def test_01_feature_engineering_integrity(self):
        """Verifies 15 causal features are engineered with 0 nulls."""
        df = pl.read_parquet(self.train_path).head(1000)
        df_feat, fcols = compute_causal_features(df)

        self.assertEqual(len(fcols), 15)
        self.assertEqual(fcols, FEATURE_COLUMNS)

        for col in fcols:
            self.assertEqual(df_feat[col].null_count(), 0, f"Nulls found in {col}")

    def test_02_pipeline_fit_and_sequence_shapes(self):
        """Verifies 3D tensor slicing [N, 60, 15] and target alignment."""
        df_train = pl.read_parquet(self.train_path).filter(pl.col("symbol") == "RELIANCE")
        pipeline = AlphaNitiDataPipeline(lookback=60, c_clip=4.5)
        
        train_ds, fcols = pipeline.fit_transform_train(df_train)
        self.assertTrue(pipeline.is_fitted)
        self.assertGreater(len(train_ds), 100)

        sample_X, sample_yr, sample_yc = train_ds[0]
        self.assertEqual(sample_X.shape, torch.Size([60, 15]))
        self.assertEqual(sample_yr.shape, torch.Size([1]))
        self.assertEqual(sample_yc.shape, torch.Size([1]))

        # Check bounds
        self.assertTrue(torch.all(torch.abs(sample_X) <= 4.5))

    def test_03_out_of_sample_transform_no_leakage(self):
        """Verifies validation set transforms using frozen training parameters."""
        df_train = pl.read_parquet(self.train_path).filter(pl.col("symbol") == "TCS")
        df_val = pl.read_parquet(self.val_path).filter(pl.col("symbol") == "TCS")

        pipeline = AlphaNitiDataPipeline(lookback=60, c_clip=4.5)
        _, _ = pipeline.fit_transform_train(df_train)
        
        train_medians = pipeline.scaler.medians_.copy()
        train_mads = pipeline.scaler.mads_.copy()

        # Transform val
        val_ds = pipeline.transform_eval(df_val)
        self.assertGreater(len(val_ds), 50)

        # Ensure parameters did not mutate
        np.testing.assert_array_equal(pipeline.scaler.medians_, train_medians)
        np.testing.assert_array_equal(pipeline.scaler.mads_, train_mads)

        val_X, _, _ = val_ds[0]
        self.assertFalse(torch.isnan(val_X).any())
        self.assertFalse(torch.isinf(val_X).any())

    def test_04_full_dataloader_generation(self):
        """Tests end-to-end PyTorch DataLoader factory."""
        train_loader, val_loader, _, pipeline, fnames = create_dataloaders(
            self.train_path,
            self.val_path,
            lookback=60,
            batch_size=256
        )

        self.assertEqual(len(fnames), 15)
        self.assertGreater(len(train_loader), 100)
        self.assertGreater(len(val_loader), 20)

        batch_X, batch_yr, batch_yc = next(iter(train_loader))
        self.assertEqual(batch_X.shape, torch.Size([256, 60, 15]))
        self.assertEqual(batch_yr.shape, torch.Size([256, 1]))
        self.assertEqual(batch_yc.shape, torch.Size([256, 1]))

        # Validate zero NaNs
        self.assertFalse(torch.isnan(batch_X).any())
        self.assertFalse(torch.isnan(batch_yr).any())
        self.assertFalse(torch.isnan(batch_yc).any())


if __name__ == "__main__":
    unittest.main()
