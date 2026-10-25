"""
AlphaNiti v25.0: Pipeline Module 2 - 3D Sequence Tensor Constructor
Constructs standardized temporal tensors [N, L=60, D=15] for Evidential Deep Learning.
Key Invariants:
  1. Strict Rolling Lookback: Slices [t-L+1, t] without forward contamination.
  2. Out-of-Sample Scaling: RobustMADScaler is fitted solely on Train; Val/Test use frozen Train parameters.
  3. Paired Dual Targets: Continuous net return y_reg and binary tail risk y_clf.
"""

from typing import Tuple, List, Optional, Dict
import numpy as np
import polars as pl
import torch
from torch.utils.data import Dataset, DataLoader

from src.data.feature_engineering import compute_causal_features, FEATURE_COLUMNS
from src.data.robust_scaler import RobustMADScaler


class AlphaNitiSequenceDataset(Dataset):
    """
    PyTorch Dataset yielding:
      X:     [L=60, D=15] standardized temporal feature sequence
      y_reg: [1] continuous 20-day forward return net of 22 bps statutory friction
      y_clf: [1] binary downside risk label (1 if >5% drawdown breach, else 0)
    """
    def __init__(self, X: torch.Tensor, y_reg: torch.Tensor, y_clf: torch.Tensor):
        assert len(X) == len(y_reg) == len(y_clf), "Tensor length mismatch"
        self.X = X
        self.y_reg = y_reg
        self.y_clf = y_clf

    def __len__(self) -> int:
        return len(self.X)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.X[idx], self.y_reg[idx], self.y_clf[idx]


def build_3d_sequences(
    df_scaled: pl.DataFrame,
    feature_cols: List[str],
    lookback: int = 60,
    reg_target_col: str = "target_ret_20d_net",
    clf_target_col: str = "target_breach_5pct"
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Slices rolling sequences of shape [N, L=lookback, D=len(feature_cols)] per asset.
    """
    X_list, y_reg_list, y_clf_list = [], [], []
    symbols = df_scaled["symbol"].unique().to_list()

    for sym in symbols:
        sub = df_scaled.filter(pl.col("symbol") == sym).sort("date")
        n_rows = len(sub)
        if n_rows >= lookback:
            mat = sub.select(feature_cols).to_numpy()
            y_r = sub[reg_target_col].to_numpy()
            y_c = sub[clf_target_col].to_numpy()

            for t in range(lookback - 1, n_rows):
                seq = mat[t - lookback + 1 : t + 1] # shape (lookback, D)
                X_list.append(seq)
                y_reg_list.append(y_r[t])
                y_clf_list.append(y_c[t])

    X = torch.tensor(np.array(X_list), dtype=torch.float32)
    y_reg = torch.tensor(np.array(y_reg_list), dtype=torch.float32).unsqueeze(1)
    y_clf = torch.tensor(np.array(y_clf_list), dtype=torch.float32).unsqueeze(1)

    return X, y_reg, y_clf


class AlphaNitiDataPipeline:
    """
    End-to-End Pipeline orchestrating Feature Engineering, MAD Scaling,
    Sequence Slicing, and PyTorch DataLoader generation.
    """
    def __init__(self, lookback: int = 60, c_clip: float = 4.5):
        self.lookback = lookback
        self.c_clip = c_clip
        self.scaler = RobustMADScaler(c_clip=c_clip)
        self.is_fitted = False

    def fit_transform_train(self, train_df: pl.DataFrame) -> Tuple[AlphaNitiSequenceDataset, List[str]]:
        """
        Fits RobustMADScaler strictly on Train features and builds training Dataset.
        """
        df_feat, fcols = compute_causal_features(train_df)
        
        # Fit scaler on raw engineered features
        self.scaler.fit(df_feat.select(fcols))
        self.is_fitted = True

        # Transform
        X_norm = self.scaler.transform(df_feat.select(fcols))
        norm_cols = [f"{c}_norm" for c in fcols]
        df_scaled = df_feat.with_columns([
            pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
        ])

        X, y_reg, y_clf = build_3d_sequences(df_scaled, norm_cols, lookback=self.lookback)
        return AlphaNitiSequenceDataset(X, y_reg, y_clf), fcols

    def transform_eval(self, eval_df: pl.DataFrame) -> AlphaNitiSequenceDataset:
        """
        Transforms Validation or Test set using frozen parameters from Train scaler.
        """
        if not self.is_fitted:
            raise RuntimeError("Pipeline must be fitted on training data before evaluating.")

        df_feat, fcols = compute_causal_features(eval_df)
        X_norm = self.scaler.transform(df_feat.select(fcols))
        norm_cols = [f"{c}_norm" for c in fcols]
        df_scaled = df_feat.with_columns([
            pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
        ])

        X, y_reg, y_clf = build_3d_sequences(df_scaled, norm_cols, lookback=self.lookback)
        return AlphaNitiSequenceDataset(X, y_reg, y_clf)


def create_dataloaders(
    train_parquet_path: str,
    val_parquet_path: str,
    test_parquet_path: Optional[str] = None,
    lookback: int = 60,
    batch_size: int = 256,
    num_workers: int = 0
) -> Tuple[DataLoader, DataLoader, Optional[DataLoader], AlphaNitiDataPipeline, List[str]]:
    """
    Factory creating ready-to-train PyTorch DataLoaders for Train, Val, and Test.
    """
    train_df = pl.read_parquet(train_parquet_path)
    val_df = pl.read_parquet(val_parquet_path)

    pipeline = AlphaNitiDataPipeline(lookback=lookback)
    train_dataset, feature_names = pipeline.fit_transform_train(train_df)
    val_dataset = pipeline.transform_eval(val_df)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)

    test_loader = None
    if test_parquet_path and pl.os.path.exists(test_parquet_path):
        test_df = pl.read_parquet(test_parquet_path)
        test_dataset = pipeline.transform_eval(test_df)
        test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)

    return train_loader, val_loader, test_loader, pipeline, feature_names
