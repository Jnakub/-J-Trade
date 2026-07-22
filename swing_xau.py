"""
swing_xau.py — Swing High/Low detection สำหรับ XAUUSD (Gold)

ต่างจาก swing.py (BTC) ตรงที่:
- Volume check ใช้ median แทน mean (กัน outlier จากแท่งวันหยุด)
- lookback = 10 แท่ง (ยาวกว่า BTC ที่ใช้ 5)
- SL ใช้ Swing High/Low ที่ Volume ผ่านล่าสุด (ไม่บังคับ rejection)
- Fibonacci ใช้ 3 จุด XAB Extension
"""

import pandas as pd
from swing import calc_atr, has_rejection, is_lower_high


VOLUME_LOOKBACK   = 10
VOLUME_MULTIPLIER = 1.2


# ---------------------------------------------------------------------------
# Volume Check — median-based (กัน outlier วันหยุด)
# ---------------------------------------------------------------------------

def has_high_volume_xau(df: pd.DataFrame, idx: int) -> bool:
    if idx < VOLUME_LOOKBACK:
        return False
    window  = df["tick_volume"].iloc[idx - VOLUME_LOOKBACK: idx]
    median  = window.median()
    this_vol = df["tick_volume"].iloc[idx]
    return this_vol >= median * VOLUME_MULTIPLIER


# ---------------------------------------------------------------------------
# Swing High / Low
# ---------------------------------------------------------------------------

def find_swing_highs_xau(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[int]:
    highs = []
    for i in range(left, len(df) - right):
        window = df["high"].iloc[i - left: i + right + 1]
        peak = window.max()
        if df["high"].iloc[i] == peak and (window == peak).sum() == 1:
            if has_high_volume_xau(df, i):
                highs.append(i)
    return highs


def find_swing_lows_xau(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[int]:
    lows = []
    for i in range(left, len(df) - right):
        window = df["low"].iloc[i - left: i + right + 1]
        trough = window.min()
        if df["low"].iloc[i] == trough and (window == trough).sum() == 1:
            if has_high_volume_xau(df, i):
                lows.append(i)
    return lows


# ---------------------------------------------------------------------------
# SL — วนจากล่าสุด เลือก Swing ที่ Volume ผ่าน
# ---------------------------------------------------------------------------

def find_sl_from_structure_xau(df: pd.DataFrame, direction: str,
                                atr_buffer: float = 0.2) -> dict:
    is_short    = direction.capitalize() == "Short"
    atr         = calc_atr(df)
    swing_highs = find_swing_highs_xau(df)
    swing_lows  = find_swing_lows_xau(df)

    if is_short:
        if not swing_highs:
            return {"passed": False, "reason": "ไม่พบ Swing High ใน XAU data"}
        candidates = list(reversed(swing_highs))
    else:
        if not swing_lows:
            return {"passed": False, "reason": "ไม่พบ Swing Low ใน XAU data"}
        candidates = list(reversed(swing_lows))

    for idx in candidates:
        if is_short:
            swing_price      = df["high"].iloc[idx]
            atr_val          = atr.iloc[idx]
            rej_ok, rej_name = has_rejection(df, idx, atr)
            sl               = swing_price + atr_val * atr_buffer
            struct_ok        = is_lower_high(swing_highs, df)
        else:
            swing_price      = df["low"].iloc[idx]
            atr_val          = atr.iloc[idx]
            rej_ok, rej_name = has_rejection(df, idx, atr)
            sl               = swing_price - atr_val * atr_buffer
            struct_ok        = not is_lower_high(swing_highs, df)

        if rej_ok and struct_ok:
            return {
                "sl":           round(sl, 5),
                "swing_price":  swing_price,
                "swing_idx":    idx,
                "atr":          round(atr_val, 5),
                "rejection":    rej_name,
                "volume_ok":    True,
                "structure_ok": struct_ok,
                "passed":       True,
                "reason":       "OK",
            }

    reasons = []
    reasons.append("ไม่มี rejection ชัด" )
    reasons.append("Structure ไม่ตรง (ไม่ใช่ Lower High/Higher Low)")
    return {"passed": False, "reason": " | ".join(reasons)}


# ---------------------------------------------------------------------------
# Fibonacci Extension 3 จุด (XAB)
# Short: X=High ก่อน, A=Low ล่าสุด, B=High ล่าสุด → ฉายลงต่ำกว่า A
# Long : X=Low ก่อน,  A=High ล่าสุด, B=Low ล่าสุด  → ฉายสูงกว่า A
# ---------------------------------------------------------------------------

def find_tp_from_fibonacci_xau(df: pd.DataFrame, direction: str) -> dict:
    is_short    = direction.capitalize() == "Short"
    swing_highs = find_swing_highs_xau(df)
    swing_lows  = find_swing_lows_xau(df)

    if not swing_highs or not swing_lows:
        return {"passed": False, "reason": "ไม่พบ Swing High/Low เพียงพอ"}

    if is_short:
        # B = Swing High ล่าสุด
        b_idx   = swing_highs[-1]
        b_price = df["high"].iloc[b_idx]

        # A = Swing Low ล่าสุดก่อน B
        lows_before = [i for i in swing_lows if i < b_idx]
        if not lows_before:
            return {"passed": False, "reason": "ไม่พบ Swing Low ก่อน Swing High"}
        a_idx   = lows_before[-1]
        a_price = df["low"].iloc[a_idx]

        # X = Swing High ก่อน A
        highs_before_a = [i for i in swing_highs if i < a_idx]
        if not highs_before_a:
            return {"passed": False, "reason": "ไม่พบ Swing High ก่อน Swing Low"}
        x_idx   = highs_before_a[-1]
        x_price = df["high"].iloc[x_idx]

        move   = x_price - a_price
        origin = b_price
        sign   = -1

    else:
        # B = Swing Low ล่าสุด
        b_idx   = swing_lows[-1]
        b_price = df["low"].iloc[b_idx]

        # A = Swing High ล่าสุดก่อน B
        highs_before = [i for i in swing_highs if i < b_idx]
        if not highs_before:
            return {"passed": False, "reason": "ไม่พบ Swing High ก่อน Swing Low"}
        a_idx   = highs_before[-1]
        a_price = df["high"].iloc[a_idx]

        # X = Swing Low ก่อน A
        lows_before_a = [i for i in swing_lows if i < a_idx]
        if not lows_before_a:
            return {"passed": False, "reason": "ไม่พบ Swing Low ก่อน Swing High"}
        x_idx   = lows_before_a[-1]
        x_price = df["low"].iloc[x_idx]

        move   = a_price - x_price
        origin = b_price
        sign   = 1

    if move <= 0:
        return {"passed": False, "reason": "Move ต้องมากกว่า 0"}

    levels = {}
    for ratio in [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618]:
        levels[str(ratio)] = round(origin + sign * move * ratio, 5)

    return {
        "passed":     True,
        "origin":     origin,
        "move":       round(move, 5),
        "tp1":        levels["0.382"],
        "tp2":        levels["0.618"],
        "tp3":        levels["1.0"],
        "tp4":        levels["1.618"],
        "swing_high": b_price if is_short else a_price,
        "swing_low":  a_price if is_short else b_price,
        "x_price":    x_price,
        "levels":     levels,
    }
