"""
backtest_exit_compare.py — เทียบ P&L: Fixed SL/TP (เดิม) vs Chandelier Exit (ATR trailing)
บน BTCUSDm ย้อนหลัง 1 เดือน โดยใช้ entry logic เดียวกับ scheduler.py (compute_score)

ใช้: python backtest_exit_compare.py
"""
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from mt5_connect import connect
from config import MT5_TIMEFRAMES
from scoring import compute_score, get_trend_bias, calc_rr
from swing import calc_atr, find_swing_lows, find_swing_highs
from indicators import calc_adx
from binance import merge_real_volume

SYMBOL          = "BTCUSDm"
LOOKBACK_DAYS   = 30
SCAN_STEP_H     = 4        # สแกนหา entry ทุก 4 ชม. (ลดจำนวน MT5 calls)
ATR_MULTIPLIER  = 3.0
ATR_MULT_WIDE   = 4.5      # แบบ 1: multiplier กว้างขึ้น
STRUCT_LR       = 5        # left/right สำหรับ swing structure trail
ADX_PERIOD      = 14
ADX_TREND_TH    = 25       # ADX >= นี้ = trending
ADX_CHOPPY_TH   = 20       # ADX <  นี้ = choppy
ATR_PERIOD      = 14       # 2026-08-17: เปลี่ยนจาก 22 -> 14 ตามคำสั่งผู้ใช้ (unify ทั้งระบบ)
                          # เดิมอ้างอิง Chandelier Exit ดั้งเดิมของ Chuck LeBeau (22 ~ 1 เดือนเทรด)
                          # แต่นั่นออกแบบมาสำหรับแท่งรายวัน สคริปต์นี้รันบน SIM_TIMEFRAME="4H" —
                          # เอา 22 มาใช้ตรงๆ กับ 4H ก็ไม่ได้แปลว่า "1 เดือน" อยู่ดี (22x4H=88ชม.
                          # ~3.7 วัน) ความหมายเดิมของเลขนี้เสียไปตั้งแต่ย้ายไทม์เฟรมแล้ว ไม่มีทั้ง
                          # 14/22 ตัวไหนมี backtest ยืนยันโดยเฉพาะสำหรับ TF นี้จริงๆ
SIM_TIMEFRAME   = "4H"     # กริดที่ใช้เดินจำลอง exit — ให้ตรงกับ TF ที่ใช้หา SL/Structure ตอนเข้า
COOLDOWN_BARS   = 6        # เข้าได้อีกทีหลังไม้ก่อนปิดแล้ว (กันเข้าซ้อน) — 6 แท่ง 4H = 1 วัน


def get_hist(symbol, tf, dt, bars):
    rates = mt5.copy_rates_from(symbol, tf, dt, bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def score_entry(symbol, snap_dt):
    """เรียก scoring.compute_score(as_of=snap_dt) ตรงๆ แทนการ reimplement logic ของมัน
    (เดิมไฟล์นี้ copy compute_score มาทั้งดุ้นเหมือน backtest_score.py ก่อนแก้ — ดู
    scoring.py.compute_score docstring) ต้องหา bias เองก่อนเพื่อรู้ว่าจะส่ง direction
    ไหนให้ compute_score (มันเองก็ตรวจ bias ซ้ำภายในอีกที กันเพี้ยน)"""
    df_1d = get_hist(symbol, MT5_TIMEFRAMES["1D"], snap_dt, 800)   # 800 บาร์ให้ trend_flip มีประวัติพอ
    if df_1d is None or len(df_1d) < 205:
        return None
    df_1d = merge_real_volume(df_1d, symbol, "1D")
    direction, _ = get_trend_bias(symbol, df_1d)
    if direction is None:
        return None   # trend_flip ไม่มี k หรือ bootstrap ยังไม่พร้อม — ข้ามจุดนี้

    df_1h_snap = get_hist(symbol, MT5_TIMEFRAMES["1H"], snap_dt, 2)
    if df_1h_snap is None or df_1h_snap.empty:
        return None
    entry = df_1h_snap["close"].iloc[-1]

    try:
        total, criteria, passed, sl_info = compute_score(symbol, direction, entry, as_of=snap_dt)
    except ValueError:
        return None
    if not passed:
        return None

    sl, tp = sl_info["sl"], sl_info["tp"]
    rr = calc_rr(entry, sl, tp, direction)

    return {"direction": direction, "entry": entry, "sl": sl, "tp": tp, "rr": rr,
            "score": total, "time": snap_dt}


# ---------------------------------------------------------------------------
# Simulate exit เดินหน้าทีละแท่ง 1H จาก dataframe ใหญ่ที่ fetch มาครั้งเดียว
# ---------------------------------------------------------------------------

def simulate_fixed(df: pd.DataFrame, start_idx: int, direction: str,
                    entry: float, sl: float, tp: float) -> dict:
    risk = abs(entry - sl)
    for i in range(start_idx, len(df)):
        bar = df.iloc[i]
        if direction == "Long":
            hit_sl = bar["low"]  <= sl
            hit_tp = bar["high"] >= tp
        else:
            hit_sl = bar["high"] >= sl
            hit_tp = bar["low"]  <= tp
        if hit_sl:
            r = -1.0
            return {"result": "SL", "r_multiple": r, "bars_held": i - start_idx}
        if hit_tp:
            r = (tp - entry) / risk if direction == "Long" else (entry - tp) / risk
            return {"result": "TP", "r_multiple": r, "bars_held": i - start_idx}
    last_close = df["close"].iloc[-1]
    r = (last_close - entry) / risk if direction == "Long" else (entry - last_close) / risk
    return {"result": "Open", "r_multiple": r, "bars_held": len(df) - start_idx}


def simulate_chandelier(df: pd.DataFrame, atr_series: pd.Series, start_idx: int,
                        direction: str, entry: float, sl: float, tp: float,
                        mult: float = ATR_MULTIPLIER) -> dict:
    risk    = abs(entry - sl)
    extreme = entry
    trail   = sl
    for i in range(start_idx, len(df)):
        bar = df.iloc[i]
        atr = atr_series.iloc[i]
        if pd.isna(atr):
            atr = 0.0

        if direction == "Long":
            extreme = max(extreme, bar["high"])
            chandelier = extreme - atr * mult
            trail      = max(trail, chandelier)     # ไม่มีวันถอยหลัง
            hit_sl = bar["low"]  <= trail
            hit_tp = bar["high"] >= tp
        else:
            extreme = min(extreme, bar["low"])
            chandelier = extreme + atr * mult
            trail      = min(trail, chandelier)
            hit_sl = bar["high"] >= trail
            hit_tp = bar["low"]  <= tp

        if hit_sl:
            r = (trail - entry) / risk if direction == "Long" else (entry - trail) / risk
            return {"result": "Chandelier SL", "r_multiple": r, "bars_held": i - start_idx}
        if hit_tp:
            r = (tp - entry) / risk if direction == "Long" else (entry - tp) / risk
            return {"result": "TP", "r_multiple": r, "bars_held": i - start_idx}

    last_close = df["close"].iloc[-1]
    r = (last_close - entry) / risk if direction == "Long" else (entry - last_close) / risk
    return {"result": "Open", "r_multiple": r, "bars_held": len(df) - start_idx}


# calc_adx(period=ADX_PERIOD) มาจาก indicators.py แล้ว (2026-08-01) — เดิมเป็นสำเนาที่ 3
# ของสูตรเดียวกัน (ซ้ำกับ swing.calc_di/regime_check.calc_adx เดิม)


# ---------------------------------------------------------------------------
# Trail series — คำนวณ stop level ที่แต่ละแท่ง (เดินหน้าครั้งเดียว เก็บเป็น dict)
# ---------------------------------------------------------------------------

def chandelier_trail_series(df, atr_series, start_idx, direction, entry, sl, mult):
    trail = {}
    extreme = entry
    t = sl
    for i in range(start_idx, len(df)):
        bar = df.iloc[i]
        atr = atr_series.iloc[i]
        if pd.isna(atr):
            atr = 0.0
        if direction == "Long":
            extreme = max(extreme, bar["high"])
            t = max(t, extreme - atr * mult)
        else:
            extreme = min(extreme, bar["low"])
            t = min(t, extreme + atr * mult)
        trail[i] = t
    return trail


def structure_trail_series(df, swing_lows_idx, swing_highs_idx, start_idx, direction, entry, sl,
                           right=STRUCT_LR):
    """Long: trail ตาม swing low ล่าสุดที่ยืนยันแล้ว (idx+right <= แท่งปัจจุบัน) แทน High-ATR"""
    swing_idx = swing_lows_idx if direction == "Long" else swing_highs_idx
    trail = {}
    t = sl
    for i in range(start_idx, len(df)):
        confirmed = [idx for idx in swing_idx if idx + right <= i]
        if confirmed:
            latest = max(confirmed)
            level  = df["low"].iloc[latest] if direction == "Long" else df["high"].iloc[latest]
            t = max(t, level) if direction == "Long" else min(t, level)
        trail[i] = t
    return trail


def regime_switch_trail_series(df, chand_trail, struct_trail, adx_series, start_idx):
    """ADX>=25 = trending -> ใช้ structure trail | ADX<20 = choppy -> ใช้ Chandelier | 20-25 = คงสถานะเดิม (hysteresis)"""
    trail = {}
    regime = "chandelier"   # เริ่มต้นแบบระมัดระวังไว้ก่อน
    for i in range(start_idx, len(df)):
        adx = adx_series.iloc[i]
        if not pd.isna(adx):
            if adx >= ADX_TREND_TH:
                regime = "structure"
            elif adx < ADX_CHOPPY_TH:
                regime = "chandelier"
        trail[i] = struct_trail[i] if regime == "structure" else chand_trail[i]
    return trail


def regime_switch_simple_trail_series(df, chand_trail, struct_trail, adx_series, start_idx):
    """ตามภาพที่เสนอ — เส้นเดียว ไม่มี hysteresis: ADX>=25 structure, ADX<25 Chandelier ทันที"""
    trail = {}
    for i in range(start_idx, len(df)):
        adx = adx_series.iloc[i]
        use_structure = (not pd.isna(adx)) and adx >= ADX_TREND_TH
        trail[i] = struct_trail[i] if use_structure else chand_trail[i]
    return trail


def widest_stop_trail_series(df, chand_trail, struct_trail, direction, start_idx):
    """ใช้ stop ที่ห่างจากราคามากกว่า (หลวมกว่า) เป็นตัวจริงเสมอ — ไม่ตัดสั้น"""
    trail = {}
    for i in range(start_idx, len(df)):
        if direction == "Long":
            trail[i] = min(chand_trail[i], struct_trail[i])   # ยิ่งต่ำ ยิ่งห่างราคา = หลวมกว่า
        else:
            trail[i] = max(chand_trail[i], struct_trail[i])   # ยิ่งสูง ยิ่งห่างราคา = หลวมกว่า
    return trail


def walk_with_trail(df, start_idx, direction, entry, sl, tp, trail, label):
    risk = abs(entry - sl)
    for i in range(start_idx, len(df)):
        bar = df.iloc[i]
        t   = trail[i]
        if direction == "Long":
            hit_sl = bar["low"]  <= t
            hit_tp = bar["high"] >= tp
        else:
            hit_sl = bar["high"] >= t
            hit_tp = bar["low"]  <= tp
        if hit_sl:
            r = (t - entry) / risk if direction == "Long" else (entry - t) / risk
            return {"result": f"{label} SL", "r_multiple": r, "bars_held": i - start_idx}
        if hit_tp:
            r = (tp - entry) / risk if direction == "Long" else (entry - tp) / risk
            return {"result": "TP", "r_multiple": r, "bars_held": i - start_idx}
    last_close = df["close"].iloc[-1]
    r = (last_close - entry) / risk if direction == "Long" else (entry - last_close) / risk
    return {"result": "Open", "r_multiple": r, "bars_held": len(df) - start_idx}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()
    connect()

    now = datetime.now()
    window_start = now - timedelta(days=LOOKBACK_DAYS)

    print(f"Backtest {SYMBOL}  ย้อนหลัง {LOOKBACK_DAYS} วัน  ({window_start} -> {now})")
    print(f"กำลังดึงข้อมูล {SIM_TIMEFRAME} สำหรับจำลอง exit... (ATR period={ATR_PERIOD})")

    big_1h = get_hist(SYMBOL, MT5_TIMEFRAMES[SIM_TIMEFRAME], now, (LOOKBACK_DAYS + 10) * 6 + 60)
    if big_1h is None:
        print(f"[ERROR] ดึงข้อมูล {SIM_TIMEFRAME} ไม่ได้"); mt5.shutdown(); return

    atr_series  = calc_atr(big_1h, ATR_PERIOD)
    adx_series  = calc_adx(big_1h, ADX_PERIOD)
    swing_lows  = find_swing_lows(big_1h,  left=STRUCT_LR, right=STRUCT_LR, tolerance_atr=0.22)
    swing_highs = find_swing_highs(big_1h, left=STRUCT_LR, right=STRUCT_LR, tolerance_atr=0.22)

    trades = []
    last_entry_bar_idx = -10_000
    snap_dt = window_start
    step_count = 0
    while snap_dt <= now - timedelta(hours=1):
        step_count += 1
        sig = score_entry(SYMBOL, snap_dt)
        if sig:
            idx_arr = big_1h.index[big_1h["time"] <= snap_dt]
            if len(idx_arr) == 0:
                snap_dt += timedelta(hours=SCAN_STEP_H)
                continue
            start_idx = idx_arr[-1]

            if start_idx - last_entry_bar_idx >= COOLDOWN_BARS:
                d, entry, sl, tp = sig["direction"], sig["entry"], sig["sl"], sig["tp"]
                s = start_idx + 1

                fixed = simulate_fixed(big_1h, s, d, entry, sl, tp)

                chand_t3   = chandelier_trail_series(big_1h, atr_series, s, d, entry, sl, ATR_MULTIPLIER)
                chand      = walk_with_trail(big_1h, s, d, entry, sl, tp, chand_t3, "Chandelier")

                chand_t45  = chandelier_trail_series(big_1h, atr_series, s, d, entry, sl, ATR_MULT_WIDE)
                mult45     = walk_with_trail(big_1h, s, d, entry, sl, tp, chand_t45, "Chandelier x4.5")

                struct_t   = structure_trail_series(big_1h, swing_lows, swing_highs, s, d, entry, sl)
                struct     = walk_with_trail(big_1h, s, d, entry, sl, tp, struct_t, "Structure")

                regime_t   = regime_switch_trail_series(big_1h, chand_t3, struct_t, adx_series, s)
                regime     = walk_with_trail(big_1h, s, d, entry, sl, tp, regime_t, "Regime")

                regime2_t  = regime_switch_simple_trail_series(big_1h, chand_t3, struct_t, adx_series, s)
                regime2    = walk_with_trail(big_1h, s, d, entry, sl, tp, regime2_t, "Regime-simple")

                widest_t   = widest_stop_trail_series(big_1h, chand_t3, struct_t, d, s)
                widest     = walk_with_trail(big_1h, s, d, entry, sl, tp, widest_t, "Widest")

                trades.append({**sig, "fixed": fixed, "chandelier": chand, "mult45": mult45,
                               "structure": struct, "regime": regime, "regime2": regime2, "widest": widest})
                last_entry_bar_idx = start_idx
                print(f"  [{snap_dt}] {sig['direction']:<5}  Score={sig['score']:.1f}  "
                      f"Fixed={fixed['r_multiple']:+.2f}  Chand3={chand['r_multiple']:+.2f}  "
                      f"Chand4.5={mult45['r_multiple']:+.2f}  Struct={struct['r_multiple']:+.2f}  "
                      f"Regime={regime['r_multiple']:+.2f}  Regime2={regime2['r_multiple']:+.2f}  "
                      f"Widest={widest['r_multiple']:+.2f}")

        snap_dt += timedelta(hours=SCAN_STEP_H)

    mt5.shutdown()

    print()
    print("=" * 70)
    print(f"  ทดสอบ {step_count} จุดเวลา  พบ entry ที่ผ่าน score {len(trades)} ไม้")
    print("=" * 70)

    if not trades:
        print("ไม่มี entry ที่ผ่าน score ในช่วงนี้ — ลองขยาย LOOKBACK_DAYS หรือลด MIN_SCORE")
        return

    variants = [
        ("Fixed SL/TP (ตัวหลัก — ห้ามแก้)", "fixed"),
        ("1. Chandelier x3 (เดิม)",          "chandelier"),
        ("1. Chandelier x4.5 (กว้างขึ้น)",   "mult45"),
        ("2. Structure Trail",              "structure"),
        ("3. Regime Switch (hysteresis)",   "regime"),
        ("3b. Regime Switch (เส้นเดียว ADX>=25)", "regime2"),
        ("4. Widest-Stop-Wins",             "widest"),
    ]

    def summarize(label, rs, results):
        total    = sum(rs)
        avg      = total / len(rs)
        win_rate = len([r for r in rs if r > 0]) / len(rs) * 100
        print(f"  {label}")
        print(f"    Total R       : {total:+.2f}R")
        print(f"    Avg R/เทรด    : {avg:+.2f}R")
        print(f"    Win Rate      : {win_rate:.1f}%")
        print(f"    ผลลัพธ์       : {', '.join(r['result'] for r in results)}")
        print()
        return total

    totals = {}
    for label, key in variants:
        rs = [t[key]["r_multiple"] for t in trades]
        results = [t[key] for t in trades]
        totals[key] = summarize(label, rs, results)

    baseline = totals["fixed"]
    print("=" * 70)
    print(f"  เทียบกับ Fixed SL/TP (ตัวหลัก) — Total R = {baseline:+.2f}R")
    print("-" * 70)
    for label, key in variants[1:]:
        diff = totals[key] - baseline
        tag  = "ดีกว่า" if diff > 0 else ("แย่กว่า" if diff < 0 else "เท่ากัน")
        print(f"  {label:<32} ส่วนต่าง {diff:+.2f}R  ({tag})")
    print("=" * 70)


if __name__ == "__main__":
    main()
