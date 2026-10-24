# AlphaNiti: Theoretical Architecture & Mathematical Specification v2.0
**Status:** Approved Quantitative Architecture Blueprint  
**Target Asset Class:** Indian Listed Cash Equities (National Stock Exchange - NSE)  
**Investment Horizons:** $H \in \{20, 60\}$ Trading Sessions  

---

## 1. Executive Summary of the Theoretical Upgrade

This specification upgrades AlphaNiti from a point-return forecasting tool to an **institutional-grade, distribution-aware equity research and decision platform**. 

It formally codifies the **Tripartite Separation of Concerns**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER A: CONDITIONAL RETURN DISTRIBUTION FORECASTING                        │
│ Inputs: Bitemporal PIT Features X(i, t)                                      │
│ Output: Continuous Distribution F_H(r | X_t) & Calibrated Tail Quantiles    │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER B: DECISION THEORY, CONSTRAINTS & RISK GATES                          │
│ Evaluator: Expected Utility Optimization, Friction Models & Veto Enforcer   │
│ Output: Candidate Decision State ('Buy Candidate', 'Hold', 'Avoid')         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER C: NESTED WALK-FORWARD EVALUATION & AUDIT                              │
│ Framework: Date-Clustered Purged Chronological Backtesting & Reproducibility│
│ Metrics: Distributional Scores (CRPS), Brier Calibration, Realized Net P&L │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mathematical Formalization of the Three Layers

### Layer A — Forecasting (The Conditional Return Distribution)
Instead of predicting a single point scalar $\widehat{R}_{i,t,H}$, Layer A estimates the full conditional cumulative distribution function (CDF) of excess returns:
$$F_{H}(r \mid X_{i,t}) = P(R_{i,t,H}^{\text{excess}} \le r \mid X_{i,t})$$

#### Key Derived Objects:
1. **Expected Excess Return:**
   $$\mathbb{E}[R_{i,t,H}^{\text{excess}} \mid X_{i,t}] = \int_{-\infty}^{\infty} r \, dF_H(r \mid X_{i,t})$$
2. **Conditional Quantiles (Quantile Regression Head):**
   $$q_{\alpha}(i, t, H) = \inf \{ r : F_H(r \mid X_{i,t}) \ge \alpha \}, \quad \alpha \in \{0.05, 0.25, 0.50, 0.75, 0.95\}$$
3. **Downside Tail-Risk Breach Probability:**
   For a predefined risk threshold $\theta \in \{-5\%, -10\%\}$:
   $$P_{\text{downside}}(i, t, H) = F_H(\theta \mid X_{i,t}) = P(R_{i,t,H}^{\text{excess}} \le \theta \mid X_{i,t})$$
4. **Epistemic Uncertainty:**
   Quantified via ensemble variance or prediction interval width:
   $$U(i, t, H) = q_{0.95}(i, t, H) - q_{0.05}(i, t, H)$$

---

### Layer B — Decision Theory & Risk Gating
Layer B converts the distributional forecast from Layer A into actionable portfolio candidate states under user constraints, friction models, and strict asymmetric veto gates.

#### 1. Net Realizable Return & Cost Modeling
For a trade evaluated on end-of-day date $t$, execution occurs strictly at date $t+1$ (Day $t+1$ Open or VWAP):
$$R_{i,t,H}^{\text{net}} = \frac{P_{i, t+1+H}^{\text{exit}} - P_{i, t+1}^{\text{entry}} - \text{Friction}_{i}}{P_{i, t+1}^{\text{entry}}}$$
$$\text{Friction}_{i} = \text{STT} + \text{Exchange Fees} + \text{SEBI Turnover} + \text{GST} + \text{Stamp Duty} + \text{Slippage}(V_{i,t})$$

#### 2. Asymmetric Non-Compensatory Veto Logic
Let $\mathcal{V}$ be the set of mandatory veto rules defined in the investor's compiled strategy (e.g., $\text{Debt/Equity} \le 1.0$, $\text{Promoter Pledge} \le 5\%$, $\text{Circuit Lockout} = \text{False}$).
$$\text{DecisionState}(i, t) = 
\begin{cases} 
\text{Avoid}, & \text{if } \exists v \in \mathcal{V} \text{ s.t. } v(X_{i,t}) = \text{Fail} \\
\text{Insufficient Evidence}, & \text{if } \exists v \in \mathcal{V} \text{ s.t. } v(X_{i,t}) = \text{Missing} \\
\text{Buy Candidate}, & \text{if } \forall v \in \mathcal{V}, v = \text{Pass} \;\land\; \mathbb{E}[R_{i,t,H}^{\text{net}}] \ge \gamma_{\text{hurdle}} \;\land\; P_{\text{downside}} \le \lambda_{\text{tol}} \\
\text{Hold / Neutral}, & \text{otherwise}
\end{cases}$$

> **Core Axiom:** A failed mandatory veto $v$ can **never be overridden by a high expected return**. An algorithm is mathematically forbidden from averaging away risk.

---

### Layer C — Evaluation & Robustness Protocol

To prevent repeated-testing bias and cross-sectional correlation distortion:

1. **Nested Walk-Forward Cross-Validation:**
   - **Outer Loop:** Chronological splits testing forward out-of-sample periods.
   - **Inner Loop:** Purged time-series folds for feature selection, hyperparameter tuning, and probability calibration.
   - **Untouched Final Holdout:** The most recent calendar period (e.g., last 18 months) is locked and consulted only once.
2. **Date-Clustered Dependence:**
   - Equities on the same date share systemic market shocks (Nifty beta). Standard errors and $t$-statistics must be **date-clustered** or two-way clustered (Date + Sector).
3. **Distributional Loss Functions:**
   - Evaluated using **Continuous Ranked Probability Score (CRPS)** and **Brier Score** in the tails, rather than simplistic classification accuracy.

---

## 3. The 10 Implementation Directives

| # | Directive | Implementation Specification |
| :- | :--- | :--- |
| **01** | **Conditional Return Distribution** | Replace scalar prediction with Quantile Gradient Boosted Trees and parametric distribution modeling for $H \in \{20, 60\}$. |
| **02** | **Formal Target & Timeline** | Executable entry at Day $t+1$ Open; benchmark subtracted over exact same holding interval; net of statutory Indian transaction costs. |
| **03** | **Nested Purged Walk-Forward** | Date-grouped temporal folds; $H$-day embargo gap between train and test boundaries; inner fold calibration. |
| **04** | **Cross-Sectional & Regime Awareness** | Date-clustered standard errors; explicit performance reporting across Bull, Bear, Sideways, and High-Volatility regimes. |
| **05** | **Bitemporal Point-in-Time Data** | Every record stores `real_world_period_end`, `source_publication_time`, and `system_availability_time`. Restatements maintain immutable lineage. |
| **06** | **Empirical News Decay Model** | Estimate half-life $\lambda$ strictly within training folds; run ablations: (Price vs Fundamentals vs News vs Multimodal Fusion). |
| **07** | **Formal Meta-Labeling** | Primary model forecasts distribution; secondary model evaluates execution probability on out-of-fold data. |
| **08** | **Probability Calibration** | Temporally valid out-of-fold Isotonic/Sigmoid calibration; Brier score reporting; tail-risk calibration curves. |
| **09** | **Nested Model Selection** | Compare ElasticNet, LightGBM, and Factor Baselines; learn ensemble weights out-of-fold; test shallow neural network challenger. |
| **10** | **Decision Theory & Tradability** | Check ASM/GSM surveillance lists, upper/lower circuit locks, and liquidity-adjusted position sizing (Fractional Kelly). |

---

## 4. Verification & Acceptance Criteria Matrix

Before deploying any model into production, it must clear this strict verification matrix:

| Verification Area | Required Quantitative Proof |
| :--- | :--- |
| **Data Integrity** | $0\%$ lookahead leakage confirmed via automated audit script; all filings verified against exchange timestamps. |
| **Forecast Quality** | Out-of-sample CRPS and MAE beat equal-weighted sector and historical momentum baselines with statistical significance ($p < 0.05$). |
| **Calibration** | Reliability curve slope within $[0.90, 1.10]$ and Brier score $< 0.15$ on out-of-sample tail breach probabilities. |
| **Economic Value** | Net Sharpe Ratio $> 1.0$ and positive excess return after full STT, GST, brokerage, and bid-ask slippage. |
| **Tradability** | Zero fills assumed on days where equity is locked in upper or lower circuits. |
| **Reproducibility** | Every published decision can be bit-for-bit reproduced from its code commit hash, model checkpoint, and database snapshot. |
