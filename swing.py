import pandas as pd

from config import TP_FIB_RATIO, ASSET_CLASS
from indicators import calc_atr, calc_di   # re-exported เพื่อไม่ให้ต้องแก้ import ที่อื่น

# สวิตช์ทดลอง — ค่า default = พฤติกรรมระบบจริง ห้ามแก้ที่นี่
# False = find_sl_from_structure กลับไปตรวจด่าน still_valid ด้วย close ของแท่ง 4H ล่าสุดแทน
# ราคาที่จะเข้าไม้จริง (= พฤติกรรมก่อน 2026-09-12 ดูคำอธิบายเต็มในฟังก์ชัน)
# backtest_replay.py ตั้งค่านี้เฉพาะรอบที่รันด้วย --sl-guard-legacy ไว้เทียบว่าการแก้คุ้มไหม
USE_ENTRY_AS_CURRENT_PRICE = True

# SWING_RECENCY_TOL_ATR — ในพวง swing ให้จุดล่าสุดชนะจุดสุดขั้วถ้าแพ้ไม่เกิน k × ATR · None = ปิด (⚠️ 0 ไม่ใช่ no-op)
#   วัดแล้วไม่ช่วย (2026-09-15 · 0.22 ATR: direct ≈ 0 ที่เหลือ churn ล้วน) · กระทบ 6 จุดพร้อมกัน (SL/TP · structure · divergence ...)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — SWING_RECENCY_TOL_ATR (2026-09-15)"
SWING_RECENCY_TOL_ATR = None


# has_rejection() / _rejection_ok() ลบแล้ว 2026-08-13 (ไม่มีใครเรียก)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — has_rejection ลบแล้ว (2026-08-13)"


# ---------------------------------------------------------------------------
# ข้อ 2 — Volume Check
# ---------------------------------------------------------------------------

# ค่า vol ราย symbol เลือกจากตาราง swing (inspect_swings) ตามคำสั่งผู้ใช้ทีละตัว · ส่วนใหญ่ไม่มี backtest ยืนยันค่าเฉพาะตัว
# ลำดับการหา: symbol -> class (config.ASSET_CLASS) -> 1.9 กลาง (2026-08-18) · symbol ที่ถอดแล้ว (GBP/XRP) ค่ายังอยู่
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — VOL_MULTIPLIER ที่มา 2026-08"
VOL_MULTIPLIER_BY_SYMBOL = {
    # HK50m 1.6 (คู่ wick 0.52) — ผู้ใช้เลือก 2026-09-20 (เดิม 1.5/0.45) · vol เป็นคันโยกหลักของ HK50 (ไส้ชนพื้นแล้ว) · วัดที่ BAR_OFFSET_H = 1
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — HK50m vol (2026-09-19..20)"
    "HK50m":   1.6,
    # 2026-09-29: GBPCHFm 1.69 (คู่กับ wick 0.59) ผู้ใช้เลือกหลังดูตาราง 5 ชุด (0.45/1.5 · 0.5/1.6 ·
    #   0.6/1.63 · 0.59/1.69 · 0.58/1.71) ที่ offset 0 — ได้ 47 จุด ใช้จริง 28 (14H/14L)
    # 🔑 ไม่มี real volume (FX OTC) vol ของคู่นี้คือ **นาฬิกา session**: จุดที่ผ่าน vol ~95% เป็นแท่ง
    #   08:00/12:00 UTC (เปิดลอนดอน/ลอนดอนทับนิวยอร์ก) แท่ง 20:00-04:00 UTC เข้าได้ทาง wick อย่างเดียว
    # ⚠️ 1.69 อยู่ใต้กอง 1.70-1.71x พอดี (29/07 H · 26/08 H · 08/07 L = ก้นจริง) ขยับเป็น 1.71 แล้ว
    #   ก้น 08/07 หาย ตัวแทนเลื่อนไปจุดที่สูงกว่า 7 pip — ถ้าจะจูนต่อให้ดูตารางใหม่ทั้งชุด
    "GBPCHFm": 1.69,
    # 2026-09-30: AUDNZDm 1.3 (คู่กับ wick 0.65) ผู้ใช้เลือกจาก 3 ตาราง (0.45/1.5 · 0.5/1.6 · 0.65/1.3) ที่ offset 1
    #   ได้ 28 จุด ใช้จริง 17 (9H/8L) · ผ่านด้วย vol ล้วน 12 · wick ล้วน 14 · ทั้งคู่ 2
    # 🔑 ต่างจาก GBPCHF: tick volume ของ AUDNZD ไม่พุ่งตาม session (สกุลโซนเดียวกัน) ที่ vol 1.5 แทบไม่มี
    #   จุดผ่าน vol เลย (4/43) — vol 1.3 จึงเป็นการเปิดทางที่สองให้ ไม่ใช่การคลายเกณฑ์ที่เคยทำงาน
    "AUDNZDm": 1.3,
    "XAUUSDm": 1.6,
    "USDJPYm": 1.6,
    "US500m":  2.0,
    # EURUSDm 2.0 (คู่ wick 0.46) — ผู้ใช้เลือก 2026-09-20 (เดิม 1.8/0.6) · 1.85-2.0 ให้ผลเท่ากัน · น้ำหนักย้ายไปทาง wick เกือบหมด
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — EURUSDm vol (2026-09-20)"
    "EURUSDm": 2.0,
    "GBPUSDm": 1.8,
    # UKOILm 1.55 (คู่ wick 0.47) — เลือก 2026-09-19 จากตาราง 7 ชุด (จุดใช้จริง 14 -> 25) · ได้ไม้เพิ่มแค่ 1 ไม้ = จูน swing ไม่ใช่คอขวด
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — UKOILm vol (2026-09-19)"
    "UKOILm":  1.55,
}
VOL_MULTIPLIER_BY_CLASS = {
    "CRYPTO": 1.9,
    "GOLD":   1.5,
    "FOREX":  1.7,   # ค่ากลางระหว่าง USDJPYm(1.6) กับ EUR/GBP(1.8) ที่ tune แยกไว้แล้ว
    "INDEX":  2.0,
}


def swing_vol_multiplier(symbol: str) -> float:
    """เกณฑ์ volume ของ swing ตาม symbol — จุดเดียวทั้งระบบ ห้าม hardcode ซ้ำที่อื่น
    ลำดับ: override เฉพาะ symbol -> default ตาม asset class -> 1.9 กลาง"""
    if symbol in VOL_MULTIPLIER_BY_SYMBOL:
        return VOL_MULTIPLIER_BY_SYMBOL[symbol]
    cls = ASSET_CLASS.get(symbol)
    if cls in VOL_MULTIPLIER_BY_CLASS:
        return VOL_MULTIPLIER_BY_CLASS[cls]
    return 1.9


# ค่า wick ราย symbol เลือกจากตาราง swing ตามคำสั่งผู้ใช้ (ส่วนใหญ่ไม่มี backtest ยืนยัน) · wick สำคัญกับตัวที่ไม่มี real volume (ทอง/FX/ดัชนี)
# ลำดับการหา: symbol -> class -> default (2026-08-18)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — WICK_RATIO_MIN ที่มา 2026-07..08"
WICK_RATIO_MIN_BY_SYMBOL = {
    # 2026-09-19: HK50m — ผู้ใช้เลือกคู่กับ vol 1.5 (ดูเหตุผลเต็มที่ VOL_MULTIPLIER_BY_SYMBOL)
    # ที่ HK50 เกณฑ์นี้ **ชนพื้นแล้ว** — จุด swing ที่ wick ต่ำสุดอยู่ที่ 0.44 การลดต่ำกว่า
    # 0.45 จึงไม่ได้จุดเพิ่มเลยสักจุด (ต่างจาก UKOILm ที่ wick 0.54->0.45 ได้เพิ่ม 9 จุด)
    # สองทางแบ่งกันคนละครึ่งพอดี: ผ่านด้วย vol ล้วน 19 จุด · wick ล้วน 17 · ทั้งคู่ 12
    "HK50m":   0.52,
    # 2026-09-29: GBPCHFm 0.59 — ดูเหตุผลที่ VOL_MULTIPLIER_BY_SYMBOL (0.59 ให้จุดเท่ากับ 0.60 ทุกจุด
    #   เพราะไม่มีไส้เทียนในช่วง 0.59-0.60 เลย)
    "GBPCHFm": 0.59,
    # 2026-09-30: AUDNZDm 0.65 — ดูเหตุผลที่ VOL_MULTIPLIER_BY_SYMBOL · ช่วง wick 0.45-0.64 ถูกตัดออกหมด
    #   ทำให้คลื่นย่อยหายหลายลูก (เช่นก้น 14/09 หาย ยอด 18/09 จึงถูกยุบ) — รู้แล้วตอนเลือก
    "AUDNZDm": 0.65,
    "XAUUSDm": 0.3,
    # BTCUSDm 0.48 — ผู้ใช้ 2026-09-21 (เดิม 0.5) ให้ pivot low จริงที่ wickDn 0.488 ผ่าน · replay BTC −4.04R (noise) = ปรับขอบให้พอดีเคสเดียว
    #   🔴 ตัวเลขในตาราง swing ถูกปัดทศนิยม — ต้องดึงค่าจริงก่อนตั้งเกณฑ์ (0.49 ในตาราง = 0.488 จริง)
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "swing.py — BTCUSDm wick 0.48 (2026-09-21)"
    "BTCUSDm": 0.48,
    "ETHUSDm": 0.52,
    "XRPUSDm": 0.5,
    "USDJPYm": 0.55,
    "US500m":  0.6,
    # 2026-09-20: EURUSDm 0.6 -> 0.46 ตามคำสั่งผู้ใช้ (คู่กับ vol 2.0 — ดูเหตุผลเต็มที่
    # VOL_MULTIPLIER_BY_SYMBOL) ที่ 0.46 ดึงก้นจริง 2 จุดที่เคยพลาดหวุดหวิดเข้ามาได้:
    #   09-14 11:00 (wickL 0.48) = ก้นจริงของช่วง 14-15 ก.ย. ที่เดิมตกทั้งสองเกณฑ์
    #   07-12 23:00 (wickL 0.47)
    # ⚠️ แต่ 0.48 -> 0.46 ให้จุดใช้จริง **เท่าเดิมที่ 23 จุด** — ได้ 2 จุดใหม่ แลกกับ
    #    07-08 11:00 ที่หลุดไปเพราะ collapse_swing_runs จัดรันใหม่ = ขยับ 0.02 ได้ผลสุทธิศูนย์
    "EURUSDm": 0.46,
    "GBPUSDm": 0.5,
    # 2026-09-19: UKOILm — ที่ default 0.5 มี **25/26 จุดผ่าน** = ตัวกรองแทบไม่ได้กรองอะไร
    # (wick_ratio ของจุด swing อยู่ที่ 0.38-0.91) **ผู้ใช้เลือก 0.47 หลังไล่ดูตารางทีละค่า**
    # 0.47 เป็นจุดที่พวง 09-11 (3 จุดใน 8 ชม.) คลายออกจนมีตัวแทน และจุดคุณภาพสูงสุดในตาราง
    # (06-09 13:00 · 1.92x/0.56 ผ่านทั้งสองเกณฑ์) ยังได้ใช้ ต่างจาก 0.40 ที่มันถูกยุบทิ้ง
    # ⚠️ wick เป็นคันโยกที่แรงกว่า vol มากสำหรับ UKOIL — 0.54->0.45 ได้จุดเพิ่ม 9 จุด
    #    ขณะที่ vol 1.9->1.5 ได้เพิ่มแค่ 5 จุด (เพราะจุดส่วนใหญ่ผ่านทาง wick อยู่แล้ว)
    #    และช่วง 0.45-0.54 คือโซนที่ไส้เทียนของ UKOIL กระจุกที่สุด = **ไวต่อการสุ่มมาก**
    "UKOILm":  0.47,
}
WICK_RATIO_MIN_BY_CLASS = {
    "CRYPTO": 0.5,
    "GOLD":   0.5,
    "FOREX":  0.55,  # ตรงกับ USDJPYm — เหตุผล OTC/ไม่มี real volume ใช้ได้กับ Forex ทุกคู่
    "INDEX":  0.6,
}


def swing_wick_ratio_min(symbol: str) -> float | None:
    """เกณฑ์ wick ratio ขั้นต่ำสำหรับ OR-logic ของ swing — คืน None = ปิด OR-logic (ใช้ volume
    อย่างเดียวเหมือนเดิม), คืนตัวเลข = เปิด OR-logic (ผ่านได้ถ้า volume เข้าเกณฑ์ **หรือ**
    wick ratio ถึงค่านี้) ลำดับ: override เฉพาะ symbol -> default ตาม asset class -> 0.5 กลาง
    ดูที่มาของแต่ละค่า/ประวัติ backtest ใน comment เหนือ WICK_RATIO_MIN_BY_SYMBOL ด้านบน"""
    if symbol in WICK_RATIO_MIN_BY_SYMBOL:
        return WICK_RATIO_MIN_BY_SYMBOL[symbol]
    cls = ASSET_CLASS.get(symbol)
    if cls in WICK_RATIO_MIN_BY_CLASS:
        return WICK_RATIO_MIN_BY_CLASS[cls]
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
    เข้าเกณฑ์ **หรือ** wick ratio >= ค่านี้ (ดู swing_wick_ratio_min)
    🔴 2026-09-20 แก้: บรรทัดนี้เคยเขียนว่า "เฉพาะ XAU เท่านั้น symbol อื่นส่ง None เสมอ"
    ซึ่ง**หมดอายุไปนานแล้ว** — ตอนนี้ทั้ง 9 symbol มีค่า wick ของตัวเองใน
    WICK_RATIO_MIN_BY_SYMBOL ทุกตัว = OR-logic ทำงานทั้งระบบ ไม่มีตัวไหนเป็น AND ล้วน
    (คำเตือนที่ค้างแบบนี้เคยทำให้อ่านตาราง swing ผิดมาแล้วจริง 1 ครั้ง — ดู inspect_swings)
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

    SWING_RECENCY_TOL_ATR (ปกติ None = ปิด): ถ้าตั้งเป็นตัวเลข จุด "ล่าสุดของรัน" จะชนะแทน
    ถ้ามันแพ้จุดสุดขั้วไม่เกิน ATR × ค่านั้น — ดูเหตุผลและตัวเลขความถี่ที่ตัวแปรนั้น

    คืน (clean_highs, clean_lows) — สลับ High/Low จริงเสมอ ไม่มี type เดียวกันติดกัน 2 ครั้ง"""
    points = sorted(
        [(i, "H") for i in swing_highs] + [(i, "L") for i in swing_lows],
        key=lambda x: x[0]
    )
    if not points:
        return [], []

    # ATR คำนวณครั้งเดียวต่อการเรียก และเฉพาะตอนเปิดสวิตช์ — ฟังก์ชันนี้ถูกเรียกทุกรอบสแกน
    # จาก 6 จุด รอบที่ไม่ได้ทดลองจึงต้องไม่จ่ายค่า calc_atr เลย
    _atr = calc_atr(df) if SWING_RECENCY_TOL_ATR is not None else None

    def pick_extreme(run):
        idx_list = [i for i, _ in run]
        kind = run[0][1]
        if kind == "H":
            best = max(idx_list, key=lambda i: df["high"].iloc[i])
        else:
            best = min(idx_list, key=lambda i: df["low"].iloc[i])
        if _atr is not None and len(idx_list) > 1:
            latest = idx_list[-1]
            if latest != best:
                col  = "high" if kind == "H" else "low"
                lost = abs(df[col].iloc[best] - df[col].iloc[latest])
                if lost <= _atr.iloc[latest] * SWING_RECENCY_TOL_ATR:
                    best = latest          # แพ้ไม่มาก แต่สดกว่า -> เอาจุดล่าสุด
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
                           wick_ratio_min: float | None = None,
                           current_price: float | None = None) -> dict:
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
    # current_price = ราคาที่จะใช้เข้าไม้จริง ส่งมาจากผู้เรียก (compute_entry/compute_reversal_entry)
    # 2026-09-12: เดิมอ่าน df["close"].iloc[-1] เอง ซึ่งเป็น close ของแท่ง **4H** ที่ปิดล่าสุด
    # แต่ราคาที่เอาไปเข้าไม้จริงคือ tick/close ของแท่ง 1H ณ วินาทีที่ตัดสินใจ — ห่างกันได้ถึง
    # 3 ชม. (bars.py กรองแท่ง 4H ที่ยังไม่ครบทิ้งอยู่แล้ว แท่งล่าสุดจึงปิดไปแล้วเสมอ)
    # ผล: ด่าน still_valid ด้านล่างประกาศว่ากันไม่ให้ราคาทะลุ swing เกิน ATR×tolerance_atr
    # (0.22) แต่บังคับกับราคาที่เก่าได้ถึง 3 ชม. ซึ่งบนแท่ง 1H ราคาวิ่งเกิน 0.22 ATR ได้สบาย
    # = ด่านหลวมกว่าที่เขียนไว้มาก เจอจากไม้ BTCUSDm 2025-03-04 02:00 Long ที่ผ่านด่านมาได้
    # ทั้งที่ SL โครงสร้างอยู่ **เหนือ** ราคาเข้า 0.95 ATR (ถอดกลับแล้วราคาที่ด่านใช้ตรวจสูงกว่า
    # ราคาเข้าจริงอย่างน้อย 0.83 ATR) — ไม้แบบนั้นชนะไม่ได้ตั้งแต่ก่อนเข้าเพราะ SL ผิดฝั่ง
    # เป็นบั๊กประเภทเดียวกับ lookahead ที่แก้ไปเมื่อ 2026-09-01 (ตัดสินใจด้วยราคาหนึ่ง เทรดด้วย
    # อีกราคาหนึ่ง) ต่างกันที่ครั้งนี้เป็นราคาเก่า ไม่ใช่ราคาอนาคต
    # ไม่ส่งมา = ใช้พฤติกรรมเดิม (ผู้เรียกที่เป็นเครื่องมือวินิจฉัยยังเรียกแบบเดิมได้)
    if current_price is None or not USE_ENTRY_AS_CURRENT_PRICE:
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

    wick_ratio_min: เหมือน find_swing_highs — OR-logic เฉพาะเมื่อส่งมา
    🔴 2026-09-21 แก้: บรรทัดนี้เคยต่อท้ายว่า "(XAU เท่านั้น)" ซึ่ง**หมดอายุไปแล้ว** เหมือนกับ
    ที่แก้ใน find_swing_highs เมื่อ 2026-09-20 — ตอนนี้ทุก symbol ใน SYMBOLS มีค่า wick ของ
    ตัวเองใน WICK_RATIO_MIN_BY_SYMBOL ครบ ผู้เรียกทุกทาง (find_sl_from_structure ·
    find_tp_from_fibonacci · check_structure · check_divergence) ส่งค่าจริงมาเสมอ ไม่มี None
    (รอบ 09-20 แก้ไปแค่ฝั่ง highs ฝั่ง lows ตกหล่น — เจอตอนไล่ grep 2026-09-21)
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
                           wick_ratio_min: float | None = None,
                           ratio: float | None = None) -> dict:
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
    # ratio: ทับอัตราส่วน TP เฉพาะการเรียกครั้งนี้ (None = TP_FIB_RATIO ของ module — Breakout สลับค่านี้
    # ชั่วคราวอยู่ ต้องอ่านตอนเรียกเสมอ ห้าม bind เป็น default argument) · 2026-09-26 เพิ่มให้ Reversal
    _r = TP_FIB_RATIO if ratio is None else ratio
    ratios = sorted({0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618, _r})
    levels = {str(r): round(origin + sign * move * r, 5) for r in ratios}

    return {
        "passed":       True,
        "origin":       origin,
        "move":         round(move, 5),
        # TP ที่ใช้จริง — คำนวณจาก config.TP_FIB_RATIO จุดเดียว (เดิม caller ไปหยิบ
        # levels["1.618"] ด้วย string key hardcode เองคนละที่ จน backtest หลุด sync)
        "tp":           round(origin + sign * move * _r, 5),
        "tp_ratio":     _r,
        "swing_high":   b_price if is_short else a_price,
        "swing_low":    a_price if is_short else b_price,
        "x_price":      x_price,
        "levels":       levels,
    }
