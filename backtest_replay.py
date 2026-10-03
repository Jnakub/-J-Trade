"""
backtest_replay.py — จำลอง scheduler.scan_symbol() ย้อนหลังทีละชั่วโมง ให้ "เงื่อนไขการเข้าไม้"
ตรงกับระบบจริงมากที่สุดเท่าที่ backtest จะทำได้

ตัวนี้ตั้งใจ **ไม่ตัดอะไรออกเลย** เดินตามลำดับด่านเดียวกับ
scheduler.scan_symbol() เป๊ะ เพื่อให้ตัวเลขที่ได้เอาไปตัดสินใจแทนผลเทรดจริงได้

ลำดับด่าน (ตรงกับ scheduler.scan_symbol ข้อต่อข้อ):
  1. มี position เปิดอยู่ -> ไม่หา entry ใหม่ (ดูแลไม้เดิมแทน)
  3. Daily loss guard — ขาดทุนรวมของ "ไม้ที่ปิดวันนี้" ถึง MAX_DAILY_LOSS แล้วหยุดหาไม้ใหม่
  3b. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่ถึง COOLDOWN_HOURS_BY_SYMBOL ชม.
  4b. News guard — ⚠️ จำลองไม่ได้ (ดู "สิ่งที่ยังต่างจากของจริง" ด้านล่าง)
  4. Regime check — SKIP ถ้า regime อยู่ใน REGIME_NO_TRADE
     REGIME_TREND    -> Scoring  (get_trend_bias + compute_entry)
     REGIME_REVERSAL -> Reversal (compute_reversal_entry ทิศตามขั้ว divergence)
  4b-2. ผ่านสกอร์การ์ดแล้วดึง TP เข้าไม่ให้ไกลเกิน config.TP_MAX_ATR เท่าของ ATR ตอนเข้าไม้
  4c. Run-up guard — ข้ามไม้ที่ราคาวิ่งไปทางที่จะเข้ามาแล้วเกิน MAX_RUNUP_24H_R (เฉพาะ Scoring)
  5. ผ่านทุกด่าน -> เปิดไม้ที่ราคา ณ ชั่วโมงนั้น + spread

รอบสแกน = ทุก 1 ชั่วโมง ตรงกับ scheduler.INTERVAL_SECONDS (เครื่องมือรุ่นก่อนที่สแกนทุก 4 ชม.
พลาดจังหวะที่ระบบจริงเข้าได้ 3 ใน 4 ของโอกาส)

⚠️ สิ่งที่ยังต่างจากของจริง — อ่านก่อนเชื่อตัวเลข:
  1. **Exit ใช้ exit_monitor.analyze_position(as_of=...) ตัวจริง** (2026-08-27) — ได้ ATR
     trailing SL, structure break, trend invalidation, climax, RSI/BB ร้อน, slow-trade 3 วัน,
     TP trailing, การคูณ keep% เป็นทอด ครบตามระบบจริง เรียกทุก 1 ชม.ตรงกับ INTERVAL_SECONDS
     สิ่งที่ยังต่าง: broker fill SL/TP ถือว่าได้ราคาเป๊ะ และ execute_decision() ตัวจริงจะปิด
     บางส่วนตาม lot ที่ปัดแล้ว (clamp_lot) ส่วนที่นี่คิดเป็นสัดส่วนล้วน
  2. **News guard จำลองไม่ได้** — check_upcoming_news() ยิง ForexFactory แบบ real-time
     ไม่มีข้อมูลย้อนหลัง ผลคือ backtest จะ "เข้าไม้ก่อนข่าว" ในจังหวะที่ระบบจริงหลบ
  3. ราคาที่ใช้เป็น close ของแท่ง 1H ที่ปิดพอดี ณ วินาทีที่ตัดสินใจ (ของจริงคือ tick.bid ที่
     scheduler อ่านตอนรัน ซึ่งห่างจากขอบแท่งไม่กี่วินาที) — 2026-09-01: ก่อนหน้านี้ข้อนี้
     **ไม่จริง** โค้ดใช้ close ของแท่งที่ยังไม่ปิด (ราคาอีก 1 ชม.ข้างหน้า) ขณะที่ส่ง as_of=t
     ให้ scorecard = lookahead 1 ชม.บนราคาเข้า และเพราะ entry ถูกส่งเข้า compute_score ไปคิด
     R:R ด้วย ด่าน R:R จึงคัดไม้โดยเห็นอนาคต ทำให้ผลดีเกินจริง (ดู comment ในลูปหลัก)
  4. SL/TP ถือว่า fill ได้ที่ราคานั้นเป๊ะ ไม่มี slippage / gap / requote
  5. ไม่ได้จำลอง broker trade_mode, balance จริง, lot rounding — R-multiple ไม่ขึ้นกับ lot อยู่แล้ว
     แต่ daily loss guard ที่คิดจาก R จึงเป็นค่าประมาณ (MAX_DAILY_LOSS / RISK_PER_TRADE = กี่ R)

ใช้: py backtest_replay.py BTCUSDm [days=730] [--no-cost] [--min-sl=X]
     --min-sl=X  ทับค่า MIN_SL_DISTANCE_PCT ของ symbol นี้ (ไว้เทียบว่าเกณฑ์ไหนดีกว่าบนไม้จริง)
     --no-widen  ห้าม SL ขยับออกไปไกลกว่า SL ตอนเข้า (ยังขยับเข้าหาราคาได้ตามปกติ) — ไว้ตอบว่า
                 การที่ ATR trailing สั่ง SL แรกกว้างกว่า SL ตอนเข้า เป็นตัวทำให้ขาดทุนเกิน 1R
                 หรือมันช่วยให้ไม้รอดจากการย่อจนไปต่อได้ (ทดลองใน backtest เท่านั้น ไม่แตะระบบจริง)
     --legacy-sl ใช้ SL โครงสร้างเป็น SL ที่ส่ง broker แบบเดิม (ก่อน 2026-08-27) — ไว้เทียบผล
                 ของการส่ง SL แรกไปที่จุดเดียวกับ ATR trailing ตามที่ scheduler.py ทำตอนนี้
     --skip-regime="TREND แรงจัด"  เพิ่ม regime เข้า REGIME_NO_TRADE เฉพาะรอบนี้ (คั่นหลายตัว
                 ด้วย ,) — ไว้ตอบว่า "ถ้าไม่เข้าไม้ตอน regime นี้เลย ผลรวมดีขึ้นไหม"
     --rev-min-sl=X  ทับเกณฑ์ระยะ SL ขั้นต่ำ **เฉพาะทาง Reversal** (--min-sl ทับทั้งสองทาง) —
                 ไว้ตอบว่าเกณฑ์ที่ backtest มาจากฝั่ง Scoring เหมาะกับไม้สวนด้วยไหม
     --slot-per-strategy  ให้ถือไม้ได้ 1 ไม้ต่อ **กลยุทธ์** ต่อ symbol (ปกติ 1 ไม้ต่อ symbol) —
                 regime แยก TREND/REVERSAL-READY ชัดเจนอยู่แล้ว สองกลยุทธ์ไม่เคยแย่ง "สัญญาณ"
                 กันจริง แค่แย่ง "ช่องถือไม้" — ไว้ตอบว่าถ้าปลดล็อกช่องแล้ว Reversal ที่โดน
                 Scoring เบียดหายไปจะกลับมาไหม
                 ⚠️ ผลคือถือได้ 2 ไม้พร้อมกันต่อ symbol = ความเสี่ยงต่อ symbol เป็น 2 เท่า
                 backtest บวก R ตรงๆ ไม่ได้ปรับ sizing ให้ ตัวเลขที่ได้จึงเป็น "ถ้ายอมเสี่ยง
                 2 เท่า" ไม่ใช่ "ได้ฟรี"
     --rev-tp-ratio=X  ทับ config.REVERSAL_TP_FIB_RATIO (TP ไม้ Reversal) · tag _revtpX
     --scoring-min-rr=X  ทับด่าน R:R ขั้นต่ำเฉพาะทาง Scoring (Breakout ยังใช้ config) · tag _scoringminrrX
     --rev-min-rr=X  ทับ config.MIN_RR_HARD_BLOCK_REVERSAL (ดูค่าที่ config) — ด่าน R:R ขั้นต่ำ
                 ของไม้สวน (ตัวแปรคนละตัวกับ MIN_RR_HARD_BLOCK ที่ Scoring ใช้ ซึ่งเป็น 1.5
                 เท่ากันอยู่ตอนนี้) เคยลองลดเป็น 1.1 แล้วไม่ช่วย ดู config.py ที่ตัวแปรนั้น
     --rev-tp-from-entry  ฉาย Fibonacci TP ของ Reversal จากราคาเข้าแทน swing B — ไว้ตอบว่า
                 การที่ reward หดตามระยะที่ราคาห่างจาก swing (จน TP ไปโผล่หลัง entry 22-28%
                 ของ setup) เป็นตัวที่ทำให้ไม้ Reversal เข้าน้อยหรือเปล่า
                 ⚠️ ทั้งสองตัวคือการ "ผ่อนด่าน" ไม่ใช่แก้บั๊ก — ต้องดู WR คู่กับ Total R เสมอ
     --rule-1r-keep=N  ทับ exit_monitor.RULE_1R_KEEP (ปกติ 50) — พอกำไรแตะ 1R เหลือไม้ไว้กี่ %
                 (100 = ไม่ตัดเลย) ไว้ตอบว่ากฎล็อกกำไรครึ่งไม้ที่ 1R คุ้มไหม เพราะไม้ที่ชนะ
                 ของ Scoring วิ่งเฉลี่ยถึง +2.25R แต่เก็บได้จริงแค่ 30% ของนั้น (ตัวเลขก่อน
                 ปิดกฎ Climax — 2026-09-13 วัดใหม่ได้ 56%)
     --rule-halfway-keep=N  ทับ exit_monitor.RULE_HALFWAY_KEEP (ปกติ 60) — กฎ "เดินทาง >=50%
                 ไป TP แล้วตัด 40%" ซึ่งยิงใส่เฉพาะไม้ที่กำลังวิ่งเข้าหา TP = ไม้ที่ชนะ
     --tp-cap-atr=X  ทับ config.TP_MAX_ATR (ปกติ 18) — เพดานระยะ TP เป็นเท่าของ ATR ตอนเข้าไม้
                 ใส่ 0 เพื่อปิดเพดานทิ้ง (= พฤติกรรมก่อน 2026-09-11 ไว้เทียบ base)
                 ⚠️ ค่า default = ของระบบจริง รันเปล่าๆ จึงมีเพดานติดมาด้วยแล้ว ไฟล์ผลจะติด tag
                 เฉพาะรอบที่สวนค่า (_tpcapatr15 / _notpcap) รอบที่ตรงกับระบบจริงได้ชื่อไฟล์เปล่า
     --max-runup-24h=X  ทับ config.MAX_RUNUP_24H_R (ปกติ 0.5) — ข้ามรอบนั้นถ้าราคา "วิ่งไปทางที่กำลังจะเข้า" มาแล้วเกิน X R ใน 24 แท่ง
                 1H ก่อนหน้า (ใช้กับทาง Scoring เท่านั้น) — มาจากการวัดไม้ Scoring 90 ไม้ 8 symbol:
                   ย่อลงมาหาเรา (<0)  10 ไม้ WR 60.0% +5.53R   วิ่งไปแล้ว 0-0.3R  32 ไม้ WR 40.6% -4.18R
                   วิ่งไปแล้ว 0.3-0.6R 44 ไม้ WR 31.8% -4.77R   >0.6R              4 ไม้ WR 50.0% +0.20R
                 ยิ่งวิ่งไปก่อนแล้ว SL ยิ่งห่าง (1.89% -> 3.07%) R:R แผนยิ่งแคบ (4.28 -> 2.86)
                 ⚠️ ตารางแบบนี้เคยหลอกมาแล้ว (ดู --min-sl) การตั้งเกณฑ์ไม่ได้ตัดไม้แย่ทิ้งเฉยๆ
                 แต่เลื่อนจังหวะเข้าไปรอบถัดไปซึ่งอาจแย่กว่าเดิม ต้องวัดทั้งระบบเท่านั้น
     --structure-break  **เปิดกลับ** กฎ "ราคาปิด 4H หลุด Swing ล่าสุด -> ออก 100% ทันที"
                 ซึ่งตอนนี้ปิดอยู่ในระบบจริงแล้ว (exit_monitor.STRUCTURE_BREAK_ENABLED = False
                 ดูตัวเลขที่ทำให้ปิดที่นั่น) — ไว้เทียบย้อนกลับ/วัดซ้ำเมื่อมีข้อมูลเพิ่ม
     --no-structure-break  บังคับปิดกฎ (ตอนนี้เป็นค่า default อยู่แล้ว — เก็บไว้ให้สคริปต์เก่า
                 ที่ส่งธงนี้มายังทำงานได้เหมือนเดิม)
     --no-trend-invalidate  ปิดกฎ "Daily ปิดสวน trend ติดกัน -> ตัด 25/50/100%" ทั้งชุด
                 (ไม้ Reversal ข้ามกฎนี้อยู่แล้ว มีผลเฉพาะฝั่ง Scoring) — ไว้ตอบว่ากฎที่ตัดไม้
                 ทิ้ง 12 ครั้งแลกกลับมา -0.5R นั้นคุ้มไหม
                 ⚠️ สองตัวนี้คือกฎที่ยังไม่ถูกทดสอบ หลังจาก --rule-1r-keep พิสูจน์แล้วว่ากฎ 1R
                 ไม่ใช่ตัวที่กินกำไรไม้ใหญ่ (ปิดกฎ 1R ไปเลย ไม้ TP ก้อนใหญ่ไม่ขยับสักไม้)
     --div-max-age=N  ทับ DIV_MAX_AGE_BARS (ปกติ 20) — swing ของ divergence เก่าได้กี่แท่ง
     --div-price-tol=X  ผ่อนการเทียบ "ราคาทำ new extreme" ได้ X ATR (ปกติ 0 = เทียบเป๊ะ)
                 ATR(14) บน 4H ณ แท่งของจุด swing ใหม่ · ไม่แตะรายการ swing = เพิ่มไม้อย่างเดียว
                 เคยวัดที่ 0.5 แล้ว ΔR -0.78 (ดู regime_check.DIV_PRICE_TOLERANCE_ATR)
     --adx-decline-bars=N  ทับ ADX_DECLINE_BARS (ปกติ 3) — ADX ต้องลงติดกันกี่แท่งจาก peak
                 ⚠️ ไม่ใช่ subset สะอาด — เลื่อนเวลาเข้าไม้ = เปลี่ยนการครองช่อง churn เยอะ
     --div-stall=N  ทับ DIV_STALL_THRESHOLD (ปกติ 5) — RSI สวนได้กี่แต้มถึงยังนับเป็น divergence
                 0 = strict ล้วน · เลขมาก = หลวมขึ้น · ไม่แตะรายการ swing เหมือนกัน
                 ⚠️ ค่า 5 ปัจจุบันมาจาก sample 3-6 เคส ยังไม่เคยกวาด — ใส่ 5 ในชุดกวาดเป็น
                 identity check ด้วยเสมอ
     --div-rsi-period=N  ทับ DIV_RSI_PERIOD (ปกติ 20) — period ของ RSI ที่ใช้หา divergence
                 ไม่กระทบ exit_monitor.RSI_PERIOD (ดู --exit-rsi-period)
     --exit-rsi-period=N  ทับ exit_monitor.RSI_PERIOD (ปกติ 20) — คุมกฎ "Indicator ร้อน"
                 ที่ตัด RULE_HOT_KEEP เมื่อ RSI แตะ 70/30 หรือราคาทะลุ Bollinger
                 (ขา Bollinger ไม่ขยับตาม BB_PERIOD เป็น 20 ของมันเอง คนละตัวกัน)
     --sl-guard-legacy  ให้ด่านตรวจ SL ใช้ close แท่ง 4H ล่าสุดแทนราคาเข้าไม้จริง
                 = พฤติกรรมก่อน 2026-09-12 (ดู swing.find_sl_from_structure) ไว้เทียบผลการแก้บั๊ก
     --div-no-volume  หา swing สำหรับ divergence โดยไม่กรอง volume/wick
                 สองตัวนี้คลายด่าน Divergence ซึ่งเป็นด่านที่ตัดโอกาส Reversal ทิ้งมากที่สุด
                 (มีผลกับ regime ด้วย: REVERSAL-WATCH จะกลายเป็น REVERSAL-READY มากขึ้น)
     --live-spread  อ่าน spread สดจากโบรกแทนค่าที่ตรึงใน config.SPREAD_PCT_BY_SYMBOL
                 ⚠️ ผลของรอบนั้นจะขึ้นกับ "เวลาที่กดรัน" และเทียบกับรอบอื่นไม่ได้ (ไฟล์ติด tag
                 _livespread) — ค่าตรึงมีไว้ให้ A/B ข้ามรอบเชื่อถือได้ ดูเหตุผลเต็มที่ config
     --max-hold=N  ทับ exit_monitor.MAX_HOLD_DAYS (ปกติ 30) — เพดานเวลาถือไม้ 0 = ปิดเพดาน
                 เพดานนี้เคยวัดตัวเองไม่ได้: backtest ตั้งเลขไว้เองตั้งแต่ก่อนมันเข้าระบบจริง
                 ทุกรอบที่เคยรันจึงสมมติว่ามีเพดานอยู่แล้ว (ไฟล์ผลติด tag _nomaxhold / _maxholdN)
     --slow-calendar  ให้ SLOW_TRADE_DAYS นับวันปฏิทิน (รวมเสาร์-อาทิตย์) แบบก่อน 2026-09-22
                 ระบบจริงนับวันทำการแล้ว — ธงนี้คือ "ของเดิม" ไว้วัดส่วนต่าง (tag _slowcal)
                 ชุดไม้ไม่เปลี่ยน (กฎตัดแต่ขนาด ไม่คืนช่อง) จึงเทียบ direct แบบ paired ได้
     --tp-cooldown=N  ทับ config.TP_COOLDOWN_HOURS (ระบบจริง 3 ตั้งแต่ 2026-09-25) — ห้ามเปิดไม้ใหม่
                 ใน symbol นี้ N ชม. หลังไม้ใดก็ตามชน TP · 0 = ปิด · tag _tpcdN เมื่อสวนค่าระบบจริง
                 N นับจากรอบสแกนแรกที่เห็นไม้ปิด: N = 1 ข้าม 1 รอบ เข้าได้เร็วสุดรอบที่ 2
                 ⚠️ ใช้คู่กับ --same-scan-reentry เสมอ ไม่งั้น N = 1 เกือบเป็น no-op (replay
                 ข้ามรอบแรกหลัง SL/TP อยู่แล้วโดยโครงสร้าง ส่วนระบบจริงไม่ข้าม)
     --end=YYYY-MM-DDTHH:MM  ตรึงปลายหน้าต่าง (เวลา MT5 = UTC) — ใส่ค่าเดียวกันทุกรอบที่จะ diff กัน
                 ไม่งั้นโปรเซสที่ได้ข้อมูลค้างจะได้หน้าต่างต่างออกไปเงียบๆ (เกิดจริง 2026-09-25)
     --limit-rr=X --limit-hours=H  จำลอง limit order ให้ setup Reversal ที่ติดด่าน R:R — ตั้งที่ราคา
                 ที่ R:R = X ค้าง H ชม. (default 12) · ระบบจริงไม่มี · tag _limitrrXhH
                 H = 0 = identity check · กติกาเต็มที่จุด parse ธง
     --be-ladder=T:L[,T:L]  ขั้นบันไดต่อจาก BE — ถึง T R ล็อก SL ที่ +L R (exit_monitor.BREAKEVEN_LADDER)
                 tag _beladderT-L · 99:0 = identity check
     --trail-tp-buffer=X / --trail-tp-trigger=X  ทับ exit_monitor.TRAIL_TP_ATR_BUFFER (0.5 · 0 = ปิด
                 TP trailing) / TRAIL_TP_TRIGGER_PCT (1.0) · tag _trailtpbufX / _trailtptrigX
     --same-scan-reentry  ไม้ที่ broker ปิด (SL/BE/TP) คืนช่องในรอบสแกนนั้นเลย = ตรงกับ scheduler
                 ที่อ่าน positions_get ตอนสแกน (tag _samescan · ที่มาดูตรงที่ parse ธง)
"""
import re
import sys
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from mt5_connect import connect
import config
from config import (MT5_TIMEFRAMES, MAX_DAILY_LOSS, RISK_PER_TRADE,
                    COOLDOWN_HOURS_BY_SYMBOL, SPREAD_PCT_BY_SYMBOL,
                    get_min_sl_distance_pct)
import config as cfg
import scoring
from scoring import compute_entry, get_trend_bias, get_ohlcv, get_ohlcv_real, calc_rr
from bars import BAR_OFFSET_H
from regime_check import get_regime
import exit_monitor as em
import reversal

# อ่านจาก config ที่เดียว (2026-09-15) — เดิมเป็นสำเนา literal พร้อม comment "ตรงกับ scheduler.py"
from config import REGIME_NO_TRADE, REGIME_TREND, REGIME_REVERSAL

# เพดานถือไม้ — 2026-09-14: อ่านจาก exit_monitor แทนการตั้งเลขเอง เพราะเพดานนี้เข้าระบบจริงแล้ว
# (เดิมมีแต่ใน backtest ตั้งไว้กันไม้ค้างกินเวลารัน = backtest กับระบบจริงทำคนละอย่าง)
# ตั้งค่าที่ exit_monitor.MAX_HOLD_DAYS ที่เดียว ที่นี่แค่ตามมัน
#
# --max-hold=N : ทับเพดานเฉพาะรอบนี้ (0 = ปิดเพดาน ปล่อยไม้เดินจนกว่าจะชน SL/TP/กฎอื่น)
# มีไว้เพราะเพดานนี้ **วัดตัวเองไม่ได้มาตลอด** — backtest ตั้งเลขนี้ไว้เองตั้งแต่ก่อนมันเข้าระบบ
# จริง ทุกรอบที่รันจึงสมมติว่ามีเพดานอยู่แล้ว ไม่มีรอบไหนเทียบกับ "ไม่มีเพดาน" ได้เลย หลักฐาน
# เดียวที่เคยมีมาจาก backtest_trade_sim ซึ่ง **มองไม่เห็นช่องถือไม้** = มองไม่เห็นต้นทุนหลักของ
# การไม่มีเพดาน (ไม้ที่ไม่ถูกปิดครองช่องต่อ มัธยฐาน 37 วัน) ต้องผ่านตรงนี้เท่านั้นถึงจะวัดครบ
# ต้อง set ทั้งสองที่: em.MAX_HOLD_DAYS คุมข้อ 0 ของ checklist (analyze_position อ่านตอนถูกเรียก)
# ส่วนตัวล่างคุมการปิดไม้ของ step_position เอง
_mh_arg = next((a for a in sys.argv if a.startswith("--max-hold=")), None)
if _mh_arg:
    # 0 = ปิดเพดาน — ใช้เลขใหญ่แทน inf เพราะ timedelta(days=inf) โยน OverflowError
    em.MAX_HOLD_DAYS = float(_mh_arg.split("=", 1)[1]) or 1e6
MAX_HOLD_DAYS   = em.MAX_HOLD_DAYS


class SimPos:
    """แทน position object ของ MT5 ให้ analyze_position() อ่านได้ — ไม้จำลองไม่มีใน MT5/journal"""
    def __init__(self, symbol, direction, entry, sl, tp, lot, t):
        self.symbol, self.price_open, self.sl, self.tp = symbol, entry, sl, tp
        self.volume, self.ticket = lot, 0
        self.time = int(pd.Timestamp(t).timestamp())
        self.type = mt5.POSITION_TYPE_BUY if direction == "Long" else mt5.POSITION_TYPE_SELL

symbol   = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
days     = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 730
use_cost  = "--no-cost" not in sys.argv
no_widen  = "--no-widen" in sys.argv
legacy_sl = "--legacy-sl" in sys.argv   # ตั้ง scoring.EXEC_SL_ATR_MULT = 0 ให้ด้านล่าง

# --min-sl=X : ทับเกณฑ์ระยะ SL ขั้นต่ำของ symbol นี้ — get_min_sl_distance_pct() อ่าน dict
# ตอนถูกเรียกทุกครั้ง การแก้ตรงนี้จึงมีผลกับ compute_entry/reversal ทันทีโดยไม่ต้องแก้ config.py
_min_sl_arg = next((a for a in sys.argv if a.startswith("--min-sl=")), None)
if _min_sl_arg:
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL = dict(config.MIN_SL_DISTANCE_PCT_BY_SYMBOL)
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL[symbol] = float(_min_sl_arg.split("=")[1])

# --legacy-sl : ไม่ขยับ SL ออกจาก SL โครงสร้างเลย (พฤติกรรมก่อน 2026-08-27) — ตั้งที่เดียว
# แล้วมีผลทั้ง exec_sl ที่ส่ง "broker" และด่าน R:R ใน compute_entry พร้อมกัน
if legacy_sl:
    scoring.EXEC_SL_ATR_MULT = 0.0

# --rev-min-sl / --rev-tp-from-entry : สวิตช์ทดลองของทาง Reversal อย่างเดียว — compute_reversal_entry
# อ่านสองตัวนี้จาก reversal ตอนถูกเรียกทุกครั้ง การ set ตรงนี้จึงมีผลทันทีโดยไม่ต้องแก้ reversal.py
_rms_arg = next((a for a in sys.argv if a.startswith("--rev-min-sl=")), None)
if _rms_arg:
    reversal.MIN_SL_OVERRIDE = float(_rms_arg.split("=")[1])
# สองตัวนี้อ่านค่าเริ่มต้นจาก config เพื่อให้ backtest ตรงกับระบบจริงเสมอ — flag มีไว้สลับ
# ทดลองเท่านั้น (--one-slot / --no-rev-short-1d = ย้อนกลับไปพฤติกรรมก่อนหน้า)
slot_per_strategy = config.SLOT_PER_STRATEGY
if "--slot-per-strategy" in sys.argv:
    slot_per_strategy = True
if "--one-slot" in sys.argv:
    slot_per_strategy = False
rev_short_1d = config.REVERSAL_SHORT_NEEDS_1D_TREND   # Reversal Short ต้องมีเทรนด์ 1D หนุน
if "--rev-short-1d" in sys.argv:
    rev_short_1d = True
if "--no-rev-short-1d" in sys.argv:
    rev_short_1d = False
no_rev_short = "--no-rev-short" in sys.argv            # ปิดฝั่ง Short ของ Reversal ทิ้งเลย
# --breakout : ไม้ Reversal Short ที่ถูก REVERSAL_SHORT_NEEDS_1D_TREND กัก ให้**กลับข้าง
# เป็น Long** แทนการทิ้ง (เฉพาะตอนเทรนด์ 1D = Long เท่านั้น · bias = None ยังทิ้งเหมือนเดิม
# เพราะไม่มีอะไรหนุนทั้งสองทาง) ดูเหตุผล/หลักฐานเต็มที่จุดใช้งานในลูปหลัก
# ต้องใช้คู่กับด่านที่เปิดอยู่ — ถ้าสั่ง --no-rev-short-1d ด้วย ธงนี้จะไม่มีผลเพราะไม่มีไม้ถูกกัก
_BREAKOUT_LIVE = cfg.BREAKOUT_ENABLED          # ค่าระบบจริง (config = แหล่งเดียว)
breakout_mode = _BREAKOUT_LIVE
if "--breakout" in sys.argv:
    breakout_mode = True
if "--no-breakout" in sys.argv:
    breakout_mode = False
# --breakout-tp=X : อัตราส่วน Fibonacci ที่ **ไม้ Breakout เท่านั้น** ใช้วาง TP
# (ไม้ Scoring/Reversal ยังใช้ config.TP_FIB_RATIO = 1.618 เหมือนเดิม ไม่ถูกแตะ)
# ที่มา 2026-09-21: ไม้ Breakout เกิดตอนราคาเพิ่งทำยอดใหม่ จุด B ของ fib (= swing low ล่าสุด)
# จึงอยู่ใกล้ราคามาก -> TP ที่ 1.618 ตกมาอยู่แค่เอื้อม ขณะที่ SL อยู่ใต้ก้นจริง = R:R พังทันที
# เคสจริง BTCUSDm 2025-05-13 20:00 (หลังตั้ง wick 0.48 ให้เห็นก้นจริง):
#   entry 104,794 · SL 99,182 (1R = 5,613) · TP@1.618 = 105,298 (ห่างแค่ 504 จุด) -> R:R 0.09
#   ถูก MIN_RR_HARD_BLOCK (1.5) ตัดทิ้ง
# 🔴 **ยังไม่มีหลักฐานว่าดีกว่า** และมีหลักฐานที่ขัดอยู่ 2 ชิ้น อ่านก่อนเชื่อผล:
#   1) ไม้ Breakout 31 ไม้ (วัด 2026-09-20) **ไม้ที่แพ้มี R:R แผนสูงกว่าไม้ที่ชนะ**
#      (เฉลี่ย 2.90 vs 2.44) = TP ไกลไม่ได้แปลว่าดี ในกลุ่มนี้มันสัมพันธ์กับการแพ้ด้วยซ้ำ
#   2) การยืด TP ทำให้ไม้ที่เคยถูกด่าน R:R ตัดทิ้ง **กลับเข้ามาได้** = ผลิต R:R ด้วยการย้ายเป้า
#      ไม่ใช่ด้วยการหาจุดเข้าที่ดีขึ้น ตรงกับรูปแบบ "ด่านที่รอให้เงื่อนไขดีขึ้น พังทุกตัว"
#      ที่บันทึกไว้ที่ config.MIN_RR_HARD_BLOCK
_botp_arg = next((a for a in sys.argv if a.startswith("--breakout-tp=")), None)
BREAKOUT_TP_FIB_RATIO = float(_botp_arg.split("=")[1]) if _botp_arg else cfg.BREAKOUT_TP_FIB_RATIO
# --tp-cap-r=X : เพดานระยะ TP เป็นเท่าของความเสี่ยง (ดูที่จุดใช้งานในลูปหลัก)
_tpc_arg = next((a for a in sys.argv if a.startswith("--tp-cap-r=")), None)
tp_cap_r = float(_tpc_arg.split("=")[1]) if _tpc_arg else None
# --tp-cap-atr=X : เพดานระยะ TP เป็นเท่าของ ATR ตอนเข้าไม้ (คนละแกนกับ --tp-cap-r)
# 2026-09-11: **default = config.TP_MAX_ATR ไม่ใช่ None** — ตั้งแต่เพดานนี้เข้าระบบจริงแล้ว
# การรัน replay เปล่าๆ ต้องได้พฤติกรรมเดียวกับระบบจริง ไม่งั้นเครื่องมือที่ใช้ตัดสินใจจะวัด
# คนละสูตรกับของจริงเงียบๆ (เคยเกิดมาแล้วกับ TP_FIB_RATIO ที่ backtest ค้างที่ 0.786 อยู่ 11 วัน
# ดู comment ที่ config.TP_FIB_RATIO) — ใส่ --tp-cap-atr=0 เพื่อปิดเพดานสำหรับรอบทดลอง
_tpa_arg = next((a for a in sys.argv if a.startswith("--tp-cap-atr=")), None)
tp_cap_atr = float(_tpa_arg.split("=")[1]) if _tpa_arg else config.TP_MAX_ATR
if not tp_cap_atr:          # 0 / None = ปิด (เทียบกับ base ที่ไม่มีเพดาน)
    tp_cap_atr = None
# --max-sl-atr=X : เพดานความกว้างของ SL เป็นเท่าของ ATR (ดูที่จุดใช้งานในลูปหลัก)
_msa_arg = next((a for a in sys.argv if a.startswith("--max-sl-atr=")), None)
max_sl_atr = float(_msa_arg.split("=")[1]) if _msa_arg else None
# --log-cuts : บันทึกทุกครั้งที่กฎ Position Sizing สั่งปิดบางส่วน พร้อมว่ากฎไหนยิง — ไว้วัดว่า
# กฎแต่ละตัวคุ้มไหม (ไม้ที่ชนะเก็บได้แค่ 38% ของกำไรสูงสุดที่เคยมี — 2026-09-13 วัดใหม่หลังปิด
# กฎ Climax ได้ 56% แล้ว ดู config.TP_MAX_ATR)
# ไม่เปลี่ยนพฤติกรรมอะไรเลย แค่เขียน log เพิ่ม
log_cuts = "--log-cuts" in sys.argv
cut_log = []
# --log-blocked : รอบที่ข้ามเพราะ "ช่องไม่ว่าง" ให้เดินด่านที่เหลือต่อจนสุด แล้วบันทึกไว้ว่า
# ถ้าช่องว่างรอบนั้นจะได้ไม้อะไร (ไม่เปิดไม้จริง ผลของ replay จึงไม่เปลี่ยนแม้แต่ไม้เดียว)
# ไว้ตอบคำถามที่ไม่มีเครื่องมือไหนตอบได้: **ค่าเสียโอกาสของการถือช่องไว้นาน** ซึ่งเป็น
# เหตุผลเดียวที่กฎอย่าง slow trade มีอยู่ — เอาไฟล์ผลไปเดินต่อด้วย backtest_blocked_value.py
log_blocked = "--log-blocked" in sys.argv
# --log-rr-blocked : บันทึกทุกรอบสแกนที่ setup Reversal (ไม่ใช่ flip) ถูกด่าน R:R ปฏิเสธ พร้อม SL/TP/ATR
# -> replay_rrblocked_<sym><tag>.csv · บันทึกอย่างเดียว ไม่แตะการตัดสินใจ (2026-09-26 ใช้ดูว่าไม้ที่ติด
# R:R หน้าตาเป็นยังไง ก่อนคิดวิธีแก้ — ข้อมูลเดิมจากรอบ --limit-rr เห็นเฉพาะที่ตั้ง order ได้)
log_rr_blocked = "--log-rr-blocked" in sys.argv
rr_blocked_log = []
blocked_log = []
# --scoring-min-rr=X : ทับด่าน R:R ขั้นต่ำ **เฉพาะทาง Scoring** (ระบบจริง config.MIN_RR_HARD_BLOCK = 1.5 ใช้ร่วม
# กับ Breakout) · ไม่แตะ Breakout และไม่แตะตัวคูณ TP fallback (ดู scoring.compute_entry min_rr) · tag _scoringminrrX
_smr_arg = next((a for a in sys.argv if a.startswith("--scoring-min-rr=")), None)
scoring_min_rr = float(_smr_arg.split("=")[1]) if _smr_arg else None
# --rev-tp-ratio=X : ทับ config.REVERSAL_TP_FIB_RATIO (อัตราส่วน Fibonacci ของ TP ไม้ Reversal) · tag _revtpX
_rtr_arg = next((a for a in sys.argv if a.startswith("--rev-tp-ratio=")), None)
if _rtr_arg:
    reversal._TP_RATIO_OVERRIDE = float(_rtr_arg.split("=")[1])
_rmr_arg = next((a for a in sys.argv if a.startswith("--rev-min-rr=")), None)
if _rmr_arg:
    reversal._MIN_RR_OVERRIDE = float(_rmr_arg.split("=")[1])

rev_tp_entry = "--rev-tp-from-entry" in sys.argv
if rev_tp_entry:
    reversal.TP_FROM_ENTRY = True

# --div-max-age / --div-no-volume : คลายด่าน Divergence — check_divergence อ่านค่าจาก regime_check
# ตอนถูกเรียกทุกครั้ง (จาก get_regime) การ set ตรงนี้จึงมีผล
# กับทั้งการจัด regime และสกอร์การ์ดพร้อมกัน เหมือนแก้ค่าคงที่จริงแต่เฉพาะรอบนี้
import regime_check
import sideway as _sideway_mod
# --sideway / --no-sideway : กลยุทธ์ Sideway (config.SIDEWAY_ENABLED = default · กฎอยู่ที่ sideway.py)
# เข้าได้เฉพาะรอบที่ regime อยู่ใน REGIME_NO_TRADE และ ADX 4H < ADX_CHOPPY (Reversal มาก่อน) ·
# ช่องเดียวกับ Scoring (config.slot_of) · R ของไม้ Sideway = หน่วยความเสี่ยงของตัวเอง (1%) คอลัมน์
# risk_w = SIDEWAY_RISK_PER_TRADE / RISK_PER_TRADE (0.5) ไว้แปลงเป็น R ของระบบ (2%) ตอนรวมยอด
sideway_enabled = (config.SIDEWAY_ENABLED or "--sideway" in sys.argv) and "--no-sideway" not in sys.argv
_SIDEWAY_W = config.SIDEWAY_RISK_PER_TRADE / config.RISK_PER_TRADE
# --struct-reg : เปลี่ยน check_structure จาก "เทียบ swing high/low" เป็น "ความชัน regression + R²"
# ดูเหตุผล/พารามิเตอร์เต็มที่ regime_check.check_structure_reg — พารามิเตอร์ (N ราย symbol จาก
# คลื่นราคา x2 · R2_MIN 0.5) **ประกาศไว้ก่อนรัน ห้ามขยับหลังเห็นผล**
# ผลติด tag _structreg ที่ชื่อไฟล์ผล base จึงไม่ถูกทับ
_STRUCT_REG_LIVE = regime_check.USE_STRUCT_REG      # ค่าระบบจริง เก็บไว้ก่อนถูกทับ (ไว้ติด tag)
struct_reg = _STRUCT_REG_LIVE
if "--struct-reg" in sys.argv:
    struct_reg = True
if "--swing-struct" in sys.argv:                    # ย้อนกลับไปใช้ check_structure แบบ swing
    struct_reg = False
regime_check.USE_STRUCT_REG = struct_reg
# --struct-reg-n=X / --struct-reg-r2=X : ทับ N และ R2_MIN ของรอบนั้น (ใช้ได้เพราะ replay รัน
# ทีละ symbol) — ค่า default มาจาก regime_check.STRUCT_REG_N / STRUCT_REG_R2_MIN
_srn_arg = next((a for a in sys.argv if a.startswith("--struct-reg-n=")), None)
_srr_arg = next((a for a in sys.argv if a.startswith("--struct-reg-r2=")), None)
if struct_reg:
    if _srn_arg:
        regime_check.STRUCT_REG_N = dict(regime_check.STRUCT_REG_N)
        regime_check.STRUCT_REG_N[sys.argv[1]] = int(_srn_arg.split("=")[1])
        regime_check.STRUCT_REG_N_DEFAULT = int(_srn_arg.split("=")[1])
    if _srr_arg:
        regime_check.STRUCT_REG_R2_MIN = float(_srr_arg.split("=")[1])
_DIV_AGE_LIVE = regime_check.DIV_MAX_AGE_BARS      # ค่าของระบบจริง เก็บไว้ก่อนถูกทับ ใช้ตัดสินว่า
_DIV_RSI_LIVE = regime_check.DIV_RSI_PERIOD        # รอบนี้ "สวนค่าระบบจริง" หรือไม่ (ไว้ติด tag)
_dma_arg = next((a for a in sys.argv if a.startswith("--div-max-age=")), None)
if _dma_arg:
    regime_check.DIV_MAX_AGE_BARS = int(_dma_arg.split("=")[1])

# --div-max-lookback=N : ทับ DIV_MAX_LOOKBACK_BARS (ปกติ 180) — คู่เทียบ divergence (h1) ถอย
# ย้อนหลังได้ไกลสุดกี่แท่ง · ดูที่มาทั้งหมดที่ regime_check.DIV_MAX_LOOKBACK_BARS
# 🟢 เป็น **subset แท้**: ลดแล้วทำได้แค่ "เคยเจอ -> ไม่เจอ" ไม่มีทางไปเบียดคู่ของใคร (ต่างจาก
#    wick/vol/tolerance ที่เปลี่ยน*รายชื่อ* swing แล้วสลับคู่) = ปุ่ม divergence ที่สะอาดที่สุด
# 👉 ใส่ 180 ในชุดกวาดด้วยเสมอ = identity check ฟรี (ต้องออกมาเท่า base เป๊ะทุกไม้)
_dml_arg = next((a for a in sys.argv if a.startswith("--div-max-lookback=")), None)
_DIV_LOOKBACK_LIVE = regime_check.DIV_MAX_LOOKBACK_BARS
if _dml_arg:
    regime_check.DIV_MAX_LOOKBACK_BARS = int(_dml_arg.split("=")[1])
# --div-min-spacing=N : ทับ DIV_MIN_SPACING_BARS (ปกติ 5) — คู่เทียบ divergence ต้องห่างจาก
# จุดล่าสุดอย่างน้อยกี่แท่ง ถ้าใกล้กว่านี้ให้ถอยไปหาจุดก่อนหน้าแทน (ไม่ข้ามทิ้ง)
# 🔴 **ไม่ใช่ subset แท้ ต่างจาก --div-max-lookback** — ขยับแล้วมัน **สลับคู่** ไม่ใช่ทำให้
#    "เจอ -> ไม่เจอ" เฉยๆ: divergence ยังเจอเหมือนเดิมแต่เทียบกับคนละจุด -> SL คนละที่ ->
#    คนละไม้ = กลไกเดียวกับเคส wick ที่ได้ |t| 2.93 แล้วตีความผิดอยู่เป็นเดือน
#    อ่านผลด้วยกติกาข้อ 3b (ดู "ไม้ที่หายเป็นแบบไหน" ก่อนยอดรวม) เสมอ
# ⚠️ 2026-09-23: ค่า 5 ถูกตั้งตอน DIV_MAX_LOOKBACK_BARS ยังเป็น 180 = หน้าต่าง [5,180] กว้าง
#    175 แท่ง พื้นแทบไม่มีความหมาย · ตอนนี้เพดานเป็น 20 แล้ว หน้าต่างเหลือ [5,20] = พื้นกิน
#    พื้นที่ 1 ใน 4 **บทบาทของค่านี้เปลี่ยนไปโดยไม่มีใครตั้งใจ**
# 👉 ใส่ 5 ในชุดกวาดด้วยเสมอ = identity check ฟรี (ต้องออกมาเท่า base เป๊ะ)
_dms_arg = next((a for a in sys.argv if a.startswith("--div-min-spacing=")), None)
if _dms_arg:
    regime_check.DIV_MIN_SPACING_BARS = int(_dms_arg.split("=")[1])
# --div-rsi-period=N : ทับ regime_check.DIV_RSI_PERIOD เฉพาะรอบนี้
# (เดิมขยับเกณฑ์ "RSI extreme" ของสกอร์การ์ด Reversal ไปพร้อมกันด้วย — สกอร์การ์ดถูกลบแล้ว
#  2026-09-25 ตอนนี้ธงนี้คุม divergence อย่างเดียว)
_drp_arg = next((a for a in sys.argv if a.startswith("--div-rsi-period=")), None)
if _drp_arg:
    regime_check.DIV_RSI_PERIOD = int(_drp_arg.split("=")[1])
    # ⚠️ ตั้งตัวแปรโมดูลอย่างเดียว **ไม่พอ** — regime_check.calc_rsi ประกาศว่า
    # `def calc_rsi(series, period=DIV_RSI_PERIOD)` default ถูกผูกค่าไว้ตั้งแต่ตอน def
    # การแก้ตัวแปรทีหลังจึงไม่มีผลกับคนที่เรียกแบบไม่ส่ง period (check_divergence เรียกแบบนั้น)
    # ต้องแก้ที่ __defaults__ ของตัวฟังก์ชันเอง
    regime_check.calc_rsi.__defaults__ = (regime_check.DIV_RSI_PERIOD,)
# --sl-guard-legacy : ให้ด่านตรวจ SL (swing.find_sl_from_structure) กลับไปใช้ close ของแท่ง 4H
# ล่าสุดแทนราคาที่จะเข้าไม้จริง = พฤติกรรมก่อน 2026-09-12 ไว้วัดว่าการแก้บั๊กนั้นคุ้มไหม
# (ราคา 4H เก่าได้ถึง 3 ชม. ทำให้ด่าน "ราคาทะลุ swing เกิน 0.22 ATR" หลวมกว่าที่เขียนไว้มาก)
import swing as _swing_mod
sl_guard_legacy = "--sl-guard-legacy" in sys.argv
if sl_guard_legacy:
    _swing_mod.USE_ENTRY_AS_CURRENT_PRICE = False

# --swing-recency=X : ให้ collapse_swing_runs เลือก "จุดล่าสุดของรัน" แทน "จุดสุดขั้ว" ถ้าจุดล่าสุด
# แพ้ไม่เกิน ATR × X — ทุก caller อ่านค่าจาก swing ตอนถูกเรียก การ set ตรงนี้จึงมีผลทันทีทั้ง 6 จุด
# ⚠️ **เปลี่ยนชุดไม้** ไม่ใช่แค่ทางออก: ขยับ swing = ขยับ SL = เปลี่ยนระยะ 1R = เปลี่ยน R:R =
# เปลี่ยนว่าไม้ไหนผ่าน MIN_RR_HARD_BLOCK  ห้ามวัดด้วย backtest_exit_rules/backtest_trade_sim
# ⚠️ ทิศทางของมันทำให้ตัวเลขดูดีขึ้นเอง (จุดล่าสุดใกล้ราคากว่า -> SL แคบลง -> R:R ดีขึ้น ->
# ผ่านด่านง่ายขึ้น = ได้ไม้เพิ่ม) ตอนอ่านผลต้องแยก direct/churn เสมอ อย่าอ่าน TotalR ตรงๆ
# ⚠️ X=0 ไม่ใช่ no-op (เคสราคาเสมอจะสลับไปใช้จุดล่าสุด) ตัวตรวจ identity คือ "ไม่ใส่ธงนี้"
_swr_arg = next((a for a in sys.argv if a.startswith("--swing-recency=")), None)
if _swr_arg:
    _swing_mod.SWING_RECENCY_TOL_ATR = float(_swr_arg.split("=", 1)[1])

# --div-wick-gold-only : กลับไปพฤติกรรมก่อน 2026-09-15 (check_divergence ให้ wick เฉพาะทอง)
# ไว้เทียบกับ default ปัจจุบันที่เปิดให้ทุก symbol — ดู regime_check.DIV_WICK_ALL_SYMBOLS
div_wick_gold = "--div-wick-gold-only" in sys.argv
if div_wick_gold:
    regime_check.DIV_WICK_ALL_SYMBOLS = False

# --div-zone-legacy : กลับไปใช้โซน 55/45 (ค่าที่จูนบน RSI(14) ก่อน 2026-09-16)
div_zone_legacy = "--div-zone-legacy" in sys.argv
if div_zone_legacy:
    regime_check.DIV_ZONE_OVERBOUGHT, regime_check.DIV_ZONE_OVERSOLD = 55, 45

# --div-price-tol=X : ผ่อนการเทียบ "ราคาทำ new extreme" ได้ X ATR (ปกติ 0 = เทียบเป๊ะ)
# ดู regime_check.DIV_PRICE_TOLERANCE_ATR — ไม่แตะรายการ swing จึงเพิ่มไม้ได้อย่างเดียว
_dpt_arg = next((a for a in sys.argv if a.startswith("--div-price-tol=")), None)
if _dpt_arg:
    regime_check.DIV_PRICE_TOLERANCE_ATR = float(_dpt_arg.split("=")[1])

# --div-stall=N : ทับ DIV_STALL_THRESHOLD (ปกติ 5) — RSI ขึ้น/ลงสวนได้ไม่เกินกี่แต้มถึงยังนับ
# เป็น divergence · 0 = strict ล้วน (ต้องเป็น LH/HL จริงเท่านั้น) · เลขมาก = หลวมขึ้น
# เหมือน --div-price-tol ตรงที่ทดสอบกับคู่ swing ที่ _find_spacing_partner เลือกมาแล้ว
# **ไม่แตะรายการ swing** จึงไม่เบียดของเดิม (ดู DIV_WICK_ALL_SYMBOLS ว่ากรณีเบียดเป็นยังไง)
# ⚠️ ค่า 5 ที่ใช้อยู่มาจาก sample 3-6 เคสบน BTC เท่านั้น ยังไม่เคยกวาดจริงสักครั้ง
# 👉 ใส่ --div-stall=5 ในชุดกวาดด้วยเสมอ = identity check ฟรี (ต้องออกมาเท่า control เป๊ะ)
_dst_arg = next((a for a in sys.argv if a.startswith("--div-stall=")), None)
if _dst_arg:
    regime_check.DIV_STALL_THRESHOLD = float(_dst_arg.split("=")[1])

# --adx-decline-bars=N : ทับ ADX_DECLINE_BARS (ปกติ 3)
# 🔴 2026-09-18: บรรทัดนี้เคยเขียนว่า "ปกติ 4 ตั้งแต่ 2026-09-17" ซึ่ง **ไม่จริง** — 4 ถูกลอง
# แล้วย้อนกลับ (ดู config.REVERSAL_NEEDS_CHOCH ที่อ้างถึง "ADX_DECLINE_BARS 4 ที่ลองแล้วย้อน")
# ค่าจริงใน regime_check.py คือ 3 มาตลอด · เจอตอนไล่ว่าทำไม XAU เข้าไม้ 2026-09-17 12:50
# ⚠️ ตัวนี้ **ไม่ใช่ subset สะอาด** แบบ --div-stall — มันเลื่อนเวลาที่ regime ปล่อยให้เข้าไม้
# = เปลี่ยนว่าใครครองช่องเมื่อไหร่ = churn เยอะกว่ามาก ต้องดู direct/churn แยกเสมอ
_adb_arg = next((a for a in sys.argv if a.startswith("--adx-decline-bars=")), None)
if _adb_arg:
    regime_check.ADX_DECLINE_BARS = int(_adb_arg.split("=")[1])

# --adx-min-peak=N : ทับ ADX_MIN_PEAK_REVERSAL (ปกติ 28.5) — 0 = ปิดด่าน (พฤติกรรมก่อน 2026-09-17)
# ⚠️ ไม่ใช่ subset สะอาด — แท่งที่ตกด่านนี้ไหลไป branch TREND ได้ = Scoring อาจเพิ่ม
# --trail-extreme[=MULT] : เปิด trailing ที่เกาะจุดสูงสุด/ต่ำสุดหลังเข้าไม้ (ดู exit_monitor)
# default ปิด = พฤติกรรมเดิม (pinned base ที่ swing ตอนเข้า ซึ่งขึ้นไม่ถึง entry โดยโครงสร้าง)
_tex_arg = next((a for a in sys.argv if a == "--trail-extreme" or a.startswith("--trail-extreme=")), None)
if _tex_arg:
    em.TRAIL_FROM_EXTREME = True
    if "=" in _tex_arg:
        em.TRAIL_EXTREME_ATR_MULT = float(_tex_arg.split("=")[1])

# --adx-dir-bars=N / --adx-dir-eps=X : ไม้บรรทัดที่ตัดสินว่าเส้น ADX "ขึ้น/ทรง/ลง"
#   diff = ADX[แท่งปิดล่าสุด] − ADX[ย้อน N แท่ง] · |diff| < EPS = "ทรง" (ซึ่งผ่านด่าน TREND
#   เท่ากับ "ขึ้น") · diff <= −EPS = "ลง" = เขตเทา = ไม่เข้าไม้   ของจริง 4 / 1.0
# ⚠️ **ยิ่งแน่น (N มากขึ้น หรือ EPS น้อยลง) = ตัดไม้ออก ไม่ใช่เพิ่มไม้** — คัดกรองบนไม้ 145 ไม้
#   ของ base (adx_dir_at_entry.py 2026-09-18) ว่าไม้ที่จะถูกตัดทิ้งทำเงินได้เท่าไหร่:
#     5/0.5 -> ตัด 25 ไม้ที่รวมกัน **+5.81R** (= เสีย)   5/1.0 -> ตัด 13 ไม้ −0.96R (= ได้)
#     6/1.0 -> ตัด 19 ไม้ +0.60R         8/1.0 -> ตัด 24 ไม้ +1.03R
#   ทุกช่อง |t| <= 1.01 (SE = 1.15×√n) = **ไม่มีช่องไหนแยกจากศูนย์ได้** อย่าคาดหวังผลจากแกนนี้
# ⚠️ เปลี่ยนชุดไม้ (ตัดไม้ = คืนช่อง = ไม้อื่น backfill) -> replay เต็มเท่านั้น ตัวเลขข้างบน
#   เป็นแค่การคัดกรอง ไม่ได้นับไม้ที่จะเข้ามาแทน
_adb2_arg = next((a for a in sys.argv if a.startswith("--adx-dir-bars=")), None)
if _adb2_arg:
    regime_check.ADX_DIR_BARS = int(_adb2_arg.split("=")[1])
_ade_arg = next((a for a in sys.argv if a.startswith("--adx-dir-eps=")), None)
if _ade_arg:
    regime_check.ADX_DIR_EPS = float(_ade_arg.split("=")[1])

# --tp-lock[=KEEP] : หลังกฎ BE ยิงแล้ว ให้ SL ไต่ขึ้นตามระยะทางที่เดินไปหา TP (ดู exit_monitor)
# default ปิด = พฤติกรรมเดิม (SL แช่ที่ entry ตลอดอายุไม้ที่เหลือ)  KEEP=0 = no-op เป๊ะ
_tpl_arg = next((a for a in sys.argv if a == "--tp-lock" or a.startswith("--tp-lock=")), None)
if _tpl_arg:
    em.TP_PROGRESS_LOCK = True
    if "=" in _tpl_arg:
        em.TP_PROGRESS_LOCK_KEEP = float(_tpl_arg.split("=")[1])

_amp_arg = next((a for a in sys.argv if a.startswith("--adx-min-peak=")), None)
if _amp_arg:
    _v = float(_amp_arg.split("=")[1])
    regime_check.ADX_MIN_PEAK_REVERSAL = _v if _v > 0 else None

# --breakout-no-min-peak : ด่าน peak ≥ ADX_MIN_PEAK_REVERSAL ใช้กับ Reversal เท่าเดิม แต่ **Breakout ไม่ต้องผ่าน**
# (ไอเดียผู้ใช้ 2026-09-30 หลังกวาด peak: ปิดด่านทั้งใบได้ +13.52R ซึ่ง Breakout ให้ +18.12R ขณะที่ Reversal
#  ใหม่ 74 ไม้ได้แค่ +0.39R) — ทำโดย: รอบที่ regime ไม่ใช่ REVERSAL-READY แต่จะเป็นถ้าไม่มีเกณฑ์ peak
# (reversal_nopeak) + divergence bearish + เทรนด์ 1D Long = ทางที่จะถูก flip เป็น Breakout แน่ๆ
# -> ถือว่าเป็น REVERSAL-READY รอบนั้น ไหลเข้าทาง flip ตามปกติ · รอบอื่นใช้ regime เดิม (Scoring ไม่ถูกแย่ง
# ยกเว้นรอบที่ Breakout เกิดจริง — ตรงกับรอบปิดด่านที่ branch Reversal มาก่อน TREND)
# 🔻 วัดแล้ว 2026-09-30 (10 symbol · --end=2026-09-24T00:00): 216 -> 247 ไม้ · **+16.73R** · Reversal ไม่ขยับ
#    Scoring direct 0 · Breakout 29 -> 60 ไม้ (+19.64 -> +36.98R) · ไม้ใหม่ 32 (1 ไม้คือ XAU Scoring เดิม
#    +3.06 ที่เปลี่ยนป้ายเป็น Breakout) · churn SE 9.59 -> **|t| 1.74** · ตัด 5 ไม้ใหญ่ (HK50 4.29 · UKOIL 3.74 ·
#    UKOIL 3.10 · XAU 2.50 · XAU Scoring 2.45) เหลือ **+0.65R** · ดีขึ้น 7/10 (BTC −4.15 แพ้ 4/4)
#    = ผ่านเกณฑ์ข้อ 3 แค่ข้อ symbol · Long ล้วนในช่วงทอง/ดัชนี/น้ำมันขาขึ้น
# 2026-09-30: เข้าระบบจริงแล้วที่ config.BREAKOUT_IGNORES_MIN_PEAK (= default ของที่นี่) · ปิดด้วย --no-breakout-no-min-peak
breakout_no_min_peak = (config.BREAKOUT_IGNORES_MIN_PEAK or "--breakout-no-min-peak" in sys.argv) \
    and "--no-breakout-no-min-peak" not in sys.argv

# --rev-no-adx-floor / --rev-adx-floor : Reversal ไม่ต้อง/ต้องผ่านพื้น ADX ≥ 22 เหลือแค่ peak ≥ ADX_MIN_PEAK_REVERSAL + โค้งลง
# (ดู regime_check.REVERSAL_USES_ADX_FLOOR)
# 2026-09-30: ระบบจริงเอาพื้นออกแล้ว (default = False) · --rev-adx-floor = ใส่พื้นกลับเพื่อเทียบ
_rev_floor_default = regime_check.REVERSAL_USES_ADX_FLOOR
if "--rev-no-adx-floor" in sys.argv:
    regime_check.REVERSAL_USES_ADX_FLOOR = False
if "--rev-adx-floor" in sys.argv:
    regime_check.REVERSAL_USES_ADX_FLOOR = True

# --rev-choch / --no-rev-choch : ทับ config.REVERSAL_NEEDS_CHOCH — Reversal ต้องเห็นโครงสร้าง
# เดิมพัง (CHoCH) ก่อนเข้าไหม · ใช้ exit_monitor.check_structure_break ตัวเดียวกับกฎออก
rev_choch = cfg.REVERSAL_NEEDS_CHOCH
if "--rev-choch" in sys.argv:
    rev_choch = True
if "--no-rev-choch" in sys.argv:
    rev_choch = False

# --scoring-struct-match / --no-scoring-struct-match : ทับ config.SCORING_NEEDS_STRUCTURE_MATCH
# ทิศ Scoring (trend_flip 1D) ต้องตรงกับโครงสร้าง 4H ไหม — เหตุผล+ตัวเลขอยู่ที่ค่าคงที่นั้น
scoring_struct_match = cfg.SCORING_NEEDS_STRUCTURE_MATCH
if "--scoring-struct-match" in sys.argv:
    scoring_struct_match = True
if "--no-scoring-struct-match" in sys.argv:
    scoring_struct_match = False

div_no_vol = "--div-no-volume" in sys.argv
if div_no_vol:
    regime_check.DIV_SWING_VOL_FILTER = False

# --exit-rsi-period=N : ทับ exit_monitor.RSI_PERIOD เฉพาะรอบนี้ — คุมกฎ "Indicator ร้อน" (ข้อ 2)
# ที่ตัด RULE_HOT_KEEP เมื่อ RSI แตะ RSI_OVERBOUGHT/OVERSOLD **หรือ** ราคาทะลุ Bollinger
# ขา Bollinger ไม่ขยับตาม (BB_PERIOD เป็น 20 ของมันเอง) กฎจึงยังยิงจากขานั้นเท่าเดิม
# ต่างจาก --div-rsi-period ตรงที่ไม่ต้อง patch __defaults__: analyze_position:554 เรียก
# calc_rsi(df["close"], RSI_PERIOD) โดยส่ง period มาตรงๆ และอ่าน global ตอนถูกเรียกทุกครั้ง
# (ยังแก้ __defaults__ ของ em.calc_rsi ให้ด้วย เผื่ออนาคตมีคนเพิ่ม caller ที่ไม่ส่ง period)
_EXIT_RSI_LIVE = em.RSI_PERIOD          # ค่าระบบจริง เก็บก่อนถูกทับ
_erp_arg = next((a for a in sys.argv if a.startswith("--exit-rsi-period=")), None)
if _erp_arg:
    em.RSI_PERIOD = int(_erp_arg.split("=")[1])
    em.calc_rsi.__defaults__ = (em.RSI_PERIOD,)

# --rule-1r-keep : ทับกฎ Position Sizing ข้อ 1 ของ exit_monitor — analyze_position อ่านค่าจาก
# โมดูลตอนประกอบ position_rules ทุกครั้งที่ถูกเรียก การ set ตรงนี้จึงมีผลกับทุกไม้ในรอบนี้
_1rk_arg = next((a for a in sys.argv if a.startswith("--rule-1r-keep=")), None)
if _1rk_arg:
    em.RULE_1R_KEEP = float(_1rk_arg.split("=")[1])
# --rule-climax-keep / --rule-hot-keep : สองกฎที่เหลือของชุด Position Sizing (100 = ปิดกฎนั้น)
# วัดแล้วว่า Climax เป็นตัวที่กินกำไรมากที่สุด: ยิง 118 ครั้งตอน R เฉลี่ย -0.06 (คือตัดตอนไม้
# ยังติดลบ ไม่ใช่ล็อกกำไร) และทำให้เสียโอกาสรวม +12.53R จากทั้งชุด +15.39R
_clk_arg = next((a for a in sys.argv if a.startswith("--rule-climax-keep=")), None)
if _clk_arg:
    em.RULE_CLIMAX_KEEP = float(_clk_arg.split("=")[1])
_hotk_arg = next((a for a in sys.argv if a.startswith("--rule-hot-keep=")), None)
if _hotk_arg:
    em.RULE_HOT_KEEP = float(_hotk_arg.split("=")[1])
# --climax-only-in-profit : ให้กฎ Climax ยิงเฉพาะตอนไม้กำไรอยู่ (ดูเหตุผลที่ exit_monitor.py)
climax_in_profit = "--climax-only-in-profit" in sys.argv
if climax_in_profit:
    em.CLIMAX_ONLY_IN_PROFIT = True
_hwk_arg = next((a for a in sys.argv if a.startswith("--rule-halfway-keep=")), None)
if _hwk_arg:
    em.RULE_HALFWAY_KEEP = float(_hwk_arg.split("=")[1])
# --slow-r / --slow-days / --slow-keep : กฎ slow trade (ถือครบ N วันแล้วยังไม่ถึง R -> เหลือ keep%)
# analyze_position อ่านทั้งสามค่าจากโมดูลตอนถูกเรียกทุกครั้ง การ set ตรงนี้จึงมีผลทันที
#   --slow-r=0     = ตัดเฉพาะไม้ที่ **ติดลบ** ที่วันที่ N (เลิกยุ่งกับไม้ที่กำไรอยู่แต่ไปช้า)
#   --slow-keep=0  = ปิดไม้ 100% (คืนช่องถือไม้จริง ต่างจาก 50 ที่ช่องยังไม่ว่าง)
for _flag, _attr, _cast in (("--slow-r=",     "SLOW_TRADE_R",    float),
                            ("--slow-days=",  "SLOW_TRADE_DAYS", float),
                            ("--slow-keep=",  "SLOW_TRADE_KEEP", float)):
    _a = next((a for a in sys.argv if a.startswith(_flag)), None)
    if _a:
        setattr(em, _attr, _cast(_a.split("=", 1)[1]))
        # กฎถูกปิดเป็น default ตั้งแต่ 2026-09-23 (em.SLOW_TRADE_ENABLED = False) — การส่งธง
        # ใดธงหนึ่งของชุดนี้แปลว่าตั้งใจจะวัดกฎนี้ จึงเปิดให้อัตโนมัติ ไม่งั้นทุกค่าจะเป็น no-op
        em.SLOW_TRADE_ENABLED = True
# --slow-calendar : ย้อนกลับไปนับ "วันปฏิทิน" แบบก่อน 2026-09-22 (รวมเสาร์-อาทิตย์)
# ระบบจริงนับวันทำการแล้ว (ดู exit_monitor.market_days_held) ธงนี้มีไว้วัดส่วนต่างของสองนิยาม
# เท่านั้น — กฎตัดแค่ขนาดไม้ ไม่คืนช่อง **ชุดไม้จึงไม่เปลี่ยน** เทียบ direct (paired) ได้เลย
slow_calendar = "--slow-calendar" in sys.argv
if slow_calendar:
    em.SLOW_TRADE_SKIP_WEEKENDS = False
# ปิดกฎ trend invalidation: คง code path เดิมไว้ทุกบรรทัด แค่ให้ทุกระดับความต่อเนื่องคืน 100%
# ปิดกฎ structure break: patch ที่ตัวฟังก์ชันเลย — analyze_position เรียกผ่านชื่อใน module
# globals ทุกครั้ง การแทนที่ตรงนี้จึงมีผลทันทีโดยไม่ต้องแก้ exit_monitor.py
_runup_arg = next((a for a in sys.argv if a.startswith("--max-runup-24h=")), None)
# default มาจาก config.MAX_RUNUP_24H_R (ค่าที่ระบบจริงใช้) — ธงมีไว้ทับเฉพาะรอบทดลองเท่านั้น
max_runup = float(_runup_arg.split("=")[1]) if _runup_arg else config.MAX_RUNUP_24H_R

# --min-turn=X : ทับ config.MIN_TURN_FROM_EXTREME_R (ปกติ None = ปิด) — กันเข้าไม้ตรงจุดสุดขั้ว
# 24 ชม. พอดี (ดูที่มา/ตัวเลขคัดกรองทั้งหมดที่ config.MIN_TURN_FROM_EXTREME_R) · 0 = ปิดด่าน
_mt_arg = next((a for a in sys.argv if a.startswith("--min-turn=")), None)
min_turn = float(_mt_arg.split("=")[1]) if _mt_arg else config.MIN_TURN_FROM_EXTREME_R
if min_turn == 0:
    min_turn = None

# --reject-cooldown=N : ทับ config.REJECT_COOLDOWN_HOURS — พอด่านปฏิเสธ setup แล้วล็อกทิศนั้น
# ไว้ N ชม. กันสัญญาณเดิมกลับเข้ามา (ที่มา/ตัวเลขทั้งหมดที่ config.REJECT_COOLDOWN_HOURS)
_rcd_arg = next((a for a in sys.argv if a.startswith("--reject-cooldown=")), None)
reject_cd = float(_rcd_arg.split("=")[1]) if _rcd_arg else config.REJECT_COOLDOWN_HOURS
if not reject_cd:
    reject_cd = None
reject_until = {}     # {direction: เวลาที่ปลดล็อก} — อายุเท่ากับการรัน 1 symbol

# --tp-cooldown=N : พักทั้ง symbol N ชม. หลังไม้ชน TP (2026-09-24 ผู้ใช้ขอวัด)
# ขอบเขต = ทั้ง symbol ทุกกลยุทธ์ทุกทิศ เหมือน journal.check_cooldown ตัวเดิม — ไม่แยกทิศเพราะ
# คัดกรองจาก base แล้ว ไม้ที่เปิดตามหลัง TP ภายใน 1 สัปดาห์เป็นทิศเดิม 20/22 = แยกไปก็ได้ชุดเดียวกัน
# "TP" = ราคาแตะ pos["tp"] ระหว่างแท่ง (รวม TP ที่ถูก TP trailing ดึงเข้ามาแล้ว) ซึ่งคือสิ่งที่ระบบ
# จริงจะเห็นเป็นดีล reason=TP · 0 = no-op ไว้เป็น identity check
# 2026-09-25: เข้าระบบจริงแล้วที่ config.TP_COOLDOWN_HOURS (= default ของที่นี่) ธงไว้ทับเท่านั้น
_tpcd_arg = next((a for a in sys.argv if a.startswith("--tp-cooldown=")), None)
tp_cd = float(_tpcd_arg.split("=")[1]) if _tpcd_arg else float(config.TP_COOLDOWN_HOURS or 0)
last_tp_close_time = None

# --same-scan-reentry : ให้ไม้ที่ **broker ปิด** (SL/BE/TP ระหว่างแท่ง) คืนช่องทันทีในรอบสแกนนั้น
# 🔴 2026-09-24 เจอว่า replay กับระบบจริงไม่ตรงกันตรงนี้: scheduler อ่าน positions_get ตอนสแกน
# ไม้ที่ชน TP ตอน 10:37 จึงหายไปแล้วตอนสแกน 11:00 = **เปิดไม้ใหม่ได้ที่ 11:00** แต่ replay
# snapshot ช่องก่อนเดินไม้ (`_occupied`) เลยเห็นช่องยังไม่ว่างที่ 11:00 เข้าได้เร็วสุด 12:00
# = replay มี "cooldown แฝง 1 รอบ" หลัง SL/BE/TP ทุกไม้ ที่ระบบจริงไม่มี
# (comment ที่ scheduler.py:195 ว่า "ตรงกับ backtest_replay" จริงเฉพาะไม้ที่ exit_monitor ปิด
#  ตอนสแกน ซึ่ง live ก็อ่าน occupied ก่อนรัน monitor เหมือนกัน — ธงนี้จึงไม่แตะไม้กลุ่มนั้น)
# default ยังปิดไว้ เพราะเปิดแล้วเปลี่ยนชุดไม้ของ base ทั้งระบบ — วัดก่อนแล้วค่อยตัดสินใจ
# วัดแล้ว 2026-09-24 (7 symbol ไม่รวม ETH ที่รันไม่จบเพราะ MT5 หลุด): **ต่างกันแค่ 2 ไม้ +0.30R**
# (XAU 20:00 แทน 22:00 · US500 17:00 แทน 18:00) = ความคลาดนี้ไม่ได้ทำให้ตัวเลข base เพี้ยน
same_scan_reentry = "--same-scan-reentry" in sys.argv

# --entry-limit=X : แทน market order ด้วย limit order ห่างจากราคาตลาด X·R (R = ระยะถึง SL ที่ส่ง broker)
# อายุ 1 ชม. = แท่ง 1H ถัดไปแท่งเดียว (ผู้ใช้ 2026-09-30 ต่อจาก --perfect-entry ที่ได้เพดาน +29.98R)
# เติม = แท่งนั้นแตะ L (เติมที่ L เสมอ ไม่นับ gap ที่ได้ราคาดีกว่า) · SL/TP คงราคาเดิม -> ระยะ 1R แคบลง
# ไม่เติม = ทิ้งสัญญาณรอบนั้น ช่องยังว่าง รอบสแกนถัดไปเช็คสัญญาณใหม่ตามปกติ (= บอทตั้ง limit ใหม่ได้ถ้ายังค้าง)
# แท่งที่เติมแตะ SL ด้วย = นับโดน SL ทันที (มองร้าย เหมือน --limit-rr) · ด่านทุกตัวคิดที่ราคาตลาดเหมือนเดิม
_el_arg = next((a for a in sys.argv if a.startswith("--entry-limit=")), None)
entry_limit = float(_el_arg.split("=")[1]) if _el_arg else 0.0
# --entry-limit-atr=X : เหมือน --entry-limit แต่ระยะเป็น X·ATR 1H (pinned_atr_entry) แทน X·R — 2026-10-01 เพราะ
# limit แบบ R ไม่เติมเป็นระบบเมื่อ SL กว้างเทียบ ATR (SL/ATR บนสุด 1/3 วิ่งหนี 57% vs 27-30%) ใช้คู่ --entry-limit-fallback ได้
# --entry-oco-atr=X : ตั้งสองคำสั่งพร้อมกัน (ผู้ใช้ 2026-10-01 "แบบ ก") — limit ด้านหน้า (Long ต่ำกว่าราคา X·ATR)
# + stop ด้านหลัง (Long สูงกว่าราคา X·ATR) อายุ 1 ชม. ฝั่งไหนโดนก่อนเข้าฝั่งนั้น ยกเลิกอีกฝั่ง
# แท่ง 1H บอกลำดับในแท่งไม่ได้ -> โดนทั้งสองฝั่ง = นับเติมฝั่ง stop (ราคาแย่ = มองร้าย)
# ไม่โดนเลย (อยู่ในกรอบทั้งชั่วโมง) = เข้า market ปลายชั่วโมง → ไม่มีสัญญาณไหนพลาด
_eoa_arg = next((a for a in sys.argv if a.startswith("--entry-oco-atr=")), None)
entry_oco_atr = float(_eoa_arg.split("=")[1]) if _eoa_arg else 0.0
# --entry-oco-both=limit : โดนทั้งสองฝั่งในแท่งเดียว -> นับเติมฝั่ง limit แทน (default = stop/มองร้าย)
# มีไว้คร่อมคำตอบสองข้าง เพราะ M1/M5 ของโบรกย้อนได้แค่ไม่กี่เดือน (ตรวจ 2026-10-01: XAU M5 ไม่มีก่อน ~2025)
# 🔻 วัดแล้ว 2026-10-01 (10 symbol · --end=2026-09-24T00:00 · base 247 ไม้ +127.07R):
#    limit 0.3 ATR ไม่ไล่ (--entry-limit-atr=0.3 · หลังแก้ bool)  236 ไม้ +124.57R  ΔR  −2.50  |t| 0.11  เติม 239/416 (57%)
#    OCO 0.3 ATR มองร้าย (โดนสองฝั่ง = stop)                  247 ไม้ +114.30R  ΔR −12.77  |t| 2.18  ดีขึ้น 1/10
#    OCO 0.3 ATR มองดี   (โดนสองฝั่ง = limit · --entry-oco-both=limit) 247 ไม้ +128.21R  ΔR  +1.14  |t| 0.15
#    👉 คำตอบจริงของ OCO อยู่ระหว่าง −12.77 ถึง +1.14R = กรณีดีที่สุดก็แค่เสมอ market · ไม่เอา ใช้ market ต่อ
entry_oco_both = next((a.split("=")[1] for a in sys.argv if a.startswith("--entry-oco-both=")), "stop")
_ela_arg = next((a for a in sys.argv if a.startswith("--entry-limit-atr=")), None)
entry_limit_atr = float(_ela_arg.split("=")[1]) if _ela_arg else 0.0
# 🔻 วัดแล้ว 2026-10-01 (10 symbol · --end=2026-09-24T00:00 · base 247 ไม้ +127.07R · เพดาน --perfect-entry +29.98R):
#    แบบ                      ไม้  ΣR       ΔR      |t|   ตัด 5 ไม้ใหญ่  ดีขึ้น  เติม/สัญญาณ
#    limit 0.05R ไม่ไล่        237  +118.04   −9.03  0.40   −5.06        4/10   239/457 (52%)
#    limit 0.1R  ไม่ไล่        205   +99.80  −27.27  0.96  −23.94        2/10   208/932 (22%)
#    limit 0.05R + ไล่ market  248  +120.58   −6.50  0.78   +1.71        3/10   เติม 155 · ไล่ 95
#    limit 0.1R  + ไล่ market  249  +109.35  −17.72  1.94   −9.50        2/10   เติม 80 · ไล่ 171
#    👉 **แพ้ market ทุกแบบ** · ไม่ไล่ = ไม้ที่วิ่งหนีทันที (ไม้ดี) หายไป · ไล่ = ไม้พวกนั้นได้ราคาแย่ลงเกินกว่าที่
#    ไม้ที่เติมได้ราคาดีขึ้น (direct −9.09 / −19.22R) · +30R ของ --perfect-entry เก็บจริงไม่ได้ด้วย limit แบบนี้
entry_limit_fallback = "--entry-limit-fallback" in sys.argv   # ไม่เติมใน 1 ชม. -> เข้า market ปลายชั่วโมง (ผู้ใช้ 2026-09-30)
entry_limit_stats = {"เติม": 0, "ไม่เติม": 0, "เติมแล้วโดน SL แท่งเดียวกัน": 0,
                     "ไม่เติม -> market ปลายชั่วโมง": 0, "ไม่เติม · แตะ TP แล้วไม่ไล่": 0}
entry_limit_missed = []

# --perfect-entry : **การทดลองทางความคิด ใช้ข้อมูลอนาคต (lookahead) โดยตั้งใจ — ห้ามใช้เป็น base**
# ไม้ที่ผ่านทุกด่านที่ราคาตลาด (SL/TP/ด่าน R:R คิดที่ราคาตลาดเหมือนเดิม) แต่ "เติม" ที่ราคาดีที่สุดของแท่ง 1H
# ถัดไป (Long = low · Short = high) — ถามว่า "ถ้าเข้าแม่นที่สุดในชั่วโมงนั้นจะได้เพิ่มเท่าไหร่" (ผู้ใช้ 2026-09-30)
# R คิดจากราคาเติม = ระยะ 1R แคบลง ขาดทุนเต็มยังเป็น 1R เท่าเดิม (lot ใหญ่ขึ้น) และกฎ exit ทุกตัวทำงานจริง
# (BE 1.5R ยิงเร็วขึ้นตามระยะที่แคบลง) · ชั่วโมงที่ราคาทะลุ SL = ไม่นับ เติมที่ราคาตลาดเดิม
# 🔻 วัดแล้ว 2026-09-30 (10 symbol · --end=2026-09-24T00:00): 247 -> 250 ไม้ · **+127.07 -> +157.05R (+29.98R · +24%)**
#    direct +25.26R (ไม้ตรงกัน 244) · churn +4.72R (ใหม่ 6 · หาย 3 = จังหวะคืนช่อง = noise)
#    BE ยิงเร็วขึ้นตามที่คาด: วิธีจบเปลี่ยน 17 ไม้ (SL -> BE 7 · TP -> BE 5) หักล้างกันเหลือ −0.54R
#    ค่าประมาณแบบคงราคาออก (ไม่มีกฎ exit) ได้ +34.21R = คลาดจากของจริงแค่ ~4R · WR 57.9 -> 61.2%
#    👉 = **เพดาน** ของทุกวิธีเข้าไม้ที่ละเอียดกว่า 1H (limit / TF เล็ก) ทำได้ไม่เกินนี้
perfect_entry = "--perfect-entry" in sys.argv
perfect_log = []

# --reverse-on-opposite : ไม้ใหม่เปิดสวนทิศไม้ที่ถืออยู่ในอีกช่อง (Scoring vs Reversal/Breakout)
# -> ปิดไม้เก่า 100% ที่ราคาเข้าของไม้ใหม่ในรอบสแกนเดียวกัน แล้วเปิดไม้ใหม่ตามปกติ (= กลับทิศ)
# ระบบจริงไม่มีสิ่งนี้: SLOT_PER_STRATEGY เช็คแค่ช่องว่าง ไม่ดูทิศ จึงถือสองไม้สวนกันค้างไว้ (hedge)
# 2026-09-30 base 10 symbol: เกิด 14 ครั้ง · ไม้ใหม่ +10.02R · ไม้เก่า +4.20R
# ไม้ที่ถูกปิดได้ label "REVERSE" · ปิดกฎนี้ = no-op (ไม่ใส่ธง = base เป๊ะ)
# 🔻 วัดแล้ว 2026-09-30 (7 symbol ที่มีคู่สวน + HK50 identity = 0.00 เป๊ะ · --end=2026-09-24T00:00):
#    **−8.54R · แย่ลง 5/7** · direct 16 ไม้ −4.80R |t| 0.98 · churn ไม้ใหม่ 4 ไม้ −3.77R (แพ้ 4/4)
#    ไม้เก่าที่ถูกตัดส่วนใหญ่ยังจะไปถึง TP (BTC 2.41->0.02 · EUR 2.71->0.59) — สัญญาณสวนไม่ได้แปลว่า
#    ไม้เก่าผิดทาง · GBPCHF กลับไปกลับมาเป็นลูกโซ่ (Reversal/Scoring สลับกันทุกไม่กี่ชั่วโมง)
#    👉 ไม่เอา — คง hedge ไว้ตามระบบจริง
reverse_on_opposite = "--reverse-on-opposite" in sys.argv

# --tp-entry-on-opposite : ไม้ใหม่เปิดสวนทิศไม้ที่ถืออยู่ในอีกช่อง และไม้เก่า **ขาดทุนอยู่** ณ ราคาเข้า
# ของไม้ใหม่ -> ย้าย TP ของไม้เก่ามาไว้ที่ entry ของมันเอง (ออกเสมอตัวถ้าราคากลับมา ไม่งั้นรอ SL เดิม)
# ไอเดียผู้ใช้ 2026-09-30 จากคู่ HK50 จริง: TP ของ Reversal Long (25,618) อยู่เหนือ SL ของ Scoring
# Short (24,899) = ไม้เก่าจะชนะได้ก็ต่อเมื่อไม้ใหม่แพ้ · ไม้เก่าที่กำไรอยู่ไม่แตะ (TP ที่ entry
# จะอยู่ผิดฝั่งราคา broker ปฏิเสธ) · TP trailing ของ exit_monitor ratchet เข้าอย่างเดียว จึงไม่ดึงกลับ
# 🔻 วัดแล้ว 2026-09-30 (7 symbol ที่มีคู่สวน · --end=2026-09-24T00:00): **−0.47R · direct 6 ไม้ |t| 0.27
#    churn 0** = ไม่มีผล · เปลี่ยนวิธีจบจริงแค่ 3 ไม้ และหักล้างกันเอง: GBPCHF ไม้ที่จะโดน SL −1.06
#    ออกที่ 0 · อีกไม้ที่จะได้ TP +1.18 ก็ออกที่ 0 · EUR +0.38 -> 0 · ไม้เก่าที่ขาดทุนตอนไม้สวนเปิด
#    ส่วนใหญ่ไม่กลับมาแตะ entry เลย (จบ SL เหมือนเดิม) 👉 ไม่เอา
# 2026-09-30: เข้าระบบจริงแล้วที่ config.TP_TO_ENTRY_ON_OPPOSITE (= default ของที่นี่) · ปิดด้วย --no-tp-entry-on-opposite
tp_entry_on_opposite = (config.TP_TO_ENTRY_ON_OPPOSITE or "--tp-entry-on-opposite" in sys.argv) \
    and "--no-tp-entry-on-opposite" not in sys.argv

# --limit-rr=X --limit-hours=H : จำลอง limit order ให้ setup Reversal ที่ติดด่าน R:R (2026-09-25
# ไอเดียผู้ใช้) — ระบบจริงส่งแต่ market order จึงไม่มีสิ่งนี้ ธงนี้ถามว่า "ถ้ามีจะได้ไม้เพิ่มกี่ไม้ กี่ R"
# ที่มา: REVERSAL-READY 2,303 ชม. ใน 2 ปี ตายที่ R:R ต่ำกว่า 1.15 ถึง 48% เพราะ divergence ยืนยัน
# ตอนราคาเด้งออกจากจุดสุดขั้วไปแล้ว entry จึงห่าง SL · limit รอให้ราคาย่อกลับมาที่ R:R = X
# กติกา (เลียนแบบ pending order ที่โบรก):
#   ตั้ง   — ที่รอบสแกนที่ compute_reversal_entry ปฏิเสธ และ R:R ที่ราคาตลาด < ขั้นต่ำ (ติด R:R จริง
#            ไม่ใช่ด่านอื่น) · ราคา L = (TP + X·SL) / (1+X) จาก SL ที่ส่ง broker กับ TP เดิม
#            (สองตัวนี้ไม่ขึ้นกับราคาเข้า) · ชน TP_MAX_ATR เมื่อไหร่แก้สมการด้วย TP ที่ถูกเพดาน
#            แล้วรัน compute_reversal_entry ซ้ำที่ L — ด่านไหนไม่ผ่านที่ราคานั้นก็ไม่ตั้ง
#   เติม   — แท่งไหน low/high แตะ L (โบรกเติมกลางแท่ง ไม่ต้องรอสแกน) · เปิด gap เลย L = เติมที่ open
#            แท่งเดียวกันแตะ SL ด้วย = นับว่าโดน SL ทันที (มองร้ายไว้ก่อน)
#   ยกเลิก — ครบ H ชม. · ราคาแตะ TP ก่อนเติม (ยกเลิกตอนสแกน) · ระบบเข้าไม้ market ในช่องเดียวกันได้เอง
# ไม่แตะพฤติกรรมเดิม: ไม้ market ทุกไม้ยังเกิดเหมือนเดิม limit ตั้งเฉพาะรอบที่ market ถูกปฏิเสธ
# เฉพาะ Reversal ที่ไม่ใช่ flip (Breakout ใช้ด่าน R:R ของ Scoring คนละชุด) · 1 order ค้างได้ต่อช่อง
# --limit-hours=0 = ตั้งแล้วหมดอายุก่อนแท่งถัดไป = identity check ของ code path
# 🔻 วัดแล้ว 2026-09-26 (8 symbol · --end=2026-09-24T00:00 · identity h0 ตรง base เป๊ะ US500/HK50):
#    R:R 2 · 24 ชม.: ตั้งไม่ได้ 647/864 ครั้ง (ราคา limit ชิด SL จนต่ำกว่า MIN_SL) · เติม 2 ไม้ SL ทั้งคู่ −2.14R
#    R:R 1.5 · 24 ชม.: ตั้ง 285 · เติม 12 ไม้ +3.47R (TP 6 · SL 5 · t 0.81) แต่ไปแย่งช่องไม้เดิม
#                     ทั้งระบบ **−1.05R** · อายุ 12 ชม. ≈ 8 ไม้ +1.58R · 4 ชม. ≈ 2 ไม้ +0.40R
#    🔴 ข้อค้นพบหลัก: 85% ของ order ที่ตั้งได้ ราคาอยู่ที่/เลย TP ไปแล้วตั้งแต่ตอนตั้ง (R:R ตลาด ≤ 0.05)
#       = setup Reversal ที่ "ติด R:R" ส่วนใหญ่คือการเคลื่อนไหวที่จบไปแล้วตอน divergence ยืนยัน
#       ไม่ใช่ราคาเด้งเลยจุดเข้าไปนิดเดียว ด่าน R:R บล็อกถูกแล้ว ไม่มีอะไรให้ limit เก็บ
#    👉 ไม่ทำ limit ในระบบจริง (งานใหญ่ · ได้ไม้เพิ่ม ~6 ไม้/ปี ที่แยกจาก noise ไม่ได้)
_lrr_arg = next((a for a in sys.argv if a.startswith("--limit-rr=")), None)
_lh_arg = next((a for a in sys.argv if a.startswith("--limit-hours=")), None)
limit_rr = float(_lrr_arg.split("=")[1]) if _lrr_arg else None
limit_hours = float(_lh_arg.split("=")[1]) if _lh_arg else 12.0
pending = None           # limit order ที่ค้างอยู่ (ช่อง Reversal) — dict หรือ None
limit_log = []           # ทุก order ที่ตั้ง + จบยังไง -> replay_limits_<sym><tag>.csv (บันทึกอย่างเดียว)
limit_stats = {"ตั้ง": 0, "เติม": 0, "เติมแล้วโดน SL แท่งเดียวกัน": 0, "หมดอายุ": 0,
               "ยกเลิก: แตะ TP ก่อน": 0, "ยกเลิก: market เข้าเอง": 0,
               "ไม่ตั้ง: ด่านไม่ผ่านที่ราคา limit": 0}
no_struct_break = "--no-structure-break" in sys.argv
if no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = False
if "--structure-break" in sys.argv and not no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = True
no_trend_inval = "--no-trend-invalidate" in sys.argv
if no_trend_inval:
    em.TREND_CHECK_KEEP_BY_CONSEC = {k: 100 for k in em.TREND_CHECK_KEEP_BY_CONSEC}
# --no-breakeven : ปิดการบังคับเลื่อน SL ไป entry (checklist ข้อ 5)
#   จุดที่ยิงอ่านจาก exit_monitor.BREAKEVEN_TRIGGER_R — **1.5R ตั้งแต่ 2026-09-22** ไม่ใช่ 1R
# ⚠️ **ไม่ใช่ "ปล่อยให้ ATR trailing คุมแทน"** อย่างที่เคยเขียนไว้ตรงนี้ — วัดแล้วได้ SL ที่แคบลง
# 0 ไม้จาก 188 เพราะ trailing ตรึงฐานที่ swing ตอนเข้า ขึ้นไม่ถึง entry โดยโครงสร้าง
# (ดู BREAKEVEN_ENABLED ที่ exit_monitor) รอบนี้จึงวัด "ไม่มีการดึง SL ให้แคบลงเลย"
# analyze_position อ่าน BREAKEVEN_ENABLED จากโมดูลตอนถูกเรียกทุกครั้ง
# ⚠️ ต้องวัดที่นี่ ไม่ใช่แค่ backtest_exit_rules: ปิด BE ทำให้ไม้ที่เคยจบที่ศูนย์เดินต่อจนถึง
# TP/SL = **ครองช่องนานขึ้น** ซึ่งเป็นสิ่งเดียวที่เครื่องมือตัวนั้นมองไม่เห็นตามนิยามของมัน
no_breakeven = "--no-breakeven" in sys.argv
if no_breakeven:
    em.BREAKEVEN_ENABLED = False

# --be-trigger=X : R ที่กฎ BE เริ่มยิง (ปัจจุบัน 1.5) · --be-level=X : จุดที่ SL ไปนั่งเทียบ entry
# ทั้งสองตัวต้องวัดที่นี่ ไม่ใช่ backtest_exit_rules ด้วยเหตุผลเดียวกับ --no-breakeven ข้างบน:
# มันเปลี่ยนว่าไม้จบเมื่อไหร่ = ครองช่องนานขึ้น/สั้นลง = **ชุดไม้เปลี่ยน**
# 🔴 be-level เคยถูกกวาดเฉพาะฝั่งลบ (0.0/-0.1/-0.2/-0.3/-0.5 ใน backtest_exit_rules --set=belevel)
#    ซึ่งเป็นเครื่องคัดกรองที่มองไม่เห็นไม้ที่เข้ามาแทน — **ฝั่งบวก (ล็อกกำไรไว้เหนือ entry)
#    ไม่เคยถูกวัดเลยทั้งสองเครื่องมือ**
# --be-ladder=T:L[,T:L] : ขั้นบันไดต่อจาก BE (ดู exit_monitor.BREAKEVEN_LADDER) เช่น 3:0.4 = ถึง 3R
# ล็อก +0.4R · 99:0 = มีขั้นแต่ไม่มีวันถึง = identity check ของ code path
_bel_arg = next((a for a in sys.argv if a.startswith("--be-ladder=")), None)
if _bel_arg:
    em.BREAKEVEN_LADDER = tuple(tuple(float(x) for x in p.split(":"))
                                for p in _bel_arg.split("=", 1)[1].split(",") if p)
# --trail-tp-buffer=X : ระยะที่ TP trailing ดึง TP เข้า (×ATR1H · ปกติ 0.5) · 0 = ปิดกฎ
# --trail-tp-trigger=X : เริ่มดึงเมื่อห่าง TP เดิมไม่เกิน X% ของราคา (ปกติ 1.0)
# 2026-09-25 เพิ่มเพื่อวัด TP trailing ครั้งแรก — กฎนี้แตะ 40% ของไม้แต่ไม่เคยถูกเทียบกับ "ไม่มี"
# ต้องวัดที่นี่ด้วยเหตุผลเดียวกับ BE: เปลี่ยนเวลาออก = คืนช่องถือไม้ = ชุดไม้เปลี่ยน
for _flag, _attr in (("--be-trigger=", "BREAKEVEN_TRIGGER_R"),
                     ("--be-level=",   "BREAKEVEN_LEVEL_R"),
                     ("--trail-tp-buffer=",  "TRAIL_TP_ATR_BUFFER"),
                     ("--trail-tp-trigger=", "TRAIL_TP_TRIGGER_PCT")):
    _a = next((a for a in sys.argv if a.startswith(_flag)), None)
    if _a:
        setattr(em, _attr, float(_a.split("=", 1)[1]))

# --adx-period / --adx-choppy / --adx-gray-high / --adx-strong : ทับเกณฑ์ ADX เฉพาะรอบนี้
# classify_regime อ่านค่าพวกนี้จาก module global ตอนถูกเรียกทุกครั้ง การ set ตรงนี้จึงมีผลทันที
#   --adx-strong=999  = ปลด regime "TREND แรงจัด" ทิ้ง (ไม่มี ADX ไหนถึง 999) ไม้ที่เคยถูกล็อก
#                       จะกลับมาเข้าเป็น TREND ปกติ — ดู comment ที่ scheduler.REGIME_NO_TRADE
#                       ซึ่งบันทึกไว้เองว่าด่านนี้ "หลักฐานบางมาก ยังไม่ได้ทดสอบ out-of-sample"
#   --adx-gray-high=20 = ยุบเขตเทาทิ้ง (ADX 20-22 กลายเป็น TREND)
for _flag, _attr, _cast in (("--adx-period=",    "ADX_PERIOD",       int),
                            ("--adx-choppy=",    "ADX_CHOPPY",       float),
                            ("--adx-gray-high=", "ADX_GRAY_HIGH",    float),
                            ("--adx-strong=",    "ADX_STRONG_TREND", float)):
    _a = next((a for a in sys.argv if a.startswith(_flag)), None)
    if _a:
        setattr(regime_check, _attr, _cast(_a.split("=", 1)[1]))

# --skip-regime : ปิดไม่ให้เข้าไม้ตอน regime ที่ระบุ (เพิ่มเข้า REGIME_NO_TRADE เฉพาะรอบนี้)
_sr_arg = next((a for a in sys.argv if a.startswith("--skip-regime=")), None)
if _sr_arg:
    _skip = tuple(s.strip() for s in _sr_arg.split("=", 1)[1].split(","))
    _known = REGIME_NO_TRADE + REGIME_TREND + REGIME_REVERSAL
    _bad = [r for r in _skip if r not in _known]
    if _bad:
        sys.exit(f"--skip-regime: ไม่รู้จัก regime {_bad} — มีให้เลือก {list(_known)}")
    REGIME_NO_TRADE = REGIME_NO_TRADE + tuple(r for r in _skip if r not in REGIME_NO_TRADE)

connect()

info = mt5.symbol_info(symbol)
# ต้นทุน spread — ตรึงจาก config เป็นค่าเริ่มต้น **ไม่อ่านค่าสด** เพราะค่าสดทำให้ผลของรอบหนึ่ง
# ขึ้นกับเวลาที่กดรัน และเลื่อนไม้ทุกไม้พร้อมกันเท่าๆ กัน = หน้าตาเหมือนผลจริงที่กระจายทั้งระบบ
# แยกไม่ออกตอน diff สองรอบ (ดูเหตุผลเต็มที่ config.SPREAD_PCT_BY_SYMBOL)
live_spread = "--live-spread" in sys.argv
spread_price = (info.ask - info.bid) if info else 0.0
if spread_price <= 0 and info:
    spread_price = info.spread * info.point
live_cost_pct = (spread_price / info.bid * 100) if info and info.bid else 0.0
if live_spread:
    cost_pct = live_cost_pct
else:
    cost_pct = SPREAD_PCT_BY_SYMBOL.get(symbol)
    if cost_pct is None:
        sys.exit(f"ไม่มี {symbol} ใน config.SPREAD_PCT_BY_SYMBOL — วัด spread แล้วใส่ค่าไว้ก่อน "
                 f"(ตอนนี้ตลาดให้ {live_cost_pct:.4f}%) หรือใช้ --live-spread ถ้าตั้งใจรันด้วยค่าสด")

h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=days * 24 + 500)
h4 = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=days * 6 + 400)
end_time   = h1["time"].iloc[-1]
# --end=YYYY-MM-DDTHH:MM : ตรึงปลายหน้าต่าง (นาฬิกา MT5 = UTC) ให้ทุกรอบที่จะเทียบกันใช้แท่งชุดเดียวกัน
# 🔴 2026-09-25 เพิ่มเพราะ ETH สองโปรเซสที่เริ่ม**พร้อมกัน**ได้ปลายหน้าต่างต่างกัน 5 ชม.
# (19:00 vs 00:00) — terminal ยัง sync แท่งไม่เสร็จหลังหลุด/ต่อใหม่ โปรเซสหนึ่งเลยได้ข้อมูลค้าง
# หน้าต่างไม่ตรงกัน = ไม้ที่ขอบต่างกัน แล้วลามต่อผ่านช่องถือไม้ ปนเข้าไปในผลต่างที่กำลังวัด
# ตั้ง --end ให้อยู่ในอดีตพอที่ทุก symbol มีแท่งเลยจุดนั้นแล้ว ถ้าข้อมูลที่ได้มาไม่ถึง = ข้อมูลค้าง
# -> จบด้วย error ให้รันใหม่ แทนที่จะได้หน้าต่างสั้นกว่าแบบเงียบๆ
_end_arg = next((a for a in sys.argv if a.startswith("--end=")), None)
if _end_arg:
    _end = pd.Timestamp(_end_arg.split("=", 1)[1])
    if end_time < _end:
        sys.exit(f"--end: ข้อมูล 1H ของ {symbol} มาถึงแค่ {end_time} ไม่ถึง {_end} "
                 f"(terminal ยัง sync ไม่เสร็จ?) — รันใหม่")
    h1 = h1[h1["time"] <= _end].reset_index(drop=True)
    end_time = _end
start_time = end_time - timedelta(days=days)
clock = h1[h1["time"] >= start_time].reset_index(drop=True)
h4_idx = {t: i for i, t in enumerate(h4["time"])}

max_daily_loss_r = MAX_DAILY_LOSS / RISK_PER_TRADE   # 6% / 2% = 3R ต่อวัน

print(f"\n{symbol}  replay backtest  {clock['time'].iloc[0]} -> {end_time}")
print(f"  สแกนทุก 1 ชม. ({len(clock)} รอบ)   ต้นทุน spread {cost_pct:.4f}% ของราคา "
      f"{'[--live-spread: ค่าสด ณ ตอนรัน]' if live_spread else '[ตรึงจาก config]'}"
      f"{'  [คิดต้นทุน]' if use_cost else '  [--no-cost]'}")
if not live_spread and abs(live_cost_pct - cost_pct) > 0.2 * max(cost_pct, 1e-9):
    # เตือนเมื่อค่าที่ตรึงไว้เริ่มห่างจากตลาดจริงเกิน 20% — ค่าตรึงมีไว้ให้เทียบข้ามรอบได้
    # ไม่ได้มีไว้ให้ค้างจนไม่ตรงกับโบรกอีกต่อไป
    print(f"  ⚠️ ตลาดตอนนี้ให้ {live_cost_pct:.4f}% ห่างจากค่าที่ตรึงไว้เกิน 20% "
          f"— ถ้าไม่ใช่ช่วง rollover/ข่าว ให้วัดใหม่แล้วแก้ config.SPREAD_PCT_BY_SYMBOL")
print(f"  Daily loss guard {MAX_DAILY_LOSS*100:.0f}% / risk {RISK_PER_TRADE*100:.0f}% ต่อไม้ "
      f"= หยุดหาไม้ใหม่เมื่อวันนั้นขาดทุนรวมถึง {max_daily_loss_r:.1f}R")
print(f"  Cooldown {COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)} ชม."
      f" + หลัง TP {tp_cd:g} ชม.{'  [ทับด้วย --tp-cooldown]' if _tpcd_arg else ''}"
      f"{'  ไม้ที่ broker ปิดคืนช่องในรอบนั้นเลย [--same-scan-reentry]' if same_scan_reentry else ''}   "
      f"MIN_SL {get_min_sl_distance_pct(symbol)}%{'  [--no-widen]' if no_widen else ''}"
      f"{'  [ทับด้วย --min-sl]' if _min_sl_arg else ''}")
print(f"  ไม่เข้าไม้เมื่อ regime = {', '.join(REGIME_NO_TRADE)}"
      f"{'   [เพิ่มด้วย --skip-regime]' if _sr_arg else ''}")
if _rms_arg or rev_tp_entry:
    print(f"  Reversal (ทดลอง): MIN_SL "
          f"{reversal.MIN_SL_OVERRIDE if _rms_arg else get_min_sl_distance_pct(symbol)}%"
          f"{'   TP ฉายจากราคาเข้า' if rev_tp_entry else ''}")
print(f"  ด่าน SL: ตรวจ 'ราคาทะลุ swing' ด้วย "
      f"{'close แท่ง 4H ล่าสุด [legacy]' if sl_guard_legacy else 'ราคาที่เข้าไม้จริง'}")
print(f"  Divergence: อายุ swing <= {regime_check.DIV_MAX_AGE_BARS} แท่ง   "
      f"RSI period {regime_check.DIV_RSI_PERIOD}"
      f"{'   ไม่กรอง volume/wick' if div_no_vol else ''}"
      f"{'   [ทับค่าระบบจริง]' if (regime_check.DIV_MAX_AGE_BARS != _DIV_AGE_LIVE or regime_check.DIV_RSI_PERIOD != _DIV_RSI_LIVE) else ''}")
# พิมพ์สถานะกฎ exit ทุกรอบเสมอ (ไม่ใช่เฉพาะรอบที่ใส่ธง) — ตั้งแต่ 2026-09-03 ที่ structure
# break ถูกปิดเป็น default การอ่าน log เก่าเทียบใหม่โดยไม่รู้ว่ารอบนั้นกฎเปิดหรือปิดจะหลงทางได้
if max_runup is not None:
    print(f"  Entry: ข้ามรอบที่ราคาวิ่งไปทางที่จะเข้าเกิน {max_runup:g}R ใน 24 แท่ง 1H (เฉพาะ Scoring)"
          f"{'   [ทับด้วย --max-runup-24h]' if _runup_arg else ''}")
print(f"  Slot: ถือได้ 1 ไม้ต่อ{'กลยุทธ์ต่อ' if slot_per_strategy else ''} symbol   "
      f"Reversal Short: {'ต้องมีเทรนด์ 1D หนุน' if rev_short_1d else 'ไม่กรองเทรนด์ 1D'}"
      f"{'   [ปิดฝั่ง Short ทิ้ง]' if no_rev_short else ''}"
      f"{'   [กลับข้างเป็น Long แทนการทิ้ง -> strategy=Breakout]' if breakout_mode else ''}")
if max_sl_atr is not None:
    print(f"  Entry: ข้ามไม้ที่ SL ห่างจาก entry เกิน {max_sl_atr:g} ATR")
if tp_cap_r is not None:
    print(f"  TP: ดึงเข้าไม่ให้ไกลเกิน {tp_cap_r:g}R ของระยะเสี่ยงจริง (ปกติใช้ Fibonacci "
          f"{config.TP_FIB_RATIO:g} ล้วน)")
print(f"  TP: {f'ดึงเข้าไม่ให้ไกลเกิน {tp_cap_atr:g} ATR ตอนเข้าไม้' if tp_cap_atr else 'ไม่มีเพดาน ATR'}"
      f"{'' if tp_cap_atr == (config.TP_MAX_ATR or None) else '   [ทับด้วย --tp-cap-atr]'}")
print(f"  Exit: RSI period {em.RSI_PERIOD} (กฎ Indicator ร้อน){'   [ทับค่าระบบจริง]' if em.RSI_PERIOD != _EXIT_RSI_LIVE else ''}")
print(f"  Exit: ถึง 1R เหลือ {em.RULE_1R_KEEP:g}%   ครึ่งทางไป TP เหลือ {em.RULE_HALFWAY_KEEP:g}%   "
      f"Climax เหลือ {em.RULE_CLIMAX_KEEP:g}%   ร้อนเหลือ {em.RULE_HOT_KEEP:g}%\n        "
      f"structure break: {'เปิด' if em.STRUCTURE_BREAK_ENABLED else 'ปิด'}   "
      f"TP trailing: {f'ใกล้ TP <= {em.TRAIL_TP_TRIGGER_PCT:g}% ดึงเข้า {em.TRAIL_TP_ATR_BUFFER:g}xATR1H' if em.TRAIL_TP_ATR_BUFFER > 0 and em.TRAIL_TP_TRIGGER_PCT > 0 else 'ปิด'}"
      f"\n        BE: ถึง {em.BREAKEVEN_TRIGGER_R:g}R ล็อก {em.BREAKEVEN_LEVEL_R:+g}R"
      f"{''.join(f' · ถึง {t:g}R ล็อก {l:+g}R' for t, l in em.BREAKEVEN_LADDER)}"
      f"{'' if em.BREAKEVEN_ENABLED else ' [ปิดกฎ BE]'}"
      f"{'   ปิดกฎ trend invalidation' if no_trend_inval else ''}")
print()

# ช่องถือไม้: dict slot -> ไม้ที่ถืออยู่ — โหมดปกติมีช่องเดียวชื่อ "ANY" (พฤติกรรมเดิมเป๊ะ)
# โหมด --slot-per-strategy แยกช่องตามชื่อกลยุทธ์
positions = {}
trades, skips = [], {}
regime_seen, reversal_fate = {}, {}   # นับทุกรอบสแกน (ดู comment ใน loop)
daily_r = {}
last_close_time = None

# cache: regime คำนวณจากแท่ง 4H ที่ปิดแล้วเท่านั้น จึงคงที่ตลอดช่วง 4 ชม.เดียวกัน — ไม่ต้อง
# คำนวณซ้ำทุกชั่วโมง (ลด MT5 call + ADX/structure/key level/divergence ลง 4 เท่า)
# เช่นเดียวกับเฟรม 1D ที่เปลี่ยนวันละครั้ง
_regime_cache, _df1d_cache = {}, {}


_BAR_OFFSET = timedelta(hours=BAR_OFFSET_H.get(symbol, 0))


def regime_eff(t):
    """regime_at + --breakout-no-min-peak (ดูที่จุด parse ธง) — คำนวณทุกครั้ง ไม่ cache เพราะ bias 1D เปลี่ยนรายวัน"""
    r = regime_at(t)
    if (breakout_no_min_peak and breakout_mode and rev_short_1d
            and r["regime"] != "REVERSAL-READY"
            and (r.get("reversal_nopeak") or ("",))[0] == "REVERSAL-READY"
            and r["divergence"].get("divergence") == "bearish"
            and get_trend_bias(symbol, df1d_at(t))[0] == "Long"):
        return {**r, "regime": "REVERSAL-READY", "breakout_nopeak": True}
    return r


def regime_at(t):
    # 2026-09-01: key ต้องเป็น "ขอบแท่ง 4H จริงของ symbol นี้" ไม่ใช่ t.floor("4h") เฉยๆ —
    # symbol ที่ BAR_OFFSET_H != 0 (เช่น XAUUSDm=2) แท่งปิดที่ 02/06/10/14 แต่ floor("4h")
    # ตัดที่ 00/04/08/12 พอไม่ตรงกัน cache ที่คำนวณตอน 04:00 จะถูกใช้ต่อถึง 07:00 ทั้งที่แท่ง
    # 02:00-06:00 ปิดไปแล้วตอน 06:00 = 2 ใน 4 ชั่วโมง replay ใช้ regime ของแท่งเก่า
    # (ระบบจริงคำนวณใหม่ทุกชั่วโมงจึงเห็นแท่งใหม่ทันที) BTC offset=0 ไม่เคยโดนผลนี้
    key = (t - _BAR_OFFSET).floor("4h") + _BAR_OFFSET
    if key not in _regime_cache:
        _regime_cache.clear()
        _regime_cache[key] = get_regime(symbol, as_of=t)
    return _regime_cache[key]


def df1d_at(t):
    # cache ต่อวันปฏิทินปลอดภัย (ตรวจ 2026-09-25): ผู้ใช้เฟรมนี้มีตัวเดียวคือ get_trend_bias ซึ่งตัด
    # แท่งวันนี้ (ที่ยังไม่ปิด) ทิ้ง และแท่ง D1 ทั้ง 8 symbol เปิดที่ 00:00 UTC ทุกแท่ง = แท่งที่ปิด
    # แล้วเป็นชุดเดียวกันทุกชั่วโมงของวันนั้น ส่วนแท่งวันนี้ที่ถูกแช่ไว้ตั้งแต่ชั่วโมงแรกที่เรียกไม่มี
    # ใครอ่าน (ผู้อ่านเดิมคือ OBV 1D ในสกอร์การ์ดที่ลบไปแล้ว) — ถ้าวันไหนมีโค้ดใหม่อ่าน iloc[-1]
    # ของ df_1d ต้อง key ด้วยชั่วโมงแทน ไม่งั้น replay จะเห็นแท่งวันนี้ค้างได้ถึง ~20 ชม. ต่างจาก
    # scheduler ที่ดึงใหม่ทุกรอบ · ไม่ merge real volume แล้วเพราะ trend_flip ใช้แค่ราคา
    key = t.date()
    if key not in _df1d_cache:
        _df1d_cache.clear()
        _df1d_cache[key] = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t)
    return _df1d_cache[key]


def note(reason):
    skips[reason] = skips.get(reason, 0) + 1


def slot_of(strategy):
    # 🔴 ไม้ "Breakout" (--breakout) ใช้ **ช่องเดียวกับ Reversal** โดยตั้งใจ ไม่ใช่ช่องที่สาม
    # เหตุผล 2 ข้อ:
    #   1) ความถูกต้อง — ด่านเช็คช่อง (`_want`) ถูกคำนวณ *ก่อน* รู้ว่าจะ flip หรือไม่ ตอนนั้น
    #      regime เป็น REVERSAL-READY จึงเช็คช่อง "Reversal" เสมอ ถ้าปล่อยให้ไม้ไปเก็บใน
    #      ช่อง "Breakout" ด่านกับที่เก็บจะเป็นคนละช่อง => ไม้ Breakout ตัวที่ 2 จะเปิดทับ
    #      ตัวแรกใน dict และไม้แรก**หายไปจากผลทั้งใบ**โดยไม่มี error (ไม่เคยถูกปิด =
    #      ไม่เคยถูก append เข้า trades)
    #   2) การทดลอง — ให้ช่องที่สามพร้อมกับการทดลองนี้ = วัดสองอย่างปนกัน (flip คุ้มไหม +
    #      เพิ่มช่องคุ้มไหม) ใช้ช่องร่วมทำให้คำถามเหลือข้อเดียว: "ตรงจุดที่เคยทิ้ง เข้า Long
    #      ดีกว่าไม่ทำอะไรหรือเปล่า" และความเสี่ยงค้างพร้อมกันต่อ symbol ไม่เปลี่ยนจากเดิม
    # mapping อยู่ที่ config.slot_of ตัวเดียวกับที่ scheduler ใช้ (2026-09-28 — เดิมอยู่ที่นี่ที่เดียว
    # แล้ว scheduler ไม่ได้ทำตาม ระบบจริงจึงเปิด Breakout ซ้อนได้ ดู docstring ที่ config.slot_of)
    strategy = cfg.slot_of(strategy)
    return strategy if slot_per_strategy else "ANY"


# label ที่ขา broker ของ step_position (ข้อ 1 ข้างล่าง) เท่านั้นที่ตั้ง — ไม้ที่ exit_monitor ปิด
# ได้ label จาก final_decision ซึ่งเป็นข้อความไทย ไม่มีทางเท่ากับสามตัวนี้เป๊ะ
_BROKER_HOW = ("SL", "BE", "TP")


def step_position(pos, key, t, bar, now):
    """เดินไม้ที่ถืออยู่ไป 1 ชั่วโมง — broker เช็ค SL/TP ระหว่างแท่ง t->now ก่อน แล้ว
    exit_monitor ตัวจริงทำงานที่ปลายชั่วโมง (now) ตรงกับ INTERVAL_SECONDS=3600 ของระบบจริง"""
    long_ = pos["direction"] == "Long"
    risk = abs(pos["entry"] - pos["sl0"])      # ระยะเสี่ยงตอนเข้า = ฐาน 1R ของบัญชี

    # 1) broker: SL/TP ทำงานระหว่างแท่งเสมอ ไม่ต้องรอ monitor
    if (bar["low"] <= pos["sl"]) if long_ else (bar["high"] >= pos["sl"]):
        r = ((pos["sl"] - pos["entry"]) if long_ else (pos["entry"] - pos["sl"])) / risk
        how = "BE" if abs(pos["sl"] - pos["entry"]) < 1e-9 else "SL"
        return close_pos(pos, key, now, pos["booked"] + pos["rem"] * r, how)
    if (bar["high"] >= pos["tp"]) if long_ else (bar["low"] <= pos["tp"]):
        r = ((pos["tp"] - pos["entry"]) if long_ else (pos["entry"] - pos["tp"])) / risk
        return close_pos(pos, key, now, pos["booked"] + pos["rem"] * r, "TP")

    # 2) exit_monitor ตัวจริง — เรียกด้วย as_of + ctx (ไม้จำลองไม่มีใน journal)
    sim = SimPos(symbol, pos["direction"], pos["entry"], pos["sl"], pos["tp"], pos["rem"], pos["time"])
    try:
        m = em.analyze_position(sim, as_of=now, ctx={
            "pinned_swing": pos["pinned_swing"], "pinned_atr_entry": pos["pinned_atr_entry"],
            "strategy": pos["strategy"], "original_lot": 1.0, "original_tp": pos["tp0"]})
    except Exception:
        return None                            # ข้อมูลไม่พอรอบนี้ — ถือต่อ

    price = m["current_price"]
    keep = m["recommended_keep_pct"] / 100
    if keep < pos["rem"] - 1e-9:               # ปิดบางส่วน/ทั้งหมดตามที่ระบบสั่ง
        cut = pos["rem"] - keep
        r_now = ((price - pos["entry"]) if long_ else (pos["entry"] - price)) / risk
        if log_cuts:
            cut_log.append({
                "symbol": symbol, "entry_time": pos["time"], "cut_time": now,
                "direction": pos["direction"], "strategy": pos["strategy"],
                "entry": pos["entry"], "sl0": pos["sl0"], "risk": risk,
                "price": price, "R_ตอนตัด": r_now, "ตัดไป": cut, "เหลือ": keep,
                "base_keep": m["base_keep_pct"], "stage_keep": m["stage_keep_pct"],
                "กฎที่ยิง": "|".join(r["name"] for r in m["position_rules"] if r["trigger"]),
                "ชม.ที่ถือมา": (now - pos["time"]).total_seconds() / 3600,
                "final": str(m["final_decision"][0])[:40]})
        pos["booked"] += cut * r_now
        pos["rem"] = keep
        pos["cuts"] += 1
        if pos["rem"] <= 1e-9:
            return close_pos(pos, key, now, pos["booked"], m["final_decision"][0][:24])

    if m["desired_sl"] is not None:            # ATR trailing / breakeven
        new_sl = m["desired_sl"]
        if no_widen:                           # ห้ามถอย SL ออกไกลกว่าตอนเข้า
            new_sl = max(new_sl, pos["sl0"]) if long_ else min(new_sl, pos["sl0"])
        pos["sl"] = new_sl
        # เวลาที่ความเสี่ยงของไม้เหลือศูนย์ครั้งแรก (SL ถึง/เลย entry = กฎ BE ยิง) -> คอลัมน์ be_time
        # ของไฟล์ผล ให้ backtest_portfolio คิดความเสี่ยงที่ยังมีชีวิตได้ (2026-09-28 — เดิมมันเดาจากแถว
        # ใน replay_cuts ซึ่งหายไปพร้อมกฎปิดบางส่วน) ไม่แตะการตัดสินใจใดๆ · ratchet ใน exit_monitor
        # กัน SL ถอยกลับหลัง BE อยู่แล้ว ครั้งแรกจึงเป็นจุดเดียวที่ต้องจำ
        if "be_time" not in pos and ((new_sl >= pos["entry"]) if long_ else (new_sl <= pos["entry"])):
            pos["be_time"] = now
    if m["desired_tp"] is not None:            # TP trailing
        pos["tp"] = m["desired_tp"]

    if (now - pos["time"]) >= timedelta(days=MAX_HOLD_DAYS):
        r = ((bar["close"] - pos["entry"]) if long_ else (pos["entry"] - bar["close"])) / risk
        return close_pos(pos, key, now, pos["booked"] + pos["rem"] * r, "HOLD-CAP")
    return None


def close_pos(pos, key, t, r, how):
    global last_close_time, last_tp_close_time
    if use_cost:
        r -= cost_pct / 100 * pos["entry"] / abs(pos["entry"] - pos["sl0"])   # spread ขาเข้า+ออก ~1 ครั้ง
    _w = _SIDEWAY_W if pos.get("strategy") == "Sideway" else 1.0
    rec = {**pos, "exit_time": t, "R": r, "how": how, "risk_w": _w}
    daily_r[t.date()] = daily_r.get(t.date(), 0.0) + r * _w   # daily loss นับเป็น R ของระบบ (2%)
    last_close_time = t
    if how == "TP":
        last_tp_close_time = t
    positions.pop(key, None)
    return rec


def try_place_limit(direction, entry, df_4h, now, regime):
    """--limit-rr: ตั้ง limit ให้ setup Reversal ที่ market ถูกปฏิเสธ (กติกาเต็มที่จุด parse ธง)"""
    global pending
    try:
        inf0 = reversal.compute_reversal_entry(symbol, direction, entry, force=True,
                                               df_4h=df_4h, as_of=now)
    except ValueError:
        return                                  # หา SL ไม่ได้ — ไม่มี setup ให้ตั้ง
    _min = (config.MIN_RR_HARD_BLOCK_REVERSAL if reversal._MIN_RR_OVERRIDE is None
            else reversal._MIN_RR_OVERRIDE)
    if inf0["rr"] is None or inf0["rr"] >= _min - 1e-9:
        return                                  # ถูกปฏิเสธด้วยด่านอื่น ไม่ใช่ R:R ต่ำ
    se, tp_raw, atr = inf0["exec_sl"], inf0["tp"], inf0["atr_entry"]
    lg, X = direction == "Long", limit_rr
    L, tp_final = (tp_raw + X * se) / (1 + X), tp_raw
    if tp_cap_atr is not None and atr and abs(tp_raw - L) > atr * tp_cap_atr:
        cap = atr * tp_cap_atr                  # TP โดนเพดาน ATR -> แก้สมการด้วย TP ที่ถูกเพดาน
        L = se + cap / X if lg else se - cap / X
        tp_final = L + cap if lg else L - cap
    if (L >= entry) if lg else (L <= entry):
        return                                  # R:R ที่ตลาด < ขั้นต่ำ < X จึงไม่ควรเกิด — กันไว้
    try:                                        # ด่านทุกตัวของ Reversal ต้องผ่านที่ราคา L ด้วย
        inf1 = reversal.compute_reversal_entry(symbol, direction, L, df_4h=df_4h, as_of=now)
    except ValueError as exc:
        limit_stats["ไม่ตั้ง: ด่านไม่ผ่านที่ราคา limit"] += 1
        _why = "  └ " + re.sub(r"[-+]?\d[\d,.]*", "N", str(exc))[:60]     # ใช้รายงานอย่างเดียว
        limit_stats[_why] = limit_stats.get(_why, 0) + 1
        return
    if abs(inf1["exec_sl"] - se) > 1e-9 * abs(se) or abs(inf1["tp"] - tp_raw) > 1e-9 * abs(tp_raw):
        limit_stats["ไม่ตั้ง: SL/TP ขยับตามราคา"] = limit_stats.get("ไม่ตั้ง: SL/TP ขยับตามราคา", 0) + 1
        return                                  # สมการ L ใช้ไม่ได้ถ้า SL/TP ขึ้นกับราคาเข้า
    pin = (se + scoring.EXEC_SL_ATR_MULT * atr if (atr and lg)
           else (se - scoring.EXEC_SL_ATR_MULT * atr if atr else se))
    pending = {"direction": direction, "limit": L, "sl": se, "tp": tp_final, "tp_fib": tp_raw,
               "pin": pin, "atr": atr, "regime": regime, "placed": now,
               "expires": now + timedelta(hours=limit_hours)}
    pending["log"] = {"symbol": symbol, "placed": now, "direction": direction, "market": entry,
                      "limit": L, "sl": se, "tp": tp_final, "tp_fib": tp_raw,
                      "rr_market": inf0["rr"], "status": None, "resolved": None}
    limit_log.append(pending["log"])
    limit_stats["ตั้ง"] += 1
    if inf0["rr"] <= 0:                         # รายงานอย่างเดียว: ราคาตลาดเลย TP ไปแล้วตอนตั้ง
        _k = "  └ ตั้งทั้งที่ราคาเลย TP ไปแล้ว (R:R ที่ตลาด ≤ 0)"
        limit_stats[_k] = limit_stats.get(_k, 0) + 1


for n, row in enumerate(clock.to_dict("records")):
    t, bar = row["time"], row
    # 2026-09-01: "เวลาที่ระบบตัดสินใจ" คือ **ปลาย** แท่ง 1H นี้ ไม่ใช่ต้นแท่ง — MT5 นับ time ของ
    # แท่ง = เวลาเปิด ดังนั้น bar["close"] คือราคา ณ t+1h เดิมโค้ดเอา close ตัวนี้ไปเป็นราคาเข้า
    # แต่ส่ง as_of=t ให้ทุกด่าน = ตัดสินใจด้วยข้อมูลถึง t แล้วได้ราคาของอีก 1 ชม.ถัดมา
    # (lookahead) ซึ่งไม่ใช่แค่ noise เพราะ entry ตัวนั้นถูกส่งเข้า compute_score ไปคิด R:R ด้วย
    # ไม้ที่ราคาย่อมาเข้าทางในชั่วโมงนั้นจึงผ่านด่าน R:R ง่ายกว่าความจริง = ผลดีเกินจริงอย่างเป็นระบบ
    # แก้โดยเลื่อนเวลาตัดสินใจเป็น now = t + 1h ทั้งหมด (ราคาเข้ายังเป็น bar["close"] เหมือนเดิม
    # ซึ่งตอนนี้กลายเป็น "ราคา ณ วินาทีที่ตัดสินใจ" พอดี ตรงกับ scheduler ที่รันแล้วยิงราคาตลาด)
    now = t + timedelta(hours=1)

    # นับ regime ของ "ทุกรอบสแกน" ก่อนด่านใดๆ — ตารางเหตุผลที่ไม่เข้าด้านล่างนับเฉพาะรอบที่
    # เดินมาถึงด่านนั้นๆ รอบที่ถือไม้อยู่จึงหายไปทั้งหมด ทำให้ตอบไม่ได้ว่า "REVERSAL-READY
    # เกิดกี่ครั้งจริง แล้วตายที่ไหน" (regime_at cache ต่อแท่ง 4H อยู่แล้ว ต้นทุนจึงต่ำ)
    try:
        _rg = regime_eff(now)["regime"]
    except Exception as exc:
        _rg = f"regime error: {type(exc).__name__}"
    regime_seen[_rg] = regime_seen.get(_rg, 0) + 1
    _rev = _rg in REGIME_REVERSAL          # รอบนี้เป็นโอกาส Reversal ไหม

    def fate(tag):                          # จบชะตากรรมของโอกาส Reversal รอบนี้ยังไง
        if _rev:
            reversal_fate[tag] = reversal_fate.get(tag, 0) + 1

    # ด่าน 1 — เช็คช่องก่อนเดินไม้ (ตรงกับ scheduler ที่อ่าน positions_get ก่อนทำอย่างอื่น
    # แล้วถ้ามีไม้อยู่จะไม่เปิดใหม่ในรอบนั้น) ไม้ที่เพิ่งปิดในชั่วโมงนี้จึงยังไม่เปิดไม้ใหม่ทันที
    _occupied = set(positions)
    for _k, _p in list(positions.items()):
        _rec = step_position(_p, _k, t, bar, now)
        if _rec:
            trades.append(_rec)
            if same_scan_reentry and _rec["how"] in _BROKER_HOW:
                _occupied.discard(_k)      # broker ปิดไปก่อนสแกน = live เห็นช่องว่างแล้ว

    # --limit-rr : limit ที่ค้างอยู่ — โบรกเติมกลางแท่ง (t, now] ก่อนรอบสแกนนี้จะเห็น
    # เติมที่ L เสมอ ไม่นับ gap ที่ได้ราคาดีกว่า (lot ถูกคิดไว้ที่ L = R ต้องวัดจาก L)
    if pending is not None:
        _lk, _lg = slot_of("Reversal"), pending["direction"] == "Long"
        if t >= pending["expires"]:
            limit_stats["หมดอายุ"] += 1; pending["log"].update(status="หมดอายุ", resolved=t); pending = None
        elif _lk in positions:             # ช่องถูกใช้ไปแล้ว — ปกติ market เข้าเองจะยกเลิกให้ก่อน
            limit_stats["ยกเลิก: market เข้าเอง"] += 1
            pending["log"].update(status="ยกเลิก: market", resolved=now); pending = None
        elif (bar["low"] <= pending["limit"]) if _lg else (bar["high"] >= pending["limit"]):
            _lp = pending; pending = None
            _lp["log"].update(status="เติม", resolved=now)
            _pos = {"time": now, "direction": _lp["direction"], "entry": _lp["limit"],
                    "sl": _lp["sl"], "sl0": _lp["sl"], "tp": _lp["tp"], "tp0": _lp["tp"],
                    "tp_fib": _lp["tp_fib"], "strategy": "Reversal", "regime": _lp["regime"],
                    "booked": 0.0, "rem": 1.0, "cuts": 0, "pinned_swing": _lp["pin"],
                    "pinned_atr_entry": _lp["atr"], "limit_placed": _lp["placed"]}
            limit_stats["เติม"] += 1
            _occupied.add(_lk)             # live: positions_get เห็นไม้นี้แล้วตอนสแกน
            if (bar["low"] <= _lp["sl"]) if _lg else (bar["high"] >= _lp["sl"]):
                limit_stats["เติมแล้วโดน SL แท่งเดียวกัน"] += 1      # มองร้าย: เติมแล้วลงต่อถึง SL
                trades.append(close_pos(_pos, _lk, now, -1.0, "SL"))
            else:
                positions[_lk] = _pos
        elif (bar["high"] >= pending["tp"]) if _lg else (bar["low"] <= pending["tp"]):
            limit_stats["ยกเลิก: แตะ TP ก่อน"] += 1
            pending["log"].update(status="ยกเลิก: แตะ TP", resolved=now); pending = None

    # กลยุทธ์ที่ regime รอบนี้จะเปิด (ไม่มีทางเกิดพร้อมกัน — regime เป็นตัวเลือกให้ตัวเดียว)
    _sw = False
    if sideway_enabled and _rg in REGIME_NO_TRADE:
        try:
            _sw = regime_eff(now)["adx_now"] < regime_check.ADX_CHOPPY
        except Exception:
            _sw = False
    _want = ("Sideway" if _sw else
             "Scoring" if _rg in REGIME_TREND else ("Reversal" if _rev else None))
    _shadow = False
    if _want is not None and slot_of(_want) in _occupied:
        note("ถือไม้อยู่แล้ว (ช่องไม่ว่าง)"); fate("ถือไม้อื่นอยู่")
        # --log-blocked: เดินด่านที่เหลือต่อเพื่อดูว่า "ถ้าช่องว่าง รอบนี้จะได้ไม้ไหม" แล้ว
        # บันทึกไว้เป็นสัญญาณเงา (ไม่เปิดไม้จริง ไม่แตะ positions/trades/daily_r เลย)
        if not log_blocked:
            continue
        _shadow = True
        # ไม้ที่ครองช่องอาจเพิ่งถูก step_position ปิดไปในชั่วโมงนี้เอง (ระบบจริงก็ไม่เปิดไม้ใหม่
        # ในรอบเดียวกันอยู่แล้ว) — เก็บเวลาไว้ตอนนี้ ไม่ใช่ตอนท้ายลูปที่ dict อาจว่างแล้ว
        _blk = positions.get(slot_of(_want))
        _blocker_time = _blk["time"] if _blk else None

    if daily_r.get(now.date(), 0.0) <= -max_daily_loss_r:  # ด่าน 3
        note("daily loss guard"); fate("daily loss guard")
        continue

    cd = COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)           # ด่าน 3b
    if cd and last_close_time is not None and (now - last_close_time) < timedelta(hours=cd):
        note("cooldown"); fate("cooldown")
        continue
    if tp_cd and last_tp_close_time is not None \
       and (now - last_tp_close_time) < timedelta(hours=tp_cd):   # --tp-cooldown
        note(f"cooldown หลัง TP ({tp_cd:g} ชม.)"); fate("cooldown หลัง TP")
        continue

    try:                                                   # ด่าน 4
        rinfo = regime_eff(now)
    except Exception as exc:
        # ใส่ชนิด+ข้อความไว้ด้วย — รอบที่ MT5 หลุดกลางทางเคยขึ้น "regime error" เฉยๆ หลายพัน
        # รอบแล้วผลออกมาดูเหมือนผลปกติ แยกไม่ออกว่าเป็นผลจริงหรือ run เสีย
        note(f"regime error: {type(exc).__name__} {str(exc)[:40]}"); fate("regime error")
        continue
    regime = rinfo["regime"]
    if regime in REGIME_NO_TRADE and not _sw:
        note(f"regime = {regime}")
        continue
    if _sw:
        regime = "SIDEWAY"

    entry = float(bar["close"])
    try:
        if regime == "SIDEWAY":
            sl_info = _sideway_mod.compute_sideway_entry(symbol, entry, as_of=now)
            direction = sl_info["direction"]
            sl, tp, strategy = sl_info["sl"], sl_info["tp"], "Sideway"
            exec_sl, atr_entry_ = sl_info["exec_sl"], None      # None = ไม่มี ATR trailing / TP_MAX_ATR
        elif regime in REGIME_TREND:
            df_1d = df1d_at(now)
            direction, _ = get_trend_bias(symbol, df_1d)
            if direction is None:
                note("หา bias ไม่ได้")
                continue
            _struct = rinfo["structure"]["trend"]
            if scoring_struct_match and not _struct.startswith(direction):
                note(f"bias {direction} สวนโครงสร้าง 4H ({_struct})")
                fate("bias สวนโครงสร้าง 4H")
                continue
            sl_info = compute_entry(symbol, direction, entry, as_of=now, df_1d=df_1d,
                                    min_rr=scoring_min_rr)   # --scoring-min-rr (None = ค่าระบบจริง)
            # exec_sl มาจาก compute_entry แล้ว (ด่าน R:R ใช้ตัวนี้ตรวจ) ไม่คำนวณซ้ำที่นี่
            sl, tp, strategy = sl_info["sl"], sl_info["tp"], "Scoring"
            exec_sl, atr_entry_ = sl_info["exec_sl"], sl_info["atr_entry"]
        elif regime in REGIME_REVERSAL:
            pol = rinfo["divergence"]["divergence"]
            direction = "Long" if pol == "bullish" else "Short"
            # --no-rev-short / --rev-short-1d : ทดลองปิดหรือกรองฝั่ง Short ของ Reversal
            # ที่มา: บน 8 symbol ไม้ Reversal Short 18 ไม้ -6.08R และ first-touch ด้วย SL/TP
            # แผนเดิม (ไม่มีกฎ exit เลย) แตะ SL ก่อน 15/16 = WR ดิบ 6.2% ที่จุดคุ้มทุน 29.4%
            # ขณะที่ฝั่ง Long 10/19 = 52.6% ที่จุดคุ้มทุน 26.2% — สาเหตุที่วัดได้คือ RSI 4H
            # อยู่เหนือ 70 บ่อยกว่าต่ำกว่า 30 ราว 1.7 เท่าทุก symbol (overbought = สภาพปกติของ
            # ตลาดที่ไต่ขึ้น) bearish divergence ที่ key level จึงยิงใส่ความแข็งแรงธรรมดา
            # (ดู scratchpad/rev_short.py, div_bias.py)
            bias_1d = None
            flipped = False
            if direction == "Short" and (no_rev_short or rev_short_1d):
                if no_rev_short:
                    note("ปิดฝั่ง Short ของ Reversal"); fate("Reversal Short ถูกปิด")
                    continue
                _df1d_rev = df1d_at(now)
                bias_1d, _ = get_trend_bias(symbol, _df1d_rev)
                if bias_1d != "Short":
                    # ── --breakout : กลับข้างแทนที่จะทิ้ง (สมมติฐานผู้ใช้ 2026-09-20) ──
                    # ไอเดีย: ไม้ Reversal Short ที่ด่านนี้กักไว้ ส่วนใหญ่ไม่ใช่ "จุดกลับตัวที่
                    # ทำไม่สำเร็จ" แต่เป็นจุดที่ราคา**ทะลุไปต่อ** (breakout) ถ้าจริง การเข้า Long
                    # ตรงนั้นแทนควรได้กำไร แทนที่จะแค่ไม่ขาดทุน
                    # หลักฐานที่มีอยู่ก่อนรัน (จากรอบ --no-rev-short-1d 2026-09-17 · 6 symbol):
                    #   ไม้ Short ที่ปลดล็อกแล้ว 28 ไม้ แพ้ 20 · WR 33% · จบด้วย SL 64%
                    #   ถือ median 35 ชม. (ฝั่ง Long 53) · MAE median −1.06R
                    #   และ **10 จาก 20 ไม้ที่แพ้ MFE < 0.3R** = ไม่เคยกลับตัวเลยแม้แต่นิดเดียว
                    # ⚠️ แต่ฝั่ง Long ก็มีสัดส่วนเดียวกันเป๊ะ (5/10) = ครึ่งหนึ่งของไม้ Reversal
                    #    ที่แพ้ "ไม่เคยกลับตัว" ทั้งสองฝั่ง ไม่ใช่ลักษณะเฉพาะของฝั่ง Short
                    #    สมมติฐานนี้จึงยังไม่ถูกพิสูจน์ ต้องวัดด้วยธงนี้เท่านั้น
                    #
                    # คิด SL/TP ทาง **Scoring** (compute_entry) ไม่ใช่ Reversal โดยตั้งใจ: ไม้ที่ได้
                    # คือ "ไปตามเทรนด์ 1D" = การเทรดต่อเนื่อง ไม่ใช่การกลับตัว
                    # ติด strategy = "Breakout" ไว้ในไฟล์ผล เพื่อแยกออกจากไม้ Scoring ปกติได้
                    if breakout_mode and bias_1d == "Long":
                        direction = "Long"
                        _struct = rinfo["structure"]["trend"]
                        if scoring_struct_match and not _struct.startswith(direction):
                            note(f"flip -> Long สวนโครงสร้าง 4H ({_struct})")
                            fate("flip Long สวนโครงสร้าง 4H")
                            continue
                        # TP ของไม้ Breakout ใช้อัตราส่วน fib ของตัวเอง (ดู --breakout-tp)
                        # swing.py ผูก TP_FIB_RATIO ไว้ที่ระดับโมดูลตอน import จึงสลับตรงนั้น
                        # แล้วคืนค่าเดิมเสมอใน finally — ไม้ Scoring/Reversal ต้องไม่ถูกแตะ
                        _saved_fib = _swing_mod.TP_FIB_RATIO
                        _swing_mod.TP_FIB_RATIO = BREAKOUT_TP_FIB_RATIO
                        try:
                            sl_info = compute_entry(
                                symbol, direction, entry, as_of=now, df_1d=_df1d_rev)
                        finally:
                            _swing_mod.TP_FIB_RATIO = _saved_fib
                        sl, tp, strategy = sl_info["sl"], sl_info["tp"], "Breakout"
                        exec_sl, atr_entry_ = sl_info["exec_sl"], sl_info["atr_entry"]
                        note("Reversal Short -> กลับเป็น Long (เทรนด์ 1D = Long)")
                        flipped = True
                    else:
                        note(f"Reversal Short แต่เทรนด์ 1D = {bias_1d}")
                        fate("Reversal Short ไม่มีเทรนด์ 1D หนุน")
                        continue
            if not flipped:
                # CHoCH — โครงสร้างฝั่งตรงข้ามต้องพังแล้ว (ดู config.REVERSAL_NEEDS_CHOCH)
                # ไม้ flip ไม่ต้องผ่านด่านนี้: CHoCH ถามว่า "โครงสร้างเดิมพังหรือยัง" ซึ่งเป็น
                # คำถามของการกลับตัว ส่วนไม้ flip เดิมพันว่าโครงสร้างเดิม **ไม่พัง** และไปต่อ
                if rev_choch:
                    _opp = "Short" if direction == "Long" else "Long"
                    if not em.check_structure_break(symbol, _opp, as_of=now):
                        note(f"ยังไม่เห็น CHoCH (โครงสร้าง {_opp} ยังไม่พัง)")
                        fate("ยังไม่เห็น CHoCH")
                        continue
                inf = reversal.compute_reversal_entry(
                    symbol, direction, entry, df_4h=rinfo["df_4h"], as_of=now)
                sl, tp, strategy = inf["sl"], inf["tp"], "Reversal"
                # 2026-09-05: exec_sl มาจาก compute_reversal_entry แล้ว (เหมือนทาง Scoring) —
                # เดิมคำนวณเองตรงนี้ ทำให้ด่าน R:R ข้างในตรวจด้วย SL โครงสร้างที่แคบกว่าของจริง
                exec_sl, atr_entry_ = inf["exec_sl"], inf["atr_entry"]
        else:
            note(f"regime ไม่รู้จัก = {regime}")
            continue
    except ValueError as exc:
        # เดิมตัดข้อความที่ "—" ตัวแรก ทำให้ "หา SL ไม่ได้" สองสาเหตุ (ไม่พบ Swing เลย vs
        # ราคาทะลุ Swing ล่าสุดไปแล้ว) ยุบรวมเป็นบรรทัดเดียว แยกไม่ออกว่าอันไหนเป็นตัวหลัก —
        # เก็บสาเหตุย่อยไว้ด้วย (ตัดเฉพาะตัวเลขท้ายที่ทำให้บรรทัดแตกกระจาย)
        _m = str(exc).replace("\n", " ")
        note(re.sub(r"[-+]?\d[\d,.]*", "N", _m)[:76]); fate("hard block: " + re.sub(r"[-+]?\d[\d,.]*", "N", _m)[:40])
        if log_rr_blocked and regime in REGIME_REVERSAL and not flipped and not _shadow:
            try:
                _i = reversal.compute_reversal_entry(symbol, direction, entry, force=True,
                                                     df_4h=rinfo["df_4h"], as_of=now)
                _min = (config.MIN_RR_HARD_BLOCK_REVERSAL if reversal._MIN_RR_OVERRIDE is None
                        else reversal._MIN_RR_OVERRIDE)
                if _i["rr"] is not None and _i["rr"] < _min - 1e-9:     # ติด R:R จริง ไม่ใช่ด่านอื่น
                    rr_blocked_log.append({"symbol": symbol, "time": now, "direction": direction,
                                           "price": entry, "sl": _i["sl"], "exec_sl": _i["exec_sl"],
                                           "tp": _i["tp"], "rr": _i["rr"], "atr": _i["atr_entry"],
                                           "occupied": slot_of("Reversal") in _occupied})
            except ValueError:
                pass
        # --limit-rr: ต้องเช็ค regime ก่อน flipped เสมอ (flipped ถูกตั้งเฉพาะทาง Reversal ในรอบนี้)
        if (limit_rr and regime in REGIME_REVERSAL and not flipped and not _shadow
                and pending is None and slot_of("Reversal") not in _occupied):
            try_place_limit(direction, entry, rinfo["df_4h"], now, regime)
        continue
    except Exception as exc:
        note(f"ERROR {type(exc).__name__}")
        continue

    # 2026-08-31: SL ที่ส่ง broker มาจาก compute_entry แล้ว (sl_info["exec_sl"]) ไม่คำนวณเองซ้ำ —
    # เดิมคำนวณที่นี่ *หลัง* ด่าน R:R ทำให้ด่านตรวจคนละระยะเสี่ยงกับที่ใช้จริง (ดู scoring.py)
    # pinned_swing ยังเป็น SL โครงสร้างเท่าเดิม สูตร trailing จึงไม่เปลี่ยน
    sl, atr_entry = exec_sl, atr_entry_

    # --max-sl-atr=X : ข้ามไม้ที่ SL (ที่ส่ง broker จริง) ห่างจาก entry เกิน X เท่าของ ATR
    # ที่มา: SL/ATR เป็นตัวแปร ณ เวลาเข้าไม้ตัวเดียวที่แยก "ไม้ที่ไม่เคยไปทางเราเลย" ออกจาก
    # "ไม้ที่ชนะ" ได้จริง (ห่างกัน 0.62 SD) — ADX 1H/4H 0.06/0.11, ATR percentile 0.12,
    # ระยะ EMA200 0.19, R:R แผน 0.28 = แยกไม่ออกทั้งหมด
    # กลไก: SL ห่าง 19 ATR แปลว่าต้องให้ราคาวิ่ง 19 ATR ถึงได้ 1R ไม้พวกนี้ MFE 0.02-0.32R
    # ทั้งที่ราคาขยับ 1-2 ATR ตามปกติ = ชนะไม่ได้ตั้งแต่ก่อนเข้า ไม่ใช่เพราะทิศผิด
    # ระบบมี MIN_SL_DISTANCE_PCT เป็นพื้นอยู่แล้วแต่ไม่เคยมีเพดาน
    if max_sl_atr is not None and atr_entry:
        _sl_atr = abs(entry - sl) / atr_entry
        if _sl_atr > max_sl_atr:
            note(f"SL ห่างเกิน {max_sl_atr:g} ATR ({_sl_atr:.1f})")
            fate(f"SL ห่างเกิน {max_sl_atr:g} ATR")
            continue

    # tp_fib = TP ที่ Fibonacci ให้ **ก่อนโดนเพดานใดๆ ดึงเข้า** — เก็บแยกไว้ลงไฟล์ผล
    # 2026-09-12: ตั้งแต่ TP_MAX_ATR=18 เข้าระบบ ไม้ราว 1 ใน 3 มี tp0 ที่ถูกเพดานตัดแล้ว
    # (Long 34% / Short 27% จาก 197 ไม้) ใครก็ตามที่เอา tp0 ไปคิด R:R แผน หรือถอด `move`
    # ของ fib กลับ จะได้ค่าที่ต่ำกว่าจริงโดยไม่มีอะไรฟ้อง — เจอมาแล้วตอนไล่ดูว่าทำไม R:R
    # ของไม้ Short ต่ำกว่า Long (ตัวเลขชี้ว่า fib ลำเอียงตามทิศ พอตัดไม้ที่ชนเพดานออกแล้ว
    # ความต่างหดจาก 0.25 เหลือ 0.17 และกลับด้านใน 2 จาก 6 symbol = ไม่ใช่ของจริง)
    # เทียบ tp0 กับ tp_fib ได้ว่าไม้ไหนโดนเพดาน: tp0 != tp_fib
    tp_fib = tp

    # --tp-cap-r=X : ดึง TP เข้ามาไม่ให้ไกลเกิน X เท่าของระยะเสี่ยงจริง (entry -> exec_sl)
    # ที่มา: TP จาก Fibonacci 1.618 ตั้งไว้ไกลกว่าที่ราคาวิ่งไปจริงราว 2 เท่าในทุก symbol —
    # R:R แผนเฉลี่ย 2.87 แต่ MFE (ราคาวิ่งไปทางเราสูงสุดจริง) เฉลี่ยแค่ 1.0-1.6R ต่อ symbol
    # ทำให้ไม้ถึง TP แค่ 25.6% ที่เหลือไปจบที่ SL/BE ทั้งที่เคยกำไรมาแล้ว
    # ใช้ min() ไม่ใช่ตั้งค่าตายตัว — ไม้ที่ fib ให้ TP ใกล้กว่า X อยู่แล้วไม่ถูกยืดออก
    # วางไว้หลังด่าน R:R (ในสกอร์การ์ด) โดยตั้งใจ: ไม้ยังต้องผ่าน MIN_RR_HARD_BLOCK ด้วย TP
    # โครงสร้างจริงก่อน แล้วค่อยดึงเข้า ไม่ใช่ปล่อยไม้ที่โครงสร้างไม่มีที่ไปให้ผ่านเพราะเป้าใกล้
    if tp_cap_r is not None:
        _risk = abs(entry - sl)
        _cap = _risk * tp_cap_r
        tp = (min(tp, entry + _cap) if direction == "Long" else max(tp, entry - _cap))

    # เพดาน TP เป็นเท่าของ ATR ตอนเข้าไม้ — **เป็นพฤติกรรมของระบบจริงแล้ว** (config.TP_MAX_ATR)
    # ตั้งแต่ 2026-09-11 ไม่ใช่แค่แฟลกทดลอง ที่มา/ตัวเลข/คำเตือนทั้งหมดอยู่ที่ config.TP_MAX_ATR
    # ใช้ --tp-cap-atr=X ทับ หรือ =0 เพื่อปิด (จะติด tag _tpcapatrX / _notpcap ที่ชื่อไฟล์ผล)
    # ตรงกับ scheduler.scan_symbol ข้อ 4b-2 ทั้งตำแหน่งในลำดับด่าน (หลังสกอร์การ์ดผ่าน ก่อนด่าน
    # runup) และตัว atr_entry ที่ใช้ (sl_info["atr_entry"] ตัวเดียวกับที่คิด exec_sl)
    if tp_cap_atr is not None and atr_entry:
        _cap = atr_entry * tp_cap_atr
        tp = (min(tp, entry + _cap) if direction == "Long" else max(tp, entry - _cap))

    # --max-runup-24h : ข้ามไม้ที่ "ราคาวิ่งไปทางเรามาก่อนแล้ว" (เข้าตอนปลายทาง) — วัดเทียบเป็น R
    # ด้วยระยะเสี่ยงจริงของไม้นี้ ใช้ 24 แท่ง 1H ย้อนหลังในนาฬิกาเดียวกับ replay (fx/index ที่มี
    # วันหยุดจึงเท่ากับ 24 ชั่วโมง "ที่ตลาดเปิด" ไม่ใช่ 24 ชม.ตามปฏิทิน)
    if max_runup is not None and strategy == "Scoring" and n >= 24:
        _risk = abs(entry - sl)
        _past = float(clock["close"].iloc[n - 24])
        _runup = ((entry - _past) if direction == "Long" else (_past - entry)) / _risk if _risk else 0.0
        if _runup > max_runup:
            note(f"ราคาวิ่งไปทางที่จะเข้ามาแล้ว > {max_runup:g}R ใน 24 ชม.")
            continue

    # --reject-cooldown : ทิศนี้เพิ่งถูกด่านปฏิเสธไปไม่นาน -> ยังไม่ให้เข้า (ดู config)
    # ต้องเช็ค **ก่อน** ตัวด่านเอง ไม่งั้นการปฏิเสธซ้ำจะไปต่ออายุ cooldown ของตัวเองไปเรื่อยๆ
    # 🔴 2026-09-18: ต้องมี `strategy == "Scoring"` ด้วย — รอบแรกลืมใส่ ทำให้ไม้ Reversal โดน
    # บล็อกจาก cooldown ที่ **Scoring** เป็นคนตั้ง ซึ่ง (ก) ไม่ตรงกับ scheduler.py ที่บล็อก 4d
    # ซ้อนอยู่ใต้เงื่อนไข Scoring อยู่แล้ว = live ต่างจาก backtest เงียบๆ และ (ข) ผิดเจตนา —
    # การที่ราคาอยู่ตรงจุดสุดขั้วคือ "เหตุผลที่ Reversal ควรเข้า" ไม่ใช่เหตุผลที่ควรห้าม
    if reject_cd and strategy == "Scoring" \
       and reject_until.get(direction) is not None and now < reject_until[direction]:
        note(f"cooldown หลังถูกด่านปฏิเสธ ({reject_cd} ชม.)")
        continue

    # --min-turn : ด่านฝาแฝดคนละด้าน — กันเข้าไม้ "ตรงจุดสุดขั้วพอดี" (ยังไม่เด้งให้เห็น)
    # ใช้หน้าต่าง 24 แท่งชุดเดียวกับ run-up guard ข้างบน (clock เดียวกัน = นาฬิกาตลาดเปิด)
    if min_turn is not None and strategy == "Scoring" and n >= 24:
        _risk = abs(entry - sl)
        _w = clock.iloc[n - 24:n]
        _turn = ((entry - float(_w["low"].min())) if direction == "Long"
                 else (float(_w["high"].max()) - entry)) / _risk if _risk else 0.0
        if _turn < min_turn:
            note(f"เข้าตรงจุดสุดขั้ว 24 ชม. (เด้งมาแค่ {_turn:.2f}R < {min_turn:g}R)")
            if reject_cd:
                reject_until[direction] = now + timedelta(hours=reject_cd)
            continue

    _pin = (sl + scoring.EXEC_SL_ATR_MULT * atr_entry if (atr_entry and direction == "Long")
            else (sl - scoring.EXEC_SL_ATR_MULT * atr_entry if atr_entry else sl))

    # --log-blocked: รอบนี้ช่องไม่ว่าง — บันทึกเป็นสัญญาณเงาแล้วไปต่อ ไม่เปิดไม้จริง
    if _shadow:
        blocked_log.append({"symbol": symbol, "time": now, "direction": direction,
                            "strategy": strategy, "regime": regime, "entry": entry,
                            "sl0": sl, "tp0": tp, "pinned_swing": _pin,
                            "pinned_atr_entry": atr_entry,
                            "ช่องที่ไม่ว่าง": slot_of(strategy),
                            "ไม้ที่ครองช่องอยู่": _blocker_time})
        continue

    _limit_hit_sl = False
    # --entry-oco-atr=X : limit ด้านหน้า + stop ด้านหลัง ห่าง X·ATR ทั้งคู่ (ดูที่จุด parse ธง)
    if entry_oco_atr and atr_entry:
        if n + 1 >= len(clock):
            continue
        _nb = clock.iloc[n + 1]
        _lg = direction == "Long"
        _d = entry_oco_atr * atr_entry
        _L, _S = (entry - _d, entry + _d) if _lg else (entry + _d, entry - _d)
        _hitL = (_nb["low"] <= _L) if _lg else (_nb["high"] >= _L)
        _hitS = (_nb["high"] >= _S) if _lg else (_nb["low"] <= _S)
        if _hitS and _hitL and entry_oco_both == "limit":   # --entry-oco-both=limit : มองดี (ขอบอีกด้านของคำตอบ)
            _hitS = False
        if _hitS:                                   # มองร้าย: โดนทั้งสองฝั่งในแท่งเดียว = นับว่าเติมฝั่ง stop (ราคาแย่)
            entry_limit_stats["oco: stop (ด้านหลัง)" + (" · โดนทั้งคู่" if _hitL else "")] = \
                entry_limit_stats.get("oco: stop (ด้านหลัง)" + (" · โดนทั้งคู่" if _hitL else ""), 0) + 1
            entry = _S
        elif _hitL:
            entry_limit_stats["oco: limit (ด้านหน้า)"] = entry_limit_stats.get("oco: limit (ด้านหน้า)", 0) + 1
            entry = _L
        else:                                       # อยู่ในกรอบ ±X·ATR ทั้งชั่วโมง -> เข้า market ปลายชั่วโมง
            entry_limit_stats["oco: ไม่โดนทั้งคู่ -> market ปลายชั่วโมง"] = \
                entry_limit_stats.get("oco: ไม่โดนทั้งคู่ -> market ปลายชั่วโมง", 0) + 1
            entry = _nb["close"]
        # SL อยู่ด้านหลังของ limit เสมอ — แท่งที่แตะ SL ด้วย = มองร้ายว่าโดน SL ทันที (ทุกฝั่งที่เติม)
        _limit_hit_sl = (_nb["low"] <= sl) if _lg else (_nb["high"] >= sl)
    if (entry_limit or entry_limit_atr) and not (entry_limit_atr and not atr_entry):
        if n + 1 >= len(clock):
            continue
        _nb = clock.iloc[n + 1]
        _lg = direction == "Long"
        # ระยะ limit: --entry-limit = X·R (ระยะถึง SL) · --entry-limit-atr = X·ATR 1H ตอนเข้า (atr_entry)
        _dist = entry_limit_atr * atr_entry if entry_limit_atr else entry_limit * abs(entry - sl)
        _L = entry - _dist if _lg else entry + _dist
        _touched = bool((_nb["low"] <= _L) if _lg else (_nb["high"] >= _L))   # bool() สำคัญ: numpy.bool_ ไม่ `is False`
        # 🔴 2026-10-01 รอบแรกของ --entry-limit-atr=0.3 ไม่มี bool() -> np.False_ หลุดทั้ง "ไม่เติม" และ "เติม"
        #    ไม้ที่ไม่แตะเลยเข้าที่ราคาตลาดเดิม = รู้อนาคตว่าจะไม่แตะแล้วค่อยเลือก market (ได้ +14.98R ปลอม)
        #    รอบ --entry-limit=0.05/0.1 ไม่โดน (รันก่อนเพิ่มบรรทัดนี้ ใช้ `if not` ตรงๆ) · รอบ fallback ไม่โดน
        if not _touched and entry_limit_fallback:
            # --entry-limit-fallback : ไม่แตะใน 1 ชม. -> เข้า market ตอนปลายชั่วโมง (close ของแท่งนั้น)
            # SL/TP เดิม · ถ้าชั่วโมงนั้นแตะ TP ไปแล้ว = ไม่ไล่เข้า (ราคาไปถึงเป้าแล้ว) · แตะ SL ไม่มีทาง
            # เพราะ SL อยู่ฝั่งเดียวกับ L และเลยไปกว่า L (ไม่แตะ L = ไม่แตะ SL)
            if (_nb["high"] >= tp) if _lg else (_nb["low"] <= tp):
                entry_limit_stats["ไม่เติม · แตะ TP แล้วไม่ไล่"] += 1
                note("limit ไม่เติม และราคาถึง TP ไปแล้ว")
                continue
            entry_limit_stats["ไม่เติม -> market ปลายชั่วโมง"] += 1
            entry = _nb["close"]
            _touched = None                      # เข้าแล้ว — ข้ามบล็อก "ไม่เติม" และ "เติม" ด้านล่าง
        if _touched is False:
            entry_limit_stats["ไม่เติม"] += 1
            entry_limit_missed.append({"symbol": symbol, "time": now, "direction": direction,
                                       "strategy": strategy, "entry": entry, "sl0": sl, "tp0": tp})
            note("limit ไม่เติมใน 1 ชม.")
            continue
        if _touched:
            entry_limit_stats["เติม"] += 1
            entry = _L
            _limit_hit_sl = (_nb["low"] <= sl) if _lg else (_nb["high"] >= sl)   # มองร้าย: เติมแล้วลงต่อถึง SL
    if pending is not None and slot_of(strategy) == slot_of("Reversal"):
        limit_stats["ยกเลิก: market เข้าเอง"] += 1                    # ระบบเดิมมาก่อนเสมอ
        pending["log"].update(status="ยกเลิก: market", resolved=now); pending = None
    if reverse_on_opposite:
        for _k, _p in list(positions.items()):
            if _k != slot_of(strategy) and _p["direction"] != direction:
                _pl = _p["direction"] == "Long"
                _r = ((entry - _p["entry"]) if _pl else (_p["entry"] - entry)) / abs(_p["entry"] - _p["sl0"])
                trades.append(close_pos(_p, _k, now, _p["booked"] + _p["rem"] * _r, "REVERSE"))
    if tp_entry_on_opposite and strategy != "Sideway":      # ไม่ใช้กับไม้ Sideway ทั้งสองทาง (ผู้ใช้ 2026-10-02)
        for _k, _p in list(positions.items()):
            if _k != slot_of(strategy) and _p["direction"] != direction and _p["strategy"] != "Sideway":
                _pl = _p["direction"] == "Long"
                if (entry < _p["entry"]) if _pl else (entry > _p["entry"]):
                    _p["tp"] = _p["entry"]
                    _p["tp_entry_at"] = now
    if perfect_entry and n + 1 < len(clock):   # --perfect-entry : เติมที่ราคาดีสุดของชั่วโมงถัดไป (ดูที่จุด parse ธง)
        _nb = clock.iloc[n + 1]
        _best = min(_nb["low"], entry) if direction == "Long" else max(_nb["high"], entry)
        if (_best > sl) if direction == "Long" else (_best < sl):   # ถ้าชั่วโมงนั้นทะลุ SL ไม่นับ
            perfect_log.append({"symbol": symbol, "time": now, "market": entry, "fill": _best,
                                "gain_R_old": abs(entry - _best) / abs(entry - sl)})
            entry = _best
    positions[slot_of(strategy)] = {"time": now, "direction": direction, "entry": entry, "sl": sl, "sl0": sl,
           # tp0 = TP ที่ส่งจริงตอนเข้า (ผ่านเพดานแล้ว)  tp_fib = ที่ Fibonacci ให้ก่อนเพดาน
           # สองค่านี้ต่างกันเมื่อไม้นั้นโดนเพดานดึงเข้า — ดู comment ที่จุดคำนวณ tp_fib
           "tp": tp, "tp0": tp, "tp_fib": tp_fib, "strategy": strategy, "regime": regime,
           "booked": 0.0, "rem": 1.0, "cuts": 0,
           # pinned_swing = SL โครงสร้าง (ถอย exec_sl กลับด้วยตัวคูณเดียวกับที่ขยับออกไป)
           "pinned_swing": _pin,
           "pinned_atr_entry": atr_entry}
    if _limit_hit_sl:
        entry_limit_stats["เติมแล้วโดน SL แท่งเดียวกัน"] += 1
        trades.append(close_pos(positions[slot_of(strategy)], slot_of(strategy), now, -1.0, "SL"))

    if n % 2000 == 0:
        print(f"  ... {t}  ({n}/{len(clock)})  ปิดไปแล้ว {len(trades)} ไม้", flush=True)

mt5.shutdown()

# ---------------------------------------------------------------------------
t = pd.DataFrame(trades)
print(f"\n{'=' * 78}")
print(f"  รอบสแกน {len(clock)}  ->  เข้าไม้จริง {len(t)} ไม้"
      f"{f'  (ยังถือค้าง {len(positions)} ไม้)' if positions else ''}")
print(f"  {'-' * 74}")
print("  เหตุผลที่ไม่เข้า (นับรอบสแกน):")
for k, v in sorted(skips.items(), key=lambda x: -x[1])[:10]:
    print(f"    {v:>6}  {k}")

# regime ของทุกรอบสแกน (รวมรอบที่ถือไม้อยู่ ซึ่งตารางข้างบนมองไม่เห็น)
print(f"  {'-' * 74}")
print("  Regime ของทุกรอบสแกน:")
for k, v in sorted(regime_seen.items(), key=lambda x: -x[1]):
    print(f"    {v:>6}  ({v / len(clock) * 100:>5.1f}%)  {k}")
if reversal_fate:
    tot = sum(reversal_fate.values())
    entered = regime_seen.get("REVERSAL-READY", 0) - tot
    print(f"  {'-' * 74}")
    print(f"  โอกาส Reversal ({regime_seen.get('REVERSAL-READY', 0)} รอบที่ regime = REVERSAL-READY) จบยังไง:")
    for k, v in sorted(reversal_fate.items(), key=lambda x: -x[1]):
        print(f"    {v:>6}  {k}")
    print(f"    {entered:>6}  -> เข้าไม้จริง")
if limit_rr:
    print(f"  {'-' * 74}")
    print(f"  limit order (Reversal ที่ติด R:R -> ตั้งที่ R:R {limit_rr:g} · อายุ {limit_hours:g} ชม.):")
    for k, v in limit_stats.items():
        print(f"    {v:>6}  {k}")
    if pending is not None:
        print("    (ยังค้าง 1 order ตอนจบหน้าต่าง — ไม่นับ)")

if t.empty:
    print(f"{'=' * 78}")
    sys.exit(0)

# นิยาม "ชนะ" — 2026-09-20 เปลี่ยนจาก `R > 0` เป็น `R > WIN_THRESHOLD_R` ตามคำสั่งผู้ใช้
# เหตุผล: ไม้ที่ออกที่จุดคุ้มทุน (BE) จบด้วย R ติดลบนิดเดียวจากค่า spread ล้วนๆ แล้วถูกนับเป็น
# "แพ้" เต็มหน่วยเท่ากับไม้ที่โดน SL เต็ม −1.05R ทำให้ WR ขยับแรงจากการเปลี่ยนที่แทบไม่กระทบเงิน
# (เคสจริง: ปิดกฎ Indicator ร้อน 2026-09-20 ทำให้ไม้ BE 21 ไม้พลิกจาก +0.01…+0.2R เป็น
#  median −0.007R -> WR ร่วง 13.9 จุด ทั้งที่ผลสุทธิของการเปลี่ยน **+6.33R**)
#
# 🔴 ทำไมเป็น −0.05 ไม่ใช่ −0.01: **ข้อมูลมีช่องว่างจริงตรงนั้น** วัดบน base 160 ไม้
#   −0.25..−0.10  6 ไม้ | −0.10..−0.05 **0 ไม้** | −0.05..−0.02 2 ไม้
#   −0.02..−0.01  9 ไม้ | −0.01..0.00 18 ไม้ | 0.00..+0.05 **0 ไม้**
#   ไม้ BE กองรวมกัน 27 ไม้ในช่วง −0.02..0.00 — เส้น −0.01 ผ่ากลางกองนั้น (18 ชนะ/9 แพ้)
#   ซึ่งจะเด้งไปมาเมื่อ spread ของ symbol ใดเปลี่ยน ส่วนเส้น −0.05 ตกในช่องว่างที่ไม่มีไม้เลย
#   => ทุกค่าตั้งแต่ −0.05 ถึง −0.10 ให้คำตอบเดียวกันเป๊ะ (53.1%) = ไม่ไวต่อ spread
# ⚠️ **ตัวเลข WR ก่อน 2026-09-20 ทั้งหมดคิดด้วย `R > 0` เทียบกับของใหม่ตรงๆ ไม่ได้**
#   (base ปัจจุบัน: นิยามเก่า 35.0% · นิยามใหม่ 53.1% — ไม้ชุดเดียวกันเป๊ะ)
# ⚠️ และ WR ไม่ใช่ตัวตัดสิน — เส้นเสมอตัวของระบบอยู่ที่ WR ~33% เท่านั้น (ไม้ชนะเฉลี่ยใหญ่กว่า
#   ไม้แพ้ ~2 เท่า) การไล่ทำ WR ให้สูงวัดแล้วว่าซื้อได้ที่ราคา 2-3R ต่อ 1 จุด = ขาดทุนล้วน
#   (ดูบล็อกการกวาด --tp-cap-r ที่ config.TP_FIB_RATIO)
WIN_THRESHOLD_R = -0.05
t["win"] = t["R"] > WIN_THRESHOLD_R
print(f"  {'-' * 74}")
print(f"  Win Rate   : {t['win'].mean()*100:.1f}%  ({int(t['win'].sum())}/{len(t)})"
      f"   [ชนะ = R > {WIN_THRESHOLD_R:g} — ไม้ BE ไม่นับเป็นแพ้]")
print(f"  Avg R      : {t['R'].mean():+.2f}R      Total R: {t['R'].sum():+.1f}R")
print(f"  ถือเฉลี่ย   : {(t['exit_time']-t['time']).mean().total_seconds()/86400:.1f} วัน")

print(f"\n  {'กลุ่ม':<22}{'ไม้':>6}{'Win':>8}{'AvgR':>8}{'TotalR':>9}")
print(f"  {'-' * 53}")
for col in ("strategy", "direction", "how", "regime"):
    for k, g in t.groupby(col):
        print(f"  {str(k):<22}{len(g):>6}{g['win'].mean()*100:>7.1f}%{g['R'].mean():>+8.2f}{g['R'].sum():>+9.1f}")
    print(f"  {'-' * 53}")

mid = t["time"].min() + (t["time"].max() - t["time"].min()) / 2
for lbl, g in (("ครึ่งแรก", t[t["time"] < mid]), ("ครึ่งหลัง", t[t["time"] >= mid])):
    if len(g):
        print(f"  {lbl:<22}{len(g):>6}{g['win'].mean()*100:>7.1f}%{g['R'].mean():>+8.2f}{g['R'].sum():>+9.1f}")
print(f"{'=' * 78}")
# รอบที่สวนค่าระบบจริงเขียนคนละไฟล์ — ไม่งั้นทับผลรอบปกติที่เอาไว้เทียบ
_tag = ""
if _sr_arg:
    _tag += "_skip-" + "-".join(r.replace(" ", "") for r in _skip)
if _rms_arg:
    _tag += f"_revminsl{reversal.MIN_SL_OVERRIDE:g}"
if _rmr_arg:
    _tag += f"_revminrr{reversal._MIN_RR_OVERRIDE:g}"
if scoring_min_rr is not None:
    _tag += f"_scoringminrr{scoring_min_rr:g}"
if _rtr_arg:
    _tag += f"_revtp{reversal._TP_RATIO_OVERRIDE:g}"
for _flag, _attr, _short in (("--adx-period=",    "ADX_PERIOD",       "adxp"),
                             ("--adx-choppy=",    "ADX_CHOPPY",       "adxchop"),
                             ("--adx-gray-high=", "ADX_GRAY_HIGH",    "adxgray"),
                             ("--adx-strong=",    "ADX_STRONG_TREND", "adxstrong")):
    if any(a.startswith(_flag) for a in sys.argv):
        _tag += f"_{_short}{getattr(regime_check, _attr):g}"
if slot_per_strategy != config.SLOT_PER_STRATEGY:          # ติด tag เฉพาะรอบที่สวนค่าในระบบจริง
    _tag += "_slotper" if slot_per_strategy else "_oneslot"
if no_rev_short:
    _tag += "_norevshort"
if tp_cap_r is not None:
    _tag += f"_tpcap{tp_cap_r:g}"
if tp_cap_atr != (config.TP_MAX_ATR or None):    # ติด tag เฉพาะรอบที่สวนค่าในระบบจริง
    _tag += f"_tpcapatr{tp_cap_atr:g}" if tp_cap_atr else "_notpcap"
if max_sl_atr is not None:
    _tag += f"_maxslatr{max_sl_atr:g}"
if rev_short_1d != config.REVERSAL_SHORT_NEEDS_1D_TREND:
    _tag += "_revshort1d" if rev_short_1d else "_revshortany"
if breakout_mode != _BREAKOUT_LIVE:        # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_breakout" if breakout_mode else "_nobreakout"
if breakout_mode:
    if _botp_arg:                      # ติด tag เฉพาะรอบที่สวนค่า default 2.618
        _tag += f"_botp{BREAKOUT_TP_FIB_RATIO:g}"
if struct_reg != _STRUCT_REG_LIVE:      # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_structreg" if struct_reg else "_swingstruct"
if struct_reg:
    if _srn_arg: _tag += f"n{int(_srn_arg.split('=')[1])}"
    if _srr_arg: _tag += f"r2{float(_srr_arg.split('=')[1]):g}"
if rev_tp_entry:
    _tag += "_revtpentry"
if regime_check.DIV_MAX_AGE_BARS != _DIV_AGE_LIVE:    # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += f"_divage{regime_check.DIV_MAX_AGE_BARS}"
if _dml_arg:      # ติด tag ทุกครั้งที่ส่งธง รวม 180 ที่เป็น identity check (กันทับไฟล์ base)
    _tag += f"_divlook{regime_check.DIV_MAX_LOOKBACK_BARS}"
if _dms_arg:
    _tag += f"_divspace{regime_check.DIV_MIN_SPACING_BARS}"
if regime_check.DIV_RSI_PERIOD != _DIV_RSI_LIVE:
    _tag += f"_divrsi{regime_check.DIV_RSI_PERIOD}"
if em.RSI_PERIOD != _EXIT_RSI_LIVE:
    _tag += f"_exitrsi{em.RSI_PERIOD}"
if sl_guard_legacy:
    _tag += "_slguardlegacy"
if div_no_vol:
    _tag += "_divnovol"
if _1rk_arg:
    _tag += f"_1rkeep{em.RULE_1R_KEEP:g}"
if _clk_arg:
    _tag += f"_climaxkeep{em.RULE_CLIMAX_KEEP:g}"
if climax_in_profit:
    _tag += "_climaxprofit"
if _hotk_arg:
    _tag += f"_hotkeep{em.RULE_HOT_KEEP:g}"
if _hwk_arg:
    _tag += f"_hwkeep{em.RULE_HALFWAY_KEEP:g}"
for _flag, _attr, _short in (("--slow-r=",    "SLOW_TRADE_R",    "slowr"),
                             ("--slow-days=", "SLOW_TRADE_DAYS", "slowd"),
                             ("--slow-keep=", "SLOW_TRADE_KEEP", "slowkeep")):
    if any(a.startswith(_flag) for a in sys.argv):
        _tag += f"_{_short}{getattr(em, _attr):g}"
if slow_calendar:
    _tag += "_slowcal"
if no_trend_inval:
    _tag += "_notrendinval"
if "--structure-break" in sys.argv and not no_struct_break:
    _tag += "_structbreak"
if _runup_arg:
    _tag += f"_runup{max_runup:g}"
if min_turn != config.MIN_TURN_FROM_EXTREME_R:   # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += f"_minturn{min_turn:g}" if min_turn else "_nominturn"
if reject_cd != config.REJECT_COOLDOWN_HOURS:
    _tag += f"_rejcd{reject_cd:g}" if reject_cd else "_norejcd"
if entry_oco_atr:
    _tag += f"_entryocoatr{entry_oco_atr:g}"
    if entry_oco_both != "stop":
        _tag += f"both{entry_oco_both}"
if entry_limit or entry_limit_atr:
    _tag += f"_entrylimit{entry_limit:g}" if entry_limit else f"_entrylimitatr{entry_limit_atr:g}"
    if entry_limit_fallback:
        _tag += "fb"
if perfect_entry:
    _tag += "_perfectentry"
if sideway_enabled != config.SIDEWAY_ENABLED:      # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_sideway" if sideway_enabled else "_nosideway"
if breakout_no_min_peak != config.BREAKOUT_IGNORES_MIN_PEAK:   # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_bonopeak" if breakout_no_min_peak else "_nobonopeak"
if regime_check.REVERSAL_USES_ADX_FLOOR != _rev_floor_default:   # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_revnoadxfloor" if not regime_check.REVERSAL_USES_ADX_FLOOR else "_revadxfloor"
if same_scan_reentry:
    _tag += "_samescan"
if reverse_on_opposite:
    _tag += "_reverseopp"
if tp_entry_on_opposite != config.TP_TO_ENTRY_ON_OPPOSITE:   # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += "_tpentryopp" if tp_entry_on_opposite else "_notpentryopp"
if limit_rr:
    _tag += f"_limitrr{limit_rr:g}h{limit_hours:g}"
if log_rr_blocked:     # บันทึกอย่างเดียว — ติด tag กันทับ base (ไฟล์ไม้ต้องตรง base เป๊ะ = identity check)
    _tag += "_rrlog"
if tp_cd != float(config.TP_COOLDOWN_HOURS or 0):   # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += f"_tpcd{tp_cd:g}"
if _mh_arg:
    _tag += "_nomaxhold" if MAX_HOLD_DAYS >= 1e6 else f"_maxhold{MAX_HOLD_DAYS:g}"
if live_spread:      # ผลรอบนี้ขึ้นกับเวลาที่รัน — อย่าให้ทับไฟล์ base ที่เทียบข้ามรอบได้
    _tag += "_livespread"
if no_breakeven:
    _tag += "_nobe"
if any(a.startswith("--be-trigger=") for a in sys.argv):
    _tag += f"_betrig{em.BREAKEVEN_TRIGGER_R:g}"
if _bel_arg:
    _tag += "_beladder" + "_".join(f"{t:g}-{l:g}" for t, l in em.BREAKEVEN_LADDER)
if any(a.startswith("--trail-tp-buffer=") for a in sys.argv):
    _tag += f"_trailtpbuf{em.TRAIL_TP_ATR_BUFFER:g}"
if any(a.startswith("--trail-tp-trigger=") for a in sys.argv):
    _tag += f"_trailtptrig{em.TRAIL_TP_TRIGGER_PCT:g}"
if any(a.startswith("--be-level=") for a in sys.argv):
    _tag += f"_belevel{em.BREAKEVEN_LEVEL_R:g}"
if div_wick_gold:
    _tag += "_divwickgold"
if div_zone_legacy:
    _tag += "_divzonelegacy"
if _dpt_arg:
    _tag += f"_divpricetol{regime_check.DIV_PRICE_TOLERANCE_ATR:g}"
if _dst_arg:
    _tag += f"_divstall{regime_check.DIV_STALL_THRESHOLD:g}"
if _adb_arg:
    _tag += f"_adxdecl{regime_check.ADX_DECLINE_BARS:g}"
if _amp_arg:
    _tag += f"_adxminpeak{regime_check.ADX_MIN_PEAK_REVERSAL or 0:g}"
if _tex_arg:
    _tag += f"_trailext{em.TRAIL_EXTREME_ATR_MULT:g}"
if _tpl_arg:
    _tag += f"_tplock{em.TP_PROGRESS_LOCK_KEEP:g}"
if _adb2_arg or _ade_arg:
    _tag += f"_adxdir{regime_check.ADX_DIR_BARS:g}-{regime_check.ADX_DIR_EPS:g}"
if scoring_struct_match != cfg.SCORING_NEEDS_STRUCTURE_MATCH:
    _tag += "_structmatch" if scoring_struct_match else "_nostructmatch"
if rev_choch != cfg.REVERSAL_NEEDS_CHOCH:
    _tag += "_revchoch" if rev_choch else "_norevchoch"
if _swr_arg:
    _tag += f"_swingrec{_swing_mod.SWING_RECENCY_TOL_ATR:g}"
# เขียนเสมอเมื่อใส่ --log-cuts แม้ไม่มี cut เลย (ไฟล์มีแต่หัวตาราง) — เดิมเขียนเฉพาะตอนมี cut
# symbol ที่ไม่มีจึงค้างไฟล์รุ่นเก่าไว้เงียบๆ: 2026-09-27 XAU/ETH/US500 ค้างไฟล์ 09-22 ที่มี cut
# ของ slow trade (ปิดไปตั้งแต่ 09-23) ซึ่ง backtest_portfolio อ่านไปคิดความเสี่ยงผิดโดยไม่มี error
# ตอนนี้ cut เหลือแค่ไม้ที่ชนเพดาน 30 วัน symbol ส่วนใหญ่จึงไม่มี cut เลย
if log_cuts:
    _cut_cols = ["symbol", "entry_time", "cut_time", "direction", "strategy", "entry", "sl0", "risk",
                 "price", "R_ตอนตัด", "ตัดไป", "เหลือ", "base_keep", "stage_keep", "กฎที่ยิง",
                 "ชม.ที่ถือมา", "final"]   # ใช้แค่ตอนไม่มี cut — ตอนมี cut คอลัมน์มาจาก dict ใน step_position
    (pd.DataFrame(cut_log) if cut_log else pd.DataFrame(columns=_cut_cols)).to_csv(
        f"replay_cuts_{symbol}{_tag}.csv", index=False)
    print(f"  เขียน log การปิดบางส่วน {len(cut_log)} ครั้งลง replay_cuts_{symbol}{_tag}.csv")
if log_blocked:
    pd.DataFrame(blocked_log).to_csv(f"replay_blocked_{symbol}{_tag}.csv", index=False)
    print(f"  เขียนสัญญาณเงา {len(blocked_log)} รอบลง replay_blocked_{symbol}{_tag}.csv "
          f"(รอบที่ผ่านทุกด่านแต่ช่องไม่ว่าง)")
if log_rr_blocked:
    pd.DataFrame(rr_blocked_log).to_csv(f"replay_rrblocked_{symbol}{_tag}.csv", index=False)
    print(f"  เขียนรอบที่ Reversal ติด R:R {len(rr_blocked_log)} รอบลง replay_rrblocked_{symbol}{_tag}.csv")
if limit_rr:
    pd.DataFrame(limit_log).to_csv(f"replay_limits_{symbol}{_tag}.csv", index=False)
    print(f"  เขียน limit order {len(limit_log)} ตัวลง replay_limits_{symbol}{_tag}.csv")
t.to_csv(f"replay_trades_{symbol}{_tag}.csv", index=False)
if entry_limit or entry_limit_atr or entry_oco_atr:
    print(f"  --entry-limit {entry_limit:g}R/{entry_limit_atr:g}ATR: {entry_limit_stats}")
    pd.DataFrame(entry_limit_missed).to_csv(f"replay_limitmissed_{symbol}{_tag}.csv", index=False)
print(f"  เขียนไม้ทั้งหมดลง replay_trades_{symbol}{_tag}.csv")
