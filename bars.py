"""
bars.py — จุดเดียวทั้งระบบสำหรับดึงแท่ง 4H ที่ "เลื่อนขอบ" ให้ตรงกับ TradingView

ที่มา: MT5 ตัดแท่ง 4H ตามเวลาเซิร์ฟเวอร์โบรก TradingView ตัดตาม session ของ feed ที่เลือก
ขอบแท่งคนละจุด => OHLC ทุกแท่งต่างกัน => ทุกอย่างที่คำนวณจากแท่ง 4H (ADX, ATR, swing high/low,
SL/TP, structure break, key level, divergence) ต่างกันไปด้วย ทั้งที่สูตรเดียวกันเป๊ะ — ปัญหานี้
เจอครั้งแรกตอน ADX ของ US500m ไม่ตรงกับ TradingView (2026-08-12) ตอนนั้นจำกัดขอบเขตแก้ไว้แค่
ADX/regime เท่านั้น เพราะ vol/wick ที่ tune ไว้ผูกกับแท่ง MT5 ดิบ — ภายหลัง (2026-08-18) ผู้ใช้
ขอให้ทำให้ "ตรงกันทั้งหมด" จึงย้าย logic มาไว้ที่นี่จุดเดียว ให้ทุกจุดที่แตะแท่ง 4H เรียกร่วมกัน

BAR_OFFSET_H = จำนวนชั่วโมงที่ต้องเลื่อนขอบแท่ง 4H จากของ MT5 (เดิมชื่อ ADX_BAR_OFFSET_H ใน
regime_check.py — ย้ายมาที่นี่เพราะตอนนี้ไม่ใช่แค่ ADX ใช้แล้ว regime_check.py ยังคง import
ชื่อเดิมไว้ให้โค้ด/comment เก่าที่อ้างถึงไม่พัง) 0 = ใช้แท่ง MT5 ตรงๆ ไม่ต้อง resample

🔴 เพิ่ม symbol ใหม่ต้องมาเช็คตรงนี้: เปิดกราฟ 4H บน TradingView ตั้ง ADX 20/20 เทียบกับที่ระบบ
คำนวณ ลอง offset 0-3 หาว่าอันไหนตรงที่สุด (ดู regime_check.get_adx_bars ตัวอย่างการเรียก)
symbol ที่ไม่มีในนี้ default = 0

⚠️ ผลกระทบของการเปลี่ยน offset (2026-08-18): กระทบ "ทุกอย่าง" ที่คำนวณจากแท่ง 4H ไม่ใช่แค่ ADX
อีกต่อไป — swing high/low ที่ใช้หา SL/TP, structure break (exit checklist ข้อ 2), key level,
divergence ก็เปลี่ยนตามไปด้วย เพราะแท่งที่เห็นเปลี่ยนไปทั้งชุด symbol ที่มี offset != 0
(XAUUSDm/ETHUSDm/USDJPYm/US500m/GBPUSDm ณ ตอนที่เขียน) จึง "ควร" ไปเช็คตาราง swing จริงซ้ำ
(inspect_swings.py) ว่า vol_multiplier/wick_ratio_min ที่ tune ไว้เดิมยังเหมาะไหม — ค่าที่ tune
ไว้ก่อนหน้านี้ผูกกับแท่งดิบของ MT5 (offset=0) ทั้งหมด
"""
import pandas as pd

from config import MT5_TIMEFRAMES

BAR_OFFSET_H = {
    "BTCUSDm": 0,
    "XAUUSDm": 1,
    "ETHUSDm": 2,
    "XRPUSDm": 0,
    "USDJPYm": 1,
    "US500m":  2,
    "EURUSDm": 0,
    "GBPUSDm": 2,
}


def get_aligned_4h(symbol: str, bars: int, as_of=None) -> pd.DataFrame:
    """คืนแท่ง 4H เลื่อนขอบตาม BAR_OFFSET_H[symbol] — offset=0 ใช้แท่ง 4H ของ MT5 ตรงๆ
    (เหมือนเดิมทุกประการ ไม่มี resample มาเกี่ยวเลย) offset!=0 ดึง 1H มารวมเป็น 4H เองด้วย
    offset นั้น แล้วตัดให้เหลือ `bars` แท่งสุดท้าย (นับถึง as_of ถ้าระบุ ไม่งั้นถึงปัจจุบัน)

    คืนคอลัมน์เดียวกับที่ mt5.copy_rates_from* ให้ (time/open/high/low/close/tick_volume)
    ใช้แทน mt5.copy_rates_from(_pos) ได้ตรงๆ ในทุกจุดที่ต้องการแท่ง 4H"""
    import MetaTrader5 as mt5

    offset = BAR_OFFSET_H.get(symbol, 0)
    if offset == 0:
        if as_of is None:
            rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES["4H"], 0, bars)
        else:
            rates = mt5.copy_rates_from(symbol, MT5_TIMEFRAMES["4H"], as_of, bars)
        if rates is None or len(rates) == 0:
            code, msg = mt5.last_error()
            raise RuntimeError(f"ดึงข้อมูล {symbol} ไม่ได้  [{code}] {msg}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    # เผื่อแท่ง 1H ให้พอ: 4 แท่ง/1 แท่ง 4H + เผื่อ gap ช่วงตลาดปิด (forex/index ไม่ได้เทรด 24/7)
    # และเผื่อแท่งหัว-ท้ายที่ resample แล้วไม่เต็ม 4 ชม.
    h1_bars = bars * 4 + 50
    if as_of is None:
        rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES["1H"], 0, h1_bars)
    else:
        rates = mt5.copy_rates_from(symbol, MT5_TIMEFRAMES["1H"], as_of, h1_bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงข้อมูล {symbol} (1H สำหรับ resample 4H) ไม่ได้  [{code}] {msg}")

    h1 = pd.DataFrame(rates)
    h1["time"] = pd.to_datetime(h1["time"], unit="s")
    g = (h1.set_index("time")
           .resample("4h", offset=f"{offset}h")
           .agg({"open": "first", "high": "max", "low": "min",
                 "close": "last", "tick_volume": "sum"})
           .dropna()
           .reset_index())
    return g.iloc[-bars:].reset_index(drop=True)
