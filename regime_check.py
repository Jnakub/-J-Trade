"""
regime_check.py — REGIME CHECK: ตลาดตอนนี้เป็นแบบไหน? เปิด scorecard ไหน?

ใช้ก่อนเปิด Scoring/Reversal ทุกครั้ง — อ่าน ADX(20) จาก TF 4H
ตอบว่าตอนนี้ควรใช้ scorecard ไหน: Scoring (เทรดตามเทรนด์) หรือ Reversal (เทรดสวน)

Checklist:
  1. ADX(20) ปัจจุบัน                — ค่า ADX แท่งปิดล่าสุดบน 4H
  2. ทิศของเส้น ADX                  — ขึ้น / ทรง / ลง (เทียบ 3 แท่งล่าสุด)
  3. ADX peak                        — มองย้อน 10 แท่ง หา peak แล้วเช็คว่าโค้งลงหรือยัง
  4. โครงสร้าง trend ยัง intact?     — Swing High/Low บน 1D (HH/HL = Long, LL/LH = Short)
  5. ราคาอยู่ที่ Key Level สำคัญ?     — โซนแนวรับ/ต้าน (Swing High+Low แตะซ้ำ >=2 ครั้ง, ห่าง <=1%)
  6. มี Divergence (RSI)?            — ราคาทำ new extreme แต่ RSI(14) ไม่ทำตาม
                                        (สดภายใน 10 แท่ง + จุดแรกต้องเคย overbought/oversold จริง)

กฎ Mutual Exclusivity:
  - ห้ามเปิด 2 scorecard กับ setup เดียวกัน (ห้าม scorecard shopping)
  - Scoring เข้า "ตาม" ทิศเทรนด์ / Reversal เข้า "สวน" ทิศเทรนด์เดิมเท่านั้น
  - Reversal ใช้เกณฑ์ relative peak & decline — ADX ทำจุดสูงสุดใหม่ในรอบที่มองย้อน (10 แท่ง)
    ไม่ว่าตัวเลขจะเป็นเท่าไหร่ (28, 32, 45) แล้วโค้งลงติดกัน 3 แท่ง ถือว่าเข้าเกณฑ์ Reversal
    (ไม่ใช้ threshold ตายตัวอย่าง 40 อีกต่อไป เพราะ "แรงสุดของรอบนั้น" ไม่จำเป็นต้องแตะเลขคงที่)
    ส่วน ADX>=40 ที่ยังพุ่งไม่หยุด (ยังไม่ peak) = ห้ามสวนเด็ดขาด
  - CHOPPY (ADX<20) = พัก การไม่เทรดคือ position ที่ถูกต้อง
  - Regime ใช้ 2 จังหวะ: ก่อนเข้า → เลือก scorecard | ระหว่างถือ → ADX>=25 ใช้ Structure Stop trail

ใช้: python regime_check.py                  # เช็คทุก symbol ใน config
     python regime_check.py BTCUSDm          # เช็คเฉพาะ symbol ที่ระบุ
"""
import sys
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import MT5_TIMEFRAMES, SYMBOLS
from mt5_connect import connect
from scoring import get_ohlcv, get_ohlcv_real
from swing import find_swing_highs, find_swing_lows, swing_vol_multiplier, swing_wick_ratio_min, collapse_swing_runs
from indicators import calc_adx, calc_rsi as _calc_rsi
from bars import BAR_OFFSET_H as ADX_BAR_OFFSET_H, get_aligned_4h

REGIME_TIMEFRAME = MT5_TIMEFRAMES["4H"]
BARS             = 210

ADX_PERIOD       = 20     # ทดสอบลดเป็น 14 แล้ว (2026-07-20) — regime threshold ไวขึ้นจริงแต่ noise
                          # เพิ่มมาก (BTC +32%, XAU +75% transitions) ขณะที่กลไก peak-decline ของ
                          # Reversal แทบไม่เร็วขึ้นเลย (~0 แท่งเฉลี่ย) เลย revert กลับเป็น 20 ตามเดิม
ADX_CHOPPY       = 20     # ต่ำกว่านี้ = CHOPPY ห้ามเทรด
ADX_GRAY_HIGH    = 22     # 20-22 = เขตเทา รอ ADX เลือกทาง (ปรับจาก 25 — 2026-07-22 หลัง backtest
                          # เทียบ 20/22/25/28/30 บน BTCUSDm 96 วัน: 22 เปิด scorecard ได้ 31.2%
                          # ของเวลา vs 25 เดิมที่ 23.4% โดย REVERSAL-READY แทบไม่เปลี่ยน (คอขวดจริง
                          # คือ Key Level+Divergence ไม่ใช่ ADX) — ยังไม่มี full win-rate backtest
                          # ยืนยัน แค่ยืนยันว่าเปิดโอกาสได้บ่อยขึ้น
ADX_REVERSAL     = 40     # peak ต้อง >= 40 ถึงเข้าเกณฑ์ Reversal

# ---------------------------------------------------------------------------
# ⚠️ ADX_BAR_OFFSET_H — ย้ายไปอยู่ที่ bars.BAR_OFFSET_H แล้ว (import เป็น alias ชื่อเดิมด้านบน)
# ---------------------------------------------------------------------------
# ปัญหา (เจอ 2026-08-12 ตอนเพิ่ม US500m): MT5 ตัดแท่ง 4H ตามเวลาเซิร์ฟเวอร์โบรก แต่ TradingView
# ตัดตาม session ของ feed ที่เลือก — ขอบแท่งคนละจุด => OHLC ทุกแท่งต่างกัน => TR/DM ต่างกัน =>
# ADX ต่างกัน ทั้งที่ period เดียวกันและสูตรเดียวกันเป๊ะ
#
# วัดจริงบน 4H แท่งปิดล่าสุด (รวมแท่ง 1H เป็น 4H ด้วย offset 0-3 ชม.) — ADX แกว่งแรงมาก:
#   BTCUSDm  18.67 / 18.20 / 15.43 / 15.23   (แกว่ง 3.45)
#   XAUUSDm  38.91 / 40.54 / 42.70 / 39.53   (แกว่ง 3.79)
#   ETHUSDm  16.48 / 14.20 / 15.73 / 14.03   (แกว่ง 2.45)
#   XRPUSDm  26.00 / 31.29 / 33.90 / 28.63   (แกว่ง 7.90 — ไวที่สุด)
#   USDJPYm  26.70 / 28.11 / 26.22 / 29.09   (แกว่ง 2.87)
#   US500m   21.51 / 23.29 / 25.93 / 26.51   (แกว่ง 5.00 — ข้ามเส้น ADX_GRAY_HIGH ได้)
# ตอนวัด มีแค่ US500m ที่ค่าคร่อมเส้น 22 พอดีจนเปลี่ยนคำตัดสิน regime (เทา <-> TREND) ตัวอื่น
# ค่าอยู่ลึกในเขตเดียวกันหมดเลยยังไม่เห็นผล — แต่เป็นปัญหาแฝงของทุก symbol วันไหนค่าเข้าใกล้
# เส้น 20 หรือ 22 ก็เจอทันที
#
# 2026-08-18: **ขยายขอบเขตจาก "เฉพาะ ADX" เป็น "ทุกอย่างที่แตะแท่ง 4H"** ตามคำสั่งผู้ใช้ —
# เดิมจำกัดไว้แค่ ADX/regime เพราะ vol/wick ที่ tune ไว้ผูกกับแท่ง MT5 ดิบ ตอนนี้ dict นี้ย้าย
# ไปอยู่ที่ bars.BAR_OFFSET_H และ scoring.get_ohlcv() route แท่ง 4H ทั้งหมดผ่าน bars.py แล้ว
# (ดู bars.py docstring เต็ม) แปลว่า swing/SL/TP/structure break/key level/divergence ของ
# symbol ที่ offset != 0 เปลี่ยนไปด้วย — vol_multiplier/wick_ratio_min ที่เคย tune ไว้ก่อนหน้า
# (XAU/ETH/USDJPY/US500/GBP) ควรเช็คตาราง swing ซ้ำด้วย inspect_swings.py
#
# 🔴 เพิ่ม symbol ใหม่ต้องมาเช็คตรงนี้ทุกครั้ง: เปิดกราฟ 4H บน TradingView ตั้ง ADX เป็น 20/20
#    เทียบกับที่ระบบคำนวณ ลอง offset 0-3 หาว่าอันไหนตรงที่สุด (แก้ที่ bars.BAR_OFFSET_H)
#    symbol ที่ไม่มีในนี้จะ default = 0 (ใช้แท่ง MT5 ตรงๆ) ซึ่งอาจไม่ตรงกับ TradingView
#
# หมายเหตุข้อจำกัด: ต่อให้ offset ตรงแล้วค่าก็ยังไม่เท่า TradingView เป๊ะ 100% เพราะ feed ราคา
# คนละเจ้ากัน (Exness vs feed ที่ TradingView ใช้) เช่น US500m offset=2 ได้ 25.93 แต่จอได้
# 25.44 — alignment อธิบายช่องว่างได้ ~90% ที่เหลือแก้ไม่ได้เพราะไม่มีข้อมูลของเขา

# ค่าเดิมจาก backtest 400 แท่ง 1D (BTC/XAU): 5 แท่งนิ่งกว่า 3 แท่งชัดเจน (ทิศสลับ ~52 vs
# ~76 ครั้ง) — แต่ backtest นั้นทำบน 1D ทั้งที่ตัวจริงรันบน 4H (คนละ scale เวลา) เลยรัน
# ใหม่บน 4H โดยตรง (2026-07-24, ~5000 แท่ง 2+ ปี ทั้ง BTC/XAU, ไม่ติด limit เพราะ ADX ไม่พึ่ง
# volume): วัด 2 มิติคู่กัน — ความนิ่ง (ทิศสลับกี่%) และ responsiveness (lag กี่แท่งกว่าจะจับ
# จุดพลิกจริงของ ADX ได้) — BTC ที่ 4 แท่งดีกว่า 3 และ 5 พร้อมกันทุกมิติ (lag ต่ำสุด 5.29
# แท่ง, พลาดสัญญาณน้อยสุด 10 ครั้ง, นิ่งกว่า 3 ด้วย) ไม่ใช่แค่ trade-off ธรรมดา — XAU ที่ 5
# แท่งยังเป็นจุดสมดุลที่ดีอยู่ (แต่ยังไม่ได้แยกค่าตาม symbol ตอนนี้ ใช้ 4 ร่วมกันทั้งคู่)
#   ลงติดกัน 3 แท่ง -> ADX ลงต่อจริง 100% (2 แท่งได้ 89-96%)
ADX_DIR_BARS     = 4      # ทิศ ADX เทียบกี่แท่ง
ADX_DIR_EPS      = 1.0    # เปลี่ยน < ค่านี้ = "ทรง"
ADX_PEAK_LOOKBACK = 10    # ADX peak มองย้อนกี่แท่ง
ADX_DECLINE_BARS  = 3     # โค้งลงติดกันกี่แท่งจาก peak = "เริ่มลงจริง"

# Swing High/Low ของ ADX เอง (คนละตัวกับ find_swing_highs/lows ที่ใช้กับราคา — ไม่มี volume filter)
# ทดสอบแล้ว left/right=7 แคบไป มองไม่เห็นยอดเขาที่อยู่ไกล ทำให้ V-shape ลึกจริงหลุดไป
# left/right=14 จับจุดได้แม่นขึ้น แลกกับยืนยันช้าลง (~2.3 วันบน 4H)
ADX_SWING_LEFT_RIGHT  = 14
ADX_SWING_PROMINENCE  = 3.0

SWING_LEFT_RIGHT = 4
SWING_TOLERANCE  = 0.22   # 2026-07-24: เปลี่ยนจาก 0.25 — ยังไม่มี backtest ยืนยัน

# Key Level — โซนแนวรับ/แนวต้านใหญ่ (รวม Swing High+Low เป็นโซนเดียวกัน — support เก่ากลายเป็น resistance ใหม่ได้)
# ค่าจาก backtest จริง (BTC/XAU, วัดว่าราคาที่แตะโซน 'เด้งจริง' กี่ % ใน 6 แท่งถัดไป):
#   proximity 0.3% ให้ XAU แค่ 35% (แทบเดา), proximity 1.0% ให้ทั้งคู่นิ่งที่ ~65-71%
#   lookback 400 แท่ง ให้สมดุลสุด (จำนวนโซนไม่มาก/น้อยไป, ความแม่นยำดีที่สุดของ BTC)
KEY_LEVEL_BARS         = 400
KEY_LEVEL_PROXIMITY    = 0.01   # ±1% รอบศูนย์กลางโซน = ถือว่า "อยู่ที่ Key Level"
KEY_LEVEL_MIN_TOUCHES  = 2      # ต้องโดนแตะอย่างน้อย 2 ครั้งถึงนับเป็นโซนสำคัญ

# Divergence (RSI) — ราคาทำ new extreme แต่ RSI ไม่ทำตาม (เทียบ swing ราคา 2 จุดล่าสุด)
# swing สำหรับ divergence "ปิด volume filter" — จากทดสอบ ถ้ากรอง volume XAU ตรวจไม่เจอเลย (0%)
# เพราะ divergence ดูรูปร่างราคา vs โมเมนตัม ไม่เกี่ยวกับแรง volume ยืนยัน
# DIV_MAX_AGE: swing จุดใหม่ต้องเพิ่งยืนยันภายใน N แท่ง — ตัด divergence ค้างเก่า
# (backtest: ไม่จำกัดเจอ 39-46% ของเวลา = ไร้ความหมาย, จำกัด 10 แท่งเหลือ 14-18% = เลือกสรรจริง)
# DIV_ZONE: จุดแรกของ swing ต้องเคยอยู่ในโซน overbought/oversold มาก่อน (นิยาม divergence คลาสสิก)
# backtest 800 แท่ง (BTC/XAU): เพิ่ม zone req -> ความแม่นขึ้นจาก 77/88% เป็น 82/93%
# (สัญญาณลดจาก 13/16 เหลือ 11/14 ครั้ง — ยังพอมี sample ไม่เสี่ยง overfit เท่า min_diff)
DIV_RSI_PERIOD   = 14
DIV_MAX_AGE_BARS = 10
DIV_ZONE_OVERBOUGHT = 55
DIV_ZONE_OVERSOLD   = 45

# Soft divergence (OR-logic) — เพิ่มเข้ามา 2026-07-23: นอกจาก RSI Lower High/Higher Low
# เป๊ะ (strict) ยังนับเป็น divergence ได้ถ้า RSI "แทบไม่ขยับตามราคา" (stall) คือขึ้น/ลง
# ไม่เกิน DIV_STALL_THRESHOLD แต้มในทิศตรงข้ามกับที่ควรจะเป็น — backtest เบื้องต้นบน BTC
# 4H (จำลองเทรดจริง TP/SL แบบ Reversal, sample เล็กมากแค่ 3-6 เคส) พบว่า stall=5 ให้
# win rate รวม 50% (3/6) ดีกว่า strict อย่างเดียว (33%, 1/3) — ยังไม่มี full backtest
# ยืนยันหนักแน่น (sample เล็กเกินจะฟันธง) ปรับได้ถ้ามีข้อมูลเพิ่มแล้วผลต่าง
DIV_STALL_THRESHOLD = 5

# Min spacing / max lookback (2026-07-25) — swing point ที่ผ่านเกณฑ์อาจอยู่ติดกันเกินไป
# (window หา swing overlap กัน) โดยเฉพาะ XAU ที่เพิ่งเปิด volume OR wick ratio: backtest
# สำรวจ (ไม่ใช่ optimize) เจอคู่ห่าง <=2-3 แท่ง ~8-13% ของ XAU, BTC แทบไม่เจอ (0-4.8%)
# DIV_MIN_SPACING_BARS: จุดที่เอามาเทียบ divergence ต้องห่างกันอย่างน้อยเท่านี้ ถ้าไม่พอ
# ให้ถอยหาจุดก่อนหน้าแทน (ไม่ข้ามไปเลย กันเสียโอกาสตรวจ divergence)
# DIV_MAX_LOOKBACK_BARS: เพดานถอยหาย้อนหลัง (~1 เดือนบน 4H = 30 วัน x 6 แท่ง/วัน) กันถอยไกล
# จนจุดเทียบไม่มีความหมาย (คนละ "รอบ" ตลาดไปแล้ว)
# ทั้งสองค่านี้เป็นค่าเริ่มต้นจากการคุยกัน ยังไม่ได้ backtest หา win rate จริง — TODO: optimize
# ทีหลังเมื่อมีข้อมูลเทรดจริงมากพอ
DIV_MIN_SPACING_BARS  = 5
DIV_MAX_LOOKBACK_BARS = 180

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)


# ---------------------------------------------------------------------------
# ADX (period = ADX_PERIOD = 20) — สูตร Wilder
# ---------------------------------------------------------------------------

# calc_adx ย้ายไป indicators.py แล้ว (2026-08-01) — เดิมซ้ำกับ swing.calc_di ทุกประการ
# เพราะติด circular import (ดู indicators.py docstring)


def get_adx_bars(symbol: str, bars: int = BARS) -> pd.DataFrame:
    """ดึงแท่ง 4H สำหรับคำนวณ ADX โดยเลื่อนขอบแท่งตาม bars.BAR_OFFSET_H ให้ตรงกับ TradingView
    (ดูคำอธิบายเต็มที่ bars.py) — คืน DataFrame คอลัมน์เดียวกับ get_ohlcv ใช้กับ calc_adx ได้ตรงๆ

    2026-08-18: get_ohlcv() เองก็ apply offset นี้ให้ทุก timeframe="4H" อยู่แล้ว (ดู scoring.py)
    ฟังก์ชันนี้เลยเหลือแค่ wrapper บาง ๆ ไว้เผื่อโค้ดเก่าที่เรียกชื่อนี้อยู่ ไม่ต้องมี logic
    resample ซ้ำสองที่แล้ว"""
    return get_ohlcv(symbol, REGIME_TIMEFRAME, bars=bars)

# ---------------------------------------------------------------------------
# ข้อ 2 — ทิศของเส้น ADX (ขึ้น/ทรง/ลง เทียบ ADX_DIR_BARS แท่งล่าสุด)
# ---------------------------------------------------------------------------

def adx_direction(adx: pd.Series, closed_idx: int) -> str:
    diff = adx.iloc[closed_idx] - adx.iloc[closed_idx - ADX_DIR_BARS]
    if diff >= ADX_DIR_EPS:
        return "ขึ้น"
    if diff <= -ADX_DIR_EPS:
        return "ลง"
    return "ทรง"


# ---------------------------------------------------------------------------
# ข้อ 3 — ADX peak: มองย้อน ADX_PEAK_LOOKBACK แท่ง (ไม่ใช้ swing)
#          "เริ่มลงจริง" = peak แล้ว ADX ลดลงติดต่อกัน >= ADX_DECLINE_BARS แท่ง
# ---------------------------------------------------------------------------

def adx_peak_info(adx: pd.Series, closed_idx: int) -> dict:
    window = adx.iloc[closed_idx - ADX_PEAK_LOOKBACK + 1: closed_idx + 1]
    peak_val = window.max()
    peak_pos = window.idxmax()               # index จริงใน series
    bars_since_peak = closed_idx - peak_pos

    # เช็คว่าลดลง "ติดต่อกัน" ไหม — 2026-08-11: เดิมเช็คทั้งช่วงตั้งแต่ peak มาจนถึงตอนนี้
    # (seg = adx.iloc[peak_pos:closed_idx+1]) ทำให้ยิ่ง bars_since_peak มาก ยิ่งต้องลงรวดเดียว
    # ไม่สะดุดเลยนานขึ้นเรื่อยๆ ทั้งที่ backtest ที่อ้างอิง (ดู comment ที่ ADX_DECLINE_BARS
    # ด้านบน: "ลงติดกัน 3 แท่ง -> ADX ลงต่อจริง 100%") วัดจากแค่ 3 แท่งล่าสุดเท่านั้น ไม่ใช่
    # ทั้งช่วงจาก peak — แก้ให้เช็คแค่ ADX_DECLINE_BARS แท่งล่าสุดตรงตามที่ backtest พิสูจน์ไว้จริง
    declining = False
    if bars_since_peak >= ADX_DECLINE_BARS:
        recent = adx.iloc[closed_idx - ADX_DECLINE_BARS + 1: closed_idx + 1]
        declining = all(recent.iloc[i + 1] < recent.iloc[i] for i in range(len(recent) - 1))

    return {"peak": peak_val, "bars_since_peak": bars_since_peak, "declining": declining}


# ---------------------------------------------------------------------------
# Swing High/Low ของ ADX เอง — สำหรับดูอ้างอิงประกอบ (ไม่ใช่ตัวตัดสิน Regime หลัก)
#   left/right=14: จุด i ต้องเป็นค่าสูงสุด/ต่ำสุดในหน้าต่าง 29 แท่ง (14+ตัวเอง+14)
#   prominence=3 : ต้องต่างจากอีกฝั่งของหน้าต่างอย่างน้อย 3 แต้ม ถึงนับเป็น swing จริง
#   ไม่มี volume filter (ADX ไม่มี "volume" ให้เช็คแบบราคา)
# ---------------------------------------------------------------------------

def find_adx_swings(adx: pd.Series, left: int = ADX_SWING_LEFT_RIGHT,
                    right: int = ADX_SWING_LEFT_RIGHT,
                    min_prominence: float = ADX_SWING_PROMINENCE) -> dict:
    """คืน {highs: [(idx, value)], lows: [(idx, value)]} ของทั้งอนุกรม"""
    highs, lows = [], []
    for i in range(left, len(adx) - right):
        window = adx.iloc[i - left: i + right + 1]
        if adx.iloc[i] == window.max() and adx.iloc[i] - window.min() >= min_prominence:
            highs.append((i, adx.iloc[i]))
        if adx.iloc[i] == window.min() and window.max() - adx.iloc[i] >= min_prominence:
            lows.append((i, adx.iloc[i]))
    return {"highs": highs, "lows": lows}


# ---------------------------------------------------------------------------
# ข้อ 4 — โครงสร้าง trend ยัง intact? (Swing High/Low บน 4H — TF เดียวกับที่ใช้หา SL)
#   Long intact  = HH/HL ต่อเนื่อง (swing high ใหม่สูงกว่าเดิม และ swing low ใหม่สูงกว่าเดิม)
#   Short intact = LL/LH ต่อเนื่อง
# ---------------------------------------------------------------------------

def check_structure(df: pd.DataFrame, vol_multiplier: float, wick_ratio_min: float = None) -> dict:
    """เทียบ 3 จุดสุดท้ายของแต่ละฝั่ง แต่ดูแค่ 'จุดแรก vs จุดสุดท้าย' (ข้ามจุดกลาง)
    จุดกลางถือเป็นการย่อ/เด้งชั่วคราวที่ยอมรับได้ในเทรนด์จริง — ไม่เอามาตัดสิน
    ทดสอบแล้วว่าสมดุลกว่าเทียบแค่ 2 จุด (ไวเกิน) หรือบังคับ 3 จุดเรียงต่อเนื่องเป๊ะ (เข้มเกิน)

    wick_ratio_min: OR-logic เฉพาะ XAU (ดู swing.swing_wick_ratio_min) — symbol อื่นส่ง None เสมอ"""
    highs = find_swing_highs(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                             tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                             wick_ratio_min=wick_ratio_min)
    lows  = find_swing_lows(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                            tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                            wick_ratio_min=wick_ratio_min)
    highs, lows = collapse_swing_runs(highs, lows, df)

    hh = hl = ll = lh = None
    if len(highs) >= 3:
        h0, h2 = df["high"].iloc[highs[-3]], df["high"].iloc[highs[-1]]
        hh = h2 > h0
        lh = h2 < h0
    if len(lows) >= 3:
        l0, l2 = df["low"].iloc[lows[-3]], df["low"].iloc[lows[-1]]
        hl = l2 > l0
        ll = l2 < l0

    long_intact  = bool(hh) and bool(hl)
    short_intact = bool(ll) and bool(lh)

    if long_intact:
        trend, intact = "Long (HH/HL)", True
    elif short_intact:
        trend, intact = "Short (LL/LH)", True
    else:
        trend, intact = "ไม่ชัด / กำลังพัง", False

    return {"trend": trend, "intact": intact,
            "hh": hh, "hl": hl, "ll": ll, "lh": lh,
            "n_highs": len(highs), "n_lows": len(lows)}


# ---------------------------------------------------------------------------
# ข้อ 5 — ราคาอยู่ที่ Key Level สำคัญ? (แนวรับ/ต้านใหญ่ — Fibo/Round number ยังไม่ทำ)
#   จัดกลุ่ม Swing High+Low ที่ราคาใกล้กัน (ภายใน KEY_LEVEL_PROXIMITY) เป็น "โซน" เดียวกัน
#   โซนที่โดนแตะ >= KEY_LEVEL_MIN_TOUCHES ครั้ง ถือว่าเป็นแนวรับ/ต้านใหญ่จริง
# ---------------------------------------------------------------------------

def cluster_price_zones(points: list[tuple[float, pd.Timestamp, str]], pct_tol: float) -> list[dict]:
    """จัดกลุ่มราคาที่ใกล้กันภายใน pct_tol (เช่น 0.01 = 1%) ให้เป็นโซนเดียวกัน
    points = [(price, time, kind)] — kind = 'High'/'Low' เก็บไว้บอกที่มาแต่ละจุด
    คืน [{"center": ..., "members": [(price,time,kind), ...]}] เรียงตามราคา

    เทียบจุดใหม่กับ "anchor" (ราคาต่ำสุดของโซน — จุดแรกที่เข้าโซนนั้น เพราะ points ถูก sort
    จากน้อยไปมาก) ไม่ใช่กับ center ที่ขยับได้ทุกครั้งที่มีสมาชิกใหม่ — กัน "chaining" ที่จุดต่อจุด
    ห่างกันไม่เกิน pct_tol แต่ปลายสุดสองข้างของโซนห่างกันเกิน pct_tol ไปไกล"""
    zones: list[dict] = []
    for pt in sorted(points, key=lambda x: x[0]):
        p = pt[0]
        for z in zones:
            if abs(p - z["anchor"]) / z["anchor"] <= pct_tol:
                z["members"].append(pt)
                z["center"] = sum(m[0] for m in z["members"]) / len(z["members"])
                break
        else:
            zones.append({"center": p, "anchor": p, "members": [pt]})
    return zones


def check_key_level(symbol: str, current_price: float, df: pd.DataFrame = None,
                    as_of: datetime = None) -> dict:
    """df: ส่งข้อมูล 4H (real volume, >= KEY_LEVEL_BARS แท่ง) ที่ดึงมาแล้วมาใช้ซ้ำได้
    (เช่นจาก get_regime() ที่ดึงไปแล้วรอบหนึ่ง) — ถ้าไม่ส่งมาจะดึงเองเหมือนเดิม

    as_of: ส่งมาเมื่อเรียกจาก backtest (เช่น reversal.compute_reversal_score) — ถ้าไม่ส่ง df
    มาเอง จะ fetch ด้วย as_of นี้แทนดึงสด และ**ไม่ตัดแท่งท้ายซ้ำ** เพราะ get_ohlcv_real(as_of=...)
    ตัดแท่งฟอร์มมิ่งให้เสร็จแล้วในตัวมันเอง (2026-08-09: เดิมตัดซ้ำแบบไม่มีเงื่อนไขเหมือนบั๊กที่
    เจอใน reversal.py — reversal.py ส่ง df ที่ bound ด้วย as_of มาแล้วแต่ตัวนี้ตัดทิ้งซ้ำอีกแท่ง)"""
    if df is None:
        df = get_ohlcv_real(symbol, "4H", bars=KEY_LEVEL_BARS, as_of=as_of)
    df = df.iloc[-KEY_LEVEL_BARS:].reset_index(drop=True)
    if as_of is None:
        df = df.iloc[:len(df) - 1].reset_index(drop=True)   # ตัดแท่งยังไม่ปิด (เฉพาะโหมดสด)
    vol_mult = swing_vol_multiplier(symbol)
    wick_min = swing_wick_ratio_min(symbol)

    highs = find_swing_highs(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                             tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_mult,
                             wick_ratio_min=wick_min)
    lows  = find_swing_lows(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                            tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_mult,
                            wick_ratio_min=wick_min)
    points = ([(df["high"].iloc[i], df["time"].iloc[i], "High") for i in highs] +
              [(df["low"].iloc[i],  df["time"].iloc[i],  "Low")  for i in lows])

    zones = cluster_price_zones(points, KEY_LEVEL_PROXIMITY)
    major = [z for z in zones if len(z["members"]) >= KEY_LEVEL_MIN_TOUCHES]

    if not major:
        return {"at_key_level": False, "nearest": None, "distance_pct": None,
                "touches": 0, "zones": major}

    # เลือกโซน: ในบรรดาโซนที่ "อยู่ในระยะ Key Level จริง" (<=proximity) เลือกตัวที่แตะเยอะสุด (แข็งแรงสุด)
    # ถ้าไม่มีโซนไหนเข้าเกณฑ์เลย ค่อย fallback ไปเอาโซนที่ใกล้สุดมาโชว์อ้างอิง (แต่ at_key_level = False แน่นอน)
    in_range = [z for z in major
                if abs(current_price - z["center"]) / z["center"] <= KEY_LEVEL_PROXIMITY]

    if in_range:
        chosen = max(in_range, key=lambda z: len(z["members"]))
    else:
        chosen = min(major, key=lambda z: abs(current_price - z["center"]))

    distance_pct = abs(current_price - chosen["center"]) / chosen["center"]
    return {
        "at_key_level": distance_pct <= KEY_LEVEL_PROXIMITY,
        "nearest":      chosen["center"],
        "distance_pct": distance_pct,
        "touches":      len(chosen["members"]),
        "members":      chosen["members"],
        "zones":        major,
    }


# calc_rsi ย้ายไป indicators.py แล้ว (2026-08-19) — ดู comment ที่ exit_monitor.calc_rsi
def calc_rsi(series: pd.Series, period: int = DIV_RSI_PERIOD) -> pd.Series:
    return _calc_rsi(series, period)


def _find_spacing_partner(points: list[int]) -> int | None:
    """หาจุดก่อนหน้า (h1) ที่ห่างจากจุดล่าสุด (points[-1]) อย่างน้อย DIV_MIN_SPACING_BARS แท่ง
    ถอยย้อนจาก points[-2] ไปเรื่อยๆ จนเจอ หรือเกิน DIV_MAX_LOOKBACK_BARS ก็เลิกหา (คืน None)"""
    if len(points) < 2:
        return None
    last = points[-1]
    for cand in reversed(points[:-1]):
        gap = last - cand
        if gap > DIV_MAX_LOOKBACK_BARS:
            break
        if gap >= DIV_MIN_SPACING_BARS:
            return cand
    return None


def check_divergence(df: pd.DataFrame, symbol: str = None) -> dict:
    """ข้อ 6 — Divergence (RSI(14), 4H): ราคาทำ new extreme แต่ RSI ไม่ทำตาม
    Bearish: ราคา Higher High แต่ RSI Lower High  (ระวังฝั่งขึ้นอ่อนแรง)
    Bullish: ราคา Lower Low  แต่ RSI Higher Low   (ระวังฝั่งลงอ่อนแรง)
    เทียบ swing ราคา 2 จุดล่าสุด + จุดใหม่ต้องยืนยันภายใน DIV_MAX_AGE_BARS แท่ง
    + จุดแรกต้องเคยอยู่โซน overbought/oversold จริง (นิยาม divergence คลาสสิก, ยืนยันด้วย backtest)

    Volume filter: BTC ใช้ volume อย่างเดียว (มี real volume จาก Bitstamp เชื่อถือได้ — 2026-07-23)
    XAU ใช้ volume OR wick ratio (2026-07-25) — เดิมเคยปิด volume filter ไปเลยเพราะทดสอบแล้ว
    กรอง volume แบบ AND เดี่ยวๆ ตรวจ divergence ไม่เจอเลย 0% แต่นั่นคือก่อนมี wick_ratio_min
    OR-logic (ดู check_structure) พอเปลี่ยนมาเป็น volume OR wick ratio เหมือน check_structure/
    check_key_level แล้วปัญหาเดิมไม่เกิดซ้ำ (wick ช่วยกู้จุดที่ volume คนเดียวมองไม่เห็น)
    ไม่ส่ง symbol มา (None) จะ fallback ปิด volume filter เหมือนเดิมทุกกรณี

    Soft divergence (OR-logic): เปิดทั้ง BTC และ XAU (2026-07-25) — เดิมเปิดเฉพาะ BTC เพราะ
    backtest แรกสุดทดสอบแค่ BTC (sample เล็กมาก 3-6 เคส) ตอนนี้ backtest แยกของ XAU เองแล้ว
    (scratch_stall_backtest_xau.py, ~500 วัน 4H, threshold=5 win rate 80% จาก 20 เคส — สูงสุด
    ในบรรดา threshold ที่ลอง 3/5/7/10 และตรงกับค่า DIV_STALL_THRESHOLD ของ BTC พอดี) — sample
    ยังเล็ก ไม่ได้ผ่าน reversal scorecard filter เต็มรูปแบบ ควร optimize/ยืนยันซ้ำทีหลัง
    นอกจาก RSI Lower High/Higher Low เป๊ะ (strict) ยังนับเป็น divergence ได้ถ้า RSI แทบไม่ขยับ
    ตามราคา (ไม่เกิน DIV_STALL_THRESHOLD แต้มในทิศตรงข้าม)"""
    is_btc = bool(symbol) and "XAU" not in symbol.upper() and "GOLD" not in symbol.upper()
    # XAU ใช้ volume OR wick ratio เหมือน check_structure/check_key_level (2026-07-25) —
    # BTC ยังคงได้ vol_multiplier=1.9x, wick_ratio_min=None เหมือนเดิมทุกกรณี (ไม่แตะ path เดิม)
    vol_multiplier = swing_vol_multiplier(symbol) if symbol else 0.0
    wick_ratio_min = swing_wick_ratio_min(symbol) if (symbol and not is_btc) else None
    rsi = calc_rsi(df["close"])
    highs = find_swing_highs(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                             tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                             wick_ratio_min=wick_ratio_min)
    lows  = find_swing_lows(df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT,
                            tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                            wick_ratio_min=wick_ratio_min)
    last_idx = len(df) - 1

    result = {"divergence": None, "detail": "", "points": [], "swing_idx": None}

    h1 = _find_spacing_partner(highs)
    if h1 is not None:
        h2 = highs[-1]
        fresh   = (last_idx - h2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi.iloc[h1] >= DIV_ZONE_OVERBOUGHT
        price_hh = df["high"].iloc[h2] > df["high"].iloc[h1]
        rsi_diff = rsi.iloc[h2] - rsi.iloc[h1]
        strict_lh = rsi_diff < 0
        soft_stall = bool(symbol) and 0 <= rsi_diff <= DIV_STALL_THRESHOLD
        if fresh and zone_ok and price_hh and (strict_lh or soft_stall):
            result["divergence"] = "bearish"
            kind = "LH" if strict_lh else "แทบไม่ขึ้น (stall)"
            result["detail"] = (f"ราคา HH ({df['high'].iloc[h1]:,.2f} -> {df['high'].iloc[h2]:,.2f}) "
                                f"แต่ RSI {kind} ({rsi.iloc[h1]:.1f} -> {rsi.iloc[h2]:.1f})")
            result["points"] = [(df["time"].iloc[h1], df["high"].iloc[h1], rsi.iloc[h1]),
                                (df["time"].iloc[h2], df["high"].iloc[h2], rsi.iloc[h2])]
            result["swing_idx"] = h2
            return result

    l1 = _find_spacing_partner(lows)
    if l1 is not None:
        l2 = lows[-1]
        fresh   = (last_idx - l2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi.iloc[l1] <= DIV_ZONE_OVERSOLD
        price_ll = df["low"].iloc[l2] < df["low"].iloc[l1]
        rsi_diff = rsi.iloc[l1] - rsi.iloc[l2]
        strict_hl = rsi_diff < 0
        soft_stall = bool(symbol) and 0 <= rsi_diff <= DIV_STALL_THRESHOLD
        if fresh and zone_ok and price_ll and (strict_hl or soft_stall):
            result["divergence"] = "bullish"
            kind = "HL" if strict_hl else "แทบไม่ลง (stall)"
            result["detail"] = (f"ราคา LL ({df['low'].iloc[l1]:,.2f} -> {df['low'].iloc[l2]:,.2f}) "
                                f"แต่ RSI {kind} ({rsi.iloc[l1]:.1f} -> {rsi.iloc[l2]:.1f})")
            result["points"] = [(df["time"].iloc[l1], df["low"].iloc[l1], rsi.iloc[l1]),
                                (df["time"].iloc[l2], df["low"].iloc[l2], rsi.iloc[l2])]
            result["swing_idx"] = l2
            return result

    result["detail"] = "ไม่พบ divergence (สดภายใน {} แท่ง)".format(DIV_MAX_AGE_BARS)
    return result


# ---------------------------------------------------------------------------
# สรุป Regime
# ---------------------------------------------------------------------------

def classify_regime(adx_now: float, direction: str, peak: dict, structure: dict,
                    key_level: dict = None, divergence: dict = None) -> tuple[str, str, str]:
    """คืน (regime, action, color)"""
    # CHOPPY — ห้ามเทรด
    if adx_now < ADX_CHOPPY:
        return ("CHOPPY", f"พัก — การไม่เทรดคือ position ที่ถูกต้อง (ADX < {ADX_CHOPPY:.0f})", RED)

    # เขตเทา — 2026-08-11: ข้อความเดิม hardcode "20-25" ค้างมาจากก่อนแก้ ADX_GRAY_HIGH จาก 25
    # เป็น 22 (ดู comment ที่นิยามค่าคงที่ด้านบน) ทำให้ log แสดงช่วงผิดจากเกณฑ์จริงมาตลอด — ใช้
    # ค่าคงที่จริงแทน hardcode กันเพี้ยนซ้ำถ้ามีคนปรับ threshold อีกในอนาคตแล้วลืมแก้ข้อความ
    if adx_now < ADX_GRAY_HIGH:
        return ("เขตเทา",
                f"รอ ADX เลือกทางก่อน ห้ามฝืนเปิด scorecard (ADX {ADX_CHOPPY:.0f}-{ADX_GRAY_HIGH:.0f})",
                YELLOW)

    # Reversal candidate — relative peak & decline: ทำจุดสูงสุดใหม่ในรอบที่มองย้อน (ไม่ว่าตัวเลขจะเป็นเท่าไหร่
    # เช่น 28, 32, 45) แล้วโค้งลงติดกันครบแท่ง — ไม่ใช้ threshold ตายตัวอย่าง 40 อีกต่อไป เพราะ "แรงสุดของรอบนั้น"
    # ไม่จำเป็นต้องแตะระดับคงที่เสมอไป จุดเปลี่ยนโมเมนตัม (peak แล้วอ่อนแรง) สำคัญกว่าตัวเลขสัมบูรณ์
    if peak["declining"]:
        at_key  = bool(key_level and key_level.get("at_key_level"))
        has_div = bool(divergence and divergence.get("divergence"))

        # ครบ 3 เงื่อนไข (peak โค้งลง + อยู่ที่ Key Level + มี Divergence) -> พร้อมเปิด Reversal จริง
        if at_key and has_div:
            return ("REVERSAL-READY",
                    f"ครบ 3 เงื่อนไข: ADX peak {peak['peak']:.1f} โค้งลง {peak['bars_since_peak']} แท่ง "
                    f"+ อยู่ที่ Key Level {key_level['nearest']:,.2f} "
                    f"+ Divergence ({divergence['divergence']}) -> เปิด Reversal scorecard ได้", CYAN)

        # peak โค้งลงแล้ว แต่เงื่อนไขยืนยันยังไม่ครบ -> เฝ้าดู
        missing = []
        if not at_key:  missing.append("Key Level")
        if not has_div: missing.append("Divergence")
        return ("REVERSAL-WATCH",
                f"ADX peak {peak['peak']:.1f} แล้วโค้งลง {peak['bars_since_peak']} แท่ง "
                f"-> เฝ้าดู Reversal แต่ยังขาด: {' + '.join(missing)}", CYAN)

    # ADX 40+ ยังพุ่งไม่หยุด (ยังไม่โค้งลง) = ห้ามสวนเด็ดขาด ยิ่งแรงยิ่งต้องระวังไม่สวน
    if adx_now >= ADX_REVERSAL and direction == "ขึ้น":
        return ("TREND แรงจัด", "ADX 40+ ที่ยังพุ่ง (ยังไม่ peak) = ห้ามสวน — ใช้ Scoring ตามเทรนด์เท่านั้น"
                + ("" if structure["intact"] else " (แต่ structure ไม่ intact — ระวัง)"), GREEN)

    # TREND ปกติ — ADX >= 25 + ทิศไม่ลง + structure intact
    if direction in ("ขึ้น", "ทรง") and structure["intact"]:
        return ("TREND", f"เปิด Scoring scorecard — เทรดตามทิศ {structure['trend']}", GREEN)

    # เงื่อนไขไม่ครบ
    return ("เขตเทา", "เงื่อนไขไม่ครบ (ADX ลง หรือ structure ไม่ intact) — รอความชัดเจน", YELLOW)


def get_regime(symbol: str) -> dict:
    """เก็บ logic การเก็บข้อมูล+ตัดสินใจ Regime ทั้งหมดไว้ที่เดียว — ใช้ทั้งจาก CLI (run_check)
    และจากที่อื่น (เช่น scheduler.py) โดยไม่ต้อง print รายงานเต็ม"""
    # 4H — ADX ไม่ต้องพึ่ง volume | ขอบแท่งเลื่อนตาม ADX_BAR_OFFSET_H ให้ตรง TradingView
    df = get_adx_bars(symbol, bars=BARS)
    adx = calc_adx(df, ADX_PERIOD)
    closed_idx = len(df) - 2                 # แท่ง 4H ปิดล่าสุด

    adx_now   = adx.iloc[closed_idx]
    direction = adx_direction(adx, closed_idx)
    peak      = adx_peak_info(adx, closed_idx)

    # Swing High/Low ของ ADX เอง (ข้อมูลอ้างอิงเสริม — ไม่ใช่ตัวตัดสิน Regime)
    adx_upto_now = adx.iloc[:closed_idx + 1].reset_index(drop=True)
    adx_swings   = find_adx_swings(adx_upto_now)

    # โครงสร้าง trend + Key Level ดูจาก 4H เดียวกัน — ดึงครั้งเดียวที่ขนาดใหญ่สุดที่ต้องใช้ (KEY_LEVEL_BARS)
    # แล้ว reuse ทั้ง Structure (ตัดมาแค่ BARS แท่งท้าย) และ Key Level (ใช้เต็ม) — กันดึง/merge real
    # volume ซ้ำสองรอบต่อ symbol ต่อรอบสแกน (เดิมดึงแยก 210 แท่งสำหรับ structure + 400 แท่งสำหรับ key level)
    df_4h_full = get_ohlcv_real(symbol, "4H", bars=KEY_LEVEL_BARS)
    df_4h = df_4h_full.iloc[-BARS:].reset_index(drop=True)
    structure = check_structure(df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True),
                                vol_multiplier=swing_vol_multiplier(symbol),
                                wick_ratio_min=swing_wick_ratio_min(symbol))
    current_price = df_4h["close"].iloc[len(df_4h) - 2]   # ราคาปิดแท่ง 4H ล่าสุด
    key_level  = check_key_level(symbol, current_price, df=df_4h_full)
    # ใช้ df_4h (real volume) ไม่ใช่ df เดิม (tick_volume) — จำเป็นตั้งแต่เปิด volume filter
    # สำหรับ BTC divergence (2026-07-23) ไม่งั้น tick_volume ไม่ผ่านเกณฑ์ 1.9x เกือบตลอด
    # ทำให้หา swing high/low ไม่เจอเลย divergence เลยไม่มีทางเกิดขึ้นได้จริง
    divergence = check_divergence(df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True), symbol=symbol)   # ตัดแท่งยังไม่ปิด

    regime, action, color = classify_regime(adx_now, direction, peak, structure,
                                            key_level=key_level, divergence=divergence)

    return {
        "symbol": symbol, "regime": regime, "action": action, "color": color,
        "adx_now": adx_now, "adx_direction": direction, "peak": peak, "adx_swings": adx_swings,
        "structure": structure, "key_level": key_level, "divergence": divergence,
        "closed_idx": closed_idx,
        "df_4h": df_4h,   # real volume, BARS แท่ง, ยังไม่ตัดแท่งฟอร์มมิ่ง — reuse ต่อได้เลย
                         # (เช่น reversal.compute_reversal_score) กันดึง+merge real volume ซ้ำ
    }


def run_check(symbol: str) -> None:
    r = get_regime(symbol)
    adx_now, direction, peak, adx_swings = r["adx_now"], r["adx_direction"], r["peak"], r["adx_swings"]
    structure, key_level, divergence     = r["structure"], r["key_level"], r["divergence"]
    regime, action, color, closed_idx    = r["regime"], r["action"], r["color"], r["closed_idx"]

    print()
    print("=" * 66)
    print(f"{BOLD}  REGIME CHECK — {symbol}  (TF 4H){RESET}")
    print("=" * 66)
    print(f"  1. ADX({ADX_PERIOD}) ปัจจุบัน     : {BOLD}{adx_now:.2f}{RESET}")
    print(f"  2. ทิศของเส้น ADX       : {direction}   {DIM}(เทียบ {ADX_DIR_BARS} แท่ง, ±{ADX_DIR_EPS} = ทรง){RESET}")
    decline_str = (f"โค้งลงติดกัน {peak['bars_since_peak']} แท่ง ✅" if peak["declining"]
                   else f"ยังไม่โค้งลงจริง (ผ่านมา {peak['bars_since_peak']} แท่ง)")
    print(f"  3. ADX peak (ย้อน {ADX_PEAK_LOOKBACK} แท่ง): {peak['peak']:.2f} — {decline_str}")
    if adx_swings["highs"]:
        si, sv = adx_swings["highs"][-1]
        days_ago = (closed_idx - si) * 4 / 24
        print(f"     {DIM}[อ้างอิง] ADX Swing HIGH ล่าสุด: {sv:.2f} เมื่อ {days_ago:.1f} วันก่อน "
              f"(left/right={ADX_SWING_LEFT_RIGHT}, prominence={ADX_SWING_PROMINENCE}){RESET}")
    if adx_swings["lows"]:
        si, sv = adx_swings["lows"][-1]
        days_ago = (closed_idx - si) * 4 / 24
        print(f"     {DIM}[อ้างอิง] ADX Swing LOW  ล่าสุด: {sv:.2f} เมื่อ {days_ago:.1f} วันก่อน{RESET}")
    intact_str = f"{GREEN}Yes{RESET}" if structure["intact"] else f"{RED}No{RESET}"
    print(f"  4. โครงสร้าง trend intact? (4H): {intact_str}   ทิศ: {structure['trend']} "
          f"{DIM}(HH={structure['hh']} HL={structure['hl']} LL={structure['ll']} LH={structure['lh']}){RESET}")
    if key_level["nearest"] is None:
        print(f"  5. Key Level สำคัญ?      : {DIM}ไม่พบโซนที่โดนแตะ >= {KEY_LEVEL_MIN_TOUCHES} ครั้ง ({KEY_LEVEL_BARS} แท่งย้อนหลัง){RESET}")
    else:
        at_str  = f"{GREEN}Yes{RESET}" if key_level["at_key_level"] else f"{RED}No{RESET}"
        dist_pc = key_level["distance_pct"] * 100
        print(f"  5. Key Level สำคัญ?      : {at_str}   โซนที่เลือก {key_level['nearest']:,.2f} "
              f"(แตะ {key_level['touches']} ครั้ง, ห่าง {dist_pc:.2f}%)   "
              f"{DIM}[ทั้งหมด {len(key_level['zones'])} โซนใน {KEY_LEVEL_BARS} แท่ง]{RESET}")
        for price, t, kind in sorted(key_level["members"], key=lambda m: m[1]):
            print(f"       {DIM}- {t}  {kind}={price:,.2f}{RESET}")
    if divergence["divergence"]:
        div_color = RED if divergence["divergence"] == "bearish" else GREEN
        print(f"  6. Divergence?           : {div_color}Yes ({divergence['divergence']}){RESET}   {divergence['detail']}")
        for t, price, rsi_v in divergence["points"]:
            print(f"       {DIM}- {t}  price={price:,.2f}  RSI={rsi_v:.1f}{RESET}")
    else:
        print(f"  6. Divergence?           : {DIM}No — {divergence['detail']}{RESET}")
    print("-" * 66)
    print(f"  {BOLD}Regime ตอนนี้{RESET} : {color}{BOLD}{regime}{RESET}")
    print(f"  {BOLD}ACTION{RESET}        : {color}{action}{RESET}")
    print("=" * 66)


def main():
    load_dotenv()
    symbols = [a for a in sys.argv[1:] if not a.startswith("-")] or SYMBOLS
    connect()
    try:
        for symbol in symbols:
            try:
                run_check(symbol)
            except Exception as exc:
                print(f"{RED}  [{symbol}] ERROR — {exc}{RESET}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
