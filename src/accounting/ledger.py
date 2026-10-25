"""
AlphaNiti v25.0 Institutional Execution & Accounting Engine
Pipeline Module 6 - Conserved Five-Bucket Double-Entry Ledger State Machine
Reference: docs/06_ACCOUNTING_NAV_AND_DOUBLE_ENTRY_LEDGER.md (Callout 3)
"""

from typing import Dict, Any, Optional, Tuple, List
import hashlib
import time


class DoubleEntryLedgerViolation(Exception):
    """Raised when an accounting invariant or solvency constraint is violated."""
    pass


class FiveBucketBalanceSheet:
    """
    Conserved Five-Bucket Double-Entry State Machine enforcing inductive NAV conservation,
    disjoint balance sheet segregation, and pre-trade physical solvency.

    Accounts:
        Assets:
            - W_settled: Cleared, unencumbered INR cash
            - W_escrow: Quarantined cash backing open buy orders (Restricted Funds, part of NAV)
            - W_recv: Unsettled T+1 sales receivables
            - Securities: Custody shares q_i valued at market price P_i
        Liabilities:
            - W_lien: Margin and regulatory clearing liens
            - W_payable: Trade and statutory payables owed to third parties
    """

    def __init__(self, initial_capital_inr: float = 1_000_000.0):
        if initial_capital_inr <= 0:
            raise ValueError("Initial capital must be strictly positive.")

        self.W_settled: float = float(initial_capital_inr)
        self.W_escrow: float = 0.0
        self.W_recv: float = 0.0
        self.W_lien: float = 0.0
        self.W_payable: float = 0.0

        self.positions: Dict[str, int] = {}
        self.cost_basis: Dict[str, float] = {}

        # Audit ledger
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.transaction_history: List[Dict[str, Any]] = []

    def get_equity_valuation(self, prices: Dict[str, float]) -> float:
        """Market value of depository custody positions: sum_i q_i * P_i."""
        total = 0.0
        for symbol, qty in self.positions.items():
            if qty > 0:
                p = prices.get(symbol, self.cost_basis.get(symbol, 0.0))
                total += qty * p
        return total

    def get_tav(self, prices: Dict[str, float]) -> float:
        """Total Asset Value (TAV) = W_settled + W_escrow + W_recv + EquityValuation."""
        return self.W_settled + self.W_escrow + self.W_recv + self.get_equity_valuation(prices)

    def get_liabilities(self) -> float:
        """Total Liabilities = W_lien + W_payable."""
        return self.W_lien + self.W_payable

    def get_nav(self, prices: Dict[str, float]) -> float:
        """
        True Net Asset Value (Callout 3 Resolution):
            NAV = TAV - Liabilities
        Note: Restricted funds (W_escrow) remain fund assets and are NOT deducted!
        """
        return self.get_tav(prices) - self.get_liabilities()

    def get_buying_power(self) -> float:
        """
        Decoupled Available Buying Power (Callout 3):
            BuyingPower = W_settled - W_lien - W_payable
        Guarantees that no order can be authorized without immediately available cleared cash.
        """
        return max(0.0, self.W_settled - self.W_lien - self.W_payable)

    def dispatch_buy_order(
        self,
        order_id: str,
        symbol: str,
        quantity: int,
        limit_price: float,
        estimated_charges: float,
    ) -> Dict[str, Any]:
        """
        Case 1: Buy Order Authorization & Dispatch.
        Quarantines escrow cash: W_settled -= C, W_escrow += C.
        Pre-condition: C <= BuyingPower.
        NAV invariant: Delta NAV = 0.
        """
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")
        if limit_price <= 0:
            raise ValueError(f"Limit price must be positive, got {limit_price}")

        required_escrow = quantity * limit_price + estimated_charges
        buying_power = self.get_buying_power()

        if required_escrow > buying_power + 1e-4:
            raise DoubleEntryLedgerViolation(
                f"Solvency violation: required escrow INR {required_escrow:,.2f} "
                f"exceeds available buying power INR {buying_power:,.2f}"
            )

        self.W_settled -= required_escrow
        self.W_escrow += required_escrow

        self.open_orders[order_id] = {
            "order_id": order_id,
            "type": "BUY",
            "symbol": symbol,
            "quantity": quantity,
            "limit_price": limit_price,
            "escrow_allocated": required_escrow,
        }

        entry = {
            "action": "DISPATCH_BUY",
            "order_id": order_id,
            "symbol": symbol,
            "quantity": quantity,
            "escrow": required_escrow,
            "timestamp": time.time(),
        }
        self.transaction_history.append(entry)
        return entry

    def fill_buy_order(
        self,
        order_id: str,
        fill_price: float,
        actual_charges: float,
    ) -> Dict[str, Any]:
        """
        Case 2: Buy Order Fill.
        Releases escrow, pays for shares + charges, refunds excess limit slippage to settled cash.
        NAV invariant: Delta NAV = -actual_charges.
        """
        if order_id not in self.open_orders:
            raise KeyError(f"Order ID {order_id} not found in open orders ledger.")

        order = self.open_orders.pop(order_id)
        symbol = order["symbol"]
        qty = order["quantity"]
        escrow = order["escrow_allocated"]

        actual_outlay = qty * fill_price + actual_charges
        if actual_outlay > escrow + 1e-4:
            raise DoubleEntryLedgerViolation(
                f"Limit Collar violation: actual outlay INR {actual_outlay:,.2f} "
                f"exceeds quarantined escrow INR {escrow:,.2f}"
            )

        refund = escrow - actual_outlay
        self.W_escrow -= escrow
        self.W_settled += refund

        # Update custody shares and weighted average cost basis
        curr_qty = self.positions.get(symbol, 0)
        curr_basis = self.cost_basis.get(symbol, 0.0)
        new_qty = curr_qty + qty
        new_basis = ((curr_qty * curr_basis) + (qty * fill_price)) / new_qty

        self.positions[symbol] = new_qty
        self.cost_basis[symbol] = new_basis

        entry = {
            "action": "FILL_BUY",
            "order_id": order_id,
            "symbol": symbol,
            "quantity": qty,
            "fill_price": fill_price,
            "charges": actual_charges,
            "refund": refund,
            "timestamp": time.time(),
        }
        self.transaction_history.append(entry)
        return entry

    def fill_sell_order(
        self,
        symbol: str,
        quantity: int,
        fill_price: float,
        actual_charges: float,
    ) -> Dict[str, Any]:
        """
        Case 3: Sell Order Fill.
        Decreases custody shares, transfers net proceeds to unsettled T+1 receivables.
        NAV invariant: Delta NAV = RealizedPnL - Charges.
        """
        curr_qty = self.positions.get(symbol, 0)
        if quantity > curr_qty:
            raise DoubleEntryLedgerViolation(
                f"Short sell violation: attempting to sell {quantity} shares of {symbol}, "
                f"custody holds only {curr_qty}"
            )

        basis = self.cost_basis.get(symbol, fill_price)
        realized_pnl = quantity * (fill_price - basis)
        net_proceeds = quantity * fill_price - actual_charges

        self.positions[symbol] = curr_qty - quantity
        if self.positions[symbol] == 0:
            self.cost_basis[symbol] = 0.0

        self.W_recv += net_proceeds

        entry = {
            "action": "FILL_SELL",
            "symbol": symbol,
            "quantity": quantity,
            "fill_price": fill_price,
            "charges": actual_charges,
            "net_proceeds": net_proceeds,
            "realized_pnl": realized_pnl,
            "timestamp": time.time(),
        }
        self.transaction_history.append(entry)
        return entry

    def settle_t1_batch(self, amount: Optional[float] = None) -> float:
        """
        Case 4: NSCCL T+1 Settlement Batch.
        Clears receivables into settled cash: W_recv -= A, W_settled += A.
        NAV invariant: Delta NAV = 0.
        """
        settle_amt = self.W_recv if amount is None else min(amount, self.W_recv)
        if settle_amt < 0:
            raise ValueError("Settlement amount cannot be negative.")

        self.W_recv -= settle_amt
        self.W_settled += settle_amt

        entry = {
            "action": "SETTLE_T1",
            "amount": settle_amt,
            "timestamp": time.time(),
        }
        self.transaction_history.append(entry)
        return settle_amt

    def record_cash_dividend(self, symbol: str, dividend_per_share: float) -> float:
        """
        Case 5: Corporate Action Cash Dividend.
        Dividend cash lands in receivables/settled cash, exactly offsetting ex-date price drop.
        NAV invariant: Delta NAV = 0.
        """
        qty = self.positions.get(symbol, 0)
        total_div = qty * dividend_per_share
        self.W_recv += total_div

        entry = {
            "action": "CASH_DIVIDEND",
            "symbol": symbol,
            "shares": qty,
            "dividend_per_share": dividend_per_share,
            "total_dividend": total_div,
            "timestamp": time.time(),
        }
        self.transaction_history.append(entry)
        return total_div
