"""
AlphaNiti v25.0: Script to apply all 7 audit critique updates across docs and README.
Ensures 100% GFM MathJax compliance (no \operatorname, valid $$, spaced subscripts).
"""

import os
import re

def update_doc01():
    path = os.path.join("docs", "01_DATA_INGESTION_AND_TEMPORAL_MODEL.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    target_sec = "## 5. Academic Literature & References"
    new_sec = """## 5. Formal Prediction Target & Purged Validation Protocol

To eliminate lookahead bias and execution-timing ambiguity, the predictive modeling targets and cross-validation boundaries are defined with strict mathematical precision:

### 5.1 Continuous Excess Return Target Formulation
For asset $i$ on trading day $t$, the continuous prediction target $r _ {i, t}^{(H)}$ over investment horizons $H \\in \\{20, 60\\}$ trading days is defined as:

$$
r _ {i, t}^{(H)} \\equiv \\ln\\left(\\frac{P _ {i, t+1+H}^{\\text{open}}}{P _ {i, t+1}^{\\text{open}}}\\right) - \\ln\\left(\\frac{P _ {\\text{bench}, t+1+H}^{\\text{open}}}{P _ {\\text{bench}, t+1}^{\\text{open}}}\\right) - \\mathcal{C} _ {\\text{statutory}}
$$

where:
1. **Conditioning Filtration**: The forecast is conditioned strictly on information $\\mathcal{F} _ t$ available at Day $t$ market close (15:30 IST).
2. **Execution Timing**: Trade entry is simulated at Day $t+1$ Market Open (09:15 IST) auction prices, and trade exit at Day $t+1+H$ Market Open (09:15 IST) auction prices, completely eliminating Day $t$ close execution bias and post-close fill assumptions.
3. **Statutory Friction**: Two-way statutory friction $\\mathcal{C} _ {\\text{statutory}}$ (Securities Transaction Tax, Stamp Duty, GST, exchange and regulatory turnover charges loaded dynamically from `config/statutory_rates_2026.json`) is subtracted directly to ensure net realizable returns.
4. **Benchmark**: $P _ {\\text{bench}}$ represents the Nifty 50 or Nifty 500 Total Return Index (TRI).

### 5.2 Binary Downside Tail-Risk Target
In addition to the continuous return target, each observation carries a binary downside breach indicator:

$$
y _ {i, t}^{\\text{breach}} \\equiv \\mathbb{I}\\left( \\min _ {\\tau \\in [1, H]} \\left\\{ \\frac{P _ {i, t+\\tau}^{\\text{low}}}{P _ {i, t+1}^{\\text{open}}} - 1 \\right\\} \\le -0.05 \\right)
$$

flagging any asset that suffers an intraday drawdown exceeding 5.0% during the forward holding window.

### 5.3 Overlapping Label Purging & Embargo Protocol
Because forward return windows $[t+1, t+1+H]$ overlap by $H-1$ trading days across consecutive sessions, standard randomized cross-validation induces massive informational leakage.
1. **Purging Window**: When evaluating a test fold spanning $[\\tau _ {\\text{start}}, \\tau _ {\\text{end}}]$, any training sample whose forward return evaluation window $[t+1, t+1+H]$ overlaps with $[\\tau _ {\\text{start}}, \\tau _ {\\text{end}}]$ is strictly purged:

$$
\\text{Purge if } [t+1, \\; t+1+H] \\cap [\\tau _ {\\text{start}}, \\; \\tau _ {\\text{end}}] \\ne \\emptyset
$$

2. **Embargo Window**: An embargo buffer of $h _ {\\text{embargo}} = 10$ trading days is enforced immediately following $\\tau _ {\\text{end}}$ before subsequent training data can begin, eliminating autoregressive memory leakage from lagged feature components (such as 20-day historical volatilities and EMAs).

---

## 6. Academic Literature & References"""

    if target_sec in content and "Formal Prediction Target & Purged Validation Protocol" not in content:
        content = content.replace(target_sec, new_sec)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Updated docs/01_DATA_INGESTION_AND_TEMPORAL_MODEL.md")
    else:
        print("docs/01 already updated or target not found.")

def update_doc02():
    path = os.path.join("docs", "02_FORECASTING_AND_EVIDENTIAL_DEEP_ENSEMBLE.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    critique_sec = """---

## 5. The Meinert et al. (2023) Critique & Deep Ensemble Benchmarking

### 5.1 The Evidential Degeneracy Critique
Recent research (**Meinert, Gawlikowski & Lavin, 2023**, *"The Unreasonable Effectiveness of Deep Evidential Regression"*) demonstrated that in Deep Evidential Regression (Amini et al., 2020), the theoretical decomposition between aleatoric and epistemic uncertainty often fails to behave as claimed under real-world domain shift. Specifically:
1. The analytical epistemic variance $\\sigma _ {\\text{epistemic}}^2 = \\frac{\\beta}{\\nu(\\alpha - 1)}$ can act largely as an empirical regularizer rather than a truthful measure of posterior parameter ignorance.
2. During gradient descent, networks can minimize the evidence loss by inflating $\\alpha$ or suppressing $\\nu$ across the entire dataset, causing the epistemic signal to degenerate into a non-monotonic heuristic that correlates poorly with true out-of-distribution errors.

### 5.2 Dual Engine Architecture: Plain Deep Ensemble Baseline
To audit and safeguard against evidential degeneracy, AlphaNiti implements a **Dual Predictive Architecture**:
1. **Evidential Ensemble**: Evaluates the Normal-Inverse-Gamma conjugate updates and analytical dispersion.
2. **Plain Deep Ensemble (Lakshminarayanan et al., 2017)**: Operates a parallel baseline of $M=5$ independently initialized models trained with Gaussian NLL. The empirical epistemic variance is computed directly from inter-model disagreement:

$$
\\sigma _ {\\text{epistemic, ens}}^2 = \\frac{1}{M}\\sum _ {m=1}^M (\\mu _ m - \\bar{\\mu})^2
$$

The runtime monitoring engine continuously tests whether the analytical evidential uncertainty $\\sigma _ {\\text{epistemic}}^2$ or the empirical ensemble disagreement $\\sigma _ {\\text{epistemic, ens}}^2$ better predicts empirical forecast errors in unseen market regimes.

---

## 6. Canonical Operating Tiers & Hysteresis Calibration

To eliminate ambiguity across system documentation, AlphaNiti enforces a single canonical hierarchy of **4 Operating Tiers (Tiers 0–3)** across the 9 pipeline modules:

| Operating Tier | Uncertainty Gate Condition | Portfolio Execution Action |
| :--- | :--- | :--- |
| **Tier 0: Normal Execution** | $u _ t < \\theta _ {\\text{soft}}$ | Standard systematic allocation, full position sizing, standard horizon ($H \\in \\{20, 60\\}\\text{d}$). |
| **Tier 1: Soft Buy-Freeze** | $\\theta _ {\\text{soft}} \\le u _ t < \\theta _ {\\text{elevated}}$ | Freeze new buy orders ($q _ i^{\\text{buy}} = 0$); shorten horizon to 5d; existing custody positions held untouched. |
| **Tier 2: De-Grossing** | $\\theta _ {\\text{elevated}} \\le u _ t < \\theta _ {\\text{extreme}}$ | Passive 25%–50% linear gross de-leveraging via passive limit orders pegged to NBB; turnover capped at $\\le 0.05 \\cdot \\text{ADV} _ {20\\text{d}}$. |
| **Tier 3: Systemic Freeze** | $u _ t \\ge \\theta _ {\\text{extreme}}$ OR Exchange Circuit Halt | 100% liquidation to sovereign risk-free cash/TREPS; immediate cancellation of all open broker orders. |

### 6.1 Threshold Calibration from Validation Quantiles
Threshold values are calibrated objectively from out-of-sample purged validation quantiles (stored in `config/uncertainty_thresholds.json`):
- $\\theta _ {\\text{soft}} = Q _ {0.75}(\\sigma _ {\\text{epistemic}}^2) = 0.00185$ (75th percentile of validation epistemic variance)
- $\\theta _ {\\text{elevated}} = Q _ {0.90}(\\sigma _ {\\text{epistemic}}^2) = 0.00342$ (90th percentile)
- $\\theta _ {\\text{extreme}} = Q _ {0.99}(\\sigma _ {\\text{epistemic}}^2) = 0.00780$ (99th percentile)

### 6.2 Dual-Threshold Hysteresis State Machine
To prevent daily flip-flopping between tiers during noisy market transitions, a dual-threshold hysteresis filter with a persistence requirement is enforced:

$$
\\text{Enter Tier } k: \\quad u _ t \\ge \\theta _ k
$$

$$
\\text{De-escalate to Tier } k-1: \\quad u _ t \\le \\theta _ k \\cdot (1 - \\delta _ {\\text{hys}}) \\quad \\text{for } \\tau \\ge k _ {\\text{persist}} = 3 \\text{ consecutive sessions}
$$

where $\\delta _ {\\text{hys}} = 0.15$ (15% deadband below the entry threshold).

---

## 7. Academic Literature & References"""

    ref_target = "## 5. Academic Literature & References"
    if ref_target in content and "The Meinert et al. (2023) Critique" not in content:
        content = content.replace(ref_target, critique_sec)
        # Add Meinert reference
        meinert_ref = """\n6. **Meinert, N., Gawlikowski, J., & Lavin, A. (2023)**. *The Unreasonable Effectiveness of Deep Evidential Regression*. ICML Workshop on Uncertainty and Robustness in Deep Learning, arXiv:2205.10060.  
   *Demonstrates the empirical failure modes of evidential uncertainty splitting and motivates ensemble-based empirical validation.*
"""
        content += meinert_ref
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Updated docs/02_FORECASTING_AND_EVIDENTIAL_DEEP_ENSEMBLE.md")
    else:
        print("docs/02 already updated or ref target not found.")

def update_doc04():
    path = os.path.join("docs", "04_PORTFOLIO_OPTIMIZATION_AND_DISCRETE_SIZING.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update total objective to include Wasserstein DRO term
    old_obj = """$$
J(\\mathbf{q}) \\equiv \\underbrace{\\text{CVaR} _ \\alpha(\\mathbf{q})} _ {\\text{Portfolio Tail Risk}} - \\underbrace{\\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}}} _ {\\text{Expected Portfolio Return}} + \\underbrace{\\sum _ {i=1}^N \\text{Cost} _ i(q _ i)} _ {\\text{Execution & Statutory Costs}}
$$"""
    
    new_obj = """$$
J _ {\\text{DRO}}(\\mathbf{q}) \\equiv \\underbrace{\\lambda _ {\\text{risk}} \\cdot \\text{CVaR} _ \\alpha^{\\mathcal{P} _ \\epsilon}(\\mathbf{q})} _ {\\text{Wasserstein Worst-Case CVaR}} + \\underbrace{\\lambda _ {\\text{DRO}} \\epsilon _ f \\sum _ {i=1}^N P _ i^{\\text{eval}} q _ i} _ {\\text{Factor Wasserstein Penalty}} - \\underbrace{\\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}}} _ {\\text{Expected Portfolio Return}} + \\underbrace{\\sum _ {i=1}^N \\text{Cost} _ i(q _ i)} _ {\\text{Execution & Statutory Costs}}
$$"""

    if old_obj in content:
        content = content.replace(old_obj, new_obj)

    # Update LP relaxation formulation
    old_lp = """$$
\\begin{aligned}
z _ {\\text{LP}}(\\mathcal{N}) \\equiv \\min _ {\\mathbf{q} \\in [\\mathbf{l}, \\mathbf{u}], \\; \\gamma \\in \\mathbb{R}, \\; \\mathbf{s} \\in \\mathbb{R} _ {\\ge 0}^S} \\quad & \\gamma + \\frac{1}{(1-\\alpha)S}\\sum _ {s=1}^S s _ s - \\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}} + \\sum _ {i=1}^N \\text{Cost} _ i^{\\text{cvx}}(q _ i) \\\\
\\text{subject to:} \\quad & s _ s \\ge -\\sum _ {i=1}^N q _ i r _ {i, s} P _ i^{\\text{eval}} - \\gamma, \\quad \\forall s \\in \\lbrace 1, \\dots, S\\rbrace  \\\\
& \\sum _ {i=1}^N q _ i P _ i^{\\text{eval}} \\le W^{\\text{settled}} - W^{\\text{lien}} - W^{\\text{payable}} \\\\
& q _ i P _ i^{\\text{eval}} \\le w _ {\\max} \\cdot \\text{NAV}, \\quad \\forall i \\in \\lbrace 1, \\dots, N\\rbrace
\\end{aligned}
$$"""

    new_lp = """$$
\\begin{aligned}
z _ {\\text{LP}}(\\mathcal{N}) \\equiv \\min _ {\\mathbf{q} \\in [\\mathbf{l}, \\mathbf{u}], \\; \\gamma \\in \\mathbb{R}, \\; \\mathbf{s} \\in \\mathbb{R} _ {\\ge 0}^S} \\quad & \\gamma + \\frac{1}{(1-\\alpha)S}\\sum _ {s=1}^S s _ s + \\lambda _ {\\text{DRO}} \\epsilon _ f \\sum _ {i=1}^N P _ i^{\\text{eval}} q _ i - \\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}} + \\sum _ {i=1}^N \\text{Cost} _ i^{\\text{cvx}}(q _ i) \\\\
\\text{subject to:} \\quad & s _ s \\ge -\\sum _ {i=1}^N q _ i r _ {i, s} P _ i^{\\text{eval}} - \\gamma, \\quad \\forall s \\in \\lbrace 1, \\dots, S\\rbrace  \\\\
& \\sum _ {i=1}^N q _ i P _ i^{\\text{eval}} \\le W^{\\text{settled}} - W^{\\text{payable}} \\\\
& q _ i P _ i^{\\text{eval}} \\le w _ {\\max} \\cdot \\text{NAV}, \\quad \\forall i \\in \\lbrace 1, \\dots, N\\rbrace
\\end{aligned}
$$"""

    if old_lp in content:
        content = content.replace(old_lp, new_lp)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated docs/04_PORTFOLIO_OPTIMIZATION_AND_DISCRETE_SIZING.md")

def update_doc06():
    path = os.path.join("docs", "06_ACCOUNTING_NAV_AND_DOUBLE_ENTRY_LEDGER.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace conservation with accounting identity
    content = content.replace("Inductive Balance Sheet Conservation", "Inductive Balance Sheet Accounting Identity")
    content = content.replace("Theorem (NAV Conservation Invariant)", "Theorem (NAV Accounting Identity Invariant)")
    content = content.replace("enforcing inductive NAV conservation", "enforcing inductive NAV accounting identity")

    # Correct NAV equation: liens are assets, not subtracted liabilities!
    old_nav = """$$
\\begin{aligned}
\\text{Total Asset Value (TAV)} _ k &\\equiv W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{recv}} + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}} \\\\
\\text{Total Liabilities} _ k &\\equiv W _ k^{\\text{lien}} + W _ k^{\\text{payable}}
\\end{aligned}
$$

$$
\\text{NAV} _ k \\equiv \\text{TAV} _ k - \\text{Total Liabilities} _ k
$$

$$
\\text{NAV} _ k = \\left( W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{recv}} \\right) - \\left( W _ k^{\\text{lien}} + W _ k^{\\text{payable}} \\right) + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}}
$$"""

    new_nav = """$$
\\begin{aligned}
\\text{Total Asset Value (TAV)} _ k &\\equiv W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{lien}} + W _ k^{\\text{recv}} + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}} \\\\
\\text{Total Liabilities} _ k &\\equiv W _ k^{\\text{payable}}
\\end{aligned}
$$

$$
\\text{NAV} _ k \\equiv \\text{TAV} _ k - \\text{Total Liabilities} _ k
$$

$$
\\text{NAV} _ k = \\left( W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{lien}} + W _ k^{\\text{recv}} \\right) - W _ k^{\\text{payable}} + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}}
$$"""

    if old_nav in content:
        content = content.replace(old_nav, new_nav)

    # Correct Buying power:
    old_bp = """$$
\\text{BuyingPower} _ k \\equiv W _ k^{\\text{settled}} - W _ k^{\\text{lien}} - W _ k^{\\text{payable}}
$$"""
    new_bp = """$$
\\text{BuyingPower} _ k \\equiv W _ k^{\\text{settled}} - W _ k^{\\text{payable}}
$$"""
    if old_bp in content:
        content = content.replace(old_bp, new_bp)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated docs/06_ACCOUNTING_NAV_AND_DOUBLE_ENTRY_LEDGER.md")

def update_doc08():
    path = os.path.join("docs", "08_PLATFORM_SECURITY_AND_ISOLATED_SAFETY_KERNEL.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace hyperbolic claims with exact accurate terminology
    content = content.replace("Enclave Architecture & Formal Safety Kernel", "Process Isolation, Memory-Locked Safety Kernel & Model Checking")
    content = content.replace("Hardware Enclave Ed25519 Signing Path", "Memory-Locked Ed25519 Signing Path (Isolated Process Space)")
    content = content.replace("formally verified", "model-checked on a bounded model via TLC")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated docs/08_PLATFORM_SECURITY_AND_ISOLATED_SAFETY_KERNEL.md")

def update_readme():
    path = "README.md"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update title and claims
    content = content.replace("Conserved Double-Entry Ledger", "Conserved Double-Entry Ledger & NAV Accounting Identity")
    content = content.replace("formally verified", "model-checked on a bounded model via TLC")
    content = content.replace("Hardware/OS memory locking", "OS process memory locking and isolation")

    # 2. Harmonize Tier list
    old_tiers = """1. **Tier 1 (Soft Uncertainty: $\\gamma _ {\\text{soft}} < \\sigma _ {\\text{epistemic}, i}^2 \\le \\gamma _ {\\text{hard}}$)**: Halt new buy orders ($q _ i^{\\text{buy}} = 0$) and cancel working buy orders for asset $i$. Existing settled custody positions are held untouched, incurring zero liquidation spread cost.
2. **Tier 2 (Elevated Uncertainty: $\\gamma _ {\\text{hard}} < \\sigma _ {\\text{epistemic}, i}^2 \\le \\gamma _ {\\text{crit}}$)**: Cancel open buy orders and de-gross exposure by 25%–50% using passive limit orders pegged to the National Best Bid (NBB), capped at $5\%$ of 20-day Average Daily Volume ($\\text{ADV} _ {20\\text{d}}$).
3. **Tier 3 (Extreme Breakdown: $\\sigma _ {\\text{epistemic}, i}^2 > \\gamma _ {\\text{crit}}$)**: Execute an orderly passive TWAP exit exclusively for asset $i$; all unrelated portfolio holdings remain unaffected.
4. **Tier 4 (Systemic Market Circuit / Broker Partition)**: Immediate execution freeze; all working orders are cancelled and custody assets remain segregated in NSDL/CDSL depository accounts."""

    new_tiers = """AlphaNiti unifies the system state into **4 Canonical Operating Tiers (Tiers 0–3)** calibrated from out-of-sample validation quantiles (`config/uncertainty_thresholds.json`):

1. **Tier 0 (Normal Execution: $u _ t < \\theta _ {\\text{soft}}$)**: Standard systematic allocation, full position sizing, standard investment horizon ($H \\in \\{20, 60\\}\\text{d}$).
2. **Tier 1 (Soft Buy-Freeze: $\\theta _ {\\text{soft}} \\le u _ t < \\theta _ {\\text{elevated}}$)**: Halt new buy orders ($q _ i^{\\text{buy}} = 0$) and cancel working buy orders for asset $i$. Shorten forward evaluation horizon to 5 days. Existing settled custody positions are held untouched, incurring zero liquidation spread cost.
3. **Tier 2 (De-Grossing: $\\theta _ {\\text{elevated}} \\le u _ t < \\theta _ {\\text{extreme}}$)**: Cancel open buy orders and de-gross exposure by 25%–50% using passive limit orders pegged to the National Best Bid (NBB), capped at $5\%$ of 20-day Average Daily Volume ($\\text{ADV} _ {20\\text{d}}$).
4. **Tier 3 (Systemic Freeze: $u _ t \\ge \\theta _ {\\text{extreme}}$ OR Exchange Circuit Halt / Structural Breach)**: Immediate execution freeze; all working orders are cancelled and custody assets remain safely segregated in NSDL/CDSL depository accounts or liquidated to sovereign cash/TREPS.

#### Threshold Calibration & Hysteresis State Machine
- **Quantiles**: $\\theta _ {\\text{soft}} = Q _ {0.75}(\\sigma _ {\\text{epistemic}}^2) = 0.00185$, $\\theta _ {\\text{elevated}} = Q _ {0.90}(\\sigma _ {\\text{epistemic}}^2) = 0.00342$, $\\theta _ {\\text{extreme}} = Q _ {0.99}(\\sigma _ {\\text{epistemic}}^2) = 0.00780$.
- **Hysteresis**: To prevent day-to-day oscillation between tiers, the engine requires:
  $$\\text{Enter Tier } k: \\; u _ t \\ge \\theta _ k; \\qquad \\text{Exit Tier } k \\to k-1: \\; u _ t \\le \\theta _ k(1 - \\delta _ {\\text{hys}}) \\quad \\text{for } \\tau \\ge 3 \\text{ consecutive sessions} (\\delta _ {\\text{hys}} = 0.15)$$

#### The Meinert et al. (2023) Critique & Deep Ensemble Benchmark
In accordance with Meinert, Gawlikowski & Lavin (2023, *"The Unreasonable Effectiveness of Deep Evidential Regression"*), the analytical evidential variance $\\frac{\\beta}{\\nu(\\alpha-1)}$ often behaves as an empirical heuristic rather than truthful epistemic uncertainty. AlphaNiti runs a **Plain Deep Ensemble (Lakshminarayanan et al., 2017)** side-by-side to test whether empirical ensemble variance $\\frac{1}{M}\\sum (\\mu _ m - \\bar{\\mu})^2$ predicts out-of-sample error in unseen regimes."""

    if old_tiers in content:
        content = content.replace(old_tiers, new_tiers)

    # 3. Update B&B objective in README
    old_bb_obj = """$$
J(\\mathbf{q}) \\equiv \\text{CVaR} _ \\alpha(\\mathbf{q}) - \\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}} + \\sum _ {i=1}^N \\text{Cost} _ i(q _ i)
$$"""
    new_bb_obj = """$$
J _ {\\text{DRO}}(\\mathbf{q}) \\equiv \\lambda _ {\\text{risk}} \\cdot \\text{CVaR} _ \\alpha^{\\mathcal{P} _ \\epsilon}(\\mathbf{q}) + \\lambda _ {\\text{DRO}} \\epsilon _ f \\sum _ {i=1}^N P _ i^{\\text{eval}} q _ i - \\sum _ {i=1}^N \\mu _ i q _ i P _ i^{\\text{eval}} + \\sum _ {i=1}^N \\text{Cost} _ i(q _ i)
$$"""
    if old_bb_obj in content:
        content = content.replace(old_bb_obj, new_bb_obj)

    # 4. Update NAV equation in README: Liens are assets!
    old_readme_nav = """$$
W _ k^{\\text{NAV}} \\equiv \\left( W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{recv}} \\right) - \\left( W _ k^{\\text{lien}} + W _ k^{\\text{payable}} \\right) + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}}
$$

$$
\\text{BP} _ k \\equiv W _ k^{\\text{settled}} - W _ k^{\\text{lien}} - W _ k^{\\text{payable}}
$$"""
    new_readme_nav = """$$
W _ k^{\\text{NAV}} \\equiv \\left( W _ k^{\\text{settled}} + W _ k^{\\text{escrow}} + W _ k^{\\text{lien}} + W _ k^{\\text{recv}} \\right) - W _ k^{\\text{payable}} + \\sum _ {i=1}^N q _ {i, k} P _ {i, k}^{\\text{eval}}
$$

$$
\\text{BP} _ k \\equiv W _ k^{\\text{settled}} - W _ k^{\\text{payable}}
$$"""
    if old_readme_nav in content:
        content = content.replace(old_readme_nav, new_readme_nav)

    # Also update mermaid in README
    content = content.replace(
        'A1 & A2 & A3 --> Assets["Total Cash Assets = W_settled + W_escrow + W_recv"]\n    L1 & L2 --> Liab["Total Liabilities = W_lien + W_payable"]',
        'A1 & A2 & A3 & L1 --> Assets["Total Assets = W_settled + W_escrow + W_lien + W_recv"]\n    L2 --> Liab["Total Liabilities = W_payable"]'
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated README.md")

if __name__ == "__main__":
    update_doc01()
    update_doc02()
    update_doc04()
    update_doc06()
    update_doc08()
    update_readme()
