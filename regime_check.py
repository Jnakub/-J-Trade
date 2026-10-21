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
  6. มี Divergence (RSI)?            — ราคาทำ new extreme แต่ RSI ไม่ทำตาม
                                       (period = DIV_RSI_PERIOD ด้านล่าง ปัจจุบัน 20)
                                        (สดภายใน DIV_MAX_AGE_BARS แท่ง + จุดแรกต้องเคย
                                         overbought/oversold จริง)

กฎ Mutual Exclusivity:
  - ห้ามเปิด 2 scorecard กับ setup เดียวกัน (ห้าม scorecard shopping)
  - Scoring เข้า "ตาม" ทิศเทรนด์ / Reversal เข้า "สวน" ทิศเทรนด์เดิมเท่านั้น
  - Reversal ใช้เกณฑ์ relative peak & decline — ADX ทำจุดสูงสุดใหม่ในรอบที่มองย้อน (10 แท่ง)
    ไม่ว่าตัวเลขจะเป็นเท่าไหร่ (28, 32, 45) แล้วโค้งลงติดกัน 3 แท่ง ถือว่าเข้าเกณฑ์ Reversal
    (ไม่ใช้ threshold ตายตัวอย่าง 40 อีกต่อไป เพราะ "แรงสุดของรอบนั้น" ไม่จำเป็นต้องแตะเลขคงที่)
    ส่วน ADX>=40 ที่ยังพุ่งไม่หยุด (ยังไม่ peak) = regime "TREND แรงจัด" ซึ่งตั้งแต่ 2026-08-31
    ไม่เข้าไม้ทั้งสองทาง (อยู่ใน scheduler.REGIME_NO_TRADE — ดูเหตุผล+ข้อจำกัดของหลักฐานที่นั่น)
  - CHOPPY (ADX<20) = พัก การไม่เทรดคือ position ที่ถูกต้อง
  - Regime ใช้ 2 จังหวะ: ก่อนเข้า → เลือก scorecard | ระหว่างถือ → ADX>=25 ใช้ Structure Stop trail

ใช้: python regime_check.py                  # เช็คทุก symbol ใน config
     python regime_check.py BTCUSDm          # เช็คเฉพาะ symbol ที่ระบุ
"""
import sys
from datetime import datetime

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import MT5_TIMEFRAMES, SYMBOLS
from mt5_connect import connect
from scoring import get_ohlcv, get_ohlcv_real
from swing import find_swing_highs, find_swing_lows, swing_vol_multiplier, swing_wick_ratio_min, collapse_swing_runs
from indicators import calc_adx, calc_atr, calc_rsi as _calc_rsi
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
                          # คือ Key Level+Divergence ไม่ใช่ ADX) — ~~ยังไม่มี full win-rate
                          # backtest ยืนยัน แค่ยืนยันว่าเปิดโอกาสได้บ่อยขึ้น~~
# 🔻 22 วัดด้วย R แล้ว 2026-09-18: ยกเป็น 23/24/25 แย่ลงทุกระดับ (−9.43/−9.93/−14.68R · บวก 1-3/7) · ยุบเขตเทา (20) −8.95R (2026-09-13)
#   = อย่าขยับทั้งสองทาง · ไม้ Scoring เข้าแท่งแรกที่ ADX ข้ามเส้นเสมอ — ห้ามอ่าน avgR แยกตามระดับ ADX ตอนเข้าเป็นคุณภาพของระดับนั้น
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — ADX_GRAY_HIGH (วัด 2026-09-13..18)"
ADX_STRONG_TREND = 40     # ADX ตั้งแต่ค่านี้ขึ้นไปที่ "ยังไม่ผ่านการโค้งลง" = regime "TREND แรงจัด"
                          # 🔴 ค่านี้ **ไม่เกี่ยวกับเกณฑ์ Reversal** — Reversal ใช้ relative peak
                          # ล้วน (peak["declining"]) ไม่มี threshold ตายตัว ดู classify_regime()
                          # เดิมชื่อ ADX_REVERSAL + comment ว่า "peak ต้อง >= 40 ถึงเข้าเกณฑ์
                          # Reversal" ซึ่งค้างมาจากดีไซน์เก่าก่อนเปลี่ยนเป็น relative peak —
                          # เปลี่ยนชื่อ 2026-08-31 เพราะหลังจาก "TREND แรงจัด" ถูกย้ายไปเป็น
                          # regime ห้ามเข้าไม้ (scheduler.REGIME_NO_TRADE) ค่านี้กลายเป็นตัวคุม
                          # ว่า "เมื่อไหร่ระบบจะไม่เทรด" ล้วนๆ ลดค่านี้ = หยุดเทรดบ่อยขึ้น
                          # (ตรงข้ามกับที่ชื่อเดิมชวนให้เข้าใจว่าเป็นการผ่อนเกณฑ์ Reversal)

# ---------------------------------------------------------------------------
# ⚠️ ADX_BAR_OFFSET_H — ย้ายไปอยู่ที่ bars.BAR_OFFSET_H แล้ว (import เป็น alias ชื่อเดิมด้านบน)
# ---------------------------------------------------------------------------
# ย้ายไป bars.BAR_OFFSET_H แล้ว (คำอธิบาย + ตารางวัดเต็มที่ bars.py) — ขอบแท่ง 4H ของ MT5 กับ TradingView ไม่ตรงกัน ADX จึงต่างกันได้หลายจุด
# 🔴 symbol ใหม่ต้องเทียบ offset กับจอ TradingView ทุกครั้ง (เช็คลิสต์ config ข้อ 4) · offset กระทบ swing/SL/TP/divergence ทั้งหมด ไม่ใช่แค่ ADX
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — ADX_BAR_OFFSET_H (2026-08-12..18)"

# ADX_DIR_BARS — ทิศ ADX เทียบกี่แท่ง · 4 -> 5 ตั้งแต่ 2026-09-18 (ผู้ใช้) · replay +3.47R |t| 0.50 · 4/7 · ตัด 5 ใหญ่ −5.22 = ตกเกณฑ์ 3/3
#   ผู้ใช้ยืนยันเก็บ 5 หลังเห็นผลครบ (ค่า 4 มีผลวัดคุณสมบัติเส้น ADX ปี 07-24 รองรับ แต่ทั้งคู่วัดไม่ออกในมิติ R)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — ADX_DIR_BARS (2026-07-24..09-18)"
ADX_DIR_BARS     = 5      # ทิศ ADX เทียบกี่แท่ง
ADX_DIR_EPS      = 1.0    # เปลี่ยน < ค่านี้ = "ทรง"
ADX_PEAK_LOOKBACK = 10    # ADX peak มองย้อนกี่แท่ง
# ADX_DECLINE_BARS — โค้งลงจาก peak กี่แท่ง ⚠️ โค้ดตัดมา N แท่ง = เทียบ N-1 คู่ (3 = ลงติดกัน 2 ครั้ง) และไม่มีเกณฑ์ขนาด
#   ลอง 4 วันที่ 2026-09-17 แล้วย้อนกลับเป็น 3 วันเดียวกัน (ผู้ใช้ · กลุ่มที่จะถูกตัดทำเงินดีกว่ากลุ่มที่รอด) · --adx-decline-bars=N
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — ADX_DECLINE_BARS (2026-09-17)"
ADX_DECLINE_BARS  = 3     # โค้งลงติดกันกี่แท่งจาก peak = "เริ่มลงจริง"

# ---------------------------------------------------------------------------
# ADX_MIN_PEAK_REVERSAL — peak ต้องสูงถึงเท่าไรถึงจะนับเป็น "เทรนด์ที่มีให้กลับตัว"
# ---------------------------------------------------------------------------
# เพิ่ม 2026-09-17 (ผู้ใช้) · กวาด replay 2026-09-30: 27-30 ที่ราบ · 32 หน้าผา (−20.97R |t| 2.46) · ปิดด่าน +13.52R แต่ระบบเปลี่ยนทั้งใบ (Breakout ตัวขับ)
#   ⚠️ ห่างหน้าผาแค่ ~12% อย่าขยับขึ้น · Reversal A ใช้ config.REV_A_ADX_MIN_PEAK แยก (28.5 เท่ากัน) · --adx-min-peak=N
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — ADX_MIN_PEAK_REVERSAL (2026-09-17..30)"
ADX_MIN_PEAK_REVERSAL = 28.5

# REVERSAL_USES_ADX_FLOOR — Reversal ต้องผ่านพื้น ADX ≥ ADX_GRAY_HIGH (22) ด้วยไหม · False ตั้งแต่ 2026-09-30 (ผู้ใช้ · เปลี่ยนแค่ 1 ไม้ −1.04R)
#   เหลือกฎเดียว: peak ≥ ADX_MIN_PEAK_REVERSAL แล้วโค้งลง · --rev-no-adx-floor / --rev-adx-floor
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — REVERSAL_USES_ADX_FLOOR (2026-09-30)"
REVERSAL_USES_ADX_FLOOR = False

# Swing High/Low ของ ADX เอง (คนละตัวกับ find_swing_highs/lows ที่ใช้กับราคา — ไม่มี volume filter)
# ทดสอบแล้ว left/right=7 แคบไป มองไม่เห็นยอดเขาที่อยู่ไกล ทำให้ V-shape ลึกจริงหลุดไป
# left/right=14 จับจุดได้แม่นขึ้น แลกกับยืนยันช้าลง (~2.3 วันบน 4H)
ADX_SWING_LEFT_RIGHT  = 14
ADX_SWING_PROMINENCE  = 3.0

SWING_LEFT_RIGHT = 4
SWING_TOLERANCE  = 0.22   # 2026-07-24: เปลี่ยนจาก 0.25 — ยังไม่มี backtest ยืนยัน
# DIV_SWING_RIGHT_* — (2026-10-10 · None = SWING_LEFT_RIGHT) จำนวนแท่ง 4H ฝั่งขวาที่ใช้ยืนยัน swing
# **เฉพาะใน check_divergence** (Key Level / check_structure ยังใช้ SWING_LEFT_RIGHT) · ที่มา: Reversal เข้าช้า — ราคาเข้าห่าง
# swing low B median 1.64 ATR4H (setup ที่ติด R:R 2.85) และไม้ที่เข้าใกล้ B ทำกำไรเกือบทั้งหมด · ต้องรอ 4 แท่ง = 16 ชม. หลังก้น
# ใช้ backtest_replay --rev-swing-right=N (ตั้งคู่กับ reversal.SWING_RIGHT_LONG/SHORT ให้ SL/TP อ้างจุดเดียวกัน)
# แยกสองฝั่ง (2026-10-10): highs = bearish divergence (Reversal Short + Breakout flip) · lows = bullish (Reversal Long)
# 🔻 สถานะปัจจุบัน: **LOWS = 2 ตั้งแต่ 2026-10-10 (คำสั่งผู้ใช้ · คู่กับ reversal.SWING_RIGHT_LONG = 2)** · HIGHS คง 4
#    replay เต็ม (+ไม่ให้ Reversal จุด TP->entry): base 376 ไม้ +180.91R -> 378 ไม้ +191.46R (ΔR +10.55) · Breakout ไม่ขยับสักไม้
#    Reversal 30 +14.87 -> 32 +25.05 · **ตกเกณฑ์ 2/3**: churn |t| 1.15 · ตัด 5 รายการ −1.65 · ดีขึ้น 6/7 symbol ✓
#    ค่า 2 เลือกหลังเห็น 4/3/2 · ยังไม่ได้เช็คที่ราบ (1/3) และยุค 2023-24 · ตารางเต็มที่ backtest_replay --rev-swing-right
#    ทั้งสองฝั่ง = 2 (--rev-swing-right=2) Breakout −11.52R จากไม้ใหม่ 39 ไม้ −7.85 = เหตุผลที่ไม่แตะ HIGHS
# 🔻 2026-10-16: **HIGHS = 2 ด้วย** (คำสั่งผู้ใช้ · คู่กับ reversal.SWING_RIGHT_SHORT = 2) — ระบบปัจจุบัน (Pullback · Reversal A ≤4
#    · divergence 50/50) ทั้งสองช่วง: 2024-26 439 ไม้ +224.47R -> 468 ไม้ +231.84R (+7.37 · |t| 0.38 · ตัด 5 ใหญ่ −10.35 · ดีขึ้น 6/10)
#    2023-24 180 ไม้ +21.18R -> 190 ไม้ +28.03R (+6.85 · |t| 0.57 · ตัด 5 ใหญ่ −0.11 · ดีขึ้น 8/10) = บวกทั้งสองช่วง แต่ตัวขับคนละกลยุทธ์
#    (2024-26 Reversal A +12.01 · 2023-24 Breakout +9.82) · **Pullback แย่ลงทั้งสองช่วง (−4.98 / −1.93)** · ผลเก่า −11.52 (Breakout) เป็นของระบบรุ่นก่อน
DIV_SWING_RIGHT_HIGHS = 2
DIV_SWING_RIGHT_LOWS  = 2

# Key Level — โซนแนวรับ/แนวต้านใหญ่ (รวม Swing High+Low เป็นโซนเดียวกัน — support เก่ากลายเป็น resistance ใหม่ได้)
# ค่าจาก backtest จริง (BTC/XAU, วัดว่าราคาที่แตะโซน 'เด้งจริง' กี่ % ใน 6 แท่งถัดไป):
#   proximity 0.3% ให้ XAU แค่ 35% (แทบเดา), proximity 1.0% ให้ทั้งคู่นิ่งที่ ~65-71%
#   lookback 400 แท่ง ให้สมดุลสุด (จำนวนโซนไม่มาก/น้อยไป, ความแม่นยำดีที่สุดของ BTC)
KEY_LEVEL_BARS         = 400
KEY_LEVEL_PROXIMITY    = 0.01   # ±1% รอบศูนย์กลางโซน = ถือว่า "อยู่ที่ Key Level"
KEY_LEVEL_MIN_TOUCHES  = 2      # ต้องโดนแตะอย่างน้อย 2 ครั้งถึงนับเป็นโซนสำคัญ
# KEY_LEVEL_REQUIRED — False = ถือว่า "อยู่ที่ Key Level" เสมอ (REVERSAL-READY/Breakout + Pullback) · ทดลองด้วย
#   backtest_replay --no-key-level (2026-10-13 ผู้ใช้: "เอา Key level ออกเลยดีไหม") · ที่มา: ±1% คงที่ผ่าน 79% ของแท่ง 4H
#   (FX 93-97%) และระยะถึงแนว (เป็น ATR) แยกไม้ดี/แย่ไม่ได้ (153 ไม้ · ใกล้ ≤0.5 ATR +0.51 vs ไกล +0.70)
# 🔻 replay เต็ม 2026-10-13 (Pullback R:R 1.5 · _now1013pb_nokl vs _now1013pb): 399 ไม้ +193.15 -> 406 ไม้ +194.74 ΔR +1.58
#    |t| 0.18 · ตัด 5 ใหญ่ −2.14 · ดีขึ้น 3/10 · Breakout 56 -> 64 ไม้ +3.93 · Pullback −3.38 · Sideway +1.03 · Scoring ไม่ขยับ
#    = ไม่มีผลชัดทั้งทางดีและทางเสีย (ตรงกับที่ KL ผ่าน 79% ของเวลา)
# 🔴 สถานะปัจจุบัน: **False ตั้งแต่ 2026-10-13 (คำสั่งผู้ใช้ "ตัด Key Level ออก")** — ยังคำนวณโซนและพิมพ์ใน log เหมือนเดิม
#    แค่ไม่ใช้ตัดสิน · เปิดกลับ = True (replay --key-level)
KEY_LEVEL_REQUIRED = False

# Divergence (RSI) — ราคาทำ new extreme แต่ RSI ไม่ทำตาม (เทียบ swing ราคา 2 จุด · swing ใช้ตัวกรอง vol/wick ราย symbol)
#   DIV_RSI_PERIOD / DIV_MAX_AGE_BARS 14 -> 20 ตั้งแต่ 2026-09-11 (ผู้ใช้ · +5.07R = 3 ไม้ GBP) · ⚠️ DIV_RSI_PERIOD เป็น default RSI ของ reversal.py ด้วย
#   DIV_ZONE: จุดแรกของ swing ต้องเคยอยู่ในโซน · ประวัติ 55/45 -> 53/46 (09-16) -> 50/50 (10-14 ข้างล่าง) · --div-zone-legacy = 55/45
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — Divergence: volume filter / DIV_MAX_AGE / DIV_RSI_PERIOD / โซนเดิม (2026-07-23..09-16)"
DIV_RSI_PERIOD   = 20
DIV_MAX_AGE_BARS = 20
# 🔴 2026-10-14: 53/46 -> **50/50** (คำสั่งผู้ใช้ หลังกวาดโซนครบ 8 จุด · RSI 20 · base 406 ไม้ +194.74R · backtest_replay --div-zone=OB/OS)
#    ไม่บังคับ +10.39 · 45/55 +9.40 · **50/50 +13.62** · 53/46 0 · 55/45 −7.01 · 60/40 −25.44 · 65/35 −38.85 · 70/30 −46.12
#    ฝั่งหลวมเป็นที่ราบ (ไม่บังคับ/45/50) · 53/46 อยู่ขอบหน้าผา (จูนเดิมจาก "swing ผ่าน ~50%" ไม่ใช่จาก R)
#    50/50 ผ่านเกณฑ์ 3/3: |t| 2.08 · ตัด 5 ใหญ่ +0.77 · ดีขึ้น 7/10 · Pullback 60 +22.05 -> 69 +31.73 · Breakout 64 +42.73 -> 66 +44.64
#    ⚠️ เลือกหลังเห็นผล · มีผลทั้ง Pullback และ REVERSAL-READY/Breakout · RSI(14)+70/30 / ตามตำราครบ −40/−47 (ดู --div-textbook)
DIV_ZONE_OVERBOUGHT = 50
DIV_ZONE_OVERSOLD   = 50

# Soft divergence (2026-07-23) — RSI ไม่ต้องสวนเป๊ะ: "แทบไม่ขยับ" (สวนทางผิดได้ไม่เกิน N แต้ม) ก็นับ · ไม่แตะรายการ swing (ไม่เบียดคู่เดิม)
#   5 -> 3 ตั้งแต่ 2026-09-16 (ผู้ใช้) · replay +0.59R |t| 0.26 = noise · ชี้ขาดแค่ ~3 ไม้ใน 2 ปี · ถอยกลับ = 5 · --div-stall=N
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_STALL_THRESHOLD (2026-07-23..09-16)"
DIV_STALL_THRESHOLD = 3

# DIV_PRICE_TOLERANCE_ATR — ผ่อนการเทียบ "ราคาทำ new extreme" ให้คลาดได้ k × ATR4H (ATR ณ จุดใหม่) · 0 = เทียบเป๊ะ
#   0.1 ตั้งแต่ 2026-09-16 (ผู้ใช้) · เลือกจากขนาดเคสต้นเหตุ (double bottom USDJPY ~0.05 เยน) · superset สะอาด (direct 0) · R +0.32 = noise
#   ⚠️ ห้ามเพิ่มเพราะ "ลองแล้ว R ดีขึ้น" — 0.5 ATR รับของที่ไม่ใช่ divergence (ราคากับ RSI ไปทางเดียวกัน) · --div-price-tol=X
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_PRICE_TOLERANCE_ATR (2026-09-13..16)"
DIV_PRICE_TOLERANCE_ATR = 0.1


def _price_tol(df: pd.DataFrame, idx: int) -> float:
    """ระยะที่ยอมให้ราคาคลาดได้ตอนเทียบ new extreme — ดู DIV_PRICE_TOLERANCE_ATR
    คืน 0.0 เมื่อปิด (ค่าปกติ) เพื่อให้ผลเท่ากับ strict เป๊ะ ไม่ต้องคำนวณ ATR เปล่าๆ"""
    if DIV_PRICE_TOLERANCE_ATR <= 0:
        return 0.0
    atr = calc_atr(df).iloc[idx]
    return 0.0 if pd.isna(atr) else float(atr) * DIV_PRICE_TOLERANCE_ATR

# 🔻 สถานะปัจจุบัน: DIV_MIN_SPACING_BARS = 5 (กวาดแล้ว 2026-09-23 · คงไว้ ดูบล็อกที่ค่านั้น) ·
#    DIV_MAX_LOOKBACK_BARS = 20 (กวาดครบแล้ว 2026-09-22 ดูบล็อกที่ค่านั้น)
# Min spacing (2026-07-25) — จุดที่เทียบต้องห่างกัน ≥ N แท่ง ไม่งั้นถอยหาจุดก่อนหน้า (window swing overlap กัน)
#   กวาด replay 2026-09-23: 3 −1.78 · **5** · 8 −5.88 · 10 −8.94 = churn ล้วน ทั้งสองทิศเอาไม้ดีออกแล้วเอาไม้แย่กว่ามาแทน · คง 5
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_MIN_SPACING_BARS (2026-07-25..09-23)"
DIV_MIN_SPACING_BARS  = 5
# 🔴 180 -> 20 ตั้งแต่ 2026-09-22 (ผู้ใช้) · 180/120/60 เป็นโค้ดตาย (ไม่มีคู่ห่างเกิน 60 แท่ง) · 20 ตัด 14 ไม้ (แพ้ 79%) +5.34R
#   dead:good 2.00 = ด่านเข้าไม้ตัวแรกที่ผ่านเกณฑ์ 3e · |t| 1.19 · ปิดประตู h1 ให้กว้างเท่า h2 (DIV_MAX_AGE_BARS)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_MAX_LOOKBACK_BARS (2026-09-16..22)"
DIV_MAX_LOOKBACK_BARS = 20

# DIV_SWING_VOL_FILTER — True = swing ของ divergence ผ่านตัวกรอง vol/wick ราย symbol (ปิดแล้ว BTC Reversal แพ้ 7/7) · --div-no-volume
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_SWING_VOL_FILTER (2026-09-01)"
DIV_SWING_VOL_FILTER  = True

# DIV_WICK_ALL_SYMBOLS — wick OR-logic ใน check_divergence ทุก symbol (เดิมทองตัวเดียว) · True ตั้งแต่ 2026-09-15 (ผู้ใช้) · False = ทองเท่านั้น
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — DIV_WICK_ALL_SYMBOLS (2026-09-15)"
DIV_WICK_ALL_SYMBOLS = True

# 🔻 2026-09-13 ให้ wick เฉพาะ symbol ที่ใช้ tick_volume −10.46R |t| 2.93 · ไม้ที่หาย = คู่ divergence ห่าง ~116 แท่ง (ช่วงที่เพดานยัง 180)
#   2026-09-22 กวาดเพดานแล้วไม่มีคู่ห่างเกิน 60 แท่ง = สองปุ่มนี้ไม่อิสระต่อกัน · บทเรียนอยู่ CLAUDE.md กติกาข้อ 3b
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — wick tick_volume −10.46R (2026-09-13..22)"

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)


# ---------------------------------------------------------------------------
# ADX (period = ADX_PERIOD = 20) — สูตร Wilder
# ---------------------------------------------------------------------------

# calc_adx ย้ายไป indicators.py แล้ว (2026-08-01) — เดิมซ้ำกับ swing.calc_di ทุกประการ
# เพราะติด circular import (ดู indicators.py docstring)


def get_adx_bars(symbol: str, bars: int = BARS, as_of=None) -> pd.DataFrame:
    """ดึงแท่ง 4H สำหรับคำนวณ ADX โดยเลื่อนขอบแท่งตาม bars.BAR_OFFSET_H ให้ตรงกับ TradingView
    (ดูคำอธิบายเต็มที่ bars.py) — คืน DataFrame คอลัมน์เดียวกับ get_ohlcv ใช้กับ calc_adx ได้ตรงๆ

    2026-08-18: get_ohlcv() เองก็ apply offset นี้ให้ทุก timeframe="4H" อยู่แล้ว (ดู scoring.py)
    ฟังก์ชันนี้เลยเหลือแค่ wrapper บาง ๆ ไว้เผื่อโค้ดเก่าที่เรียกชื่อนี้อยู่ ไม่ต้องมี logic
    resample ซ้ำสองที่แล้ว

    2026-08-27: รับ as_of เพื่อให้ backtest เรียก get_regime ย้อนหลังได้ (ดู get_regime)"""
    return get_ohlcv(symbol, REGIME_TIMEFRAME, bars=bars, as_of=as_of)

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

# ---------------------------------------------------------------------------
# โครงสร้างแบบ regression — ทางเลือกทดลอง (2026-09-21) เปิดด้วย backtest_replay --struct-reg
# ---------------------------------------------------------------------------
# check_structure เดิม (swing) เปราะ: เปลี่ยน wick เล็กน้อย index swing เลื่อน regime พลิก · regression ไม่มี index ให้เลื่อน · มีสถานะ sideways จาก R² ต่ำ
# N ต่อ symbol = ครึ่งคลื่นราคา (median ระยะ swing ติดกัน) × 2 × k · k 3 -> 3.2 ตั้งแต่ 2026-09-29 (ผู้ใช้ · 3.0-3.3 ที่ราบ · 3.4 หน้าผา −9.82R)
#   ⚠️ k เลือกหลังเห็นผล · ตรวจปี 2027 บนไม้หลัง 2026-09-21 · ครึ่งคลื่น (แท่ง): XAU 6 · EUR 7 · BTC/ETH/US500/GBPCHF 9 · UKOIL/USDJPY 11 · HK50 12 · AUDNZD 14
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — STRUCT_REG_N ที่มา + กวาด k (2026-09-21..29)"
STRUCT_REG_N = {
    "XAUUSDm": 38, "EURUSDm": 45,
    "BTCUSDm": 58, "ETHUSDm": 58, "US500m": 58,
    "UKOILm":  70, "USDJPYm": 70,
    "HK50m":   77,
    # 2026-09-29: GBPCHFm — ครึ่งคลื่น median 9.0 แท่ง ณ จุดเข้า 17 ไม้ของ replay (ระยะ swing คู่ล่าสุด
    #   หลัง collapse) -> 9 x 2 x 3.2 = 58 (ตอน k=3 ได้ 54 เท่ากับ default พอดี)
    # ⚠️ วิธีวัดนี้ให้ค่าเดิมแค่ 4/8 ตัวเมื่อวัดซ้ำบนไม้ชุดปัจจุบัน (ดู memory struct-reg-n-cannot-be-tuned)
    "GBPCHFm": 58,
    # 2026-09-30: AUDNZDm — ครึ่งคลื่น median 14.0 แท่ง ณ จุดเข้า 16 ไม้ของ replay (preset ที่เลือก · offset 1)
    #   -> 14 x 2 x 3.2 = 90 ยาวสุดในระบบ (ไม้ถือ ~13 วัน · 4H ของคู่นี้แกว่งช้า)
    "AUDNZDm": 90,
}
STRUCT_REG_N_DEFAULT = 58   # = ครึ่งคลื่น median ทั้งระบบ 9 x 2 x 3.2
# R2_MIN = 0 -> **ไม่มีด่าน R² เลย** ใช้ทิศจาก slope อย่างเดียว
# ที่มา: ตอนตั้ง 0.5 ไม้ที่ถูกด่านนี้ตัดทิ้ง 32 ไม้มี avgR **+0.678** (ดีกว่าค่าเฉลี่ยระบบ
# +0.312 เกินสองเท่า) และ R² median ของไม้พวกนั้นอยู่ที่ **0.20** ไม่ใช่ 0.45 = ไม้ที่ทำเงิน
# เกิดตอนราคา**ไม่**เป็นเส้นตรง เพราะระบบเข้าที่แท่งแรกที่ ADX พลิก ซึ่งเป็นจุดที่ 36-54 แท่ง
# ย้อนหลังยังเป็นช่วงพัก/กลับตัวของขาก่อน — ด่าน R² จึงกรองเฉพาะไม้ที่เข้าเร็ว = ไม้ที่ทำเงิน
STRUCT_REG_R2_MIN    = 0.0

# ---------------------------------------------------------------------------
# 🔴 2026-09-21: **เปิดใช้จริง (USE_STRUCT_REG = True) ตามคำสั่งผู้ใช้** — อ่านให้ครบก่อนต่อยอด
# ---------------------------------------------------------------------------
# replay k = 2 / 2.5 / 3 / 3.5: −16.73 / −4.71 / **+9.31** / +0.26R = **ยอดแหลม ไม่ใช่ที่ราบ** · ผ่านกติกา 1/4 · กำไร 60% จาก ETH · ผู้ใช้ตัดสินใจใช้ทั้งที่รู้
# 👉 **ห้ามแตะ k / N / R2_MIN จนมีไม้ใหม่ 40-80 ไม้ (~2027)** แล้วเทียบกับ swing เดิมบนไม้หลัง 2026-09-21 เท่านั้น
#   กระทบแค่ get_regime() (ด่าน TREND/REVERSAL + ทิศ Scoring) · ไม่แตะ exit_monitor.check_structure_break · ถอยกลับ = False
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "regime_check.py — USE_STRUCT_REG เปิดใช้ (2026-09-21)"
USE_STRUCT_REG = True


def check_structure_reg(df: pd.DataFrame, symbol: str) -> dict:
    """โครงสร้างจากความชันของเส้น regression บนราคาปิด N แท่งล่าสุด
    คืน dict หน้าตาเดียวกับ check_structure เพื่อให้ผู้เรียกไม่ต้องรู้ว่าใช้วิธีไหน
    (hh/hl/ll/lh คืน None เพราะวิธีนี้ไม่ได้ตอบทีละจุด — ผู้เรียกที่ใช้ค่าพวกนี้มีแต่ run_check
     ที่ print เท่านั้น)"""
    N = STRUCT_REG_N.get(symbol, STRUCT_REG_N_DEFAULT)
    if len(df) < N:
        return {"trend": "ไม่ชัด / กำลังพัง", "intact": False, "hh": None, "hl": None,
                "ll": None, "lh": None, "n_highs": 0, "n_lows": 0, "r2": 0.0, "slope": 0.0}
    y = df["close"].iloc[-N:].to_numpy(dtype=float)
    x = np.arange(N, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = ((y - (slope * x + intercept)) ** 2).sum()
    total = ((y - y.mean()) ** 2).sum()
    r2 = float(1 - resid / total) if total > 0 else 0.0
    if r2 < STRUCT_REG_R2_MIN:
        trend, intact = "ไม่ชัด / กำลังพัง", False      # sideways — ใช้สตริงเดิมเพื่อไม่ให้
    elif slope > 0:                                     # ด่าน startswith(direction) เจอโดยบังเอิญ
        trend, intact = "Long (reg)", True
    else:
        trend, intact = "Short (reg)", True
    return {"trend": trend, "intact": intact, "hh": None, "hl": None, "ll": None, "lh": None,
            "n_highs": 0, "n_lows": 0, "r2": r2, "slope": float(slope)}


def check_structure(df: pd.DataFrame, vol_multiplier: float, wick_ratio_min: float = None) -> dict:
    """เทียบ 3 จุดสุดท้ายของแต่ละฝั่ง แต่ดูแค่ 'จุดแรก vs จุดสุดท้าย' (ข้ามจุดกลาง)
    จุดกลางถือเป็นการย่อ/เด้งชั่วคราวที่ยอมรับได้ในเทรนด์จริง — ไม่เอามาตัดสิน
    ทดสอบแล้วว่าสมดุลกว่าเทียบแค่ 2 จุด (ไวเกิน) หรือบังคับ 3 จุดเรียงต่อเนื่องเป๊ะ (เข้มเกิน)

    wick_ratio_min: เกณฑ์ไส้เทียนแบบ OR-logic (ผ่านได้ถ้า volume ถึง **หรือ** ไส้ถึง)
    🔴 2026-09-21 แก้: บรรทัดนี้เคยเขียนว่า "เฉพาะ XAU — symbol อื่นส่ง None เสมอ" ซึ่ง
    **หมดอายุไปแล้ว** — get_regime() ส่ง swing_wick_ratio_min(symbol) ให้ **ทุก symbol**
    (ดูจุดเรียกด้านล่างในไฟล์นี้) และตอนนี้ทุกตัวใน SYMBOLS มีค่า wick ของตัวเองครบ
    ไม่มีตัวไหนส่ง None เลย
    ⚠️ ที่ต้องแก้เพราะถ้าเชื่อข้อความเดิมจะสรุปว่า "เปลี่ยนเกณฑ์ wick ไม่กระทบ structure/regime"
    ซึ่งตรงข้ามกับที่วัดได้จริง: BTCUSDm 2025-12-17 12:00 เปลี่ยน wick 0.50 -> 0.48 ทำให้
    structure["intact"] พลิก False -> True และ regime พลิก "เขตเทา" -> "TREND" ทั้งที่ ADX
    เท่ากันเป๊ะ (22.0) เพราะ swing low ที่ wick 0.4893 เข้ามาคั่นพวงใน collapse_swing_runs
    (คำเตือนชุดเดียวกันนี้ถูกแก้ไปแล้วที่ inspect_swings.py 2026-09-19 และ swing.py 2026-09-20
     — ทั้งสองที่บันทึกไว้ว่าเคยทำให้อ่านข้อมูลผิดมาแล้วจริง ตัวนี้ตกหล่นเพราะอยู่คนละไฟล์)"""
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

    as_of: ส่งมาเมื่อเรียกจาก backtest (เช่น reversal.compute_reversal_score) — ใช้ตอนไม่ส่ง df
    มาเอง จะ fetch ด้วย as_of นี้แทนดึงสด

    2026-09-01: ตัดแท่งท้าย (แท่งฟอร์มมิ่ง) ทิ้งทั้งสองโหมดแล้ว — เดิมตัดเฉพาะโหมดสดตาม
    ความเข้าใจของ 2026-08-09 ว่า get_ohlcv_real(as_of=...) ตัดให้เองแล้ว ซึ่งหมดอายุตั้งแต่
    2026-08-26 ที่ bars.get_bars() ต่อแท่งฟอร์มมิ่งกลับเข้ามาให้ทั้งสองโหมด (ดูเหตุผลเต็ม +
    ตัวเลขผลกระทบที่ reversal.compute_reversal_score ซึ่งเป็นบั๊กเดียวกัน) — จุดนี้ไม่กระทบ
    get_regime() ที่ส่ง df มาเองพร้อม as_of=None (ตัดอยู่แล้วเหมือนเดิม) แต่กระทบ path ที่
    reversal.py เรียกเองตอนไม่ได้รับ key_level มา"""
    if df is None:
        df = get_ohlcv_real(symbol, "4H", bars=KEY_LEVEL_BARS, as_of=as_of)
    df = df.iloc[-KEY_LEVEL_BARS:].reset_index(drop=True)
    df = df.iloc[:len(df) - 1].reset_index(drop=True)   # ตัดแท่งยังไม่ปิด
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



# DIV_REQUIRE_OPPOSITE_SWING — คู่ swing ต้องมี swing ฝั่งตรงข้ามคั่น (= คนละขา ตามนิยามตำรา) · False = ระบบจริง
#   (memory divergence-internals: 43% ของ divergence ที่เจอเทียบสองยอดในกรอบเดียวกัน และไม่ได้แย่กว่า) · ทดลองด้วย
#   backtest_replay --div-textbook (2026-10-14 ผู้ใช้: "ลองปรับ Divergence ให้ตรงตามตำรา")
DIV_REQUIRE_OPPOSITE_SWING = False


def _find_spacing_partner(points: list[int], opposite: list[int] = None) -> int | None:
    """หาจุดก่อนหน้า (h1) ที่ห่างจากจุดล่าสุด (points[-1]) อย่างน้อย DIV_MIN_SPACING_BARS แท่ง
    ถอยย้อนจาก points[-2] ไปเรื่อยๆ จนเจอ หรือเกิน DIV_MAX_LOOKBACK_BARS ก็เลิกหา (คืน None)
    DIV_REQUIRE_OPPOSITE_SWING: ต้องมี swing ฝั่งตรงข้าม (opposite) อยู่ระหว่างคู่ด้วย"""
    if len(points) < 2:
        return None
    last = points[-1]
    for cand in reversed(points[:-1]):
        gap = last - cand
        if gap > DIV_MAX_LOOKBACK_BARS:
            break
        if gap >= DIV_MIN_SPACING_BARS:
            if DIV_REQUIRE_OPPOSITE_SWING and not any(cand < o < last for o in (opposite or [])):
                continue
            return cand
    return None


def check_divergence(df: pd.DataFrame, symbol: str = None) -> dict:
    """ข้อ 6 — Divergence (RSI period = DIV_RSI_PERIOD, 4H): ราคาทำ new extreme แต่ RSI ไม่ทำตาม
    Bearish: ราคา Higher High แต่ RSI Lower High  (ระวังฝั่งขึ้นอ่อนแรง)
    Bullish: ราคา Lower Low  แต่ RSI Higher Low   (ระวังฝั่งลงอ่อนแรง)
    เทียบ swing ราคา 2 จุดล่าสุด + จุดใหม่ต้องยืนยันภายใน DIV_MAX_AGE_BARS แท่ง
    + จุดแรกต้องเคยอยู่โซน overbought/oversold จริง (นิยาม divergence คลาสสิก, ยืนยันด้วย backtest)

    Volume filter: **ทุก symbol** ใช้ volume OR wick ratio ตั้งแต่ 2026-09-15 (ก่อนหน้านั้น
    XAU/GOLD เท่านั้น) — คุมด้วย DIV_WICK_ALL_SYMBOLS ⚠️ อ่านบล็อก -10.46R ที่ค่าคงที่นั้นก่อนแตะ
    ที่มาของ XAU: เดิมเคยปิด volume filter ไปเลยเพราะทดสอบแล้วกรอง volume แบบ AND เดี่ยวๆ
    ตรวจ divergence ไม่เจอเลย 0% แต่นั่นคือก่อนมี wick_ratio_min OR-logic (ดู check_structure)
    พอเปลี่ยนมาเป็น volume OR wick ratio เหมือน check_structure/check_key_level แล้วปัญหาเดิม
    ไม่เกิดซ้ำ (wick ช่วยกู้จุดที่ volume คนเดียวมองไม่เห็น)
    ไม่ส่ง symbol มา (None) จะ fallback ปิด volume filter เหมือนเดิมทุกกรณี

    ⚠️ 2026-09-13: เงื่อนไขแยกด้วย "ใช่ทองไหม" ไม่ใช่ "มี real volume ไหม" — และ XAU เองก็
    ไม่มี real volume เหมือนกัน (BITSTAMP_MAP มีแค่ BTC/ETH/XRP) เหตุผลเดิมที่เคยจดไว้ว่า
    "BTC ใช้ volume อย่างเดียวได้เพราะมี real volume" จึงอธิบายเงื่อนไขจริงไม่ได้ แต่วัดแล้ว
    การเปิด wick ให้ตัวที่ใช้ tick_volume ทำให้แย่ลง -10.46R จึงคงพฤติกรรมเดิมไว้

    Soft divergence (OR-logic): เปิดให้ **ทุก symbol ที่ส่งชื่อมา** (เงื่อนไขจริงคือ
    `bool(symbol)` — เห็นที่ soft_stall ด้านล่าง) เดิม comment ตรงนี้เขียนว่า "เปิดทั้ง BTC และ
    XAU" ซึ่งค้างมาจากตอนมีแค่ 2 symbol ในระบบ — 2026-09-13 แก้ให้ตรงโค้ด ไม่ได้เปลี่ยนพฤติกรรม
    ที่มา: backtest แรกสุดทดสอบแค่ BTC (sample เล็กมาก 3-6 เคส) ต่อมามี backtest แยกของ XAU
    (scratch_stall_backtest_xau.py, ~500 วัน 4H, threshold=5 win rate 80% จาก 20 เคส — สูงสุด
    ในบรรดา threshold ที่ลอง 3/5/7/10 และตรงกับค่า DIV_STALL_THRESHOLD ของ BTC พอดี) — sample
    ยังเล็ก ไม่ได้ผ่าน reversal scorecard filter เต็มรูปแบบ ควร optimize/ยืนยันซ้ำทีหลัง
    นอกจาก RSI Lower High/Higher Low เป๊ะ (strict) ยังนับเป็น divergence ได้ถ้า RSI แทบไม่ขยับ
    ตามราคา (ไม่เกิน DIV_STALL_THRESHOLD แต้มในทิศตรงข้าม)"""
    # 2026-09-13: เดิมชื่อ is_btc เขียนกลับด้าน (= ไม่ใช่ XAU/GOLD) ซึ่งหลอก
    is_gold = bool(symbol) and ("XAU" in symbol.upper() or "GOLD" in symbol.upper())
    vol_multiplier = swing_vol_multiplier(symbol) if symbol else 0.0
    # DIV_WICK_ALL_SYMBOLS=True (2026-09-15) -> ทุก symbol ได้ volume OR wick เหมือนที่ทุกจุดอื่น
    # ในระบบทำอยู่แล้ว · False -> ทองเท่านั้น (พฤติกรรมก่อน 2026-09-15) ดู comment ที่ค่าคงที่นั้น
    wick_ratio_min = (swing_wick_ratio_min(symbol)
                      if (symbol and (DIV_WICK_ALL_SYMBOLS or is_gold)) else None)
    if not DIV_SWING_VOL_FILTER:          # โหมดทดลอง — ดู comment ที่ตัวแปรนั้น
        vol_multiplier, wick_ratio_min = 0.0, None
    rsi = calc_rsi(df["close"])
    _right_h = DIV_SWING_RIGHT_HIGHS or SWING_LEFT_RIGHT    # ดูที่นิยาม DIV_SWING_RIGHT_*
    _right_l = DIV_SWING_RIGHT_LOWS or SWING_LEFT_RIGHT
    highs = find_swing_highs(df, left=SWING_LEFT_RIGHT, right=_right_h,
                             tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                             wick_ratio_min=wick_ratio_min)
    lows  = find_swing_lows(df, left=SWING_LEFT_RIGHT, right=_right_l,
                            tolerance_atr=SWING_TOLERANCE, vol_multiplier=vol_multiplier,
                            wick_ratio_min=wick_ratio_min)
    last_idx = len(df) - 1

    # result["fail"] = เงื่อนไขที่ไม่ผ่าน (ฝั่งที่มีคู่ swing ให้ตรวจ) — ไม่กระทบการตัดสินใจ
    # มีไว้ตอบว่า "Divergence ติดตรงไหน" ได้โดยไม่ต้องเดา: 4 เงื่อนไขเป็น AND ซ้อนกัน ถ้าไม่วัด
    # ก็ไม่รู้ว่าตัวไหนคือคอขวด (ดู scratchpad/div_funnel.py)
    result = {"divergence": None, "detail": "", "points": [], "swing_idx": None, "fail": []}
    if not highs and not lows:
        result["fail"].append("ไม่มี swing เลย")

    h1 = _find_spacing_partner(highs, lows)
    if highs and h1 is None:
        result["fail"].append("bearish: หาคู่ swing ที่ห่างพอไม่ได้")
    if h1 is not None:
        h2 = highs[-1]
        fresh   = (last_idx - h2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi.iloc[h1] >= DIV_ZONE_OVERBOUGHT
        # ผ่อนได้ตาม DIV_PRICE_TOLERANCE_ATR (ปกติ 0 = เทียบเป๊ะแบบเดิม)
        price_hh = df["high"].iloc[h2] > df["high"].iloc[h1] - _price_tol(df, h2)
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
        for ok, why in ((fresh, "bearish: swing เก่าเกิน"), (zone_ok, "bearish: RSI จุดแรกไม่ถึงโซน"),
                        (price_hh, "bearish: ราคาไม่ได้ทำ HH"),
                        (strict_lh or soft_stall, "bearish: RSI ไม่ได้ LH/stall")):
            if not ok:
                result["fail"].append(why)

    l1 = _find_spacing_partner(lows, highs)
    if lows and l1 is None:
        result["fail"].append("bullish: หาคู่ swing ที่ห่างพอไม่ได้")
    if l1 is not None:
        l2 = lows[-1]
        fresh   = (last_idx - l2) <= DIV_MAX_AGE_BARS
        zone_ok = rsi.iloc[l1] <= DIV_ZONE_OVERSOLD
        price_ll = df["low"].iloc[l2] < df["low"].iloc[l1] + _price_tol(df, l2)
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
        for ok, why in ((fresh, "bullish: swing เก่าเกิน"), (zone_ok, "bullish: RSI จุดแรกไม่ถึงโซน"),
                        (price_ll, "bullish: ราคาไม่ได้ทำ LL"),
                        (strict_hl or soft_stall, "bullish: RSI ไม่ได้ HL/stall")):
            if not ok:
                result["fail"].append(why)

    result["detail"] = "ไม่พบ divergence (สดภายใน {} แท่ง)".format(DIV_MAX_AGE_BARS)
    return result


# ---------------------------------------------------------------------------
# สรุป Regime
# ---------------------------------------------------------------------------

def _reversal_regime(peak: dict, key_level: dict, divergence: dict, min_peak="config"):
    """branch Reversal ของ classify_regime — คืน (regime, action, color) หรือ None ถ้า ADX ไม่เข้าเกณฑ์"""
    # Reversal candidate — relative peak & decline: ทำจุดสูงสุดใหม่ในรอบที่มองย้อน (ไม่ว่าตัวเลขจะเป็นเท่าไหร่
    # เช่น 28, 32, 45) แล้วโค้งลงติดกันครบแท่ง — ไม่ใช้ threshold ตายตัวอย่าง 40 อีกต่อไป เพราะ "แรงสุดของรอบนั้น"
    # ไม่จำเป็นต้องแตะระดับคงที่เสมอไป จุดเปลี่ยนโมเมนตัม (peak แล้วอ่อนแรง) สำคัญกว่าตัวเลขสัมบูรณ์
    # peak ต้องสูงพอด้วย (ดู ADX_MIN_PEAK_REVERSAL) — ถ้าไม่ถึง ปล่อยให้ไหลไป branch ถัดไป
    # (อาจกลายเป็น TREND = เปิด Scoring แทน) ไม่ใช่ตีเป็น NO_TRADE
    # min_peak="config" = ใช้ ADX_MIN_PEAK_REVERSAL · None = ไม่มีเกณฑ์ peak (ใช้กับ reversal_nopeak ด้านล่าง)
    _mp = ADX_MIN_PEAK_REVERSAL if min_peak == "config" else min_peak
    if peak["declining"] and (not _mp or peak["peak"] >= _mp):
        at_key  = (not KEY_LEVEL_REQUIRED) or bool(key_level and key_level.get("at_key_level"))
        has_div = bool(divergence and divergence.get("divergence"))

        # ครบ 3 เงื่อนไข (peak โค้งลง + อยู่ที่ Key Level + มี Divergence) -> พร้อมเปิด Reversal จริง
        if at_key and has_div:
            # KEY_LEVEL_REQUIRED = False -> อาจไม่มีโซนเลย (nearest None) · เดิม format ตรงๆ จะ crash ทั้ง get_regime
            _kl_txt = (f"Key Level {key_level['nearest']:,.2f}" if (key_level or {}).get("nearest") is not None
                       else "ไม่มีโซน Key Level")
            _kl_txt = _kl_txt if KEY_LEVEL_REQUIRED else f"({_kl_txt} · ไม่บังคับแล้ว)"
            return ("REVERSAL-READY",
                    f"ครบเงื่อนไข: ADX peak {peak['peak']:.1f} โค้งลง {peak['bars_since_peak']} แท่ง "
                    f"+ {_kl_txt} "
                    f"+ Divergence ({divergence['divergence']}) -> เปิด Reversal scorecard ได้", CYAN)

        # peak โค้งลงแล้ว แต่เงื่อนไขยืนยันยังไม่ครบ -> เฝ้าดู
        missing = []
        if not at_key:  missing.append("Key Level")
        if not has_div: missing.append("Divergence")
        return ("REVERSAL-WATCH",
                f"ADX peak {peak['peak']:.1f} แล้วโค้งลง {peak['bars_since_peak']} แท่ง "
                f"-> เฝ้าดู Reversal แต่ยังขาด: {' + '.join(missing)}", CYAN)

    return None


def classify_regime(adx_now: float, direction: str, peak: dict, structure: dict,
                    key_level: dict = None, divergence: dict = None) -> tuple[str, str, str]:
    """คืน (regime, action, color)"""
    # REVERSAL_USES_ADX_FLOOR = False -> Reversal ตัดสินด้วย peak + โค้งลงอย่างเดียว ไม่สน ADX ตอนนี้
    if not REVERSAL_USES_ADX_FLOOR:
        rev = _reversal_regime(peak, key_level, divergence)
        if rev:
            return rev

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

    rev = _reversal_regime(peak, key_level, divergence)
    if rev:
        return rev

    # ADX 40+ ยังพุ่งไม่หยุด (ยังไม่โค้งลง) — 2026-08-31: regime นี้ถูกย้ายไป REGIME_NO_TRADE
    # แล้ว (ทั้ง scheduler.py และ backtest_replay.py) = ไม่เปิด scorecard ใดๆ ทั้งสิ้น
    # เดิมข้อความบอกว่า "ใช้ Scoring ตามเทรนด์เท่านั้น" ซึ่งตอนนี้ขัดกับสิ่งที่ระบบทำจริง —
    # scheduler จะพิมพ์ action นี้แล้วตามด้วย "SKIP" ทันที ถ้าไม่แก้ log จะอ่านแล้วสับสน
    if adx_now >= ADX_STRONG_TREND and direction == "ขึ้น":
        return ("TREND แรงจัด", "ADX 40+ ที่ยังพุ่ง (ยังไม่ peak) = ไม่เข้าไม้ทั้งสองทาง "
                "(ห้ามสวน + ไม่เข้าตามเทรนด์เพราะเป็น entry ท้ายขา)"
                + ("" if structure["intact"] else " (structure ไม่ intact ด้วย)"), GREEN)

    # TREND ปกติ — ADX >= ADX_GRAY_HIGH (22) + ทิศไม่ลง + structure intact
    # (comment เดิมเขียน ">= 25" ค้างมาจากก่อนปรับ ADX_GRAY_HIGH 25 -> 22 เมื่อ 2026-07-22)
    if direction in ("ขึ้น", "ทรง") and structure["intact"]:
        # 2026-10-10: เดิมเขียน "เทรดตามทิศ {structure}" ทั้งที่ทิศไม้มาจาก Bias 1D (scheduler พิมพ์ทั้งสองค่าในบรรทัดถัดไป)
        return ("TREND", f"เปิด Scoring — Structure 4H = {structure['trend']} (ทิศไม้มาจาก Bias 1D)", GREEN)

    # เงื่อนไขไม่ครบ
    return ("เขตเทา", "เงื่อนไขไม่ครบ (ADX ลง หรือ structure ไม่ intact) — รอความชัดเจน", YELLOW)


def get_regime(symbol: str, as_of=None) -> dict:
    """เก็บ logic การเก็บข้อมูล+ตัดสินใจ Regime ทั้งหมดไว้ที่เดียว — ใช้ทั้งจาก CLI (run_check)
    และจากที่อื่น (เช่น scheduler.py) โดยไม่ต้อง print รายงานเต็ม

    as_of=None (ปกติ) = เช็คสด ณ ตอนนี้ — as_of=datetime = จำลอง regime ณ เวลานั้นในอดีต
    (ผ่าน bars.get_bars ที่ตัดแท่งอนาคตออกให้แล้ว) เพิ่ม 2026-08-27 เพราะ scheduler.py กรอง
    ไม่ให้เปิดไม้เลยถ้า regime อยู่ใน config.REGIME_NO_TRADE (ดูรายชื่อที่นั่น — อย่าคัดลอกมาเขียนซ้ำ) แต่
    backtest ที่ผ่านมาทั้งหมดไม่เคยใส่ตัวกรองนี้ — วัดคุณภาพสัญญาณบนชุดไม้ที่ระบบจริงไม่ได้
    เทรดด้วยซ้ำ ทำให้ตัวเลขที่ได้ไม่ใช่ของระบบจริง"""
    # 4H — ADX ไม่ต้องพึ่ง volume | ขอบแท่งเลื่อนตาม ADX_BAR_OFFSET_H ให้ตรง TradingView
    df = get_adx_bars(symbol, bars=BARS, as_of=as_of)
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
    df_4h_full = get_ohlcv_real(symbol, "4H", bars=KEY_LEVEL_BARS, as_of=as_of)
    df_4h = df_4h_full.iloc[-BARS:].reset_index(drop=True)
    # USE_STRUCT_REG = True -> ใช้ check_structure_reg แทน (ตั้งจาก backtest_replay --struct-reg
    # เท่านั้น · default False = พฤติกรรมระบบจริงเป๊ะ) ดูเหตุผล/พารามิเตอร์ที่ check_structure_reg
    _df_struct = df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True)
    if USE_STRUCT_REG:
        structure = check_structure_reg(_df_struct, symbol)
    else:
        structure = check_structure(_df_struct,
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
        # regime ที่ branch Reversal จะให้ถ้าไม่มีเกณฑ์ peak — ข้อมูลอย่างเดียว ไม่แตะ "regime"
        # (ใช้โดย backtest_replay --breakout-no-min-peak · 2026-09-30)
        "reversal_nopeak": _reversal_regime(peak, key_level, divergence, min_peak=None),
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
