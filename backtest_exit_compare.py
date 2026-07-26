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
from config import (
    MT5_TIMEFRAMES, MIN_SCORE, MIN_RR, MIN_RR_HARD_BLOCK,
    WEIGHT_TREND_1D, WEIGHT_OBV_1D, WEIGHT_TREND_4H, WEIGHT_OBV_4H,
    WEIGHT_TREND_1H, WEIGHT_OBV_1H, WEIGHT_VSA, WEIGHT_MACD,
    WEIGHT_RR,
)
from scoring import ema, calc_obv, calc_macd, calc_rr, macd_ok_for_direction
from swing import (
    find_sl_from_structure, find_tp_from_fibonacci, check_confirmation, calc_atr,
    find_swing_lows, find_swing_highs,
)
from vsa import check_vsa
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
ATR_PERIOD      = 22       # ตามมาตรฐาน Chandelier Exit ดั้งเดิม (Chuck LeBeau)
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
    """ทำซ้ำ logic เดียวกับ scoring.compute_score() แต่ใช้ข้อมูลย้อนหลัง ณ snap_dt"""
    df_1d = get_hist(symbol, MT5_TIMEFRAMES["1D"], snap_dt, 210)
    df_4h = get_hist(symbol, MT5_TIMEFRAMES["4H"], snap_dt, 210)
    df_1h = get_hist(symbol, MT5_TIMEFRAMES["1H"], snap_dt, 110)
    if df_1d is None or df_4h is None or df_1h is None:
        return None
    if len(df_1d) < 205 or len(df_4h) < 60 or len(df_1h) < 60:
        return None

    df_1d = merge_real_volume(df_1d, symbol, "1D")
    df_4h = merge_real_volume(df_4h, symbol, "4H")

    entry = df_1h["close"].iloc[-1]

    ema50_1d, ema200_1d = ema(df_1d["close"], 50).iloc[-1], ema(df_1d["close"], 200).iloc[-1]
    direction = "Long" if ema50_1d > ema200_1d else "Short"
    is_long   = direction == "Long"

    sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.22)
    if not sl_info.get("passed"):
        return None
    sl        = sl_info["sl"]
    swing_idx = sl_info["swing_idx"]
    fib       = find_tp_from_fibonacci(df_4h, direction, swing_idx, left=4, right=4, tolerance_atr=0.22)
    tp        = fib["levels"]["0.786"] if fib.get("passed") else (   # ตรงกับ scoring.py/reversal.py ปัจจุบัน (2026-07-23)
        entry + abs(entry - sl) * MIN_RR if is_long else entry - abs(entry - sl) * MIN_RR
    )

    rr = calc_rr(entry, sl, tp, direction)
    if rr < MIN_RR_HARD_BLOCK - 1e-9:
        return None

    ema50_4h = ema(df_4h["close"], 50).iloc[-1]
    ema50_1h = ema(df_1h["close"], 50).iloc[-1]
    obv_1d, obv_4h, obv_1h = calc_obv(df_1d), calc_obv(df_4h), calc_obv(df_1h)
    macd_line, signal_line, macd_hist = calc_macd(df_4h)

    def obv_rising(obv, lookback):
        if len(obv) <= lookback:
            return False
        return (obv.iloc[-1] > obv.iloc[-1 - lookback]) if is_long else (obv.iloc[-1] < obv.iloc[-1 - lookback])

    vsa_result  = check_vsa(df_1d, df_4h, direction)

    criteria = [
        (WEIGHT_TREND_1D,     (entry > ema50_1d) if is_long else (entry < ema50_1d)),
        (WEIGHT_OBV_1D,       obv_rising(obv_1d, 10)),
        (WEIGHT_TREND_4H,     (entry > ema50_4h) if is_long else (entry < ema50_4h)),
        (WEIGHT_OBV_4H,       obv_rising(obv_4h, 10)),
        (WEIGHT_TREND_1H,     (entry > ema50_1h) if is_long else (entry < ema50_1h)),
        (WEIGHT_OBV_1H,       obv_rising(obv_1h, 50)),
        (WEIGHT_MACD,         macd_ok_for_direction(macd_line, signal_line, macd_hist, direction)),
        (WEIGHT_RR,           rr >= MIN_RR - 1e-9),
        (WEIGHT_VSA,          vsa_result["vsa_ok"]),
        # Confirmation ถูกตัดออกจาก scorecard 2026-07-26 (ดู config.py)
    ]
    total = sum(w for w, p in criteria if p)
    if total < MIN_SCORE:
        return None

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


# ---------------------------------------------------------------------------
# ADX(14) — ใช้แค่ในไฟล์ backtest นี้เท่านั้น (ไม่แตะ swing.py/production)
# ---------------------------------------------------------------------------

def calc_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    up_move   = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm   = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm  = ((down_move > up_move) & (down_move > 0)) * down_move

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr      = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-12)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-12)
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


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
