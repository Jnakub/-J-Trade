import os
from datetime import date, datetime

import pandas as pd

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades_log.csv")

COLUMNS = [
    "date", "time", "symbol", "direction", "entry", "sl", "tp",
    "lot", "score", "ticket", "status", "net_pnl", "note",
    "pinned_swing", "pinned_atr_entry",
]


def _load() -> pd.DataFrame:
    if os.path.exists(LOG_FILE):
        df = pd.read_csv(LOG_FILE, dtype={"ticket": str})
        for col in COLUMNS:   # เผื่อไฟล์เก่าก่อนมี pinned_swing/pinned_atr_entry
            if col not in df.columns:
                df[col] = ""
        return df
    return pd.DataFrame(columns=COLUMNS)


def _save(df: pd.DataFrame) -> None:
    try:
        df.to_csv(LOG_FILE, index=False)
    except PermissionError:
        raise PermissionError(
            f"เขียน {LOG_FILE} ไม่ได้ — ปิด Excel หรือโปรแกรมที่เปิดไฟล์นี้ค้างอยู่ก่อน"
        )


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------

def log_trade_open(symbol: str, direction: str, entry: float,
                   sl: float, tp: float, lot: float,
                   score: float, ticket: int,
                   pinned_swing: float = None, pinned_atr_entry: float = None) -> None:
    """pinned_swing/pinned_atr_entry: ฐานตรึงของ ATR Trailing SL ณ ตอนเปิดไม้ (exit_monitor.py
    ใช้ค่านี้ตลอดการถือ แทนคำนวณ swing ใหม่จาก rolling window ทุกชั่วโมง — กัน anchor สลับ
    ถ้าถือ position ยาวจน swing เดิมหลุดขอบหน้าต่างข้อมูล) — ไม่ส่งมาได้ (เช่นหา SL ไม่ได้ตอนเข้า)"""
    df = _load()
    now = datetime.now()
    row = {
        "date":      now.strftime("%Y-%m-%d"),
        "time":      now.strftime("%H:%M:%S"),
        "symbol":    symbol,
        "direction": direction,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "lot":       lot,
        "score":     score,
        "ticket":    str(ticket),
        "status":    "Open",
        "net_pnl":   "",
        "note":      "",
        "pinned_swing":     pinned_swing if pinned_swing is not None else "",
        "pinned_atr_entry": pinned_atr_entry if pinned_atr_entry is not None else "",
    }
    new_row = pd.DataFrame([row], columns=COLUMNS)
    df = pd.concat([df, new_row], ignore_index=True)
    _save(df)
    print(f"[journal] Open logged  ticket=#{ticket}  {symbol} {direction}")


# ---------------------------------------------------------------------------
# ATR Trailing SL pinned anchor (บันทึกตอนเปิดไม้ — อ่านกลับใน exit_monitor.py)
# ---------------------------------------------------------------------------

def get_pinned_anchor(ticket: int) -> tuple:
    """คืน (pinned_swing, pinned_atr_entry) ที่บันทึกไว้ตอนเปิดไม้ หรือ (None, None)
    ถ้าไม่พบ/ไม่เคยบันทึก (เช่น position เปิดก่อนมีฟีเจอร์นี้) — ให้ caller fallback เอง"""
    df = _load()
    mask = df["ticket"] == str(ticket)
    if not mask.any():
        return None, None
    row = df.loc[mask].iloc[-1]
    swing      = row.get("pinned_swing", "")
    atr_entry  = row.get("pinned_atr_entry", "")
    if swing == "" or pd.isna(swing) or atr_entry == "" or pd.isna(atr_entry):
        return None, None
    return float(swing), float(atr_entry)


def get_original_tp(ticket: int) -> float | None:
    """คืน TP ตอนเปิดไม้จริง (คอลัมน์ 'tp' ที่ log ไว้ตอน log_trade_open) — ใช้เป็น pinned
    anchor ของ TP Trailing ใน exit_monitor.py (ต้องเป็นค่าดั้งเดิม ไม่ใช่ pos.tp ปัจจุบันที่
    อาจถูกขยับไปแล้วจากรอบก่อนหน้า) คืน None ถ้าไม่พบ"""
    df = _load()
    mask = df["ticket"] == str(ticket)
    if not mask.any():
        return None
    row = df.loc[mask].iloc[-1]
    tp = row.get("tp", "")
    if tp == "" or pd.isna(tp):
        return None
    return float(tp)


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------

def log_trade_close(ticket: int, result: str,
                    net_pnl: float, note: str = "") -> None:
    """result: 'Take Profit' | 'Stop Loss' | 'Manual Cut'"""
    df = _load()
    mask = df["ticket"] == str(ticket)
    if not mask.any():
        raise ValueError(f"ticket #{ticket} ไม่พบใน {LOG_FILE}")
    df.loc[mask, "status"]  = result
    df.loc[mask, "net_pnl"] = float(net_pnl)
    df.loc[mask, "note"]    = str(note)
    _save(df)
    sign = "+" if net_pnl >= 0 else ""
    print(f"[journal] Close logged  ticket=#{ticket}  {result}  P/L {sign}{net_pnl:.2f}")


# ---------------------------------------------------------------------------
# Daily loss guard
# ---------------------------------------------------------------------------

def check_daily_loss(balance: float, max_daily_loss_pct: float) -> bool:
    """Return False if today's closed P/L already breached the daily limit."""
    df = _load()
    today = date.today().strftime("%Y-%m-%d")
    closed_today = df[(df["date"] == today) & (df["status"] != "Open")]
    if closed_today.empty:
        return True
    daily_pnl = pd.to_numeric(closed_today["net_pnl"], errors="coerce").sum()
    limit = -abs(balance * max_daily_loss_pct)
    if daily_pnl <= limit:
        print(f"[journal] Daily loss limit hit: {daily_pnl:.2f} <= {limit:.2f}  — ห้ามเข้า trade เพิ่ม")
        return False
    return True


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_statistics() -> dict:
    df = _load()
    closed = df[df["status"] != "Open"].copy()
    closed["net_pnl"] = pd.to_numeric(closed["net_pnl"], errors="coerce")
    closed = closed.dropna(subset=["net_pnl"])

    total  = len(closed)
    if total == 0:
        return {
            "total_trades": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0,  "expectancy": 0.0,
        }

    wins   = closed[closed["net_pnl"] > 0]
    losses = closed[closed["net_pnl"] < 0]

    win_rate   = len(wins) / total
    loss_rate  = 1 - win_rate
    avg_win    = wins["net_pnl"].mean()   if len(wins)   else 0.0
    avg_loss   = losses["net_pnl"].mean() if len(losses) else 0.0
    total_pnl  = closed["net_pnl"].sum()
    expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)

    stats = {
        "total_trades": total,
        "win_rate":     round(win_rate * 100, 2),
        "total_pnl":    round(total_pnl, 2),
        "avg_win":      round(avg_win, 2),
        "avg_loss":     round(avg_loss, 2),
        "expectancy":   round(expectancy, 2),
    }
    return stats


# ---------------------------------------------------------------------------
# CLI  (python journal.py stats)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    def _usage():
        print("Usage:")
        print("  python journal.py stats")
        print("  python journal.py open  <ticket> <symbol> <direction> <entry> <sl> <tp> <lot> [score]")
        print("  python journal.py close <ticket> <result> <pnl>")
        print()
        print("  direction : Long | Short")
        print("  result    : 'Take Profit' | 'Stop Loss' | 'Manual Cut'")
        print("  score     : optional (default 0)")
        print()
        print("Example:")
        print("  python journal.py open  123456 BTCUSDm Short 65000 67500 59000 0.01")
        print("  python journal.py close 123456 \"Manual Cut\" -120.50")

    if len(sys.argv) < 2:
        _usage()

    elif sys.argv[1] == "stats":
        s = get_statistics()
        print()
        print("=" * 38)
        print("  Trade Statistics")
        print("=" * 38)
        print(f"  Total Trades  : {s['total_trades']}")
        print(f"  Win Rate      : {s['win_rate']:.1f}%")
        print(f"  Total P&L     : {s['total_pnl']:+.2f} USD")
        print(f"  Avg Win       : {s['avg_win']:+.2f} USD")
        print(f"  Avg Loss      : {s['avg_loss']:+.2f} USD")
        print(f"  Expectancy    : {s['expectancy']:+.2f} USD/trade")
        print("=" * 38)

    elif sys.argv[1] == "open":
        if len(sys.argv) not in (9, 10):
            print("Error: ต้องใส่ argument ให้ครบ")
            _usage()
            sys.exit(1)
        try:
            ticket    = int(sys.argv[2])
            symbol    = sys.argv[3]
            direction = sys.argv[4].capitalize()
            entry     = float(sys.argv[5])
            sl        = float(sys.argv[6])
            tp        = float(sys.argv[7])
            lot       = float(sys.argv[8])
            score     = float(sys.argv[9]) if len(sys.argv) == 10 else 0.0
        except ValueError:
            print("Error: ตัวเลขไม่ถูกต้อง")
            sys.exit(1)

        if direction not in ("Long", "Short"):
            print("Error: direction ต้องเป็น Long หรือ Short")
            sys.exit(1)

        log_trade_open(symbol, direction, entry, sl, tp, lot, score, ticket)

    elif sys.argv[1] == "close":
        if len(sys.argv) != 5:
            print("Error: ต้องใส่ครบ 4 argument")
            _usage()
            sys.exit(1)
        try:
            ticket  = int(sys.argv[2])
            result  = sys.argv[3]
            net_pnl = float(sys.argv[4])
        except ValueError:
            print("Error: ticket ต้องเป็นตัวเลข, pnl ต้องเป็นทศนิยม")
            sys.exit(1)

        valid = ("Take Profit", "Stop Loss", "Manual Cut")
        if result not in valid:
            print(f"Error: result ต้องเป็นหนึ่งใน {valid}")
            sys.exit(1)

        try:
            log_trade_close(ticket, result, net_pnl)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    else:
        _usage()
