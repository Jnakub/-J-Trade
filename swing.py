import pandas as pd

from indicators import calc_atr, calc_di   # re-exported เพื่อไม่ให้ต้องแก้ import ที่อื่น


# ---------------------------------------------------------------------------
# ข้อ 1 — Rejection Check
# ---------------------------------------------------------------------------

def has_rejection(df: pd.DataFrame, idx: int, atr: pd.Series,
                  wick_body_ratio: float = 1.5,
                  wick_atr_ratio:  float = 0.3) -> tuple[bool, str]:
    """
    เช็คว่าแท่งที่ idx มี rejection ชัดไหม

    Pattern A (Single Candle):
        upper_wick >= body × wick_body_ratio
        upper_wick >= ATR  × wick_atr_ratio

    Pattern B (Bearish Engulfing — 2 แท่ง):
        แท่ง idx   ปิดบวก
        แท่ง idx+1 ปิดลบ และ body กิน body แท่ง idx ทั้งหมด

    คืน (True/False, ชื่อ pattern)
    """
    if idx < 0 or idx >= len(df):
        return False, "out of range"

    c = df.iloc[idx]
    body        = abs(c["close"] - c["open"])
    upper_wick  = c["high"] - max(c["close"], c["open"])
    atr_val     = atr.iloc[idx]

    # Pattern A
    body_ok = body > 0 and upper_wick >= body * wick_body_ratio
    atr_ok  = upper_wick >= atr_val * wick_atr_ratio
    if body_ok and atr_ok:
        return True, "PatternA-Wick"

    # Pattern B — ต้องมีแท่งถัดไป
    if idx + 1 < len(df):
        n = df.iloc[idx + 1]
        curr_bullish  = c["close"] > c["open"]
        next_bearish  = n["close"] < n["open"]
        engulf        = n["open"] >= max(c["open"], c["close"]) and \
                        n["close"] <= min(c["open"], c["close"])
        if curr_bullish and next_bearish and engulf:
            return True, "PatternB-Engulfing"

    return False, "no rejection"


# ---------------------------------------------------------------------------
# ข้อ 2 — Volume Check
# ---------------------------------------------------------------------------

def swing_vol_multiplier(symbol: str) -> float:
    """เกณฑ์ volume ของ swing ตาม symbol — จุดเดียวทั้งระบบ ห้าม hardcode ซ้ำที่อื่น
    XAU/Gold 1.5x (volume ทองแรงน้อยกว่า — ปรับจาก 1.6 เมื่อ 2026-07-24 ยังไม่มี backtest
    ยืนยันค่าใหม่นี้โดยตรง), อื่นๆ 1.9x"""
    return 1.5 if "XAU" in symbol.upper() or "GOLD" in symbol.upper() else 1.9


def swing_wick_ratio_min(symbol: str) -> float | None:
    """เกณฑ์ wick ratio ขั้นต่ำสำหรับ OR-logic ของ swing (2026-07-21) — ใช้เฉพาะ XAU/Gold
    เท่านั้น (คืน None สำหรับ symbol อื่นทุกตัว = ปิด OR-logic, พฤติกรรมเดิมเป๊ะ ไม่แตะ BTC)

    เหตุผล: XAUUSDm ไม่มี real volume ให้ merge (COMEX ผ่าน yfinance พังที่ 4H — ดู binance.py)
    เลยใช้ tick_volume ของโบรกเกอร์เป็น proxy ซึ่งเชื่อถือได้น้อยกว่า BTC ที่มี real volume
    จาก Bitstamp — wick ratio (สัดส่วนไส้เทียนต่อ range ทั้งแท่ง) เป็นสัญญาณอิสระจาก volume
    ที่ช่วยกู้ swing point จริงที่ tick_volume มองไม่เห็น (ยืนยันด้วย backtest 400 แท่ง:
    vol=1.6x เท่านั้น เจอ lows แค่ 9 จุด, เพิ่ม wick>=0.4 OR เข้าไปเจอเพิ่มเป็น 19 จุด)
    2026-07-24: ปรับเป็น 0.5 — เข้มขึ้นจาก 0.4 เดิม ยังไม่มี backtest ยืนยันค่าใหม่นี้โดยตรง"""
    return 0.5 if "XAU" in symbol.upper() or "GOLD" in symbol.upper() else None


def has_high_volume(df: pd.DataFrame, idx: int,
                    lookback: int = 5,
                    multiplier: float = 1.9) -> bool:
    """
    Volume ที่ idx สูงกว่าค่าเฉลี่ย lookback แท่ง (รวมตัวเอง) × multiplier
    ตรงกับ TradingView Volume MA Length=5
    """
    if idx < lookback - 1:
        return False
    avg_vol  = df["tick_volume"].iloc[idx - lookback + 1: idx + 1].mean()
    this_vol = df["tick_volume"].iloc[idx]
    return this_vol >= avg_vol * multiplier


def _wick_ratio(df: pd.DataFrame, idx: int, is_high: bool) -> float:
    """สัดส่วนไส้เทียน (upper wick สำหรับ swing high, lower wick สำหรับ swing low)
    ต่อ range ทั้งแท่ง (high-low) — ยิ่งสูงยิ่งแปลว่าราคาถูก 'ปฏิเสธ' ที่จุดนั้นแรง
    ไม่ผูกกับ volume เลย ใช้เป็นสัญญาณอิสระคู่กับ volume แบบ OR-logic"""
    row = df.iloc[idx]
    rng = row["high"] - row["low"]
    if rng <= 0:
        return 0.0
    if is_high:
        return (row["high"] - max(row["close"], row["open"])) / rng
    return (min(row["close"], row["open"]) - row["low"]) / rng


# ---------------------------------------------------------------------------
# ข้อ 3 — Structure Check (Lower High Series)
# ---------------------------------------------------------------------------

def find_swing_highs(df: pd.DataFrame, left: int = 3, right: int = 3,
                     tolerance_atr: float = 0.0, vol_multiplier: float = 1.9,
                     wick_ratio_min: float | None = None) -> list[int]:
    """
    หา index ของทุก Swing High ที่ผ่าน Volume check
    Swing High = high[i] สูงที่สุดใน window [i-left, i+right]
                 tolerance_atr > 0 → อนุญาตให้ต่ำกว่า peak ได้ไม่เกิน ATR × tolerance_atr

    wick_ratio_min: ถ้าส่งมา (ไม่ใช่ None) จะเปลี่ยนเป็น OR-logic — ผ่านได้ถ้า volume
    เข้าเกณฑ์ **หรือ** wick ratio >= ค่านี้ (ดู swing_wick_ratio_min — เฉพาะ XAU เท่านั้น
    symbol อื่นส่ง None เสมอ = พฤติกรรมเดิม (AND กับ volume อย่างเดียว) ไม่เปลี่ยนแปลง)
    """
    atr = calc_atr(df) if tolerance_atr > 0 else None
    highs = []
    for i in range(left, len(df) - right):
        window = df["high"].iloc[i - left: i + right + 1]
        peak   = window.max()
        tol    = atr.iloc[i] * tolerance_atr if atr is not None else 0.0
        if df["high"].iloc[i] >= peak - tol:
            vol_ok = has_high_volume(df, i, multiplier=vol_multiplier)
            if wick_ratio_min is None:
                if vol_ok:
                    highs.append(i)
            else:
                if vol_ok or _wick_ratio(df, i, is_high=True) >= wick_ratio_min:
                    highs.append(i)
    return highs


def collapse_swing_runs(swing_highs: list[int], swing_lows: list[int],
                        df: pd.DataFrame) -> tuple[list[int], list[int]]:
    """รวม swing point ประเภทเดียวกันที่เกิด 'ติดกัน' (ไม่มีจุดตรงข้ามคั่นระหว่างกลาง)
    ให้เหลือแค่จุดที่สุดขั้วที่สุดของรันนั้น — High รันติดกัน (ไม่มี Low คั่น) เอาแค่ High
    ที่สูงสุด, Low รันติดกัน (ไม่มี High คั่น) เอาแค่ Low ที่ต่ำสุด (2026-07-21)

    ใช้ก่อนเสมอในทุกจุดที่แตะ swing สำหรับตัดสินใจ (SL/TP/Structure/Confirmation/
    ATR trailing/Structure break) เพื่อให้ 6 จุดนี้เห็น 'swing ปัจจุบัน' ตรงกันเป๊ะ —
    ไม่ใช้กับ check_key_level (จงใจนับทุกจุดแยกเพื่อ cluster เป็นโซน) หรือ
    check_divergence (จงใจเทียบจุดดิบ 2 จุดล่าสุดตามนิยาม divergence คลาสสิก)

    คืน (clean_highs, clean_lows) — สลับ High/Low จริงเสมอ ไม่มี type เดียวกันติดกัน 2 ครั้ง"""
    points = sorted(
        [(i, "H") for i in swing_highs] + [(i, "L") for i in swing_lows],
        key=lambda x: x[0]
    )
    if not points:
        return [], []

    def pick_extreme(run):
        idx_list = [i for i, _ in run]
        kind = run[0][1]
        if kind == "H":
            best = max(idx_list, key=lambda i: df["high"].iloc[i])
        else:
            best = min(idx_list, key=lambda i: df["low"].iloc[i])
        return (best, kind)

    clean = []
    run = [points[0]]
    for p in points[1:]:
        if p[1] == run[-1][1]:
            run.append(p)
        else:
            clean.append(pick_extreme(run))
            run = [p]
    clean.append(pick_extreme(run))

    clean_highs = [i for i, t in clean if t == "H"]
    clean_lows  = [i for i, t in clean if t == "L"]
    return clean_highs, clean_lows


def is_lower_high(swing_highs: list[int], df: pd.DataFrame) -> bool:
    """
    ตรวจว่า Swing High ล่าสุดเป็น Lower High จริงไหม
    (ต่ำกว่า Swing High ก่อนหน้า = โครงสร้างขาลงยังอยู่)
    """
    if len(swing_highs) < 2:
        return False
    prev_high = df["high"].iloc[swing_highs[-2]]
    last_high = df["high"].iloc[swing_highs[-1]]
    return last_high < prev_high


# ---------------------------------------------------------------------------
# Main Function — หา SL จาก Swing High ที่ผ่านเกณฑ์ทั้ง 3 ข้อ
# ---------------------------------------------------------------------------

def _rejection_ok(df: pd.DataFrame, idx: int, atr: pd.Series) -> tuple[bool, str]:
    """
    Rejection check สำหรับ 4H:
      wick >= ATR×0.5  OR  Volume ratio >= 2.0x
    """
    c          = df.iloc[idx]
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    atr_val    = atr.iloc[idx]
    avg        = df["tick_volume"].iloc[max(0, idx - 4): idx + 1].mean()
    vol_ratio  = df["tick_volume"].iloc[idx] / avg if avg > 0 else 0.0

    # Short ดู upper wick, Long ดู lower wick
    wick = upper_wick  # caller เลือก wick ตาม direction
    if wick >= atr_val * 0.5:
        return True, f"wick={wick:.0f}"
    if vol_ratio >= 2.0:
        return True, f"vol={vol_ratio:.2f}x"
    return False, "no rejection"


def find_sl_from_structure(df: pd.DataFrame,
                           direction: str,
                           atr_buffer: float = 0.1,   # 2026-07-23: เปลี่ยนจาก 0.2 — ยังไม่มี backtest ยืนยัน (sample เล็กเกินไป)
                           left: int = 4,
                           right: int = 4,
                           tolerance_atr: float = 0.22,   # 2026-07-23: เปลี่ยนจาก 0.05 — ยังไม่มี backtest ยืนยัน
                           vol_multiplier: float = 1.9,
                           wick_ratio_min: float | None = None) -> dict:
    """
    หา SL อัตโนมัติจาก Swing High/Low **ล่าสุดสุดเท่านั้น** — จุดเดียวกับที่
    find_tp_from_fibonacci ใช้เป็น origin (B) เสมอ ทำให้ SL/TP อ้างอิง swing point
    เดียวกันเป๊ะทุกครั้งที่เทรดผ่าน (ก่อนหน้านี้ฟังก์ชันนี้ย้อนหาจุดเก่ากว่าถ้าจุดล่าสุด
    ถูกราคาทะลุไปแล้ว ทำให้ SL/TP หลุด sync กันได้ 24.3% ของเวลา (BTC) / 11.9% (XAU) —
    วัดจากหน้าต่าง 200 แท่ง — 2026-08-01 เปลี่ยนมาเป็น "ไม่เข้าเทรดเลย" แทนการย้อนหา)

    เช็คฝั่งราคา + ผ่อนด้วย ATR (2026-08-01, ใช้ tolerance_atr ตัวเดียวกับที่กรอง swing
    point — ค่ากลางเดียวที่ sync กันทั้งระบบอยู่แล้ว ไม่เพิ่ม magic number ใหม่): จุด swing
    ล่าสุดสุดถือว่า "ยังใช้ได้" ถ้าราคาปัจจุบันยังไม่ทะลุเกิน ATR(ปัจจุบัน) × tolerance_atr
    — กันไม่ให้ wick เหวี่ยงเกินเล็กน้อยถูกนับเป็น "ทะลุจริง" ทั้งที่จริงๆ เป็นแค่ noise ปกติ
    ถ้าทะลุเกิน tolerance นี้ = SL ไม่ผ่าน (ไม่เข้าเทรดรอบนี้ รอจุด swing ใหม่ยืนยันก่อน)

    SL ยึดจาก max/min(swing_price, current_price) ไม่ใช่ swing_price เฉยๆ (2026-08-01):
    ถ้าราคาทะลุจุด swing ไปแล้วแต่ยังอยู่ใน tolerance (เพิ่งอนุญาตด้านบน) การเอา swing_price
    ตรงๆ มาบวก/ลบ buffer จะได้ SL ที่อยู่ใกล้หรือผิดฝั่งราคาปัจจุบันเลย (Short: SL ต่ำกว่า
    entry ทั้งที่ต้องอยู่เหนือ) กลับไปเจอบั๊กเดิม (R:R แคบผิดธรรมชาติ) แบบเบาลง — ยึดราคา
    ปัจจุบันแทนเมื่อมันไกลจากจุด swing มากกว่า กัน SL ใกล้ราคาเข้าเกินจริง
    """
    is_short      = direction.capitalize() == "Short"
    atr           = calc_atr(df)
    current_price = df["close"].iloc[-1]
    break_tolerance = atr.iloc[-1] * tolerance_atr
    swing_highs   = find_swing_highs(df, left=left, right=right, tolerance_atr=tolerance_atr,
                                     vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    swing_lows    = find_swing_lows(df,  left=left, right=right, tolerance_atr=tolerance_atr,
                                    vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    swing_highs, swing_lows = collapse_swing_runs(swing_highs, swing_lows, df)

    swings = swing_highs if is_short else swing_lows
    if not swings:
        return {"passed": False, "reason": "ไม่พบ Swing ในข้อมูล"}

    idx   = swings[-1]
    price = df["high"].iloc[idx] if is_short else df["low"].iloc[idx]
    still_valid = (price > current_price - break_tolerance) if is_short else \
                  (price < current_price + break_tolerance)

    if not still_valid:
        side = "เหนือ" if is_short else "ใต้"
        return {"passed": False,
                "reason": f"ราคาทะลุ Swing {'High' if is_short else 'Low'} ล่าสุดไปแล้วเกิน "
                         f"ATR×{tolerance_atr} (ไม่อยู่{side}ราคาปัจจุบันแล้ว) — รอจุดใหม่ยืนยันก่อน"}

    atr_val     = atr.iloc[idx]
    swing_price = df["high"].iloc[idx] if is_short else df["low"].iloc[idx]
    anchor      = max(swing_price, current_price) if is_short else min(swing_price, current_price)
    sl          = anchor + atr_val * atr_buffer if is_short else anchor - atr_val * atr_buffer

    return {
        "sl":          round(sl, 5),
        "swing_price": swing_price,
        "swing_idx":   idx,
        "atr":         round(atr_val, 5),
        "passed":      True,
        "reason":      "OK",
    }


# ---------------------------------------------------------------------------
# Confirmation Candle — ปิดเหนือ/ใต้ Swing High/Low
# ---------------------------------------------------------------------------

def check_confirmation(current_price: float, df: pd.DataFrame,
                       direction: str, symbol: str,
                       left: int = 4, right: int = 4,
                       tolerance_atr: float = 0.22) -> dict:
    """
    Short: ราคาปัจจุบัน < Swing Low ล่าสุด (4H) = support แตก
    Long:  ราคาปัจจุบัน > Swing High ล่าสุด (4H) = resistance แตก
    ใช้เกณฑ์ swing เดียวกับ find_sl_from_structure/find_tp_from_fibonacci
    (left/right=4, tolerance=0.05, vol_multiplier ตาม symbol) — df ต้องเป็น 4H
    """
    is_short       = direction.capitalize() == "Short"
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    highs = find_swing_highs(df, left=left, right=right, tolerance_atr=tolerance_atr,
                             vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    lows  = find_swing_lows(df, left=left, right=right, tolerance_atr=tolerance_atr,
                            vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    highs, lows = collapse_swing_runs(highs, lows, df)

    if is_short:
        if not lows:
            return {"conf_ok": False, "current_price": current_price,
                    "key_level": None, "reason": "ไม่พบ Swing Low บน 4H"}
        key_level = df["low"].iloc[lows[-1]]
        conf_ok   = current_price < key_level
        reason    = (
            f"ราคา {current_price:.2f} < Swing Low {key_level:.2f} ✅"
            if conf_ok else
            f"ราคา {current_price:.2f} ยังไม่ต่ำกว่า Swing Low {key_level:.2f}"
        )
    else:
        if not highs:
            return {"conf_ok": False, "current_price": current_price,
                    "key_level": None, "reason": "ไม่พบ Swing High บน 4H"}
        key_level = df["high"].iloc[highs[-1]]
        conf_ok   = current_price > key_level
        reason    = (
            f"ราคา {current_price:.2f} > Swing High {key_level:.2f} ✅"
            if conf_ok else
            f"ราคา {current_price:.2f} ยังไม่สูงกว่า Swing High {key_level:.2f}"
        )

    return {
        "conf_ok":       conf_ok,
        "current_price": current_price,
        "key_level":     key_level,
        "reason":        reason,
    }


# ---------------------------------------------------------------------------
# Fibonacci Retracement — หา TP
# ---------------------------------------------------------------------------

FIB_LEVELS = {
    "tp1": 0.382,
    "tp2": 0.618,
    "tp3": 1.000,
}


def find_swing_lows(df: pd.DataFrame, left: int = 3, right: int = 3,
                    tolerance_atr: float = 0.0, vol_multiplier: float = 1.9,
                    wick_ratio_min: float | None = None) -> list[int]:
    """
    หา index ของทุก Swing Low ที่ผ่าน Volume check
    Swing Low = low[i] ต่ำที่สุดใน window [i-left, i+right]
                tolerance_atr > 0 → อนุญาตให้สูงกว่า trough ได้ไม่เกิน ATR × tolerance_atr

    wick_ratio_min: เหมือน find_swing_highs — OR-logic เฉพาะเมื่อส่งมา (XAU เท่านั้น)
    """
    atr = calc_atr(df) if tolerance_atr > 0 else None
    lows = []
    for i in range(left, len(df) - right):
        window = df["low"].iloc[i - left: i + right + 1]
        trough = window.min()
        tol    = atr.iloc[i] * tolerance_atr if atr is not None else 0.0
        if df["low"].iloc[i] <= trough + tol:
            vol_ok = has_high_volume(df, i, multiplier=vol_multiplier)
            if wick_ratio_min is None:
                if vol_ok:
                    lows.append(i)
            else:
                if vol_ok or _wick_ratio(df, i, is_high=False) >= wick_ratio_min:
                    lows.append(i)
    return lows


def calc_fibonacci_levels(swing_high: float, swing_low: float,
                           direction: str) -> dict:
    """
    คำนวณ Fibonacci Retracement levels

    Short: วัดจาก Swing High (0) → Swing Low (1)
           TP อยู่ต่ำกว่า entry → ราคาลงไปหา Fib levels

    Long:  วัดจาก Swing Low (0) → Swing High (1)
           TP อยู่สูงกว่า entry → ราคาขึ้นไปหา Fib levels

    คืน dict: { "tp1": ราคา, "tp2": ราคา, "tp3": ราคา, "levels": {...} }
    """
    move = swing_high - swing_low

    if direction.capitalize() == "Short":
        levels = {
            "0":     swing_high,
            "0.236": swing_high - move * 0.236,
            "0.382": swing_high - move * 0.382,
            "0.5":   swing_high - move * 0.5,
            "0.618": swing_high - move * 0.618,
            "0.786": swing_high - move * 0.786,
            "1":     swing_low,
        }
        tp1 = round(swing_high - move * FIB_LEVELS["tp1"], 5)
        tp2 = round(swing_high - move * FIB_LEVELS["tp2"], 5)
        tp3 = round(swing_low, 5)
    else:
        levels = {
            "0":     swing_low,
            "0.236": swing_low + move * 0.236,
            "0.382": swing_low + move * 0.382,
            "0.5":   swing_low + move * 0.5,
            "0.618": swing_low + move * 0.618,
            "0.786": swing_low + move * 0.786,
            "1":     swing_high,
        }
        tp1 = round(swing_low + move * FIB_LEVELS["tp1"], 5)
        tp2 = round(swing_low + move * FIB_LEVELS["tp2"], 5)
        tp3 = round(swing_high, 5)

    return {
        "tp1":        tp1,
        "tp2":        tp2,
        "tp3":        tp3,
        "swing_high": swing_high,
        "swing_low":  swing_low,
        "levels":     {k: round(v, 5) for k, v in levels.items()},
    }


def find_tp_from_fibonacci(df: pd.DataFrame, direction: str,
                           left: int = 4, right: int = 4,
                           tolerance_atr: float = 0.22,
                           vol_multiplier: float = 1.9,
                           wick_ratio_min: float | None = None) -> dict:
    """
    หา TP อัตโนมัติจาก Fibonacci Extension — ใช้ swing 3 จุดสลับกัน (X -> A -> B)
    วัด "ขนาด impulse เดิม" จากช่วง X->A แล้วฉายต่อจาก B ไปในทิศที่เทรด

    Short:
        B    = Swing High ล่าสุดสุด           <- จุดฉาย (origin)
        A    = Swing Low  ล่าสุดก่อน B
        X    = Swing High ล่าสุดก่อน A        <- ต้นทาง impulse ลง
        Move = X - A                          (ขนาด impulse ลงรอบก่อน)
        TP   = B - Move × ratio               (ฉายต่ำกว่า B)

    Long:
        B    = Swing Low  ล่าสุดสุด           <- จุดฉาย (origin)
        A    = Swing High ล่าสุดก่อน B
        X    = Swing Low  ล่าสุดก่อน A        <- ต้นทาง impulse ขึ้น
        Move = A - X                          (ขนาด impulse ขึ้นรอบก่อน)
        TP   = B + Move × ratio               (ฉายสูงกว่า B)

    ไม่ผ่าน (passed=False) เมื่อหา 3 จุดนี้ไม่ครบ หรือ Move <= 0

    B ใช้ swing ล่าสุดสุดเสมอ (swing_highs[-1]/swing_lows[-1]) — จุดเดียวกับที่
    find_sl_from_structure ใช้ทำ SL เป๊ะ (2026-08-01: find_sl_from_structure เปลี่ยนมาไม่
    fallback ไปจุดเก่ากว่าอีกแล้ว ถ้าจุดล่าสุดถูกราคาทะลุไปแล้วก็แค่ไม่เข้าเทรด แทนที่จะ
    เดิม SL/TP อ้างอิงคนละจุดกัน — parameter sl_swing_idx ที่เคยรับมาแต่ไม่ได้ใช้จึงถูกลบ
    ทิ้งไปด้วย เพราะตอนนี้ sync กันเองโดยอัตโนมัติอยู่แล้ว ไม่ต้องส่งมาซิงค์อีก)
    """
    is_short    = direction.capitalize() == "Short"
    swing_highs = find_swing_highs(df, left=left, right=right, tolerance_atr=tolerance_atr,
                                   vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    swing_lows  = find_swing_lows(df,  left=left, right=right, tolerance_atr=tolerance_atr,
                                  vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
    swing_highs, swing_lows = collapse_swing_runs(swing_highs, swing_lows, df)

    if not swing_highs or not swing_lows:
        return {"passed": False, "reason": "ไม่พบ Swing High/Low เพียงพอ"}

    if is_short:
        # B = Swing High ล่าสุดสุด
        b_idx   = swing_highs[-1]
        b_price = df["high"].iloc[b_idx]

        # A = Swing Low ล่าสุดก่อน B
        lows_before = [i for i in swing_lows if i < b_idx]
        if not lows_before:
            return {"passed": False, "reason": "ไม่พบ Swing Low ก่อน Swing High ล่าสุด"}
        a_idx   = lows_before[-1]
        a_price = df["low"].iloc[a_idx]

        # X = Swing High ก่อน A (impulse เดิม)
        highs_before_a = [i for i in swing_highs if i < a_idx]
        if not highs_before_a:
            return {"passed": False, "reason": "ไม่พบ Swing High ก่อน Swing Low"}
        x_idx   = highs_before_a[-1]
        x_price = df["high"].iloc[x_idx]

        move   = x_price - a_price   # impulse down: X → A
        origin = b_price              # ฉายจาก B ลงต่อ
        sign   = -1

    else:
        # B = Swing Low ล่าสุดสุด
        b_idx   = swing_lows[-1]
        b_price = df["low"].iloc[b_idx]

        # A = Swing High ล่าสุดก่อน B
        highs_before = [i for i in swing_highs if i < b_idx]
        if not highs_before:
            return {"passed": False, "reason": "ไม่พบ Swing High ก่อน Swing Low ล่าสุด"}
        a_idx   = highs_before[-1]
        a_price = df["high"].iloc[a_idx]

        # X = Swing Low ก่อน A (impulse เดิม)
        lows_before_a = [i for i in swing_lows if i < a_idx]
        if not lows_before_a:
            return {"passed": False, "reason": "ไม่พบ Swing Low ก่อน Swing High"}
        x_idx   = lows_before_a[-1]
        x_price = df["low"].iloc[x_idx]

        move   = a_price - x_price   # impulse up: X → A
        origin = b_price              # ฉายจาก B ขึ้นต่อ
        sign   = 1

    if move <= 0:
        return {"passed": False, "reason": "Move ต้องมากกว่า 0"}

    levels = {}
    for ratio in [0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618]:
        levels[str(ratio)] = round(origin + sign * move * ratio, 5)

    return {
        "passed":       True,
        "origin":       origin,
        "move":         round(move, 5),
        "tp1":          levels["0.382"],
        "tp2":          levels["0.618"],
        "tp3":          levels["1.0"],
        "tp4":          levels["1.618"],
        "swing_high":   b_price if is_short else a_price,
        "swing_low":    a_price if is_short else b_price,
        "x_price":      x_price,
        "levels":       levels,
    }
