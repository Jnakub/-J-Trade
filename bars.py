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

# 2026-08-18: re-sweep offset 0-3 ใหม่ทั้งหมดหลังเพิ่ม gap-filter (ดู get_aligned_4h) โดยเทียบ
# กับ ATR(14)+ADX(20) จริงจาก TradingView พร้อมกัน (เลือก offset ที่ผลรวม |ต่าง ATR%|+|ต่าง ADX%|
# น้อยสุด) — ค่าเดิมก่อนหน้านี้ (ตอนยังไม่มี gap-filter) ใช้ไม่ได้แล้วเพราะวิธีคำนวณเปลี่ยน:
#   BTCUSDm  0->3   (รวม 8.25->0.89, ATR/ADX เห็นตรงกันชัดเจน)
#   XAUUSDm  1->0   (รวม 22.40->4.44, ATR/ADX เห็นตรงกันชัดเจน)
#   US500m   2->3   (รวม 10.05->4.04, ATR/ADX เห็นตรงกันชัดเจน)
#   USDJPYm  1->1   (ไม่เปลี่ยน — offset=1 ยังดีสุดทั้ง ATR −6.60% และ ADX −0.37%)
#   EURUSDm  0->3   (รวม 8.52->3.96, ADX เป็นตัวชี้ขาด −0.60% แม้ ATR เดี่ยวๆ จะชอบ offset=1 กว่า)
#   GBPUSDm  2->1   (รวม 9.24->3.53, ATR/ADX เห็นตรงกันชัดเจน)
# ETHUSDm/XRPUSDm ยังไม่มีเป้า ATR/ADX จริงจาก TradingView ให้เทียบ — คงค่าเดิมไว้ก่อน
BAR_OFFSET_H = {
    "BTCUSDm": 3,
    "XAUUSDm": 0,
    "ETHUSDm": 2,
    "XRPUSDm": 0,
    "USDJPYm": 1,
    "US500m":  3,
    "EURUSDm": 3,
    "GBPUSDm": 1,
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

    # 2026-08-18: กรองแท่ง 4H ที่ประกอบจากแท่งย่อย 1H ไม่ครบ 4 แท่งทิ้ง — เจอตอนเทียบ ATR
    # ของ USDJPYm กับ TradingView แล้วไม่ตรงไม่ว่า offset ไหน (ต่างจาก BTCUSDm ที่หา offset
    # ตรงได้ทั้ง ADX/ATR พร้อมกัน) สาเหตุคือ resample("4h", offset=...) ของ pandas ไม่รู้จัก
    # เวลาตลาดปิด (forex/index หยุดเสาร์-อาทิตย์ ต่างจาก crypto ที่เทรด 24/7) พอเจอ gap จะได้
    # แท่ง 4H ที่มีแท่งย่อยแค่ 1-3 แท่งแทนที่จะเป็น 4 เต็ม OHLC ของแท่งนั้นเลยไม่ตรงกับที่ MT5/
    # TradingView คำนวณจริง (ทั้งคู่รู้จักเวลาตลาดปิด ไม่ตัดแบบ naive) แล้วความบิดเบี้ยวนี้
    # สะสมต่อใน ATR/ADX ที่เป็น smoothed indicator (มี "ความจำ" ยาวข้ามหลายแท่ง)
    # วัดจริงกับ USDJPYm 500 แท่ง 1H: เจอแท่ง 4H พร่อง 52/173 แท่ง (30%!)
    #
    # ข้อยกเว้น: แท่งตัวสุดท้าย (live ที่ยังไม่ปิด หรือแท่ง ณ as_of ที่ query ระหว่างแท่งกำลังก่อตัว)
    # มีแท่งย่อยไม่ครบ 4 ได้ "โดยปกติ" ไม่ใช่ gap — ต้องเก็บไว้เสมอ ไม่งั้น caller ที่คาดหวัง
    # แท่งสุดท้ายเป็นแท่ง live จะพัง (เช่น get_regime ตัด iloc[-1] ออกเองตอนหา closed_idx)
    #
    # ยังไม่การันตีตรงกับ MT5/TradingView 100% เพราะ MT5 เองน่าจะมีกฎตัดแท่งช่วง gap ที่ไม่
    # เหมือน pandas เป๊ะ แค่ลดความบิดเบี้ยวจากแท่งพร่องที่ชัดเจนที่สุดออกไปก่อน
    g = (h1.set_index("time")
           .resample("4h", offset=f"{offset}h")
           .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), tick_volume=("tick_volume", "sum"),
                n_sub_bars=("open", "count"))
           .dropna())
    live_bar = g.iloc[[-1]]                    # เก็บแท่งสุดท้ายไว้ก่อนกรอง (ป้องกันไม่ครบ 4 โดยชอบธรรม)
    g = g[g["n_sub_bars"] == 4]
    if live_bar.index[0] not in g.index:
        g = pd.concat([g, live_bar])
    g = g.drop(columns="n_sub_bars").sort_index().reset_index()
    return g.iloc[-bars:].reset_index(drop=True)
