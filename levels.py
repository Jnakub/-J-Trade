"""
levels.py — แนว (Key Level) สำหรับ Pullback แบบใช้แนวแทน ADX (config.PULLBACK_LEVEL_INSTEAD_OF_ADX · 2026-10-22 ผู้ใช้)

นิยาม (ผู้ใช้เลือก 2026-10-22 หลังคัดกรอง 5 รอบ · ตัวเลขที่ config.PULLBACK_LEVEL_INSTEAD_OF_ADX):
  A = ยอด/ก้นบน 1D (fractal 5/5) ที่ราคากลับตัวออกไป ≥ LEVEL_A_PROMINENCE_ATR × ATR1D ภายใน 10 วัน
      ใช้ได้หลังครบ 10 วันนั้น (ไม่มองอนาคต) · อายุ ≤ LEVEL_A_MAX_AGE_DAYS วัน
  C = high/low ของสัปดาห์ก่อน (จันทร์-อาทิตย์ที่จบแล้ว) + เดือนก่อน (เดือนปฏิทินที่จบแล้ว)
  "ที่แนว" = ราคาห่างแนวใดแนวหนึ่ง ≤ LEVEL_TOL_ATR × ATR1D
คำนวณจากแท่ง 1D ที่ปิดแล้วเท่านั้น (ตัดแถวท้ายของ df_1d = แท่งที่ยังไม่ปิด แบบเดียวกับ get_trend_bias)
ไม่ import MetaTrader5 — เทสต์บน Mac ได้
"""
import pandas as pd

from indicators import calc_atr

LEVEL_A_PROMINENCE_ATR = 2.0
LEVEL_A_MAX_AGE_DAYS = 60
LEVEL_TOL_ATR = 0.3


def key_levels(df_1d: pd.DataFrame) -> tuple[list[float], float | None]:
    """(ราคาแนว A + C, ATR1D ล่าสุด) ณ เวลาของแถวท้าย df_1d (แท่งที่ยังไม่ปิด)"""
    if df_1d is None or len(df_1d) < 40:
        return [], None
    now = pd.Timestamp(df_1d["time"].iloc[-1])            # ต้นวันนี้ = เวลาที่แท่งปิดล่าสุดจบ
    d = df_1d.iloc[:-1].reset_index(drop=True)
    atr = calc_atr(d, 14)
    h, l, t = d["high"].to_numpy(), d["low"].to_numpy(), d["time"]
    out = []
    for k in range(5, len(d) - 10):
        avail = t.iloc[k + 10] + pd.Timedelta(days=1)
        if (now - avail).days > LEVEL_A_MAX_AGE_DAYS:
            continue
        a = LEVEL_A_PROMINENCE_ATR * atr.iloc[k]
        if h[k] == h[k - 5:k + 6].max() and h[k] - l[k:k + 11].min() >= a:
            out.append(float(h[k]))
        if l[k] == l[k - 5:k + 6].min() and h[k:k + 11].max() - l[k] >= a:
            out.append(float(l[k]))
    di = d.set_index("time")
    wk = di.resample("W-SUN", label="right", closed="right").agg(high=("high", "max"), low=("low", "min")).dropna()
    wk = wk[wk.index + pd.Timedelta(days=1) <= now].tail(1)
    mo = di.resample("MS").agg(high=("high", "max"), low=("low", "min")).dropna()
    mo = mo[mo.index + pd.offsets.MonthBegin(1) <= now].tail(1)
    for f in (wk, mo):
        if len(f):
            out += [float(f["high"].iloc[0]), float(f["low"].iloc[0])]
    return out, float(atr.iloc[-1])


def divergence_at_level(regime_info: dict, df_1d: pd.DataFrame) -> bool:
    """swing ที่ 2 ของ divergence (ก้นของ bullish / ยอดของ bearish) อยู่ที่แนวไหม · ไม่มี divergence = False"""
    pts = (regime_info.get("divergence") or {}).get("points") or []
    if len(pts) < 2:
        return False
    lv, atr = key_levels(df_1d)
    if not lv or not atr:
        return False
    price = float(pts[1][1])
    return any(abs(x - price) <= LEVEL_TOL_ATR * atr for x in lv)
