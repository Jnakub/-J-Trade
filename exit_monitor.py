"""
exit_monitor.py — รันค้างไว้ อัปเดตข้อมูล Exit ของทุก Open Position ทุก 1 ชั่วโมง

ดึงจาก MT5 อัตโนมัติ: Current Price, Extreme Price ตั้งแต่เข้า, ATR(14),
RSI(RSI_PERIOD), Bollinger Bands(20,2), Climax check แล้วรัน Exit Decision Checklist
+ Position Sizing Rules — จากนั้น "สั่งจริง" ตามผลลัพธ์ (ปิด 100%/บางส่วน,
ขยับ SL เป็น Breakeven) โดยอัตโนมัติ — มี hard guard: สั่งได้เฉพาะบัญชี DEMO
เท่านั้น ถ้าเป็นบัญชี Real จะปฏิเสธการสั่งทุกครั้ง

ใช้: python exit_monitor.py            # รันวนทุก 1 ชม.
     python exit_monitor.py --once     # เช็ครอบเดียวแล้วจบ
"""
import sys
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import pandas as pd
import requests
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import MT5_TIMEFRAMES
from mt5_connect import connect, get_tick_or_raise, is_demo_account
from scoring import get_ohlcv, get_ohlcv_real, ema
from swing import calc_atr, find_swing_highs, find_swing_lows, swing_vol_multiplier, swing_wick_ratio_min, collapse_swing_runs
from indicators import calc_rsi as _calc_rsi
from vsa import _volume_class, _spread_class, _close_position, _price_position, detect_vsa_pattern
from order import close_order, partial_close_order, modify_sltp, clamp_lot
import journal
import notify
from logger_setup import get_logger, tee_print

log = get_logger("exit_monitor")
print = tee_print(log)   # เขียนทุกอย่างที่ print ลง logs/exit_monitor.log ด้วย (ดู logger_setup.py)

AUTO_EXECUTE = True   # False = แค่แนะนำเหมือนเดิม, True = สั่งจริงตามผลลัพธ์ (เฉพาะบัญชี DEMO)

STRUCTURE_LEFT_RIGHT  = 4
STRUCTURE_TOLERANCE   = 0.22   # 2026-07-24: เปลี่ยนจาก 0.25 — ยังไม่มี backtest ยืนยัน
# กฎ "ถือครบ N วันแล้วยังไม่ถึง R" -> base_keep_pct = 50 (ตัวเลข 50 hardcode ใน analyze_position)
# 2026-09-13: กวาดเกณฑ์ครบ 7 symbol 730 วัน (backtest_exit_rules.py --set=slow / slow2) —
# **ไม่แตะ คงไว้ที่ 3 วัน** ไม่ใช่เพราะ 3 ดีที่สุด แต่เพราะข้อมูลไม่มีทิศพอจะบอกว่าค่าไหนดีกว่า
#   ΔR เทียบ control(3 วัน):    5ว     7ว     10ว    14ว    ปิดกฎ
#     BTCUSDm                 +0.04  −0.24  −0.19  +0.20  +0.32
#     ETHUSDm                 +0.31  −0.33  +0.34  +0.81  +0.46
#     EURUSDm                 +0.39  +0.73  +0.53  +0.14  +0.11
#     GBPUSDm                 −1.32  −0.20  +0.08  +0.37  +0.84
#     US500m                  −1.34  −0.83  −0.82  −1.42  −1.14
#     USDJPYm                 +1.57  +0.89  +0.51  +0.64  +1.07
#     XAUUSDm                 −0.66  +1.21  +2.47  +2.56  +3.01
#     รวม                     −1.01  +1.23  +2.92  +3.30  +4.67
# 🔴 **ค่าที่ดีที่สุดของแต่ละ symbol ไม่ซ้ำกันเลย** (BTC/GBP/XAU = ปิด · ETH = 14 · EUR = 7 ·
#    USDJPY = 5 · US500 = control) และผลไม่เรียงตามลำดับใน 3 ใน 7 symbol = fit กับไม้ไม่กี่ไม้
#    ต่อ symbol ไม่ใช่คุณสมบัติของเกณฑ์  ยอด "ปิดกฎ +4.67R" ฝั่ง Scoring ตัด XAU ออกเหลือ
#    **−0.14R จาก 6 symbol** (ทั้งหมดคือ XAU) และไม้แย่ลง 67 ไม้ ดีขึ้น 39 ไม้
# ⚠️ "ขยับเส้นเป็น 10-14 วัน" ไม่ใช่ทางสายกลาง — ไม้ Scoring ถือเฉลี่ย 7.6 วัน (มัธยฐาน 5.2)
#    ที่ 10 วันไม้ที่กฎเคยแตะถูกปลดออกไปแล้วเกือบครบ (BTC 14/15 ไม้, XAU 19/19) = เท่ากับปิดกฎ
#    โดยเรียกชื่ออื่น  ตัวเลือกจริงมีแค่ "3 วัน" กับ "ปิด"
# ⚠️ และสมมติฐานของกฎ ("ถ้า setup ดี ราคาควรวิ่งภายใน 3 วัน") ขัดกับข้อมูลของระบบเอง:
#    71% ของไม้ Scoring ถือเกิน 3 วัน กฎจึงยิงใส่ไม้ปกติ 60% ของทั้งหมด (~7.6 ครั้ง/symbol/ปี)
#    ไม่ใช่คัดเฉพาะไม้ที่ตายแล้ว — ถ้าจะรื้อเรื่องนี้อีก ต้องเริ่มจากตรงนี้ ไม่ใช่จากตัวเลข ΔR
#
# 🔴 2026-09-14 — **ค่าเสียโอกาสของกฎนี้เป็นศูนย์โดยโครงสร้าง ไม่ต้องวัดด้วยซ้ำ**: กฎตัดแค่
# 50% ช่องจึงไม่ว่าง (ดูย่อหน้าเรื่อง "รอ setup ใหม่" ด้านบน) ต่อให้มีสัญญาณรออยู่กี่ช่วงก็เข้า
# ไม่ได้อยู่ดี สิ่งเดียวที่กฎนี้ซื้อได้จริงคือ "ลดความเสี่ยงของไม้ที่ยังไม่ไปไหน" เท่านั้น
#
# ข้อมูลประกอบจาก backtest_replay --log-blocked (เก็บทุกรอบที่ข้ามเพราะช่องไม่ว่างแต่ผ่าน
# ด่านอื่นครบ) 7 symbol 730 วัน — **556 ช่วงสัญญาณ** ที่ระบบเห็นแต่เข้าไม่ได้:
#   เกิดภายใน 4 ชม.แรกหลังไม้เดิมเปิด   186 ช่วง (33%)  = สัญญาณเดิมที่ยังยืนอยู่ (เข้าซ้ำ move เดิม)
#   เกิดหลังไม้เดิมเปิดไปแล้วเกิน 3 วัน  136 ช่วง (24%)  = ช่วงที่ "ออกเร็วขึ้น" จะปลดล็อกได้จริง
# แปลง 136 ช่วงนั้นเป็น R แล้ว (--min-age=72 คือจำลองว่าไม้แรกออกตอนครบ 3 วันพอดี ช่องว่างแล้ว
# รับสัญญาณถัดไปต่อคิวกันไป): **62 ไม้ +3.79R ตลอด 2 ปี 7 symbol = +0.27R/symbol/ปี**
#   XAU +5.58 · ETH +5.38 · EUR +1.20 · USDJPY −0.37 · US500 −2.05 · GBP −2.30 · BTC −3.65
#   บวก 3/7 symbol และ **ตัด 2 ไม้ใหญ่สุดออกกลายเป็น −2.01R** = แยกจากศูนย์ไม่ออก
# => แม้แต่กฎที่ปิดไม้ **100%** ตอนครบ 3 วัน (ซึ่งกฎนี้ไม่ได้ทำ) ก็ไม่มีอะไรให้เก็บอยู่ดี
#    ไม้ที่ระบบเห็นแต่เข้าไม่ได้ในช่วงนั้นไม่ได้ดีกว่าค่าเฉลี่ยเลย
# ⚠️ อย่าอ่านเลยไปกว่านี้: นี่คือค่าของช่วง "หลัง 3 วัน" เท่านั้น ไม่ได้แปลว่าการออกเร็วขึ้นใน
#    ช่วงอื่นไม่มีค่า และไม่ได้พูดถึงกฎที่ปิดไม้ด้วยเหตุผลอื่น (structure break / invalidation)
# เพดานเวลาถือไม้ — ครบแล้วปิด 100% ไม่สนว่า R เท่าไหร่
# 2026-09-14: **เพิ่งเพิ่มเข้าระบบจริง** ก่อนหน้านี้มีแต่ใน backtest (backtest_replay.MAX_HOLD_DAYS
# = 30 ใส่ไว้กันไม้ค้างกินเวลารัน) ระบบจริงไม่มีเพดานเวลาเลย = **ระบบจริงกับ backtest ทำคนละอย่าง
# มาตลอด** ทุกตัวเลขที่ใช้ตัดสินใจคิดบนสมมติฐานว่ามีเพดานนี้ การใส่จึงเป็นการทำให้ตรงกัน
# ไม่ใช่การเพิ่มกฎใหม่ — และ backtest วัดผลของมันไม่ได้ เพราะ backtest สมมติว่ามีอยู่แล้ว
# หลักฐานที่มี: จำลองไม้ 6 ไม้ที่เคยชนเพดานนี้ใน 730 วัน 7 symbol โดยปล่อยให้เดินต่อถึง 180 วัน
#   มีเพดาน 30 วัน  +2.27R   |   ปล่อยยาว  +0.41R   (แย่ลง 5 จาก 6 ไม้)
#   ถือจริงถ้าไม่มีเพดาน: มัธยฐาน 37 วัน ยาวสุด 55 วัน
#   ไม้พวกนี้ MFE สูงมาก (2.04 / 2.11 / 1.57 / 3.38R) แต่จบที่ BE/SL = ถูกทางแล้วคายกำไรคืนหมด
# ⚠️ 6 ไม้คือ sample ที่เล็กมาก ตัวเลข +1.86R ที่ต่างกันจึงไม่ใช่เหตุผลหลักที่ใส่กฎนี้
#    เหตุผลหลักคือความสอดคล้องระหว่างระบบจริงกับเครื่องมือที่ใช้ตัดสินใจ
# 🔴 ถ้าแก้ค่านี้ ต้องแก้ backtest_replay.MAX_HOLD_DAYS และ backtest_trade_sim.MAX_HOLD_DAYS
#    ให้ตรงกันด้วย ไม่งั้นจะกลับไปวัดคนละระบบกับที่รันจริงอีก
MAX_HOLD_DAYS         = 30

SLOW_TRADE_DAYS       = 3
# 2026-09-14: 0.5 -> 0.0 = **ตัดเฉพาะไม้ที่ติดลบจริงที่วันที่ 3** เลิกยิงใส่ไม้ที่กำไรอยู่แต่ไปช้า
# เหตุผลหลักไม่ใช่ตัวเลข แต่คือกฎเดิมไม่ตรงกับเจตนาตัวเอง: มันประกาศว่า "ยังไม่วิ่งใน 3 วัน =
# สมมติฐานอาจผิด" แต่ยิงใส่ไม้ที่ +0.3R ด้วย ซึ่งไม่ใช่หลักฐานว่าสมมติฐานผิด — ครึ่งหนึ่งของ
# การยิงทั้งหมดเป็นแบบนี้ (BTC ไม้ที่กฎแตะ 15 -> 8 ไม้เมื่อเปลี่ยนเป็น R<0)
# ยืนยันด้วย backtest_replay เต็ม 7 symbol 730 วัน (--slow-r=0) เทียบบนหน้าต่างร่วม:
#   +44.26R -> +46.12R = **+1.86R**  บวก 3/7 (USDJPY +1.90 · XAU +1.60 · ETH +0.49 ·
#   BTC −0.36 · US500 −0.39 · EUR −0.40 · GBP −0.98)
#   **ชุดไม้ไม่ขยับเลยสักไม้ทั้ง 7 symbol (200/200)** — กฎตัดขนาดอย่างเดียว ช่องไม่ว่าง
#   ไม้ขาดทุนเต็ม 1R 39 -> 40 (+1)  ฝั่งลบ −64.56 -> −68.10R  WR 53.0% -> 49.5%
# ⚠️ +1.86R ต่อ 2 ปี 7 symbol = ระดับที่แยกจาก noise ไม่ได้ **ห้ามคาดหวังเป็นผลตอบแทน**
#    สิ่งที่ยืนยันได้คือ "แก้ให้กฎตรงกับเจตนาแล้วไม่มีต้นทุน" ไม่ใช่ "วิธีทำเงิน"
# ⚠️ backtest_exit_rules (เครื่องคัดกรอง) ให้ +2.66R สูงกว่าจริง 0.80R — และรอบ 7 วันก็สูงเกิน
#    0.74R เหมือนกัน **คัดกรองประเมินสูงเป็นระบบราว 0.75R ต่อรอบ** ต้องหักก่อนใช้ตัดสินใจเสมอ
# ทางเลือกอื่นที่วัดแล้วไม่เอา (ดูตารางเต็มที่ SLOW_TRADE_DAYS/SLOW_TRADE_KEEP):
#   R<0 + ปิด 100% @7วัน (replay เต็ม) +3.12R แต่ไม้ขาดทุนเต็ม 39 -> **56** = แทบไม่ต่างจาก
#   ปิดกฎทิ้ง (57 ไม้) · ปิดกฎทิ้ง +4.67R ที่ 57 ไม้ · ทุกทางที่ได้ R มากกว่านี้จ่ายด้วยหางลบทั้งหมด
SLOW_TRADE_R          = 0.0
SLOW_TRADE_KEEP       = 50    # เหลือกี่ % เมื่อกฎยิง — 2026-09-14: แยกออกมาเป็นค่าคงที่ (เดิม
                              # hardcode 50 อยู่ใน analyze_position) เพื่อให้ backtest กวาดได้
                              # 0 = ปิดไม้ 100% ซึ่งเป็น**กฎคนละตัว** เพราะคืนช่องถือไม้จริง
                              # (ที่ 50 ช่องยังไม่ว่าง) ค่า 50 = พฤติกรรมเดิมไม่เปลี่ยน

# News calendar (ForexFactory) — เฉพาะ High impact เท่านั้น
# 2026-08-02: เคยสงสัยว่า endpoint นี้อาจเป็น "ปฏิทินตามสัปดาห์ปฏิทิน (จ-อา)" ที่จะพลาดข่าว
# สัปดาห์ถัดไปถ้ารันตอนดึกวันอาทิตย์ — เช็คจริงแล้วไม่ใช่: ทดสอบตอนวันอาทิตย์ (2026-08-02)
# endpoint คืนช่วงข้อมูล 2026-08-02 -> 2026-08-08 คือ rolling 7 วันนับจากวันที่ request จริง
# ไม่ใช่ calendar week คงที่ — ไม่มีช่องโหว่นี้ ไม่ต้องดึง nextweek เพิ่ม (ff_calendar_nextweek.json
# ก็ไม่มีอยู่จริงด้วย ลองยิงแล้วได้ 404)
NEWS_URL          = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_CURRENCY     = "USD"     # BTCUSDm/XAUUSDm ผันผวนตาม USD เป็นหลัก
NEWS_IMPACT       = "High"
NEWS_LOOKAHEAD_H  = 24        # เตือนล่วงหน้ากี่ชั่วโมง (checklist ข้อ 3)
NEWS_TIMEOUT_SEC  = 8

NEWS_IMMINENT_H   = 1         # ใกล้ข่าวภายในกี่ชั่วโมง -> ลด position
NEWS_IMMINENT_KEEP = 50       # ออกก่อนข่าว 1 ชม. -> เหลือ 50%

NEWS_POST_H       = 6         # ข่าวผ่านไปกี่ชั่วโมงถือว่า "สงบ" แล้ว

# Position Sizing Rules — "ถ้า trigger ควรเหลือกี่ %"
#
# 2026-08-27: เพิ่มกฎ "ถึง 1R" — คู่กับ checklist ข้อ 5 ที่เลื่อน SL ไป breakeven ที่ 1R อยู่แล้ว
# แต่เดิม "ไม่มีการปิดบางส่วนที่ 1R" (กฎปิดบางส่วนที่ใกล้ที่สุดคือ 2R) ซึ่งจำลองแล้วพบว่าการ
# เลื่อน BE เดี่ยวๆ เป็นตัวเลือกที่แย่ที่สุด: มันตัดไม้ที่แตะ 1R แล้วย่อกลับให้จบที่ 0R ทั้งที่
# หลายตัวจะไปถึง TP ได้ โดยไม่ได้อะไรคืนเป็น R เลย
#
# จำลองบนไม้ชุดเดียวกัน 434 ไม้ (BTCUSDm 730 วัน, ไม้ที่ผ่าน MIN_SCORE จริง, เดินแท่ง 4H
# ไปข้างหน้าจาก entry — ไม่กรองไม้เพิ่มแม้แต่ตัวเดียว เปลี่ยนแค่วิธีออก):
#   SL/TP เป๊ะ ไม่ทำอะไร        Win 32.7%  AvgR -0.03R  แพ้เต็ม -1R 61.8%
#   เลื่อน SL ทุนที่ 1R เฉยๆ     Win 19.6%  AvgR -0.03R  แพ้เต็ม -1R 45.9%  <- ของเดิม
#   ปิดครึ่งที่ 1R + เลื่อน BE   Win 50.0%  AvgR -0.01R  แพ้เต็ม -1R 45.9%  <- ของใหม่
#
# ⚠️ ข้อจำกัด: วัดจาก BTCUSDm ช่วงเดียว 2 ปี in-sample, จำลองว่าปิดได้เป๊ะที่ราคา 1R (ของจริง
# monitor รันเป็นรอบทุก INTERVAL_SECONDS ไม่ใช่ทุก tick จึงช้ากว่านี้เสมอ) และ "ชนะ" ในตัวเลข
# ข้างบนนับ +0.5R เป็นชนะ — กำไรเฉลี่ยต่อไม้เล็กลงตาม Total R ดีขึ้นแค่เล็กน้อย (-14.2R -> -3.9R)
# ผลหลักคือ win rate และการลดไม้ที่แพ้เต็มจำนวน ไม่ใช่กำไรรวมที่เพิ่มขึ้นมาก
#
# 🔴 **เงื่อนไขนี้เกิดขึ้นแล้ว 2026-09-13** (RULE_1R_KEEP = 100) — อ่านย่อหน้านี้คู่กับบล็อก
# ด้านล่างเสมอ: พร้อมกับการเพิ่มกฎ 1R มีกฎอีก 2 ข้อถูก "ลบทิ้ง" เพราะพิสูจน์ได้ว่ามันไม่มีทาง
# มีผลอีกแล้ว (ไม่ใช่แค่เกิดยาก) — พอ 1R ไม่ตรึงที่ 50% ทั้งสองข้อก็กลับมามีความหมายทันที:
#   "ถึง 2R" (trigger R>=2, keep 50) — R>=2 ย่อมแปลว่า R>=1 เสมอ และ keep เท่ากันเป๊ะ
#       min() จึงได้ 50 เท่าเดิมทุกกรณี ไม่มีทางตัดเพิ่มได้เลย
#   "ใกล้ TP (<=0.5%)" (trigger ระยะถึง TP <= 0.5%, keep 50) — ไม้ทุกตัวผ่าน hard block
#       R:R >= MIN_RR_HARD_BLOCK (1.5) มาแล้ว การที่ราคาเข้าใกล้ TP ระดับ 0.5% จึงแปลว่า
#       R เดินมาเกือบเต็ม R:R ของไม้ = อย่างน้อย ~1.4R ซึ่งเลย 1R ไปแล้วเสมอ (r_multiple
#       คิดจาก initial_sl ที่ตรึงไว้ ไม่ใช่ SL ปัจจุบัน จึงไม่แกว่งตาม trailing)
# **ยังไม่เอากลับทั้งสองข้อ** (ตัดสินใจ 2026-09-13 ไม่ใช่ลืม — ดูเหตุผลในบล็อกด้านล่าง)
# ประโยคเดิมตรงนี้ที่ว่า "กฎที่เหลือมีผลจริงเฉพาะช่วง R < 1 เพราะกฎ 1R ตรึง 50%" **ไม่จริงแล้ว**
# ตั้งแต่ 1R เป็น 100: ใน **ชุด position_rules** เหลือ Indicator ร้อน (75) กับ ใกล้ข่าว (50)
# ที่ยังตัดจริง และยิงได้ทุกช่วง R ไม่มีเพดานอะไรมาครอบอีก (1R / เดินทาง >=50% ไป TP / Climax
# = 100 ทั้งสามแล้ว) — ขั้น base_keep_pct ที่มาก่อนหน้ายังตัดตามเดิมทุกอย่าง ไม่เกี่ยวกับรอบนี้
# (trend invalidation 75/50/0 · slow trade 50% · structure broken/invalidated 0%)
#
# 2026-09-13: **RULE_1R_KEEP 50 -> 100 และ RULE_HALFWAY_KEEP 60 -> 100 = ปิดทั้งสองกฎ**
# (100 = ไม่ตัดเลย เกณฑ์ยังถูกคำนวณ/แสดงผล และ SL ยังเลื่อนไป breakeven ที่ 1R เหมือนเดิม
#  — การเลื่อน BE อยู่คนละบรรทัด ไม่ได้ถูกปิดไปด้วย ดู desired_sl ท้ายไฟล์)
# backtest_replay ครบ 7 symbol 730 วัน (--rule-1r-keep=100 --rule-halfway-keep=100):
#   ก่อน 202 ไม้ WR 54.0% +36.95R  ->  หลัง 202 ไม้ WR 52.5% +41.86R   (+4.92R, ดีขึ้น 6/7)
#   **ชุดไม้เหมือนเดิมทุกไม้ และวิธีออกเหมือนเดิมทุกช่อง** (TP 64 / BE 42 / SL 83 ตรงกันเป๊ะ)
#   เปลี่ยนแค่ขนาดไม้ระหว่างทาง: ตัดบางส่วน 308 -> 268 ครั้ง, ไม้ที่จบ TP ได้ avgR 1.33 -> 1.49
#   ไม่กระจุก — ไม้ที่เปลี่ยนมากสุด +0.78R ตัด 3 ไม้ใหญ่สุดออกยังเหลือ +2.63R
#   แยกกลยุทธ์: Scoring +2.51R/177 ไม้ · Reversal +2.41R/25 ไม้ (บวก 7/7 symbol) = ชุดกฎนี้
#   จูนมาบนไม้ Scoring แต่กิน Reversal หนักกว่าราว 10 เท่าต่อไม้
#   WR ลด 1.5pp ตามกลไก: ไม้ที่เคยล็อกกำไรครึ่งไม้ที่ 1R แล้วจบบวกนิดหน่อย กลายเป็นจบ 0/ลบ
#   แลกกับไม้ที่ไปถึงเป้าได้เต็มไม้ — Total R เป็นตัวตัดสิน ไม่ใช่ WR
# คัดกรองก่อนหน้าด้วย backtest_exit_rules.py ชี้ว่า RULE_HOT_KEEP เป็น noise (บวก 3/7 symbol)
# จึงไม่แตะ — และการวัดทีละกฎประเมินค่าต่ำกว่าจริงเสมอ เพราะ position_rules ใช้ min() กฎที่เหลือ
# รับช่วงตัดแทนทันที (BTC: ปิดทีละตัวรวมกัน +0.33R แต่ปิดพร้อมกัน +1.86R)
#
# ⚠️ ขัดกับผลวัดปี 2026-08-27 ข้างบนที่สรุปว่า "เลื่อน BE เดี่ยว ๆ แย่ที่สุด" ซึ่งคือสภาพที่
# ค่านี้พากลับไป — ถือว่า**ผลใหม่แทนที่ผลเก่า** ไม่ใช่ขัดกัน เพราะของเก่า: BTC ตัวเดียว
# in-sample · เดินแท่ง 4H เองโดยไม่มี exit_monitor ตัวจริง · ตัดสินด้วย win rate (นับ +0.5R
# เป็นชนะ) · และวัดบนไม้ที่ผ่าน MIN_SCORE ซึ่ง**ด่านนั้นถูกปิดไปแล้วตั้งแต่ 2026-09-06** =
# คนละชุดไม้กับระบบปัจจุบัน  ส่วนของใหม่วัดด้วย engine ที่ตรงกับ replay ทุกไม้ (202/202)
#
# 🔴 **ตัดสินใจแล้วว่าไม่เอากฎ 2 ข้อที่ "กลับมามีความหมาย" คืน** (ดูย่อหน้าถัดไป) — +4.92R
# ข้างบนวัดในสภาพที่ไม่มีมัน ถ้าใส่กลับต้องวัดใหม่ทั้งหมด ไม่ใช่การลืม
RULE_1R_TRIGGER      = 1.0
RULE_1R_KEEP         = 100   # เดิม 50 — ดูบล็อกด้านบน
RSI_OVERBOUGHT       = 70
RSI_OVERSOLD         = 30
RULE_HOT_KEEP        = 75
RULE_HALFWAY_TRIGGER = 50    # TP progress %
RULE_HALFWAY_KEEP    = 100   # เดิม 60 — ดูบล็อกด้านบน
# 2026-09-10: 50 -> 100 = **ปิดกฎนี้** (100 = ไม่ตัดเลย เกณฑ์ยังถูกคำนวณและแสดงผลเหมือนเดิม)
# วัดด้วย backtest_replay --log-cuts ครบ 7 symbol 730 วัน ซึ่งบันทึกทุกครั้งที่กฎสั่งปิดบางส่วน
# แล้วตามดูว่าราคาไปต่อถึงไหนจนไม้ปิดจริง (309 ครั้ง):
#   กฎ                    ครั้ง   R เฉลี่ยตอนยิง   เสียโอกาส
#   แท่ง Climax            118        -0.06        +12.53R   <- 81% ของความเสียหายทั้งชุด
#   Indicator ร้อน         120        +0.43         +1.80R
#   ถึง 1R                  40        +1.13         +1.52R
#   เดินทาง >=50% ไป TP     27        +1.05         +1.38R
#                                              รวม  +15.39R
# **Climax ยิงตอนไม้ยังติดลบ** (R เฉลี่ย -0.06) = ไม่ได้ล็อกกำไร แต่ตัดไม้ที่ขาดทุนทิ้งแล้วไม้
# พวกนั้นฟื้นกลับมา — ความเสียหาย +11.26R จาก +12.53R อยู่ในกลุ่ม "ตัดตอนติดลบ" ล้วนๆ
# ผลจริงเมื่อปิดกฎ (7 symbol 730 วัน):
#   ก่อน 192 ไม้ WR 50.5% +22.44R  ->  หลัง 192 ไม้ WR 53.1% +32.93R   (+10.48R, ดีขึ้น 7/7)
#   **ชุดไม้เหมือนเดิมทุกไม้** จุดเข้า/จุดออก/วิธีออกตรงกันหมด (SL 79 TP 55 BE 43) เปลี่ยนแค่
#   ขนาดไม้ระหว่างทาง — การปิดบางส่วนไม่ปล่อยช่อง จึงไม่เกิดปัญหา "ไม้อื่นเข้ามาแทน" ที่ทำให้
#   ด่านกรองล้มเหลวมา 3 ครั้ง (ดู MIN_SL_DISTANCE_PCT / TP_FIB_RATIO / MIN_SCORE ใน config.py)
# ⚠️ ลองแล้วว่า "ยิงเฉพาะตอนไม้กำไร" (CLIMAX_ONLY_IN_PROFIT) **แย่กว่าปิดทิ้ง** — ได้ +28.62R
#    เทียบกับ +32.93R เพราะการตัดตอนกำไรก็ยังเผาเงินทุกช่วง (0-0.5R +1.81 / 0.5-1R +0.81 /
#    1-1.5R +0.97 / 1.5-2R +0.54) ไม่มีระดับกำไรไหนที่กฎนี้คุ้ม
RULE_CLIMAX_KEEP     = 100
CLIMAX_ONLY_IN_PROFIT = False   # ทดสอบแล้วแพ้การปิดกฎทิ้ง (ดูด้านบน) — เก็บสวิตช์ไว้วัดซ้ำ

INTERVAL_SECONDS  = 3600
MONITOR_TIMEFRAME = MT5_TIMEFRAMES["4H"]
BARS              = 210

ATR_PERIOD     = 14   # 2026-08-17: เปลี่ยนจาก 22 -> 14 ตามคำสั่งผู้ใช้ — เดิม 22 ใช้แค่โชว์บน
                      # รายงาน (ไม่เคยคุม SL/TP จริง ตัวที่คุม SL trailing จริงคือ TRAIL_ATR_PERIOD
                      # ด้านล่างซึ่งเป็น 14 อยู่แล้ว) ความต่างระหว่าง 22 บนหน้าจอกับ 14 ที่ใช้จริง
                      # ทำให้สับสนว่า SL คุมด้วยค่าไหนกันแน่ — ยกเลิกความต่างนี้โดยรวมเป็น 14 ทั้ง
                      # ระบบ ยังไม่มี backtest ยืนยันว่า 14 ดีกว่า 22 จริง (ดูเหตุผลเต็มในแชท
                      # ตอนตัดสินใจ — 22 เดิมอ้างอิง Chandelier Exit ของ Chuck LeBeau ซึ่งออกแบบ
                      # มาสำหรับแท่งรายวัน เอามาใช้ตรงๆ กับแท่ง 1H ความหมาย "1 เดือน" ที่ตั้งใจไว้
                      # เสียไปอยู่แล้ว ทั้ง 14/22 ในบริบท 1H นี้ไม่มีตัวไหนมี backtest รองรับจริง)
RSI_PERIOD     = 20   # 2026-09-12: 14 -> 20 ตามคำสั่งผู้ใช้ ให้ตรงกับ DIV_RSI_PERIOD/RSI_SCORE_PERIOD
                      # **ยังไม่มี backtest รองรับ** — ค่าเดิม 14 ก็ไม่เคยมีเหมือนกัน (มาจากการ
                      # รวมให้เท่ากันทั้งระบบเมื่อ 2026-08-17 ดู ATR_PERIOD ด้านบน) จึงเป็นการ
                      # เปลี่ยนทับของที่ไม่เคยพิสูจน์ ไม่ใช่การทิ้งค่าที่พิสูจน์แล้ว
                      # คุมกฎเดียว: "Indicator ร้อน" (ข้อ 2, บรรทัด ~677) ที่ตัด RULE_HOT_KEEP
                      # เมื่อ RSI แตะ RSI_OVERBOUGHT/OVERSOLD **หรือ** ราคาทะลุ Bollinger
                      # ทิศที่คาดได้: RSI(20) แกว่งแคบกว่า RSI(14) แตะ 70/30 ยากขึ้น กฎนี้ยิง
                      # น้อยลง = ถือไม้นานขึ้น ไปทางเดียวกับปัญหา "ไม้ชนะเคยขึ้น +2.19R แต่เก็บ
                      # ได้ +0.83R" (2026-09-13 วัดใหม่หลังปิด Climax: MFE 2.20R เก็บได้ 1.23R
                      # = 56% ไม่ใช่ 38% แล้ว) — แต่เป็นการคาด ไม่ใช่ผลวัด และขาที่ทะลุ BB ไม่ขยับ
                      # ตาม (BB_PERIOD ยังเป็น 20 ของมันเอง คนละตัวกัน) กฎจึงยิงจากขา BB เท่าเดิม
                      # ถอยกลับ = ตั้ง 14 ที่บรรทัดนี้บรรทัดเดียว ไม่มีที่อื่นอ้างถึง
BB_PERIOD      = 20
BB_STD         = 2.0

# ATR Trailing SL — roll ทุก 1 ชม. ด้วย ATR(14) บน TF 1H (Swing ฐานยังใช้ 4H)
TRAIL_TIMEFRAME  = MT5_TIMEFRAMES["1H"]
TRAIL_ATR_PERIOD = 14
TRAIL_BARS       = 500   # ~20 วันบน 1H — ต้องครอบคลุมเวลาที่ถือ position

# เพดานการขยาย SL (2026-08-02) — SL ตัวนี้ตั้งใจให้ "หายใจ" ตาม ATR ได้ทั้งสองทาง (ถอยห่างตอน
# ผันผวนเพื่อไม่ให้โดนเขี่ยแล้วกลับมาที่เดิมตอนสงบ) ไม่ใช่ ratchet ทางเดียวแบบ TP — แต่ lot ถูก
# คำนวณตอนเปิดไม้จากระยะ SL เริ่มต้น (= 1R) แล้วไม่เปลี่ยนอีก ถ้า ATR ระเบิดจน SL ห่างขึ้นมาก
# ขาดทุนจริงตอนโดน SL จะเกิน RISK_PER_TRADE ตามสัดส่วน (และทำให้ MAX_DAILY_LOSS เพี้ยนตาม)
# ค่านี้จำกัดแค่ "หางความเสี่ยง" — ระยะปกติที่ ATR โตราว 2-3 เท่ายังหายใจได้ตามดีไซน์เดิม
#
# 2026-09-15: **1.5 -> 1.2** (วัดครั้งแรกด้วย backtest_exit_rules --set=widen 7 symbol 730 วัน)
# ⚠️ ต้นทุนที่วัดได้ = **ศูนย์เป๊ะ** ไม่ใช่ "เกือบศูนย์": ไม้ที่ขยาย SL มากสุดใน 200 ไม้คือ
# **1.107 เท่า** เพดานทั้ง 1.5 / 1.3 / 1.2 จึงไม่เคยยิงเลยสักครั้ง (ระดับ 1.2 เปลี่ยนไม้ 0 ไม้
# ผลต่าง +0.00R) เลือกระหว่างสามค่านี้ด้วยผล R ไม่ได้ **เป็นการตัดสินใจเชิงนโยบายความเสี่ยง**
# ที่เปลี่ยนเพราะ: ขาดทุนไม้เดียวสูงสุด 3% ของพอร์ต (1.5 x RISK_PER_TRADE 2%) -> 2.4%
# = RISK_PER_TRADE กับ MAX_DAILY_LOSS หมายความตามที่เขียนไว้จริง
#
# 🔴 อย่าลดต่อไปถึง 1.0 — วัดแล้วได้ **−0.53R** และรูปร่างกลับด้านสำหรับกฎความเสี่ยง:
# ดีขึ้น 76 ไม้รวม +2.19R (ไม้ละ 0.03R) แต่แย่ลง 3 ไม้รวม −2.73R โดย −2.29R มาจากไม้เดียว
# (GBPUSDm 2025-11-28: SL ขยายออกตอนผันผวน รอดการเขี่ย แล้ววิ่งไป MFE 3.61R จบ TP +1.70R
#  พอ cap ที่ 1.0 กลายเป็น SL −0.60R) = แลกกำไรเล็กที่แน่นอนกับขาดทุนก้อนใหญ่ที่นานๆ ครั้ง
# นั่นคือ "การหายใจ" ย่อหน้าบนทำงานตรงตามเจตนา ไม่ใช่ข้อบกพร่อง
#
# หมายเหตุวิธีวัด: ค่าที่เป็น no-op **ไม่ต้องยืนยันด้วย backtest_replay เต็ม** เพราะไม่มีไม้ไหน
# เปลี่ยนเวลาออก จึงไม่มีผลต่อช่องถือไม้ให้ backtest_exit_rules มองพลาด (ข้อจำกัดปกติของมัน)
MAX_SL_WIDEN_R   = 1.2   # SL ห่างจาก entry ได้ไม่เกินกี่เท่าของระยะ 1R เริ่มต้น

# TP Trailing (2026-07-23) — เริ่มขยับ TP เข้ามาเมื่อราคาใกล้ TP เดิมมากพอ กันเคส "เกือบถึง
# TP แล้วราคากลับตัวจนโดน SL" (ไม้เต็มกำไรกลายเป็นขาดทุน) — ใช้ pinned anchor = TP ตอนเปิดไม้
# จริง (ไม่ใช่ pos.tp ปัจจุบันที่อาจถูกขยับไปแล้ว) + ATR(1H) ล่าสุด แบบเดียวกับ SL Trailing
# กันบั๊ก "TP หดตามราคาที่ถอยกลับ" เพราะสูตรนี้ไม่ได้อ้างอิงราคาปัจจุบัน/peak เลย มีแค่
# TP เดิม (คงที่) กับ ATR สด — ratchet ทางเดียวเทียบกับ TP ปัจจุบันที่ broker เท่านั้น
TRAIL_TP_TRIGGER_PCT = 1.0   # เริ่ม trail เมื่อห่างจาก TP เดิมไม่เกิน 1%
TRAIL_TP_ATR_BUFFER  = 0.5   # ATR(1H) × 0.5

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)
def _g(s): return f"{GREEN}{s}{RESET}"
def _y(s): return f"{YELLOW}{s}{RESET}"
def _r(s): return f"{RED}{s}{RESET}"
def _c(s): return f"{CYAN}{s}{RESET}"
def _b(s): return f"{BOLD}{s}{RESET}"


# ---------------------------------------------------------------------------
# Indicators (RSI, Bollinger Bands) — ATR ใช้ swing.calc_atr ที่มีอยู่แล้ว
# ---------------------------------------------------------------------------

# calc_rsi ย้ายไป indicators.py แล้ว (2026-08-19) — เดิมเป็นสำเนาเหมือนกันเป๊ะกับใน
# regime_check.py พอ scoring.py ต้องใช้ด้วยเลยย้ายไปไว้ที่เดียว (ดู indicators.py)
def calc_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    return _calc_rsi(series, period)


def calc_bollinger(series: pd.Series, period: int = BB_PERIOD, std_mult: float = BB_STD):
    mid   = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


CLIMAX_PRICE_POS_LOOKBACK = 2   # ดู comment ด้านล่าง


def is_climax_bar(df: pd.DataFrame, idx: int) -> tuple[bool, str]:
    """เช็คแท่งปิดล่าสุดว่าเป็น Buying/Selling Climax ไหม (volume สุดขั้ว + range ใหญ่)

    2026-08-09: price_pos (ตำแหน่งราคาเทียบ 10 แท่งย้อนหลัง) เดิมเช็คจากแท่ง idx เดียวกับ
    volume/spread/close_pos ทั้งหมด — เก็บข้อมูลจริง BTC 4H ย้อน 100 วัน (600 แท่ง) พบว่าไม่มี
    Climax แม้แต่ครั้งเดียวที่ offset=0 (ทั้งฝั่ง Long/Short) เพราะจุดต่ำสุด/สูงสุดจริงมักเกิด
    คนละแท่งกับแท่งที่ volume/close ระเบิด (เช่น ทำ Low แท่งหนึ่ง แล้วเด้งแรงพร้อม volume แท่ง
    ถัดมา — พอราคาเด้งขึ้นแล้ว price_pos ของแท่งนั้นเองจะเลื่อนไปเป็น "Top" ไม่ใช่ "Bottom" อีก)
    แก้โดยเช็ค price_pos ย้อนหลังได้ถึง CLIMAX_PRICE_POS_LOOKBACK แท่ง (volume/spread/close_pos
    ยังคงเช็คจากแท่ง idx เข้มเหมือนเดิม ผ่อนแค่ price_pos) — ข้อมูลจริงพบว่า lookback=2 จับได้
    45% (Long) / 25% (Short) ของแท่ง "ระเบิด" ทั้งหมด โดยไม่ไกลจนเสี่ยงจับคู่เหตุการณ์คนละช่วง
    (เกิน 3-4 แท่ง = 12+ ชม. ความเชื่อมโยงเริ่มไม่น่าเชื่อถือ)"""
    row       = df.iloc[idx]
    color     = "Green" if row["close"] >= row["open"] else "Red"
    vol       = _volume_class(df, idx)
    spread    = _spread_class(df, idx)
    close_pos = _close_position(row)

    for back in range(CLIMAX_PRICE_POS_LOOKBACK + 1):
        check_idx = idx - back
        if check_idx < 0:
            break
        price_pos = _price_position(df, check_idx)
        pattern, _, _ = detect_vsa_pattern(color, vol, spread, close_pos, "None", price_pos)
        if "Climax" in pattern:
            return True, pattern

    return False, "No Pattern"


# ---------------------------------------------------------------------------
# Extreme price ตั้งแต่เข้า
# ---------------------------------------------------------------------------

def extreme_price_since_entry(df: pd.DataFrame, entry_time: pd.Timestamp, direction: str) -> float:
    since = df[df["time"] >= entry_time]
    if since.empty:
        since = df.tail(1)
    return since["high"].max() if direction == "Long" else since["low"].min()


# ---------------------------------------------------------------------------
# ATR Trailing SL — ฐานตรึงยึดจาก SL ที่คำนวณตอนเข้าไม้จริง (Swing + ATR4H×0.1 buffer, ตัวเดียว
# กับที่ใช้คิด R:R และเป็น Broker SL — ดู scoring.find_sl_from_structure), ATR trail ใช้ ATR(14)
# บน 1H (roll ทุกชั่วโมง) — 2026-08-07: เปลี่ยนจากยึด Swing ดิบ (ไม่มี buffer) มาเป็นยึด SL ข้อ 1
# ตรงๆ แทน กัน R:R ตอนเข้ากับระยะเสี่ยงจริงตอนถือ (trailing) ห่างกันคนละสูตร
#   ฐานตรึง (Long)  = SL ตอนเข้า − ATR 1H แท่งปิดก่อนเข้า                (ไม่เปลี่ยนตลอดการถือ)
#   SL(t)           = ฐานตรึง − ATR 1H แท่งปิดล่าสุด                     (roll ใหม่ทุกแท่ง 1H)
#   SL เริ่มต้น      = ฐานตรึง − ATR ตอนเข้า = SL ตอนเข้า − 2×ATR ตอนเข้า
#                     → ใช้เป็นระยะ 1R คงที่ (คำนวณย้อนได้เสมอ ไม่ต้องเก็บ state)
#   ฝั่ง Short กลับด้าน: SL ตอนเข้า + ATR
# ---------------------------------------------------------------------------

def calc_atr_trailing_sl(df_swing: pd.DataFrame, symbol: str,
                         entry_time: pd.Timestamp, direction: str,
                         pinned_swing: float = None, pinned_atr_entry: float = None,
                         as_of=None) -> dict | None:
    """df_swing = ข้อมูล 4H สำหรับหา Swing ฐาน (เฉพาะ fallback path ที่ไม่มี pinned_swing ส่งมา)
    — ATR ดึง 1H ภายในฟังก์ชันเอง คืน {swing, atr_entry, atr_latest, pinned_base, initial_sl, new_sl}
    หรือ None ถ้าข้อมูลย้อนหลังไม่พอ (เช่น position เก่ากว่าจำนวนแท่งที่ดึงมา)

    pinned_swing/pinned_atr_entry: ค่าที่บันทึกไว้ตอนเปิดไม้ (journal.get_pinned_anchor) — ปกติ
    pinned_swing คือ SL ที่คำนวณตอนเข้าไม้จริง (Swing + ATR4H×0.1 buffer, ดู scheduler.py) ไม่ใช่
    Swing ดิบ ถ้าส่งมาครบทั้งคู่ จะใช้ค่านี้ตรงๆ แทนการคำนวณ swing/atr_entry ใหม่จาก rolling window
    (กัน anchor สลับถ้าถือ position ยาวจน swing เดิมหลุดขอบหน้าต่างข้อมูล) — atr_latest
    ยังคำนวณสดเสมอไม่ว่ากรณีไหน เพราะเป็นตัวที่ทำให้ SL trail จริงทุกชั่วโมงตามดีไซน์เดิม
    ถ้าไม่ส่งมา (position เก่าก่อนมีฟีเจอร์นี้ หรือบันทึกฐานตรึงไม่สำเร็จตอนเข้า) จะ fallback ไป
    คำนวณ Swing ดิบใหม่จาก rolling window แบบเดิม (ไม่ใช้ SL ข้อ 1 buffer — เคสนี้พบยาก)"""
    # ── ATR(14) จาก 1H — atr_latest คำนวณสดเสมอ ──
    df_1h = get_ohlcv(symbol, TRAIL_TIMEFRAME, bars=TRAIL_BARS, as_of=as_of)
    atr_1h = calc_atr(df_1h, TRAIL_ATR_PERIOD)
    atr_latest = atr_1h.iloc[len(df_1h) - 2]

    if pinned_swing is not None and pinned_atr_entry is not None:
        swing, atr_entry = pinned_swing, pinned_atr_entry
    else:
        # ── Fallback: คำนวณ swing/atr_entry ใหม่จาก rolling window (พฤติกรรมเดิมก่อนแก้) ──
        in_4h = df_swing.index[df_swing["time"] <= entry_time]
        if len(in_4h) == 0:
            return None
        swing_end = in_4h[-1] - 1             # แท่ง 4H ปิดล่าสุดก่อนเข้า
        if swing_end < STRUCTURE_LEFT_RIGHT * 2:
            return None
        df_before = df_swing.iloc[:swing_end + 1].reset_index(drop=True)

        in_1h = df_1h.index[df_1h["time"] <= entry_time]
        if len(in_1h) == 0:
            return None
        prev_idx = in_1h[-1] - 1              # แท่ง 1H ปิดล่าสุดก่อนเข้า
        if prev_idx < TRAIL_ATR_PERIOD:
            return None
        atr_entry = atr_1h.iloc[prev_idx]

        vol_mult = swing_vol_multiplier(symbol)
        wick_min = swing_wick_ratio_min(symbol)
        swing_highs_b = find_swing_highs(df_before, left=STRUCTURE_LEFT_RIGHT,
                                         right=STRUCTURE_LEFT_RIGHT, tolerance_atr=STRUCTURE_TOLERANCE,
                                         vol_multiplier=vol_mult, wick_ratio_min=wick_min)
        swing_lows_b  = find_swing_lows(df_before, left=STRUCTURE_LEFT_RIGHT,
                                        right=STRUCTURE_LEFT_RIGHT, tolerance_atr=STRUCTURE_TOLERANCE,
                                        vol_multiplier=vol_mult, wick_ratio_min=wick_min)
        swing_highs_b, swing_lows_b = collapse_swing_runs(swing_highs_b, swing_lows_b, df_before)
        if direction == "Long":
            if not swing_lows_b:
                return None
            swing = df_before["low"].iloc[swing_lows_b[-1]]
        else:
            if not swing_highs_b:
                return None
            swing = df_before["high"].iloc[swing_highs_b[-1]]

    if direction == "Long":
        pinned = swing - atr_entry
        return {"swing": swing, "atr_entry": atr_entry, "atr_latest": atr_latest,
                "pinned_base": pinned,
                "initial_sl": pinned - atr_entry,
                "new_sl":     pinned - atr_latest}

    pinned = swing + atr_entry
    return {"swing": swing, "atr_entry": atr_entry, "atr_latest": atr_latest,
            "pinned_base": pinned,
            "initial_sl": pinned + atr_entry,
            "new_sl":     pinned + atr_latest}


# ---------------------------------------------------------------------------
# ข้อ 1 — Daily ปิดสวน trend ที่ใช้เข้า? (EMA50 บน 1D เท่านั้น — 2026-08-07 ตัด 4H ออก)
# ---------------------------------------------------------------------------
# เดิมเช็ค 1D OR 4H — 4H ไวเกินไป (เคสจริง: ราคาหลุด EMA50 บน 4H อยู่ 15 แท่ง (~2.5 วัน) ก่อนดีด
# กลับไปกำไร >1R ออกไปก่อนหน้านั้นเสียโอกาสฟรีทั้งที่ trend ใหญ่ยังไม่พัง) Daily กรอง noise ในตัว
# อยู่แล้ว (ต้องปิดทั้งวันถึงนับ) จึงตัด 4H ออกจากเงื่อนไข hard-invalidation ไปเลย เหลือ Daily อย่างเดียว
#
# เพิ่ม grace period + 3 ระดับความรุนแรงตามความต่อเนื่อง (2026-08-07):
#   - TREND_CHECK_GRACE_BARS แท่ง Daily แรกหลังเข้าไม้ — ไม่เช็คเลย ให้ thesis มีเวลาพิสูจน์ตัวเอง
#     ก่อนตัดสิน (กันโดนตัดทันทีที่เพิ่งเข้า จากที่ entry ผ่าน MIN_SCORE ได้แม้ trend criteria จะ
#     เฉียดขอบพอดี)
#   - ปิดสวน trend ติดกัน 1 แท่ง Daily (หลัง grace) -> ออก 25% (เผื่อเป็นแค่ noise แล้วดีดกลับ)
#   - ปิดสวน trend ติดกัน 2 แท่ง -> ออก เพิ่มเป็น 50% รวม
#   - ปิดสวน trend ติดกัน 3 แท่งขึ้นไป -> ออก 100% (thesis พังจริง)
#   - ไม้ Reversal ข้าม check นี้ทั้งหมด (เข้าสวน trend โดยดีไซน์อยู่แล้ว — direction มาจาก divergence
#     polarity ไม่ใช่ trend bias เช็คนี้จะ false-positive ตั้งแต่แท่งแรกที่เปิดไม้)
#
# 2026-09-13: **ปิดกฎนี้ทั้งชุด** (100 ทุกระดับ = ไม่ตัดเลย เกณฑ์ยังถูกคำนวณ/แสดงผลเหมือนเดิม)
# ตามคำสั่งผู้ใช้ หลังวัดครบ 7 symbol 730 วัน (backtest_exit_rules.py --set=trend บนฐานหลังปิด
# กฎ 1R/halfway):
#   ΔR ปิดทั้งกฎ: ETH +1.95 · XAU 0.00 · USDJPY 0.00 · EUR −0.03 · BTC −0.30 · US500 −0.30 ·
#                 GBP −0.70  = **+0.62R** จากไม้ที่กฎเคยแตะแค่ 8 ไม้ใน 177 ไม้ Scoring
# ⚠️ หลักฐานบาง: บวก 1/7 symbol ยอดทั้งหมดมาจาก ETH ตัวเดียว (3 ไม้) ส่วนอีก 4 symbol แย่ลงทุกไม้
#    ที่กฎแตะ — sample ขนาดนี้เคยหลอกโปรเจกต์นี้มาแล้ว (ดู DIV_RSI_PERIOD ที่ +5.07R มาจาก GBP
#    3 ไม้) เหตุผลที่ปิดจึงไม่ใช่ตัวเลขนี้ แต่คือความสอดคล้องกับกลไกที่วัดหนักแน่นกว่าในวันเดียวกัน
#    (กฎ 1R/halfway: 63 ไม้ บวก 6/7 symbol +4.92R) ว่าการตัดไม้ที่กำลังทำงานทิ้งคือสิ่งที่กินกำไร
#    🔴 ถ้าจะวัดซ้ำเมื่อมีไม้มากขึ้น: ถอยกลับ = ตั้ง {1: 75, 2: 50, 3: 0} ที่บรรทัดนี้บรรทัดเดียว
#
# 🔴 **ขาที่ยิงจริงคือขา 3 แท่ง (ออก 100%) ไม่ใช่ขาบางส่วน** — ไม้ทั้ง 8 ไม้ที่กฎแตะจบด้วย
# "ออก 100% ทันที — Trend/S" ทุกไม้ และรอบที่ตัดขาบางส่วนทิ้ง (เหลือแต่ระดับ 100%) ได้ +0.00R
# ไม้เปลี่ยน 0 ทุก symbol = **ขา 25%/50% ไม่เคยผูกอะไรเลยใน 2 ปี** (ยังไม่รู้กลไกว่าทำไม —
# อย่าเดา ถ้าจะแตะขาพวกนี้ต้องวัดก่อน)  ตัวอย่างที่ชัดที่สุด ETH 2026-03-29: กฎสั่งออก 100%
# ที่ +0.85R ถ้าไม่มีกฎ ไม้เดียวกันวิ่งต่อถึง TP ได้ +2.21R
TREND_CHECK_GRACE_BARS = 5
# ปิดอยู่ — ค่าเดิมก่อน 2026-09-13 คือ {1: 75, 2: 50, 3: 0} (ดูบล็อกด้านบน)
TREND_CHECK_KEEP_BY_CONSEC = {1: 100, 2: 100, 3: 100}   # แท่งปิดสวนติดกัน -> % lot ที่ควรเหลือ
TREND_CHECK_MAX_CONSEC     = max(TREND_CHECK_KEEP_BY_CONSEC)


def check_trend_invalidation(symbol: str, direction: str, entry_time: pd.Timestamp,
                             strategy: str = "Scoring", as_of=None) -> dict:
    """คืน {"active", "consec_break", "keep_pct", "reason"} — keep_pct = % lot ที่ควรเหลือ
    (100 = ปกติ, 75/50 = เตือนภัยบางส่วน, 0 = invalidated เต็มรูปแบบ)
    strategy == 'Reversal' -> ข้าม check นี้ทั้งหมด (active=False เสมอ ไม่มีทาง invalidate)"""
    if strategy == "Reversal":
        return {"active": False, "consec_break": 0, "keep_pct": 100,
                "reason": "ข้าม — ไม้ Reversal เข้าสวน trend โดยดีไซน์อยู่แล้ว"}

    df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=210, as_of=as_of)
    close = df_1d["close"]
    ema50 = ema(close, 50)
    is_long = direction == "Long"

    last_closed_idx = len(df_1d) - 2   # แท่ง Daily ปิดล่าสุด (แท่งสุดท้ายยังไม่ปิด)
    in_1d = df_1d.index[df_1d["time"] <= entry_time]
    entry_idx = in_1d[-1] if len(in_1d) else last_closed_idx
    bars_since_entry = max(0, last_closed_idx - entry_idx)

    if bars_since_entry < TREND_CHECK_GRACE_BARS:
        return {"active": False, "consec_break": 0, "keep_pct": 100,
                "reason": f"อยู่ในช่วง grace period ({bars_since_entry}/{TREND_CHECK_GRACE_BARS} แท่ง Daily หลังเข้าไม้)"}

    consec = 0
    idx = last_closed_idx
    while idx > entry_idx and consec < TREND_CHECK_MAX_CONSEC:
        broken = (close.iloc[idx] < ema50.iloc[idx]) if is_long else (close.iloc[idx] > ema50.iloc[idx])
        if not broken:
            break
        consec += 1
        idx -= 1

    if consec == 0:
        return {"active": True, "consec_break": 0, "keep_pct": 100, "reason": "OK"}

    keep_pct = TREND_CHECK_KEEP_BY_CONSEC[consec]
    if keep_pct <= 0:
        reason = f"Daily ปิดสวน trend ติดกัน {consec} แท่ง — thesis พังจริง"
    else:
        reason = f"Daily ปิดสวน trend ติดกัน {consec} แท่ง — ออก {100 - keep_pct}% เผื่อไว้ก่อน"

    return {"active": True, "consec_break": consec, "keep_pct": keep_pct, "reason": reason}


# ---------------------------------------------------------------------------
# ข้อ 2 — Structure ที่ใช้เข้าพังแล้ว? (หลุด Swing Low/High ล่าสุดบน 4H)
# ---------------------------------------------------------------------------
# 2026-08-07: เพิ่ม ATR buffer กันปิดทะลุแค่เฉียดๆ ด้วย noise ปกติ — เดิมเทียบ close_price กับ
# ราคา Swing ดิบๆ แบบ strict ไม่มี buffer เลย ปิดต่ำ/สูงกว่าแม้แค่นิดเดียวก็ออก 100% ทันที ใช้สูตร
# เดียวกับ "still_valid" ใน swing.find_sl_from_structure (ATR × STRUCTURE_TOLERANCE) ไม่เพิ่ม
# magic number ใหม่ — ต้องปิดทะลุเกิน ATR×0.22 ถึงนับว่า broken จริง

# 2026-09-03: **ปิดกฎนี้ทั้งระบบ** (STRUCTURE_BREAK_ENABLED = False) ตามการตัดสินใจของผู้ใช้
# หลังวัดด้วย backtest_replay 730 วันครบ 8 symbol ใน config.SYMBOLS (engine ที่แก้บั๊ก lookahead
# แล้ว) — กฎนี้เป็นทางออกที่ระบบใช้บ่อยที่สุดและแพงที่สุด: ปิดไม้ 74 จาก 142 ไม้ (52%) ที่ WR
# 28.4% รวม -16.98R
#   เปิดกฎ (เดิม)  142 ไม้  WR 42.3%  -1.81R
#   ปิดกฎ          130 ไม้  WR 45.4%  +1.44R      => +3.25R ทั้งพอร์ต
# แยกตาม asset class — กฎนี้มีค่าเฉพาะกับ crypto:
#   fx     -5.98R -> -2.70R (+3.28)   crypto  +4.98R -> +4.72R (-0.26)
#   index  -1.88R -> -1.75R (+0.13)   metal   +1.06R -> +1.16R (+0.10)
# (crypto หลุด swing แล้วไหลต่อจริง ส่วน fx แกว่งกลับเข้ากรอบบ่อย ปิด 100% ตอนหลุด = ขายที่ก้น)
# ผลข้างเคียงที่ต้องรู้: ไม้ที่ชน SL เพิ่มจาก 27 -> 62 ไม้ (-39.04R) แลกกับไม้ที่ถึง TP เพิ่มจาก
# 29 -> 36 (+34.74R) และ BE 12 -> 21 ไม้ (+4.55R) — ไม้ถือนานขึ้น ยึด "ช่อง" นานขึ้น จำนวนไม้รวม
# จึงลดจาก 142 เหลือ 130 และมี 8 ไม้ชนเพดาน MAX_HOLD_DAYS ของ backtest (ระบบจริงไม่มีเพดานนี้
# ตัวเลขส่วนนั้นจึงไม่ตรงกับของจริง)
# ⚠️ ทางเลือกที่ยังไม่ได้ทำ: แยกเปิด/ปิดตาม asset class (เปิดกับ crypto ปิดกับที่เหลือ) น่าจะดีกว่า
# ทั้งสองทางเลือกสุดขั้ว แต่ผู้ใช้เลือกปิดทั้งระบบไปก่อน — ฟังก์ชันยังอยู่ครบ เปิดกลับได้ที่ค่านี้
STRUCTURE_BREAK_ENABLED = False


def check_structure_break(symbol: str, direction: str, as_of=None) -> bool:
    df_4h = get_ohlcv_real(symbol, "4H", bars=210, as_of=as_of)
    vol_mult = swing_vol_multiplier(symbol)
    wick_min = swing_wick_ratio_min(symbol)
    close_price = df_4h["close"].iloc[-2]
    buffer = calc_atr(df_4h).iloc[-2] * STRUCTURE_TOLERANCE
    highs = find_swing_highs(df_4h, left=STRUCTURE_LEFT_RIGHT, right=STRUCTURE_LEFT_RIGHT,
                             tolerance_atr=STRUCTURE_TOLERANCE, vol_multiplier=vol_mult,
                             wick_ratio_min=wick_min)
    lows  = find_swing_lows(df_4h, left=STRUCTURE_LEFT_RIGHT, right=STRUCTURE_LEFT_RIGHT,
                            tolerance_atr=STRUCTURE_TOLERANCE, vol_multiplier=vol_mult,
                            wick_ratio_min=wick_min)
    highs, lows = collapse_swing_runs(highs, lows, df_4h)
    if direction == "Long":
        if not lows:
            return False
        return close_price < (df_4h["low"].iloc[lows[-1]] - buffer)
    if not highs:
        return False
    return close_price > (df_4h["high"].iloc[highs[-1]] + buffer)


# ---------------------------------------------------------------------------
# ข้อ 3 — Major news/event (ForexFactory calendar, เฉพาะ High impact)
# ---------------------------------------------------------------------------

_news_cache = {"fetched_at": None, "events": []}


def fetch_news_calendar() -> list[dict]:
    """ดึง ForexFactory calendar — cache ไว้ 1 ชม. กันยิง request รัวๆ"""
    now = datetime.now()
    if _news_cache["fetched_at"] and (now - _news_cache["fetched_at"]).total_seconds() < INTERVAL_SECONDS:
        return _news_cache["events"]

    try:
        resp = requests.get(NEWS_URL, timeout=NEWS_TIMEOUT_SEC)
        resp.raise_for_status()
        events = resp.json()
        _news_cache["fetched_at"] = now
        _news_cache["events"] = events
        return events
    except (requests.RequestException, ValueError) as exc:
        print(f"  {DIM}[news] ดึงปฏิทินข่าวไม่ได้ — {exc}{RESET}")
        return _news_cache["events"]   # ใช้ cache เก่าถ้ามี ไม่งั้นคืน list ว่าง


def check_upcoming_news(hours_ahead: int = NEWS_LOOKAHEAD_H) -> tuple[bool, str, float]:
    """คืน (has_high_impact_news, รายละเอียด, ชั่วโมงที่เหลือ) เฉพาะข่าว USD + High impact ภายใน N ชม.ข้างหน้า"""
    events = fetch_news_calendar()
    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=hours_ahead)

    upcoming = []
    for ev in events:
        if ev.get("country") != NEWS_CURRENCY or ev.get("impact") != NEWS_IMPACT:
            continue
        try:
            ev_time = datetime.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        if now_utc <= ev_time <= horizon:
            upcoming.append((ev_time, ev.get("title", "Unknown")))

    if not upcoming:
        return False, "", None

    upcoming.sort(key=lambda x: x[0])
    ev_time, title = upcoming[0]
    hours_left = (ev_time - now_utc).total_seconds() / 3600
    detail = f"{title} ({NEWS_CURRENCY}, {NEWS_IMPACT}) ในอีก {hours_left:.1f} ชม."
    return True, detail, hours_left


def check_recent_news(entry_time: pd.Timestamp, hours_back: int = NEWS_POST_H) -> tuple[bool, str]:
    """คืน (มีข่าว High impact ที่ออก 'ระหว่างถือ position' และอยู่ในช่วง N ชม.ที่ผ่านมาไหม, รายละเอียด)"""
    events = fetch_news_calendar()
    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(hours=hours_back)

    entry_time_utc = entry_time.tz_localize("UTC") if entry_time.tzinfo is None else entry_time.tz_convert("UTC")

    recent = []
    for ev in events:
        if ev.get("country") != NEWS_CURRENCY or ev.get("impact") != NEWS_IMPACT:
            continue
        try:
            ev_time = datetime.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        # นับเฉพาะข่าวที่ออก "หลังเข้า position" เท่านั้น — ถ้าเข้าหลังข่าวออกไปแล้ว ไม่นับ
        if entry_time_utc <= ev_time and since <= ev_time <= now_utc:
            recent.append((ev_time, ev.get("title", "Unknown")))

    if not recent:
        return False, ""

    recent.sort(key=lambda x: x[0])
    ev_time, title = recent[-1]
    hours_ago = (now_utc - ev_time).total_seconds() / 3600
    detail = f"{title} ({NEWS_CURRENCY}, {NEWS_IMPACT}) ผ่านมาแล้ว {hours_ago:.1f} ชม."
    return True, detail


# ---------------------------------------------------------------------------
# วิเคราะห์ 1 position
# ---------------------------------------------------------------------------

def analyze_position(pos, as_of=None, ctx: dict = None) -> dict:
    """as_of=None (ปกติ) = วิเคราะห์ไม้ ณ ตอนนี้ด้วยข้อมูลสด

    as_of=datetime (backtest) = วิเคราะห์ราวกับว่าตอนนี้คือเวลานั้น — ทุกเฟรมราคาถูกตัดที่
    as_of ผ่าน bars.get_bars() และ "ราคาปัจจุบัน" ใช้ close ของแท่ง 1H ที่ปิดล่าสุดแทน live tick
    เพิ่ม 2026-08-27 เพื่อให้ backtest_replay.py จำลอง exit ด้วย logic ตัวจริงตัวนี้ ไม่ใช่เขียน
    SL/TP + partial แบบย่อขึ้นมาเอง ซึ่งขาด ATR trailing / structure break / climax /
    slow-trade / TP trailing ไปทั้งหมด

    ctx (คู่กับ as_of) = แทนที่การอ่าน journal ด้วยค่าที่ backtest ถืออยู่แล้ว:
      pinned_swing, pinned_atr_entry, strategy, original_lot, original_tp
    ไม้จำลองไม่มีอยู่ใน journal.csv จึงต้องส่งเข้ามาเอง

    ⚠️ News guard ถูกปิดอัตโนมัติเมื่อมี as_of — check_upcoming_news()/check_recent_news()
    ยิง ForexFactory แบบ real-time ไม่มีข้อมูลย้อนหลัง จึงจำลองไม่ได้ ผลคือ backtest จะไม่เคย
    ออกไม้เพราะข่าวเลย ต่างจากของจริง (คืน news_simulated=False ไว้ให้ผู้เรียกรู้ตัว)"""
    ctx = ctx or {}
    symbol     = pos.symbol
    direction  = "Long" if pos.type == mt5.POSITION_TYPE_BUY else "Short"
    entry      = pos.price_open
    sl         = pos.sl
    tp         = pos.tp
    lot        = pos.volume
    entry_time = pd.to_datetime(pos.time, unit="s")

    # ใช้ real volume (Bitstamp/COMEX) — swing filter และ VSA climax แตะ volume ทั้งคู่
    df = get_ohlcv_real(symbol, "4H", bars=BARS, as_of=as_of)

    atr_series = calc_atr(df, ATR_PERIOD)
    rsi_series = calc_rsi(df["close"], RSI_PERIOD)
    bb_upper, bb_mid, bb_lower = calc_bollinger(df["close"], BB_PERIOD, BB_STD)

    closed_idx = len(df) - 2   # แท่งปิดล่าสุด (แท่งสุดท้ายยังไม่ปิด)
    atr_now    = atr_series.iloc[closed_idx]
    rsi_now    = rsi_series.iloc[closed_idx]
    bb_up_now  = bb_upper.iloc[closed_idx]
    bb_lo_now  = bb_lower.iloc[closed_idx]
    climax, climax_pattern = is_climax_bar(df, closed_idx)

    if as_of is None:
        tick = get_tick_or_raise(symbol)
        current_price = tick.bid if direction == "Long" else tick.ask
    else:
        # ราคา "ตอนนี้" ในโหมด backtest = close ของแท่ง 1H ที่ปิดล่าสุด ณ as_of
        current_price = float(get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=2,
                                        as_of=as_of)["close"].iloc[-1])

    extreme = extreme_price_since_entry(df, entry_time, direction)

    # ── ATR Trailing SL ──
    #   initial_sl  = SL เริ่มต้นตามสูตร (ฐานตรึง ± ATR ตอนเข้า) → ใช้เป็นระยะ 1R คงที่
    #   trailing_sl = SL ใหม่รอบนี้ (ฐานตรึง ± ATR ล่าสุด)       → ใช้ขยับ SL จริง + วัดระยะห่าง
    if ctx:
        pinned_swing, pinned_atr_entry = ctx.get("pinned_swing"), ctx.get("pinned_atr_entry")
    else:
        pinned_swing, pinned_atr_entry = journal.get_pinned_anchor(pos.ticket)
    trail       = calc_atr_trailing_sl(df, symbol, entry_time, direction,
                                       pinned_swing=pinned_swing, pinned_atr_entry=pinned_atr_entry,
                                       as_of=as_of)
    initial_sl  = trail["initial_sl"] if trail else (sl if sl else None)
    trailing_sl = trail["new_sl"] if trail else None
    effective_sl = trailing_sl if trailing_sl is not None else (sl if sl else None)

    # ── 1) สถานะ trade — R-multiple อิง SL เริ่มต้นตามสูตร (ไม่ใช่ SL หลังขยับ) ──
    sl_range = abs(entry - initial_sl) if initial_sl else None
    if direction == "Long":
        pnl_pct     = (current_price - entry) / entry * 100
        r_multiple  = (current_price - entry) / sl_range if sl_range else None
        tp_progress = (current_price - entry) / (tp - entry) * 100 if tp else None
        dist_sl_pct = (current_price - effective_sl) / effective_sl * 100 if effective_sl else None
        dist_tp_pct = (tp - current_price) / tp * 100 if tp else None
    else:
        pnl_pct     = (entry - current_price) / entry * 100
        r_multiple  = (entry - current_price) / sl_range if sl_range else None
        tp_progress = (entry - current_price) / (entry - tp) * 100 if tp else None
        dist_sl_pct = (effective_sl - current_price) / effective_sl * 100 if effective_sl else None
        dist_tp_pct = (current_price - tp) / tp * 100 if tp else None

    now = datetime.now() if as_of is None else pd.Timestamp(as_of).to_pydatetime()
    time_held_days = (now - entry_time).total_seconds() / 86400

    # ── 2) รัน Exit Decision Checklist โดยใช้ r_multiple/time_held ข้างบน (ก่อนคิด Chandelier) ──
    strategy       = ctx.get("strategy") if ctx else journal.get_trade_strategy(pos.ticket)
    trend_info     = check_trend_invalidation(symbol, direction, entry_time, strategy, as_of=as_of)
    trend_keep_pct = trend_info["keep_pct"]
    trend_broken_full    = trend_keep_pct <= 0
    trend_broken_partial = 0 < trend_keep_pct < 100
    structure_broken = (check_structure_break(symbol, direction, as_of=as_of)
                        if STRUCTURE_BREAK_ENABLED else False)
    # News ย้อนหลังไม่ได้ (ForexFactory ให้เฉพาะปฏิทินปัจจุบัน) — โหมด backtest ถือว่าไม่มีข่าว
    if as_of is None:
        has_news, news_detail, news_hours_left = check_upcoming_news()
        has_recent_news, recent_news_detail = check_recent_news(entry_time)
    else:
        has_news, news_detail, news_hours_left = False, "", None
        has_recent_news, recent_news_detail = False, ""
    news_imminent = has_news and news_hours_left is not None and news_hours_left <= NEWS_IMMINENT_H
    post_news_no_profit = has_recent_news and pnl_pct <= 0
    ge1r             = r_multiple is not None and r_multiple >= 1.0
    slow_trade       = time_held_days >= SLOW_TRADE_DAYS and r_multiple is not None and r_multiple < SLOW_TRADE_R
    hold_cap         = time_held_days >= MAX_HOLD_DAYS

    invalidated = trend_broken_full or structure_broken or post_news_no_profit or hold_cap
    if trend_broken_full or structure_broken:
        final_decision = ("ออก 100% ทันที — Trend/Structure พัง", RED)
    elif post_news_no_profit:
        final_decision = ("ออก 100% ทันที — ข่าวสงบแล้วแต่ไม่กำไร", RED)
    elif trend_broken_partial:
        final_decision = (f"ออก {100 - trend_keep_pct}% — {trend_info['reason']}", YELLOW)
    elif slow_trade:
        final_decision = ("ออก 50% (Time exit) — รอ setup ใหม่", YELLOW)
    elif ge1r:
        final_decision = (f"ขยับ SL ไปจุด Entry (Breakeven) = {entry:,.3f}", YELLOW)
    else:
        final_decision = ("ถือต่อ — ยังไม่มี signal ให้ออก", GREEN)

    breakeven_str = f"{entry:,.3f}"
    checklist = [
        {"no": 1, "q": "Daily ปิดสวน trend ที่ใช้เข้า? (พ้น grace period แล้ว)",
         "answer": trend_broken_full or trend_broken_partial,
         "action": (f"ออก 100% = Invalidation (ปิดสวนติดกัน {trend_info['consec_break']} แท่ง)" if trend_broken_full else
                    f"ออก {100 - trend_keep_pct}% = เตือนภัย (ปิดสวนติดกัน {trend_info['consec_break']} แท่ง)" if trend_broken_partial else ""),
         "severity": "red" if trend_broken_full else "yellow",
         "note": trend_info["reason"]},
        {"no": 0, "q": f"ถือมาครบ {MAX_HOLD_DAYS:g} วันแล้ว?", "answer": hold_cap,
         "action": f"ออก 100% = ชนเพดานเวลา ({time_held_days:.0f} วัน)" if hold_cap else "",
         "severity": "red",
         "note": "ปิดโดยไม่สนว่า R เท่าไหร่ — ดู comment ที่ MAX_HOLD_DAYS"},
        {"no": 2, "q": "Structure ที่ใช้เข้าพังแล้ว?",                 "answer": structure_broken,
         "action": "ออก 100% = Structure broken" if structure_broken else "",
         "severity": "red",
         "note": "swing low/high ที่ใช้ตัดสินใจถูก break"},
        {"no": 3, "q": "มี major news/event เปลี่ยน fundamental?",    "answer": has_news,
         "action": f"พิจารณาออก/ลดขนาดก่อนข่าว — {news_detail}" if has_news else "",
         "severity": "yellow",
         "note": f"เช็คจาก ForexFactory ({NEWS_CURRENCY}, {NEWS_IMPACT} เท่านั้น, ล่วงหน้า {NEWS_LOOKAHEAD_H} ชม.)"},
        {"no": 4, "q": f"เข้ามา {SLOW_TRADE_DAYS} วันแล้ว ยังไม่ได้กำไร {SLOW_TRADE_R}R?", "answer": slow_trade,
         # 2026-09-13: เดิมเขียน "รอ setup ใหม่" ซึ่ง **ไม่ตรงกับที่โค้ดทำ** — ตัด 50% ไม่ได้
         # คืนช่องถือไม้ (ช่องว่างเมื่อไม้ปิดหมดเท่านั้น) ไม้ที่โดนตัดครึ่งยังครองช่องต่อไป
         # สิ่งที่คืนจริงคือโควตา config.MAX_PORTFOLIO_RISK_R ซึ่งเป็นผลข้าม symbol
         "action": "ออก 50% (Time exit) — ลดความเสี่ยงของไม้ที่ยังไม่ไปไหน" if slow_trade else "",
         "severity": "yellow",
         "note": f"เจตนาเดิม: ถ้า setup ดี ราคาควรวิ่งภายใน {SLOW_TRADE_DAYS} วัน — ⚠️ ขัดกับข้อมูลจริง "
                 f"(71% ของไม้ Scoring ถือเกิน 3 วัน) ดู comment ที่ SLOW_TRADE_DAYS"},
        {"no": 5, "q": "กำไร >= 1R แล้ว? (ระยะกำไร = ระยะ SL)",       "answer": ge1r,
         "action": f"ขยับ SL ไปจุด Entry (Breakeven) = {breakeven_str}" if ge1r else "",
         "severity": "yellow",
         "note": "ป้องกัน winner กลายเป็น loser — นี่คือขยับ SL ไม่ใช่การออก"},
        {"no": 6, "q": f"ข่าวสงบแล้ว ({NEWS_POST_H} ชม.) แต่ยังไม่กำไร?", "answer": post_news_no_profit,
         "action": "ออก 100% = ข่าวสงบแล้วแต่ไม่กำไร" if post_news_no_profit else "",
         "severity": "red",
         "note": f"เช็คว่ามีข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ออกภายใน {NEWS_POST_H} ชม.ที่ผ่านมาไหม — {recent_news_detail if has_recent_news else 'ไม่มีข่าวล่าสุด'}"},
    ]

    # ── 4) Final Decision แปลงเป็น % ฐาน — 0% หยุดคิดทันที (คูณอะไรก็ยังเป็น 0) ──
    if invalidated:
        base_keep_pct = 0
    elif trend_broken_partial:
        base_keep_pct = trend_keep_pct   # เตือนภัย ยังไม่ฟันธง 100% — ปิดกันไว้ก่อนตามระดับความต่อเนื่อง (75/50)
    elif slow_trade:
        base_keep_pct = SLOW_TRADE_KEEP
    else:
        base_keep_pct = 100   # ถือต่อ หรือ ขยับ SL breakeven → ยังไม่ฟันธง ไปคิดต่อขั้น Position Sizing

    # ── 5) Position Sizing Rules — คิดต่อเฉพาะกรณี base_keep_pct > 0 เท่านั้น ──
    if base_keep_pct == 0:
        position_rules   = []          # ข้าม — Final ฟันธงออก 100% แล้ว ไม่ต้องคิดต่อ
        stage_keep_pct   = None
    else:
        rule_1r_trigger = r_multiple is not None and r_multiple >= RULE_1R_TRIGGER
        if direction == "Long":
            hot_trigger = rsi_now >= RSI_OVERBOUGHT or current_price >= bb_up_now
        else:
            hot_trigger = rsi_now <= RSI_OVERSOLD or current_price <= bb_lo_now
        halfway_trigger = tp_progress is not None and tp_progress >= RULE_HALFWAY_TRIGGER

        # 2026-09-13: ประโยคเดิมตรงนี้ ("ทุกข้อที่เหลือมีผลเฉพาะช่วง R < 1 เพราะกฎ 1R ตรึง 50%")
        # **ไม่จริงแล้ว** ตั้งแต่ RULE_1R_KEEP = 100 — ในชุดนี้เหลือ Indicator ร้อน (75) กับ
        # ใกล้ข่าว (50) ที่ยังตัดจริง และยิงได้ทุกช่วง R โดยไม่มีกฎไหนครอบ (1R/เดินทาง/Climax
        # = 100 ทั้งสามแล้ว) — กฎ "ถึง 2R" กับ "ใกล้ TP" ที่เคยลบทิ้งจึงกลับมามีความหมายได้
        # แต่ตัดสินใจไม่เอากลับ ดู comment ที่ RULE_1R_KEEP ด้านบนไฟล์
        position_rules = [
            {"no": 1, "name": "ถึง 1R",               "trigger": rule_1r_trigger,       "keep_pct": RULE_1R_KEEP,
             "cond": "R-multiple >= 1.0 (ล็อกกำไรครึ่งหนึ่ง คู่กับ SL ที่ขยับไป breakeven)"},
            {"no": 2, "name": "Indicator ร้อน",        "trigger": hot_trigger,           "keep_pct": RULE_HOT_KEEP,
             "cond": "RSI ชนขอบ / ราคาชน Bollinger ฝั่งกำไร"},
            {"no": 3, "name": "เดินทาง >=50% ไป TP",   "trigger": halfway_trigger,       "keep_pct": RULE_HALFWAY_KEEP,
             "cond": "TP Progress >= 50%"},
            {"no": 4, "name": "แท่ง Climax",
             # r_multiple เป็น None ได้ถ้า sl_range = 0 — ถือว่า "ยังไม่กำไร" ไม่ให้กฎยิง
             "trigger": climax and ((r_multiple is not None and r_multiple > 0)
                                    if CLIMAX_ONLY_IN_PROFIT else True),
             "keep_pct": RULE_CLIMAX_KEEP,
             "cond": "volume สุดขั้ว + range ใหญ่"},
            {"no": 5, "name": "ใกล้ข่าว High Impact",  "trigger": news_imminent,          "keep_pct": NEWS_IMMINENT_KEEP,
             "cond": f"ข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ภายใน {NEWS_IMMINENT_H} ชม. — {news_detail if news_imminent else ''}"},
        ]
        triggered_keeps = [r["keep_pct"] for r in position_rules if r["trigger"]]
        stage_keep_pct  = min(triggered_keeps) if triggered_keeps else 100

    # ── 6) รวมเป็น % สุดท้าย — คูณต่อกันเป็นทอด (base% x stage%) ──
    if base_keep_pct == 0:
        recommended_keep_pct = 0
    else:
        recommended_keep_pct = round(base_keep_pct * stage_keep_pct / 100, 1)
    # 2026-08-15: คิดจาก lot ตอนเปิดไม้ ไม่ใช่ lot ปัจจุบัน — ให้ตรงกับที่ execute_decision
    # ทำจริง (บรรทัด target_lot = original_lot * keep_pct/100) เดิมจอคิดจาก lot ปัจจุบันคนละ
    # ฐานกับการทำงานจริง พอไม้ถูกปิดบางส่วนไปแล้วจะอ่านขัดกันเอง เช่นไม้เปิด 1.91 ปิดเหลือ
    # 1.43 (75%) แล้วกฎ 75% trigger ซ้ำ จอจะขึ้น "1.43 -> 1.073" เหมือนจะตัดอีกรอบ ทั้งที่
    # ของจริง target = 1.91x0.75 = 1.433 ซึ่งถือครบแล้ว ไม่ตัดอะไรเพิ่ม
    original_lot  = ctx.get("original_lot") if ctx else journal.get_original_lot(pos.ticket)
    lot_basis     = original_lot if original_lot is not None else lot
    remaining_lot = round(lot_basis * recommended_keep_pct / 100, 3)
    held_pct      = round(lot / lot_basis * 100, 1) if lot_basis else 100.0

    final_label, final_color = final_decision
    if base_keep_pct == 0:
        combined_decision = (final_label, final_color)
    elif recommended_keep_pct >= 100:
        combined_decision = (final_label, final_color)
    else:
        sell_str    = f"{100 - recommended_keep_pct:g}%"
        keep_str_c  = f"{recommended_keep_pct:g}%"
        combined_label = f"Final={base_keep_pct}% x Position Sizing={stage_keep_pct}% => ขาย {sell_str} (เหลือ {keep_str_c})"
        combined_color = RED if recommended_keep_pct <= 0 else YELLOW
        combined_decision = (combined_label, combined_color)

    # ── SL ที่ควรตั้งรอบนี้ — ใช้ trailing SL, ถ้ากำไร >= 1R แล้วห้ามถอยต่ำกว่า Breakeven ──
    sl_widen_capped = False
    if trailing_sl is not None:
        desired_sl = trailing_sl
        # เพดานการขยาย — ห้าม SL ห่างจาก entry เกิน MAX_SL_WIDEN_R เท่าของระยะ 1R เริ่มต้น
        # (ยังปล่อยให้หายใจตาม ATR ได้ตามปกติ ตัดเฉพาะเคส ATR ระเบิดจนความเสี่ยงจริงบานเกินแผน)
        if sl_range:
            widen_limit = sl_range * MAX_SL_WIDEN_R
            capped_sl   = (entry - widen_limit) if direction == "Long" else (entry + widen_limit)
            if (direction == "Long" and desired_sl < capped_sl) or \
               (direction == "Short" and desired_sl > capped_sl):
                desired_sl      = capped_sl
                sl_widen_capped = True

        # Ratchet กันขึ้นลงๆ (2026-08-07): ATR1H ผันผวนรายชั่วโมงทำให้ trailing SL แกว่งได้ทั้งสองทาง
        # (ATR ล่าสุดหด -> SL ขยับเข้าใกล้ entry กว่ารอบก่อน) ทิศทางที่ "ห้ามถอย" สลับด้านกันตาม
        # ว่า SL ปัจจุบันบน broker (pos.sl) ผ่าน breakeven (entry) มาแล้วหรือยัง:
        #   - ยังไม่ถึง breakeven (sl ยัง "แคบกว่า" entry ฝั่งเสี่ยง): ห้ามถอยเข้าใกล้ entry กว่า sl
        #     เดิม (ขยับออกห่าง entry เพิ่มได้เสมอ)
        #   - ผ่าน breakeven ไปแล้ว (sl ไปถึง/เลย entry แล้ว): สลับกติกาเป็นห้ามถอยกลับต่ำกว่า sl
        #     เดิม (ขยับเข้าใกล้ราคาปัจจุบันเพิ่ม/ล็อกกำไรเพิ่มได้เท่านั้น) — กัน ge1r ที่ไม่ sticky
        #     (คำนวณจากราคาสดทุกรอบ) หลุด False ตอนราคาย่อกลับต่ำกว่า 1R แล้วดึง SL ที่ล็อก
        #     breakeven ไปแล้วให้ถอยกลับต่ำกว่า entry ซ้ำ (บั๊กที่พบตอนจำลอง: 900 -> 1000(BE) -> 850)
        if sl:
            past_breakeven = (sl >= entry) if direction == "Long" else (sl <= entry)
            if direction == "Long":
                if past_breakeven:
                    desired_sl = max(desired_sl, sl)
                elif desired_sl > sl:
                    desired_sl = sl
            else:
                if past_breakeven:
                    desired_sl = min(desired_sl, sl)
                elif desired_sl < sl:
                    desired_sl = sl

        # Breakeven force เป็นข้อยกเว้นตั้งใจ (กำไร >= 1R) — ให้ขยับเข้าหา entry ได้ ทับ ratchet ด้านบน
        if ge1r:
            desired_sl = max(desired_sl, entry) if direction == "Long" else min(desired_sl, entry)
    else:
        desired_sl = entry if ge1r else None   # คำนวณสูตรไม่ได้ -> ใช้กติกาเดิม (breakeven เมื่อ >=1R)

    # ── TP ที่ควรตั้งรอบนี้ — เริ่ม trail เมื่อใกล้ TP เดิม <= TRAIL_TP_TRIGGER_PCT ──
    desired_tp = None
    if (not invalidated and dist_tp_pct is not None and dist_tp_pct <= TRAIL_TP_TRIGGER_PCT
            and trail is not None and tp):
        original_tp = ctx.get("original_tp") if ctx else journal.get_original_tp(pos.ticket)
        if original_tp is None:
            original_tp = tp   # ไม้เก่าก่อนมีฟีเจอร์นี้ — fallback ใช้ TP ปัจจุบันแทน
        atr_latest_1h = trail["atr_latest"]
        if direction == "Long":
            candidate_tp = original_tp - atr_latest_1h * TRAIL_TP_ATR_BUFFER
            desired_tp = min(candidate_tp, tp)   # ratchet ทางเดียว — แคบลงเรื่อยๆ เท่านั้น
        else:
            candidate_tp = original_tp + atr_latest_1h * TRAIL_TP_ATR_BUFFER
            desired_tp = max(candidate_tp, tp)

    return {
        "now": now, "time_held_days": time_held_days,
        "symbol": symbol, "ticket": pos.ticket, "direction": direction,
        "entry": entry, "sl": sl, "tp": tp, "lot": lot,
        "trail": trail, "initial_sl": initial_sl,
        "trailing_sl": trailing_sl, "desired_sl": desired_sl,
        "sl_widen_capped": sl_widen_capped,
        "desired_tp": desired_tp,
        "entry_time": entry_time, "current_price": current_price,
        "extreme": extreme, "atr": atr_now, "rsi": rsi_now,
        "bb_upper": bb_up_now, "bb_lower": bb_lo_now,
        "climax": climax, "climax_pattern": climax_pattern,
        "pnl_pct": pnl_pct, "r_multiple": r_multiple, "sl_range": sl_range,
        "tp_progress": tp_progress, "dist_sl_pct": dist_sl_pct,
        "dist_tp_pct": dist_tp_pct,
        "checklist": checklist, "final_decision": final_decision,
        "position_rules": position_rules,
        "base_keep_pct": base_keep_pct, "stage_keep_pct": stage_keep_pct,
        "recommended_keep_pct": recommended_keep_pct,
        "news_simulated": as_of is None,   # False = โหมด backtest, ไม่มีข่าวมาให้เช็ค
        "as_of": as_of,
        "remaining_lot": remaining_lot,
        "original_lot": original_lot,   # None = ไม่พบใน journal (ไม้เก่า/เปิดมือ) -> fallback ใช้ lot ปัจจุบัน
        "lot_basis": lot_basis,         # ฐานที่ใช้คิด % — ตัวเดียวกับที่ execute_decision ใช้
        "held_pct": held_pct,           # ตอนนี้ถืออยู่กี่ % ของไม้เดิม (<100 = เคยปิดบางส่วนไปแล้ว)
        "combined_decision": combined_decision,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_report(m: dict):
    dir_color = _g if m["direction"] == "Long" else _r
    print()
    print("=" * 62)
    print(_b(f"  {m['symbol']}  ticket #{m['ticket']}  ({dir_color(m['direction'])})"))
    print("=" * 62)
    entry_str = m["entry_time"].strftime("%d/%m/%Y %H:%M")
    now_str   = m["now"].strftime("%d/%m/%Y %H:%M")
    print(f"  Entry Date/Time   : {entry_str}")
    print(f"  Current Date/Time : {now_str}")
    held_str = f"{m['time_held_days']:.1f} days"
    print(f"  Time Held         : {_y(held_str)}")
    print("-" * 62)
    print(f"  Entry           : {m['entry']:,.3f}")
    print(f"  Lot             : {m['lot']}")
    print(f"  Broker SL / TP  : {m['sl']:,.3f}  /  {m['tp']:,.3f}")
    if m["trail"]:
        t = m["trail"]
        trailing_str = f"{m['trailing_sl']:,.3f}"
        print(f"  ATR Trailing SL : ฐานตรึง {t['pinned_base']:,.3f} "
              f"(Swing {t['swing']:,.3f} ± ATRเข้า {t['atr_entry']:,.3f})")
        print(f"                    SL เริ่มต้น(1R) {m['initial_sl']:,.3f}  ->  "
              f"SL รอบนี้ {_b(trailing_str)} (ATRล่าสุด {t['atr_latest']:,.3f})")
    if m["desired_sl"] is not None:
        desired_str = f"{m['desired_sl']:,.3f}"
        cap_note = ""
        if m.get("sl_widen_capped"):
            cap_note = _y(f"  <- ชนเพดานขยาย {MAX_SL_WIDEN_R}R (ATR โตจนจะเสี่ยงเกินแผน)")
        print(f"  SL ที่จะตั้ง     : {_c(desired_str)}{cap_note}")
    if m["desired_tp"] is not None:
        desired_tp_str = f"{m['desired_tp']:,.3f}"
        print(f"  TP ที่จะตั้ง (Trailing, ห่างเดิม <={TRAIL_TP_TRIGGER_PCT}%) : {_c(desired_tp_str)}")
    cp_str = f"{m['current_price']:,.3f}"
    print(f"  Current Price   : {_b(cp_str)}")
    print(f"  Extreme Price   : {m['extreme']:,.3f}  (สุดตั้งแต่เข้า)")
    print("-" * 62)
    print(f"  ATR({ATR_PERIOD})          : {m['atr']:,.3f}")
    print(f"  RSI({RSI_PERIOD})          : {m['rsi']:.1f}")
    print(f"  Bollinger Band  : Upper {m['bb_upper']:,.3f}  /  Lower {m['bb_lower']:,.3f}")
    climax_str = _r("YES — " + m["climax_pattern"]) if m["climax"] else _g("NO")
    print(f"  Climax?         : {climax_str}")
    print("-" * 62)

    if m["sl_range"] is not None:
        print(f"  Risk Distance (Entry->SL) : {m['sl_range']:,.3f}   (ระยะ 1R)")
    pnl_color = _g if m["pnl_pct"] >= 0 else _r
    pnl_str = f"{m['pnl_pct']:+.2f}%"
    print(f"  Current P/L     : {pnl_color(pnl_str)}")
    if m["r_multiple"] is not None:
        r_color = _g if m["r_multiple"] >= 0 else _r
        r_str = f"{m['r_multiple']:+.2f}R"
        print(f"  R-multiple      : {r_color(r_str)}")
    if m["tp_progress"] is not None:
        tpp_str = f"{m['tp_progress']:.1f}%"
        print(f"  TP Progress     : {_y(tpp_str)}")
    if m["dist_sl_pct"] is not None:
        print(f"  ระยะห่างจาก SL  : {m['dist_sl_pct']:+.2f}%")
    if m["dist_tp_pct"] is not None:
        print(f"  ระยะห่างจาก TP  : {m['dist_tp_pct']:+.2f}%")

    print("-" * 62)
    print(_b("  Exit Decision Checklist"))
    print("-" * 62)
    sev_color = {"red": RED, "yellow": YELLOW}
    for item in m["checklist"]:
        color = sev_color.get(item.get("severity"), YELLOW)
        if item["answer"] is None:
            ans_str = _c("N/A ")
        elif item["answer"]:
            ans_str = f"{color}YES {RESET}"
        else:
            ans_str = _g("NO  ")
        print(f"  {item['no']}. [{ans_str}] {item['q']}")
        if item["action"]:
            print(f"       -> {color}{item['action']}{RESET}")
        print(f"       {DIM}{item['note']}{RESET}")

    if m["base_keep_pct"] == 0:
        print("-" * 62)
        print(f"  {RED}Final Decision ฟันธงออก 100% แล้ว — ข้าม Position Sizing Rules (ไม่ต้องคิดต่อ){RESET}")
    else:
        print("-" * 62)
        print(_b("  Position Sizing Rules (ควรเหลือกี่ % ของ Lot)"))
        print("-" * 62)
        for r in m["position_rules"]:
            status  = _g("TRIGGER ✅") if r["trigger"] else f"{DIM}— ไม่เข้า{RESET}"
            keep_str = f"{r['keep_pct']}%"
            keep_color = RED if r["keep_pct"] <= 0 else (YELLOW if r["keep_pct"] < 100 else GREEN)
            print(f"  กฎ {r['no']} — {r['name']:<20} {status}   ควรเหลือ {keep_color}{keep_str}{RESET}")
            print(f"       {DIM}เงื่อนไข: {r['cond']}{RESET}")

    print("-" * 62)
    keep_pct   = m["recommended_keep_pct"]
    keep_color = RED if keep_pct <= 0 else (YELLOW if keep_pct < 100 else GREEN)
    keep_str = f"{keep_pct:g}%"
    lot_str  = f"{m['remaining_lot']}"
    base_str = f"{m['base_keep_pct']}%"
    stage_str = f"{m['stage_keep_pct']}%" if m["stage_keep_pct"] is not None else "-"
    print(f"  Final % (ฐาน) = {base_str}   x   Position Sizing % = {stage_str}")
    # แสดงทั้ง 3 ตัวเลขให้ครบ กัน "100%" อ่านแล้วเข้าใจผิดว่าไม้ยังเต็มจำนวนทั้งที่ปิดไปบางส่วนแล้ว
    # (ทุกอย่างคิดจาก lot ตอนเปิดไม้ = ฐานเดียวกับ execute_decision เป๊ะ)
    basis_note = "" if m["original_lot"] is not None else f" {DIM}(ไม่พบ lot เปิดใน journal — ใช้ lot ปัจจุบันแทน){RESET}"
    print(f"  {_b('สรุป: ควรเหลือ Position')} = {keep_color}{keep_str}{RESET}"
          f" ของ lot ตอนเปิด {m['lot_basis']} = {keep_color}{lot_str}{RESET}{basis_note}")
    held_color = GREEN if m["held_pct"] >= 100 else YELLOW
    print(f"  ตอนนี้ถืออยู่ {held_color}{m['lot']} ({m['held_pct']:g}% ของไม้เดิม){RESET}", end="")
    diff = round(m["lot"] - m["remaining_lot"], 3)
    info = mt5.symbol_info(m["symbol"])
    min_lot = info.volume_min if info else 0.01
    if diff >= min_lot:
        print(f" -> {YELLOW}ต้องปิดเพิ่ม {diff:g} lot{RESET}")
    else:
        print(f" -> {DIM}ถึงเป้าแล้ว ไม่ต้องทำอะไรเพิ่ม{RESET}")

    label, color = m["final_decision"]
    print("=" * 62)
    print(f"  {_b('FINAL DECISION')} : {color}{label}{RESET}")
    print("=" * 62)

    combo_label, combo_color = m["combined_decision"]
    print(f"  {_b('>>> ACTION จริงที่ควรทำ')} : {combo_color}{combo_label}{RESET}")
    print(f"  {DIM}(กติกา: Final=0% หยุดคิดทันที | Final=50%/100% คิดต่อด้วย Position Sizing แล้วคูณกันเป็นทอด){RESET}")
    print("=" * 62)


# ---------------------------------------------------------------------------
# Auto Execute — สั่งจริงตามผลลัพธ์ (เฉพาะบัญชี DEMO เท่านั้น)
# ---------------------------------------------------------------------------

def execute_decision(m: dict) -> None:
    if not AUTO_EXECUTE:
        return

    if not is_demo_account():
        print(f"  {RED}[AUTO] ปฏิเสธ — บัญชีนี้ไม่ใช่ DEMO ห้ามสั่งเทรดอัตโนมัติ{RESET}")
        return

    ticket  = m["ticket"]
    symbol  = m["symbol"]
    lot     = m["lot"]
    entry   = m["entry"]
    sl      = m["sl"]
    tp      = m["tp"]
    keep_pct = m["recommended_keep_pct"]

    try:
        # 1) ฟันธงออก 100% (Trend/Structure พัง) — ปิดเต็มจำนวน
        if keep_pct <= 0:
            # ส่งชื่อกฎไปด้วยเพื่อให้ journal แยกออกว่าเป็นบอทปิดตามกฎ ไม่ใช่คนกดปิด
            # (2026-09-12 — เดิมทิ้งข้อมูลนี้ทั้งที่รู้อยู่แล้วตรงนี้ ดู order.close_order)
            close_order(ticket, exit_rule=m["final_decision"][0])
            print(f"  {RED}[AUTO] ปิด #{ticket} เต็มจำนวน (Final Decision = ออก 100%){RESET}")
            return

        # 2) ปิดบางส่วนตาม Position Sizing % (เช่น 50%, 60%, 75%)
        #    คิดเป็น "เป้าหมายว่าควรเหลือกี่ lot เทียบกับ lot ตอนเปิดไม้" แล้วปิดเฉพาะส่วนเกิน
        #    2026-08-02: เดิมคิด close_vol = lot ปัจจุบัน x (100-keep)% ซึ่งสั่งซ้ำไม่ได้ —
        #    ทุกชั่วโมงที่ rule ยัง trigger ค้าง (เช่น TP progress ยืนเหนือ 50%) จะปิดทบไปเรื่อยๆ
        #    1.0 -> 0.6 -> 0.36 -> 0.216 ... จนเหลือ min lot ทั้งที่ควรปิดครั้งเดียว
        #    แบบใหม่เป็น idempotent: พอถึงเป้าแล้วรอบต่อไปจะไม่ปิดอีก และถ้ามีกฎที่เข้มกว่า
        #    trigger เพิ่มทีหลัง (keep ลดลง) ก็ปิดเฉพาะส่วนต่างที่เพิ่มขึ้นเท่านั้น
        if keep_pct < 100:
            original_lot = journal.get_original_lot(ticket)
            if original_lot is None:
                print(f"  {YELLOW}[AUTO] ข้ามปิดบางส่วน #{ticket} — ไม่พบ lot ตอนเปิดไม้ใน journal "
                      f"(คิด % จาก lot ปัจจุบันจะปิดทบซ้ำ){RESET}")
            else:
                target_lot = clamp_lot(symbol, round(original_lot * keep_pct / 100, 3))
                excess     = round(lot - target_lot, 3)
                info       = mt5.symbol_info(symbol)
                min_lot    = info.volume_min if info else 0.01
                if excess >= min_lot:
                    close_vol = clamp_lot(symbol, excess)
                    if 0 < close_vol < lot:
                        partial_close_order(ticket, close_vol, m=m)
                        print(f"  {YELLOW}[AUTO] ปิดบางส่วน #{ticket} {close_vol} lot "
                              f"(เหลือ {keep_pct}% ของ {original_lot} = {target_lot}){RESET}")

        # 3) ขยับ SL ตาม ATR Trailing (ถ้า >=1R แล้ว desired_sl ถูก clamp ไม่ต่ำกว่า breakeven)
        desired_sl = m["desired_sl"]
        if desired_sl is not None and (not sl or abs(desired_sl - sl) >= 1e-3):
            modify_sltp(ticket, new_sl=desired_sl)
            print(f"  {CYAN}[AUTO] ขยับ SL #{ticket} -> {desired_sl:,.3f} (ATR Trailing){RESET}")

        # 4) ขยับ TP ตาม TP Trailing (เริ่มเมื่อใกล้ TP เดิม <= TRAIL_TP_TRIGGER_PCT)
        desired_tp = m["desired_tp"]
        if desired_tp is not None and (not tp or abs(desired_tp - tp) >= 1e-3):
            modify_sltp(ticket, new_tp=desired_tp)
            print(f"  {CYAN}[AUTO] ขยับ TP #{ticket} -> {desired_tp:,.3f} (TP Trailing){RESET}")

    except Exception as exc:
        print(f"  {RED}[AUTO] ERROR ticket #{ticket} — {exc}{RESET}")
        log.error(f"[AUTO] execute_decision ERROR ticket #{ticket}", exc_info=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_once():
    connect()
    try:
        # ปิดบัญชีค้างก่อน — ไม้ที่ชน SL/TP ไปแล้วต้องถูก mark ปิดใน journal ไม่งั้นค้าง 'Open'
        # ตลอดไป (กระทบ check_daily_loss + get_statistics — ดู journal.reconcile_closed_positions)
        try:
            journal.reconcile_closed_positions()
        except Exception as exc:
            print(_r(f"  [journal] reconcile ล้มเหลว — {exc}"))
            log.error("reconcile_closed_positions ERROR", exc_info=True)

        positions = mt5.positions_get()
        if not positions:
            print(_y("ไม่มี Open Position อยู่ตอนนี้"))
            return
        for pos in positions:
            try:
                m = analyze_position(pos)
                print_report(m)
                execute_decision(m)
            except Exception as exc:
                print(_r(f"  [{pos.symbol} #{pos.ticket}] ERROR — {exc}"))
                log.error(f"[{pos.symbol} #{pos.ticket}] analyze_position ERROR", exc_info=True)
    finally:
        mt5.shutdown()


def run_monitor():
    load_dotenv()
    print("=" * 62)
    print(_b("  EXIT MONITOR  (กด Ctrl+C เพื่อหยุด)"))
    print(f"  Timeframe : 1H   Interval : {INTERVAL_SECONDS // 60} นาที")
    print("=" * 62)

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'#' * 62}")
        print(f"  รอบใหม่  {now}")
        print(f"{'#' * 62}")

        try:
            scan_once()
        except Exception as exc:
            print(_r(f"[ERROR] {exc}"))
            log.error("run_monitor scan_once ERROR", exc_info=True)
            notify.notify_error("exit_monitor — scan_once", str(exc))

        next_run = datetime.fromtimestamp(time.time() + INTERVAL_SECONDS).strftime("%H:%M:%S")
        print(f"\n  รอบถัดไป : {next_run}")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    load_dotenv()
    if "--once" in sys.argv:
        scan_once()
    else:
        try:
            run_monitor()
        except KeyboardInterrupt:
            print("\n\n  หยุด Exit Monitor แล้ว")
