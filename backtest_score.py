"""
backtest_score.py — เช็ค Score ณ เวลาในอดีต โดยเรียก scoring.compute_score ตรงๆ ผ่าน
as_of (แทนการ copy logic ของ compute_score มาเขียนซ้ำ) กันผลลัพธ์ backtest เพี้ยนจาก
ของจริงเวลาแก้ scoring.py แล้วลืมแก้ไฟล์นี้ตาม (ดู scoring.py.compute_score docstring)
ใช้: py backtest_score.py BTCUSDm Short 2026-06-27 23:00
"""
import sys
from datetime import datetime
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from mt5_connect import connect
from config import MT5_TIMEFRAMES, MIN_SCORE, TOTAL_WEIGHT
from scoring import compute_score, get_ohlcv, calc_rr

load_dotenv()
connect()

symbol    = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
direction = sys.argv[2] if len(sys.argv) > 2 else "Short"
date_str  = sys.argv[3] if len(sys.argv) > 3 else "2026-06-27"
time_str  = sys.argv[4] if len(sys.argv) > 4 else "23:00"

snap_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
print(f"\n  Backtest  {symbol} {direction}  ณ  {snap_dt}")

# ราคา ณ เวลานั้น = close ของแท่ง 1H ล่าสุดก่อนเวลานั้น — compute_score ใช้ค่าเดียวกันนี้
# ภายในเป๊ะเมื่อส่ง as_of (ดู scoring.py) จึงดึงมาเฉพาะไว้ทำ entry ให้ compute_score
df_1h_snap = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=2, as_of=snap_dt)
entry = df_1h_snap["close"].iloc[-1]
print(f"  ราคา ณ เวลานั้น : {entry:.2f}")

try:
    total, criteria, passed, sl_info = compute_score(symbol, direction, entry, as_of=snap_dt)
except ValueError as exc:
    print(f"  [BLOCKED] {exc}")
    mt5.shutdown()
    sys.exit()

sl, tp = sl_info["sl"], sl_info["tp"]
rr = calc_rr(entry, sl, tp, direction)
conf_result = sl_info.get("conf_result", {})

print(f"\n{'=' * 48}")
print(f"  SL={sl:.0f}  TP={tp:.0f}  R:R={rr:.2f}")
if conf_result:
    print(f"  Conf: {conf_result['reason'][:40]}")
print(f"{'=' * 48}")
for name, p, w in criteria:
    print(f"  {name:<15} {'PASS' if p else 'FAIL'}  {w if p else 0:.1f}/{w:.1f}")
print(f"{'-' * 48}")
print(f"  Total : {total:.1f} / {TOTAL_WEIGHT:.0f}  (min {MIN_SCORE:.0f})")
print(f"{'=' * 48}")
print(f"  >>> {'ENTER TRADE' if passed else 'NO ENTRY'} <<<")
print(f"{'=' * 48}")

mt5.shutdown()
