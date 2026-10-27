"""
Updates Cell 13 in kaggle_notebook/alphaniti_v25_pipeline.ipynb to reflect the exact NAV accounting identity.
"""
import json

nb_path = "kaggle_notebook/alphaniti_v25_pipeline.ipynb"
with open(nb_path, "r", encoding="utf-8") as f:
    nb = json.load(f)

# Cell 13: ConservedLedger
new_cell_13_code = '''class ConservedLedger:
    """
    Conserved Five-Bucket Double-Entry State Machine enforcing the NAV Accounting Identity.
    Assets = W_settled + W_escrow + W_lien + W_recv + EquityValuation
    Liabilities = W_payable
    NAV = Assets - Liabilities
    """
    def __init__(self, initial_cash=1000000.0):
        self.w_settled = float(initial_cash)
        self.w_escrow = 0.0
        self.w_lien = 0.0     # Segregated margin collateral asset (beneficial property of the fund)
        self.w_recv = 0.0     # Unsettled T+1 sales receivables
        self.w_payable = 0.0  # External obligations / fees owed
        self.positions = {}
        self.history = []
        
    def nav(self, current_prices):
        equity_val = sum(qty * current_prices.get(sym, 0.0) for sym, qty in self.positions.items())
        # NAV Accounting Identity: Assets - Liabilities
        # Margin liens (w_lien) are segregated collateral assets, NOT liabilities
        assets = self.w_settled + self.w_escrow + self.w_lien + self.w_recv + equity_val
        liabilities = self.w_payable
        return assets - liabilities
        
    def buying_power(self):
        # Cleared available buying power guarantees solvency before order dispatch
        return max(0.0, self.w_settled - self.w_payable)
        
    def post_margin_lien(self, amount):
        assert self.buying_power() >= amount, "Insufficient buying power to post margin lien!"
        self.w_settled -= amount
        self.w_lien += amount
        
    def release_margin_lien(self, amount):
        assert self.w_lien >= amount, "Cannot release more than active lien!"
        self.w_lien -= amount
        self.w_settled += amount
        
    def dispatch_buy(self, sym, qty, limit_px, est_cost):
        assert self.buying_power() >= est_cost, "Solvency violation!"
        self.w_settled -= est_cost
        self.w_escrow += est_cost
        return {"sym": sym, "qty": qty, "limit_px": limit_px, "escrow": est_cost}
        
    def fill_buy(self, order, fill_px, current_prices):
        actual_cost = order["qty"] * fill_px
        charges = compute_statutory_costs(order["qty"], fill_px, side="BUY")["total_frictional_deduction"]
        total_outlay = actual_cost + charges
        refund = order["escrow"] - total_outlay
        assert refund >= 0, "Limit collar breach! Outlay exceeded escrow!"
        
        self.w_escrow -= order["escrow"]
        self.w_settled += refund
        self.positions[order["sym"]] = self.positions.get(order["sym"], 0) + order["qty"]
        
ledger = ConservedLedger(initial_cash=500000.0)
prices = {"TCS": 3500.0}
print(f"Initial NAV (Accounting Identity): ₹ {ledger.nav(prices):,.2f}")
print(f"Initial Buying Power:               ₹ {ledger.buying_power():,.2f}")

# Post margin lien of Rs 50,000 (segregated collateral asset)
ledger.post_margin_lien(50000.0)
print(f"Post-Lien NAV (Preserved Delta=0): ₹ {ledger.nav(prices):,.2f}")
print(f"Remaining Buying Power:            ₹ {ledger.buying_power():,.2f}")

# Order 100 shares of TCS with limit collar
order = ledger.dispatch_buy("TCS", 100, limit_px=3510.0, est_cost=352000.0)
print(f"Post-Dispatch NAV (Preserved):     ₹ {ledger.nav(prices):,.2f}")
print(f"Cleared Cash Remaining:            ₹ {ledger.w_settled:,.2f}")

# Fill at ₹3500
ledger.fill_buy(order, fill_px=3500.0, current_prices=prices)
print(f"Post-Fill NAV (Net of Charges):    ₹ {ledger.nav(prices):,.2f}")
print(f"Custody Positions:                 {ledger.positions}")
'''

nb['cells'][13]['source'] = [line + '\n' for line in new_cell_13_code.splitlines()]

with open(nb_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1)

print("Kaggle notebook Cell 13 updated successfully!")
