"""
backtest_scorecard.py — จำลอง compute_score() ย้อนหลังทีละแท่ง 4H จริง (ผ่าน as_of เหมือน
backtest_score.py) แล้ววัดผลจริงถ้าเข้าไม้ทุกครั้งที่ผ่านเกณฑ์ (Win Rate, Expectancy)

ต่างจาก backtest_score.py (เช็คจุดเดียว) — ตัวนี้วนทั้งช่วงเวลา ถือได้แค่ 1 ไม้พร้อมกัน
(เหมือน scheduler.py จริงที่ข้ามการหา entry ใหม่ถ้ามี position เปิดอยู่)

ยังไม่จำลอง Exit Monitor (Position Sizing/Trailing) เต็มรูปแบบ — ปิดไม้แค่ตอนชน SL/TP เป๊ะ
หรือ time-exit ถ้าเกิน MAX_HOLD_BARS แท่ง (ปิดที่ close ของแท่งนั้น) เพื่อความเร็ว/ความง่าย
วัดแค่ "คุณภาพของ entry signal + SL/TP" ไม่ใช่ผลจริง 100% ของทั้งระบบ

ใช้: py backtest_scorecard.py BTCUSDm [days=45]
"""
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

from mt5_connect import connect
from config import MT5_TIMEFRAMES, MIN_SCORE, TOTAL_WEIGHT
from scoring import compute_score, get_ohlcv, get_trend_bias, calc_rr
from binance import merge_real_volume

load_dotenv()
connect()

symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
days   = int(sys.argv[2]) if len(sys.argv) > 2 else 45
MAX_HOLD_BARS = 60   # ถือไม้นานสุดกี่แท่ง 4H (=10 วัน) ก่อน time-exit

df4h_full = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=days * 6 + 10)
end_time  = df4h_full["time"].iloc[-1]
start_time = end_time - timedelta(days=days)
scan_idx = df4h_full.index[df4h_full["time"] >= start_time].tolist()

print(f"\n{symbol}  scorecard backtest  {start_time} -> {end_time}  ({len(scan_idx)} แท่ง 4H)")
print(f"MAX_HOLD_BARS={MAX_HOLD_BARS}\n")

trades = []
in_position = False
pos = None
last_date = None
df_1d_cache = None

for n, i in enumerate(scan_idx):
    row = df4h_full.iloc[i]
    t   = row["time"]

    if in_position:
        hit_sl = (row["low"] <= pos["sl"]) if pos["direction"] == "Long" else (row["high"] >= pos["sl"])
        hit_tp = (row["high"] >= pos["tp"]) if pos["direction"] == "Long" else (row["low"] <= pos["tp"])
        bars_held = i - pos["entry_idx"]
        outcome = None
        if hit_sl:
            outcome = "SL"
        elif hit_tp:
            outcome = "TP"
        elif bars_held >= MAX_HOLD_BARS:
            outcome = "TIME"
        if outcome:
            exit_price = pos["sl"] if outcome == "SL" else (pos["tp"] if outcome == "TP" else row["close"])
            r_mult = ((exit_price - pos["entry"]) / abs(pos["entry"] - pos["sl"])
                      if pos["direction"] == "Long"
                      else (pos["entry"] - exit_price) / abs(pos["entry"] - pos["sl"]))
            trades.append({"entry_time": pos["entry_time"], "direction": pos["direction"],
                           "outcome": outcome, "r_multiple": r_mult, "score": pos["score"]})
            in_position = False
            pos = None
        continue

    # daily bias cache — 1D เปลี่ยนวันละครั้ง ไม่ต้องดึงซ้ำทุกแท่ง 4H
    date = t.date()
    if date != last_date:
        try:
            df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t)
            df_1d = merge_real_volume(df_1d, symbol, "1D", as_of=t)
            df_1d_cache = df_1d
        except Exception:
            df_1d_cache = None
        last_date = date

    if df_1d_cache is None:
        continue
    bias, _ = get_trend_bias(symbol, df_1d_cache)
    if bias is None:
        continue

    try:
        entry_snap = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=2, as_of=t)
        entry = entry_snap["close"].iloc[-1]
        total, criteria, passed, sl_info = compute_score(symbol, bias, entry, as_of=t, df_1d=df_1d_cache)
    except ValueError:
        continue
    except Exception as exc:
        print(f"  [{t}] ERROR ไม่คาดคิด: {exc}")
        continue

    if passed:
        in_position = True
        pos = {"entry_time": t, "entry_idx": i, "direction": bias, "entry": entry,
               "sl": sl_info["sl"], "tp": sl_info["tp"], "score": total}

    if n % 40 == 0:
        print(f"  ... สแกนถึง {t}  ({n}/{len(scan_idx)})  เข้าไม้แล้ว {len(trades)} ครั้ง")

print(f"\n{'=' * 60}")
print(f"  รวมเข้าไม้ {len(trades)} ครั้ง  (ยังเปิดค้างอยู่: {'ใช่' if in_position else 'ไม่'})")
if trades:
    wins = [t for t in trades if t["r_multiple"] > 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = sum(t["r_multiple"] for t in trades) / len(trades)
    print(f"  Win Rate     : {win_rate:.1f}%  ({len(wins)}/{len(trades)})")
    print(f"  Avg R-mult   : {avg_r:+.2f}R  (Expectancy)")
    print(f"  {'-' * 56}")
    for t in trades:
        print(f"  {t['entry_time']}  {t['direction']:<6} {t['outcome']:<5} "
              f"R={t['r_multiple']:+.2f}  Score={t['score']:.1f}")
print(f"{'=' * 60}")
