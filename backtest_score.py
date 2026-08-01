"""
backtest_score.py — เช็ค Score ณ เวลาในอดีต
ใช้: py backtest_score.py BTCUSDm Short 2026-06-27 23:00
"""
import sys
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from mt5_connect import connect
from config import MT5_TIMEFRAMES, MIN_SCORE, TOTAL_WEIGHT, MIN_RR, MIN_RR_HARD_BLOCK
from scoring import ema, calc_obv, calc_macd, calc_rr, macd_ok_for_direction, TREND_FLIP_K
from swing import find_sl_from_structure, find_tp_from_fibonacci, check_confirmation, swing_vol_multiplier, swing_wick_ratio_min, calc_di
from binance import merge_real_volume
from trend_flip import compute_trend_regime
from config import (
    WEIGHT_TREND_1D, WEIGHT_OBV_1D, WEIGHT_TREND_4H, WEIGHT_OBV_4H,
    WEIGHT_TREND_1H, WEIGHT_DI_1H, WEIGHT_MACD,
    WEIGHT_RR,
)

load_dotenv()
connect()

symbol    = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
direction = sys.argv[2] if len(sys.argv) > 2 else "Short"
date_str  = sys.argv[3] if len(sys.argv) > 3 else "2026-06-27"
time_str  = sys.argv[4] if len(sys.argv) > 4 else "23:00"

snap_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
print(f"\n  Backtest  {symbol} {direction}  ณ  {snap_dt}")

def get_hist(symbol, tf, dt, bars):
    rates = mt5.copy_rates_from(symbol, tf, dt, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"ดึงข้อมูลไม่ได้ {symbol}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

df_1d = get_hist(symbol, MT5_TIMEFRAMES["1D"], snap_dt, 800)   # 2026-07-27: 210->800 ให้ตรงกับ
                                                                # scoring.py (trend_flip ต้องการ
                                                                # ประวัติยาวพอให้ ratchet มั่นคง)
df_1d = merge_real_volume(df_1d, symbol, "1D")
df_4h = get_hist(symbol, MT5_TIMEFRAMES["4H"], snap_dt, 100)
df_1h = get_hist(symbol, MT5_TIMEFRAMES["1H"], snap_dt, 100)
df_1h = merge_real_volume(df_1h, symbol, "1H")   # 2026-07-27: ให้ตรงกับ scoring.py (เดิมไม่เคย merge)

# ราคา ณ เวลานั้น = close ของแท่งล่าสุด
entry = df_1h["close"].iloc[-1]
print(f"  ราคา ณ เวลานั้น : {entry:.2f}")

is_long  = direction.capitalize() == "Long"
vol_mult = swing_vol_multiplier(symbol)
wick_min = swing_wick_ratio_min(symbol)

# Bias — 2026-07-27: ให้ตรงกับ scoring.py.compute_score เป๊ะ (เดิมไฟล์นี้ใช้แค่ EMA เสมอ
# ทำให้ผลไม่ตรงกับที่ scheduler.py ตัดสินใจจริงสำหรับ symbol ที่มี k ยืนยันแล้ว)
ema50_1d  = ema(df_1d["close"], 50).iloc[-1]
ema200_1d = ema(df_1d["close"], 200).iloc[-1]
trend_flip_k = TREND_FLIP_K.get(symbol)
bias_source = "EMA50/200"
if trend_flip_k is not None:
    closed_1d = df_1d.iloc[:-1].reset_index(drop=True)
    flip_df, _ = compute_trend_regime(closed_1d, k=trend_flip_k)
    flip_regime = flip_df["regime"].iloc[-1]
    if flip_regime in ("Bull", "Bear"):
        bias = "Long" if flip_regime == "Bull" else "Short"
        bias_source = "trend_flip"
    else:
        bias = "Long" if ema50_1d > ema200_1d else "Short"   # bootstrap ไม่พร้อม -> fallback
else:
    bias = "Long" if ema50_1d > ema200_1d else "Short"
print(f"  Bias 1D : {bias}  ({bias_source})  (EMA50={ema50_1d:.0f}  EMA200={ema200_1d:.0f})")

if bias != direction.capitalize():
    print(f"  [BLOCKED] Direction ไม่ตรง Bias ({bias_source})")
    mt5.shutdown(); sys.exit()

# Auto SL/TP
sl_info = find_sl_from_structure(df_1d, direction, vol_multiplier=vol_mult, wick_ratio_min=wick_min)
if not sl_info.get("passed"):
    print(f"  [BLOCKED] หา SL ไม่ได้ — {sl_info.get('reason')}")
    mt5.shutdown(); sys.exit()

sl        = sl_info["sl"]
swing_idx = sl_info["swing_idx"]
fib       = find_tp_from_fibonacci(df_1d, direction, swing_idx, vol_multiplier=vol_mult, wick_ratio_min=wick_min)
tp        = fib["levels"]["0.786"] if fib.get("passed") else entry - abs(entry - sl) * MIN_RR   # ตรงกับ scoring.py ปัจจุบัน (2026-07-23)

rr = calc_rr(entry, sl, tp, direction)
if rr < MIN_RR_HARD_BLOCK:
    print(f"  [BLOCKED] R:R = {rr:.2f} ต่ำกว่า {MIN_RR_HARD_BLOCK}")
    mt5.shutdown(); sys.exit()

# Indicators
price     = entry
ema50_4h  = ema(df_4h["close"], 50).iloc[-1]
ema50_1h  = ema(df_1h["close"], 50).iloc[-1]
obv_1d    = calc_obv(df_1d)
obv_4h    = calc_obv(df_4h)
plus_di_1h, minus_di_1h = calc_di(df_1h)
macd_line, signal_line, macd_hist = calc_macd(df_4h)

def obv_ok(obv, lookback=5):
    return (obv.iloc[-1] > obv.iloc[-1-lookback]) if is_long else (obv.iloc[-1] < obv.iloc[-1-lookback])

conf_result = check_confirmation(price, df_4h, direction, symbol)

criteria = [
    ("Trend 1D",    (price > ema50_1d) if is_long else (price < ema50_1d), WEIGHT_TREND_1D),
    ("OBV 1D",      obv_ok(obv_1d),                                        WEIGHT_OBV_1D),
    ("Trend 4H",    (price > ema50_4h) if is_long else (price < ema50_4h), WEIGHT_TREND_4H),
    ("OBV 4H",      obv_ok(obv_4h),                                        WEIGHT_OBV_4H),
    ("Trend 1H",    (price > ema50_1h) if is_long else (price < ema50_1h), WEIGHT_TREND_1H),
    ("DI 1H",       (plus_di_1h.iloc[-1] > minus_di_1h.iloc[-1]) if is_long
                    else (minus_di_1h.iloc[-1] > plus_di_1h.iloc[-1]),  WEIGHT_DI_1H),
    ("MACD 4H",     macd_ok_for_direction(macd_line, signal_line, macd_hist, direction), WEIGHT_MACD),
    ("R:R",         rr >= MIN_RR,                                           WEIGHT_RR),
    # Confirmation ถูกตัดออกจาก scorecard 2026-07-26 (ดู config.py) — conf_result ยัง
    # คำนวณไว้แสดงผลอ้างอิงด้านล่างเท่านั้น ให้ตรงกับ scoring.py
]

total  = sum(w for _, p, w in criteria if p)
passed = total >= MIN_SCORE

print(f"\n{'=' * 48}")
print(f"  SL={sl:.0f}  TP={tp:.0f}  R:R={rr:.2f}")
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
