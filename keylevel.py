"""
keylevel.py — Key Level นิยามใหม่ (2026-10-20 · ผู้ใช้กำหนด · ยังไม่มีกลยุทธ์ไหนใช้ในระบบจริง)

ทำไมต้องนิยามใหม่: regime_check.check_key_level (นิยามเดิม · 400 แท่ง 4H · โซน ±1% · แตะ ≥ 2) ผ่านเกือบตลอด
— ไม้ Pullback 91% · Scoring 75-77% · Breakout 75-82% อยู่ "ที่ Key Level" = แทบไม่คัดอะไร
  เพราะ ±1% กว้างเกินสำหรับ FX (EURUSD 1% ≈ 110 pips > ช่วงทั้งวัน) · 400 แท่งมี swing เป็นสิบจุดกระจายทั่วช่วงราคา

นิยามใหม่ (ค่าตั้งไว้ก่อนดูผล · ผู้ใช้เลือก):
  - แท่ง 4H ที่ปิดแล้ว ย้อนหลัง KL_BARS (120 ≈ 20 วัน)
  - แนวที่นับ (ขึ้นกับทิศไม้):
      Long  -> swing low ล่าสุด (แนวรับที่ขาขึ้นเพิ่งสร้าง) · Short -> swing high ล่าสุด
      + โซนที่ swing (ยอด/ก้นปนกัน) จับกลุ่มกันภายใน KL_ATR × ATR4H แล้วมี ≥ KL_MIN_TOUCHES จุด
  - "อยู่ที่ Key Level" = ราคาห่างจากแนวใดแนวหนึ่งไม่เกิน KL_ATR × ATR4H(14)
  - swing = find_swing_highs/lows ค่าเดียวกับนิยามเดิม (ซ้าย/ขวา SWING_LEFT_RIGHT · ตัวกรอง vol/ไส้ราย symbol)
"""
import numpy as np
import pandas as pd

KL_BARS = 120
KL_ATR = 0.5
KL_MIN_TOUCHES = 2


def key_level(df_4h: pd.DataFrame, symbol: str, price: float, direction: str) -> dict:
    """df_4h: แท่ง 4H ที่ "รวมแท่งที่ยังไม่ปิด" ไว้ท้ายสุด (รูปเดียวกับ get_regime()["df_4h"]) — ตัดแท่งท้ายเอง
    คืน {"at_key_level", "level", "kind" ("swing ล่าสุด"/"โซน N จุด"), "dist_atr", "atr"}"""
    import regime_check as rc                     # import ช้า: regime_check ลาก MetaTrader5 มาด้วย
    from swing import find_swing_highs, find_swing_lows, swing_vol_multiplier, swing_wick_ratio_min
    from indicators import calc_atr
    df = df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True).iloc[-KL_BARS:].reset_index(drop=True)
    none = {"at_key_level": False, "level": None, "kind": None, "dist_atr": None, "atr": None}
    if len(df) < 30:
        return none
    atr = float(calc_atr(df, 14).iloc[-1])
    if not atr > 0:
        return none
    vm, wr = swing_vol_multiplier(symbol), swing_wick_ratio_min(symbol)
    highs = find_swing_highs(df, left=rc.SWING_LEFT_RIGHT, right=rc.SWING_LEFT_RIGHT,
                             tolerance_atr=rc.SWING_TOLERANCE, vol_multiplier=vm, wick_ratio_min=wr)
    lows = find_swing_lows(df, left=rc.SWING_LEFT_RIGHT, right=rc.SWING_LEFT_RIGHT,
                           tolerance_atr=rc.SWING_TOLERANCE, vol_multiplier=vm, wick_ratio_min=wr)
    levels = []
    own = lows if direction == "Long" else highs
    if own:
        p = float(df["low"].iloc[own[-1]] if direction == "Long" else df["high"].iloc[own[-1]])
        levels.append((p, "swing ล่าสุด"))
    pts = sorted([float(df["high"].iloc[i]) for i in highs] + [float(df["low"].iloc[i]) for i in lows])
    tol = KL_ATR * atr
    cluster = []
    for p in pts + [None]:                           # จับกลุ่มเรียงตามราคา · ห่างจากจุดก่อนหน้าไม่เกิน tol = กลุ่มเดียวกัน
        if p is not None and cluster and p - cluster[-1] <= tol:
            cluster.append(p)
            continue
        if len(cluster) >= KL_MIN_TOUCHES:
            levels.append((float(np.mean(cluster)), f"โซน {len(cluster)} จุด"))
        cluster = [p] if p is not None else []
    if not levels:
        return {**none, "atr": atr}
    lvl, kind = min(levels, key=lambda x: abs(price - x[0]))
    d = abs(price - lvl) / atr
    return {"at_key_level": d <= KL_ATR, "level": lvl, "kind": kind, "dist_atr": d, "atr": atr}
