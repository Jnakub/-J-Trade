"""
backtest_rsi_rebound.py — เทียบ RSI 4H Double Rebound (ใหม่) vs RSI 4H threshold เดี่ยว (เดิม)

Methodology เดียวกับที่เคยวัด Trend4H vs RSI4H (ดู scoring.py comment เหนือ WEIGHT_RSI_4H):
  - Signal Rate      : ออกสัญญาณกี่ % ของแท่งทั้งหมด
  - Predictive Power : ราคาไปตามทิศที่สัญญาณบอกกี่ % (วัดตอน forward=6 แท่งข้างหน้า, เฉพาะตอนที่ signal ออก)

ดึงข้อมูลจริงจาก MT5 ครั้งเดียว (4H, N bars) แล้ววนเช็คในหน่วยความจำทั้งหมด ไม่ยิง MT5 ซ้ำทีละแท่ง
เร็วกว่า backtest_score.py ที่ต้องเรียก compute_score() เต็มรูปแบบทีละจุดมาก

ใช้: py backtest_rsi_rebound.py <SYMBOL> [bars=1500] [forward=6]
"""
import sys
from dotenv import load_dotenv

from mt5_connect import connect
from config import MT5_TIMEFRAMES, RSI_SCORE_PERIOD, RSI_SCORE_MID, RSI_SCORE_BUFFER, \
    RSI_REBOUND_LOOKBACK_BARS, RSI_REBOUND_MIN_TOUCHES
from scoring import get_ohlcv, check_rsi_double_rebound
from indicators import calc_rsi

load_dotenv()
connect()

symbol  = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
bars    = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
forward = int(sys.argv[3]) if len(sys.argv) > 3 else 6

df  = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=bars)
rsi = calc_rsi(df["close"], RSI_SCORE_PERIOD)
close = df["close"]

zone_hi = RSI_SCORE_MID + RSI_SCORE_BUFFER
zone_lo = RSI_SCORE_MID - RSI_SCORE_BUFFER

start = RSI_REBOUND_LOOKBACK_BARS + 1
end   = len(df) - forward

results = {
    "old_long":  {"signals": 0, "correct": 0},
    "old_short": {"signals": 0, "correct": 0},
    "new_long":  {"signals": 0, "correct": 0},
    "new_short": {"signals": 0, "correct": 0},
}

for i in range(start, end):
    rsi_now  = rsi.iloc[i]
    price_now, price_fwd = close.iloc[i], close.iloc[i + forward]
    up_move  = price_fwd > price_now
    down_move = price_fwd < price_now
    window   = rsi.iloc[:i + 1]

    # เกณฑ์เดิม — threshold เดี่ยว ณ แท่งปัจจุบัน
    if rsi_now > zone_hi:
        results["old_long"]["signals"] += 1
        results["old_long"]["correct"] += int(up_move)
    if rsi_now < zone_lo:
        results["old_short"]["signals"] += 1
        results["old_short"]["correct"] += int(down_move)

    # เกณฑ์ใหม่ — Double Rebound (ส่งเฉพาะข้อมูลถึงแท่ง i กันดู future)
    if check_rsi_double_rebound(window, is_long=True,
                                lookback=RSI_REBOUND_LOOKBACK_BARS,
                                min_touches=RSI_REBOUND_MIN_TOUCHES):
        results["new_long"]["signals"] += 1
        results["new_long"]["correct"] += int(up_move)
    if check_rsi_double_rebound(window, is_long=False,
                                lookback=RSI_REBOUND_LOOKBACK_BARS,
                                min_touches=RSI_REBOUND_MIN_TOUCHES):
        results["new_short"]["signals"] += 1
        results["new_short"]["correct"] += int(down_move)

total_bars = end - start
print(f"\n  {symbol}  4H  {total_bars} แท่ง  (forward={forward} แท่ง)")
print(f"  {'='*70}")
print(f"  {'เกณฑ์':<28} {'Signal Rate':>14} {'Predictive Power':>20}")
print(f"  {'-'*70}")
for key, label in [("old_long", "เดิม (RSI>55) Long"), ("new_long", "ใหม่ (Rebound) Long"),
                   ("old_short", "เดิม (RSI<45) Short"), ("new_short", "ใหม่ (Rebound) Short")]:
    r = results[key]
    sig_rate = r["signals"] / total_bars * 100 if total_bars else 0
    pred_pow = r["correct"] / r["signals"] * 100 if r["signals"] else 0
    print(f"  {label:<28} {r['signals']:>4} ({sig_rate:5.1f}%)   {pred_pow:6.1f}%  ({r['correct']}/{r['signals']})")
print(f"  {'='*70}")
