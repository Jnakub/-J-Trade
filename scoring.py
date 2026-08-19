import sys
import os
from datetime import datetime
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import (
    WEIGHT_TREND_1D, WEIGHT_OBV_1D,
    WEIGHT_TREND_4H, WEIGHT_OBV_4H,
    WEIGHT_TREND_1H, WEIGHT_DI_1H,
    WEIGHT_MACD, WEIGHT_RR,
    TOTAL_WEIGHT, MT5_TIMEFRAMES, MIN_RR, MIN_RR_HARD_BLOCK, MAX_RR_HARD_BLOCK,
    MIN_SL_DISTANCE_PCT, MAX_TP_DISTANCE_PCT, MIN_SCORE,
)
from mt5_connect import connect, get_tick_or_raise
from swing import (find_sl_from_structure, find_tp_from_fibonacci, check_confirmation,
                   find_swing_lows, find_swing_highs, swing_vol_multiplier, swing_wick_ratio_min,
                   calc_di)
from binance import merge_real_volume
from trend_flip import compute_trend_regime
from bars import BAR_OFFSET_H, get_aligned_4h

# k สำหรับ trend_flip bias ต่อ symbol — มาจาก k-sweep บน 1D (backtest_trend_flip_ksweep.py
# <SYMBOL> 3000 1D, ~7 ปีข้อมูล) เลือกจาก FalseFlip ต่ำสุดในกลุ่มที่เร็วกว่า EMA cross จริง:
#   BTCUSDm: k=0.20  FalseFlip=0/58 (0%)   เร็วกว่า EMA cross เฉลี่ย 9.5 แท่ง  (matched 14 คู่)
#   XAUUSDm: k=0.40  FalseFlip=0/37 (0%)   เร็วกว่า EMA cross เฉลี่ย 12.4 แท่ง (matched 12 คู่)
#   ETHUSDm: k=0.40  FalseFlip=0/53 (0%)   เร็วกว่า EMA cross เฉลี่ย 7.8 แท่ง   (matched 19 คู่)
#            (2026-08-02: k<0.40 ยังมี false flip 2-3 ครั้ง — 0.40 คือจุดแรกที่ 0%)
#   XRPUSDm: k=0.20  FalseFlip=0/64 (0%)   เร็วกว่า EMA cross เฉลี่ย 8.2 แท่ง   (matched 18 คู่)
# (ครั้งแรกที่ทำ XAU ใช้ sample เล็กแค่ 2 คู่เทียบได้ k ไม่น่าเชื่อถือ — รันซ้ำด้วยข้อมูลยาวขึ้น
# แล้วได้ผลที่มั่นใจได้มากกว่านี้ 2026-07-27) symbol ที่ไม่มีในนี้จะได้ bias=None (SKIP ทุกครั้ง
# ใน Scoring — ไม่มี EMA50/200 fallback แล้วตั้งแต่ 2026-08-01) จนกว่าจะมีคน sweep หา k ให้
#
# 2026-08-09: เพิ่ม USDJPYm (k=0.20, FalseFlip=0/62=0%, เร็วกว่า EMA cross เฉลี่ย 16.6 แท่ง,
# matched 24 คู่) — sweep ซ้ำ BTC/XAU/ETH/XRP ด้วยข้อมูลสดพร้อมกัน (methodology เดิมเป๊ะ: 1D,
# 3000 แท่ง) ยืนยันว่าทั้ง 4 ตัวยังได้ค่าเดิมเป๊ะทุกตัว ไม่ต้องปรับ — พร้อมกับแก้บั๊กเกณฑ์เลือก
# k* ใน backtest_trend_flip_ksweep.py (เดิมเลือกจาก false_n ดิบ ไม่ใช่ false_pct — ดู git log)
#
# 2026-08-12: เพิ่ม US500m (S&P500, k=0.20, FalseFlip=1/71=1.4%, เร็วกว่า EMA cross เฉลี่ย
# 10.6 แท่ง, matched 22 คู่) — รัน sweep ผ่าน Wine (MT5 for Mac) 2999 แท่ง 4H ต่างจาก symbol
# อื่นตรงที่ไม่มี k ไหนได้ FalseFlip 0% เลย (ต่ำสุดคือ 1.4% เท่ากันที่ k=0.20/0.25/0.30) เลือก
# 0.20 ตามเกณฑ์อัตโนมัติของสคริปต์ (FalseFlip ต่ำสุด + lag ยังติดลบ) — sample แค่ ~500 วัน
# (3000 แท่ง 4H) ยังไม่ได้สั่นสะเทือนช่วงเวลายาวเหมือน symbol อื่น ควรเฝ้าดูผลเทรดจริงและพร้อม
# ปรับ/ปิดถ้า false flip เกิดถี่กว่าที่ backtest ชี้
#
# 2026-08-17: เพิ่ม EURUSDm (k=0.20, FalseFlip=0/67=0%, เร็วกว่า EMA cross เฉลี่ย 5.2 แท่ง,
# matched 18 คู่) และ GBPUSDm (k=0.20, FalseFlip=1/78=1.3%, เร็วกว่า EMA cross เฉลี่ย 4.6 แท่ง,
# matched 21 คู่ — เหมือนเคส US500m ไม่ได้ 0% แต่ต่ำสุดในกลุ่มที่ทดสอบ 0.20-0.60) sweep 2999 แท่ง 4H
TREND_FLIP_K = {
    "BTCUSDm": 0.20,
    "XAUUSDm": 0.40,
    "ETHUSDm": 0.40,
    "XRPUSDm": 0.20,
    "USDJPYm": 0.20,
    "US500m": 0.20,
    "EURUSDm": 0.20,
    "GBPUSDm": 0.20,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_ohlcv(symbol: str, timeframe, bars: int = 100, as_of: datetime = None) -> pd.DataFrame:
    """as_of=None (ปกติ) = ดึง bars ล่าสุดจากปัจจุบัน — as_of=datetime = ดึง bars ที่ปิดก่อน
    เวลานั้น (ใช้ backtest_score.py จำลอง compute_score ณ เวลาในอดีตแบบเป๊ะ ไม่ต้อง copy
    logic มาเขียนซ้ำ)

    2026-08-18: timeframe 4H ของ symbol ที่มี bars.BAR_OFFSET_H != 0 จะถูกเลื่อนขอบแท่งให้ตรงกับ
    TradingView แทนแท่ง MT5 ดิบ (ดู bars.py) — จุดเดียวนี้ครอบคลุมทุกที่ที่เรียก get_ohlcv/
    get_ohlcv_real ด้วย "4H" ทั้งระบบ (compute_score, structure break, key level, divergence,
    ATR trailing anchor ฯลฯ) ไม่ต้องแก้ทีละจุด — timeframe อื่น (1D/1H) ไม่กระทบเลย"""
    if timeframe == MT5_TIMEFRAMES["4H"] and BAR_OFFSET_H.get(symbol, 0) != 0:
        return get_aligned_4h(symbol, bars, as_of)

    if as_of is None:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    else:
        rates = mt5.copy_rates_from(symbol, timeframe, as_of, bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงข้อมูล {symbol} ไม่ได้  [{code}] {msg}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_ohlcv_real(symbol: str, tf_name: str, bars: int = 100, as_of: datetime = None) -> pd.DataFrame:
    """get_ohlcv + แทนที่ volume ด้วย real volume (Bitstamp/COMEX ตาม merge_real_volume)
    ใช้ตัวนี้เสมอถ้า logic ปลายทางแตะ volume (swing filter, VSA ฯลฯ) — ไม่งั้น BTC จะได้
    tick_volume ของโบรกเกอร์ซึ่งไม่ตรงกับที่ระบบใช้หา SL จริง"""
    df = get_ohlcv(symbol, MT5_TIMEFRAMES[tf_name], bars=bars, as_of=as_of)
    return merge_real_volume(df, symbol, tf_name)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_obv(df: pd.DataFrame) -> pd.Series:
    sign = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (sign * df["tick_volume"]).cumsum()


def calc_macd(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """คืน (macd_line, signal_line, histogram)"""
    macd_line   = ema(df["close"], 12) - ema(df["close"], 26)
    signal_line = ema(macd_line, 9)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_ok_for_direction(macd_line: pd.Series, signal_line: pd.Series,
                          histogram: pd.Series, direction: str) -> bool:
    """
    Short:
      เส้น MACD + Signal อยู่ใต้ 0 → ดู Histogram ลดลง (momentum อ่อน) → PASS
      เส้น MACD + Signal อยู่เหนือ 0 → Histogram < 0 → PASS

    Long:
      เส้น MACD + Signal อยู่เหนือ 0 → ดู Histogram เพิ่มขึ้น (momentum แรง) → PASS
      เส้น MACD + Signal อยู่ใต้ 0 → Histogram > 0 → PASS
    """
    is_short    = direction.capitalize() == "Short"
    macd_val    = macd_line.iloc[-1]
    signal_val  = signal_line.iloc[-1]
    hist_now    = histogram.iloc[-2]   # แท่งที่ปิดแล้ว
    hist_prev   = histogram.iloc[-3]   # แท่งก่อนหน้าที่ปิดแล้ว

    if is_short:
        both_below_zero = macd_val < 0 and signal_val < 0
        if both_below_zero:
            return hist_now < hist_prev   # Histogram ลดลง = momentum อ่อนลง
        else:
            return hist_now < 0           # อยู่เหนือ 0 → แค่ Histogram ติดลบ
    else:
        both_above_zero = macd_val > 0 and signal_val > 0
        if both_above_zero:
            return hist_now > hist_prev   # Histogram เพิ่มขึ้น = momentum แรงขึ้น
        else:
            return hist_now > 0           # อยู่ใต้ 0 → แค่ Histogram ติดบวก


def calc_rr(entry: float, sl: float, tp: float, direction: str) -> float:
    if direction.lower() == "long":
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    return reward / risk if risk > 0 else 0.0


# ---------------------------------------------------------------------------
# Reusable scoring function (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def get_trend_bias(symbol: str, df_1d: pd.DataFrame) -> tuple[str | None, str]:
    """คืน (bias, bias_source) — Long/Short ตาม trend_flip เท่านั้น (ไม่มี EMA50/200
    fallback อีกต่อไป — 2026-08-02 ตัดออก ตามที่ตกลงกันว่า bias ต้องมาจาก trend_flip
    เพียวๆ) ดึงมาเป็นฟังก์ชันแยกเพื่อให้ backtest ภายนอก (เช่น backtest_exit_compare.py)
    รู้ bias ก่อนเรียก compute_score ได้โดยไม่ต้อง copy logic ชุดนี้มาเขียนซ้ำ
    (compute_score เองก็เรียกตัวนี้ภายใน — scheduler.py ก็เรียกตัวนี้แทน EMA ของตัวเองแล้ว)

    bias = None เมื่อ symbol ไม่มีค่า k ใน TREND_FLIP_K เลย หรือ trend_flip ยัง bootstrap
    ไม่พร้อม (ไม่ควรเกิดกับ ~800 แท่ง 1D ในทางปฏิบัติ) — ผู้เรียกต้องถือว่า "ยังไม่มี bias
    ที่เชื่อถือได้" แล้วข้ามรอบสแกนนั้นไปเลย ไม่ใช่เดาทิศทางจาก EMA เหมือนเดิม

    ใช้ df_1d.iloc[:-1] (ตัดแท่งวันนี้ที่ยังไม่ปิด) ไม่ใช่ df_1d เต็ม — เพราะ trend_flip ไวกว่า
    EMA มาก การเทียบ close สดของแท่งที่ยังไม่ปิดกับรัศมี k*ATR ที่แคบ เสี่ยง bias "กระพริบ"
    ไปมาถ้าเรียกหลายครั้งในวันเดียวกันตอนราคาแกว่งใกล้เส้นพอดี"""
    trend_flip_k = TREND_FLIP_K.get(symbol)
    if trend_flip_k is None:
        return None, "no_trend_flip_k"

    closed_1d = df_1d.iloc[:-1].reset_index(drop=True)
    flip_df, _ = compute_trend_regime(closed_1d, k=trend_flip_k)
    flip_regime = flip_df["regime"].iloc[-1]
    if flip_regime == "Bull":
        return "Long", "trend_flip"
    if flip_regime == "Bear":
        return "Short", "trend_flip"
    return None, "bootstrap_not_ready"   # trend_flip ยังสรุปทิศทางไม่ได้


def compute_score(symbol: str, direction: str, entry: float,
                  sl: float = None, tp: float = None,
                  force: bool = False, as_of: datetime = None,
                  df_1d: pd.DataFrame = None) -> tuple[float, list, bool, dict]:
    """Return (total_score, criteria_list, passed, sl_info).
    ถ้าไม่ส่ง sl จะหาจาก swing structure อัตโนมัติ
    ถ้าไม่ส่ง tp จะคำนวณจาก SL × MIN_RR อัตโนมัติ

    as_of=None (ปกติ) = เช็คสด ณ ตอนนี้ (ราคาจาก live tick, bars ล่าสุด)
    as_of=datetime    = จำลองเช็ค ณ เวลานั้นในอดีต (ราคา = close 1H ล่าสุดก่อนเวลานั้น,
        bars ทั้งหมดตัดที่เวลานั้น) — ให้ backtest_score.py เรียกตัวนี้ตรงๆ แทนการ copy
        logic มาเขียนซ้ำ กันผลลัพธ์ backtest เพี้ยนจากของจริงตอนแก้ scoring.py แล้วลืมแก้ตาม

    df_1d: ส่ง df_1d (real volume, bars=800, as_of เดียวกัน) ที่ดึงมาแล้วมาใช้ซ้ำได้ — เช่นจาก
    scheduler.py ที่ต้องดึง df_1d ไปเช็ค get_trend_bias() เองอยู่แล้วก่อนเรียกฟังก์ชันนี้ (2026-08-11:
    เดิมดึง+merge_real_volume ซ้ำสองรอบ ข้อมูลชุดเดียวกันเป๊ะ เสียเวลา/ยิง Bitstamp API ซ้ำโดยไม่
    จำเป็นสำหรับ BTC/ETH/XRP) ถ้าไม่ส่งมาจะดึงเองเหมือนเดิม (ต้อง bars/as_of ตรงกันเป๊ะ ไม่งั้น
    EMA50/200 คลาดเคลื่อนจาก warm-up window ที่ต่างกัน)"""
    is_long = direction.capitalize() == "Long"

    if df_1d is None:
        df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=as_of)
        df_1d = merge_real_volume(df_1d, symbol, "1D")
    df_4h = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=200, as_of=as_of)
    df_4h = merge_real_volume(df_4h, symbol, "4H")
    df_1h = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], as_of=as_of)
    df_1h = merge_real_volume(df_1h, symbol, "1H")   # 2026-07-27: เดิมไม่เคย merge เลย (ต่างจาก
                                                     # 1D/4H) ทำให้ OBV 1H เป็น tick_volume แม้แต่ BTC

    price = df_1h["close"].iloc[-1] if as_of is not None else get_tick_or_raise(symbol).bid

    ema50_1d  = ema(df_1d["close"], 50).iloc[-1]
    ema50_4h  = ema(df_4h["close"], 50).iloc[-1]
    ema50_1h  = ema(df_1h["close"], 50).iloc[-1]

    trend_bias, bias_source = get_trend_bias(symbol, df_1d)
    if trend_bias is None:
        reason = ("ไม่มีค่า k ใน TREND_FLIP_K" if bias_source == "no_trend_flip_k"
                  else "trend_flip ยัง bootstrap ไม่พร้อม")
        raise ValueError(f"หา Bias ไม่ได้ — {reason} ({bias_source}) — ข้ามรอบนี้")
    if trend_bias != direction.capitalize():
        bias_label = "Downtrend" if trend_bias == "Short" else "Uptrend"
        raise ValueError(
            f"Direction ไม่ตรง Bias — กราฟ 1D เป็น {bias_label} ({bias_source}) "
            f"รับแค่ {trend_bias} เท่านั้น"
        )

    # Volume multiplier สำหรับหา Swing — ค่ากลางจาก swing.py (XAU 1.6x, อื่นๆ 1.9x)
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    # หา SL อัตโนมัติจาก swing structure (4H) ถ้าไม่ได้กรอกมา
    sl_info = {}
    if sl is None:
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # หา TP อัตโนมัติจาก Fibonacci (4H) ถ้าไม่ได้กรอกมา
    fib_info = {}
    used_fallback_tp = False   # TP มาจากสูตร fallback (ไม่ใช่ Fibonacci) — ดูเกณฑ์ R:R ด้านล่าง
    if tp is None:
        fib_info  = find_tp_from_fibonacci(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if fib_info.get("passed"):
            tp = fib_info["tp"]   # อัตราส่วนมาจาก config.TP_FIB_RATIO (ดูที่มา/เหตุผลที่นั่น)
        else:
            used_fallback_tp = True
            tp = (entry + abs(entry - sl) * MIN_RR) if is_long else (entry - abs(entry - sl) * MIN_RR)

    # Confirmation — ราคาทะลุ Swing Low/High บน 4H
    # 2026-07-26: ไม่ให้คะแนนแล้ว (ถูกตัดออกจาก scorecard — ดูเหตุผลใน config.py)
    # ยังคำนวณไว้แสดงผลอ้างอิงในรายงานเท่านั้น ไม่มีผลต่อการตัดสินใจเข้าไม้
    conf_result = check_confirmation(price, df_4h, direction, symbol)

    obv_1d                          = calc_obv(df_1d)
    obv_4h                          = calc_obv(df_4h)
    plus_di_1h, minus_di_1h         = calc_di(df_1h)
    macd_line, signal_line, macd_hist = calc_macd(df_4h)
    rr                              = calc_rr(entry, sl, tp, direction)

    def obv_rising(obv: pd.Series, lookback: int = 10) -> bool:
        # เทียบ OBV ล่าสุดกับ 5 แท่งก่อน — จับ trend ไม่ใช่ noise
        return (obv.iloc[-1] > obv.iloc[-1 - lookback]) if is_long else (obv.iloc[-1] < obv.iloc[-1 - lookback])

    # DI 1H: Long ผ่านเมื่อ DI+ > DI− (แรงซื้อเหนือกว่า), Short กลับด้าน
    di_1h_ok = (plus_di_1h.iloc[-1] > minus_di_1h.iloc[-1]) if is_long else \
               (minus_di_1h.iloc[-1] > plus_di_1h.iloc[-1])

    # 2026-07-27: ช่อง 1H เปลี่ยนจาก OBV เป็น DI (ดูเหตุผล+ตัวเลข redundancy ใน config.py)
    criteria = [
        ("Trend 1D",     (price > ema50_1d) if is_long else (price < ema50_1d), WEIGHT_TREND_1D),
        ("OBV 1D",       obv_rising(obv_1d),                                    WEIGHT_OBV_1D),
        ("Trend 4H",     (price > ema50_4h) if is_long else (price < ema50_4h), WEIGHT_TREND_4H),
        ("OBV 4H",       obv_rising(obv_4h),                                    WEIGHT_OBV_4H),
        ("Trend 1H",     (price > ema50_1h) if is_long else (price < ema50_1h), WEIGHT_TREND_1H),
        ("DI 1H",        di_1h_ok,                                              WEIGHT_DI_1H),
        ("MACD 4H",      macd_ok_for_direction(macd_line, signal_line, macd_hist, direction), WEIGHT_MACD),
        # 2026-07-26: ถ้า TP มาจากสูตร fallback (Fibonacci หาไม่ได้) จะไม่ให้แต้มนี้ —
        # สูตร fallback คำนวณ TP จาก MIN_RR เอง ทำให้ R:R ออกมาเท่ากับ MIN_RR พอดีเสมอ
        # เกณฑ์ rr >= MIN_RR จึงผ่าน 100% โดยอัตโนมัติ = แต้มฟรีที่ไม่ได้กรองอะไรจริง
        # (เช็คตัวเองกับตัวเอง) ต้องมี TP เชิงโครงสร้างจาก Fibonacci ถึงจะนับว่า "R:R ดีจริง"
        # ผลกระทบ ณ วันที่แก้ = ศูนย์: backtest 403 วัน Fibonacci สำเร็จ 100% ทั้ง BTC (148/148,
        # real volume) และ XAU (386/386) — fallback ไม่เคยถูกใช้เลย เป็นเกราะกันไว้ล่วงหน้า
        # TP ที่ผู้ใช้กรอกเองมา (tp is not None) ไม่ถือเป็น fallback — ยังได้แต้มตามปกติ
        ("R:R",          (not used_fallback_tp) and rr >= MIN_RR - 1e-9,          WEIGHT_RR),
    ]

    sl_info["sl"]           = sl
    sl_info["tp"]           = tp
    sl_info["fib_info"]     = fib_info
    sl_info["conf_result"]  = conf_result

    # Hard block: ระยะ entry->SL แคบเกินจนอยู่ในระยะ noise ปกติของแท่ง 4H
    # — ดู comment ที่ config.MIN_SL_DISTANCE_PCT
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct < MIN_SL_DISTANCE_PCT - 1e-9 and not force:
        raise ValueError(
            f"ระยะ SL ห่างจาก entry แค่ {sl_distance_pct:.2f}% ต่ำกว่าขั้นต่ำ "
            f"{MIN_SL_DISTANCE_PCT}% — ห้ามเข้า trade"
        )

    # Hard block: กันแค่ไม่ให้เสี่ยงมากกว่าได้ (R:R < 1) — ไม่สนคะแนนรวม
    # ส่วน "R:R ดีจริง" (>= MIN_RR) ยังต้องผ่านสกอร์การ์ดแยกต่างหากด้านบน (WEIGHT_RR)
    if rr < MIN_RR_HARD_BLOCK - 1e-9 and not force:
        raise ValueError(
            f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {MIN_RR_HARD_BLOCK}  — ห้ามเข้า trade"
        )

    # Hard block: R:R สูงผิดปกติ (> MAX_RR_HARD_BLOCK) มักมาจาก Fibonacci TP ยืดไกลเกินจริง
    # เทียบกับ SL ที่อิงโครงสร้าง ไม่ใช่สัญญาณที่ดีขึ้นจริง — ดู comment ที่ config.MAX_RR_HARD_BLOCK
    if rr > MAX_RR_HARD_BLOCK + 1e-9 and not force:
        raise ValueError(
            f"R:R = {rr:.2f} สูงเกินขั้นสูงสุด {MAX_RR_HARD_BLOCK}  — ห้ามเข้า trade"
        )

    # Hard block: TP ไกลจาก entry เกินไปจนราคาไปไม่ถึงจริง — ดู comment ที่ config.MAX_TP_DISTANCE_PCT
    tp_distance_pct = abs(tp - entry) / entry * 100
    if tp_distance_pct > MAX_TP_DISTANCE_PCT + 1e-9 and not force:
        raise ValueError(
            f"TP ห่างจาก entry {tp_distance_pct:.1f}% เกินขั้นสูงสุด {MAX_TP_DISTANCE_PCT}% "
            f"— ห้ามเข้า trade"
        )

    total  = sum(w for _, passed, w in criteria if passed)
    passed = total >= MIN_SCORE
    return total, criteria, passed, sl_info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) not in (4, 6):
        print("Usage : python scoring.py <SYMBOL> <Long/Short> <entry>")
        print("        python scoring.py <SYMBOL> <Long/Short> <entry> <sl> <tp>")
        print("Example (auto SL/TP) : python scoring.py BTCUSDm Short 65000")
        print("Example (manual SL/TP): python scoring.py BTCUSDm Short 65000 67500 59000")
        sys.exit(1)

    symbol    = sys.argv[1]
    direction = sys.argv[2].capitalize()
    entry     = float(sys.argv[3])
    sl        = float(sys.argv[4]) if len(sys.argv) == 6 else None
    tp        = float(sys.argv[5]) if len(sys.argv) == 6 else None

    load_dotenv()

    try:
        connect()

        # ---- Bias preview -----------------------------------------------
        df_1d_bias  = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
        ema50_bias  = ema(df_1d_bias["close"], 50).iloc[-1]
        ema200_bias = ema(df_1d_bias["close"], 200).iloc[-1]
        bias        = "Long" if ema50_bias > ema200_bias else "Short"
        print(f"\n  Bias 1D : {bias}  (EMA50={ema50_bias:.2f}  EMA200={ema200_bias:.2f})")

        # ---- Scoring (อัตโนมัติทั้งหมด) ---------------------------------
        score, criteria, passed, sl_info = compute_score(
            symbol, direction, entry, sl, tp
        )

        sl    = sl_info["sl"]
        tp    = sl_info["tp"]
        rr    = calc_rr(entry, sl, tp, direction)
        price = get_tick_or_raise(symbol).bid
        failed = [name for name, p, _ in criteria if not p]

        # ---- SL Detail -------------------------------------------------
        print()
        print(f"{'=' * 48}")
        if sl_info.get("swing_price"):
            auto = "(auto)" if len(sys.argv) == 4 else "(manual)"
            print(f"  SL {auto}")
            print(f"  Swing High   : {sl_info['swing_price']}")
            print(f"  ATR buffer   : {sl_info['atr']}")
            # 2026-08-13: ลบ 3 บรรทัด Rejection/Volume OK/Structure OK ทิ้ง — ทั้งสาม key นี้
            # find_sl_from_structure() ไม่เคยคืนมาเลย (คืนแค่ sl/swing_price/swing_idx/atr/
            # passed/reason) จึงตกไปที่ default '-' ตลอดกาล เป็นช่องว่างเปล่าที่ดูเหมือนมีข้อมูล
            # (has_rejection/_rejection_ok ที่เคยเป็นแหล่งข้อมูลก็ถูกลบไปแล้วในคอมมิตเดียวกัน)
            print(f"  SL           : {sl}")

        conf = sl_info.get("conf_result", {})
        if conf:
            print(f"\n  Confirmation (อ้างอิงเท่านั้น — ไม่คิดคะแนน)")
            print(f"  ราคาปัจจุบัน : {conf['current_price']}")
            print(f"  Key Level    : {conf['key_level']}")
            print(f"  Result       : {conf['reason']}")

        fib = sl_info.get("fib_info", {})
        if fib.get("passed"):
            print(f"\n  TP (Fibonacci)  จุด 0 = {fib['origin']}  Move = {fib['move']}")
            print(f"  TP1 (0.382)  : {fib['tp1']}")
            print(f"  TP2 (0.618)  : {fib['tp2']}")
            print(f"  TP  (0.786)  : {fib['levels']['0.786']}")
            print(f"  TP3 (1.000)  : {fib['tp3']}")
            print(f"  TP4 (1.618)  : {fib['tp4']}  ← ใช้เป็น TP หลัก")
        else:
            print(f"  TP (R:R)     : {tp}")

        # ---- Score Display ---------------------------------------------
        print(f"{'=' * 48}")
        print(f"  {symbol}  |  {direction}  |  Entry {entry}")
        print(f"  Current Price: {price:.5f}   R:R = {rr:.2f}")
        print(f"{'=' * 48}")

        for name, p, weight in criteria:
            got    = weight if p else 0.0
            status = "PASS" if p else "FAIL"
            tag    = f"  (x{weight:.0f})" if weight > 1 else ""
            print(f"  {name:<15} {status:<5}  {got:.1f} / {weight:.1f}{tag}")

        print(f"{'-' * 48}")
        print(f"  Total Score  :  {score:.1f} / {TOTAL_WEIGHT:.0f}")
        print(f"  MIN_SCORE    :  {MIN_SCORE:.1f}")
        print(f"{'=' * 48}")

        if passed:
            print("  >>> ENTER TRADE <<<")
        else:
            print("  >>> NO ENTRY <<<")
            if failed:
                print(f"  ข้อที่ไม่ผ่าน: {', '.join(failed)}")

        print(f"{'=' * 48}")

    except ValueError as exc:
        print(f"[BLOCKED] {exc}")
    except (EnvironmentError, ConnectionError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
