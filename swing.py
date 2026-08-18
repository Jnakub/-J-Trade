import pandas as pd

from config import TP_FIB_RATIO
from indicators import calc_atr, calc_di   # re-exported เพื่อไม่ให้ต้องแก้ import ที่อื่น


# 2026-08-13: ลบ has_rejection() / _rejection_ok() ทิ้ง — ทั้งคู่ไม่มีใครเรียกเลย (ตรวจทั้ง repo
# แล้ว) แต่ scoring.py ยังพิมพ์ `sl_info.get('rejection', '-')` อยู่ ทั้งที่ find_sl_from_structure
# ไม่เคยคืน key 'rejection' => บรรทัดนั้นพิมพ์ '-' ตลอดกาล ดูเหมือนมีข้อมูลทั้งที่ว่างเปล่า
# ลบทั้งฟังก์ชันและบรรทัดที่พิมพ์ออกพร้อมกัน (เจอตอนไล่ dead code หลังบั๊ก cooldown)


# ---------------------------------------------------------------------------
# ข้อ 2 — Volume Check
# ---------------------------------------------------------------------------

def swing_vol_multiplier(symbol: str) -> float:
    """เกณฑ์ volume ของ swing ตาม symbol — จุดเดียวทั้งระบบ ห้าม hardcode ซ้ำที่อื่น
    XAU/Gold 1.5x (volume ทองแรงน้อยกว่า — ปรับจาก 1.6 เมื่อ 2026-07-24 ยังไม่มี backtest
    ยืนยันค่าใหม่นี้โดยตรง), USDJPYm 1.6x (2026-08-09 — เทียบ swing count ที่ 1.5/1.6/1.9 บน
    400 แท่ง 4H แล้ว: 16/13/11 High, 17/14/12 Low ตามลำดับ เลือก 1.6 เป็นจุดกึ่งกลาง ยังไม่มี
    ข้อมูลเทรดจริงยืนยัน), US500m 2.0x (2026-08-12 — ปรับขึ้นจาก default 1.9 ตามคำสั่งผู้ใช้
    หลังดูตาราง swing จริง 400 แท่ง 4H ยังไม่มี backtest ยืนยันค่านี้โดยเฉพาะ ควรเฝ้าดูผลจริง),
    EURUSDm 1.8x (2026-08-17 — ปรับลงจาก default 1.9 ตามคำสั่งผู้ใช้หลังดูตาราง swing จริง
    400 แท่ง 4H ยังไม่มี backtest ยืนยันค่านี้โดยเฉพาะ), อื่นๆ 1.9x"""
    if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        return 1.5
    if "JPY" in symbol.upper():
        return 1.6
    if "US500" in symbol.upper():
        return 2.0
    if "EUR" in symbol.upper():
        return 1.8
    return 1.9


def swing_wick_ratio_min(symbol: str) -> float | None:
    """เกณฑ์ wick ratio ขั้นต่ำสำหรับ OR-logic ของ swing — คืน None = ปิด OR-logic (ใช้ volume
    อย่างเดียวเหมือนเดิม), คืนตัวเลข = เปิด OR-logic (ผ่านได้ถ้า volume เข้าเกณฑ์ **หรือ**
    wick ratio ถึงค่านี้)

    XAU/Gold (2026-07-21, threshold 0.5 ตั้งแต่ 2026-07-24): XAUUSDm ไม่มี real volume ให้
    merge (COMEX ผ่าน yfinance พังที่ 4H — ดู binance.py) เลยใช้ tick_volume ของโบรกเกอร์เป็น
    proxy ซึ่งเชื่อถือได้น้อย — wick ratio เป็นสัญญาณอิสระจาก volume ที่ช่วยกู้ swing point จริง
    ที่ tick_volume มองไม่เห็น (ยืนยันด้วย backtest 400 แท่ง: vol=1.6x เท่านั้น เจอ lows แค่
    9 จุด, เพิ่ม wick>=0.4 OR เข้าไปเจอเพิ่มเป็น 19 จุด)

    BTC (2026-08-02, threshold 0.5): ต่างจาก XAU ตรงที่ BTC มี real volume จาก Bitstamp
    เชื่อถือได้อยู่แล้ว แต่ backtest 180 วัน (regime-gated, one-position-at-a-time เหมือน
    scheduler.py จริง) พบว่า volume อย่างเดียวยังพลาดจุดกลับตัวจริงบางจุดในช่วงที่ราคาวิ่งแรง/
    ผันผวนสูง (เช่น มี.ค.-เม.ย. ที่ราคาพุ่ง 71,000->82,000 — feed swing high ส่วนใหญ่มาจาก wick
    ล้วนๆ ไม่ใช่ volume) เปิด wick OR-logic แล้ว: เทรดเพิ่มจาก 4->6 ไม้, Total R +6.41->+7.59,
    Win Rate 50%->66.7% (ไม้เดิมที่เคยแพ้ 1 ไม้กลับมาชนะเพราะ SL/TP อ้างอิงจุด swing แม่นขึ้น)
    sample เล็ก (4-6 ไม้) — ควร backtest ซ้ำเมื่อมีข้อมูลเทรดจริงมากพอ

    ETHUSDm (2026-08-02, threshold 0.5 ตอนแรก): backtest 180 วัน (เส้นทาง Scoring/TREND เท่านั้น —
    ยังไม่ได้ทดสอบผลกับ Reversal path) เทรดเท่าเดิม (6 ไม้) แต่ TotalR ดีขึ้น -2.26->-0.98,
    Win Rate 16.7%->33.3%
    2026-08-11: threshold ปรับเป็น 0.55 แล้วปรับอีกรอบเป็น 0.52 ตามคำสั่งผู้ใช้หลังดูรายจุด
    (vol ratio/wick ratio) จริงของ swing 400 แท่งล่าสุด (High เกิดจาก wick ล้วนๆ 6/19 จุด, Low
    9/19 จุด) — ยังไม่มี backtest ยืนยันค่าใหม่นี้โดยตรง ควรเฝ้าดูผลจริงและพร้อมปรับกลับถ้าจำเป็น

    XRPUSDm (2026-08-02, threshold 0.5): backtest ชุดเดียวกันพบว่า **แย่ลง** ชัดเจน
    (3->7 ไม้, TotalR +6.29->-2.86, Win Rate 33.3%->14.3%) — เปิดใช้ตามคำสั่งผู้ใช้แม้ backtest
    จะแนะนำให้คงเดิม (None) เพราะยังไม่มีข้อมูลเทรดจริงยืนยัน sample เล็กมาก (3-7 ไม้) ควร
    เฝ้าดูผลจริงและพร้อมปรับกลับถ้าผลออกมาแย่ตามที่ backtest ชี้

    USDJPYm (2026-08-09, threshold 0.55): เหตุผลเดียวกับ XAU เป๊ะ — Forex เป็นตลาด OTC ไม่มี
    "real volume" ให้ merge ได้จริงในทางทฤษฎี (ไม่มี exchange กลางรวม volume แบบ crypto/futures)
    ต้องใช้ tick_volume ของโบรกเกอร์เป็น proxy เชื่อถือได้น้อยเหมือน XAU เปิด wick OR-logic ไว้
    ตั้งแต่เริ่มเลย (ไม่รอ backtest แยกเหมือน ETH/XRP เพราะสถานการณ์เหมือน XAU ตรงๆ ไม่ใช่กรณี
    "มี real volume อยู่แล้วแต่ยังพลาดบางจุด" แบบ BTC/ETH/XRP) — threshold ปรับจาก 0.5 เป็น 0.55
    ตามคำสั่งผู้ใช้หลังดูรายจุด (vol ratio/wick ratio) จริงของ swing ที่เจอแล้ว ยังไม่มีข้อมูล
    เทรดจริงยืนยัน ควรเฝ้าดูผลจริงเหมือน symbol อื่น

    US500m (2026-08-12, threshold 0.6): เหตุผลเดียวกับ XAU เป๊ะ — S&P500 index ไม่มีแหล่ง real
    volume รวมศูนย์ให้ merge ได้เลย (ไม่อยู่ใน BITSTAMP_MAP/YFINANCE_MAP ของ binance.py และ
    โดยธรรมชาติ index ซื้อขายกระจายหลาย exchange/futures พร้อมกัน ไม่มีตัวเลข volume "จริง"
    เดียวที่รวมทุกที่) เปิด wick OR-logic ตั้งแต่เริ่มใช้งานเลยตามคำสั่งผู้ใช้ เริ่มต้นที่ 0.5
    เท่า XAU ก่อน แล้วปรับขึ้นเป็น 0.6 ตามคำสั่งผู้ใช้หลังดูตาราง swing จริง 400 แท่ง 4H —
    ยังไม่มี backtest/ข้อมูลเทรดจริงยืนยันค่านี้โดยเฉพาะสำหรับ US500m ควรเฝ้าดูผลจริงเหมือน
    symbol อื่น

    Default (2026-08-12, threshold 0.5): เปลี่ยนจาก None เป็น 0.5 ตามคำสั่งผู้ใช้ตอนเพิ่ม
    US500m — symbol ใหม่ที่ยังไม่มี branch เฉพาะจะเปิด wick OR-logic ทันทีแทนที่จะปิดไว้ก่อน
    (พฤติกรรมเดิม) ยังไม่มี backtest ยืนยันว่า 0.5 เหมาะกับทุก asset class ในอนาคต — ควรพิจารณา
    ปรับเป็นค่าเฉพาะเมื่อมีข้อมูลเทรดจริงของ symbol นั้นๆ

    EURUSDm (2026-08-17, threshold 0.6): เหตุผลเดียวกับ USDJPYm — Forex OTC ไม่มี real volume
    ให้ merge เลย ปรับขึ้นจาก default 0.5 เป็น 0.6 ตามคำสั่งผู้ใช้หลังดูตาราง swing จริง 400 แท่ง
    4H (ดู inspect_swings.py) — ยังไม่มี backtest ยืนยันค่านี้โดยเฉพาะ ควรเฝ้าดูผลจริง"""
    if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        return 0.5
    if "BTC" in symbol.upper():
        return 0.5
    if "JPY" in symbol.upper():
        return 0.55
    if "ETH" in symbol.upper():
        return 0.52
    if "XRP" in symbol.upper():
        return 0.5
    if "US500" in symbol.upper():
        return 0.6
    if "EUR" in symbol.upper():
        return 0.6
    return 0.5


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


# 2026-08-13: ลบ is_lower_high() ทิ้ง — ไม่มีใครเรียก ถูกแทนด้วย regime_check.check_structure()
# ที่ละเอียดกว่า (เทียบ 3 จุดสุดท้ายแบบข้ามจุดกลาง ไม่ใช่เทียบแค่ 2 จุดล่าสุด)


# ---------------------------------------------------------------------------
# Main Function — หา SL จาก Swing High ที่ผ่านเกณฑ์ทั้ง 3 ข้อ
# ---------------------------------------------------------------------------

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

    เคสยึด current_price (ทะลุแล้ว) ใช้ buffer = max(atr_buffer, tolerance_atr) แทน
    atr_buffer เฉยๆ (2026-08-01): tolerance_atr (0.22) > atr_buffer (0.1) อยู่แล้ว ถ้ายังใช้
    atr_buffer ตัวเดิม SL จะห่าง current_price แค่ ATR×0.1 — แคบผิดธรรมชาติอีกแบบ (กันชนที่
    ออกแบบมาสำหรับเคสปกติที่ SL ยึด swing_price ซึ่งมักไกลจากราคาอยู่แล้ว ไม่ใช่ระยะเสี่ยง
    ทั้งหมด) เคสปกติ (ไม่ทะลุ) ยังใช้ atr_buffer เดิมเป๊ะ ไม่กระทบพฤติกรรมที่มีอยู่แล้ว
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
    breached    = (current_price > swing_price) if is_short else (current_price < swing_price)
    anchor      = current_price if breached else swing_price
    buffer      = max(atr_buffer, tolerance_atr) if breached else atr_buffer
    sl          = anchor + atr_val * buffer if is_short else anchor - atr_val * buffer

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
# Fibonacci — หา TP  (ดู find_tp_from_fibonacci ด้านล่าง)
# ---------------------------------------------------------------------------
# 2026-08-13: ลบ FIB_LEVELS (0.382/0.618/1.000) + calc_fibonacci_levels() ทิ้ง — เป็นกับดัก
# ตัวจริง ไม่ใช่แค่โค้ดค้าง: FIB_LEVELS นั่งอยู่ใต้หัวข้อ "Fibonacci — หา TP" หน้าตาเหมือนค่า
# config ที่ปรับ TP ได้ แต่มีที่เดียวที่อ่านมันคือ calc_fibonacci_levels() ซึ่งไม่มีใครเรียกเลย
# (git log: ไม่เคยถูกแก้เลยตั้งแต่ initial commit) — ใครไปแก้เลขในนั้นเพื่อจูน TP จะไม่มีอะไร
# เกิดขึ้น เหมือนกรณี COOLDOWN_HOURS_BY_SYMBOL ที่ไม่มีผลจริงเป๊ะ
# ซ้ำร้ายสองฟังก์ชันคำนวณคนละวิธี: calc_fibonacci_levels เป็น retracement (2 จุด ไม่เกิน 1.0)
# ส่วนตัวที่ใช้จริงเป็น extension 3 จุด (X->A->B) ยิงได้ถึง 1.618 — ถ้าเผลอสลับไปใช้ตัวที่ตาย
# จะได้ TP ใกล้กว่าเดิมมากโดยไม่รู้ตัว
# ตอนนี้เหลือทางเดียว: find_tp_from_fibonacci() + config.TP_FIB_RATIO


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

    # levels = ตารางอ้างอิงไว้ดูประกอบเท่านั้น — TP ที่ระบบใช้จริงคือ key "tp" ด้านล่าง
    # (2026-08-13: เพิ่ม TP_FIB_RATIO เข้าไปในลิสต์ด้วย เผื่อปรับ config เป็นค่าที่ไม่อยู่ในนี้
    # จะได้ยังเห็นในตาราง — ไม่งั้นตารางกับ TP จริงจะไม่ตรงกัน)
    ratios = sorted({0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618, TP_FIB_RATIO})
    levels = {str(r): round(origin + sign * move * r, 5) for r in ratios}

    return {
        "passed":       True,
        "origin":       origin,
        "move":         round(move, 5),
        # TP ที่ใช้จริง — คำนวณจาก config.TP_FIB_RATIO จุดเดียว (เดิม caller ไปหยิบ
        # levels["1.618"] ด้วย string key hardcode เองคนละที่ จน backtest หลุด sync)
        "tp":           round(origin + sign * move * TP_FIB_RATIO, 5),
        "tp_ratio":     TP_FIB_RATIO,
        "swing_high":   b_price if is_short else a_price,
        "swing_low":    a_price if is_short else b_price,
        "x_price":      x_price,
        "levels":       levels,
    }
