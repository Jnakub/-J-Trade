"""
reversal.py — Reversal Scoring: scorecard สำหรับเทรด "สวน" ทิศเทรนด์เดิม (คู่กับ scoring.py ที่เทรด "ตาม" เทรนด์)

ใช้ได้ต่อเมื่อ regime_check.py บอกว่า REVERSAL-READY เท่านั้น (ADX peak โค้งลงจริง
+ อยู่ที่ Key Level + มี Divergence) — ห้ามเปิดพร้อมกับ Scoring กับ setup เดียวกัน
(ห้าม scorecard shopping)

Reversal Scoring Checklist (weight รวม = 10, ผ่านเกณฑ์ที่ >= 7):
  1. Price อยู่ที่ Key Level สำคัญ?        weight 2  (ใช้ check_key_level เดียวกับ regime_check.py)
  2. มี Divergence ใน RSI?                 weight 3  (ใช้ check_divergence เดียวกับ regime_check.py —
                                                       backtest แม่นสุดในบรรดาเงื่อนไขทั้งหมด 82-93%
                                                       เลยเพิ่มน้ำหนักจาก 2 เป็น 3)
  3. RSI extreme (>70 Short, <30 Long)?    weight 1
  4. มี VSA Climax pattern?                weight 2  (Buying Climax=Short, Selling Climax=Long)
  5. [ตัดออก] Reversal Candlestick ที่ Key Level — backtest ทุกแบบ (เปล่า/+volume/+key level)
     ไม่พบว่าแม่นกว่าเดา (45-62%, บาง combo sample เหลือ 1-10 ครั้งแกว่ง 0-100%) จึงตัดทิ้ง
     น้ำหนักที่หายไป (1) ย้ายไปเพิ่มให้ข้อ 2 (Divergence) แทน ตามผลทดสอบจริง
  6. ADX peak แล้วเริ่มลงจริง?             weight 1  (relative peak & decline เหมือน regime_check.py
                                                       ไม่ใช้ threshold ตายตัว 40 — สอดคล้องกันทั้งระบบ)
  7. R:R >= 2.0?                           weight 1  (Reversal เสี่ยงกว่า ต้อง R:R สูงกว่า trend-follow)

ใช้: python reversal.py <SYMBOL> <Long/Short> <entry>
     python reversal.py <SYMBOL> <Long/Short> <entry> <sl> <tp>
"""
import sys
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import (MT5_TIMEFRAMES, RISK_PER_TRADE, MAX_RR_HARD_BLOCK,
                    MIN_RR_HARD_BLOCK_REVERSAL,
                    get_min_sl_distance_pct, MAX_TP_DISTANCE_PCT, TP_FIB_RATIO)
from mt5_connect import connect, get_account_balance
import scoring
from scoring import get_ohlcv, get_ohlcv_real, calc_rr
from swing import find_sl_from_structure, find_tp_from_fibonacci, swing_vol_multiplier, swing_wick_ratio_min
from regime_check import (
    calc_adx, adx_peak_info, check_key_level, check_divergence, calc_rsi,
    ADX_PERIOD, REGIME_TIMEFRAME, KEY_LEVEL_BARS,
)
from exit_monitor import is_climax_bar, calc_atr_trailing_sl, BARS as TRAIL_BARS
from order import calculate_lot_size, clamp_lot

WEIGHT_KEY_LEVEL   = 2
WEIGHT_DIVERGENCE  = 3
WEIGHT_RSI_EXTREME = 1
WEIGHT_VSA_CLIMAX  = 2
WEIGHT_ADX_DECLINE = 1
WEIGHT_RR          = 1
TOTAL_WEIGHT       = (WEIGHT_KEY_LEVEL + WEIGHT_DIVERGENCE + WEIGHT_RSI_EXTREME +
                      WEIGHT_VSA_CLIMAX + WEIGHT_ADX_DECLINE + WEIGHT_RR)   # = 10

# 2026-09-13: **ทดสอบปลดด่านนี้แล้ว (--rev-min-score=0) — เสมอตัว คงไว้ที่ 7**
#   replay 730 วัน 5 symbol (EUR/US500 ข้าม สกอร์การ์ดตัด 0 รอบ): +32.04R -> +30.75R (ΔR -1.29)
#   XAU +0.75 | GBP +0.20 | ETH +0.07 | BTC -1.05 | USDJPY -1.26
#
# ⚠️ ด่านนี้แทบไม่ได้กรองอะไรตั้งแต่แรก — regime REVERSAL-READY บังคับ Key Level + Divergence
# + ADX peak โค้งลง อยู่แล้ว = 2+3+1 = **6 แต้มฟรีจาก 7 ที่ต้องการ** เหลือหาอีกแค่ 1 แต้ม
# จาก VSA Climax (2) หรือ R:R (1) ผลคือปัดตกจริงแค่ 3% ของรอบที่ถึงมือมัน (80/2650 บน 7 symbol)
#
# คะแนนไม่สัมพันธ์กับผลเลย (เทียบหลังได้ไม้ score 6 มาครบแล้ว):
#   score 6.0  9 ไม้ avgR +0.33  |  7.0  6 ไม้ +1.07  |  8.0  8 ไม้ +0.79  |  9.0  5 ไม้ +0.36
# = ลายเซ็นเดียวกับสกอร์การ์ด Scoring ที่เลิกใช้กรองไปแล้ว (ดู config.MIN_SCORE)
#
# ที่ ΔR ติดลบไม่ได้มาจาก "ไม้ที่ปล่อยเข้ามาแย่" — ไม้ใหม่ 9 ไม้ทำ +3.00R (จบด้วย TP 5 ไม้)
# แต่มาจาก **การกินช่อง**: ปลดด่านทำให้เข้าเร็วขึ้น 1-26 ชม. ไม้นั้นยึดช่อง Reversal ไว้
# ไม้ทีหลังที่คะแนนสูงกว่าจึงเข้าไม่ได้ หายไป 3 ไม้ +4.29R (TP ทั้งสามไม้)
#   GBP  ใหม่ 01-16 06:00 (s6, +0.49) แทน 01-17 08:00 (s9, +1.31)
#   JPY  ใหม่ 02-26 19:00 (s6, +1.26) แทน 02-26 20:00 (s7, +1.48)
#   XAU  ใหม่ 05-05 17:00 (s6, +1.36) แทน 05-05 20:00 (s7, +1.50)
# 👉 บทเรียน: การปลดด่านที่ไม่แตะการตรวจจับเลย ยังทำไม้เดิมหายได้ผ่านลำดับการกินช่อง
#    ต้องเทียบไม้ทีละตัวเสมอ ถึงจะดูเหมือน "เพิ่มอย่างเดียว" ก็ตาม
#
# RSI extreme **ไม่เคยผ่านเลย 0/19 ไม้** ใน 2 ปี = คะแนนเต็มที่เป็นไปได้จริงคือ 9 ไม่ใช่ 10
# (ยังไม่ได้แตะ — แยกเป็นอีกเรื่อง)
# ทดสอบซ้ำ: ./run_wine.sh backtest_replay.py GBPUSDm 730 --rev-min-score=0 --log-cuts
MIN_SCORE_REVERSAL = 7
MIN_RR_REVERSAL    = 2.0   # เกณฑ์ "R:R ดีจริง" ในสกอร์การ์ด + สูตร fallback TP (ยังใช้ 2.0 เหมือนเดิม)
# MIN_RR_HARD_BLOCK มาจาก config.py (2026-08-01) — เดิม duplicate ค่า 1.0 แยกไว้เองในไฟล์นี้
# ทำให้ถ้าแก้ค่าใน config.py แล้วลืมแก้ที่นี่ด้วย จะเพี้ยนกันเงียบๆ ระหว่าง Scoring กับ Reversal
RSI_OVERBOUGHT     = 70
RSI_OVERSOLD       = 30

# ── สวิตช์ทดลอง (2026-09-01) — ค่า default = พฤติกรรมระบบจริงเป๊ะ ห้ามแก้ค่าตรงนี้ ────────
# backtest_replay.py ตั้งค่าให้เฉพาะรอบที่รันด้วย --rev-min-sl / --rev-tp-from-entry
# (แบบเดียวกับ scoring.DISABLED_CRITERIA / scoring.MIN_SCORE) เพื่อตอบว่าด่านสองตัวที่ตัด
# โอกาส Reversal ทิ้งมากที่สุดควรปรับไหม โดยไม่ต้องแก้ config.py แล้วเผลอมีผลกับระบบจริง
#
# MIN_SL_OVERRIDE: ทับ get_min_sl_distance_pct() เฉพาะทาง Reversal — วัดจากไม้จริง 2 ปี พบว่า
#   setup ที่ SL แคบกว่า 0.9% (BTC 23 แท่ง) คือกลุ่มที่ R:R ดีที่สุดในกองทั้งหมด (มัธยฐาน 8.8)
#   เพราะเป็น entry ที่ชิดโครงสร้างที่สุด — ซึ่งเป็นรูปแบบปกติของไม้สวน (SL ชิดเหนือยอดกลับตัว)
#   ต่างจากฝั่ง Scoring ที่ SL อยู่ที่ swing ของ pullback ซึ่งค่า 0.9% ถูก backtest มาแล้ว
# TP_FROM_ENTRY: ฉาย Fibonacci extension จาก "ราคาเข้า" แทน swing B — สูตรเดิมฉายจาก B ทำให้
#   reward หดและ risk โตพร้อมกัน 1:1 ตามระยะที่ราคาห่างจาก B มาแล้ว (TP ไปโผล่หลัง entry
#   28% ของ setup บน BTC / 22% บน XAU) ตัวนี้ทำให้ TP ไกลกว่าเดิมเสมอ = ผ่อนด่าน R:R
#   ⚠️ ไม่ใช่การแก้บั๊ก แต่เป็นการผ่อนเกณฑ์ ต้องดู WR ควบคู่กับ Total R เสมอ
#
# ผล backtest_replay 730 วัน (2026-09-01) — **ทั้งคู่แย่กว่าของเดิม ยังไม่เอาเข้าระบบจริง**:
#   ของเดิม            BTC +2.74R (26 ไม้, Reversal 6 WR 83%)   XAU -1.46R (15 ไม้)
#   --rev-min-sl=0.5   BTC +3.15R (27 ไม้, Reversal 7 WR 86%)   XAU -2.35R (16 ไม้) => รวม -0.48R
#   --rev-tp-from-entry BTC +2.59R (26 ไม้, Reversal 9 WR 67%)  XAU -1.99R (16 ไม้) => รวม -0.68R
#   ไม้ที่เพิ่มมามีแค่ 1-3 ไม้/symbol/2 ปี (ยังตัดสินทางสถิติไม่ได้) แต่ทั้งสองทางเอียงไปทางลบเหมือนกัน
#   ข้อสังเกตที่สำคัญกว่าตัวเลข: ผ่อนด่าน Reversal แล้ว "ไม้ Scoring หายไปแทน" (BTC 20 -> 17 ไม้)
#   เพราะระบบถือได้ทีละไม้ต่อ symbol — ด่านจริงที่ตัดโอกาส Reversal ทิ้งมากที่สุดคือ "ถือไม้อื่นอยู่"
#   (195-205 จาก 304 รอบ REVERSAL-READY ของ BTC) ไม่ใช่ตัวกรองเข้าไม้ การผ่อนตัวกรองจึงได้แค่
#   ย้ายไม้จากทาง Scoring มาทาง Reversal ไม่ได้เพิ่มจำนวนไม้รวม
MIN_SL_OVERRIDE    = None
TP_FROM_ENTRY      = False
_MIN_RR_OVERRIDE   = None   # backtest_replay --rev-min-rr ตั้งให้ (None = ใช้ค่าจาก config)

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)


# ---------------------------------------------------------------------------
# Reusable scoring function (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def compute_reversal_score(symbol: str, direction: str, entry: float,
                           sl: float = None, tp: float = None,
                           force: bool = False, key_level: dict = None,
                           df_4h: pd.DataFrame = None, as_of: datetime = None) -> tuple[float, list, bool, dict]:
    """Return (total_score, criteria_list, passed, info).
    ถ้าไม่ส่ง sl/tp จะหาจาก swing structure/Fibonacci อัตโนมัติ (เกณฑ์เดียวกับ scoring.py)
    key_level: ส่งผลลัพธ์ check_key_level ที่ regime_check.get_regime() คำนวณไว้แล้วมาใช้ซ้ำได้
    (ราคาปิดแท่ง 4H เดียวกับที่ปลดล็อก REVERSAL-READY) — ถ้าไม่ส่งมา จะคำนวณใหม่ด้วย `entry` เอง
    df_4h: ส่ง get_regime()["df_4h"] มาใช้ซ้ำได้ (real volume, BARS แท่ง, ยังไม่ตัดแท่งฟอร์มมิ่ง)
    กันดึง+merge real volume จาก Bitstamp ซ้ำสองรอบต่อรอบสแกน — ถ้าไม่ส่งมาจะดึงเองเหมือนเดิม

    as_of=None (ปกติ) = เช็คสด ณ ตอนนี้ — as_of=datetime = จำลองเช็ค ณ เวลานั้นในอดีต
    (2026-08-02, ตามแบบ scoring.compute_score) ให้ backtest เรียกตัวนี้ตรงๆ ได้ ไม่ต้อง copy
    logic มาเขียนซ้ำ — มีผลเฉพาะตอนไม่ได้ส่ง key_level/df_4h มาเอง (จะ fetch ย้อนหลังแทนสด)"""
    is_long = direction.capitalize() == "Long"

    if df_4h is None:
        df_4h = get_ohlcv_real(symbol, "4H", bars=210, as_of=as_of)
    # ตัดแท่งยังไม่ปิดทิ้ง — ทั้งสองโหมด เพราะแท่งท้ายสุดของเฟรมคือแท่งฟอร์มมิ่งเสมอ ไม่ว่า
    # as_of จะเป็นอะไร (bars.get_bars ประกอบแท่งฟอร์มมิ่งจาก TF ย่อยต่อกลับเข้าไปให้ในโหมด
    # backtest ด้วย เพื่อให้หน้าตาเฟรมเหมือนตอนรันสดเป๊ะ — ดู docstring bars.get_bars)
    #
    # 2026-09-01: เดิมบรรทัดนี้เป็น `if as_of is None:` ตามความเข้าใจของ 2026-08-09 ว่าโหมด
    # backtest get_ohlcv_real ตัดแท่งฟอร์มมิ่งให้เองแล้ว — จริงตอนนั้น แต่หมดอายุไปตั้งแต่
    # 2026-08-26 ที่ย้ายตัวดึงแท่งไป bars.get_bars() (ตัวใหม่ต่อแท่งฟอร์มมิ่งกลับเข้ามา) ผลคือ
    # โหมด backtest มองเห็นแท่งเกินมา 1 แท่งเทียบกับ get_regime() ที่ตัดแท่งท้ายทิ้งเสมอ
    # (regime_check.get_regime ส่ง df_4h.iloc[:-1] ให้ check_divergence) วัดจริงบน 2 ปี:
    # divergence ที่สกอร์การ์ดคำนวณเองไม่ตรงกับตอนที่ regime ปลดล็อก REVERSAL-READY
    # 24/205 แท่ง (BTC) และ 39/379 (XAU) = เสียแต้ม Divergence (3 จาก 10) ฟรีๆ ทั้งที่ regime
    # เพิ่งยืนยันว่ามี ทำให้ตัวเลข Reversal ใน backtest ต่ำกว่าที่ระบบจริงทำได้
    # โหมดรันสดไม่เคยโดนบั๊กนี้ (scheduler ส่ง df_4h มาพร้อม as_of=None -> ตัดอยู่แล้ว)
    # กระทบทุก criteria ที่อ้างอิง df_4h ตัวนี้ (Key Level/Divergence/RSI/VSA Climax/SL/TP)
    df_4h = df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True)
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    # หา SL อัตโนมัติจาก swing structure (4H) ถ้าไม่ได้กรอกมา — เกณฑ์เดียวกับ scoring.py
    sl_info = {}
    if sl is None:
        # ส่ง entry เป็น current_price ด้วยเหตุผลเดียวกับ scoring.py (ดู swing.py)
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.22,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min,
                                         current_price=entry)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # ── SL ที่จะส่ง broker จริง (exec_sl) ────────────────────────────────────────────────
    # 2026-09-05: ยกวิธีเดียวกับ scoring.compute_score (แก้ไปเมื่อ 2026-08-31) มาใช้กับฝั่ง
    # Reversal ที่ตอนนั้นไม่ได้แก้ตาม — scheduler/backtest ขยับ SL ออกอีก EXEC_SL_ATR_MULT×ATR
    # *หลัง* compute_reversal_score จบไปแล้ว ด่าน R:R จึงตรวจด้วยไม้บรรทัดที่แคบกว่าของจริง
    # วัดจากไม้จริง 8 symbol (123 ไม้): R:R ที่ด่านเห็นเฉลี่ย 4.67 แต่ R:R จริง 2.83 และ
    # **19 จาก 39 ไม้ Reversal (49%) ผ่านด่าน MIN_RR_HARD_BLOCK=1.5 มาได้ทั้งที่ R:R จริง < 1.5**
    # (เฉลี่ย 1.17) ฝั่ง Scoring ไม่มีเคสแบบนี้เลยเพราะแก้ไปแล้ว — ความเสียหายกระจุกที่ EURUSDm
    # (4 ไม้ -2.91R จากทั้ง symbol -3.64R) แต่รวมทุก symbol กลุ่มนี้ยังเป็นบวก (+0.70R) จึงต้อง
    # วัดผลรวมหลังแก้ ไม่ใช่ถือว่าแก้แล้วดีขึ้นแน่นอน
    # เกณฑ์ไม่เปลี่ยน เปลี่ยนแค่ไม้บรรทัดที่ใช้วัดให้ตรงกับสิ่งที่ระบบทำจริง
    atr_entry, exec_sl = None, sl
    if scoring.EXEC_SL_ATR_MULT:
        try:
            _t = as_of if as_of is not None else datetime.now()
            _tr = calc_atr_trailing_sl(get_ohlcv_real(symbol, "4H", bars=TRAIL_BARS, as_of=as_of),
                                       symbol, _t, direction.capitalize(), as_of=as_of)
            if _tr:
                atr_entry = _tr["atr_entry"]
                exec_sl = (sl - scoring.EXEC_SL_ATR_MULT * atr_entry) if is_long else \
                          (sl + scoring.EXEC_SL_ATR_MULT * atr_entry)
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
            if TP_FROM_ENTRY:     # โหมดทดลอง — ระยะเท่าเดิม (move × TP_FIB_RATIO) แต่ตั้งต้นที่ราคาเข้า
                _proj = fib_info["move"] * TP_FIB_RATIO
                tp = (entry + _proj) if is_long else (entry - _proj)
        else:
            used_fallback_tp = True
            # อิง exec_sl (ระยะเสี่ยงจริง) เหมือน scoring.py — ไม่งั้น TP ที่ตั้งจากสูตร fallback
            # จะให้ R:R จริงต่ำกว่า MIN_RR_REVERSAL ที่ตั้งใจไว้
            tp = (entry + abs(entry - exec_sl) * MIN_RR_REVERSAL) if is_long else \
                 (entry - abs(entry - exec_sl) * MIN_RR_REVERSAL)

    # R:R วัดจาก exec_sl = ระยะเสี่ยงจริงที่จะส่ง broker (ดู comment ที่คำนวณ exec_sl ด้านบน)
    rr = calc_rr(entry, exec_sl, tp, direction)

    # ── ข้อ 1: Key Level ──
    if key_level is None:
        key_level_df = get_ohlcv_real(symbol, "4H", bars=KEY_LEVEL_BARS, as_of=as_of) if as_of is not None else None
        key_level = check_key_level(symbol, entry, df=key_level_df, as_of=as_of)
    at_key = bool(key_level.get("at_key_level"))

    # ── ข้อ 2: Divergence RSI ──
    divergence = check_divergence(df_4h, symbol=symbol)
    div_dir    = divergence.get("divergence")
    div_ok     = (div_dir == "bullish") if is_long else (div_dir == "bearish")

    # ── ข้อ 3: RSI extreme — เช็คที่จุด swing ของ divergence (h2/l2) แทนตอนนี้ (2026-07-23)
    #    เหตุผล: กว่า ADX จะโค้งลงยืนยันครบ + เงื่อนไขอื่นจะครบพร้อมกัน RSI มักอ่อนตัวจากจุด
    #    สุดขั้วไปแล้วหลายแท่ง (พบจริงจาก backtest — เคสหนึ่งห่างกันถึง 8 แท่ง) เช็คตอนนี้เลย
    #    มักพลาดสัญญาณที่จริงๆ สุดขั้วมาก่อนหน้านี้แล้ว ไม่ใช่แค่ "หน้าต่างเวลาแคบไป" (ลอง
    #    ขยายหน้าต่างเช็คย้อนหลัง 3-5 แท่งแล้วไม่ช่วยเลยใน backtest — ต้องเช็คที่จุดจริง)
    rsi_series    = calc_rsi(df_4h["close"])
    div_swing_idx = divergence.get("swing_idx")
    rsi_ref_idx   = div_swing_idx if div_swing_idx is not None else len(df_4h) - 1
    rsi_now       = rsi_series.iloc[rsi_ref_idx]
    rsi_ok        = (rsi_now <= RSI_OVERSOLD) if is_long else (rsi_now >= RSI_OVERBOUGHT)

    # ── ข้อ 4: VSA Climax (Selling Climax=Long, Buying Climax=Short) — เช็คตั้งแต่จุด
    #    swing ของ divergence จนถึงตอนนี้ (ไม่ใช่แค่แท่งปัจจุบันเป๊ะ) เหตุผลเดียวกับข้อ 3
    climax_start_idx = div_swing_idx if div_swing_idx is not None else len(df_4h) - 1
    climax_ok = False
    climax_pattern = None
    for k in range(climax_start_idx, len(df_4h)):
        c, pat = is_climax_bar(df_4h, k)
        if c and ((is_long and pat == "Selling Climax") or (not is_long and pat == "Buying Climax")):
            climax_ok, climax_pattern = True, pat
            break
    climax = climax_ok

    # ── ข้อ 6: ADX peak แล้วเริ่มลงจริง (relative — ไม่ใช้ threshold 40 ตายตัว) ──
    df_adx     = get_ohlcv(symbol, REGIME_TIMEFRAME, bars=210, as_of=as_of)
    adx_series = calc_adx(df_adx, ADX_PERIOD)
    peak       = adx_peak_info(adx_series, len(df_adx) - 2)
    adx_ok     = peak["declining"]

    criteria = [
        ("Key Level",   at_key,    WEIGHT_KEY_LEVEL),
        ("Divergence",  div_ok,    WEIGHT_DIVERGENCE),
        ("RSI extreme", rsi_ok,    WEIGHT_RSI_EXTREME),
        ("VSA Climax",  climax_ok, WEIGHT_VSA_CLIMAX),
        ("ADX peak+decline", adx_ok, WEIGHT_ADX_DECLINE),
        # 2026-07-26: ไม่ให้แต้มถ้า TP มาจากสูตร fallback — เหตุผลเดียวกับ scoring.py เป๊ะ
        # (สูตร fallback อิง MIN_RR_REVERSAL เอง ทำให้ rr == MIN_RR_REVERSAL เสมอ = แต้มฟรี)
        # TP ที่กรอกเองมายังได้แต้มตามปกติ
        ("R:R",         (not used_fallback_tp) and rr >= MIN_RR_REVERSAL - 1e-9, WEIGHT_RR),
    ]

    info = {
        "sl": sl, "tp": tp, "rr": rr,
        # SL ที่ต้องส่ง broker จริง — ผู้เรียกใช้ตัวนี้ อย่าคำนวณเองซ้ำ ไม่งั้นสองที่จะได้ ATR
        # คนละค่าแล้ว R:R เพี้ยนอีก (บทเรียนเดียวกับ scoring.py 2026-08-31)
        "exec_sl": exec_sl, "atr_entry": atr_entry,
        "sl_info": sl_info, "fib_info": fib_info,
        "key_level": key_level, "divergence": divergence,
        "rsi_now": rsi_now, "climax": climax, "climax_pattern": climax_pattern,
        "adx_peak": peak,
    }

    # Hard block: กันแค่ไม่ให้เสี่ยงมากกว่าได้ (R:R < 1) — ไม่สนคะแนนรวม
    # ส่วน "R:R ดีจริง" (>= MIN_RR_REVERSAL) ยังต้องผ่านสกอร์การ์ดแยกต่างหากด้านล่าง
    # Hard block: ระยะ entry->SL แคบเกิน — ดู comment ที่ config.MIN_SL_DISTANCE_PCT /
    # config.get_min_sl_distance_pct() (Forex/Index ใช้เกณฑ์ต่ำกว่า — ดู scoring.py comment)
    min_sl_pct = MIN_SL_OVERRIDE if MIN_SL_OVERRIDE is not None else get_min_sl_distance_pct(symbol)
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct < min_sl_pct - 1e-9 and not force:
        raise ValueError(f"ระยะ SL ห่างจาก entry แค่ {sl_distance_pct:.2f}% ต่ำกว่าขั้นต่ำ "
                         f"{min_sl_pct}% — ห้ามเข้า trade")

    # เกณฑ์แยกของ Reversal (ไม่ใช่ MIN_RR_HARD_BLOCK ที่ Scoring ใช้) — ดูที่มาที่ config.py
    min_rr = MIN_RR_HARD_BLOCK_REVERSAL if _MIN_RR_OVERRIDE is None else _MIN_RR_OVERRIDE
    if rr < min_rr - 1e-9 and not force:
        raise ValueError(f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {min_rr} — ห้ามเข้า trade")

    # Hard block: R:R สูงผิดปกติ — ดู comment ที่ config.MAX_RR_HARD_BLOCK
    if rr > MAX_RR_HARD_BLOCK + 1e-9 and not force:
        raise ValueError(f"R:R = {rr:.2f} สูงเกินขั้นสูงสุด {MAX_RR_HARD_BLOCK} — ห้ามเข้า trade")

    # Hard block: TP ไกลเกินจนราคาไปไม่ถึง — ดู comment ที่ config.MAX_TP_DISTANCE_PCT
    tp_distance_pct = abs(tp - entry) / entry * 100
    if tp_distance_pct > MAX_TP_DISTANCE_PCT + 1e-9 and not force:
        raise ValueError(f"TP ห่างจาก entry {tp_distance_pct:.1f}% เกินขั้นสูงสุด "
                         f"{MAX_TP_DISTANCE_PCT}% — ห้ามเข้า trade")

    total  = sum(w for _, passed, w in criteria if passed)
    passed = total >= MIN_SCORE_REVERSAL
    return total, criteria, passed, info


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_report(symbol: str, direction: str, entry: float, score: float,
                 criteria: list, passed: bool, info: dict, balance: float) -> None:
    sl, tp, rr = info["sl"], info["tp"], info["rr"]
    risk_pct   = abs(entry - sl) / entry * 100
    reward_pct = abs(tp - entry) / entry * 100
    lot, decimals = calculate_lot_size(symbol, entry, sl, balance, RISK_PER_TRADE)

    print()
    print("=" * 66)
    print(f"{BOLD}  REVERSAL SCORING — {symbol}  ({direction}){RESET}")
    print(f"  {YELLOW}⚠ Reversal เสี่ยงกว่า Trend Following — Win Rate ต่ำกว่า ต้อง R:R สูงกว่า{RESET}")
    print("=" * 66)
    print(f"  Entry Price : {entry:,.3f}")
    print(f"  Stop Loss   : {sl:,.3f}")
    print(f"  Take Profit : {tp:,.3f}")
    print(f"  Risk %      : {risk_pct:.2f}%")
    print(f"  Reward %    : {reward_pct:.2f}%")
    print(f"  R:R Ratio   : {rr:.2f}x")
    print(f"  Position Size (Lot): {lot:.{decimals}f}")
    print("-" * 66)
    print(f"{BOLD}  Reversal Scoring Checklist  (เกณฑ์ผ่าน: {MIN_SCORE_REVERSAL}/{TOTAL_WEIGHT}){RESET}")
    print("-" * 66)
    for name, ok, weight in criteria:
        ans_str = f"{GREEN}YES{RESET}" if ok else f"{RED}No {RESET}"
        print(f"  [{ans_str}]  {name:<16} weight={weight}  score={weight if ok else 0}")
    print("-" * 66)
    score_color = GREEN if passed else RED
    print(f"  {BOLD}TOTAL SCORE{RESET} : {score_color}{score:.0f} / {TOTAL_WEIGHT}{RESET}")
    print(f"  {BOLD}ผลลัพธ์{RESET}     : {score_color}{'ผ่าน — เปิดไม้ได้' if passed else 'ไม่ผ่าน — ห้ามเข้า'}{RESET}")
    print("=" * 66)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) not in (4, 6):
        print("Usage : python reversal.py <SYMBOL> <Long/Short> <entry>")
        print("        python reversal.py <SYMBOL> <Long/Short> <entry> <sl> <tp>")
        print("Example (auto SL/TP) : python reversal.py XAUUSDm Long 4060")
        sys.exit(1)

    symbol    = sys.argv[1]
    direction = sys.argv[2].capitalize()
    entry     = float(sys.argv[3])
    sl        = float(sys.argv[4]) if len(sys.argv) == 6 else None
    tp        = float(sys.argv[5]) if len(sys.argv) == 6 else None

    load_dotenv()

    try:
        connect()
        balance = get_account_balance()
        score, criteria, passed, info = compute_reversal_score(symbol, direction, entry, sl, tp)
        print_report(symbol, direction, entry, score, criteria, passed, info, balance)
    except ValueError as exc:
        print(f"{RED}[ไม่ผ่าน]{RESET} {exc}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
