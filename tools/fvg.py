"""
fvg.py — Fair Value Gap (FVG) — 2026-10-19 · คำขอผู้ใช้

🔻 สถานะปัจจุบัน: **ตัวตรวจจับอย่างเดียว ยังไม่มีกลยุทธ์/scheduler/replay ตัวไหนเรียกใช้** — ระบบจริงไม่เปลี่ยน

นิยาม (แท่ง 3 แท่งติดกัน: แท่งที่ 1 = i-2 · แท่งที่ 2 = i-1 · แท่งที่ 3 = i):
  Bullish FVG : low แท่งที่ 3 > high แท่งที่ 1  -> ช่อง = high แท่งที่ 1 .. low แท่งที่ 3
  Bearish FVG : high แท่งที่ 3 < low แท่งที่ 1  -> ช่อง = high แท่งที่ 3 .. low แท่งที่ 1
Timeframe: แท่ง 1D (คำขอผู้ใช้) · ไส้แตะกันพอดี (เท่ากัน) ไม่นับเป็นช่อง · ผู้เรียกส่งเฉพาะแท่งที่ปิดแล้ว (ตัดแท่งที่ยังวิ่งอยู่ออกเอง)
"""
import os as _os, sys as _sys  # noqa: E401
_sys.path[:0] = [_os.path.dirname(_os.path.abspath(__file__)),                     # tools/ (wine ._pth ไม่ใส่โฟลเดอร์สคริปต์ให้)
                 _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))]   # โฟลเดอร์หลัก (python3 บน Mac ไม่เห็นเอง)
import pandas as pd

from indicators import merge_sunday   # บ้านจริงอยู่ indicators.py (Bias ตัวจริงใช้ด้วย) · re-export ให้ fvg.merge_sunday ใช้ได้เหมือนเดิม


def find_fvgs(df: pd.DataFrame) -> list[dict]:
    """หา FVG ทุกช่องใน df (ต้องมีคอลัมน์ high/low · time ถ้ามี) เรียงตามเวลา
    คืน list ของ dict: i (index แท่งที่ 3) · time · type ("Bullish"/"Bearish") · bottom · top"""
    hi, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    has_time = "time" in df.columns
    out = []
    for i in range(2, len(df)):
        if lo[i] > hi[i - 2]:
            t, bottom, top = "Bullish", hi[i - 2], lo[i]
        elif hi[i] < lo[i - 2]:
            t, bottom, top = "Bearish", hi[i], lo[i - 2]
        else:
            continue
        out.append({"i": i, "time": df["time"].iloc[i] if has_time else None, "type": t,
                    "bottom": float(bottom), "top": float(top)})
    return out


# Bias 1D แบบ FVG (ไอเดียผู้ใช้ 2026-10-19 · ยังไม่มีในระบบจริง · replay --bias-fvg) — กติกาที่ตกลงกัน:
#   นับ FVG ทุกช่องตามลำดับที่เกิด (วันที่ไม่มีช่องคั่นได้ · ไม่สนว่าช่องถูกเติมแล้วหรือยัง)
#   Bullish ครบ BIAS_STREAK ช่องติดกัน (ไม่มี Bearish คั่น) -> Long · Bearish ครบ -> Short
#   เจอช่องสวนทางช่องเดียว = Bias ค้างทิศเดิม จนกว่าฝั่งตรงข้ามจะครบ BIAS_STREAK ติดกัน
BIAS_STREAK = 2

# รวมแท่ง 1D วันอาทิตย์เข้ากับแท่งถัดไป (วันจันทร์) ก่อนหา FVG — replay --bias-fvg-sunmerge ตั้งเป็น True (2026-10-19)
# เหตุผล (วัด 799 แท่ง 1D): symbol ที่ไม่เทรดเสาร์ มีแท่งอาทิตย์ = ไม่กี่ชั่วโมงหลังตลาดเปิด ยาวแค่ 0.14-0.70 เท่าของแท่งปกติ
# (XAU 0.26 · HK50 0.14) และเปิดกระโดดจากวันศุกร์ -> ช่อง FVG 39-59% มีแท่งอาทิตย์อยู่ในชุด (XAU 59%)
# เคสที่หลอก Bias: XAU 2026-06-14 (อา.) เปิด 4270 เหนือ high ศุกร์ 4235.7 -> Bullish 2 ช่อง -> Long ตอนขาลง
# crypto (มีแท่งวันเสาร์ = เทรดจริงทั้งสุดสัปดาห์) ไม่แตะ · UKOIL ไม่มีแท่งอาทิตย์อยู่แล้ว
MERGE_SUNDAY = False


# timeframe ที่ใช้หา FVG ของ Bias: "1D" (ค่าเดิม) หรือ "1W" (สร้างจากแท่ง 1D · replay --bias-fvg-1w · คำขอผู้ใช้ 2026-10-19
# หลัง 1D + รวมอาทิตย์ยัง −38.68R เพราะ Bias ยังพลิกบ่อยกว่า trend flip ~50%)
TIMEFRAME = "1D"


def _week_key(t: pd.Series, crypto: bool) -> pd.Series:
    """สัปดาห์ จันทร์-อาทิตย์ · ไม่ใช่ crypto: แท่งวันอาทิตย์ = ไม่กี่ชั่วโมงแรกของสัปดาห์ใหม่ -> นับเข้าสัปดาห์ถัดไป"""
    t = pd.to_datetime(t)
    if not crypto:
        t = t + pd.to_timedelta((t.dt.dayofweek == 6).astype(int), unit="D")
    return t.dt.to_period("W-SUN")


def to_weekly(df_1d: pd.DataFrame) -> pd.DataFrame:
    """แท่ง 1W ที่ "จบสัปดาห์แล้ว" จากแท่ง 1D ที่รวมแท่ง live ไว้ท้ายสุด (รูปเดียวกับที่ get_trend_bias รับ)
    สัปดาห์ของแท่ง live ถูกตัดทั้งสัปดาห์ = ไม่มองล่วงหน้า · crypto = มีแท่งวันเสาร์"""
    t = pd.to_datetime(df_1d["time"])
    crypto = bool((t.dt.dayofweek == 5).any())
    wk = _week_key(t, crypto)
    closed = df_1d[(wk < wk.iloc[-1]).to_numpy()]
    if closed.empty:
        return closed.reset_index(drop=True)
    g = closed.groupby(wk[(wk < wk.iloc[-1]).to_numpy()].to_numpy(), sort=True)
    return pd.DataFrame({"time": g["time"].first(), "open": g["open"].first(), "high": g["high"].max(),
                         "low": g["low"].min(), "close": g["close"].last()}).reset_index(drop=True)


def fvg_bias(symbol: str, df_1d: pd.DataFrame, streak: int = BIAS_STREAK) -> tuple[str | None, str]:
    """ลายเซ็นเดียวกับ scoring.get_trend_bias (symbol, df_1d) -> (bias, source) — สลับแทนกันได้ตรงๆ
    ตัดแท่ง 1D สุดท้าย (ยังไม่ปิด) แบบเดียวกับของเดิม · None = ยังไม่เคยมีฝั่งไหนครบ streak ในข้อมูลที่ส่งมา
    MERGE_SUNDAY = True -> รวมแท่งอาทิตย์เข้ากับวันจันทร์หลังตัดแท่ง live · TIMEFRAME = "1W" -> หาบนแท่งสัปดาห์ที่จบแล้ว"""
    if TIMEFRAME == "1W":
        closed = to_weekly(df_1d)
    else:
        closed = df_1d.iloc[:-1].reset_index(drop=True)
        if MERGE_SUNDAY:
            closed = merge_sunday(closed)
    bias, last, run = None, None, 0
    for g in find_fvgs(closed):
        run = run + 1 if g["type"] == last else 1
        last = g["type"]
        if run >= streak:
            bias = "Long" if last == "Bullish" else "Short"
    return bias, (f"fvg{streak}" if bias else "fvg_not_ready")


if __name__ == "__main__":
    # ดูช่องล่าสุดของ symbol จริง: ./run_wine.sh tools/fvg.py XAUUSDm (ค่าเริ่มต้น 1D 300 แท่ง · ใส่ 4H/1H ต่อท้ายได้)
    import sys
    from mt5_connect import connect
    from bars import get_bars
    sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1D"
    nb = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    if not connect():
        sys.exit("ต่อ MT5 ไม่ได้")
    df = get_bars(sym, tf, nb + 1).iloc[:-1].reset_index(drop=True)   # ตัดแท่งที่ยังไม่ปิด
    gs = find_fvgs(df)
    print(f"{sym} {tf} {len(df)} แท่ง: FVG {len(gs)} ช่อง "
          f"(Bullish {sum(g['type'] == 'Bullish' for g in gs)} · Bearish {sum(g['type'] == 'Bearish' for g in gs)})")
    for g in gs[-25:]:
        print(f"  {str(g['time'])[:16]}  {g['type']:<8} {g['bottom']:>12.5f} - {g['top']:<12.5f}")
