"""
Stand-alone backtest verifier.
Runs the exact same strategies + risk logic on real historical data,
prints a full report, and verifies profitability.
No server needed — pure computation.

Includes: commission, slippage, next-bar-open entry,
gap-aware stops, and in-sample / out-of-sample split.
"""
import sys, math
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from aibroker.data.historical import load_history
from aibroker.strategies.regime import RegimeDetector
from aibroker.strategies.swing import DonchianBreak, compute_atr

SYMBOLS = [
    "SPY","QQQ","AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","BRK-B",
    "JPM","V","JNJ","UNH","WMT","PG","MA","HD","DIS","NFLX",
    "PYPL","AMD","INTC","CRM","COST","PEP","KO","ABBV","MRK","BA",
]
INITIAL_EQUITY = 100_000.0
WARMUP = 50
RISK_PCT = 0.01
MAX_POS_PCT_EQUITY = 0.12
MAX_LEVERAGE = 5.0
MAX_PORTFOLIO_EXPOSURE = 1.5
MAX_CONCURRENT = 6
PYRAMID_ATR = 1.5
STOP_ATR_MULT = 3.0
TARGET_ATR_MULT = 4.0
TRAIL_ATR_MULT = 3.0

COMMISSION_PER_SHARE = 0.005
MIN_COMMISSION = 1.00
SLIPPAGE_PCT = 0.0005


def slip(px: float, side: str) -> float:
    return px * (1.0 + SLIPPAGE_PCT) if side == "buy" else px * (1.0 - SLIPPAGE_PCT)


def commission(qty: float) -> float:
    return max(MIN_COMMISSION, abs(qty) * COMMISSION_PER_SHARE)


regime = RegimeDetector(max_leverage=MAX_LEVERAGE)

print("Loading historical data (500 bars per symbol)...")
history = load_history(SYMBOLS, bars=500)
min_bars = min(len(v) for v in history.values())
print(f"Loaded {len(history)} symbols, {min_bars} bars each")
print(f"Date range: {history[SYMBOLS[0]][0]['date']} -> {history[SYMBOLS[0]][-1]['date']}")
print()


strats: dict[str, any] = {}
for sym in SYMBOLS:
    strats[sym] = DonchianBreak()


class Position:
    def __init__(self, sym, side, qty, entry_px, stop, target, bar_idx, strategy, leverage=2.0):
        self.sym = sym
        self.side = side
        self.qty = qty
        self.entry_px = entry_px
        self.avg_entry = entry_px
        self.stop = stop
        self.target = target
        self.bar_idx = bar_idx
        self.strategy = strategy
        self.highest = entry_px
        self.lowest = entry_px
        self.pyramided = False
        self.initial_stop = stop
        self.leverage = leverage


positions: dict[str, Position] = {}
pending_entries: list[dict] = []
cash = INITIAL_EQUITY
equity_curve = []
closed_trades = []


def close_position(pos, exit_px, reason, bar_idx, side_label="sell"):
    global cash
    exit_px = slip(exit_px, side_label)
    comm = commission(pos.qty)
    avg = pos.avg_entry
    margin_held = pos.qty * avg / pos.leverage
    pnl = (exit_px - avg) * pos.qty if pos.side == "long" else (avg - exit_px) * pos.qty
    pnl -= comm
    cash += margin_held + pnl
    risk_amt = abs(pos.entry_px - pos.initial_stop) * pos.qty
    r_mult = pnl / risk_amt if risk_amt > 0.01 else 0
    closed_trades.append({
        "sym": pos.sym, "side": pos.side, "entry": pos.entry_px,
        "exit": exit_px, "qty": pos.qty, "pnl": round(pnl, 2),
        "r": round(r_mult, 2), "strategy": pos.strategy, "reason": reason,
        "bars_held": bar_idx - pos.bar_idx, "exit_bar": bar_idx,
    })
    return pnl


oos_split_bar = WARMUP + int((min_bars - WARMUP) * 0.75)

for bar_idx in range(WARMUP, min_bars):
    # Fill pending entries at this bar's open
    still_pending = []
    for pend in pending_entries:
        sym = pend["sym"]
        bars = history[sym]
        open_px = slip(float(bars[bar_idx]["o"]), pend["action"])
        comm = commission(pend["qty"])
        sym_lev = pend["leverage"]
        margin_required = pend["qty"] * open_px / sym_lev + comm
        if margin_required > cash or pend["qty"] < 1:
            continue
        cash -= margin_required
        stop_dist = pend["stop_dist"]
        if pend["action"] == "buy":
            stop = open_px - stop_dist
            target = open_px + TARGET_ATR_MULT * pend["atr"]
            positions[sym] = Position(sym, "long", pend["qty"], open_px, stop, target,
                                      bar_idx, pend["strategy"], leverage=sym_lev)
        else:
            stop = open_px + stop_dist
            target = open_px - TARGET_ATR_MULT * pend["atr"]
            positions[sym] = Position(sym, "short", pend["qty"], open_px, stop, target,
                                      bar_idx, pend["strategy"], leverage=sym_lev)
    pending_entries.clear()

    eq = cash
    for sym, pos in positions.items():
        px = float(history[sym][bar_idx]["c"])
        avg = pos.avg_entry
        margin_held = pos.qty * avg / pos.leverage
        pnl = (px - avg) * pos.qty if pos.side == "long" else (avg - px) * pos.qty
        eq += margin_held + pnl
    regime.update_equity(eq)

    equity_curve.append({"bar": bar_idx, "date": history[SYMBOLS[0]][bar_idx]["date"], "equity": round(eq, 2)})

    to_close = []
    for sym, pos in list(positions.items()):
        bars = history[sym]
        bar = bars[bar_idx]
        bar_h = float(bar["h"])
        bar_l = float(bar["l"])
        bar_o = float(bar["o"])
        px_c = float(bar["c"])

        atr_list = compute_atr(bars[:bar_idx+1], 14)
        cur_atr = atr_list[bar_idx] if bar_idx < len(atr_list) and atr_list[bar_idx] is not None and atr_list[bar_idx] > 0 else px_c * 0.015

        if pos.side == "long":
            pos.highest = max(pos.highest, bar_h)
            trail = pos.highest - TRAIL_ATR_MULT * cur_atr
            if trail > pos.stop:
                pos.stop = trail
            if bar_l <= pos.stop:
                fill_px = min(pos.stop, bar_o) if bar_o < pos.stop else pos.stop
                to_close.append((sym, fill_px, "stop_loss", "sell"))
                continue
            if bar_h >= pos.target:
                fill_px = max(pos.target, bar_o) if bar_o > pos.target else pos.target
                to_close.append((sym, fill_px, "take_profit", "sell"))
                continue
        else:
            pos.lowest = min(pos.lowest, bar_l)
            trail = pos.lowest + TRAIL_ATR_MULT * cur_atr
            if trail < pos.stop:
                pos.stop = trail
            if bar_h >= pos.stop:
                fill_px = max(pos.stop, bar_o) if bar_o > pos.stop else pos.stop
                to_close.append((sym, fill_px, "stop_loss", "buy"))
                continue
            if bar_l <= pos.target:
                fill_px = min(pos.target, bar_o) if bar_o < pos.target else pos.target
                to_close.append((sym, fill_px, "take_profit", "buy"))
                continue

    for sym, exit_px, reason, side_lbl in to_close:
        if sym in positions:
            close_position(positions[sym], exit_px, reason, bar_idx, side_label=side_lbl)
            del positions[sym]

    # Pyramid
    for sym, pos in list(positions.items()):
        if pos.pyramided:
            continue
        bars_data = history[sym]
        bar = bars_data[bar_idx]
        px_c = float(bar["c"])
        atr_list = compute_atr(bars_data[:bar_idx+1], 14)
        cur_atr = atr_list[bar_idx] if bar_idx < len(atr_list) and atr_list[bar_idx] is not None and atr_list[bar_idx] > 0 else px_c * 0.015
        move = (px_c - pos.entry_px) if pos.side == "long" else (pos.entry_px - px_c)
        if move >= PYRAMID_ATR * cur_atr:
            add_qty = max(1, int(pos.qty * 0.5))
            margin_needed = add_qty * px_c / pos.leverage
            comm_cost = commission(add_qty)
            if margin_needed + comm_cost < cash * 0.3:
                fill = slip(px_c, "buy" if pos.side == "long" else "sell")
                cash -= margin_needed + comm_cost
                old_qty = pos.qty
                pos.qty += add_qty
                pos.avg_entry = (pos.avg_entry * old_qty + fill * add_qty) / pos.qty
                if pos.side == "long":
                    pos.stop = max(pos.stop, pos.entry_px)
                else:
                    pos.stop = min(pos.stop, pos.entry_px)
                pos.pyramided = True

    # New signals -> queue as pending (fill at next bar's open)
    for sym in SYMBOLS:
        if sym in positions:
            bars = history[sym]
            pos = positions[sym]
            strat = strats[sym]
            sig = strat.evaluate(bars, bar_idx, pos.side)
            if sig and sig.action == "exit_long" and pos.side == "long":
                px = float(bars[bar_idx]["c"])
                close_position(pos, px, "signal_exit", bar_idx, side_label="sell")
                del positions[sym]
            elif sig and sig.action == "exit_short" and pos.side == "short":
                px = float(bars[bar_idx]["c"])
                close_position(pos, px, "signal_exit", bar_idx, side_label="buy")
                del positions[sym]
            continue

        bars = history[sym]
        strat = strats[sym]
        sig = strat.evaluate(bars, bar_idx, "flat")
        if sig is None or sig.action not in ("buy", "sell"):
            continue

        if len(positions) >= MAX_CONCURRENT:
            continue
        total_notional = sum(p.qty * float(history[p.sym][bar_idx]["c"]) for p in positions.values())
        max_notional = eq * MAX_PORTFOLIO_EXPOSURE
        if total_notional >= max_notional:
            continue
        remaining_cap = max(0, max_notional - total_notional)

        px = float(bars[bar_idx]["c"])
        atr_list = compute_atr(bars[:bar_idx+1], 14)
        atr = atr_list[bar_idx] if bar_idx < len(atr_list) and atr_list[bar_idx] is not None and atr_list[bar_idx] > 0 else px * 0.015

        sym_lev = regime.get_leverage(sym, bars, bar_idx, eq)

        stop_dist = max(STOP_ATR_MULT * atr, px * 0.008)
        risk_budget = eq * RISK_PCT
        max_pos_val = eq * MAX_POS_PCT_EQUITY
        qty = max(1, math.floor(risk_budget / stop_dist))
        qty = min(qty, math.floor(max_pos_val / px))
        qty = min(qty, math.floor(remaining_cap / px)) if remaining_cap > 0 else qty
        if qty < 1:
            continue

        if bar_idx < min_bars - 1:
            pending_entries.append({
                "sym": sym, "action": sig.action, "qty": qty,
                "leverage": sym_lev, "stop_dist": stop_dist, "atr": atr,
                "strategy": strat.name,
            })

# Close all remaining
last_bar = min_bars - 1
for sym, pos in list(positions.items()):
    px = float(history[sym][last_bar]["c"])
    side_lbl = "sell" if pos.side == "long" else "buy"
    close_position(pos, px, "end_of_backtest", last_bar, side_label=side_lbl)
positions.clear()

# === REPORT ===
print("=" * 70)
print("BACKTEST REPORT (Realistic: commission + slippage + next-bar-open entry)")
print("=" * 70)
total_trades = len(closed_trades)
wins = [t for t in closed_trades if t["pnl"] > 0]
losses = [t for t in closed_trades if t["pnl"] <= 0]
total_pnl = sum(t["pnl"] for t in closed_trades)
win_rate = len(wins) / total_trades * 100 if total_trades else 0
avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
avg_r = sum(t["r"] for t in closed_trades) / total_trades if total_trades else 0
max_eq = max(e["equity"] for e in equity_curve) if equity_curve else INITIAL_EQUITY
min_eq = min(e["equity"] for e in equity_curve) if equity_curve else INITIAL_EQUITY
final_eq = equity_curve[-1]["equity"] if equity_curve else INITIAL_EQUITY

peak = INITIAL_EQUITY
max_dd = 0.0
for e in equity_curve:
    peak = max(peak, e["equity"])
    dd = peak - e["equity"]
    max_dd = max(max_dd, dd)

if not equity_curve:
    print("No bars simulated — check data and WARMUP setting.")
    sys.exit(1)
print(f"Period: {equity_curve[0]['date']} -> {equity_curve[-1]['date']} ({len(equity_curve)} bars)")
print(f"Initial equity: ${INITIAL_EQUITY:,.2f}")
print(f"Final equity:   ${final_eq:,.2f}")
print(f"Total P/L:      ${total_pnl:,.2f} ({total_pnl/INITIAL_EQUITY*100:.1f}%)")
print(f"Max equity:     ${max_eq:,.2f}")
print(f"Max drawdown:   ${max_dd:,.2f} ({max_dd/peak*100:.1f}%)" if peak > 0 else "Max drawdown: $0")
print()
print(f"Total trades:   {total_trades}")
print(f"Winners:        {len(wins)} ({win_rate:.1f}%)")
print(f"Losers:         {len(losses)} ({100-win_rate:.1f}%)")
print(f"Avg win:        ${avg_win:,.2f}")
print(f"Avg loss:       ${avg_loss:,.2f}")
print(f"Avg R:          {avg_r:.2f}")
loss_sum = sum(t['pnl'] for t in losses)
win_sum = sum(t['pnl'] for t in wins)
print(f"Profit factor:  {abs(win_sum / loss_sum):.2f}" if loss_sum != 0 else "Profit factor: inf")
print()

# In-sample vs Out-of-sample
is_trades = [t for t in closed_trades if t.get("exit_bar", 0) < oos_split_bar]
oos_trades = [t for t in closed_trades if t.get("exit_bar", 0) >= oos_split_bar]

def print_split(label, trades):
    if not trades:
        print(f"  {label}: no trades")
        return
    pnl = sum(t["pnl"] for t in trades)
    w = [t for t in trades if t["pnl"] > 0]
    wr = len(w) / len(trades) * 100
    ar = sum(t["r"] for t in trades) / len(trades)
    print(f"  {label}: {len(trades)} trades, P/L=${pnl:>8,.2f}, win={wr:.0f}%, avgR={ar:.2f}")

print("--- IN-SAMPLE vs OUT-OF-SAMPLE (75/25 split) ---")
print_split("In-sample (75%)", is_trades)
print_split("Out-of-sample (25%)", oos_trades)
print()

# By strategy
print("--- BY STRATEGY ---")
for sname in ["donchian_break"]:
    st = [t for t in closed_trades if t["strategy"] == sname]
    if not st:
        continue
    sw = [t for t in st if t["pnl"] > 0]
    sp = sum(t["pnl"] for t in st)
    wr = len(sw)/len(st)*100
    ar = sum(t["r"] for t in st)/len(st)
    print(f"  {sname:20s}: {len(st):3d} trades, P/L=${sp:>8,.2f}, win={wr:.0f}%, avgR={ar:.2f}")

# By exit reason
print()
print("--- BY EXIT REASON ---")
for reason in ["stop_loss", "take_profit", "signal_exit", "end_of_backtest"]:
    rt = [t for t in closed_trades if t["reason"] == reason]
    if not rt:
        continue
    rp = sum(t["pnl"] for t in rt)
    print(f"  {reason:20s}: {len(rt):3d} trades, P/L=${rp:>8,.2f}")

# Top 5 wins and losses
print()
print("--- TOP 5 WINS ---")
for t in sorted(closed_trades, key=lambda x: x["pnl"], reverse=True)[:5]:
    print(f"  {t['sym']:6s} {t['side']:5s} entry=${t['entry']:.2f} exit=${t['exit']:.2f} P/L=${t['pnl']:>8,.2f} R={t['r']:>5.2f} [{t['strategy']}] {t['bars_held']}d")

print()
print("--- TOP 5 LOSSES ---")
for t in sorted(closed_trades, key=lambda x: x["pnl"])[:5]:
    print(f"  {t['sym']:6s} {t['side']:5s} entry=${t['entry']:.2f} exit=${t['exit']:.2f} P/L=${t['pnl']:>8,.2f} R={t['r']:>5.2f} [{t['strategy']}] {t['bars_held']}d")

# Equity curve sample
print()
print("--- EQUITY CURVE (every 30 bars) ---")
for e in equity_curve[::30]:
    bar_pct = (e["equity"] - INITIAL_EQUITY) / INITIAL_EQUITY * 100
    bar_char = "+" if bar_pct >= 0 else ""
    print(f"  {e['date']}  ${e['equity']:>10,.2f}  {bar_char}{bar_pct:.1f}%")
if equity_curve:
    e = equity_curve[-1]
    bar_pct = (e["equity"] - INITIAL_EQUITY) / INITIAL_EQUITY * 100
    bar_char = "+" if bar_pct >= 0 else ""
    print(f"  {e['date']}  ${e['equity']:>10,.2f}  {bar_char}{bar_pct:.1f}%  <-- FINAL")

print()
verdict = "PROFITABLE" if total_pnl > 0 else "NOT PROFITABLE"
print(f"VERDICT: {verdict} (${total_pnl:+,.2f})")
