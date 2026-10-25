"""
AlphaNiti v25.0: Pipeline Module 3 - Evidential Model Trainer
Handles mini-batch training, validation early stopping, and ensemble training.
"""

import os
import copy
from typing import Dict, List, Tuple, Optional
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from src.models.evidential_nn import EvidentialGRUModel, EvidentialLoss, EvidentialEnsemble


class EvidentialTrainer:
    """
    Trains an individual EvidentialGRUModel on train_loader with early stopping on val_loader.
    """
    def __init__(
        self,
        model: EvidentialGRUModel,
        lr: float = 0.001,
        weight_decay: float = 1e-5,
        lambda_reg: float = 0.05,
        device: Optional[str] = None
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)
        self.loss_fn = EvidentialLoss(lambda_reg=lambda_reg)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=weight_decay)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, mode="min", factor=0.5, patience=2)
        self.history: Dict[str, List[float]] = {"train_loss": [], "val_loss": [], "val_nll": []}
        self.best_state = None
        self.best_val_loss = float("inf")

    def train_epoch(self, train_loader: DataLoader, grad_clip: float = 1.0) -> Dict[str, float]:
        self.model.train()
        total_loss, total_nll, total_reg, total_bce = 0.0, 0.0, 0.0, 0.0
        n_batches = 0

        for X, y_reg, y_clf in train_loader:
            X = X.to(self.device)
            y_reg = y_reg.to(self.device)
            y_clf = y_clf.to(self.device)

            self.optimizer.zero_grad()
            gamma, nu, alpha, beta, p_breach = self.model(X)
            loss, metrics = self.loss_fn(gamma, nu, alpha, beta, p_breach, y_reg, y_clf)

            loss.backward()
            nn.utils.clip_grad_norm_(self.model.parameters(), grad_clip)
            self.optimizer.step()

            total_loss += metrics["total_loss"]
            total_nll += metrics["nll_loss"]
            total_reg += metrics["reg_loss"]
            total_bce += metrics["bce_loss"]
            n_batches += 1

        return {
            "loss": total_loss / max(1, n_batches),
            "nll": total_nll / max(1, n_batches),
            "reg": total_reg / max(1, n_batches),
            "bce": total_bce / max(1, n_batches),
        }

    def evaluate(self, val_loader: DataLoader) -> Dict[str, float]:
        self.model.eval()
        total_loss, total_nll, total_reg, total_bce = 0.0, 0.0, 0.0, 0.0
        n_batches = 0

        with torch.no_grad():
            for X, y_reg, y_clf in val_loader:
                X = X.to(self.device)
                y_reg = y_reg.to(self.device)
                y_clf = y_clf.to(self.device)

                gamma, nu, alpha, beta, p_breach = self.model(X)
                _, metrics = self.loss_fn(gamma, nu, alpha, beta, p_breach, y_reg, y_clf)

                total_loss += metrics["total_loss"]
                total_nll += metrics["nll_loss"]
                total_reg += metrics["reg_loss"]
                total_bce += metrics["bce_loss"]
                n_batches += 1

        return {
            "loss": total_loss / max(1, n_batches),
            "nll": total_nll / max(1, n_batches),
            "reg": total_reg / max(1, n_batches),
            "bce": total_bce / max(1, n_batches),
        }

    def fit(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        epochs: int = 10,
        patience: int = 3,
        verbose: bool = True
    ) -> Dict[str, List[float]]:
        patience_counter = 0

        for ep in range(1, epochs + 1):
            train_m = self.train_epoch(train_loader)
            val_m = self.evaluate(val_loader)

            self.scheduler.step(val_m["loss"])
            self.history["train_loss"].append(train_m["loss"])
            self.history["val_loss"].append(val_m["loss"])
            self.history["val_nll"].append(val_m["nll"])

            if verbose:
                print(
                    f"Epoch {ep:02d}/{epochs:02d} | "
                    f"Train Loss: {train_m['loss']:.4f} (NLL: {train_m['nll']:.4f}) | "
                    f"Val Loss: {val_m['loss']:.4f} (NLL: {val_m['nll']:.4f}) | "
                    f"LR: {self.optimizer.param_groups[0]['lr']:.2e}"
                )

            if val_m["loss"] < self.best_val_loss:
                self.best_val_loss = val_m["loss"]
                self.best_state = copy.deepcopy(self.model.state_dict())
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    if verbose:
                        print(f"Early stopping triggered at epoch {ep} (Best Val Loss: {self.best_val_loss:.4f})")
                    break

        if self.best_state is not None:
            self.model.load_state_dict(self.best_state)

        return self.history


def train_evidential_ensemble(
    train_loader: DataLoader,
    val_loader: DataLoader,
    n_models: int = 3,
    in_features: int = 15,
    hidden_dim: int = 64,
    epochs: int = 5,
    lr: float = 0.001,
    device: Optional[str] = None
) -> EvidentialEnsemble:
    """
    Trains an ensemble of n_models diverse evidential networks with different random initializations.
    """
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    ensemble_models = []

    for m_idx in range(n_models):
        print(f"\n--- Training Evidential Ensemble Member {m_idx + 1}/{n_models} ---")
        torch.manual_seed(42 + m_idx * 1337)
        model = EvidentialGRUModel(in_features=in_features, hidden_dim=hidden_dim).to(dev)
        trainer = EvidentialTrainer(model, lr=lr, device=dev)
        trainer.fit(train_loader, val_loader, epochs=epochs, verbose=True)
        ensemble_models.append(trainer.model)

    return EvidentialEnsemble(ensemble_models)
