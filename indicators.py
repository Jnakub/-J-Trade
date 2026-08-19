"""
indicators.py — ATR/DI/ADX ที่ swing.py, regime_check.py และ backtest_exit_compare.py
ใช้ร่วมกัน แยกมาไว้ที่นี่เพราะ regime_check.py import จาก scoring.py/swing.py อยู่แล้ว ทำให้
swing.py import กลับจาก regime_check.py ไม่ได้ (circular) — ไฟล์นี้ไม่ import โมดูลอื่นใน
โปรเจกต์เลย (มีแค่ pandas) เพื่อให้ทุกไฟล์ import ได้อิสระโดยไม่ชนกัน
"""
import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)

    2026-08-19: เปลี่ยนจาก ewm(span=period) เป็น ewm(alpha=1/period) — Wilder's RMA ตัวจริง
    (alpha=1/14≈0.071) แทน EMA มาตรฐาน (alpha=2/15≈0.133, ไวกว่า RMA เกือบ 2 เท่า) ที่ใช้มา
    ก่อนหน้านี้ — เดิม trend_flip.py เขียนเตือนตัวเองไว้ล่วงหน้าแล้วว่าต่างจาก TradingView
    default ประมาณนี้ (ดู comment เดิมของไฟล์นั้น) แต่ไม่เคยมีใครวัดจริงว่ากระทบขนาดไหน

    วัดจริง 2026-08-19 (ATR(14) บน 1D เทียบ TradingView, สูตรเดิม vs RMA):
      BTCUSDm  -8.16% -> +2.02%   ETHUSDm -14.26% -> +1.74%   XRPUSDm -15.79% -> -1.57%
      (crypto ตรงเกือบเป๊ะทันทีหลังแก้ — ยืนยันว่าสูตรคือสาเหตุหลัก ไม่ใช่ feed/gap)
    XAUUSDm/USDJPYm/US500m/EURUSDm/GBPUSDm ยังเหลือ residual ~12-16% หลังแก้สูตรแล้ว — ตรวจ
    แล้วไม่ใช่ day-boundary offset (ทดสอบ 0-22 ชม. ไม่มีผล) และผู้ใช้ยืนยันจอ TradingView ตั้ง
    Smoothing=RMA ถูกต้องอยู่แล้ว เหลือคำอธิบายเดียวคือ feed คนละเจ้า (Exness vs ที่ TradingView
    ใช้) ซึ่งแก้ด้วยโค้ดไม่ได้

    ⚠️ กระทบทุกจุดที่ใช้ ATR ทั้งระบบพร้อมกัน (จุดเดียวจริงตามที่ตั้งใจไว้แต่แรก): SL buffer,
    swing tolerance, structure break buffer, trend_flip bias (k×ATR), ATR trailing SL,
    รายงาน exit_monitor — ดู bars.BAR_OFFSET_H ที่ sweep ใหม่ตามสูตรนี้ด้วย (US500m/EURUSDm
    offset เปลี่ยนเพราะ sweep เดิมใช้สูตร ATR ผิดตัดสินใจ)"""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calc_di(df: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series]:
    """DI+ / DI− (Wilder) — วัด "แรงกดดันเชิงทิศทาง" แยกขาขึ้น/ขาลง คืน (plus_di, minus_di)

    DI+ > DI−  = แรงซื้อเหนือกว่า (ขาขึ้น)   |   DI− > DI+ = แรงขายเหนือกว่า (ขาลง)

    period=20 ตาม ADX_PERIOD ที่ regime_check ใช้ (ไม่ใช่ default 14 ของ Wilder) — ยังไม่ได้
    sweep หาค่าที่เหมาะสมแยกต่างหากแบบที่ทำกับ trend_flip k ถ้าจะจูนควรวัดก่อน
    """
    high, low, close = df["high"], df["low"], df["close"]
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean().replace(0, 1e-12)
    plus_di  = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    return plus_di, minus_di


def calc_adx(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """ADX (Wilder) — ความแรงของเทรนด์ (ไม่สนทิศทาง) ต่อยอดจาก calc_di"""
    plus_di, minus_di = calc_di(df, period)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False).mean()
