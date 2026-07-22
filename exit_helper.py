"""
Exit Decision Helper — ใช้ตอนจะตัดสินใจออก trade ก่อน SL/TP

Usage:
    python exit_helper.py                  # ดู open trades ทั้งหมด
    python exit_helper.py <ticket>         # ตรวจ trade ตาม ticket
    python exit_helper.py <ticket> <price> # ระบุ current price เอง
"""

import io
import os
import sys

# รองรับ UTF-8 บน Windows terminal
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import datetime

import pandas as pd

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades_log.csv")

# ─────────────────────────────────────────────
# ANSI colors (ใช้ได้ใน Windows Terminal / PowerShell 7+)
# ─────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def _g(s): return f"{GREEN}{s}{RESET}"
def _y(s): return f"{YELLOW}{s}{RESET}"
def _r(s): return f"{RED}{s}{RESET}"
def _c(s): return f"{CYAN}{s}{RESET}"
def _b(s): return f"{BOLD}{s}{RESET}"


# ─────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────

def load_open_trades() -> pd.DataFrame:
    if not os.path.exists(LOG_FILE):
        print(_r(f"ไม่พบ {LOG_FILE}"))
        sys.exit(1)
    df = pd.read_csv(LOG_FILE, dtype={"ticket": str})
    return df[df["status"] == "Open"].copy()


# ─────────────────────────────────────────────
# Calculations
# ─────────────────────────────────────────────

def calc_metrics(row: pd.Series, current_price: float) -> dict:
    entry     = float(row["entry"])
    sl        = float(row["sl"])
    tp        = float(row["tp"])
    direction = str(row["direction"]).strip().capitalize()

    sl_range  = abs(entry - sl)
    tp_range  = abs(tp - entry)

    if direction == "Long":
        dist_sl_pct  = (current_price - sl)  / sl  * 100   # + = ยังห่าง SL
        dist_tp_pct  = (tp - current_price)  / tp  * 100   # + = ยังห่าง TP
        pnl_pct      = (current_price - entry) / entry * 100
        tp_progress  = (current_price - entry) / tp_range * 100 if tp_range else 0
        r_multiple   = (current_price - entry) / sl_range if sl_range else 0
    else:  # Short
        dist_sl_pct  = (sl - current_price)  / sl  * 100
        dist_tp_pct  = (current_price - tp)  / tp  * 100
        pnl_pct      = (entry - current_price) / entry * 100
        tp_progress  = (entry - current_price) / tp_range * 100 if tp_range else 0
        r_multiple   = (entry - current_price) / sl_range if sl_range else 0

    # Time held
    entry_dt_str = f"{row['date']} {row['time']}"
    try:
        entry_dt  = datetime.strptime(entry_dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            entry_dt = datetime.strptime(entry_dt_str, "%d/%m/%Y %H:%M:%S")
        except ValueError:
            entry_dt = None

    time_held_days = None
    if entry_dt:
        delta = datetime.now() - entry_dt
        time_held_days = delta.total_seconds() / 86400

    return {
        "direction":      direction,
        "entry":          entry,
        "sl":             sl,
        "tp":             tp,
        "current_price":  current_price,
        "sl_range":       sl_range,
        "tp_range":       tp_range,
        "dist_sl_pct":    dist_sl_pct,
        "dist_tp_pct":    dist_tp_pct,
        "pnl_pct":        pnl_pct,
        "tp_progress":    tp_progress,
        "r_multiple":     r_multiple,
        "time_held_days": time_held_days,
    }


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def yn(val: bool) -> str:
    return _g("YES") if val else _r("NO ")


def print_header(ticket: str, symbol: str, m: dict):
    print()
    print("=" * 62)
    print(_b(f"  EXIT DECISION HELPER  —  {symbol}  ticket #{ticket}"))
    print("=" * 62)
    dir_color = _g if m["direction"] == "Long" else _r
    print(f"  Direction    : {dir_color(m['direction'])}")
    print(f"  Entry        : {m['entry']:,.3f}")
    print(f"  Stop Loss    : {m['sl']:,.3f}")
    print(f"  Take Profit  : {m['tp']:,.3f}")
    cp = f"{m['current_price']:,.3f}"
    print(f"  Current Price: {_b(cp)}")
    print()
    print("─" * 62)
    print(_b("  AUTO-CALCULATED METRICS"))
    print("─" * 62)

    dist_sl_color = _g if m["dist_sl_pct"] > 0 else _r
    dist_tp_color = _g if m["dist_tp_pct"] > 0 else _r
    pnl_color     = _g if m["pnl_pct"]  >= 0 else _r
    r_color       = _g if m["r_multiple"] >= 0 else _r

    s_sl  = f"{m['dist_sl_pct']:+.2f}%"
    s_tp  = f"{m['dist_tp_pct']:+.2f}%"
    s_pnl = f"{m['pnl_pct']:+.2f}%"
    s_r   = f"{m['r_multiple']:+.2f}R"
    s_tpp = f"{m['tp_progress']:,.1f}%"
    print(f"  ระยะห่างจาก SL  : {dist_sl_color(s_sl)}")
    print(f"  ระยะห่างจาก TP  : {dist_tp_color(s_tp)}")
    print(f"  Current P/L     : {pnl_color(s_pnl)}")
    print(f"  R-multiple      : {r_color(s_r)}")
    print(f"  TP Progress     : {_y(s_tpp)}")
    if m["time_held_days"] is not None:
        s_days = f"{m['time_held_days']:.1f} days"
        print(f"  Time Held       : {_y(s_days)}")
    print()


# ─────────────────────────────────────────────
# Checklist
# ─────────────────────────────────────────────

def ask_manual(question: str, hint: str = "") -> bool:
    """ถามคำถาม Yes/No จากผู้ใช้"""
    if hint:
        print(f"     {DIM}({hint}){RESET}")
    while True:
        ans = input(f"     → {question}  [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", ""):
            return False


def run_checklist(m: dict) -> list[dict]:
    """
    Returns list of {no, question, auto, answer, action}
    """
    d   = m["direction"]
    r   = m["r_multiple"]
    tp_p = m["tp_progress"]
    days = m["time_held_days"] or 0
    dist_tp = m["dist_tp_pct"]

    # hit SL / hit TP  (auto)
    if d == "Long":
        hit_sl = m["current_price"] <= m["sl"]
        hit_tp = m["current_price"] >= m["tp"]
    else:
        hit_sl = m["current_price"] >= m["sl"]
        hit_tp = m["current_price"] <= m["tp"]

    ge1r        = r >= 1.0
    near_tp     = dist_tp <= 0.5
    slow_trade  = days >= 3 and r < 0.5
    fast_win    = r >= 2.0 and days < 3
    half_way    = tp_p >= 50

    print("─" * 62)
    print(_b("  EXIT CHECKLIST"))
    print("─" * 62)

    items = []

    def _item(no, q, auto_ans, action, hint="", ask=False):
        ans = auto_ans
        if ask:
            print(f"\n  {no}. {q}")
            ans = ask_manual(q, hint)
        tag = _y("AUTO") if not ask else _c("MANUAL")
        items.append({"no": no, "question": q, "answer": ans,
                      "action": action, "tag": tag})

    _item(1,  "Hit SL แล้วหรือยัง?",
          hit_sl, "ออกทันที ไม่มีข้อยกเว้น")

    _item(2,  "Hit TP แล้วหรือยัง?",
          hit_tp, "เก็บกำไรตามแผน")

    _item(3,  "Daily/4H ปิดสวน trend ที่ใช้เข้า?",
          False, "พิจารณาออก",
          hint="เช่น Long แต่ Daily ปิดต่ำกว่า EMA 50",
          ask=True)

    _item(4,  "Structure ที่ใช้เข้าพังแล้ว?",
          False, "พิจารณาออก",
          hint="swing low/high ที่ใช้ตัดสินใจถูก break",
          ask=True)

    _item(5,  "มี major news/event เปลี่ยน fundamental?",
          False, "พิจารณาออก",
          hint="Fed, FOMC, เหตุการณ์ใหญ่",
          ask=True)

    _item(6,  "กำไร >= 1R แล้ว?",
          ge1r, "ขยับ SL เป็น Breakeven")

    _item(7,  "ราคาใกล้ TP มาก (ภายใน 0.5%)?",
          near_tp, "แบ่งเก็บ TP1 50%, ปล่อย runner")

    _item(8,  "เข้ามา 3 วันแล้ว ยังไม่ได้กำไร 0.5R?",
          slow_trade, "Time exit — ตัดออก รอ setup ใหม่")

    _item(9,  "กำไรเร็วปกติ (>= 2R ใน < 3 วัน)?",
          fast_win, "แบ่งเก็บ 50% ก่อน เพราะมักมี pullback")

    _item(10, "ราคาเดินทาง >= 50% ของระยะ Entry→TP?",
          half_way, "แบ่งเก็บ TP1 (25-50%)")

    _item(11, "แท่งล่าสุดเป็น Climax (volume สุดขั้ว + range ใหญ่)?",
          False, "แบ่งเก็บ ไม่ปล่อยเต็ม",
          hint="Buying/Selling climax = exhaustion signal",
          ask=True)

    return items


# ─────────────────────────────────────────────
# Final Decision
# ─────────────────────────────────────────────

RULES = [
    "Score เปลี่ยน ≠ ต้องออก — signal เข้าใช้แคตตอนเข้า ห้ามใช้เพื่อออก",
    "Hit SL = ออกทันที ไม่มีข้อยกเว้น (ห้ามขยับ)",
    "ขยับ SL เข้าได้ (เป็น breakeven หรือใกล้กำไร) — ห้ามขยับออกไป",
    "ออกก่อน SL ได้แค่เมื่อ structure พัง หรือ major news",
    "ถ้าลังเล = ถือต่อ จนกว่าจะ hit SL/TP",
    "กำไร 1R แล้ว → ขยับ SL เป็น breakeven",
    "ใกล้ TP → แบ่งเก็บ 50% ที่ TP, ปล่อยอีก",
    "กำไรเร็วผิดปกติ (2R ใน 1-2 วัน) → แบ่งเก็บ 50% ก่อน เพราะมักมี pullback",
    "เดินทาง 50% ของระยะ TP → TP1 เพื่อ secure กำไรที่ paper อยู่",
    "แท่ง Climax (volume สุดขั้ว) → exhaustion signal → แบ่งเก็บ ไม่ปล่อยเต็ม",
]


def print_result(items: list[dict], m: dict):
    print()
    print("─" * 62)
    print(_b("  CHECKLIST RESULT"))
    print("─" * 62)
    for it in items:
        ans_str = _g("YES") if it["answer"] else _r("NO ")
        print(f"  {it['no']:>2}. [{ans_str}] [{it['tag']}]  {it['question']}")
        if it["answer"]:
            print(f"        → {_y(it['action'])}")

    # Derive final decision
    yes_items  = [it for it in items if it["answer"]]
    urgent     = any(it["no"] in (1,) for it in yes_items)   # hit SL
    exit_now   = any(it["no"] in (3, 4) for it in yes_items)  # structure/trend break
    partial    = any(it["no"] in (7, 9, 10, 11) for it in yes_items)
    move_sl    = any(it["no"] == 6 for it in yes_items)

    print()
    print("─" * 62)
    print(_b("  FINAL DECISION"))
    print("─" * 62)

    if urgent:
        print(f"  {_r('🔴 ออกทันที! Hit SL — ห้ามรั้ง')}")
    elif exit_now:
        print(f"  {_r('🔴 พิจารณาออก — Structure/Trend เปลี่ยน')}")
    elif partial and move_sl:
        print(f"  {_g('🟢 แบ่งเก็บกำไร + เลื่อน SL เป็น Breakeven')}")
    elif partial:
        print(f"  {_y('🟡 แบ่งเก็บกำไรบางส่วน')}")
    elif move_sl:
        print(f"  {_y('🟡 ขยับ SL เป็น Breakeven — ถือต่อ')}")
    else:
        print(f"  {_g('🟢 ถือต่อ — ยังไม่มี signal ให้ออก')}")

    print()
    print("─" * 62)
    print(_b("  กฎทองของการออก"))
    print("─" * 62)
    for i, rule in enumerate(RULES, 1):
        print(f"  {i:>2}. {DIM}{rule}{RESET}")
    print()


# ─────────────────────────────────────────────
# Show open trades list
# ─────────────────────────────────────────────

def show_open_trades(df: pd.DataFrame):
    print()
    print(_b("  OPEN TRADES"))
    print("─" * 62)
    print(f"  {'#':<4} {'Ticket':<14} {'Symbol':<12} {'Dir':<6} {'Entry':>10} {'SL':>10} {'TP':>10} {'Date'}")
    print("  " + "-" * 58)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        print(f"  {i:<4} {row['ticket']:<14} {row['symbol']:<12} {row['direction']:<6} "
              f"{float(row['entry']):>10,.3f} {float(row['sl']):>10,.3f} {float(row['tp']):>10,.3f}  {row['date']}")
    print()


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    open_trades = load_open_trades()

    if open_trades.empty:
        print(_y("ไม่มี Open trade ใน trades_log.csv"))
        return

    # ── no args → show list ──
    if len(sys.argv) == 1:
        show_open_trades(open_trades)
        print(f"Usage: python exit_helper.py <ticket> [current_price]")
        return

    ticket = sys.argv[1]
    mask   = open_trades["ticket"] == str(ticket)
    if not mask.any():
        print(_r(f"ticket #{ticket} ไม่พบในรายการ Open"))
        show_open_trades(open_trades)
        return

    row = open_trades[mask].iloc[0]

    # current price — arg หรือถามผู้ใช้
    if len(sys.argv) >= 3:
        try:
            current_price = float(sys.argv[2])
        except ValueError:
            print(_r("current_price ต้องเป็นตัวเลข"))
            sys.exit(1)
    else:
        while True:
            try:
                current_price = float(input(f"\n  ราคาปัจจุบัน ({row['symbol']}): "))
                break
            except ValueError:
                print("  กรุณาใส่ตัวเลข")

    m = calc_metrics(row, current_price)

    print_header(ticket, row["symbol"], m)
    items = run_checklist(m)
    print_result(items, m)


if __name__ == "__main__":
    main()
