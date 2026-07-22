"""
dashboard.py — สรุป trades_log.csv
รัน: python dashboard.py
     python dashboard.py --symbol BTCUSDm
     python dashboard.py --from 2026-01-01 --to 2026-12-31
"""
import sys
import argparse
from datetime import datetime

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from journal import LOG_FILE, COLUMNS


# ---------------------------------------------------------------------------
# Load & Filter
# ---------------------------------------------------------------------------

def load(symbol: str = None, date_from: str = None, date_to: str = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        df = pd.read_csv(LOG_FILE, dtype={"ticket": str})
    except FileNotFoundError:
        df = pd.DataFrame(columns=COLUMNS)

    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce")
    df["entry"]   = pd.to_numeric(df["entry"],   errors="coerce")
    df["score"]   = pd.to_numeric(df["score"],   errors="coerce")

    if symbol:
        df = df[df["symbol"].str.upper() == symbol.upper()]
    if date_from:
        df = df[df["date"] >= date_from]
    if date_to:
        df = df[df["date"] <= date_to]

    open_pos = df[df["status"] == "Open"].copy()
    closed   = df[df["status"] != "Open"].dropna(subset=["net_pnl"]).copy()
    return open_pos, closed


# ---------------------------------------------------------------------------
# Max Drawdown
# ---------------------------------------------------------------------------

def calc_max_drawdown(pnl_series: pd.Series) -> float:
    """Max Drawdown = max peak-to-trough ของ cumulative P&L"""
    cumulative = pnl_series.cumsum()
    peak       = cumulative.cummax()
    drawdown   = cumulative - peak
    return drawdown.min()


# ---------------------------------------------------------------------------
# Streak
# ---------------------------------------------------------------------------

def calc_streak(pnl_series: pd.Series) -> tuple[int, int]:
    """คืน (max_win_streak, max_loss_streak)"""
    max_win = max_loss = cur_win = cur_loss = 0
    for v in pnl_series:
        if v > 0:
            cur_win  += 1
            cur_loss  = 0
            max_win   = max(max_win, cur_win)
        else:
            cur_loss += 1
            cur_win   = 0
            max_loss  = max(max_loss, cur_loss)
    return max_win, max_loss


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

SEP = "=" * 54
SEP2 = "-" * 54


def _sign(v: float) -> str:
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


def print_summary(closed: pd.DataFrame) -> None:
    total = len(closed)
    if total == 0:
        print("  ยังไม่มี trade ที่ปิดแล้ว")
        return

    wins   = closed[closed["net_pnl"] > 0]
    losses = closed[closed["net_pnl"] <= 0]

    win_rate   = len(wins) / total * 100
    loss_rate  = 100 - win_rate
    total_pnl  = closed["net_pnl"].sum()
    avg_win    = wins["net_pnl"].mean()   if len(wins)   else 0.0
    avg_loss   = losses["net_pnl"].mean() if len(losses) else 0.0
    expectancy = (win_rate / 100 * avg_win) + (loss_rate / 100 * avg_loss)
    profit_factor = (wins["net_pnl"].sum() / abs(losses["net_pnl"].sum())
                     if losses["net_pnl"].sum() != 0 else float("inf"))
    max_dd     = calc_max_drawdown(closed["net_pnl"])
    max_win_s, max_loss_s = calc_streak(closed["net_pnl"])
    best_trade  = closed["net_pnl"].max()
    worst_trade = closed["net_pnl"].min()
    avg_score   = closed["score"].mean() if "score" in closed.columns else float("nan")

    print(SEP)
    print("  TRADE SUMMARY")
    print(SEP)
    print(f"  Total Trades    : {total}")
    print(f"  Win / Loss      : {len(wins)} / {len(losses)}")
    print(f"  Win Rate        : {win_rate:.1f}%")
    print(SEP2)
    print(f"  Total P&L       : {_sign(total_pnl)} USD")
    print(f"  Avg Win         : {_sign(avg_win)} USD")
    print(f"  Avg Loss        : {_sign(avg_loss)} USD")
    print(f"  Best Trade      : {_sign(best_trade)} USD")
    print(f"  Worst Trade     : {_sign(worst_trade)} USD")
    print(SEP2)
    print(f"  Expectancy      : {_sign(expectancy)} USD/trade")
    print(f"  Profit Factor   : {profit_factor:.2f}")
    print(f"  Max Drawdown    : {max_dd:.2f} USD")
    print(SEP2)
    print(f"  Max Win Streak  : {max_win_s}")
    print(f"  Max Loss Streak : {max_loss_s}")
    if not pd.isna(avg_score):
        print(f"  Avg Score       : {avg_score:.1f}")
    print(SEP)


def print_pnl_table(closed: pd.DataFrame) -> None:
    if closed.empty:
        return
    print()
    print("  P&L BY TRADE")
    print(SEP)
    header = f"  {'#':<4} {'Date':<12} {'Sym':<10} {'Dir':<6} {'Entry':>10} {'P&L':>10}  Status"
    print(header)
    print(SEP2)
    for i, (_, row) in enumerate(closed.iterrows(), 1):
        pnl_str = _sign(row["net_pnl"])
        color   = "WIN " if row["net_pnl"] > 0 else "LOSS"
        print(f"  {i:<4} {row['date']:<12} {row['symbol']:<10} {row['direction']:<6} "
              f"{row['entry']:>10.2f} {pnl_str:>10}  {color} [{row['status']}]")
    print(SEP)


def print_monthly(closed: pd.DataFrame) -> None:
    if closed.empty:
        return
    closed = closed.copy()
    closed["month"] = pd.to_datetime(closed["date"]).dt.to_period("M").astype(str)
    monthly = (
        closed.groupby("month")
        .agg(trades=("net_pnl", "count"),
             wins=("net_pnl", lambda x: (x > 0).sum()),
             pnl=("net_pnl", "sum"))
        .reset_index()
    )
    monthly["win_rate"] = monthly["wins"] / monthly["trades"] * 100

    print()
    print("  MONTHLY BREAKDOWN")
    print(SEP)
    print(f"  {'Month':<12} {'Trades':>8} {'Win%':>8} {'P&L':>12}")
    print(SEP2)
    for _, r in monthly.iterrows():
        print(f"  {r['month']:<12} {r['trades']:>8} {r['win_rate']:>7.1f}% {_sign(r['pnl']):>12}")
    print(SEP)


def print_by_symbol(closed: pd.DataFrame) -> None:
    if closed.empty or closed["symbol"].nunique() <= 1:
        return
    by_sym = (
        closed.groupby("symbol")
        .agg(trades=("net_pnl", "count"),
             wins=("net_pnl", lambda x: (x > 0).sum()),
             pnl=("net_pnl", "sum"))
        .reset_index()
    )
    by_sym["win_rate"] = by_sym["wins"] / by_sym["trades"] * 100

    print()
    print("  P&L BY SYMBOL")
    print(SEP)
    print(f"  {'Symbol':<12} {'Trades':>8} {'Win%':>8} {'P&L':>12}")
    print(SEP2)
    for _, r in by_sym.iterrows():
        print(f"  {r['symbol']:<12} {r['trades']:>8} {r['win_rate']:>7.1f}% {_sign(r['pnl']):>12}")
    print(SEP)


def print_open(open_pos: pd.DataFrame) -> None:
    if open_pos.empty:
        return
    print()
    print("  OPEN POSITIONS")
    print(SEP)
    print(f"  {'Ticket':<14} {'Date':<12} {'Sym':<10} {'Dir':<6} {'Entry':>10} {'Score':>6}")
    print(SEP2)
    for _, row in open_pos.iterrows():
        score_str = f"{row['score']:.1f}" if pd.notna(row["score"]) else "-"
        print(f"  {row['ticket']:<14} {row['date']:<12} {row['symbol']:<10} "
              f"{row['direction']:<6} {row['entry']:>10.2f} {score_str:>6}")
    print(SEP)


def print_equity_curve(closed: pd.DataFrame) -> None:
    """ASCII equity curve"""
    if len(closed) < 2:
        return
    equity = closed["net_pnl"].cumsum().tolist()
    height = 8
    width  = min(len(equity), 60)

    # sample ลง width จุด
    if len(equity) > width:
        step = len(equity) / width
        equity = [equity[int(i * step)] for i in range(width)]

    mn, mx = min(equity), max(equity)
    rng = mx - mn if mx != mn else 1.0

    print()
    print("  EQUITY CURVE")
    print(SEP)
    for row in range(height, -1, -1):
        level = mn + rng * row / height
        line  = f"  {level:>8.0f} |"
        for v in equity:
            line += "█" if v >= level else " "
        print(line)
    print(f"  {'':>9}+" + "─" * len(equity))
    print(SEP)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Auto Trader Dashboard")
    parser.add_argument("--symbol",  default=None, help="กรอง symbol เช่น BTCUSDm")
    parser.add_argument("--from",    dest="date_from", default=None, help="วันเริ่ม YYYY-MM-DD")
    parser.add_argument("--to",      dest="date_to",   default=None, help="วันสิ้นสุด YYYY-MM-DD")
    parser.add_argument("--no-table",  action="store_true", help="ซ่อนตาราง P&L รายไม้")
    parser.add_argument("--no-equity", action="store_true", help="ซ่อน equity curve")
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print()
    print(SEP)
    print(f"  AUTO TRADER DASHBOARD  [{now}]")
    if args.symbol:
        print(f"  Symbol filter : {args.symbol}")
    if args.date_from or args.date_to:
        print(f"  Date range    : {args.date_from or '—'} → {args.date_to or '—'}")
    print(SEP)

    open_pos, closed = load(args.symbol, args.date_from, args.date_to)

    print_summary(closed)
    print_monthly(closed)
    print_by_symbol(closed)

    if not args.no_table:
        print_pnl_table(closed)

    if not args.no_equity:
        print_equity_curve(closed)

    print_open(open_pos)


if __name__ == "__main__":
    main()
