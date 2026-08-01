"""
indicators.py — ATR/DI/ADX ที่ swing.py, regime_check.py และ backtest_exit_compare.py
ใช้ร่วมกัน แยกมาไว้ที่นี่เพราะ regime_check.py import จาก scoring.py/swing.py อยู่แล้ว ทำให้
swing.py import กลับจาก regime_check.py ไม่ได้ (circular) — ไฟล์นี้ไม่ import โมดูลอื่นใน
โปรเจกต์เลย (มีแค่ pandas) เพื่อให้ทุกไฟล์ import ได้อิสระโดยไม่ชนกัน
"""
import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)"""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


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
