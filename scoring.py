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
    MT5_TIMEFRAMES, MIN_RR_HARD_BLOCK, MAX_RR_HARD_BLOCK,
    get_min_sl_distance_pct, MAX_TP_DISTANCE_PCT,
)
from mt5_connect import connect
from swing import (find_sl_from_structure, find_tp_from_fibonacci,
                   find_swing_lows, find_swing_highs, swing_vol_multiplier, swing_wick_ratio_min)
from binance import merge_real_volume
from trend_flip import compute_trend_regime
from bars import BAR_OFFSET_H, get_aligned_4h, get_bars

# ตัวคูณ ATR ที่ SL ตอนเข้าถูกขยับออกจาก SL โครงสร้าง ก่อนส่ง broker — ต้องตรงกับสูตรใน
# exit_monitor.calc_atr_trailing_sl (initial_sl = pinned_swing ∓ 2×ATR) ไม่งั้น SL แรกกับรอบ
# trailing แรกจะอยู่คนละจุด ตั้ง 0 = ไม่ขยับ (SL ที่ส่ง = SL โครงสร้าง แบบก่อน 2026-08-27)
# ไว้ให้ backtest_replay.py --legacy-sl ใช้เทียบ
EXEC_SL_ATR_MULT = 2.0
# ENFORCE_BIAS_MATCH — compute_entry บังคับทิศไม้ให้ตรง Bias 1D (trend_flip) ไหม · True = พฤติกรรมระบบจริง
# ปิดได้จาก backtest_replay --scoring-dir-structure เท่านั้น (ทดลอง 2026-10-10: ให้ Scoring ใช้ทิศจาก Structure 4H แทน Bias)
# Breakout ใช้ฟังก์ชันนี้ด้วยแต่ทิศของมัน (Long) ตรง Bias เสมออยู่แล้ว จึงไม่ได้รับผล
ENFORCE_BIAS_MATCH = True


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
    # 2026-09-28: GBPCHFm — backtest_trend_flip_ksweep GBPCHFm 3000 1D (2,999 แท่ง 1D)
    #   k 0.20/0.25 FalseFlip 1.4% · k 0.30-0.60 = 0% · เลือก 0.30 = ค่าต่ำสุดที่ FalseFlip 0%
    #   (63 flip · เร็วกว่า EMA50/200 cross เฉลี่ย 13.6 แท่ง · matched 26 คู่)
    "GBPCHFm": 0.30,
    # 2026-09-28: AUDNZDm — backtest_trend_flip_ksweep AUDNZDm 3000 1D (2,999 แท่ง)
    #   **ไม่มีค่าไหนได้ FalseFlip 0%** ทุก k พลิกหลอก 2 ครั้ง (0.20 = 2/67 · 0.25-0.60 = 2/65)
    #   เลือก 0.20 = พลิกบ่อยสุด เร็วกว่า EMA50/200 cross 19.3 แท่ง (matched 33)
    "AUDNZDm": 0.20,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TF_NAME = {v: k for k, v in MT5_TIMEFRAMES.items()}   # mt5.TIMEFRAME_* -> "1D"/"4H"/"1H"


def get_ohlcv(symbol: str, timeframe, bars: int = 100, as_of: datetime = None) -> pd.DataFrame:
    """as_of=None (ปกติ) = ดึง bars ล่าสุดจากปัจจุบัน — as_of=datetime = ดึงข้อมูลเท่าที่ "มีจริง
    ณ วินาทีนั้น" (ใช้ backtest จำลอง compute_entry ณ เวลาในอดีตแบบเป๊ะ ไม่ต้อง copy logic
    มาเขียนซ้ำ)

    2026-08-18: timeframe 4H ของ symbol ที่มี bars.BAR_OFFSET_H != 0 จะถูกเลื่อนขอบแท่งให้ตรงกับ
    TradingView แทนแท่ง MT5 ดิบ (ดู bars.py) — จุดเดียวนี้ครอบคลุมทุกที่ที่เรียก get_ohlcv/
    get_ohlcv_real ด้วย "4H" ทั้งระบบ (compute_entry, structure break, key level, divergence,
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


def calc_rr(entry: float, sl: float, tp: float, direction: str) -> float:
    if direction.lower() == "long":
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    return reward / risk if risk > 0 else 0.0


# ---------------------------------------------------------------------------
# Trend bias + แผนเข้าไม้ (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def get_trend_bias(symbol: str, df_1d: pd.DataFrame) -> tuple[str | None, str]:
    """คืน (bias, bias_source) — Long/Short ตาม trend_flip เท่านั้น (ไม่มี EMA50/200
    fallback อีกต่อไป — 2026-08-02 ตัดออก ตามที่ตกลงกันว่า bias ต้องมาจาก trend_flip
    เพียวๆ) ดึงมาเป็นฟังก์ชันแยกเพื่อให้ backtest ภายนอก (เช่น backtest_exit_compare.py)
    รู้ bias ก่อนเรียก compute_entry ได้โดยไม่ต้อง copy logic ชุดนี้มาเขียนซ้ำ
    (compute_entry เองก็เรียกตัวนี้ภายใน — scheduler.py ก็เรียกตัวนี้แทน EMA ของตัวเองแล้ว)

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


def compute_entry(symbol: str, direction: str, entry: float,
                  sl: float = None, tp: float = None,
                  force: bool = False, as_of: datetime = None,
                  df_1d: pd.DataFrame = None, min_rr: float = None) -> dict:
    """วางแผนไม้ทาง Scoring (และ Breakout) — คืน dict ของ SL/TP/R:R หรือ raise ValueError
    ถ้าติดด่านใดด่านหนึ่ง (= ห้ามเข้าไม้นี้) ด่านที่ตัดสินจริงมีเท่านี้:
      1. ทิศต้องตรง trend_flip bias ของ 1D
      2. หา SL จาก swing โครงสร้าง 4H ได้ (ไม่ส่ง sl มา) · TP จาก Fibonacci (หาไม่ได้ = fallback)
      3. ระยะ entry->SL ไม่แคบกว่า get_min_sl_distance_pct
      4. MIN_RR_HARD_BLOCK <= R:R <= MAX_RR_HARD_BLOCK (วัดด้วย exec_sl = SL ที่ส่ง broker จริง)
      5. ระยะ TP ไม่เกิน MAX_TP_DISTANCE_PCT

    2026-09-25: เดิมชื่อ compute_score และคืน (score, criteria, passed, sl_info) — สกอร์การ์ด
    ถูกลบทิ้งทั้งใบ (เลิกใช้กรองตั้งแต่ 2026-09-06 ดูเหตุผลที่ config.py) เหลือคืน dict เดียว
    คีย์: sl (SL โครงสร้าง = pinned_swing) · exec_sl (ส่ง broker) · atr_entry · tp ·
    rr (จาก exec_sl) · fib_info + คีย์จาก find_sl_from_structure (swing_price/swing_idx/atr)
    🔴 เดิมไม่มีคีย์ rr — scheduler ทาง Breakout อ่าน sl_info["rr"] แล้วจะโยน KeyError ทุกครั้ง
    ที่ไม้ผ่านด่านครบ (log จริงยังไม่เคยมีไม้ Breakout เลยตั้งแต่ 2026-09-21 จึงไม่เคยเห็น)

    as_of=None (ปกติ) = เช็คสด ณ ตอนนี้ · as_of=datetime = จำลอง ณ เวลานั้นในอดีต (bars ทั้งหมด
    ตัดที่เวลานั้น) ให้ backtest เรียกตัวนี้ตรงๆ แทนการ copy logic มาเขียนซ้ำ

    df_1d: ส่ง df_1d (bars=800, as_of เดียวกัน) ที่ดึงมาแล้วมาใช้ซ้ำได้ — ใช้แค่ตรวจทิศกับ
    get_trend_bias ซึ่งอ่านเฉพาะแท่งที่ปิดแล้ว (ไม่ต้อง merge real volume: trend_flip ไม่ใช้ volume)

    min_rr: ทับเกณฑ์ด่าน R:R ขั้นต่ำ **เฉพาะด่าน** (None = MIN_RR_HARD_BLOCK) — ตัวคูณของ TP fallback
    ยังเป็น MIN_RR_HARD_BLOCK เสมอ ไม่งั้นการวัดด่านจะพ่วงการหด TP ของไม้ fallback มาด้วย
    (2026-09-26 เพิ่มให้ backtest_replay --scoring-min-rr ทับเฉพาะทาง Scoring ไม่แตะ Breakout)"""
    is_long = direction.capitalize() == "Long"

    if df_1d is None:
        df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=as_of)
    df_4h = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=200, as_of=as_of)
    df_4h = merge_real_volume(df_4h, symbol, "4H", as_of=as_of)   # swing filter ใช้ volume

    trend_bias, bias_source = get_trend_bias(symbol, df_1d)
    if ENFORCE_BIAS_MATCH and trend_bias is None:
        reason = ("ไม่มีค่า k ใน TREND_FLIP_K" if bias_source == "no_trend_flip_k"
                  else "trend_flip ยัง bootstrap ไม่พร้อม")
        raise ValueError(f"หา Bias ไม่ได้ — {reason} ({bias_source}) — ข้ามรอบนี้")
    if ENFORCE_BIAS_MATCH and trend_bias != direction.capitalize():
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
    # EXEC_SL_ATR_MULT×ATR *หลัง* compute_score (ชื่อเดิม) จบไปแล้ว (ตั้งแต่ 2026-08-27) ระยะเสี่ยงจริงจึง
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
    if tp is None:
        fib_info  = find_tp_from_fibonacci(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if fib_info.get("passed"):
            tp = fib_info["tp"]   # อัตราส่วนมาจาก config.TP_FIB_RATIO (ดูที่มา/เหตุผลที่นั่น)
        else:
            # อิง MIN_RR_HARD_BLOCK = "ขั้นต่ำที่ยอมเทรด" (2026-08-26 แยกออกจาก MIN_RR ของ
            # สกอร์การ์ดที่ลบไปแล้ว — ตอนนั้น MIN_RR 2.0 เคยยืด TP ของไม้ fallback โดยไม่ตั้งใจ)
            # 2026-08-31: อิง exec_sl (ระยะเสี่ยงจริง) ไม่ใช่ sl โครงสร้าง — ไม่งั้น TP ที่ตั้งให้
            # ได้ R:R = MIN_RR_HARD_BLOCK พอดี จะให้ R:R จริงต่ำกว่าเกณฑ์ทันทีที่ SL ถูกขยับออก
            fallback_rr = MIN_RR_HARD_BLOCK
            tp = (entry + abs(entry - exec_sl) * fallback_rr) if is_long else \
                 (entry - abs(entry - exec_sl) * fallback_rr)

    # R:R วัดจาก exec_sl = ระยะเสี่ยงจริงที่จะส่ง broker (ดู comment ที่คำนวณ exec_sl ด้านบน)
    rr = calc_rr(entry, exec_sl, tp, direction)

    sl_info["sl"]           = sl          # SL โครงสร้าง — เป็น pinned_swing ของสูตร trailing
    sl_info["exec_sl"]      = exec_sl     # SL ที่ต้องส่ง broker จริง (ผู้เรียกใช้ตัวนี้ อย่าคำนวณเอง
    sl_info["atr_entry"]    = atr_entry   # ซ้ำ ไม่งั้นสองที่จะได้ ATR คนละค่าแล้ว R:R เพี้ยนอีก)
    sl_info["tp"]           = tp
    sl_info["rr"]           = rr
    sl_info["fib_info"]     = fib_info

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

    # Hard block: R:R ต่ำกว่าขั้นต่ำที่ยอมเทรด (ดูประวัติ/ตัวเลขที่ config.MIN_RR_HARD_BLOCK)
    _min_rr = MIN_RR_HARD_BLOCK if min_rr is None else min_rr
    if rr < _min_rr - 1e-9 and not force:
        raise ValueError(
            f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {_min_rr}  — ห้ามเข้า trade"
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

    return sl_info


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
        plan = compute_entry(symbol, direction, entry, sl, tp)

        print()
        print(f"{'=' * 48}")
        print(f"  {symbol}  |  {direction}  |  Entry {entry}")
        print(f"{'=' * 48}")
        if plan.get("swing_price"):
            print(f"  Swing        : {plan['swing_price']}   ATR buffer {plan['atr']}")
        print(f"  SL โครงสร้าง : {plan['sl']}")
        print(f"  SL ส่ง broker : {plan['exec_sl']}")
        fib = plan.get("fib_info", {})
        if fib.get("passed"):
            print(f"  TP (Fibonacci)  จุด 0 = {fib['origin']}  Move = {fib['move']}  -> {plan['tp']}")
        else:
            print(f"  TP (fallback R:R {MIN_RR_HARD_BLOCK}) : {plan['tp']}")
        print(f"  R:R (จาก SL ส่ง broker) = {plan['rr']:.2f}")
        print(f"{'=' * 48}")
        print("  >>> ผ่านทุกด่าน — เข้าไม้ได้ <<<")
        print(f"{'=' * 48}")

    except ValueError as exc:
        print(f"[BLOCKED] {exc}")
    except (EnvironmentError, ConnectionError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
