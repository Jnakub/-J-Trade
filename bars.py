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

# BAR_OFFSET_H — เลื่อนขอบแท่ง 4H (ชม.) ให้ ATR/ADX ตรงจอ TradingView ของผู้ใช้ · ต้องวัดราย symbol (ไม่ผูก asset class)
#   ⚠️ วัดด้วย path เดียวกับที่ระบบใช้ (resample + กรองแท่งพร่อง) และ ATR สูตร Wilder RMA · residual ~1-7% เหลือจาก feed คนละเจ้า
#   วิธีที่ใช้ตอนเพิ่ม symbol: ถามผู้ใช้ว่าแท่ง 4H บนจอเริ่มกี่โมง (เวลาไทย 0 = 03/07/11… · 1 = 00/04/08… · 2 = 01/05/09… · 3 = 02/06/10…)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "bars.py — BAR_OFFSET_H sweep (2026-08-18..20)"
BAR_OFFSET_H = {
    "BTCUSDm": 0,
    "XAUUSDm": 1,   # 2026-09-09: 2 -> 1 ยืนยันกับจอผู้ใช้ (ADX 20 บน 4H = 23.8, 6 แท่ง/วัน)
                    # offset=1 ให้ 23.74 ส่วน 0/2/3 ให้ 26.70 / 19.46 / 22.41
                    # ⚠️ ต้องคู่กับ DAILY_GAP_SYMBOLS ด้านล่าง ไม่งั้นยังได้ 5 แท่ง/วันอยู่ดี
    "ETHUSDm": 0,
    "XRPUSDm": 0,
    "USDJPYm": 1,
    "US500m":  0,
    "EURUSDm": 3,
    "GBPUSDm": 1,
    # 2026-09-19: HK50m — **ยืนยันกับจอผู้ใช้แล้วว่า offset=1** (เช็คลิสต์ข้อ 4)
    #   offset 0 -> ADX 32.06   ·   **1 -> ADX 35.22**   ·   2 -> 27.67   ·   3 -> 31.03
    #   ผู้ใช้อ่านจากจอได้ ~35.2
    # 🔴 **แกว่ง 7.55 จุดตาม offset = มากที่สุดในระบบ** (US500 5.00 · EUR 4.22 · GBP 2.54
    #   · UKOIL 2.40) และช่วง 27.7-35.2 นี้คร่อมทั้งเส้น ADX_GRAY_HIGH (22) และ
    #   ADX_STRONG_TREND (40) ได้ในบางจังหวะ = ตั้งผิดแล้วระบบเห็น regime คนละแบบกับจอ
    #   ถ้าวันหนึ่งสงสัยว่า HK50 ตัดสิน regime แปลกๆ ให้กลับมาตรวจค่านี้เป็นอย่างแรก
    # ไม่ใส่ DAILY_GAP_SYMBOLS — HK50 มีแท่ง 1H ครบ 23 ชม./วัน (หายแค่ชั่วโมง 21)
    # ต่างจาก UKOIL ที่หยุด 3 ชม. จึงไม่มีปัญหาบัคเก็ตเหลือแท่งย่อยไม่ครบ
    "HK50m":   1,
    # 2026-09-28: GBPCHFm — **ยืนยันกับจอผู้ใช้แล้วว่า offset=0** (แท่งที่ปิดล่าสุด ณ 22:06 ไทย)
    #   **0 -> 20.92** · 1 -> 20.00 · 2 -> 21.84 · 3 -> 23.93 (แกว่ง 3.9 จุด ทั้งช่วงคร่อมเส้น 22)
    # 🔴 copy_rates ครั้งแรกหลัง select อาจคืนแท่งค้างหลายชั่วโมง — ตอนวัดต้องขอซ้ำจนแท่งล่าสุดถึงปัจจุบัน
    "GBPCHFm": 0,
    # 2026-09-29: AUDNZDm — **ผู้ใช้ยืนยันกับจอว่า offset=1** (ADX ทั้ง 4 offset ห่างกันไม่ถึง 1.3 จุด
    #   14.66-15.72 ตัดสินจากเวลาเริ่มแท่งบนจอแทน) · ⚠️ ต่างจาก GBPCHF (0) ทั้งที่เป็น FX เหมือนกัน
    "AUDNZDm": 1,
    # 2026-09-18: UKOILm — **ยืนยันกับจอผู้ใช้แล้วว่า offset=1** (เช็คลิสต์ข้อ 4)
    #   offset 0 -> 6 แท่ง/วัน ADX 22.64   ·   **1 -> ADX 23.64 (5 แท่ง) / 22.94 (6 แท่ง)**
    #   offset 2 -> 4 แท่ง ADX 24.94       ·   3 -> 4 แท่ง ADX 22.20
    # ✅ **ผู้ใช้อ่านจากจอได้ ~23.6 = ค่าที่ตรงกับ "ไม่ใส่ DAILY_GAP_SYMBOLS" (5 แท่ง/วัน)**
    #   ถามแยกแล้วเพราะที่ offset=1 มีสองค่าให้เลือก: 23.64 (5 แท่ง) vs 22.94 (6 แท่ง)
    # 🔴 ผลที่ตามมาที่ต้องรู้ตัว: UKOIL หยุด **3 ชม./วัน (21:00-23:59)** ต่างจาก XAU/US500
    #   ที่หยุดแค่ชั่วโมงเดียว บัคเก็ต 21:00-00:59 จึงเหลือแท่งย่อยไม่ครบ 4 และถูก
    #   get_aligned_4h ตัดทิ้งทุกวัน = ระบบไม่เห็นช่วง 21:00-00:59 เลย
    #   **ไม่ใช่บั๊ก** — เป็นผลของการเลือกให้ตรงกับจอ และช่วงที่หายคือช่วงที่ตลาดปิดจริง 3 ใน 4 ชม.
    #   ถ้าวันหนึ่งพบว่ามันทำให้พลาดสัญญาณ ให้ใส่ UKOILm ใน DAILY_GAP_SYMBOLS แล้ว ADX จะ
    #   กลายเป็น 22.94 (6 แท่ง/วัน) ซึ่ง **ไม่ตรงกับจอ** — ต้องเลือกอย่างใดอย่างหนึ่ง
    "UKOILm": 1,
}

# symbol ที่โบรกพักตลาดกลางวัน (ไม่ใช่แค่สุดสัปดาห์) — ยกเว้นจากตัวกรอง "แท่งย่อยต้องครบ 4"
# ใน get_aligned_4h เพราะ gap ประจำวันทำให้ bucket เดิมโดนตัดทิ้งทุกวัน = ตาบอดอย่างเป็นระบบ
# วิธีเช็คว่า symbol ใหม่ต้องอยู่ในลิสต์นี้ไหม: นับแท่ง 1H แยกตามชั่วโมงของวัน ถ้ามีชั่วโมงไหน
# หายเกือบทุกวัน (XAUUSDm = 21:00) และ symbol นั้น offset != 0 -> ต้องใส่
# วัดแล้ว: XAUUSDm หาย 21:00 ทุกวัน / US500m หาย 21:00 เหมือนกันแต่ offset=0 (ใช้แท่ง native
# ของ MT5 ไม่ผ่านตัวกรองนี้เลย) / USDJPYm-EURUSDm-GBPUSDm ไม่มีชั่วโมงหายประจำวัน
DAILY_GAP_SYMBOLS = {"XAUUSDm"}


# วินาทีต่อแท่งของแต่ละ timeframe — ใช้คำนวณ "เวลาปิดแท่ง" (time + TF_SECONDS) ใน as_of mode
TF_SECONDS = {"1D": 86400, "4H": 14400, "1H": 3600, "M15": 900}

# TF ย่อยที่ใช้ประกอบ "แท่งที่ยังไม่ปิด" ณ as_of (ดู _partial_bar) — 1D/4H ประกอบจาก 1H,
# 1H ประกอบจาก M15 (ละเอียดกว่านี้ไม่คุ้ม MT5 call ที่เพิ่ม)
PARTIAL_SRC = {"1D": "1H", "4H": "1H", "1H": "M15"}


def _copy_rates(symbol: str, tf_name: str, bars: int, as_of=None) -> pd.DataFrame:
    """ดึงแท่งดิบจาก MT5 (รองรับ M15 ที่ไม่มีใน config.MT5_TIMEFRAMES ด้วย)"""
    import MetaTrader5 as mt5

    tf = MT5_TIMEFRAMES.get(tf_name) or {"M15": mt5.TIMEFRAME_M15}[tf_name]
    if as_of is None:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    else:
        rates = mt5.copy_rates_from(symbol, tf, as_of, bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงข้อมูล {symbol} {tf_name} ไม่ได้  [{code}] {msg}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def _partial_bar(symbol: str, tf_name: str, bar_start, as_of) -> dict | None:
    """ประกอบแท่งที่ "กำลังก่อตัว" ณ as_of จากแท่ง TF ย่อยที่ปิดแล้วเท่านั้น
    คืน None ถ้ายังไม่มีแท่งย่อยปิดเลย (as_of อยู่ตรงขอบแท่งพอดี) หรือดึง TF ย่อยไม่ได้"""
    src = PARTIAL_SRC.get(tf_name)
    if src is None:
        return None
    sub_dur = pd.Timedelta(seconds=TF_SECONDS[src])
    need    = int(TF_SECONDS[tf_name] / TF_SECONDS[src]) + 4
    try:
        sub = _copy_rates(symbol, src, need, as_of)
    except RuntimeError:
        return None
    sub = sub[(sub["time"] >= bar_start) & (sub["time"] + sub_dur <= as_of)]
    if sub.empty:
        return None
    return {"time": bar_start, "open": sub["open"].iloc[0], "high": sub["high"].max(),
            "low": sub["low"].min(), "close": sub["close"].iloc[-1],
            "tick_volume": float(sub["tick_volume"].sum())}


def get_bars(symbol: str, tf_name: str, bars: int = 100, as_of=None) -> pd.DataFrame:
    """จุดเดียวทั้งระบบสำหรับดึงแท่งราคา — scoring.get_ohlcv() เป็นแค่ wrapper ของตัวนี้

    as_of=None (รันสด) = เหมือนเดิมทุกประการ: แท่งล่าสุดจาก MT5 โดยแท่งท้ายสุดคือแท่งที่ยัง
    ไม่ปิด (live)

    as_of=datetime (backtest) = **คืนเฉพาะข้อมูลที่มีอยู่จริง ณ วินาทีนั้น** คือแท่งที่ปิดแล้ว
    (time + TF_SECONDS <= as_of) บวกแท่งที่กำลังก่อตัวซึ่งประกอบขึ้นใหม่จาก TF ย่อยที่ปิดแล้ว
    (ดู _partial_bar) ให้หน้าตาเหมือนที่ MT5 คืนตอนรันสดเป๊ะ

    🔴 2026-08-26 นี่คือจุดที่แก้บั๊ก lookahead ที่กระทบ backtest ทุกตัวในระบบ: mt5.copy_rates_from
    (สิ่งที่ฟังก์ชันนี้เคยเรียกตรงๆ) คืนแท่งที่ "ครอบ" as_of มาให้แบบ **ปิดสมบูรณ์แล้ว** ทำให้
    backtest เห็นอนาคตของแท่งนั้นทั้งแท่ง — 4 ชม.บนกราฟ 4H และเต็มวันบนกราฟ 1D (ราคาปิดของ
    วันตัวเองก่อนตัดสินใจ! กระทบ Trend 1D / OBV 1D / EMA50 1D ตรงๆ) ยืนยันด้วยการทดสอบจริงกับ
    MT5 เครื่องนี้: copy_rates_from(as_of=16:00) คืนแท่ง 4H ของ 16:00 มาทั้งแท่ง และ 1H ของ
    16:00 มาทั้งแท่ง ผลคือ entry price ใน backtest = ราคาในอนาคตอีก 1 ชม.
    ตัวเลข backtest ทั้งหมดที่รันก่อนวันนี้จึงเทียบกับหลังวันนี้ไม่ได้ (ของเก่าดีเกินจริง)"""
    if as_of is None:
        return (get_aligned_4h(symbol, bars, None) if tf_name == "4H"
                else _copy_rates(symbol, tf_name, bars, None))

    as_of = pd.Timestamp(as_of)
    dur   = pd.Timedelta(seconds=TF_SECONDS[tf_name])
    raw   = (get_aligned_4h(symbol, bars + 2, as_of) if tf_name == "4H"
             else _copy_rates(symbol, tf_name, bars + 2, as_of))

    out     = raw[raw["time"] + dur <= as_of].tail(bars).reset_index(drop=True)
    forming = raw[(raw["time"] <= as_of) & (raw["time"] + dur > as_of)]
    if forming.empty or out.empty:
        return out

    bar_start = forming["time"].iloc[0]
    part = _partial_bar(symbol, tf_name, bar_start, as_of)
    if part is None:
        # as_of ตรงขอบแท่งพอดี (ยังไม่มีแท่งย่อยปิดเลย) — ใส่แท่งความกว้างศูนย์ที่ราคาล่าสุด
        # แทน ให้รูปร่างเฟรมเหมือนตอนรันสดเสมอ (โค้ดหลายที่ตัด iloc[-1] ทิ้งเองเพราะถือว่า
        # แท่งท้ายคือแท่ง live เช่น regime_check.get_regime / get_trend_bias)
        last = out["close"].iloc[-1]
        part = {"time": bar_start, "open": last, "high": last, "low": last,
                "close": last, "tick_volume": 0.0}

    row = pd.DataFrame([part])
    for col in out.columns:            # spread/real_volume ฯลฯ ที่ MT5 แถมมา — ยกค่าล่าสุดมาใส่
        if col not in row.columns:
            row[col] = out[col].iloc[-1]
    return pd.concat([out, row[out.columns]], ignore_index=True).tail(bars).reset_index(drop=True)


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

    # เผื่อแท่ง 1H: ขั้นต่ำทางทฤษฎีคือ 4 แท่ง/1 แท่ง 4H แต่ต้องเผื่อ gap ช่วงตลาดปิดเยอะกว่านั้น
    # มาก เพราะแท่ง 4H ที่คร่อม gap จะถูก gap-filter ด้านล่างตัดทิ้ง — 2026-08-18: เดิมใช้
    # bars*4+50 ไม่พอ US500m ขอ 210 แท่งได้จริงแค่ 193 (หาย 17) เพราะ index/gold มี gap ถึง 22
    # ครั้งใน 500 แท่ง 1H (ปิดรายวัน ไม่ใช่แค่เสาร์-อาทิตย์แบบ forex ที่มีแค่ 4 ครั้ง) ตัวเลข
    # x8+200 ทดสอบแล้วได้ครบ 209/210 ทุก symbol รวม US500m ที่ gap หนักสุด
    h1_bars = bars * 8 + 200
    if as_of is None:
        rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES["1H"], 0, h1_bars)
    else:
        rates = mt5.copy_rates_from(symbol, MT5_TIMEFRAMES["1H"], as_of, h1_bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงข้อมูล {symbol} (1H สำหรับ resample 4H) ไม่ได้  [{code}] {msg}")

    h1 = pd.DataFrame(rates)
    h1["time"] = pd.to_datetime(h1["time"], unit="s")

    # กรองแท่ง 4H ที่ประกอบจาก 1H ไม่ครบ 4 แท่งทิ้ง (แท่งพร่องช่วงตลาดปิดทำ ATR/ADX เพี้ยนสะสม) · ยกเว้นแท่ง live ตัวสุดท้าย
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "bars.py — กรองแท่ง 4H พร่อง (2026-08-18)"
    g = (h1.set_index("time")
           .resample("4h", offset=f"{offset}h")
           .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), tick_volume=("tick_volume", "sum"),
                n_sub_bars=("open", "count"))
           .dropna())
    # ยกเว้น symbol ใน DAILY_GAP_SYMBOLS (ตลาดปิด 1 ชม. ทุกวัน = 5 แท่ง/วันถ้ากรอง) · ⚠️ เคยถอดตัวกรองทั้งระบบแล้วแย่ลง (181 ไม้ +20.26 -> +16.45R)
    # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "bars.py — DAILY_GAP_SYMBOLS / ถอดตัวกรอง (2026-09-09)"
    live_bar = g.iloc[[-1]]
    g = g[(g["n_sub_bars"] == 4) | (symbol in DAILY_GAP_SYMBOLS)]
    if live_bar.index[0] not in g.index:
        g = pd.concat([g, live_bar])
    g = g.drop(columns="n_sub_bars").sort_index().reset_index()
    out = g.iloc[-bars:].reset_index(drop=True)

    # เตือนถ้า gap กินจนได้แท่งไม่ครบตามที่ขอ — เคยเกิดเงียบๆ กับ US500m (ขอ 210 ได้ 193) แล้ว
    # ไม่มีใครรู้ จนไปเจอตอนไล่ debug ATR ไม่ตรง TradingView: แท่งหายกระทบ EMA warm-up ของ
    # ATR/ADX, หน้าต่าง swing detection และทุกที่ที่ index ด้วย iloc[-N] โดยคาดว่าได้ครบ
    if len(out) < bars:
        print(f"  [bars] ⚠️ {symbol}: ขอแท่ง 4H {bars} ได้จริง {len(out)} "
              f"(gap-filter ตัดทิ้ง — เพิ่ม h1_bars headroom ถ้าเจอบ่อย)")
    return out
