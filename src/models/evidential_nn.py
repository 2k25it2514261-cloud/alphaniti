"""
AlphaNiti v25.0: Pipeline Module 3 - Evidential Bayesian Deep Ensemble
Implementation of Deep Evidential Regression with Normal-Inverse-Gamma (NIG) Priors,
Uncertainty Decomposition, and Split-Conformal Prediction Calibration.

Mathematical Model:
  r_t ~ N(mu, sigma^2)
  mu | sigma^2 ~ N(gamma, sigma^2 / nu),  sigma^2 ~ Inv-Gamma(alpha, beta)
  Marginal p(r) = Student-t(2*alpha, gamma, beta*(nu+1) / (nu*alpha))

Uncertainty Decomposition:
  sigma_aleatoric^2 = beta / (alpha - 1)
  sigma_epistemic^2 = beta / (nu * (alpha - 1)) + Ensemble_Variance
"""

from typing import Tuple, List, Optional, Dict
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class EvidentialHead(nn.Module):
    """
    Parameterizes the 4 Normal-Inverse-Gamma (NIG) parameters:
      gamma: Mean prediction in R
      nu:    Virtual evidence in (0, inf)
      alpha: Shape parameter in (1, inf)
      beta:  Scale parameter in (0, inf)
    Along with binary downside breach probability p_breach in (0, 1).
    """
    def __init__(self, in_features: int):
        super().__init__()
        self.fc_reg = nn.Linear(in_features, 4)
        self.fc_clf = nn.Linear(in_features, 1)

    def forward(self, h: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        reg_out = self.fc_reg(h)
        gamma = reg_out[:, 0:1]
        nu = F.softplus(reg_out[:, 1:2]) + 1e-4
        alpha = F.softplus(reg_out[:, 2:3]) + 1.0 + 1e-4
        beta = F.softplus(reg_out[:, 3:4]) + 1e-4
        
        p_breach = torch.sigmoid(self.fc_clf(h))
        return gamma, nu, alpha, beta, p_breach


class EvidentialGRUModel(nn.Module):
    """
    Recurrent Evidential Architecture:
      Input Sequence X: [B, L=60, D=15]
      GRU Backbone: 2-layer GRU with layer normalization and dropout
      Output Head: NIG 4-tuple + Downside risk probability
    """
    def __init__(self, in_features: int = 15, hidden_dim: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.gru = nn.GRU(
            input_size=in_features,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.layer_norm = nn.LayerNorm(hidden_dim)
        self.head = EvidentialHead(in_features=hidden_dim)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        out, _ = self.gru(x)
        last_step = out[:, -1, :] # final time step
        normed = self.layer_norm(last_step)
        return self.head(normed)


class EvidentialLoss(nn.Module):
    """
    Total Evidential Objective:
      L = L_NLL + lambda_reg * L_reg + lambda_clf * L_BCE

    Where:
      L_NLL: Student-t Negative Log-Likelihood
      L_reg: Adversarial Evidence Regularizer |r - gamma| * (2*nu + alpha)
    """
    def __init__(self, lambda_reg: float = 0.05, lambda_clf: float = 1.0):
        super().__init__()
        self.lambda_reg = lambda_reg
        self.lambda_clf = lambda_clf
        self.bce = nn.BCELoss()

    def forward(
        self,
        gamma: torch.Tensor,
        nu: torch.Tensor,
        alpha: torch.Tensor,
        beta: torch.Tensor,
        p_breach: torch.Tensor,
        y_reg: torch.Tensor,
        y_clf: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        # Omega = 2 * beta * (1 + nu)
        omega = 2.0 * beta * (1.0 + nu)

        # Student-t NLL terms
        term1 = 0.5 * torch.log(np.pi / nu)
        term2 = -alpha * torch.log(omega)
        term3 = (alpha + 0.5) * torch.log(nu * (y_reg - gamma) ** 2 + omega)
        term4 = torch.lgamma(alpha) - torch.lgamma(alpha + 0.5)

        loss_nll = torch.mean(term1 + term2 + term3 + term4)

        # Evidence Regularizer
        loss_reg = torch.mean(torch.abs(y_reg - gamma) * (2.0 * nu + alpha))

        # Downside Risk BCE
        loss_bce = self.bce(p_breach, y_clf)

        total_loss = loss_nll + (self.lambda_reg * loss_reg) + (self.lambda_clf * loss_bce)

        metrics = {
            "total_loss": float(total_loss.item()),
            "nll_loss": float(loss_nll.item()),
            "reg_loss": float(loss_reg.item()),
            "bce_loss": float(loss_bce.item()),
        }
        return total_loss, metrics


class EvidentialEnsemble:
    """
    Deep Evidential Ensemble of M diverse networks:
      Mixture reduction pooling:
        gamma_bar = (1/M) * sum(gamma_m)
        sigma_aleatoric^2 = (1/M) * sum( beta_m / (alpha_m - 1) )
        sigma_epistemic^2 = (1/M) * sum( beta_m / (nu_m * (alpha_m - 1)) ) + Var_between(gamma_m)
    """
    def __init__(self, models: List[EvidentialGRUModel]):
        self.models = models

    def predict(self, x: torch.Tensor) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Runs forward pass across all M models and computes analytical uncertainty decomposition.
        Returns: (gamma_bar, sigma_aleatoric, sigma_epistemic, p_breach_bar)
        """
        for m in self.models:
            m.eval()

        gammas, nus, alphas, betas, breaches = [], [], [], [], []
        with torch.no_grad():
            for m in self.models:
                g, n, a, b, pb = m(x)
                gammas.append(g.cpu().numpy())
                nus.append(n.cpu().numpy())
                alphas.append(a.cpu().numpy())
                betas.append(b.cpu().numpy())
                breaches.append(pb.cpu().numpy())

        # Stack across ensemble dimension [M, B, 1]
        G = np.array(gammas)
        N = np.array(nus)
        A = np.array(alphas)
        B = np.array(betas)
        PB = np.array(breaches)

        # 1. Ensemble Mean Forecast
        gamma_bar = np.mean(G, axis=0) # [B, 1]
        p_breach_bar = np.mean(PB, axis=0)

        # 2. Aleatoric Variance: E[ beta / (alpha - 1) ]
        aleatoric_var = np.mean(B / (A - 1.0), axis=0)
        sigma_aleatoric = np.sqrt(np.maximum(aleatoric_var, 1e-8))

        # 3. Epistemic Variance: E[ beta / (nu * (alpha - 1)) ] + Var_between(gamma)
        within_epistemic = np.mean(B / (N * (A - 1.0)), axis=0)
        between_epistemic = np.var(G, axis=0)
        total_epistemic_var = within_epistemic + between_epistemic
        sigma_epistemic = np.sqrt(np.maximum(total_epistemic_var, 1e-8))

        return gamma_bar, sigma_aleatoric, sigma_epistemic, p_breach_bar


class SplitConformalCalibrator:
    """
    Split-Conformal Prediction Calibrator with finite-sample coverage guarantees:
      P( r_{t+1} in [gamma - q * sigma_total,  gamma + q * sigma_total] ) >= 1 - epsilon_cal
    """
    def __init__(self, target_coverage: float = 0.90):
        self.target_coverage = target_coverage
        self.epsilon_cal = 1.0 - target_coverage
        self.q_hat: Optional[float] = None

    def calibrate(self, y_true: np.ndarray, y_pred_mean: np.ndarray, y_sigma_total: np.ndarray) -> float:
        """
        Computes non-conformity scores on calibration holdout and calculates (1 - eps)-quantile.
        """
        assert len(y_true) == len(y_pred_mean) == len(y_sigma_total), "Dimension mismatch"
        non_conformity = np.abs(y_true - y_pred_mean) / np.maximum(y_sigma_total, 1e-6)

        K = len(non_conformity)
        # Conformal finite-sample adjusted quantile
        rank_idx = int(np.ceil((K + 1) * (1.0 - self.epsilon_cal)))
        rank_idx = min(K, max(1, rank_idx)) - 1 # 0-indexed

        sorted_scores = np.sort(non_conformity.flatten())
        self.q_hat = float(sorted_scores[rank_idx])
        return self.q_hat

    def predict_intervals(self, y_pred_mean: np.ndarray, y_sigma_total: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if self.q_hat is None:
            raise RuntimeError("Calibrator must be calibrated before generating intervals.")
        margin = self.q_hat * y_sigma_total
        return y_pred_mean - margin, y_pred_mean + margin
