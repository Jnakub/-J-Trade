"""
backtest_replay.py — จำลอง scheduler.scan_symbol() ย้อนหลังทีละชั่วโมง ให้ "เงื่อนไขการเข้าไม้"
ตรงกับระบบจริงมากที่สุดเท่าที่ backtest จะทำได้

ต่างจาก backtest_criteria.py (เครื่องมือวินิจฉัยรายเกณฑ์ ที่จงใจตัดตัวกรองบางอย่างออกเพื่อให้มี
ตัวอย่างพอวัดผลรายเกณฑ์) — ตัวนี้ตั้งใจ **ไม่ตัดอะไรออกเลย** เดินตามลำดับด่านเดียวกับ
scheduler.scan_symbol() เป๊ะ เพื่อให้ตัวเลขที่ได้เอาไปตัดสินใจแทนผลเทรดจริงได้

ลำดับด่าน (ตรงกับ scheduler.scan_symbol ข้อต่อข้อ):
  1. มี position เปิดอยู่ -> ไม่หา entry ใหม่ (ดูแลไม้เดิมแทน)
  3. Daily loss guard — ขาดทุนรวมของ "ไม้ที่ปิดวันนี้" ถึง MAX_DAILY_LOSS แล้วหยุดหาไม้ใหม่
  3b. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่ถึง COOLDOWN_HOURS_BY_SYMBOL ชม.
  4b. News guard — ⚠️ จำลองไม่ได้ (ดู "สิ่งที่ยังต่างจากของจริง" ด้านล่าง)
  4. Regime check — SKIP ถ้า regime อยู่ใน REGIME_NO_TRADE
     REGIME_TREND    -> Scoring  (get_trend_bias + compute_score)
     REGIME_REVERSAL -> Reversal (compute_reversal_score ทิศตามขั้ว divergence)
  4b-2. ผ่านสกอร์การ์ดแล้วดึง TP เข้าไม่ให้ไกลเกิน config.TP_MAX_ATR เท่าของ ATR ตอนเข้าไม้
  4c. Run-up guard — ข้ามไม้ที่ราคาวิ่งไปทางที่จะเข้ามาแล้วเกิน MAX_RUNUP_24H_R (เฉพาะ Scoring)
  5. ผ่านทุกด่าน -> เปิดไม้ที่ราคา ณ ชั่วโมงนั้น + spread

รอบสแกน = ทุก 1 ชั่วโมง ตรงกับ scheduler.INTERVAL_SECONDS (backtest_criteria.py สแกนทุก 4 ชม.
ซึ่งทำให้พลาดจังหวะที่ระบบจริงเข้าได้ 3 ใน 4 ของโอกาส)

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
     --drop="OBV 4H"  ตัดเกณฑ์นี้ออกจากสกอร์การ์ด Scoring (คั่นหลายตัวด้วย ,) — ไว้ตอบว่าเกณฑ์
                 นั้นช่วยหรือถ่วงเมื่อวัดทั้งระบบ ต้องคู่กับ --min-score เสมอเพราะการตัดเกณฑ์
                 ออกโดยไม่ลดเพดานเท่ากับทำให้ด่านเข้มขึ้นไปด้วย = เปลี่ยนสองอย่างพร้อมกัน
     --min-score=X   ทับ MIN_SCORE (ปกติ 5 จาก 6 เกณฑ์)
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
     --rev-min-rr=X  ทับ config.MIN_RR_HARD_BLOCK_REVERSAL (ปัจจุบัน 1.5) — ด่าน R:R ขั้นต่ำ
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
                 ⚠️ คุมเกณฑ์ "RSI extreme" (30/70) ของ Reversal ด้วย (reversal.py:207 เรียก
                 calc_rsi() โดยไม่ส่ง period) รอบที่ใส่ธงนี้จึงขยับสองด่านพร้อมกัน แยกผลไม่ได้
                 ไม่กระทบ RSI_SCORE_PERIOD (สกอร์การ์ด Scoring) และ exit_monitor.RSI_PERIOD
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
from scoring import compute_score, get_trend_bias, get_ohlcv, get_ohlcv_real, calc_rr
from binance import merge_real_volume
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
# ตอนถูกเรียกทุกครั้ง การแก้ตรงนี้จึงมีผลกับ compute_score/reversal ทันทีโดยไม่ต้องแก้ config.py
_min_sl_arg = next((a for a in sys.argv if a.startswith("--min-sl=")), None)
if _min_sl_arg:
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL = dict(config.MIN_SL_DISTANCE_PCT_BY_SYMBOL)
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL[symbol] = float(_min_sl_arg.split("=")[1])

# --drop / --min-score : ทดลองเปลี่ยนรูปสกอร์การ์ดเฉพาะรอบนี้ — compute_score อ่านสองตัวนี้จาก
# scoring ตอนถูกเรียกทุกครั้ง การ set ตรงนี้จึงมีผลทันทีโดยไม่ต้องแก้ scoring.py/config.py
_drop_arg = next((a for a in sys.argv if a.startswith("--drop=")), None)
if _drop_arg:
    _dropped = tuple(s.strip() for s in _drop_arg.split("=", 1)[1].split(","))
    _unknown = [n for n in _dropped if n not in scoring.SCORECARD_CRITERIA_NAMES]
    if _unknown:
        sys.exit(f"--drop: ไม่รู้จักเกณฑ์ {_unknown} — มีให้เลือก "
                 f"{list(scoring.SCORECARD_CRITERIA_NAMES)}")
    scoring.DISABLED_CRITERIA = _dropped
_ms_arg = next((a for a in sys.argv if a.startswith("--min-score=")), None)
if _ms_arg:
    scoring.MIN_SCORE = float(_ms_arg.split("=")[1])

# --legacy-sl : ไม่ขยับ SL ออกจาก SL โครงสร้างเลย (พฤติกรรมก่อน 2026-08-27) — ตั้งที่เดียว
# แล้วมีผลทั้ง exec_sl ที่ส่ง "broker" และด่าน R:R ใน compute_score พร้อมกัน
if legacy_sl:
    scoring.EXEC_SL_ATR_MULT = 0.0

# --rev-min-sl / --rev-tp-from-entry : สวิตช์ทดลองของทาง Reversal อย่างเดียว — compute_reversal_score
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
breakout_mode = "--breakout" in sys.argv
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
blocked_log = []
_rmr_arg = next((a for a in sys.argv if a.startswith("--rev-min-rr=")), None)
if _rmr_arg:
    reversal._MIN_RR_OVERRIDE = float(_rmr_arg.split("=")[1])

# --rev-min-score=N : ทับ reversal.MIN_SCORE_REVERSAL (ปกติ 7/10) — คู่กับ --min-score ของ Scoring
# หมายเหตุโครงสร้าง: regime REVERSAL-READY บังคับ Key Level + Divergence + ADX peak โค้งลง
# อยู่แล้ว = 2+3+1 = 6 แต้มฟรีจาก 7 ที่ต้องการ สกอร์การ์ดจึงตัดจริงแค่ 3% ของรอบที่ถึงมือมัน
# (80/2650 บน 7 symbol) การตั้ง 0 จึงเป็นการปลดด่านที่แทบไม่ได้กรองอะไรอยู่แล้ว
_rms_score_arg = next((a for a in sys.argv if a.startswith("--rev-min-score=")), None)
if _rms_score_arg:
    reversal.MIN_SCORE_REVERSAL = float(_rms_score_arg.split("=")[1])
rev_tp_entry = "--rev-tp-from-entry" in sys.argv
if rev_tp_entry:
    reversal.TP_FROM_ENTRY = True

# --div-max-age / --div-no-volume : คลายด่าน Divergence — check_divergence อ่านค่าจาก regime_check
# ตอนถูกเรียกทุกครั้ง (ทั้งจาก get_regime และจาก compute_reversal_score) การ set ตรงนี้จึงมีผล
# กับทั้งการจัด regime และสกอร์การ์ดพร้อมกัน เหมือนแก้ค่าคงที่จริงแต่เฉพาะรอบนี้
import regime_check
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
# --div-rsi-period=N : ทับ regime_check.DIV_RSI_PERIOD เฉพาะรอบนี้
# ⚠️ ค่านี้ไม่ได้คุมแค่ divergence — reversal.py:207 เรียก calc_rsi() โดยไม่ส่ง period จึงรับ
# ค่านี้เป็น default ด้วย = เกณฑ์ "RSI extreme" (30/70) ของ Reversal ขยับตามไปพร้อมกัน
# (RSI period ยาวขึ้น = แกว่งแคบลง = แตะ 30/70 ยากขึ้น = ด่านนั้นเข้มขึ้นเอง) เวลาอ่านผลรอบนี้
# จึงแยกไม่ได้ว่าอะไรมาจาก divergence อะไรมาจาก RSI extreme — เป็นสองอย่างที่ขยับพร้อมกัน
# แก้ที่โมดูลไม่ใช่ที่ config เพราะ reversal.py/regime_check.py อ่านจาก regime_check ตอนถูกเรียก
_drp_arg = next((a for a in sys.argv if a.startswith("--div-rsi-period=")), None)
if _drp_arg:
    regime_check.DIV_RSI_PERIOD = int(_drp_arg.split("=")[1])
    # ⚠️ ตั้งตัวแปรโมดูลอย่างเดียว **ไม่พอ** — regime_check.calc_rsi ประกาศว่า
    # `def calc_rsi(series, period=DIV_RSI_PERIOD)` default ถูกผูกค่าไว้ตั้งแต่ตอน def
    # การแก้ตัวแปรทีหลังจึงไม่มีผลกับคนที่เรียกแบบไม่ส่ง period (check_divergence:459 และ
    # reversal.py:207 เรียกแบบนั้นทั้งคู่) ต้องแก้ที่ __defaults__ ของตัวฟังก์ชันเอง
    # reversal.calc_rsi เป็น object เดียวกัน (import มาจาก regime_check) จึงถูกแก้ไปพร้อมกัน
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
no_struct_break = "--no-structure-break" in sys.argv
if no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = False
if "--structure-break" in sys.argv and not no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = True
no_trend_inval = "--no-trend-invalidate" in sys.argv
if no_trend_inval:
    em.TREND_CHECK_KEEP_BY_CONSEC = {k: 100 for k in em.TREND_CHECK_KEEP_BY_CONSEC}
# --no-breakeven : ปิดการบังคับเลื่อน SL ไป entry ที่ 1R (checklist ข้อ 5)
# ⚠️ **ไม่ใช่ "ปล่อยให้ ATR trailing คุมแทน"** อย่างที่เคยเขียนไว้ตรงนี้ — วัดแล้วได้ SL ที่แคบลง
# 0 ไม้จาก 188 เพราะ trailing ตรึงฐานที่ swing ตอนเข้า ขึ้นไม่ถึง entry โดยโครงสร้าง
# (ดู BREAKEVEN_ENABLED ที่ exit_monitor) รอบนี้จึงวัด "ไม่มีการดึง SL ให้แคบลงเลย"
# analyze_position อ่าน BREAKEVEN_ENABLED จากโมดูลตอนถูกเรียกทุกครั้ง
# ⚠️ ต้องวัดที่นี่ ไม่ใช่แค่ backtest_exit_rules: ปิด BE ทำให้ไม้ที่เคยจบที่ศูนย์เดินต่อจนถึง
# TP/SL = **ครองช่องนานขึ้น** ซึ่งเป็นสิ่งเดียวที่เครื่องมือตัวนั้นมองไม่เห็นตามนิยามของมัน
no_breakeven = "--no-breakeven" in sys.argv
if no_breakeven:
    em.BREAKEVEN_ENABLED = False

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
print(f"  Cooldown {COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)} ชม.   "
      f"MIN_SL {get_min_sl_distance_pct(symbol)}%{'  [--no-widen]' if no_widen else ''}"
      f"{'  [ทับด้วย --min-sl]' if _min_sl_arg else ''}")
_kept = [n for n in scoring.SCORECARD_CRITERIA_NAMES if n not in scoring.DISABLED_CRITERIA]
print(f"  สกอร์การ์ด {len(_kept)} เกณฑ์ ผ่านที่ {scoring.MIN_SCORE:g}"
      f"{'   ตัดออก: ' + ', '.join(scoring.DISABLED_CRITERIA) if scoring.DISABLED_CRITERIA else ''}")
print(f"  ไม่เข้าไม้เมื่อ regime = {', '.join(REGIME_NO_TRADE)}"
      f"{'   [เพิ่มด้วย --skip-regime]' if _sr_arg else ''}")
if _rms_arg or rev_tp_entry:
    print(f"  Reversal (ทดลอง): MIN_SL "
          f"{reversal.MIN_SL_OVERRIDE if _rms_arg else get_min_sl_distance_pct(symbol)}%"
          f"{'   TP ฉายจากราคาเข้า' if rev_tp_entry else ''}")
print(f"  ด่าน SL: ตรวจ 'ราคาทะลุ swing' ด้วย "
      f"{'close แท่ง 4H ล่าสุด [legacy]' if sl_guard_legacy else 'ราคาที่เข้าไม้จริง'}")
print(f"  Divergence: อายุ swing <= {regime_check.DIV_MAX_AGE_BARS} แท่ง   "
      f"RSI period {regime_check.DIV_RSI_PERIOD} (คุม RSI extreme ของ Reversal ด้วย)"
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
      f"structure break: {'เปิด' if em.STRUCTURE_BREAK_ENABLED else 'ปิด'}"
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
    key = t.date()
    if key not in _df1d_cache:
        _df1d_cache.clear()
        _df1d_cache[key] = merge_real_volume(
            get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t), symbol, "1D", as_of=t)
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
    if strategy == "Breakout":
        strategy = "Reversal"
    return strategy if slot_per_strategy else "ANY"


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
    if m["desired_tp"] is not None:            # TP trailing
        pos["tp"] = m["desired_tp"]

    if (now - pos["time"]) >= timedelta(days=MAX_HOLD_DAYS):
        r = ((bar["close"] - pos["entry"]) if long_ else (pos["entry"] - bar["close"])) / risk
        return close_pos(pos, key, now, pos["booked"] + pos["rem"] * r, "HOLD-CAP")
    return None


def close_pos(pos, key, t, r, how):
    global last_close_time
    if use_cost:
        r -= cost_pct / 100 * pos["entry"] / abs(pos["entry"] - pos["sl0"])   # spread ขาเข้า+ออก ~1 ครั้ง
    rec = {**pos, "exit_time": t, "R": r, "how": how}
    daily_r[t.date()] = daily_r.get(t.date(), 0.0) + r
    last_close_time = t
    positions.pop(key, None)
    return rec


for n, row in enumerate(clock.to_dict("records")):
    t, bar = row["time"], row
    # 2026-09-01: "เวลาที่ระบบตัดสินใจ" คือ **ปลาย** แท่ง 1H นี้ ไม่ใช่ต้นแท่ง — MT5 นับ time ของ
    # แท่ง = เวลาเปิด ดังนั้น bar["close"] คือราคา ณ t+1h เดิมโค้ดเอา close ตัวนี้ไปเป็นราคาเข้า
    # แต่ส่ง as_of=t ให้ทุก scorecard = ตัดสินใจด้วยข้อมูลถึง t แล้วได้ราคาของอีก 1 ชม.ถัดมา
    # (lookahead) ซึ่งไม่ใช่แค่ noise เพราะ entry ตัวนั้นถูกส่งเข้า compute_score ไปคิด R:R ด้วย
    # ไม้ที่ราคาย่อมาเข้าทางในชั่วโมงนั้นจึงผ่านด่าน R:R ง่ายกว่าความจริง = ผลดีเกินจริงอย่างเป็นระบบ
    # แก้โดยเลื่อนเวลาตัดสินใจเป็น now = t + 1h ทั้งหมด (ราคาเข้ายังเป็น bar["close"] เหมือนเดิม
    # ซึ่งตอนนี้กลายเป็น "ราคา ณ วินาทีที่ตัดสินใจ" พอดี ตรงกับ scheduler ที่รันแล้วยิงราคาตลาด)
    now = t + timedelta(hours=1)

    # นับ regime ของ "ทุกรอบสแกน" ก่อนด่านใดๆ — ตารางเหตุผลที่ไม่เข้าด้านล่างนับเฉพาะรอบที่
    # เดินมาถึงด่านนั้นๆ รอบที่ถือไม้อยู่จึงหายไปทั้งหมด ทำให้ตอบไม่ได้ว่า "REVERSAL-READY
    # เกิดกี่ครั้งจริง แล้วตายที่ไหน" (regime_at cache ต่อแท่ง 4H อยู่แล้ว ต้นทุนจึงต่ำ)
    try:
        _rg = regime_at(now)["regime"]
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

    # กลยุทธ์ที่ regime รอบนี้จะเปิด (ไม่มีทางเกิดพร้อมกัน — regime เป็นตัวเลือกให้ตัวเดียว)
    _want = "Scoring" if _rg in REGIME_TREND else ("Reversal" if _rev else None)
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

    try:                                                   # ด่าน 4
        rinfo = regime_at(now)
    except Exception as exc:
        # ใส่ชนิด+ข้อความไว้ด้วย — รอบที่ MT5 หลุดกลางทางเคยขึ้น "regime error" เฉยๆ หลายพัน
        # รอบแล้วผลออกมาดูเหมือนผลปกติ แยกไม่ออกว่าเป็นผลจริงหรือ run เสีย
        note(f"regime error: {type(exc).__name__} {str(exc)[:40]}"); fate("regime error")
        continue
    regime = rinfo["regime"]
    if regime in REGIME_NO_TRADE:
        note(f"regime = {regime}")
        continue

    entry = float(bar["close"])
    try:
        if regime in REGIME_TREND:
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
            score, criteria, passed, sl_info = compute_score(symbol, direction, entry,
                                                            as_of=now, df_1d=df_1d)
            # exec_sl มาจาก compute_score แล้ว (ด่าน R:R ใช้ตัวนี้ตรวจ) ไม่คำนวณซ้ำที่นี่
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
                    # เข้าทาง **Scoring** ไม่ใช่ Reversal โดยตั้งใจ: ไม้ที่ได้คือ "ไปตามเทรนด์ 1D"
                    # = การเทรดต่อเนื่อง ไม่ใช่การกลับตัว และสกอร์การ์ด Reversal จะตัดแต้ม
                    # Divergence/RSI/VSA ทิ้งทั้งหมดอยู่แล้วเพราะขั้ว divergence เป็น bearish
                    # สวนกับทิศ Long (reversal.py:219 · 230 · 239) = ให้คะแนนไม้นี้ไม่ได้เลย
                    # ติด strategy = "Breakout" ไว้ในไฟล์ผล เพื่อแยกออกจากไม้ Scoring ปกติได้
                    if breakout_mode and bias_1d == "Long":
                        direction = "Long"
                        _struct = rinfo["structure"]["trend"]
                        if scoring_struct_match and not _struct.startswith(direction):
                            note(f"flip -> Long สวนโครงสร้าง 4H ({_struct})")
                            fate("flip Long สวนโครงสร้าง 4H")
                            continue
                        score, criteria, passed, sl_info = compute_score(
                            symbol, direction, entry, as_of=now, df_1d=_df1d_rev)
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
                score, criteria, passed, inf = reversal.compute_reversal_score(
                    symbol, direction, entry, key_level=rinfo["key_level"],
                    df_4h=rinfo["df_4h"], as_of=now)
                sl, tp, strategy = inf["sl"], inf["tp"], "Reversal"
                # 2026-09-05: exec_sl มาจาก compute_reversal_score แล้ว (เหมือนทาง Scoring) —
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
        continue
    except Exception as exc:
        note(f"ERROR {type(exc).__name__}")
        continue

    if not passed:
        note(f"ไม่ผ่านสกอร์การ์ด ({strategy})"); fate("ไม่ผ่านสกอร์การ์ด Reversal")
        continue

    # 2026-08-31: SL ที่ส่ง broker มาจาก compute_score แล้ว (sl_info["exec_sl"]) ไม่คำนวณเองซ้ำ —
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

    # เก็บผลรายเกณฑ์ลงไม้ด้วย — ไม่งั้นต้องมาไล่เรียก compute_score ซ้ำทีหลังเพื่อวิเคราะห์
    # รายเกณฑ์ ซึ่งได้ค่าจาก scoring.py "ณ วันที่วิเคราะห์" ไม่ใช่ตัวที่กรองไม้นี้จริงตอน replay
    # (ถ้าสกอร์การ์ดถูกแก้ระหว่างนั้น ตัวเลขจะไม่ตรงกับไม้ที่ได้มาโดยที่ไม่มีอะไรฟ้อง)
    # ชื่อคอลัมน์ = ชื่อเกณฑ์ตรงๆ ฝั่ง Scoring/Reversal คนละชุด อีกฝั่งจึงเป็นค่าว่าง
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

    positions[slot_of(strategy)] = {"time": now, "direction": direction, "entry": entry, "sl": sl, "sl0": sl,
           # tp0 = TP ที่ส่งจริงตอนเข้า (ผ่านเพดานแล้ว)  tp_fib = ที่ Fibonacci ให้ก่อนเพดาน
           # สองค่านี้ต่างกันเมื่อไม้นั้นโดนเพดานดึงเข้า — ดู comment ที่จุดคำนวณ tp_fib
           "tp": tp, "tp0": tp, "tp_fib": tp_fib, "score": score, "strategy": strategy, "regime": regime,
           **{name: bool(ok) for name, ok, _ in criteria},
           "booked": 0.0, "rem": 1.0, "cuts": 0,
           # pinned_swing = SL โครงสร้าง (ถอย exec_sl กลับด้วยตัวคูณเดียวกับที่ขยับออกไป)
           "pinned_swing": _pin,
           "pinned_atr_entry": atr_entry}

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
# รอบที่เปลี่ยนรูปสกอร์การ์ดเขียนคนละไฟล์ — ไม่งั้นทับผลรอบปกติที่เอาไว้เทียบ
_tag = ""
if scoring.DISABLED_CRITERIA:
    _tag = "_drop-" + "-".join(n.replace(" ", "") for n in scoring.DISABLED_CRITERIA)
if _ms_arg:
    _tag += f"_min{scoring.MIN_SCORE:g}"
if _sr_arg:
    _tag += "_skip-" + "-".join(r.replace(" ", "") for r in _skip)
if _rms_arg:
    _tag += f"_revminsl{reversal.MIN_SL_OVERRIDE:g}"
if _rmr_arg:
    _tag += f"_revminrr{reversal._MIN_RR_OVERRIDE:g}"
if _rms_score_arg:
    _tag += f"_revminscore{reversal.MIN_SCORE_REVERSAL:g}"
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
if breakout_mode:
    _tag += "_breakout"
if rev_tp_entry:
    _tag += "_revtpentry"
if regime_check.DIV_MAX_AGE_BARS != _DIV_AGE_LIVE:    # ติด tag เฉพาะรอบที่สวนค่าระบบจริง
    _tag += f"_divage{regime_check.DIV_MAX_AGE_BARS}"
if _dml_arg:      # ติด tag ทุกครั้งที่ส่งธง รวม 180 ที่เป็น identity check (กันทับไฟล์ base)
    _tag += f"_divlook{regime_check.DIV_MAX_LOOKBACK_BARS}"
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
if _mh_arg:
    _tag += "_nomaxhold" if MAX_HOLD_DAYS >= 1e6 else f"_maxhold{MAX_HOLD_DAYS:g}"
if live_spread:      # ผลรอบนี้ขึ้นกับเวลาที่รัน — อย่าให้ทับไฟล์ base ที่เทียบข้ามรอบได้
    _tag += "_livespread"
if no_breakeven:
    _tag += "_nobe"
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
if log_cuts and cut_log:
    pd.DataFrame(cut_log).to_csv(f"replay_cuts_{symbol}{_tag}.csv", index=False)
    print(f"  เขียน log การปิดบางส่วน {len(cut_log)} ครั้งลง replay_cuts_{symbol}{_tag}.csv")
if log_blocked:
    pd.DataFrame(blocked_log).to_csv(f"replay_blocked_{symbol}{_tag}.csv", index=False)
    print(f"  เขียนสัญญาณเงา {len(blocked_log)} รอบลง replay_blocked_{symbol}{_tag}.csv "
          f"(รอบที่ผ่านทุกด่านแต่ช่องไม่ว่าง)")
t.to_csv(f"replay_trades_{symbol}{_tag}.csv", index=False)
print(f"  เขียนไม้ทั้งหมดลง replay_trades_{symbol}{_tag}.csv")
