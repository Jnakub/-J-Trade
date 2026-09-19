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
    WEIGHT_OBV_1D,
    WEIGHT_RSI_4H, RSI_SCORE_BUFFER,
    WEIGHT_TREND_1H, WEIGHT_DI_1H,
    WEIGHT_MACD, WEIGHT_RR,
    RSI_SCORE_PERIOD, RSI_SCORE_MID,
    RSI_REBOUND_LOOKBACK_BARS, RSI_REBOUND_MIN_TOUCHES,
    TOTAL_WEIGHT, MT5_TIMEFRAMES, MIN_RR, MIN_RR_HARD_BLOCK, MAX_RR_HARD_BLOCK,
    get_min_sl_distance_pct, MAX_TP_DISTANCE_PCT, MIN_SCORE,
)
from mt5_connect import connect, get_tick_or_raise
from swing import (find_sl_from_structure, find_tp_from_fibonacci, check_confirmation,
                   find_swing_lows, find_swing_highs, swing_vol_multiplier, swing_wick_ratio_min,
                   calc_di)
from indicators import calc_rsi
from binance import merge_real_volume
from trend_flip import compute_trend_regime
from bars import BAR_OFFSET_H, get_aligned_4h, get_bars

# ชื่อเกณฑ์ในสกอร์การ์ด Scoring ตามลำดับใน compute_score() — มีไว้ให้ backtest ตรวจว่าชื่อที่สั่ง
# ตัดสะกดถูก (สะกดผิด = ไม่ได้ตัดอะไรเลยแต่รายงานบอกว่าตัดแล้ว) ถ้าแก้ criteria list ต้องแก้ตรงนี้
# ตามด้วย — คอลัมน์รายเกณฑ์ใน replay_trades_*.csv เป็นตัวเช็คว่าสองที่ยังตรงกันอยู่
SCORECARD_CRITERIA_NAMES = ("OBV 1D", "RSI 4H Rebound", "Trend 1H",
                            "DI 1H", "MACD 4H", "R:R")

# ชื่อเกณฑ์ที่ถูก "ปิด" ชั่วคราว — ระบบจริงต้องเป็น () เสมอ มีไว้ให้ backtest ทดลองตัดเกณฑ์ออก
# แล้ววัดผลทั้งระบบ โดยไม่ต้องแก้ criteria list จริงแล้วลืมแก้กลับ (backtest_replay.py --drop=)
# ตัวที่ตั้งค่าจะ set scoring.DISABLED_CRITERIA + scoring.MIN_SCORE เอง แล้วพิมพ์ลง header
# ของรายงานให้เห็นชัด — ผลที่ได้จากรอบที่ไม่ว่างห้ามเอาไปเทียบกับรอบปกติโดยไม่ดูว่าตัดอะไรไป
DISABLED_CRITERIA: tuple = ()

# ตัวคูณ ATR ที่ SL ตอนเข้าถูกขยับออกจาก SL โครงสร้าง ก่อนส่ง broker — ต้องตรงกับสูตรใน
# exit_monitor.calc_atr_trailing_sl (initial_sl = pinned_swing ∓ 2×ATR) ไม่งั้น SL แรกกับรอบ
# trailing แรกจะอยู่คนละจุด ตั้ง 0 = ไม่ขยับ (SL ที่ส่ง = SL โครงสร้าง แบบก่อน 2026-08-27)
# ไว้ให้ backtest_replay.py --legacy-sl ใช้เทียบ
EXEC_SL_ATR_MULT = 2.0


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
    # 2026-09-18: UKOILm — backtest_trend_flip_ksweep UKOILm 3000 1D (1,726 แท่ง 1D)
    #   k 0.20-0.60 ให้ FalseFlip = 0% ทุกค่า · เลือก 0.20 เพราะได้ flip มากสุด (36 ครั้ง)
    #   และเร็วกว่า EMA50/200 cross เฉลี่ย 10.2 แท่ง (matched 13 คู่) — เกณฑ์เดียวกับตัวอื่น
    "UKOILm": 0.20,
    # 2026-09-19: HK50m — backtest_trend_flip_ksweep HK50m 3000 1D (2,007 แท่ง 1D)
    #   k 0.20-0.60 ให้ FalseFlip = 0% ทุกค่า · เลือก 0.20 เพราะได้ flip มากสุด (46 ครั้ง)
    #   และเร็วกว่า EMA50/200 cross เฉลี่ย 9.3 แท่ง (matched 17 คู่) — เกณฑ์เดียวกับตัวอื่น
    # ⚠️ **ต้องส่ง `1D` ต่อท้ายเสมอ** — ตัว sweep default เป็น 4H แต่ compute_trend_regime
    #   ที่ระบบเรียกจริงใช้ `df_1d` (ดู get_trend_flip_bias) รันด้วย default จะได้ k ของ
    #   timeframe ที่ไม่มีใครใช้ (เจอกับตัวเองรอบนี้: 4H ให้ FalseFlip 2.1% ส่วน 1D ให้ 0%)
    "HK50m": 0.20,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TF_NAME = {v: k for k, v in MT5_TIMEFRAMES.items()}   # mt5.TIMEFRAME_* -> "1D"/"4H"/"1H"


def get_ohlcv(symbol: str, timeframe, bars: int = 100, as_of: datetime = None) -> pd.DataFrame:
    """as_of=None (ปกติ) = ดึง bars ล่าสุดจากปัจจุบัน — as_of=datetime = ดึงข้อมูลเท่าที่ "มีจริง
    ณ วินาทีนั้น" (ใช้ backtest จำลอง compute_score ณ เวลาในอดีตแบบเป๊ะ ไม่ต้อง copy logic
    มาเขียนซ้ำ)

    2026-08-18: timeframe 4H ของ symbol ที่มี bars.BAR_OFFSET_H != 0 จะถูกเลื่อนขอบแท่งให้ตรงกับ
    TradingView แทนแท่ง MT5 ดิบ (ดู bars.py) — จุดเดียวนี้ครอบคลุมทุกที่ที่เรียก get_ohlcv/
    get_ohlcv_real ด้วย "4H" ทั้งระบบ (compute_score, structure break, key level, divergence,
    ATR trailing anchor ฯลฯ) ไม่ต้องแก้ทีละจุด — timeframe อื่น (1D/1H) ไม่กระทบเลย

    2026-08-26: ย้ายตัวดึงแท่งทั้งหมดไปที่ bars.get_bars() — พร้อมกับแก้บั๊ก lookahead ของ
    as_of mode ที่กระทบ backtest ทุกตัว (อ่านรายละเอียดที่ bars.get_bars) path รันสด
    (as_of=None) ไม่เปลี่ยนพฤติกรรมเลย"""
    return get_bars(symbol, TF_NAME[timeframe], bars=bars, as_of=as_of)


def get_ohlcv_real(symbol: str, tf_name: str, bars: int = 100, as_of: datetime = None) -> pd.DataFrame:
    """get_ohlcv + แทนที่ volume ด้วย real volume (Bitstamp/COMEX ตาม merge_real_volume)
    ใช้ตัวนี้เสมอถ้า logic ปลายทางแตะ volume (swing filter, VSA ฯลฯ) — ไม่งั้น BTC จะได้
    tick_volume ของโบรกเกอร์ซึ่งไม่ตรงกับที่ระบบใช้หา SL จริง"""
    df = get_ohlcv(symbol, MT5_TIMEFRAMES[tf_name], bars=bars, as_of=as_of)
    return merge_real_volume(df, symbol, tf_name, as_of=as_of)


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
    """PASS เมื่อเส้น MACD และ Signal อยู่ฝั่งเดียวกับไม้ (Long: ทั้งคู่เหนือ 0, Short: ทั้งคู่
    ใต้ 0) — ไม่ใช้ Histogram ตัดสินเลย (รับ argument ไว้เพื่อความเข้ากันได้ของผู้เรียกเดิม)

    2026-08-26: เปลี่ยนจากนิยามเดิมที่ใช้ตำแหน่งเทียบเส้น 0 แค่ "เลือกสาขา" แล้วตัดสินด้วย
    Histogram (Long: เหนือ 0 -> ต้อง hist[-2] > hist[-3] / ใต้ 0 -> ต้อง hist[-2] > 0, Short
    กลับด้าน) — backtest BTCUSDm 730 วัน (backtest_criteria.py, 1250 setup, จำลอง compute_score
    ทีละแท่ง 4H ด้วย as_of หลังแก้บั๊ก lookahead) พบว่านิยามเดิม **ให้ผลติดลบ** ทั้งสองสาขา:
      สาขา "MACD อยู่ฝั่ง trend แล้ว" : PASS AvgR -0.23R vs FAIL -0.01R  (n=281/339)
      สาขา "MACD ยังอยู่ฝั่งตรงข้าม"  : PASS Win 19.1%  vs FAIL 23.0%   (n=299/331)
    เพราะทั้งสองสาขาถามว่า "โมเมนตัมวิ่งไปทางเดียวกับไม้แล้วหรือยัง" = เข้าไม้หลังการเคลื่อนไหว
    เริ่มไปแล้ว ขณะที่ระบบวาง TP ไกลถึง Fibonacci 1.618 จึงต้องการ "ระยะที่เหลือ" เป็นหลัก

    ตัวเลขเทียบสองนิยาม (ΔWR = Win Rate ตอน PASS ลบตอน FAIL):
      เดิม : ชุดเต็ม -2.4  | bootstrap 400 ชุด non-overlap เฉลี่ย -5.9  (เป็นบวก 0% ของชุด)
      ใหม่ : ชุดเต็ม +8.7  | bootstrap เฉลี่ย +8.7 ช่วง 5-95% = +5.7..+12.3 (เป็นบวก 100%)
    ผลสุทธิต่อไม้ที่ผ่าน MIN_SCORE: เดิม 443 ไม้ Win 29.8% Total -55.8R -> ใหม่ 457 ไม้
    Win 33.0% Total -15.1R (ตัวเลข R ยังติดลบเพราะการจำลองปิดที่ SL/TP เป๊ะ ไม่มี trailing/
    partial ของ exit_monitor.py ใช้เทียบสองนิยามกันเองได้ แต่ไม่ใช่ผลตอบแทนจริงของระบบ)

    ⚠️ ข้อจำกัดของหลักฐาน (ตัดสินใจโดยรู้ตัว): วัดจาก BTCUSDm ช่วงเดียว 2 ปีเท่านั้น ยังไม่ได้
    ยืนยันกับ ETHUSDm/XAUUSDm และผลเกือบทั้งหมดมาจากฝั่ง Short (Short+MACD ใต้ 0: Win 33.1%
    AvgR +0.17R n=242 / ฝั่ง Long แทบไม่ต่าง 27.8% vs 25.5%) ซึ่งอาจเป็นลักษณะเฉพาะของช่วงที่
    BTC ลงแรง — ควรรัน backtest_criteria.py กับ symbol อื่นยืนยัน และเฝ้าดูผลเทรดจริง"""
    is_short   = direction.capitalize() == "Short"
    macd_val   = macd_line.iloc[-1]
    signal_val = signal_line.iloc[-1]

    if is_short:
        return macd_val < 0 and signal_val < 0
    return macd_val > 0 and signal_val > 0


def check_rsi_double_rebound(rsi: pd.Series, is_long: bool,
                             lookback: int = RSI_REBOUND_LOOKBACK_BARS,
                             min_touches: int = RSI_REBOUND_MIN_TOUCHES) -> bool:
    """RSI Double Rebound — สัญญาณต่อ trend (continuation) ต่างจาก Divergence ที่เป็นสัญญาณ
    กลับตัว: ใน trend ที่แข็งแรง RSI มักย่อมาแตะโซนกลาง (RSI_SCORE_MID ± RSI_SCORE_BUFFER)
    ระหว่างพักตัว แล้วเด้งกลับออกไปฝั่ง trend โดยไม่หลุดลึกไป oversold/overbought จริง — ยิ่งเด้ง
    ซ้ำได้หลายครั้งในช่วงเวลาสั้นๆ ยิ่งแปลว่า trend แข็งแรง (ดูรูปตัวอย่างที่ผู้ใช้ส่งมา 2026-08-20)

    นิยาม "แตะแล้วเด้ง" ในโค้ดนี้ = RSI ไขว้จากในโซน (Long: <=MID+BUFFER, Short: >=MID-BUFFER)
    ออกไปฝั่ง trend ในแท่งถัดไป — นับเป็น 1 ครั้ง ต้องมีอย่างน้อย min_touches ครั้งใน lookback
    แท่งล่าสุด และ RSI ปัจจุบันต้องยังอยู่ฝั่ง trend (ยืนยันว่าเด้งครั้งล่าสุดยังไม่หลุดกลับเข้าโซน)

    2026-08-20: เพิ่มเงื่อนไข "จุดต่ำสุด" ของแต่ละครั้งที่แตะต้องขยับตามทิศ trend เทียบกับครั้งแรก
    (ตามคำสั่งผู้ใช้) — Long: จุดต่ำสุดของการแตะครั้งหลังๆ ต้อง "สูงกว่า" ครั้งแรก (higher low บน
    RSI, ย่อตื้นขึ้นเรื่อยๆ = แรงขายอ่อนลง) Short: ต้อง "ต่ำกว่า" ครั้งแรก (lower high, แรงซื้อ
    อ่อนลง) กันเคสที่แตะซ้ำแต่ย่อลึกขึ้นเรื่อยๆ (สัญญาณอ่อนแรงจริง ไม่ใช่ trend แข็งแรง) ทั้งที่
    เข้าเกณฑ์จำนวนครั้งพอ — ยังไม่มี backtest ยืนยัน ตามคำสั่งผู้ใช้

    แทนที่เกณฑ์ "RSI 4H" เดิม (threshold เดี่ยว ณ แท่งปัจจุบัน) — ยังไม่มี backtest ยืนยัน ตาม
    คำสั่งผู้ใช้ ควร backtest เทียบกับเกณฑ์เดิมก่อนใช้ตัดสินใจเทรดจริง

    2026-09-01: วัดแล้วด้วย backtest_replay 730 วัน (--drop="RSI 4H Rebound" --min-score=4)
    ผลคนละทิศกันสอง symbol และรวมแล้วแย่ลง จึง **เก็บเกณฑ์นี้ไว้ตามเดิม**:
      BTC Scoring 20 ไม้ -1.05R -> 21 ไม้ -0.80R (ดีขึ้น)
      XAU Scoring 12 ไม้ -0.34R -> 16 ไม้ -1.00R (แย่ลง)   รวม -1.39R -> -1.80R
    ที่มาของการทดสอบ: ในไม้ Scoring ที่เข้าจริง กลุ่มที่ "ตก" เกณฑ์นี้ (ผ่าน 5/6 ได้อยู่) ทำกำไร
    +1.40R ขณะที่กลุ่มผ่านครบ 6/6 ขาดทุน -1.67R ดูเผินๆ เหมือนเกณฑ์กลับด้าน แต่พอวัดทั้งระบบ
    แล้วไม่ใช่ — กลุ่มที่ตกเกณฑ์นี้ถือเฉลี่ย 8.5 วัน ส่วนกลุ่ม 6/6 ถือ 4.3 วัน ต่างกันที่ประเภท
    ของ setup มากกว่าตัวเกณฑ์"""
    if len(rsi) < lookback + 1:
        return False

    window  = rsi.iloc[-(lookback + 1):]
    zone_hi = RSI_SCORE_MID + RSI_SCORE_BUFFER
    zone_lo = RSI_SCORE_MID - RSI_SCORE_BUFFER
    in_zone = (window <= zone_hi) if is_long else (window >= zone_lo)
    on_trend_side = (window.iloc[-1] > zone_hi) if is_long else (window.iloc[-1] < zone_lo)

    # ไล่หาแต่ละ "ครั้งที่แตะ" (ช่วงต่อเนื่องที่ in_zone=True) แล้วเก็บจุดต่ำสุด(Long)/สูงสุด(Short)
    # ของแต่ละครั้งไว้เทียบกัน — ครั้งที่ยัง "ค้าง" ท้าย window (ยังไม่ทันเด้งออก) ไม่นับ
    touch_extremes = []
    in_run, run_extreme = False, None
    for val, flag in zip(window, in_zone):
        if flag:
            run_extreme = val if run_extreme is None else (min(run_extreme, val) if is_long
                                                            else max(run_extreme, val))
            in_run = True
        elif in_run:
            touch_extremes.append(run_extreme)
            in_run, run_extreme = False, None

    touches = len(touch_extremes)
    if touches < min_touches or not on_trend_side:
        return False

    progressing = all(v > touch_extremes[0] for v in touch_extremes[1:]) if is_long \
                 else all(v < touch_extremes[0] for v in touch_extremes[1:])
    return progressing


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
    ถ้าไม่ส่ง tp จะหาจาก Fibonacci อัตโนมัติ (หาไม่ได้ถึงตกไปใช้ SL × MIN_RR_HARD_BLOCK)

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
        df_1d = merge_real_volume(df_1d, symbol, "1D", as_of=as_of)
    df_4h = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=200, as_of=as_of)
    df_4h = merge_real_volume(df_4h, symbol, "4H", as_of=as_of)
    df_1h = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], as_of=as_of)
    df_1h = merge_real_volume(df_1h, symbol, "1H", as_of=as_of)   # 2026-07-27: เดิมไม่เคย merge เลย
                                                     # (ต่างจาก 1D/4H) ทำให้ OBV 1H เป็น tick_volume แม้แต่ BTC

    price = df_1h["close"].iloc[-1] if as_of is not None else get_tick_or_raise(symbol).bid

    ema50_1h  = ema(df_1h["close"], 50).iloc[-1]
    # ema50_4h ถูกลบ 2026-08-19 — ช่อง 4H เปลี่ยนจาก Trend (EMA50) เป็น RSI แล้ว ไม่มีใครใช้ต่อ

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
        # ส่ง entry เข้าไปเป็น current_price — ด่านตรวจ "ราคาทะลุ swing ไปแล้วหรือยัง" ต้องวัด
        # กับราคาที่จะเข้าไม้จริง ไม่ใช่ close ของแท่ง 4H ที่ปิดไปแล้วถึง 3 ชม. (ดู swing.py)
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min,
                                         current_price=entry)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # ── SL ที่จะส่ง broker จริง (exec_sl) ────────────────────────────────────────────────
    # 2026-08-31: เดิมด่าน R:R คำนวณจาก `sl` (SL โครงสร้าง) แต่ scheduler.py ขยับ SL ออกไปอีก
    # EXEC_SL_ATR_MULT×ATR *หลัง* compute_score จบไปแล้ว (ตั้งแต่ 2026-08-27) ระยะเสี่ยงจริงจึง
    # กว้างกว่าที่ด่านคิดเฉลี่ย 1.49 เท่า (ช่วง 1.20-2.06) วัดจากไม้ Scoring 38 ไม้ของ replay:
    #   R:R ที่ด่านเห็น เฉลี่ย 4.12 (ต่ำสุด 1.50) แต่ R:R จริง เฉลี่ย 2.65 (ต่ำสุด 1.06)
    #   11/38 ไม้ (29%) ผ่านด่าน MIN_RR_HARD_BLOCK=1.5 มาได้ทั้งที่ R:R จริงต่ำกว่า 1.5
    # ย้ายการคำนวณเข้ามาที่นี่เพื่อให้ "เลขที่ด่านตรวจ" = "สิ่งที่ระบบทำจริง" — เกณฑ์ไม่เปลี่ยน
    # เปลี่ยนแค่ไม้บรรทัดที่ใช้วัด (ดู comment เจตนาเดิมที่ scheduler.py: "R:R ตอนเข้า และ
    # Trailing SL ระหว่างถือ จะไปทางเดียวกันเสมอ" ซึ่งขาดไปโดยไม่ตั้งใจตอนแก้ 2026-08-27)
    #
    # import ในฟังก์ชันเพราะ exit_monitor import scoring อยู่แล้ว — import ระดับโมดูลจะวน
    # ใช้ calc_atr_trailing_sl ตัวเดียวกับที่ scheduler/exit_monitor ใช้ ไม่คำนวณ ATR เองซ้ำ
    # กันสองที่คิดคนละค่า ถ้าคำนวณไม่ได้ (ข้อมูลไม่พอ) จะ fallback เป็น exec_sl = sl แบบเดิม
    atr_entry, exec_sl = None, sl
    if EXEC_SL_ATR_MULT:
        try:
            from exit_monitor import calc_atr_trailing_sl, BARS as _TRAIL_BARS
            _t = as_of if as_of is not None else datetime.now()
            _tr = calc_atr_trailing_sl(get_ohlcv_real(symbol, "4H", bars=_TRAIL_BARS, as_of=as_of),
                                       symbol, _t, direction.capitalize(), as_of=as_of)
            if _tr:
                atr_entry = _tr["atr_entry"]
                exec_sl = (sl - EXEC_SL_ATR_MULT * atr_entry) if is_long else \
                          (sl + EXEC_SL_ATR_MULT * atr_entry)
        except Exception:
            pass

    # หา TP อัตโนมัติจาก Fibonacci (4H) ถ้าไม่ได้กรอกมา
    fib_info = {}
    used_fallback_tp = False   # TP มาจากสูตร fallback (ไม่ใช่ Fibonacci) — ดูเกณฑ์ R:R ด้านล่าง
    if tp is None:
        fib_info  = find_tp_from_fibonacci(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if fib_info.get("passed"):
            tp = fib_info["tp"]   # อัตราส่วนมาจาก config.TP_FIB_RATIO (ดูที่มา/เหตุผลที่นั่น)
        else:
            # 2026-08-26: อิง MIN_RR_HARD_BLOCK ไม่ใช่ MIN_RR — เดิมสองค่านี้เท่ากัน (1.5) จึงไม่
            # ต่างกัน แต่ตอนที่ MIN_RR ถูกขึ้นเป็น 2.0 เพื่อให้เกณฑ์ R:R ในสกอร์การ์ดกรองได้จริง
            # (ดู config.MIN_RR) สูตรนี้พลอยยืด TP ของไม้ fallback ตามไปด้วยทั้งที่ไม่ได้ตั้งใจ
            # — แยกให้ขาดกัน: MIN_RR = "เกณฑ์ให้คะแนน", MIN_RR_HARD_BLOCK = "ขั้นต่ำที่ยอมเทรด"
            # ซึ่งเป็นความหมายที่ตรงกับ TP ขั้นต่ำที่ยอมรับได้มากกว่า (ไม้ fallback ไม่ได้แต้ม R:R
            # อยู่แล้วเพราะ used_fallback_tp — ค่านี้จึงไม่กระทบคะแนน กระทบแค่ TP จริง)
            # 2026-08-31: อิง exec_sl (ระยะเสี่ยงจริง) ไม่ใช่ sl โครงสร้าง — ไม่งั้น TP ที่ตั้งให้
            # ได้ R:R = MIN_RR_HARD_BLOCK พอดี จะให้ R:R จริงต่ำกว่าเกณฑ์ทันทีที่ SL ถูกขยับออก
            used_fallback_tp = True
            fallback_rr = MIN_RR_HARD_BLOCK
            tp = (entry + abs(entry - exec_sl) * fallback_rr) if is_long else \
                 (entry - abs(entry - exec_sl) * fallback_rr)

    # Confirmation — ราคาทะลุ Swing Low/High บน 4H
    # 2026-07-26: ไม่ให้คะแนนแล้ว (ถูกตัดออกจาก scorecard — ดูเหตุผลใน config.py)
    # ยังคำนวณไว้แสดงผลอ้างอิงในรายงานเท่านั้น ไม่มีผลต่อการตัดสินใจเข้าไม้
    conf_result = check_confirmation(price, df_4h, direction, symbol)

    obv_1d                          = calc_obv(df_1d)
    plus_di_1h, minus_di_1h         = calc_di(df_1h)
    macd_line, signal_line, macd_hist = calc_macd(df_4h)
    rsi_4h                          = calc_rsi(df_4h["close"], RSI_SCORE_PERIOD)
    # R:R วัดจาก exec_sl = ระยะเสี่ยงจริงที่จะส่ง broker (ดู comment ที่คำนวณ exec_sl ด้านบน)
    rr                              = calc_rr(entry, exec_sl, tp, direction)

    def obv_rising(obv: pd.Series, lookback: int = 10) -> bool:
        # เทียบ OBV ล่าสุดกับ 5 แท่งก่อน — จับ trend ไม่ใช่ noise
        return (obv.iloc[-1] > obv.iloc[-1 - lookback]) if is_long else (obv.iloc[-1] < obv.iloc[-1 - lookback])

    # DI 1H: Long ผ่านเมื่อ DI+ > DI− (แรงซื้อเหนือกว่า), Short กลับด้าน
    di_1h_ok = (plus_di_1h.iloc[-1] > minus_di_1h.iloc[-1]) if is_long else \
               (minus_di_1h.iloc[-1] > plus_di_1h.iloc[-1])

    # 2026-07-27: ช่อง 1H เปลี่ยนจาก OBV เป็น DI (ดูเหตุผล+ตัวเลข redundancy ใน config.py)
    criteria = [
        ("OBV 1D",       obv_rising(obv_1d),                                    WEIGHT_OBV_1D),
        # 2026-08-19: ช่องนี้เดิมคือ Trend 4H (close vs EMA50) เปลี่ยนเป็น RSI 4H ตามคำสั่งผู้ใช้
        # (ดูตัวเลข redundancy/predictive power ที่วัดไว้ใน config.py เหนือ WEIGHT_RSI_4H)
        # 2026-08-20: เปลี่ยนจาก threshold เดี่ยว (RSI > 50+buffer) เป็น RSI Double Rebound —
        # ต้องเคยย่อแตะโซนกลางแล้วเด้งกลับฝั่ง trend อย่างน้อย 2 ครั้ง ไม่ใช่แค่ค่าปัจจุบันผ่าน
        # เฉยๆ (ดู check_rsi_double_rebound ด้านบน) ตามคำสั่งผู้ใช้ ยังไม่มี backtest ยืนยัน
        ("RSI 4H Rebound", check_rsi_double_rebound(rsi_4h, is_long),           WEIGHT_RSI_4H),
        # 2026-08-30: ช่อง "OBV 4H" ถูกตัดออกตามคำสั่งผู้ใช้ (TOTAL_WEIGHT 7 -> 6, MIN_SCORE
        # คงที่ 5 จึงเข้มขึ้นจากยอมตกได้ 2 ข้อเหลือ 1 ข้อ) — ⚠️ A/B บอกว่าผลของการตัดไม่คงเส้น
        # คงวาข้ามระดับความเข้ม (-0.01R กับ +1.22R) และอยู่ในกรอบ noise ทั้งคู่ ดูตาราง 2x2
        # เต็ม + วิธีถอยกลับที่ config.py เหนือ WEIGHT_OBV_1D
        ("Trend 1H",     (price > ema50_1h) if is_long else (price < ema50_1h), WEIGHT_TREND_1H),
        ("DI 1H",        di_1h_ok,                                              WEIGHT_DI_1H),
        ("MACD 4H",      macd_ok_for_direction(macd_line, signal_line, macd_hist, direction), WEIGHT_MACD),
        # 2026-07-26: ถ้า TP มาจากสูตร fallback (Fibonacci หาไม่ได้) จะไม่ให้แต้มนี้ —
        # ตอนนั้นสูตร fallback อิง MIN_RR ทำให้ R:R ออกมาเท่ากับ MIN_RR พอดีเสมอ เกณฑ์
        # rr >= MIN_RR จึงผ่าน 100% โดยอัตโนมัติ = แต้มฟรี (เช็คตัวเองกับตัวเอง)
        # 2026-08-26: fallback ย้ายไปอิง MIN_RR_HARD_BLOCK (1.5) ซึ่งต่ำกว่า MIN_RR (2.0) แล้ว
        # ไม้ fallback จึงตกเกณฑ์ rr >= MIN_RR ด้วยตัวเลขเองอยู่แล้ว — คงเงื่อนไขนี้ไว้เป็นเกราะ
        # กันกรณีที่ใครตั้งสองค่าให้เท่ากันอีกในอนาคต (และเพื่อรักษาเจตนาเดิม: ต้องมี TP เชิง
        # โครงสร้างจาก Fibonacci ถึงจะนับว่า "R:R ดีจริง")
        # ผลกระทบ ณ วันที่แก้ = ศูนย์: backtest 403 วัน Fibonacci สำเร็จ 100% ทั้ง BTC (148/148,
        # real volume) และ XAU (386/386) — fallback ไม่เคยถูกใช้เลย เป็นเกราะกันไว้ล่วงหน้า
        # TP ที่ผู้ใช้กรอกเองมา (tp is not None) ไม่ถือเป็น fallback — ยังได้แต้มตามปกติ
        ("R:R",          (not used_fallback_tp) and rr >= MIN_RR - 1e-9,          WEIGHT_RR),
    ]

    sl_info["sl"]           = sl          # SL โครงสร้าง — เป็น pinned_swing ของสูตร trailing
    sl_info["exec_sl"]      = exec_sl     # SL ที่ต้องส่ง broker จริง (ผู้เรียกใช้ตัวนี้ อย่าคำนวณเอง
    sl_info["atr_entry"]    = atr_entry   # ซ้ำ ไม่งั้นสองที่จะได้ ATR คนละค่าแล้ว R:R เพี้ยนอีก)
    sl_info["tp"]           = tp
    sl_info["fib_info"]     = fib_info
    sl_info["conf_result"]  = conf_result

    # Hard block: ระยะ entry->SL แคบเกินจนอยู่ในระยะ noise ปกติของแท่ง 4H
    # — ดู comment ที่ config.MIN_SL_DISTANCE_PCT / config.get_min_sl_distance_pct()
    # (2026-08-18: Forex/Index ใช้เกณฑ์ต่ำกว่า 0.9% เพราะ ATR/ราคา ของมันต่ำกว่า BTC/XAU
    # โดยธรรมชาติ — ใช้ 0.9% ตายตัวจะบล็อกเกือบทุกไม้ของมัน — ตัดสินใจตาม asset class)
    min_sl_pct = get_min_sl_distance_pct(symbol)
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct < min_sl_pct - 1e-9 and not force:
        raise ValueError(
            f"ระยะ SL ห่างจาก entry แค่ {sl_distance_pct:.2f}% ต่ำกว่าขั้นต่ำ "
            f"{min_sl_pct}% — ห้ามเข้า trade"
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

    if DISABLED_CRITERIA:                 # ปกติว่าง — ดู comment ที่หัวไฟล์
        criteria = [c for c in criteria if c[0] not in DISABLED_CRITERIA]

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
