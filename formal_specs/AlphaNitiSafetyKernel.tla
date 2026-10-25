----------------------- MODULE AlphaNitiSafetyKernel -----------------------
EXTENDS Naturals, Integers, Sequences, FiniteSets

CONSTANTS
    Symbols,             \* Set of tradable symbols
    MaxBudget,           \* Total initial cleared cash capital
    PMin                 \* Minimum positive tick price floor

VARIABLES
    w_settled,           \* Cleared cash freely available for trading
    w_escrow,            \* Quarantined cash backing open buy orders
    w_recv,              \* Unsettled receivables from sales (T+1)
    w_lien,              \* Operational / margin liens
    w_payable,           \* Unsettled trade / statutory payables
    positions,           \* Function from Symbols to Non-negative Integers
    prices,              \* Function from Symbols to Positive Integers (ticks)
    active_orders,       \* Set of active order records
    kernel_state,        \* State of the formal safety kernel
    merkle_root          \* Current cryptographic root of the causal DAG

vars == <<w_settled, w_escrow, w_recv, w_lien, w_payable, positions, prices, active_orders, kernel_state, merkle_root>>

OrderRecord == [
    id: Nat,
    sym: Symbols,
    side: {"BUY", "SELL"},
    qty: Nat \ {0},
    limit_price: Nat \ {0},
    escrow: Nat,
    status: {"SUBMITTED", "CONFIRMED", "FILLED", "CANCELLED"}
]

TypeOK ==
    /\ w_settled >= 0
    /\ w_escrow >= 0
    /\ w_recv >= 0
    /\ w_lien >= 0
    /\ w_payable >= 0
    /\ \A s \in Symbols : positions[s] >= 0
    /\ \A s \in Symbols : prices[s] >= PMin
    /\ kernel_state \in {"IDLE", "EVALUATING", "DISPATCHED", "RECON_HALT", "HALTED"}

SolvencyInvariant ==
    w_settled >= w_lien + w_payable

NonNegativeBalancesInvariant ==
    /\ w_settled >= 0
    /\ w_escrow >= 0
    /\ w_recv >= 0

Init ==
    /\ w_settled = MaxBudget
    /\ w_escrow = 0
    /\ w_recv = 0
    /\ w_lien = 0
    /\ w_payable = 0
    /\ positions = [s \in Symbols |-> 0]
    /\ prices = [s \in Symbols |-> PMin]
    /\ active_orders = {}
    /\ kernel_state = "IDLE"
    /\ merkle_root = 0

SafetyKernelAuthorize(sym, qty, limit_px, est_cost) ==
    /\ kernel_state = "IDLE"
    /\ qty > 0
    /\ limit_px >= PMin
    /\ w_settled - w_lien - w_payable >= est_cost
    /\ limit_px * qty <= est_cost
    /\ kernel_state' = "EVALUATING"
    /\ UNCHANGED <<w_settled, w_escrow, w_recv, w_lien, w_payable, positions, prices, active_orders, merkle_root>>

DispatchBuy(id, sym, qty, limit_px, est_cost) ==
    /\ kernel_state = "EVALUATING"
    /\ qty > 0
    /\ limit_px >= PMin
    /\ w_settled - w_lien - w_payable >= est_cost
    /\ limit_px * qty <= est_cost
    /\ w_settled' = w_settled - est_cost
    /\ w_escrow' = w_escrow + est_cost
    /\ active_orders' = active_orders \cup {[
           id |-> id, sym |-> sym, side |-> "BUY", qty |-> qty,
           limit_price |-> limit_px, escrow |-> est_cost, status |-> "SUBMITTED"
       ]}
    /\ kernel_state' = "DISPATCHED"
    /\ UNCHANGED <<w_recv, w_lien, w_payable, positions, prices, merkle_root>>

FillBuy(id, fill_px, fill_fee) ==
    \E o \in active_orders :
        /\ o.id = id
        /\ o.side = "BUY"
        /\ o.status \in {"SUBMITTED", "CONFIRMED"}
        /\ fill_px <= o.limit_price
        /\ LET actual_cost == (o.qty * fill_px) + fill_fee
               refund == o.escrow - actual_cost
           IN
           /\ refund >= 0
           /\ w_escrow' = w_escrow - o.escrow
           /\ w_settled' = w_settled + refund
           /\ positions' = [positions EXCEPT ![o.sym] = positions[o.sym] + o.qty]
           /\ active_orders' = (active_orders \ {o}) \cup {[o EXCEPT !.status = "FILLED"]}
           /\ kernel_state' = "IDLE"
           /\ UNCHANGED <<w_recv, w_lien, w_payable, prices, merkle_root>>

DispatchSell(id, sym, qty) ==
    /\ kernel_state = "IDLE"
    /\ qty > 0
    /\ positions[sym] >= qty
    /\ positions' = [positions EXCEPT ![sym] = positions[sym] - qty]
    /\ active_orders' = active_orders \cup {[
           id |-> id, sym |-> sym, side |-> "SELL", qty |-> qty,
           limit_price |-> PMin, escrow |-> 0, status |-> "SUBMITTED"
       ]}
    /\ kernel_state' = "DISPATCHED"
    /\ UNCHANGED <<w_settled, w_escrow, w_recv, w_lien, w_payable, prices, merkle_root>>

FillSell(id, fill_px, fill_fee) ==
    \E o \in active_orders :
        /\ o.id = id
        /\ o.side = "SELL"
        /\ o.status \in {"SUBMITTED", "CONFIRMED"}
        /\ LET net_proceeds == (o.qty * fill_px) - fill_fee
           IN
           /\ net_proceeds >= 0
           /\ w_recv' = w_recv + net_proceeds
           /\ active_orders' = (active_orders \ {o}) \cup {[o EXCEPT !.status = "FILLED"]}
           /\ kernel_state' = "IDLE"
           /\ UNCHANGED <<w_settled, w_escrow, w_lien, w_payable, positions, prices, merkle_root>>

SettleReceivables(amount) ==
    /\ amount > 0
    /\ w_recv >= amount
    /\ w_recv' = w_recv - amount
    /\ w_settled' = w_settled + amount
    /\ UNCHANGED <<w_escrow, w_lien, w_payable, positions, prices, active_orders, kernel_state, merkle_root>>

BrokerTimeout(id) ==
    \E o \in active_orders :
        /\ o.id = id
        /\ o.status = "SUBMITTED"
        /\ kernel_state' = "RECON_HALT"
        /\ UNCHANGED <<w_settled, w_escrow, w_recv, w_lien, w_payable, positions, prices, active_orders, merkle_root>>

Next ==
    \/ \E sym \in Symbols, q \in {1, 2}, px \in {PMin, PMin + 1}, c \in {1..MaxBudget} : SafetyKernelAuthorize(sym, q, px, c)
    \/ \E id \in {1, 2}, sym \in Symbols, q \in {1, 2}, px \in {PMin, PMin + 1}, c \in {1..MaxBudget} : DispatchBuy(id, sym, q, px, c)
    \/ \E id \in {1, 2}, px \in {PMin}, fee \in {0, 1} : FillBuy(id, px, fee)
    \/ \E id \in {1, 2}, sym \in Symbols, q \in {1} : DispatchSell(id, sym, q)
    \/ \E id \in {1, 2}, px \in {PMin}, fee \in {0} : FillSell(id, px, fee)
    \/ \E a \in {1..MaxBudget} : SettleReceivables(a)
    \/ \E id \in {1, 2} : BrokerTimeout(id)

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

THEOREM Spec => [](TypeOK /\ SolvencyInvariant /\ NonNegativeBalancesInvariant)
=============================================================================
