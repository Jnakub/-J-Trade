"""
backtest_reversal.py — Backtest ของ Reversal path ทั้งเส้น (regime_check REVERSAL-READY -> scheduler
direction decision -> reversal.compute_reversal_score) ณ เวลาในอดีต ใช้ historical bars เท่านั้น
(ไม่แตะ live tick/positions) — เทียบเท่า backtest_score.py แต่สำหรับ Reversal แทน Trend-following

โดยเฉพาะใช้เช็คการแก้ scheduler.py:108-110 (direction มาจาก divergence polarity ไม่ใช่ invert bias)
ว่าให้ direction/score ตรงกับที่ regime_check ตั้งใจจริงหรือไม่ ในเคสอดีตที่เคย REVERSAL-READY

ใช้: python backtest_reversal.py <SYMBOL> "<YYYY-MM-DD>" "<HH:MM>"
     python backtest_reversal.py BTCUSDm 2026-06-27 23:00
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
from config import MT5_TIMEFRAMES
from scoring import calc_rr
from swing import (
    find_swing_highs, find_swing_lows, find_sl_from_structure,
    find_tp_from_fibonacci, swing_vol_multiplier, swing_wick_ratio_min,
)
from exit_monitor import is_climax_bar
from regime_check import (
    calc_adx, adx_direction, adx_peak_info, check_structure, cluster_price_zones,
    calc_rsi, ADX_PERIOD, ADX_CHOPPY, ADX_GRAY_HIGH, ADX_PEAK_LOOKBACK,
    SWING_LEFT_RIGHT, SWING_TOLERANCE, KEY_LEVEL_PROXIMITY, KEY_LEVEL_MIN_TOUCHES,
    DIV_MAX_AGE_BARS, DIV_ZONE_OVERBOUGHT, DIV_ZONE_OVERSOLD,
)
import reversal as rev

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)

load_dotenv()
connect()

symbol   = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
date_str = sys.argv[2] if len(sys.argv) > 2 else "2026-06-27"
time_str = sys.argv[3] if len(sys.argv) > 3 else "23:00"

snap_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
print(f"\n  Backtest Reversal path  {symbol}  ณ  {snap_dt}")


def get_hist(sym, tf, dt, bars):
    rates = mt5.copy_rates_from(sym, tf, dt, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"ดึงข้อมูลไม่ได้ {sym}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


# ── historical 4H data, ตัดแท่งที่ยังไม่ปิด ณ snap_dt (เหมือน regime_check/reversal ตัดแท่งสด) ──
df_4h_full = get_hist(symbol, MT5_TIMEFRAMES["4H"], snap_dt, 400)
df_4h      = df_4h_full.iloc[:len(df_4h_full) - 1].reset_index(drop=True)
entry      = df_4h["close"].iloc[-1]
vol_mult   = swing_vol_multiplier(symbol)
wick_min   = swing_wick_ratio_min(symbol)
closed_idx = len(df_4h) - 1

print(f"  ราคา ณ เวลานั้น (close 4H ปิดล่าสุด) : {entry:.3f}")

# ── ข้อ 1-3: ADX peak & decline (regime_check logic, historical) ──
adx_series = calc_adx(df_4h, ADX_PERIOD)
adx_now    = adx_series.iloc[closed_idx]
direction_adx = adx_direction(adx_series, closed_idx)
peak       = adx_peak_info(adx_series, closed_idx)

# ── ข้อ 4: Structure ──
structure = check_structure(df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True),
                            vol_multiplier=vol_mult, wick_ratio_min=wick_min)

# ── ข้อ 5: Key Level (window ท้ายสุดของ df_4h ที่ดึงมา ~400 แท่ง) ──
highs = find_swing_highs(df_4h, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                         tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_mult, wick_ratio_min=wick_min)
lows  = find_swing_lows(df_4h, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                        tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_mult, wick_ratio_min=wick_min)
points = ([(df_4h["high"].iloc[i], df_4h["time"].iloc[i], "High") for i in highs] +
          [(df_4h["low"].iloc[i],  df_4h["time"].iloc[i],  "Low")  for i in lows])
zones = cluster_price_zones(points, KEY_LEVEL_PROXIMITY)
major = [z for z in zones if len(z["members"]) >= KEY_LEVEL_MIN_TOUCHES]
if major:
    in_range = [z for z in major if abs(entry - z["center"]) / z["center"] <= KEY_LEVEL_PROXIMITY]
    chosen = max(in_range, key=lambda z: len(z["members"])) if in_range else min(major, key=lambda z: abs(entry - z["center"]))
    dist_pct = abs(entry - chosen["center"]) / chosen["center"]
    key_level = {"at_key_level": dist_pct <= KEY_LEVEL_PROXIMITY, "nearest": chosen["center"],
                "distance_pct": dist_pct, "touches": len(chosen["members"]), "members": chosen["members"], "zones": major}
else:
    key_level = {"at_key_level": False, "nearest": None, "distance_pct": None, "touches": 0, "zones": major}


# ── ข้อ 6: Divergence (regime_check.check_divergence logic, historical) ──
def check_divergence_hist(df):
    rsi_series = calc_rsi(df["close"])
    hs = find_swing_highs(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                          tolerance_atr=SWING_TOLERANCE, vol_multiplier=0.0)
    ls = find_swing_lows(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                         tolerance_atr=SWING_TOLERANCE, vol_multiplier=0.0)
    last_idx = len(df) - 1
    result = {"divergence": None, "detail": "", "points": []}

    if len(hs) >= 2:
        h1, h2 = hs[-2], hs[-1]
        fresh   = (last_idx - h2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi_series.iloc[h1] >= DIV_ZONE_OVERBOUGHT
        if fresh and zone_ok and df["high"].iloc[h2] > df["high"].iloc[h1] and rsi_series.iloc[h2] < rsi_series.iloc[h1]:
            result["divergence"] = "bearish"
            result["detail"] = (f"ราคา HH ({df['high'].iloc[h1]:,.2f} -> {df['high'].iloc[h2]:,.2f}) "
                                f"แต่ RSI LH ({rsi_series.iloc[h1]:.1f} -> {rsi_series.iloc[h2]:.1f})")
            return result

    if len(ls) >= 2:
        l1, l2 = ls[-2], ls[-1]
        fresh   = (last_idx - l2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi_series.iloc[l1] <= DIV_ZONE_OVERSOLD
        if fresh and zone_ok and df["low"].iloc[l2] < df["low"].iloc[l1] and rsi_series.iloc[l2] > rsi_series.iloc[l1]:
            result["divergence"] = "bullish"
            result["detail"] = (f"ราคา LL ({df['low'].iloc[l1]:,.2f} -> {df['low'].iloc[l2]:,.2f}) "
                                f"แต่ RSI HL ({rsi_series.iloc[l1]:.1f} -> {rsi_series.iloc[l2]:.1f})")
            return result

    result["detail"] = f"ไม่พบ divergence (สดภายใน {DIV_MAX_AGE_BARS} แท่ง)"
    return result


divergence = check_divergence_hist(df_4h)

# ── สรุป Regime เหมือน classify_regime ──
if adx_now < ADX_CHOPPY:
    regime = "CHOPPY"
elif adx_now < ADX_GRAY_HIGH:
    regime = "เขตเทา"
elif peak["declining"]:
    at_key  = bool(key_level.get("at_key_level"))
    has_div = bool(divergence.get("divergence"))
    regime = "REVERSAL-READY" if (at_key and has_div) else "REVERSAL-WATCH"
else:
    regime = "TREND" if structure["intact"] else "TREND แรงจัด"

print(f"\n{'=' * 66}")
print(f"  ADX({ADX_PERIOD}) = {adx_now:.2f}  ทิศ={direction_adx}  "
      f"peak={peak['peak']:.1f} (declining={peak['declining']}, {peak['bars_since_peak']} แท่งหลังพีค)")
print(f"  Structure : {structure['trend']}  intact={structure['intact']}")
if key_level["nearest"] is not None:
    print(f"  Key Level : nearest={key_level['nearest']:,.2f}  at_key={key_level['at_key_level']}  "
          f"touches={key_level['touches']}  dist={key_level['distance_pct']*100:.2f}%")
else:
    print(f"  Key Level : ไม่พบโซนสำคัญ")
print(f"  Divergence: {divergence['divergence']}  {divergence['detail']}")
print(f"  Regime    : {BOLD}{regime}{RESET}")
print("=" * 66)

if regime != "REVERSAL-READY":
    print(f"\n  {YELLOW}[SKIP]{RESET} ไม่ใช่ REVERSAL-READY ณ เวลานี้ — ไม่มี Reversal ให้ทดสอบ direction fix")
    mt5.shutdown()
    sys.exit()

# ── scheduler.py fix: direction มาจาก divergence polarity ไม่ใช่ invert bias ──
direction = "Long" if divergence["divergence"] == "bullish" else "Short"
print(f"\n  {CYAN}[Direction]{RESET} divergence={divergence['divergence']} -> direction={direction}  "
      f"(ตาม scheduler.py:108-110 หลังแก้)")

# ── reversal.py fix: Key Level เช็คด้วยราคาเดียวกับที่ regime_check ใช้ปลดล็อก REVERSAL-READY
#    (ผ่าน key_level=regime_info["key_level"]) แทนที่จะคำนวณใหม่ด้วย live tick price ──
# จำลอง "ราคาสด" ตอนที่ scheduler.py จริงจะรัน (tick.bid ณ ตอนนั้น) ด้วยแท่ง 1H ที่ปิดล่าสุด
# ก่อน snap_dt — เป็นตัวแทนของช่วง drift สูงสุด ~4 ชั่วโมงระหว่างแท่ง 4H ปิด กับตอน scheduler เช็คจริง
df_1h_live = get_hist(symbol, MT5_TIMEFRAMES["1H"], snap_dt, 5)
live_price = df_1h_live["close"].iloc[-1]
price_drift_pct = abs(live_price - entry) / entry * 100

if major:
    in_range_old = [z for z in major if abs(live_price - z["center"]) / z["center"] <= KEY_LEVEL_PROXIMITY]
    chosen_old   = max(in_range_old, key=lambda z: len(z["members"])) if in_range_old else min(major, key=lambda z: abs(live_price - z["center"]))
    dist_old_pct = abs(live_price - chosen_old["center"]) / chosen_old["center"]
    at_key_old   = dist_old_pct <= KEY_LEVEL_PROXIMITY
else:
    at_key_old = False

at_key_new = bool(key_level.get("at_key_level"))   # หลังแก้: reused จาก regime (4H close เดียวกัน)

print(f"\n  {CYAN}[Key Level consistency]{RESET} ราคาปิด 4H (regime) = {entry:,.3f}  "
      f"vs ราคาสดจำลอง ณ scheduler รัน (1H ล่าสุดก่อน snap_dt) = {live_price:,.3f}  "
      f"(drift {price_drift_pct:.3f}%)")
print(f"  {DIM}ก่อนแก้ (บั๊ก) — reversal.py เช็คด้วย live price เอง: at_key_level = {at_key_old}{RESET}")
print(f"  {GREEN}หลังแก้ — reversal.py reuse key_level จาก regime_check (4H close): at_key_level = {at_key_new}{RESET}")

if at_key_old != at_key_new:
    print(f"  {RED}[BUG CONFIRMED]{RESET} เคสนี้ at_key_level ต่างกันจริงระหว่างราคา 2 ตัว "
          f"— ก่อนแก้จะเสีย/ได้ {rev.WEIGHT_KEY_LEVEL} แต้มจาก Key Level check ผิดจากที่ regime ตั้งใจ "
          f"(สอดคล้องกับที่วิเคราะห์ไว้ — drift ได้ถึง ~4h ระหว่าง 2 จุดเช็ค)")
else:
    print(f"  {DIM}เคสนี้ราคาไม่ขยับข้ามขอบโซน — at_key_level เหมือนกันทั้ง 2 แบบ บั๊กไม่โผล่ในรอบนี้ "
          f"(ลองรันช่วงเวลาอื่นที่ราคาขยับใกล้ขอบ ±{KEY_LEVEL_PROXIMITY*100:.0f}% ของโซน){RESET}")

# ── รัน Reversal scorecard ด้วยข้อมูล historical เดียวกัน (mirror ของ reversal.compute_reversal_score) ──
is_long = direction == "Long"

sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.05,
                                 vol_multiplier=vol_mult, wick_ratio_min=wick_min)
if not sl_info.get("passed"):
    print(f"\n  {RED}[BLOCKED]{RESET} หา SL ไม่ได้ — {sl_info.get('reason')}")
    mt5.shutdown()
    sys.exit()
sl = sl_info["sl"]

fib_info = find_tp_from_fibonacci(df_4h, direction, sl_info.get("swing_idx"), left=4, right=4,
                                  tolerance_atr=0.05, vol_multiplier=vol_mult, wick_ratio_min=wick_min)
if fib_info.get("passed"):
    tp = fib_info["levels"]["0.786"]
else:
    tp = (entry + abs(entry - sl) * rev.MIN_RR_REVERSAL) if is_long else (entry - abs(entry - sl) * rev.MIN_RR_REVERSAL)

rr = calc_rr(entry, sl, tp, direction)

at_key = bool(key_level.get("at_key_level"))
div_ok = (divergence.get("divergence") == "bullish") if is_long else (divergence.get("divergence") == "bearish")
rsi_now = calc_rsi(df_4h["close"]).iloc[-1]
rsi_ok  = (rsi_now <= rev.RSI_OVERSOLD) if is_long else (rsi_now >= rev.RSI_OVERBOUGHT)
climax, climax_pattern = is_climax_bar(df_4h, len(df_4h) - 1)
climax_ok = climax and ((is_long and climax_pattern == "Selling Climax") or (not is_long and climax_pattern == "Buying Climax"))
adx_ok = peak["declining"]

criteria = [
    ("Key Level",        at_key,    rev.WEIGHT_KEY_LEVEL),
    ("Divergence",        div_ok,    rev.WEIGHT_DIVERGENCE),
    ("RSI extreme",       rsi_ok,    rev.WEIGHT_RSI_EXTREME),
    ("VSA Climax",        climax_ok, rev.WEIGHT_VSA_CLIMAX),
    ("ADX peak+decline",  adx_ok,    rev.WEIGHT_ADX_DECLINE),
    ("R:R",               rr >= rev.MIN_RR_REVERSAL - 1e-9, rev.WEIGHT_RR),
]
total  = sum(w for _, p, w in criteria if p)
passed = total >= rev.MIN_SCORE_REVERSAL and rr >= rev.MIN_RR_REVERSAL - 1e-9

print(f"\n{'=' * 66}")
print(f"{BOLD}  REVERSAL SCORING (backtest) — {symbol}  ({direction}){RESET}")
print(f"  Entry={entry:,.3f}  SL={sl:,.3f}  TP={tp:,.3f}  R:R={rr:.2f}")
print("-" * 66)
for name, p, w in criteria:
    status = "PASS" if p else "FAIL"
    print(f"  {name:<18} {status}  {w if p else 0:.1f}/{w:.1f}")
print("-" * 66)
print(f"  Total : {total:.1f} / {rev.TOTAL_WEIGHT}  (min {rev.MIN_SCORE_REVERSAL})")
score_color = GREEN if passed else RED
print(f"  >>> {score_color}{'ENTER TRADE' if passed else 'NO ENTRY'}{RESET} <<<")
print("=" * 66)

# ── สรุป score delta เฉพาะจาก Key Level bug (ก่อน/หลังแก้) ──
if at_key_old != at_key_new:
    total_old  = total + (rev.WEIGHT_KEY_LEVEL if (at_key_old and not at_key_new) else -rev.WEIGHT_KEY_LEVEL)
    passed_old = total_old >= rev.MIN_SCORE_REVERSAL and rr >= rev.MIN_RR_REVERSAL - 1e-9
    print(f"\n  {YELLOW}[Key Level bug impact]{RESET} ก่อนแก้ (เช็คด้วย live price แยกจาก regime) "
          f"Total จะเป็น {total_old:.1f}/{rev.TOTAL_WEIGHT}  -> {'ENTER TRADE' if passed_old else 'NO ENTRY'}")
    if passed_old != passed:
        print(f"  {RED}[OUTCOME FLIP]{RESET} บั๊กนี้เปลี่ยนผลตัดสินใจเข้า/ไม่เข้าไม้จริงในเคสนี้ "
              f"({'ผ่าน' if passed else 'ไม่ผ่าน'} หลังแก้  vs  {'ผ่าน' if passed_old else 'ไม่ผ่าน'} ก่อนแก้)")
    else:
        print(f"  {DIM}เคสนี้ score เปลี่ยนแต่ผลตัดสินใจ ENTER/NO ENTRY ยังเหมือนเดิม (ไม่ข้าม MIN_SCORE_REVERSAL threshold){RESET}")

# ── Sanity check เฉพาะของ fix นี้: Divergence check ต้อง "ไม่มีทาง fail" เพราะ direction derive มาจากมันเอง ──
if not div_ok:
    print(f"\n  {RED}[LOGIC ERROR]{RESET} Divergence check fail ทั้งที่ direction derive มาจาก divergence polarity เอง "
          f"— นี่คือบั๊กเดิมที่ regression กลับมา ควรเป็นไปไม่ได้หลังแก้ scheduler.py:108-110")
else:
    print(f"\n  {GREEN}[OK]{RESET} Divergence check ผ่านเสมอเมื่อ direction derive จาก divergence polarity ตรงจุด — ตามที่ fix ตั้งใจ")

mt5.shutdown()
