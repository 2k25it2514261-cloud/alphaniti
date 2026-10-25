"""
AlphaNiti v25.0: Pipeline Module 1 - Robust Normalization & Winsorization Engine
Implementation of Median Absolute Deviation (MAD) Normalization with Soft Outlier Winsorization

Mathematical Model:
  Median_i = median(x_i)
  MAD_i = 1.4826 * median(|x_i - Median_i|)
  z_{i,t} = (x_{i,t} - Median_i) / max(MAD_i, 1e-6)
  z_tilde_{i,t} = sign(z_{i,t}) * min(|z_{i,t}|, c_clip), where c_clip = 4.5
"""

import numpy as np
import polars as pl
import pandas as pd
from typing import Union, List, Optional, Dict


class RobustMADScaler:
    """
    Robust Feature Scaler and Outlier Winsorizer based on Median Absolute Deviation.
    Guarantees robustness to fat tails, extreme microstructure spikes, and price jumps.
    """
    def __init__(self, c_clip: float = 4.5, eps: float = 1e-6):
        self.c_clip = float(c_clip)
        self.eps = float(eps)
        self.medians_: Optional[np.ndarray] = None
        self.mads_: Optional[np.ndarray] = None
        self.feature_names_: Optional[List[str]] = None

    def fit(self, X: Union[np.ndarray, pd.DataFrame, pl.DataFrame]) -> "RobustMADScaler":
        """
        Computes the feature-wise median and MAD parameters on training data.
        """
        arr, names = self._to_numpy(X)
        self.feature_names_ = names

        # Median_i = median(x_i)
        self.medians_ = np.nanmedian(arr, axis=0)

        # Deviation: |x_i - Median_i|
        abs_dev = np.abs(arr - self.medians_)

        # MAD_i = 1.4826 * median(|x_i - Median_i|)
        self.mads_ = 1.4826 * np.nanmedian(abs_dev, axis=0)
        # Ensure scale is at least eps to prevent division by zero on static features
        self.mads_ = np.maximum(self.mads_, self.eps)

        return self

    def transform(self, X: Union[np.ndarray, pd.DataFrame, pl.DataFrame]) -> np.ndarray:
        """
        Applies robust standardization and soft winsorization at threshold c_clip.
        """
        if self.medians_ is None or self.mads_ is None:
            raise RuntimeError("RobustMADScaler must be fitted before transforming data.")

        arr, _ = self._to_numpy(X)

        # z_{i,t} = (x_{i,t} - Median_i) / max(MAD_i, 1e-6)
        z = (arr - self.medians_) / self.mads_

        # z_tilde_{i,t} = sign(z_{i,t}) * min(|z_{i,t}|, c_clip)
        z_clipped = np.sign(z) * np.minimum(np.abs(z), self.c_clip)

        return z_clipped

    def fit_transform(self, X: Union[np.ndarray, pd.DataFrame, pl.DataFrame]) -> np.ndarray:
        return self.fit(X).transform(X)

    def inverse_transform(self, Z: np.ndarray) -> np.ndarray:
        """
        Inverse transformation (un-standardizes unclipped components).
        """
        if self.medians_ is None or self.mads_ is None:
            raise RuntimeError("RobustMADScaler must be fitted before inverse transform.")
        return Z * self.mads_ + self.medians_

    def transform_polars(self, df: pl.DataFrame, cols: Optional[List[str]] = None) -> pl.DataFrame:
        """
        Applies scaling directly within a Polars DataFrame, returning standardized columns.
        """
        target_cols = cols or (self.feature_names_ if self.feature_names_ else df.columns)
        sub_df = df.select(target_cols)
        z_arr = self.transform(sub_df)
        
        exprs = []
        for idx, col in enumerate(target_cols):
            exprs.append(pl.Series(f"{col}_mad_norm", z_arr[:, idx], dtype=pl.Float32))
            
        return df.with_columns(exprs)

    def _to_numpy(self, X: Union[np.ndarray, pd.DataFrame, pl.DataFrame]):
        if isinstance(X, pl.DataFrame):
            return X.to_numpy(), X.columns
        elif isinstance(X, pd.DataFrame):
            return X.to_numpy(), X.columns.tolist()
        elif isinstance(X, np.ndarray):
            return X, None
        else:
            raise TypeError(f"Unsupported data type: {type(X)}")
