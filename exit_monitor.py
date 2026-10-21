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

from config import MT5_TIMEFRAMES, get_asset_class
import config as _cfg
from mt5_connect import connect, get_tick_or_raise, is_demo_account, mt5_now
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
# MAX_HOLD_DAYS — เพดานเวลาถือ 30 วันปฏิทิน ครบแล้วปิด 100% (ไม้ Sideway ใช้ config.SIDEWAY_MAX_HOLD_H แทน)
#   วัดซ้ำ 2026-09-24 / 10-06: ปิดเพดาน ≈ 0 · ลดเป็น 20/15/10 วัน −8.01/−12.32/−19.66R = **ห้ามลด** (แม้จะเปิดช่องให้ Sideway)
#   คงไว้เพราะจำกัดเวลาที่เงินถูกล็อก · backtest_replay / backtest_trade_sim อ่านค่านี้ตรงๆ · --max-hold=N ทับเฉพาะรอบทดลอง
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — MAX_HOLD_DAYS / slow trade (2026-09-13..24)"
MAX_HOLD_DAYS         = 30

# 🔴 slow trade (ถือครบ SLOW_TRADE_DAYS วันทำการแล้ว R < SLOW_TRADE_R -> เหลือ SLOW_TRADE_KEEP%) ปิดตั้งแต่ 2026-09-23 (ผู้ใช้)
#   replay +12.63R · direct |t| 2.15 · ตัด 5 ใหญ่ยัง +4.51 · 7/8 = ผ่านเกณฑ์ครบ 3 ข้อ · ตั้ง True = กฎกลับมา (ธง --slow-* ของ replay)
#   ผลข้างเคียง: ระบบไม่เหลือการปิดบางส่วนเลย — ทุกไม้ all-or-nothing
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — SLOW_TRADE_ENABLED (ปิด 2026-09-23)"
SLOW_TRADE_ENABLED    = False

SLOW_TRADE_DAYS       = 3
# หน่วย = วันทำการตั้งแต่ 2026-09-22 (symbol ที่ไม่ใช่ CRYPTO หักเสาร์-อาทิตย์ · market_days_held)
# SLOW_TRADE_R 0.0 ตั้งแต่ 2026-09-14 = ตัดเฉพาะไม้ที่ติดลบจริง (เดิม 0.5 ยิงใส่ไม้ที่กำไรแต่ช้าด้วย)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — SLOW_TRADE_DAYS / SLOW_TRADE_R (2026-09-14..22)"
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
                              # (ยังใช้อยู่ 2 ที่: นิยาม news_imminent ด้านล่าง + ด่านห้าม
                              #  "เข้า" ไม้ใหม่ของ scheduler.py 4b ซึ่งเป็นคนละกฎ ไม่ได้ปิด)
# 🔴 กฎข่าว B (ใกล้ข่าว High USD ภายใน NEWS_IMMINENT_H -> เหลือ 50%) ปิดตั้งแต่ 2026-09-19 (ผู้ใช้) · 100 = no-op แต่ checklist ยังรายงาน
#   news_sensitivity −4.57R · ไม่ได้กันอะไร (gap > 0.25R ระหว่างตลาดเปิด 0 ครั้งใน 21,374 แท่ง-ชม.)
#   เหตุผลเดียวที่จะเอากลับ = เจอไม้จริงที่ SL ฟิลแย่กว่าราคา SL ชัดๆ ช่วงข่าว · backtest ไม่เคยจำลองกฎข่าว
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — NEWS_IMMINENT_KEEP กฎข่าว B (ปิด 2026-09-19)"
NEWS_IMMINENT_KEEP = 100      # 100 = ปิดกฎ · ค่าเดิม 50 (ดูบล็อกข้างบน)

NEWS_POST_H       = 6         # ข่าวผ่านไปกี่ชั่วโมงถือว่า "สงบ" แล้ว

# 🔴 กฎข่าว A (ข่าวผ่าน NEWS_POST_H ชม. แล้วยังไม่กำไร -> ออก 100%) ปิดตั้งแต่ 2026-09-23 (ผู้ใช้) · False = ยังรายงานแต่ไม่ปิดไม้
#   news_sensitivity −4.33R · คัดถูกทาง (dead:good 3.1) แต่ปิดกลางทางจึงแพ้ = เกณฑ์ 3e ใช้กับกฎ exit ไม่ได้
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — NEWS_POST_EXIT_ENABLED กฎข่าว A (ปิด 2026-09-23)"
NEWS_POST_EXIT_ENABLED = False

# Position Sizing Rules — "ถ้า trigger ควรเหลือกี่ %" · 🔴 **ทั้งชุดปิดอยู่** (KEEP = 100 ทุกข้อ = no-op · ยังคำนวณ/แสดงผล)
#   ถึง 1R + เดินทาง ≥50% ไป TP ปิด 2026-09-13 (+4.92R · 6/7 · ไม้ชุดเดิมทุกไม้) · กฎ 2R / ใกล้ TP 0.5% ถูกลบไปตอนนั้น (ไม่เอาคืน)
#   RULE_1R_TRIGGER ยัง 1.0 แต่ BE ย้ายไป 1.5R แล้ว (ไม่คู่กันแล้ว)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — Position Sizing Rules / RULE_1R / halfway (2026-08-27..09-22)"
RULE_1R_TRIGGER      = 1.0
RULE_1R_KEEP         = 100   # เดิม 50 — ดูบล็อกด้านบน

# BREAKEVEN_ENABLED — close 1H เหนือ BREAKEVEN_TRIGGER_R -> ย้าย SL ไปจุดล็อก (entry + BREAKEVEN_LEVEL_R) ถาวร (ratchet)
#   = กลไกเดียวทั้งระบบที่ดึง SL ให้แคบลง (ATR trailing ตรึงฐานที่ swing ตอนเข้า ขึ้นไม่ถึง entry โดยโครงสร้าง)
#   ปิดตัวนี้ = ไม้ที่เคยจบ BE บางส่วนกลายเป็น −1R เต็ม
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — BREAKEVEN_ENABLED (2026-09-15)"
BREAKEVEN_ENABLED    = True

# BREAKEVEN_TRIGGER_R — 1.0 -> 1.5 ตั้งแต่ 2026-09-22 (ผู้ใช้ · ตกเกณฑ์ 2/3) · ตรวจซ้ำ 09-23 ยังเป็นยอด (1.0 −7.22 · 1.25 −4.33 · 2.0 −0.76)
#   แกน BREAKEVEN_LEVEL_R ปิดจบทั้งสองทิศ: ล็อกเหนือ entry หนีบหางขวา (+0.2R ที่ 1.5R = −11.80R ไม้ชุดเดียวกัน) · ต่ำกว่า entry ก็แพ้
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — BREAKEVEN_TRIGGER_R / แกน BREAKEVEN_LEVEL_R (2026-09-22..23)"
BREAKEVEN_TRIGGER_R  = 1.5

# จุดที่ SL ไปนั่งตอนกฎยิง — หน่วยเป็นเท่าของระยะ 1R นับจาก entry (ค่าลบ = ต่ำกว่า entry ฝั่งเสี่ยง
# เช่น -0.2 = ยอมเสีย 0.2R แทนที่จะเสมอตัว)  0.0 = ล็อกที่ entry เป๊ะ = พฤติกรรมเดิม
# 2026-09-15: เพิ่มพร้อม BREAKEVEN_ENABLED — ที่มาคือผลของการ "ปิดทั้งกฎ" ซึ่งได้ +4.96R แต่
# ไม่เอาเข้าระบบเพราะกระจุกที่ไม้ 5 ไม้ (ตัดออกเหลือ −1.75R) และบวกแค่ 4/7 symbol
# สิ่งที่ข้อมูลรอบนั้นชี้: ไม้ที่แย่ลง 23 ไม้เกือบทั้งหมดกลายเป็น SL เต็ม (−0.57R เฉลี่ย) ทั้งที่
# กฎเดิมให้ 0 — ตัวที่ฆ่าไม้ชนะไม่ใช่ "การล็อก" แต่คือ **การวาง SL ตรง entry เป๊ะ ซึ่งเป็นจุดที่
# ราคามักย่อกลับมาแตะ** แกนนี้จึงเก็บการป้องกันไว้แต่ขยับจุดล็อกออกจากจุดนั้น
BREAKEVEN_LEVEL_R    = 0.0

# BREAKEVEN_ATR_FLOAT — BE แบบจุดล็อกลอยตาม ATR (ไอเดียผู้ใช้ 2026-10-16 · 0 = ปิด = พฤติกรรมเดิมเป๊ะ)
# ถึง BREAKEVEN_TRIGGER_R -> SL = entry ∓ k × ATR1H ล่าสุด (แทน entry เป๊ะ) แล้วคำนวณใหม่ทุกรอบ **ถอยหลังได้**
# (ไม่มี ratchet ในช่วงนี้) จนกว่าจะถึงขั้นบันได (BREAKEVEN_LADDER) ซึ่งล็อกเหนือ entry แล้วกลับไปใช้ ratchet เดิม
# รู้ว่า "เคยถึง BE แล้ว" จาก SL บน broker: ก่อน BE ratchet ยอมให้ SL ห่างออกเท่านั้น -> SL ที่แคบกว่า initial_sl
# แปลว่าเคยยิงแล้ว (ไม่ต้องจำ state ข้ามรอบ) · ไม่ต่ำกว่า initial_sl เสมอ · ไม้ Sideway ไม่มี ATR -> ใช้ BE เดิม
# ที่มา: ไม้จริง HK50 2026-10-14 จบ BE 0 แล้วราคาไปต่อ · ผลวัดอยู่ที่ backtest_replay --be-atr-float
# 🔻 ผล 2026-10-16 (K = 1 · ผสมกับ SL ขั้นต่ำดัชนี 0.5% · เทียบ 444 ไม้ +230.77R · ไม่เอา ตามคำสั่งผู้ใช้):
#   2024-26 436 ไม้ +227.02R (−3.75) · ไม้ชุดเดียวกัน: จบ BE -> โดน SL 30 ไม้ −6.90R (−0.25/ไม้) vs จบ BE -> TP 1 ไม้ +1.89R
#   = ไม้ที่ย้อนกลับถึง entry หลังเคยถึง +1.5R ส่วนใหญ่ย้อนต่อ ที่หายใจ 1 ATR แค่เปลี่ยน 0R เป็นขาดทุนเล็กน้อย
BREAKEVEN_ATR_FLOAT  = 0.0

# BREAKEVEN_LADDER — ขั้นบันไดต่อจาก BE: ((trigger_R, level_R), ...) ถึง trigger เมื่อไหร่ จุดล็อก
# ขยับขึ้นเป็น level (เอาขั้นสูงสุดที่ถึง · ratchet `past_breakeven` ด้านล่างกัน SL ถอยกลับเอง)
# นับ "ถึง" ด้วย r_multiple ของรอบนั้น (ราคาปัจจุบัน) แบบเดียวกับ BREAKEVEN_TRIGGER_R ไม่ใช่ extreme
# 2026-09-25 เพิ่มเพื่อวัดไอเดียผู้ใช้ "1.5R -> ล็อก +0.2R · 3R -> ล็อก +0.4R"
# ⚠️ ขั้นแรกของไอเดียนั้น (level +0.2 ที่ trigger 1.5) วัดแล้ว 2026-09-22 = −11.80R ชุดไม้เดียวกันเป๊ะ
#    (ดู BREAKEVEN_LEVEL_R ด้านบน) — ขั้นที่ 3R ไม่เคยวัด
# () = ไม่มีขั้นเพิ่ม = พฤติกรรมเดิมเป๊ะ · backtest: --be-ladder=3:0.4[,5:1.0] · 99:0 = identity
# 🔻 สถานะปัจจุบัน: **((3.0, 1.5),) ตั้งแต่ 2026-10-07 (คำสั่งผู้ใช้)** — ถึง 3R -> ล็อก SL ที่ +1.5R
# replay เต็ม 10 symbol (--end=2026-09-24T00:00 · --be-ladder=3:1.5) 435 -> 437 ไม้ +163.70 -> **+165.48R (+1.79R)**
#   เปลี่ยนแค่ 3 ไม้: XAU 2026-08-11 BE -> +1.50 · UKOIL 2025-10-07 BE -> +1.48 · HK50 2025-08-04 เพดาน +3.39 -> +1.48
#   + Sideway ใหม่ 2 ไม้ +0.70 (ช่อง HK50 ว่างเร็วขึ้น) · **ไม้ TP ที่โดนตัด 0** · |t| 0.32 = noise · ตัด 5 ไม้ใหญ่เหลือ 0
#   => "ประกันราคาถูก" ไม่ใช่ตัวเพิ่มกำไร — ไม่ได้ดีขึ้นอย่างมีนัย แต่ก็ไม่แย่ลง (เกณฑ์ที่ล็อกไว้ก่อนรันผ่านครบ 3 ข้อ)
# ทำไมต้องเริ่มที่ 3R (คัดกรองบนเส้นทาง 1H จริง 2026-10-07 · ชุดไม้คงที่ 247 ไม้):
#   ล็อกที่ 2-2.5R ทุกระดับ −5.1 ถึง −13.4R · trailing ระยะ R −3.2 ถึง −16.9R — ไม้ที่เคยถึง 2R เป็น TP 44 vs BE 9
#   และไม้ TP 1/4 ย่อจากจุดสูงสุดเกิน 1R ระหว่างทาง · ตัด TP ผิด 1 ไม้เสีย 2-3R ส่วนช่วย BE ได้ 0.5-1R
#   ที่ 3R ขึ้นไปแทบไม่เหลือไม้ TP ที่ย่อลึกขนาดนั้น — ขั้นสูงกว่านี้/ต่ำกว่านี้อย่าเพิ่ม (ดู memory mfe-leak)
BREAKEVEN_LADDER     = ((3.0, 1.5),)

# ข้อความนำหน้าของ final_decision ตอนกฎ BE ยิง — **มีคนอ่านสตริงนี้จริง**
# backtest_portfolio.py ใช้มันดูจากคอลัมน์ final ของ replay_cuts_*.csv ว่าไม้นั้นล็อกความเสี่ยง
# เป็นศูนย์ไปแล้วหรือยัง ถ้าแก้ข้อความแล้วไม่แก้ที่นั่น มันจะเลิกเจอเงียบๆ แล้วนับความเสี่ยงเกินจริง
# (เกิดมาแล้วตอน rename รอบนี้) — ตรึงไว้เป็นค่าคงที่ให้มีแหล่งเดียว อย่า hardcode ซ้ำที่อื่น
BE_DECISION_PREFIX   = "ขยับ SL ไปจุดล็อก"
RSI_OVERBOUGHT       = 70
RSI_OVERSOLD         = 30
# RULE_HOT_KEEP (Indicator ร้อน: RSI สุดขั้ว / ทะลุ BB -> เหลือ 75%) ปิดตั้งแต่ 2026-09-20 (ผู้ใช้) · replay +6.33R |t| 1.85 · 6/8 · ไม่มี churn
#   ถอยกลับ = 75 · ถ้าจะเพิ่มกฎปิดบางส่วนใหม่ ต้องรู้ว่าแนวคิดนี้แพ้มาแล้ว 3 ครั้ง (1R/halfway · Climax · ร้อน)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — RULE_HOT_KEEP (ปิด 2026-09-20)"
RULE_HOT_KEEP        = 100   # เดิม 75 — ดูบล็อกด้านบน
RULE_HALFWAY_TRIGGER = 50    # TP progress %
RULE_HALFWAY_KEEP    = 100   # เดิม 60 — ดูบล็อกด้านบน
# RULE_CLIMAX_KEEP (แท่ง Climax -> เหลือ 50%) ปิดตั้งแต่ 2026-09-10 · replay +10.48R 7/7 · ยิงตอนไม้ยังติดลบ (R เฉลี่ย −0.06)
#   ยิงเฉพาะตอนกำไร (CLIMAX_ONLY_IN_PROFIT) ก็ยังแพ้การปิดทิ้ง
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — RULE_CLIMAX_KEEP (ปิด 2026-09-10)"
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

# ---------------------------------------------------------------------------
# Trailing ที่เกาะจุดสูงสุด/ต่ำสุดหลังเข้าไม้ (2026-09-17) — default = ปิด = พฤติกรรมเดิมเป๊ะ
# ---------------------------------------------------------------------------
# ปิดอยู่ · SL = จุดสูงสุด/ต่ำสุดหลังเข้า ∓ MULT × ATR1H · วัดแล้วแพ้ −14.95R (TP 56 -> 23 ไม้ · หนีบไม้ที่ย่อระหว่างทางไป TP)
# ธง backtest: --trail-extreme[=MULT]
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — TRAIL_FROM_EXTREME (2026-09-17)"
TRAIL_FROM_EXTREME     = False
TRAIL_EXTREME_ATR_MULT = 2.0

# ---------------------------------------------------------------------------
# ล็อกกำไรตามระยะทางที่เดินไปหา TP (2026-09-18) — default = ปิด = พฤติกรรมเดิมเป๊ะ
# ---------------------------------------------------------------------------
# ปิดอยู่ · หลัง BE ล็อก SL = entry + KEEP × progress × (TP − entry) · วัดแล้วแพ้ทุก KEEP (0.25 −0.88 · 0.5 −1.72 · 0.75 −4.26)
#   ไม้ที่เสียคือไม้ที่ย่อระหว่างทางแล้วไปถึง TP จริง — กลไกเดียวกับ TRAIL_FROM_EXTREME
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — TP_PROGRESS_LOCK (2026-09-18)"
TP_PROGRESS_LOCK       = False
TP_PROGRESS_LOCK_KEEP  = 0.5

# MAX_SL_WIDEN_R — SL ที่ ATR trailing ขยายออกได้ไม่เกินกี่เท่าของ 1R (2026-08-02) · 1.5 -> 1.2 ตั้งแต่ 2026-09-15
#   ไม่เคยยิงเลย (ขยายมากสุด 1.107 เท่า) = นโยบายความเสี่ยง (ขาดทุนไม้เดียวไม่เกิน ~2.4%) · **ห้ามลดถึง 1.0** (−0.53R ตัดไม้ที่หายใจแล้วรอด)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — MAX_SL_WIDEN_R (2026-08-02..09-15)"
MAX_SL_WIDEN_R   = 1.2   # SL ห่างจาก entry ได้ไม่เกินกี่เท่าของระยะ 1R เริ่มต้น

# TP trailing (2026-07-23) — ราคาห่าง TP เดิมไม่เกิน TRAIL_TP_TRIGGER_PCT % -> ดึง TP เข้า ATR1H × TRAIL_TP_ATR_BUFFER
#   คิดจาก TP เดิม (คงที่) + ATR สด · ratchet ทางเดียวเทียบ TP ปัจจุบัน · 0 = ปิดกฎ (analyze_position คืน desired_tp = None)
# 🔻 0.25 ตั้งแต่ 2026-09-27 (ผู้ใช้ · เดิม 0.5) · กวาด replay: 0.25-1.0 ที่ราบ · ≥ 1.5 แย่ชัด (2.0 −11.28R) · ปิดกฎ −2.67R
#   ⚠️ replay ตั้ง TP ใหม่แม้อยู่ผิดฝั่งราคา (broker ปฏิเสธ) — buffer ≥ 1.0 ต้องแก้ replay ก่อนเชื่อ · TRIGGER 1% คัดกรองแล้วไม่ต้องจูน
#   ⚠️ FX ราคา ~1 ต้องพึ่ง _price_moved (ก่อน 2026-09-28 คำสั่งดึง TP ของ EUR ไม่เคยถึงโบรก)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — TRAIL_TP_ATR_BUFFER / TP trailing (2026-07-23..09-28)"
TRAIL_TP_TRIGGER_PCT = 1.0   # เริ่ม trail เมื่อห่างจาก TP เดิมไม่เกิน 1%
TRAIL_TP_ATR_BUFFER  = 0.25  # ATR(1H) × ค่านี้ · 0 = ปิดกฎ · เดิม 0.5 (เปลี่ยน 2026-09-27)

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

    # ── Trailing เกาะจุดสูงสุด/ต่ำสุดหลังเข้าไม้ (ดู TRAIL_FROM_EXTREME) ──
    # ใช้เฉพาะแท่ง 1H ที่ **ปิดแล้ว** (ตัดแท่งท้ายที่ยังฟอร์มอยู่ ตัวเดียวกับที่ atr_latest ใช้)
    extreme_sl = None
    if TRAIL_FROM_EXTREME:
        closed_1h = df_1h.iloc[:len(df_1h) - 1]
        seg = closed_1h[closed_1h["time"] >= entry_time]
        # ต้องมีแท่งแรกของไม้อยู่ในหน้าต่างจริง ไม่งั้นจุดสูงสุดจะไม่ครบ -> ปล่อยให้ fallback
        if len(seg) and closed_1h["time"].iloc[0] <= entry_time:
            ext = seg["high"].max() if direction == "Long" else seg["low"].min()
            extreme_sl = (ext - TRAIL_EXTREME_ATR_MULT * atr_latest) if direction == "Long" \
                         else (ext + TRAIL_EXTREME_ATR_MULT * atr_latest)

    if direction == "Long":
        pinned = swing - atr_entry
        return {"swing": swing, "atr_entry": atr_entry, "atr_latest": atr_latest,
                "pinned_base": pinned,
                "initial_sl": pinned - atr_entry,
                "new_sl":     extreme_sl if extreme_sl is not None else pinned - atr_latest}

    pinned = swing + atr_entry
    return {"swing": swing, "atr_entry": atr_entry, "atr_latest": atr_latest,
            "pinned_base": pinned,
            "initial_sl": pinned + atr_entry,
            "new_sl":     extreme_sl if extreme_sl is not None else pinned + atr_latest}


# ---------------------------------------------------------------------------
# ข้อ 1 — Daily ปิดสวน trend ที่ใช้เข้า? (EMA50 บน 1D เท่านั้น — 2026-08-07 ตัด 4H ออก)
# ---------------------------------------------------------------------------
# 🔻 สถานะปัจจุบัน: **ปิดอยู่** (TREND_CHECK_KEEP_BY_CONSEC = 100 ทุกระดับ ตั้งแต่ 2026-09-13)
#    ไม่ถูกพิมพ์ในรายงาน · ทุกอย่างใต้บรรทัดนี้คือ**ดีไซน์เดิมตอนกฎยังเปิด** อ่านเป็นประวัติ
#    เช็คสถานะจริงเสมอด้วย `exit_monitor.py --rules` อย่าอนุมานจาก comment
#   (กฎเดิม: ปิด 1D สวน EMA50 ติดกัน 1/2/3 แท่งหลัง grace -> ออก 25/50/100% · ไม้ Reversal ข้าม)
#   ปิดทั้งชุด 2026-09-13 (ผู้ใช้) · +0.62R จากไม้ 8 ไม้ (หลักฐานบาง) · ขาที่ยิงจริงคือ 3 แท่ง · ถอยกลับ = {1: 75, 2: 50, 3: 0}
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — TREND_CHECK (ปิด 2026-09-13)"
TREND_CHECK_GRACE_BARS = 5
# ปิดอยู่ — ค่าเดิมก่อน 2026-09-13 คือ {1: 75, 2: 50, 3: 0} (ดูบล็อกด้านบน)
TREND_CHECK_KEEP_BY_CONSEC = {1: 100, 2: 100, 3: 100}   # แท่งปิดสวนติดกัน -> % lot ที่ควรเหลือ
TREND_CHECK_MAX_CONSEC     = max(TREND_CHECK_KEEP_BY_CONSEC)
# ทุกระดับ = 100 แปลว่ากฎไม่มีทางตัดอะไร -> ไม่ต้องพิมพ์ในรายงาน (คำนวณต่อเหมือนเดิม
# เพื่อให้ trend_info['reason'] ยังใช้ได้ถ้าเปิดกลับ) ตั้งค่าใดค่าหนึ่ง < 100 กฎโผล่กลับมาเอง
TREND_CHECK_ENABLED = any(v < 100 for v in TREND_CHECK_KEEP_BY_CONSEC.values())


def check_trend_invalidation(symbol: str, direction: str, entry_time: pd.Timestamp,
                             strategy: str = "Scoring", as_of=None) -> dict:
    """คืน {"active", "consec_break", "keep_pct", "reason"} — keep_pct = % lot ที่ควรเหลือ
    (100 = ปกติ, 75/50 = เตือนภัยบางส่วน, 0 = invalidated เต็มรูปแบบ)
    strategy == 'Reversal' -> ข้าม check นี้ทั้งหมด (active=False เสมอ ไม่มีทาง invalidate)"""
    if strategy in ("Reversal", _cfg.REV_A_STRATEGY):      # Retracement = Reversal A เปลี่ยนชื่อ 2026-10-21
        return {"active": False, "consec_break": 0, "keep_pct": 100,
                "reason": f"ข้าม — ไม้ {strategy} เข้าสวน trend โดยดีไซน์อยู่แล้ว"}
    if strategy == "Sideway":
        return {"active": False, "consec_break": 0, "keep_pct": 100,
                "reason": "ข้าม — ไม้ Sideway ไม่ได้อิง trend 1D (ออกเมื่อ ADX ≥ SIDEWAY_EXIT_ADX แทน)"}

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

# 🔴 ปิดทั้งระบบตั้งแต่ 2026-09-03 (ผู้ใช้) · replay +3.25R (กฎช่วยแค่ crypto ที่เหลือแพ้) · ถอยกลับ = True
# 🔻 2026-10-13 ลองปิดไม้เมื่อ Structure 4H (regression) พลิกสวนทิศไม้: −33.62R |t| ~2 · รอ 10-20 วันก่อนใช้ยังติดลบ · ใช้ Bias 1D แทน ≈ 0
#   = Structure เร็วไป (จับการย่อ) · Bias ช้าไป (SL มาก่อน)
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "exit_monitor.py — STRUCTURE_BREAK_ENABLED (ปิด 2026-09-03 · ทดลอง 2026-10-13)"
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
# นับ "วันที่ถือไม้" — มี 2 นิยาม อย่าสลับกัน
# ---------------------------------------------------------------------------
# 🔴 2026-09-22 (คำสั่งผู้ใช้): กฎที่ถามว่า "ราคาควรวิ่งภายใน N วัน" ต้องนับเฉพาะวันที่ตลาด
# เปิดจริง — ของเดิมนับวันปฏิทินล้วน เสาร์-อาทิตย์ที่ตลาดปิดจึงถูกนับเป็น "เวลาที่ให้ราคาวิ่ง"
# ทั้งที่ราคาขยับไม่ได้เลย  เคสจริงที่ทำให้เจอ: XAUUSDm #4726514804 เข้า พฤ 17/09 05:50
# กฎ 3 วันยิงตอน **อา 20/09 ซึ่งตลาดปิด** ทั้งที่ตลาดเพิ่งเปิดให้ราคาวิ่งไปแค่ 1 วันทำการ
# (ศุกร์) — นับแบบใหม่ได้ 3 วันทำการพอดีที่ อัง 22/09 05:50 = ช้ากว่าเดิม 2 วัน
#
# CRYPTO เปิด 24/7 จึงไม่หักอะไร (ดู config.ASSET_CLASS = แหล่งความจริงเดียวของการจัดกลุ่ม)
# ⚠️ ตัดหยาบระดับ "ทั้งวันเสาร์ + ทั้งวันอาทิตย์" ตามที่สั่ง ไม่ได้ไล่ตามเวลาเปิด-ปิดจริงของ
#    โบรก (FX ปิดศุกร์ ~23:59 เปิดอาทิตย์เย็น) ส่วนต่างระดับไม่กี่ชั่วโมงต่อสุดสัปดาห์
# ⚠️ **ใช้กับ SLOW_TRADE_DAYS เท่านั้น** MAX_HOLD_DAYS ยังนับวันปฏิทินเหมือนเดิม เพราะมันคือ
#    เพดาน "เงินทุนถูกล็อกไว้นานแค่ไหน" (เสาร์-อาทิตย์ก็ล็อกอยู่) และเป็นตัวที่ backtest_replay
#    /backtest_trade_sim บังคับเองด้วย timedelta ปฏิทิน — เปลี่ยนนิยามที่นี่ที่เดียวจะทำให้
#    ระบบจริงกับ backtest ทำคนละอย่าง ซึ่งเป็นบั๊กที่เพิ่งแก้ไปเมื่อ 2026-09-14
def _weekend_seconds(start: datetime, end: datetime) -> float:
    """จำนวนวินาทีในช่วง [start, end) ที่ตกวันเสาร์/อาทิตย์"""
    if end <= start:
        return 0.0
    total = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        nxt = day + timedelta(days=1)
        if day.weekday() >= 5:            # 5 = เสาร์, 6 = อาทิตย์
            total += (min(end, nxt) - max(start, day)).total_seconds()
        day = nxt
    return total


SLOW_TRADE_SKIP_WEEKENDS = True   # False = นับวันปฏิทินแบบก่อน 2026-09-22 (มีไว้ให้ backtest
                                  # เทียบ 2 นิยามได้: `backtest_replay.py <SYM> 730 --slow-calendar`)


def market_days_held(symbol: str, entry_time: datetime, now: datetime) -> float:
    """จำนวน 'วันที่ตลาดเปิด' ที่ถือไม้มา — CRYPTO = วันปฏิทินตรงๆ, ที่เหลือหักเสาร์-อาทิตย์

    now < entry_time (นาฬิกา MT5 กับเครื่องเหลื่อมกัน) คืนค่าติดลบตามจริง ไม่ clamp เป็น 0 —
    ให้พฤติกรรมเหมือน time_held_days เดิมเป๊ะ และค่าติดลบทำให้กฎไม่ยิง ซึ่งเป็นฝั่งที่ปลอดภัย"""
    elapsed = (now - entry_time).total_seconds()
    if not SLOW_TRADE_SKIP_WEEKENDS or get_asset_class(symbol) == "CRYPTO":
        return elapsed / 86400
    return (elapsed - _weekend_seconds(entry_time, now)) / 86400


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
    strategy = ctx.get("strategy") if ctx else journal.get_trade_strategy(pos.ticket)
    # 2026-10-02: ไม้ Sideway ไม่ใช้ ATR trailing (สูตรนั้นจะถอย SL ออกไป 2 ATR จาก swing ทันทีรอบแรก)
    #   และไม่ใช้ TP trailing (วัดแล้ว −10R · TP กลางกรอบอยู่ใน 1% แทบทุกไม้) — trail = None ปิดทั้งสองอย่าง
    #   ระยะ 1R = SL ตอนเข้า ซึ่ง scheduler/replay เก็บไว้ใน pinned_swing (pinned_atr_entry = None)
    #   เหลือแค่ BE ที่ BREAKEVEN_TRIGGER_R (วัดแล้วแทบไม่มีผลกับไม้ sideway — TP อยู่ราว 1.8R)
    if strategy == "Sideway":
        trail = None
        initial_sl = pinned_swing if pinned_swing else (sl if sl else None)
    else:
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

    # นาฬิกาเดียวกับ entry_time (เวลา MT5) — ห้ามใช้ datetime.now() ที่นี่ ดูที่ mt5_now()
    now = mt5_now() if as_of is None else pd.Timestamp(as_of).to_pydatetime()
    time_held_days = (now - entry_time).total_seconds() / 86400
    # วันทำการ (หักเสาร์-อาทิตย์ถ้าไม่ใช่ CRYPTO) — ใช้กับกฎ slow trade เท่านั้น
    # ดู comment ที่ market_days_held ว่าทำไม MAX_HOLD_DAYS ถึงไม่ใช้ตัวนี้
    market_days = market_days_held(symbol, entry_time, now)

    # ── 2) รัน Exit Decision Checklist โดยใช้ r_multiple/time_held ข้างบน (ก่อนคิด Chandelier) ──
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
    # post_news_no_profit = "เข้าเงื่อนไขไหม" (ข้อเท็จจริง ใช้แสดงผลเสมอ)
    # post_news_exit      = "แล้วจะปิดไม้ไหม" — แยกกันเพื่อให้ตอนปิดสวิตช์ checklist
    # ยังรายงานตามจริง ไม่ใช่เงียบไปเฉยๆ (ดู NEWS_POST_EXIT_ENABLED)
    post_news_no_profit = has_recent_news and pnl_pct <= 0
    post_news_exit      = post_news_no_profit and NEWS_POST_EXIT_ENABLED
    # ไม้ Sideway ใช้จุดยิง BE ของตัวเองได้ (config.SIDEWAY_BE_TRIGGER_R · None = ค่าเดียวกับทุกกลยุทธ์)
    be_trigger_r     = (_cfg.SIDEWAY_BE_TRIGGER_R if strategy == "Sideway" and _cfg.SIDEWAY_BE_TRIGGER_R
                        else BREAKEVEN_TRIGGER_R)
    ge1r             = r_multiple is not None and r_multiple >= be_trigger_r
    # ge1r = "ถึงเกณฑ์แล้วจริงไหม" (ข้อเท็จจริง ใช้แสดงผลเสมอ)  be_lock = "แล้วจะบังคับ BE ไหม"
    # แยกสองอย่างนี้ออกจากกัน ไม่งั้นตอนปิดสวิตช์ checklist จะยังประกาศว่าขยับ SL ทั้งที่ไม่ได้ขยับ
    be_lock          = ge1r and BREAKEVEN_ENABLED
    # จุดที่ SL ไปนั่งตอนกฎยิง = entry + BREAKEVEN_LEVEL_R * ระยะ 1R (ค่าลบ = ต่ำกว่า entry
    # ฝั่งเสี่ยง) 0.0 = ล็อกที่ entry เป๊ะ = พฤติกรรมเดิม — คำนวณตรงนี้เพื่อให้ทั้งข้อความที่แสดง
    # และ desired_sl ท้ายฟังก์ชันอ้างตัวเลขเดียวกัน ไม่ใช่คนละจุดเวลาตั้งค่าไม่เป็นศูนย์
    be_level         = BREAKEVEN_LEVEL_R
    for _trig, _lvl in BREAKEVEN_LADDER:       # ขั้นบันได — ว่าง = ไม่แตะ be_level เลย
        if r_multiple is not None and r_multiple >= _trig:
            be_level = max(be_level, _lvl)
    be_price         = (entry + be_level * sl_range * (1 if direction == "Long" else -1)
                        if sl_range else entry)
    slow_trade       = (SLOW_TRADE_ENABLED and market_days >= SLOW_TRADE_DAYS
                        and r_multiple is not None and r_multiple < SLOW_TRADE_R)
    hold_cap         = time_held_days >= MAX_HOLD_DAYS   # เพดานเงินทุน = วันปฏิทิน (ตรงกับ backtest)
    # Sideway: เพดานเวลาของตัวเอง + ออกเมื่อ ADX 4H กลับขึ้น ≥ SIDEWAY_EXIT_ADX (sideway จบ สมมติฐานหมด)
    sideway_done = False
    if strategy == "Sideway":
        hold_cap = time_held_days * 24 >= _cfg.SIDEWAY_MAX_HOLD_H
        from sideway import adx_now_closed          # import ช้า กันวนกับ scoring/regime_check
        sideway_adx  = adx_now_closed(symbol, as_of=as_of)
        sideway_done = sideway_adx >= _cfg.SIDEWAY_EXIT_ADX

    # ต่อท้ายเมื่อจุดล็อกไม่ใช่ entry — ไม่งั้นอ่าน log แล้วนึกว่าเสมอตัวทั้งที่ยอมเสียไว้แล้ว
    _be_note      = "" if not be_level else f" ({be_level:+g}R จาก entry)"
    breakeven_str = f"{be_price:,.3f}{_be_note}"

    invalidated = trend_broken_full or structure_broken or post_news_exit or hold_cap or sideway_done
    if trend_broken_full or structure_broken:
        final_decision = ("ออก 100% ทันที — Trend/Structure พัง", RED)
    elif sideway_done:
        final_decision = (f"ออก 100% ทันที — Sideway จบ (ADX 4H {sideway_adx:.1f} ≥ {_cfg.SIDEWAY_EXIT_ADX:g})", RED)
    elif hold_cap and strategy == "Sideway":
        final_decision = (f"ออก 100% ทันที — Sideway ถือครบ {_cfg.SIDEWAY_MAX_HOLD_H:g} ชม.", RED)
    elif post_news_exit:
        final_decision = ("ออก 100% ทันที — ข่าวสงบแล้วแต่ไม่กำไร", RED)
    elif trend_broken_partial:
        final_decision = (f"ออก {100 - trend_keep_pct}% — {trend_info['reason']}", YELLOW)
    elif slow_trade:
        # 🔴 2026-09-22: เดิมบรรทัดนี้ hardcode "ออก 50%" + "รอ setup ใหม่" ทั้งสองอย่างไม่ตรงกับ
        # ที่โค้ดทำ — % มาจาก SLOW_TRADE_KEEP (กวาดค่าได้ ไม่ใช่ 50 ตายตัว) และการตัดบางส่วน
        # **ไม่คืนช่องถือไม้** จึงไม่มี "setup ใหม่" ให้รอ (ช่องว่างเมื่อไม้ปิดหมดเท่านั้น)
        # checklist ข้อ 4 ถูกแก้ถ้อยคำนี้ไปแล้วตั้งแต่ 2026-09-13 แต่บรรทัดนี้ถูกลืมไว้
        final_decision = (f"ออก {100 - SLOW_TRADE_KEEP:g}% (Time exit) — "
                          f"ลดความเสี่ยงของไม้ที่ยังไม่ไปไหน", YELLOW)
    elif be_lock:
        final_decision = (f"{BE_DECISION_PREFIX} = {be_price:,.3f}{_be_note}", YELLOW)
    else:
        final_decision = ("ถือต่อ — ยังไม่มี signal ให้ออก", GREEN)

    checklist = [
        # กฎนี้ปิดเมื่อ TREND_CHECK_KEEP_BY_CONSEC เป็น 100 ทุกระดับ (สถานะปัจจุบัน)
        # -> ไม่ต้องพิมพ์ ตามหลักเดียวกับกฎอื่นที่ปิดอยู่ (คำสั่งผู้ใช้ 2026-09-23)
        *([{"no": 1, "q": "Daily ปิดสวน trend ที่ใช้เข้า? (พ้น grace period แล้ว)",
            "answer": trend_broken_full or trend_broken_partial,
            "action": (f"ออก 100% = Invalidation (ปิดสวนติดกัน {trend_info['consec_break']} แท่ง)"
                       if trend_broken_full else
                       f"ออก {100 - trend_keep_pct}% = เตือนภัย (ปิดสวนติดกัน {trend_info['consec_break']} แท่ง)"
                       if trend_broken_partial else ""),
            "severity": "red" if trend_broken_full else "yellow",
            "note": trend_info["reason"]}]
          if TREND_CHECK_ENABLED else []),
        *([{"no": 2, "q": "Structure ที่ใช้เข้าพังแล้ว?", "answer": structure_broken,
            "action": "ออก 100% = Structure broken" if structure_broken else "",
            "severity": "red",
            "note": "swing low/high ที่ใช้ตัดสินใจถูก break"}]
          if STRUCTURE_BREAK_ENABLED else []),
        {"no": 0, "q": f"ถือมาครบ {MAX_HOLD_DAYS:g} วันปฏิทินแล้ว?", "answer": hold_cap,
         "action": f"ออก 100% = ชนเพดานเวลา ({time_held_days:.0f} วันปฏิทิน)" if hold_cap else "",
         "severity": "red",
         "note": f"ถือมา {time_held_days:.1f} วันปฏิทิน — เพดานนี้นับรวมเสาร์-อาทิตย์ตั้งใจ "
                 f"(เงินทุนถูกล็อกอยู่จริง) ปิดโดยไม่สนว่า R เท่าไหร่ ดู comment ที่ MAX_HOLD_DAYS"},
        {"no": 3, "q": f"[ข้อมูล] มีข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ใน {NEWS_LOOKAHEAD_H} ชม.ข้างหน้าไหม?",
         # ไม่ใช่กฎ — ไม่เคยมีโค้ดที่ทำอะไรกับ has_news เลยตั้งแต่ต้น (ข้อความเดิมเขียนว่า
         # "พิจารณาออก/ลดขนาดก่อนข่าว" ซึ่งไม่มีอะไรรองรับ) เก็บไว้เป็นบรรทัดข้อมูลล้วน
         "answer": has_news, "action": "",
         "severity": "yellow",
         "note": f"บรรทัดข้อมูล ไม่ใช่กฎ ไม่มีผลต่อการตัดสินใจ — {news_detail if has_news else 'ไม่มีข่าวใกล้'}"},
        # กฎนี้ปิดอยู่ (SLOW_TRADE_ENABLED = False) -> ไม่พิมพ์ ตามหลักเดียวกับกฎอื่น
        *([{"no": 4, "q": f"ถือมา {SLOW_TRADE_DAYS:g} วันทำการแล้ว R ยังต่ำกว่า {SLOW_TRADE_R:g}R?",
            "answer": slow_trade,
            # 2026-09-13: เดิมเขียน "รอ setup ใหม่" ซึ่ง **ไม่ตรงกับที่โค้ดทำ** — ตัด 50% ไม่ได้
            # คืนช่องถือไม้ (ช่องว่างเมื่อไม้ปิดหมดเท่านั้น) ไม้ที่โดนตัดครึ่งยังครองช่องต่อไป
            # สิ่งที่คืนจริงคือโควตา config.MAX_PORTFOLIO_RISK_R ซึ่งเป็นผลข้าม symbol
            "action": (f"ออก {100 - SLOW_TRADE_KEEP:g}% (Time exit) — ลดความเสี่ยงของไม้ที่ยังไม่ไปไหน"
                       if slow_trade else ""),
            "severity": "yellow",
            "note": f"ถือมา {market_days:.1f} วันทำการ (เกณฑ์ {SLOW_TRADE_DAYS:g}) · R ตอนนี้ "
                    f"{'N/A' if r_multiple is None else f'{r_multiple:+.2f}'} (เกณฑ์ < {SLOW_TRADE_R:g}) — "
                    f"⚠️ เจตนาเดิม 'setup ดีราคาควรวิ่งภายใน {SLOW_TRADE_DAYS:g} วัน' ขัดกับข้อมูลจริง "
                    f"(71% ของไม้ Scoring ถือเกิน 3 วัน — เลขนั้นนับวันปฏิทิน ยังไม่ได้วัดใหม่แบบ"
                    f"วันทำการ) ดู comment ที่ SLOW_TRADE_DAYS"}]
          if SLOW_TRADE_ENABLED else []),
        {"no": 5, "q": f"กำไร >= {be_trigger_r:g}R แล้ว? (ระยะกำไร = ระยะ SL)", "answer": ge1r,
         "action": (f"{BE_DECISION_PREFIX} = {breakeven_str}" if be_lock else
                    f"ถึง {be_trigger_r:g}R แล้ว แต่ BREAKEVEN_ENABLED = False — "
                    f"ปล่อยให้ ATR trailing คุม SL" if ge1r else ""),
         "severity": "yellow",
         "note": "ป้องกัน winner กลายเป็น loser — นี่คือขยับ SL ไม่ใช่การออก"},
        *([{"no": 6, "q": f"ข่าวสงบแล้ว ({NEWS_POST_H} ชม.) แต่ยังไม่กำไร?",
            "answer": post_news_no_profit,
            "action": "ออก 100% = ข่าวสงบแล้วแต่ไม่กำไร" if post_news_exit else "",
            "severity": "red",
            "note": f"เช็คว่ามีข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ออกภายใน {NEWS_POST_H} "
                    f"ชม.ที่ผ่านมาไหม — {recent_news_detail if has_recent_news else 'ไม่มีข่าวล่าสุด'}"}]
          if NEWS_POST_EXIT_ENABLED else []),
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
             # 🔴 2026-09-22: ข้อความเดิม "ล็อกกำไรครึ่งหนึ่ง คู่กับ SL ที่ขยับไป breakeven"
             # ผิดสองทาง — (1) RULE_1R_KEEP = 100 ตั้งแต่ 2026-09-13 กฎนี้ไม่ตัดอะไรเลย
             # (2) ตั้งแต่ BREAKEVEN_TRIGGER_R = 1.5 มันไม่ได้ "คู่กับ" กฎ BE อีกต่อไป
             # สองกฎยิงคนละจุดแล้ว  และเลข 1.0 เคย hardcode ทั้งที่ค่าจริงอยู่ที่ RULE_1R_TRIGGER
             "cond": (f"R-multiple >= {RULE_1R_TRIGGER:g}"
                      + ("" if RULE_1R_KEEP < 100 else " (กฎปิดอยู่ ไม่ลดขนาดไม้)")
                      + f" — คนละจุดกับกฎ BE ที่ยิงที่ {BREAKEVEN_TRIGGER_R:g}R")},
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
            # keep_pct = 100 ตั้งแต่ 2026-09-19 = รายงานว่าใกล้ข่าวแต่ไม่หั่นไม้ (ดูที่นิยาม
            # NEWS_IMMINENT_KEEP ว่าทำไม) — คงแถวไว้เพื่อให้ log ยังบอกว่ามีข่าวใกล้เข้ามา
            {"no": 5, "name": "ใกล้ข่าว High Impact",  "trigger": news_imminent,          "keep_pct": NEWS_IMMINENT_KEEP,
             "cond": f"ข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ภายใน {NEWS_IMMINENT_H} ชม. "
                     f"(กฎปิดอยู่ ไม่ลดขนาดไม้) — {news_detail if news_imminent else ''}"},
        ]
        # 🔴 2026-09-23 (คำสั่งผู้ใช้): **ตัดกฎที่ keep = 100 ออกจากรายการไปเลย ไม่ต้องแสดง**
        # keep = 100 แปลว่ากฎนั้นปิดอยู่ (min() ไม่มีทางถูก 100 ดึงลง) การพิมพ์มันออกมาพร้อม
        # คำว่า "ควรเหลือ 100%" ทำให้อ่านเหมือนระบบมีขั้นที่สองที่ทำงานอยู่ ทั้งที่ไม่มี
        # ตอนนี้ทั้ง 5 กฎเป็น 100 หมด -> รายการว่าง -> print_report ข้ามทั้งบล็อก
        # ค่าคงที่ RULE_*_KEEP ยังอยู่ครบ เพราะธง --rule-1r-keep / --rule-hot-keep ฯลฯ ของ
        # backtest_replay ใช้กวาดค่ากลับมาได้ตลอด พอตั้งค่าต่ำกว่า 100 กฎจะโผล่กลับมาเอง
        position_rules  = [r for r in position_rules if r["keep_pct"] < 100]
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
        # ไม่มีกฎ Position Sizing เหลือ = ไม่ต้องอธิบายการคูณสองขั้น บอกผลลัพธ์ตรงๆ พอ
        combined_label = (f"Final={base_keep_pct}% x Position Sizing={stage_keep_pct}% => "
                          f"ขาย {sell_str} (เหลือ {keep_str_c})" if position_rules else
                          f"ขาย {sell_str} (เหลือ {keep_str_c})")
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
            # เทียบกับ "จุดที่ล็อก" ไม่ใช่ entry — ไม่งั้นตอน BREAKEVEN_LEVEL_R < 0 ไม้จะค้างอยู่
            # ที่จุดล็อกตลอดไป: sl ไม่มีวัน >= entry ratchet จึงอยู่ขาที่ห้าม SL ขยับเข้าใกล้ราคา
            # = ATR trailing ล็อกกำไรเพิ่มไม่ได้อีกเลย (ที่ 0.0 นิพจน์นี้เท่าเดิมทุกประการ)
            past_breakeven = (sl >= be_price) if direction == "Long" else (sl <= be_price)
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

        # Breakeven force เป็นข้อยกเว้นตั้งใจ (กำไร >= 1R) — ให้ขยับเข้าหาจุดล็อกได้ ทับ ratchet ด้านบน
        if be_lock:
            desired_sl = max(desired_sl, be_price) if direction == "Long" else min(desired_sl, be_price)
            # ล็อกเพิ่มตามระยะทางที่เดินไปหา TP (ดู TP_PROGRESS_LOCK) — ทับ ratchet เหมือน BE
            # เพราะเป็นการขยับ "เข้าหากำไร" ทางเดียว และ monotone ในตัว extreme อยู่แล้ว
            # (extreme ไม่ถอยหลัง -> lock_sl ไม่ถอยหลัง) จึงไม่ต้องจำ state ข้ามรอบ
            if TP_PROGRESS_LOCK and tp and TP_PROGRESS_LOCK_KEEP:
                span = (tp - entry) if direction == "Long" else (entry - tp)
                run  = (extreme - entry) if direction == "Long" else (entry - extreme)
                if span > 0:
                    # คร่อมที่ 1.0 — ในโหมด backtest extreme ล้ำ tp ได้ (แท่งเดียวทะลุทั้งแท่ง)
                    # ซึ่งจะทำให้ล็อกเกินจุดที่เคยเป็นจริง
                    progress = min(max(run / span, 0.0), 1.0)
                    lock_sl  = entry + TP_PROGRESS_LOCK_KEEP * progress * span * \
                               (1 if direction == "Long" else -1)
                    desired_sl = max(desired_sl, lock_sl) if direction == "Long" \
                                 else min(desired_sl, lock_sl)
    else:
        desired_sl = be_price if be_lock else None  # คำนวณสูตรไม่ได้ -> ใช้กติกาเดิม (ล็อกเมื่อ >=1R)

    # BE ลอยตาม ATR (BREAKEVEN_ATR_FLOAT > 0 · ดูที่ค่าคงที่) — ทับผลของ BE/ratchet ข้างบนเฉพาะช่วงก่อนถึงขั้นบันได
    if (BREAKEVEN_ATR_FLOAT and BREAKEVEN_ENABLED and trail is not None and initial_sl and sl
            and be_level <= 0):
        _lg = direction == "Long"
        _eps = 1e-9 * abs(entry)
        _float = (entry - BREAKEVEN_ATR_FLOAT * trail["atr_latest"]) if _lg \
            else (entry + BREAKEVEN_ATR_FLOAT * trail["atr_latest"])
        _float = max(_float, initial_sl) if _lg else min(_float, initial_sl)
        _in_float = (initial_sl + _eps < sl < entry - _eps) if _lg else (entry + _eps < sl < initial_sl - _eps)
        if ge1r or _in_float:
            desired_sl = _float

    # ── TP ที่ควรตั้งรอบนี้ — เริ่ม trail เมื่อใกล้ TP เดิม <= TRAIL_TP_TRIGGER_PCT ──
    desired_tp = None
    # เช็คสวิตช์ก่อน — ถ้าปล่อยให้คำนวณด้วย buffer 0 จะได้ desired_tp = TP เดิม (ไม่ส่งคำสั่งก็จริง)
    # แต่รายงานรายไม้จะพิมพ์ "TP ที่จะตั้ง (Trailing)" ของกฎที่ปิดอยู่ (ขัดคำสั่งผู้ใช้ 2026-09-23)
    if (TRAIL_TP_ATR_BUFFER > 0 and TRAIL_TP_TRIGGER_PCT > 0
            and not invalidated and dist_tp_pct is not None and dist_tp_pct <= TRAIL_TP_TRIGGER_PCT
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
        # วันทำการ = ตัวที่กฎ slow trade ใช้จริง (ต่างจาก time_held_days เมื่อไม่ใช่ CRYPTO)
        "market_days_held": market_days,
        "symbol": symbol, "ticket": pos.ticket, "direction": direction,
        "entry": entry, "sl": sl, "tp": tp, "lot": lot,
        "trail": trail, "initial_sl": initial_sl,
        "trailing_sl": trailing_sl, "desired_sl": desired_sl,
        "be_lock": be_lock, "be_price": be_price, "be_level": be_level,
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
    # ทั้งคู่เป็นเวลา MT5 (UTC) ไม่ใช่เวลาเครื่อง — ช้ากว่า timestamp หัวบรรทัดของ log 7 ชม.
    print(f"  Entry Date/Time   : {entry_str}  (เวลา MT5 · UTC)")
    print(f"  Current Date/Time : {now_str}  (เวลา MT5 · UTC)")
    # แสดง 2 ตัวเลขเมื่อไม่เท่ากัน — กฎคนละข้อใช้คนละตัว (ข้อ 0 = ปฏิทิน, ข้อ 4 = วันทำการ)
    # ถ้าโชว์ตัวเดียวจะอ่าน checklist แล้วงงว่าทำไม "5.1 วัน" ยังไม่ชนเกณฑ์ 3 วัน
    held_str = f"{m['time_held_days']:.1f} days"
    mkt_days = m.get("market_days_held")
    if mkt_days is not None and abs(mkt_days - m["time_held_days"]) >= 0.05:
        held_str += f"  ({mkt_days:.1f} วันทำการ — หักเสาร์-อาทิตย์ที่ตลาดปิด)"
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
        _skip = " — ข้าม Position Sizing Rules (ไม่ต้องคิดต่อ)" if m["position_rules"] else ""
        print(f"  {RED}Final Decision ฟันธงออก 100% แล้ว{_skip}{RESET}")
    elif m["position_rules"]:
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
    # ไม่มีกฎ Position Sizing เหลือ = ไม่ต้องโชว์การคูณสองขั้นที่ขั้นหนึ่งไม่มีอยู่จริง
    if m["position_rules"]:
        print(f"  Final % (ฐาน) = {base_str}   x   Position Sizing % = {stage_str}")
    # แสดงทั้ง 3 ตัวเลขให้ครบ กัน "100%" อ่านแล้วเข้าใจผิดว่าไม้ยังเต็มจำนวนทั้งที่ปิดไปบางส่วนแล้ว
    # (ทุกอย่างคิดจาก lot ตอนเปิดไม้ = ฐานเดียวกับ execute_decision เป๊ะ)
    basis_note = "" if m["original_lot"] is not None else f" {DIM}(ไม่พบ lot เปิดใน journal — ใช้ lot ปัจจุบันแทน){RESET}"
    print(f"  {_b('สรุป: ควรเหลือ Position')} = {keep_color}{keep_str}{RESET}"
          f" ของ lot ตอนเปิด {m['lot_basis']} = {keep_color}{lot_str}{RESET}{basis_note}")

    # 🔴 2026-09-22: บล็อกนี้เคยโกหก 2 ทาง — (1) โชว์ lot เป้าหมายแบบไม่ปัดตาม step/min ของโบรก
    # ทั้งที่ execute_decision ปัดด้วย clamp_lot เสมอ  (2) พอส่วนเกินต่ำกว่า min lot กลับพิมพ์ว่า
    # "ถึงเป้าแล้ว" ทั้งที่ยังถือเต็มไม้และกฎยิงค้างอยู่  เคสจริง XAUUSDm lot 0.01 keep 50% ->
    # เป้า 0.005 ซึ่งโบรกไม่รับ: จอขึ้น "ควรเหลือ 0.005 ... ถึงเป้าแล้ว" พร้อมกับ ACTION
    # "ขาย 50%" ในหน้าจอเดียวกัน  ตอนนี้คิดเป้าด้วย clamp_lot ตัวเดียวกับที่สั่งจริง
    blocked_note = ""      # เก็บ "เหตุผล" ล้วน — คนเรียกเติมคำนำหน้าเองตามบริบทของบรรทัด
    if keep_pct < 100 and m["original_lot"] is None:
        # execute_decision ข้ามปิดบางส่วนทั้งดุ้นเมื่อไม่รู้ lot ตอนเปิด (คิด % จาก lot ปัจจุบัน
        # จะปิดทบซ้ำทุกชั่วโมง) — จอต้องบอกตรงกัน
        blocked_note = "ไม่พบ lot ตอนเปิดใน journal"
    held_color = GREEN if m["held_pct"] >= 100 else YELLOW
    print(f"  ตอนนี้ถืออยู่ {held_color}{m['lot']} ({m['held_pct']:g}% ของไม้เดิม){RESET}", end="")
    info       = mt5.symbol_info(m["symbol"])
    min_lot    = info.volume_min if info else 0.01
    step       = info.volume_step if info else 0.01
    # เป้าจริงที่ execute_decision ใช้ — keep=0 คือปิดเต็มจำนวน (close_order) ไม่ผ่าน clamp_lot
    # ซึ่งมี min_lot เป็นพื้น ถ้าเผลอ clamp ตรงนี้จะกลายเป็น "เหลือ 0.01" ทั้งที่จะปิดหมด
    target_lot = 0.0 if keep_pct <= 0 else clamp_lot(m["symbol"], m["remaining_lot"])
    diff       = round(m["lot"] - target_lot, 3)
    # "กฎสั่งให้ถือน้อยกว่าที่ถืออยู่" — เทียบกับเป้าดิบก่อนปัด ไม่ใช่เป้าหลังปัด
    wants_cut  = keep_pct < 100 and m["lot"] > m["remaining_lot"] + 1e-9
    if diff >= min_lot and not blocked_note:
        print(f" -> {YELLOW}ต้องปิดเพิ่ม {diff:g} lot (เหลือ {target_lot:g}){RESET}")
    elif wants_cut:
        if not blocked_note:
            blocked_note = (f"เป้า {m['remaining_lot']:g} lot โบรกรับไม่ได้ (min {min_lot:g} / "
                            f"step {step:g}) ปัดแล้วได้ {target_lot:g} = ไม่มีส่วนเกินให้ปิด")
        print(f" -> {YELLOW}ปิดเพิ่มไม่ได้ — {blocked_note} ไม้จึงยังเท่าเดิม{RESET}")
    else:
        blocked_note = ""
        print(f" -> {DIM}ถึงเป้าแล้ว ไม่ต้องทำอะไรเพิ่ม{RESET}")

    label, color = m["final_decision"]
    print("=" * 62)
    print(f"  {_b('FINAL DECISION')} : {color}{label}{RESET}")
    print("=" * 62)

    combo_label, combo_color = m["combined_decision"]
    print(f"  {_b('>>> ACTION จริงที่ควรทำ')} : {combo_color}{combo_label}{RESET}")
    if blocked_note:
        # บรรทัด ACTION พูดถึง "กฎบอกให้ขายเท่าไหร่" ส่วนบรรทัดนี้คือ "รอบนี้ทำได้จริงแค่ไหน"
        print(f"  {YELLOW}⚠️ รอบนี้ทำจริงไม่ได้ — {blocked_note}{RESET}")
    if m["position_rules"]:
        print(f"  {DIM}(กติกา: Final=0% หยุดคิดทันที | Final=50%/100% คิดต่อด้วย Position Sizing "
              f"แล้วคูณกันเป็นทอด){RESET}")
    print("=" * 62)


# ---------------------------------------------------------------------------
# Auto Execute — สั่งจริงตามผลลัพธ์ (เฉพาะบัญชี DEMO เท่านั้น)
# ---------------------------------------------------------------------------

def _price_moved(new: float, old: float, digits: int | None) -> bool:
    """ต้องส่งคำสั่งขยับ SL/TP ไหม — เทียบหลังปัดเป็นจำนวนตำแหน่งทศนิยมของ symbol
    (ต่างกันไม่ถึงครึ่ง point = broker ปัดเป็นราคาเดิม ส่งไปก็ได้ "no changes" กลับมา)

    🔴 2026-09-28: เดิมเทียบ `abs(new − old) >= 1e-3` เป็นหน่วยราคาตายตัว = 10 pip ของ EURUSD
    ระยะที่ TP trailing ดึง TP ของ EUR คือ 0.0003-0.0007 (13/13 ไม้ใน base ต่ำกว่า 0.001) คำสั่ง
    จึงไม่เคยถูกส่ง = TP trailing ไม่เคยทำงานกับ EUR ในระบบจริง และการถอย SL ตาม ATR ก็แทบไม่ถูกส่ง
    ขณะที่ backtest_replay ขยับให้ทุกครั้ง (ไม่ผ่านฟังก์ชันนี้) · symbol อื่นใน SYMBOLS ไม่โดน
    เพราะ 0.001 <= 1 point ของมันอยู่แล้ว (tick_size = point ทุกตัว ตรวจแล้ว 2026-09-28)
    digits = None (อ่านสเปคไม่ได้) -> ใช้เกณฑ์เดิม ไม่ส่งคำสั่งรัวๆ ตอนข้อมูลโบรกหาย"""
    if not old:
        return True                        # ยังไม่มี SL/TP บน broker เลย -> ตั้งได้เสมอ
    if digits is None:
        return abs(new - old) >= 1e-3
    return round(new, digits) != round(old, digits)


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

        # เกณฑ์ "ขยับจริงไหม" ต้องอิงความละเอียดราคาของ symbol — ดู _price_moved
        _info   = mt5.symbol_info(symbol)
        _digits = _info.digits if _info else None

        # 3) ขยับ SL ตาม ATR Trailing (ถ้า >=1R แล้ว desired_sl ถูก clamp ไม่ต่ำกว่า breakeven)
        desired_sl = m["desired_sl"]
        if desired_sl is not None and _price_moved(desired_sl, sl, _digits):
            modify_sltp(ticket, new_sl=desired_sl)
            print(f"  {CYAN}[AUTO] ขยับ SL #{ticket} -> {desired_sl:,.{_digits or 3}f} (ATR Trailing){RESET}")
            # แจ้ง Telegram เฉพาะตอนที่การขยับนี้คือ "ไป BE" — ATR trailing ปิดอยู่และขึ้นไม่ถึง entry
            # โดยโครงสร้าง SL ที่ไปนั่งตรง be_price จึงมาจากกฎ BE เท่านั้น · ยิงครั้งเดียวต่อไม้เอง
            # (รอบถัดไป SL = be_price แล้ว _price_moved เป็น False ไม่ส่ง modify ซ้ำ)
            # การขยับอื่น (เช่นขยาย SL รอบแรกหลังเปิดไม้) ไม่แจ้ง — ผู้ใช้ขอแค่ BE
            if m.get("be_lock") and not _price_moved(desired_sl, m["be_price"], _digits):
                notify.notify_breakeven(symbol, m["direction"], ticket, entry, desired_sl,
                                        m.get("r_multiple"), is_demo=is_demo_account(),
                                        lock_r=m.get("be_level", 0.0))   # > 0 = ขั้น BREAKEVEN_LADDER

        # 4) ขยับ TP ตาม TP Trailing (เริ่มเมื่อใกล้ TP เดิม <= TRAIL_TP_TRIGGER_PCT)
        desired_tp = m["desired_tp"]
        if desired_tp is not None and _price_moved(desired_tp, tp, _digits):
            modify_sltp(ticket, new_tp=desired_tp)
            print(f"  {CYAN}[AUTO] ขยับ TP #{ticket} -> {desired_tp:,.{_digits or 3}f} (TP Trailing){RESET}")

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


def rules_status() -> list[tuple[str, bool, str]]:
    """สถานะกฎ exit ทุกตัว **คำนวณจากค่าคงที่จริง ไม่ใช่สำเนาใน comment**

    มีไว้เพราะ comment ในไฟล์นี้อธิบายค่า*เดิม*ของกฎที่ปิดไปแล้วยาวหลายสิบบรรทัด จนอ่านผ่านๆ
    แล้วนึกว่าเป็นค่าปัจจุบันได้ง่ายมาก (เกิดขึ้นจริง 2026-09-23 กับ TREND_CHECK_KEEP_BY_CONSEC)
    ตารางที่เขียนด้วยมือจะกลายเป็นสำเนาที่ล้าสมัยอีกใบ — ตัวนี้จึงอ่านจากตัวแปรตรงๆ
    ใช้: `./run_wine.sh exit_monitor.py --rules`  (และพิมพ์อัตโนมัติตอน run_monitor เริ่ม)"""
    return [
        ("SL / TP ที่ broker",        True,  "ทางออกหลักของระบบ"),
        (f"เพดานเวลา {MAX_HOLD_DAYS:g} วันปฏิทิน", True, "checklist ข้อ 0 — ปิด 100%"),
        (f"slow trade {SLOW_TRADE_DAYS:g} วันทำการ", SLOW_TRADE_ENABLED and SLOW_TRADE_KEEP < 100,
         f"checklist ข้อ 4 — เหลือ {SLOW_TRADE_KEEP:g}% · วัดได้ +12.63R ตอนปิด |t| 2.15"),
        (f"breakeven ที่ {BREAKEVEN_TRIGGER_R:g}R", BREAKEVEN_ENABLED,
         f"checklist ข้อ 5 — ล็อก SL ที่ {BREAKEVEN_LEVEL_R:+g}R จาก entry"
         + "".join(f" · ถึง {t:g}R ล็อก {l:+g}R" for t, l in BREAKEVEN_LADDER)),
        ("trend invalidation",        TREND_CHECK_ENABLED,
         f"checklist ข้อ 1 — TREND_CHECK_KEEP_BY_CONSEC = {TREND_CHECK_KEEP_BY_CONSEC}"),
        ("structure break",           STRUCTURE_BREAK_ENABLED, "checklist ข้อ 2"),
        ("กฎข่าว A (ข่าวสงบไม่กำไร)",  NEWS_POST_EXIT_ENABLED,  "checklist ข้อ 6 — วัดได้ -4.33R"),
        ("กฎข่าว B (ใกล้ข่าว)",        NEWS_IMMINENT_KEEP < 100, "sizing 5 — วัดได้ -4.57R"),
        ("sizing 1 ถึง 1R",           RULE_1R_KEEP < 100,      f"RULE_1R_KEEP = {RULE_1R_KEEP:g}"),
        ("sizing 2 Indicator ร้อน",    RULE_HOT_KEEP < 100,     f"RULE_HOT_KEEP = {RULE_HOT_KEEP:g}"),
        ("sizing 3 เดินทาง >=50% TP",  RULE_HALFWAY_KEEP < 100, f"RULE_HALFWAY_KEEP = {RULE_HALFWAY_KEEP:g}"),
        ("sizing 4 Climax",           RULE_CLIMAX_KEEP < 100,  f"RULE_CLIMAX_KEEP = {RULE_CLIMAX_KEEP:g}"),
        (f"TP trailing (ใกล้ TP <={TRAIL_TP_TRIGGER_PCT:g}%)",
         TRAIL_TP_ATR_BUFFER > 0 and TRAIL_TP_TRIGGER_PCT > 0,
         (f"ดึง TP เข้าหา entry {TRAIL_TP_ATR_BUFFER:g}xATR1H" if TRAIL_TP_ATR_BUFFER > 0
          else "ปิดอยู่ (buffer 0)")
         + " · กวาด 0-2 (2026-09-27): ที่ราบ 0.25-1.0 · ปิดกฎ −2.67R · 0.25 ดีกว่า 0.5 +3.37R"),
        ("TP progress lock",          bool(TP_PROGRESS_LOCK),  "ล็อก SL ตามระยะทางไป TP"),
        ("ATR trailing (ดึง SL แคบลง)", False,
         "เปิดอยู่แต่ทำไม่ได้โดยโครงสร้าง — ฐานตรึงที่ swing ตอนเข้า ขึ้นไม่ถึง entry"),
    ]


def print_rules_status() -> None:
    on  = [r for r in rules_status() if r[1]]
    off = [r for r in rules_status() if not r[1]]
    print("-" * 62)
    print(_b(f"  กฎ exit ที่ทำงานจริง {len(on)} ตัว"))
    for name, _, detail in on:
        print(f"    {GREEN}ON {RESET} {name:26s} {DIM}{detail}{RESET}")
    print(f"  {DIM}ปิดอยู่ {len(off)} ตัว — ไม่ถูกพิมพ์ในรายงานรายไม้{RESET}")
    for name, _, detail in off:
        print(f"    {DIM}off  {name:26s} {detail}{RESET}")
    print("-" * 62)


def run_monitor():
    load_dotenv()
    print("=" * 62)
    print(_b("  EXIT MONITOR  (กด Ctrl+C เพื่อหยุด)"))
    print(f"  Timeframe : 1H   Interval : {INTERVAL_SECONDS // 60} นาที")
    print("=" * 62)
    print_rules_status()   # ให้ log ของทุกรอบการรันจริงบันทึกไว้เสมอว่าตอนนั้นเปิดกฎอะไรอยู่

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
    if "--rules" in sys.argv:
        print_rules_status()
        sys.exit(0)
    if "--once" in sys.argv:
        scan_once()
    else:
        try:
            run_monitor()
        except KeyboardInterrupt:
            print("\n\n  หยุด Exit Monitor แล้ว")
