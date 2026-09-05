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
  5. ผ่านสกอร์การ์ด -> เปิดไม้ที่ราคา ณ ชั่วโมงนั้น + spread

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
     --rev-tp-from-entry  ฉาย Fibonacci TP ของ Reversal จากราคาเข้าแทน swing B — ไว้ตอบว่า
                 การที่ reward หดตามระยะที่ราคาห่างจาก swing (จน TP ไปโผล่หลัง entry 22-28%
                 ของ setup) เป็นตัวที่ทำให้ไม้ Reversal เข้าน้อยหรือเปล่า
                 ⚠️ ทั้งสองตัวคือการ "ผ่อนด่าน" ไม่ใช่แก้บั๊ก — ต้องดู WR คู่กับ Total R เสมอ
     --rule-1r-keep=N  ทับ exit_monitor.RULE_1R_KEEP (ปกติ 50) — พอกำไรแตะ 1R เหลือไม้ไว้กี่ %
                 (100 = ไม่ตัดเลย) ไว้ตอบว่ากฎล็อกกำไรครึ่งไม้ที่ 1R คุ้มไหม เพราะไม้ที่ชนะ
                 ของ Scoring วิ่งเฉลี่ยถึง +2.25R แต่เก็บได้จริงแค่ 30% ของนั้น
     --rule-halfway-keep=N  ทับ exit_monitor.RULE_HALFWAY_KEEP (ปกติ 60) — กฎ "เดินทาง >=50%
                 ไป TP แล้วตัด 40%" ซึ่งยิงใส่เฉพาะไม้ที่กำลังวิ่งเข้าหา TP = ไม้ที่ชนะ
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
     --div-max-age=N  ทับ DIV_MAX_AGE_BARS (ปกติ 14) — swing ของ divergence เก่าได้กี่แท่ง
     --div-no-volume  หา swing สำหรับ divergence โดยไม่กรอง volume/wick
                 สองตัวนี้คลายด่าน Divergence ซึ่งเป็นด่านที่ตัดโอกาส Reversal ทิ้งมากที่สุด
                 (มีผลกับ regime ด้วย: REVERSAL-WATCH จะกลายเป็น REVERSAL-READY มากขึ้น)
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
                    COOLDOWN_HOURS_BY_SYMBOL, get_min_sl_distance_pct)
import scoring
from scoring import compute_score, get_trend_bias, get_ohlcv, get_ohlcv_real, calc_rr
from binance import merge_real_volume
from bars import BAR_OFFSET_H
from regime_check import get_regime
import exit_monitor as em
import reversal

REGIME_NO_TRADE = ("CHOPPY", "เขตเทา", "REVERSAL-WATCH", "TREND แรงจัด")   # ตรงกับ scheduler.py
REGIME_TREND    = ("TREND",)   # 2026-08-31: "TREND แรงจัด" ย้ายไป NO_TRADE — ดูเหตุผลใน scheduler.py
REGIME_REVERSAL = ("REVERSAL-READY",)

# เพดานถือไม้ของ backtest เอง — ระบบจริงไม่มีเพดานเวลา (กฎ slow-trade ตัดแค่ 50%) ตั้งไว้กัน
# ไม้ค้างกินเวลารันเท่านั้น ถ้ามีไม้ชนเพดานบ่อยแปลว่าต้องขยาย
MAX_HOLD_DAYS   = 30


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
rev_tp_entry = "--rev-tp-from-entry" in sys.argv
if rev_tp_entry:
    reversal.TP_FROM_ENTRY = True

# --div-max-age / --div-no-volume : คลายด่าน Divergence — check_divergence อ่านค่าจาก regime_check
# ตอนถูกเรียกทุกครั้ง (ทั้งจาก get_regime และจาก compute_reversal_score) การ set ตรงนี้จึงมีผล
# กับทั้งการจัด regime และสกอร์การ์ดพร้อมกัน เหมือนแก้ค่าคงที่จริงแต่เฉพาะรอบนี้
import regime_check
_dma_arg = next((a for a in sys.argv if a.startswith("--div-max-age=")), None)
if _dma_arg:
    regime_check.DIV_MAX_AGE_BARS = int(_dma_arg.split("=")[1])
div_no_vol = "--div-no-volume" in sys.argv
if div_no_vol:
    regime_check.DIV_SWING_VOL_FILTER = False

# --rule-1r-keep : ทับกฎ Position Sizing ข้อ 1 ของ exit_monitor — analyze_position อ่านค่าจาก
# โมดูลตอนประกอบ position_rules ทุกครั้งที่ถูกเรียก การ set ตรงนี้จึงมีผลกับทุกไม้ในรอบนี้
_1rk_arg = next((a for a in sys.argv if a.startswith("--rule-1r-keep=")), None)
if _1rk_arg:
    em.RULE_1R_KEEP = float(_1rk_arg.split("=")[1])
_hwk_arg = next((a for a in sys.argv if a.startswith("--rule-halfway-keep=")), None)
if _hwk_arg:
    em.RULE_HALFWAY_KEEP = float(_hwk_arg.split("=")[1])
# ปิดกฎ trend invalidation: คง code path เดิมไว้ทุกบรรทัด แค่ให้ทุกระดับความต่อเนื่องคืน 100%
# ปิดกฎ structure break: patch ที่ตัวฟังก์ชันเลย — analyze_position เรียกผ่านชื่อใน module
# globals ทุกครั้ง การแทนที่ตรงนี้จึงมีผลทันทีโดยไม่ต้องแก้ exit_monitor.py
_runup_arg = next((a for a in sys.argv if a.startswith("--max-runup-24h=")), None)
# default มาจาก config.MAX_RUNUP_24H_R (ค่าที่ระบบจริงใช้) — ธงมีไว้ทับเฉพาะรอบทดลองเท่านั้น
max_runup = float(_runup_arg.split("=")[1]) if _runup_arg else config.MAX_RUNUP_24H_R
no_struct_break = "--no-structure-break" in sys.argv
if no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = False
if "--structure-break" in sys.argv and not no_struct_break:
    em.STRUCTURE_BREAK_ENABLED = True
no_trend_inval = "--no-trend-invalidate" in sys.argv
if no_trend_inval:
    em.TREND_CHECK_KEEP_BY_CONSEC = {k: 100 for k in em.TREND_CHECK_KEEP_BY_CONSEC}

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
spread_price = (info.ask - info.bid) if info else 0.0
if spread_price <= 0 and info:
    spread_price = info.spread * info.point
cost_pct = (spread_price / info.bid * 100) if info and info.bid else 0.0

h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=days * 24 + 500)
h4 = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=days * 6 + 400)
end_time   = h1["time"].iloc[-1]
start_time = end_time - timedelta(days=days)
clock = h1[h1["time"] >= start_time].reset_index(drop=True)
h4_idx = {t: i for i, t in enumerate(h4["time"])}

max_daily_loss_r = MAX_DAILY_LOSS / RISK_PER_TRADE   # 6% / 2% = 3R ต่อวัน

print(f"\n{symbol}  replay backtest  {clock['time'].iloc[0]} -> {end_time}")
print(f"  สแกนทุก 1 ชม. ({len(clock)} รอบ)   spread ปัจจุบัน {spread_price:.2f} "
      f"({cost_pct:.4f}% ของราคา){'  [คิดต้นทุน]' if use_cost else '  [--no-cost]'}")
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
if _dma_arg or div_no_vol:
    print(f"  Divergence (ทดลอง): อายุ swing <= {regime_check.DIV_MAX_AGE_BARS} แท่ง"
          f"{'   ไม่กรอง volume/wick' if div_no_vol else ''}")
# พิมพ์สถานะกฎ exit ทุกรอบเสมอ (ไม่ใช่เฉพาะรอบที่ใส่ธง) — ตั้งแต่ 2026-09-03 ที่ structure
# break ถูกปิดเป็น default การอ่าน log เก่าเทียบใหม่โดยไม่รู้ว่ารอบนั้นกฎเปิดหรือปิดจะหลงทางได้
if max_runup is not None:
    print(f"  Entry: ข้ามรอบที่ราคาวิ่งไปทางที่จะเข้าเกิน {max_runup:g}R ใน 24 แท่ง 1H (เฉพาะ Scoring)"
          f"{'   [ทับด้วย --max-runup-24h]' if _runup_arg else ''}")
print(f"  Exit: ถึง 1R เหลือ {em.RULE_1R_KEEP:g}%   ครึ่งทางไป TP เหลือ {em.RULE_HALFWAY_KEEP:g}%   "
      f"structure break: {'เปิด' if em.STRUCTURE_BREAK_ENABLED else 'ปิด'}"
      f"{'   ปิดกฎ trend invalidation' if no_trend_inval else ''}")
print()

pos = None
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


def step_position(t, bar, now):
    """เดินไม้ที่ถืออยู่ไป 1 ชั่วโมง — broker เช็ค SL/TP ระหว่างแท่ง t->now ก่อน แล้ว
    exit_monitor ตัวจริงทำงานที่ปลายชั่วโมง (now) ตรงกับ INTERVAL_SECONDS=3600 ของระบบจริง"""
    global pos
    long_ = pos["direction"] == "Long"
    risk = abs(pos["entry"] - pos["sl0"])      # ระยะเสี่ยงตอนเข้า = ฐาน 1R ของบัญชี

    # 1) broker: SL/TP ทำงานระหว่างแท่งเสมอ ไม่ต้องรอ monitor
    if (bar["low"] <= pos["sl"]) if long_ else (bar["high"] >= pos["sl"]):
        r = ((pos["sl"] - pos["entry"]) if long_ else (pos["entry"] - pos["sl"])) / risk
        how = "BE" if abs(pos["sl"] - pos["entry"]) < 1e-9 else "SL"
        return close_pos(now, pos["booked"] + pos["rem"] * r, how)
    if (bar["high"] >= pos["tp"]) if long_ else (bar["low"] <= pos["tp"]):
        r = ((pos["tp"] - pos["entry"]) if long_ else (pos["entry"] - pos["tp"])) / risk
        return close_pos(now, pos["booked"] + pos["rem"] * r, "TP")

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
        pos["booked"] += cut * r_now
        pos["rem"] = keep
        pos["cuts"] += 1
        if pos["rem"] <= 1e-9:
            return close_pos(now, pos["booked"], m["final_decision"][0][:24])

    if m["desired_sl"] is not None:            # ATR trailing / breakeven
        new_sl = m["desired_sl"]
        if no_widen:                           # ห้ามถอย SL ออกไกลกว่าตอนเข้า
            new_sl = max(new_sl, pos["sl0"]) if long_ else min(new_sl, pos["sl0"])
        pos["sl"] = new_sl
    if m["desired_tp"] is not None:            # TP trailing
        pos["tp"] = m["desired_tp"]

    if (now - pos["time"]) >= timedelta(days=MAX_HOLD_DAYS):
        r = ((bar["close"] - pos["entry"]) if long_ else (pos["entry"] - bar["close"])) / risk
        return close_pos(now, pos["booked"] + pos["rem"] * r, "HOLD-CAP")
    return None


def close_pos(t, r, how):
    global pos, last_close_time
    if use_cost:
        r -= cost_pct / 100 * pos["entry"] / abs(pos["entry"] - pos["sl0"])   # spread ขาเข้า+ออก ~1 ครั้ง
    rec = {**pos, "exit_time": t, "R": r, "how": how}
    daily_r[t.date()] = daily_r.get(t.date(), 0.0) + r
    last_close_time = t
    pos = None
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

    if pos is not None:                                   # ด่าน 1
        fate("ถือไม้อื่นอยู่")
        rec = step_position(t, bar, now)
        if rec:
            trades.append(rec)
        continue

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
            score, criteria, passed, sl_info = compute_score(symbol, direction, entry,
                                                            as_of=now, df_1d=df_1d)
            # exec_sl มาจาก compute_score แล้ว (ด่าน R:R ใช้ตัวนี้ตรวจ) ไม่คำนวณซ้ำที่นี่
            sl, tp, strategy = sl_info["sl"], sl_info["tp"], "Scoring"
            exec_sl, atr_entry_ = sl_info["exec_sl"], sl_info["atr_entry"]
        elif regime in REGIME_REVERSAL:
            pol = rinfo["divergence"]["divergence"]
            direction = "Long" if pol == "bullish" else "Short"
            score, criteria, passed, inf = reversal.compute_reversal_score(
                symbol, direction, entry, key_level=rinfo["key_level"],
                df_4h=rinfo["df_4h"], as_of=now)
            sl, tp, strategy = inf["sl"], inf["tp"], "Reversal"
            # ฝั่ง Reversal ยังคำนวณ exec_sl ที่นี่เหมือนเดิม — compute_reversal_score ไม่ได้ย้าย
            # ตามมา (ด่าน R:R ของ Reversal เป็นคนละชุด ไม่ได้อยู่ในขอบเขตการแก้ครั้งนี้)
            exec_sl, atr_entry_ = sl, None
            if scoring.EXEC_SL_ATR_MULT:
                try:
                    _tr = em.calc_atr_trailing_sl(get_ohlcv_real(symbol, "4H", bars=210, as_of=now),
                                                  symbol, now, direction, as_of=now)
                    if _tr:
                        atr_entry_ = _tr["atr_entry"]
                        exec_sl = (sl - scoring.EXEC_SL_ATR_MULT * atr_entry_) if direction == "Long" \
                                  else (sl + scoring.EXEC_SL_ATR_MULT * atr_entry_)
                except Exception:
                    pass
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

    # เก็บผลรายเกณฑ์ลงไม้ด้วย — ไม่งั้นต้องมาไล่เรียก compute_score ซ้ำทีหลังเพื่อวิเคราะห์
    # รายเกณฑ์ ซึ่งได้ค่าจาก scoring.py "ณ วันที่วิเคราะห์" ไม่ใช่ตัวที่กรองไม้นี้จริงตอน replay
    # (ถ้าสกอร์การ์ดถูกแก้ระหว่างนั้น ตัวเลขจะไม่ตรงกับไม้ที่ได้มาโดยที่ไม่มีอะไรฟ้อง)
    # ชื่อคอลัมน์ = ชื่อเกณฑ์ตรงๆ ฝั่ง Scoring/Reversal คนละชุด อีกฝั่งจึงเป็นค่าว่าง
    pos = {"time": now, "direction": direction, "entry": entry, "sl": sl, "sl0": sl,
           "tp": tp, "tp0": tp, "score": score, "strategy": strategy, "regime": regime,
           **{name: bool(ok) for name, ok, _ in criteria},
           "booked": 0.0, "rem": 1.0, "cuts": 0,
           # pinned_swing = SL โครงสร้าง (ถอย exec_sl กลับด้วยตัวคูณเดียวกับที่ขยับออกไป)
           "pinned_swing": sl + scoring.EXEC_SL_ATR_MULT * atr_entry if (atr_entry and direction == "Long")
                           else (sl - scoring.EXEC_SL_ATR_MULT * atr_entry if atr_entry else sl),
           "pinned_atr_entry": atr_entry}

    if n % 2000 == 0:
        print(f"  ... {t}  ({n}/{len(clock)})  ปิดไปแล้ว {len(trades)} ไม้", flush=True)

mt5.shutdown()

# ---------------------------------------------------------------------------
t = pd.DataFrame(trades)
print(f"\n{'=' * 78}")
print(f"  รอบสแกน {len(clock)}  ->  เข้าไม้จริง {len(t)} ไม้"
      f"{'  (ยังถือค้าง 1 ไม้)' if pos else ''}")
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

t["win"] = t["R"] > 0
print(f"  {'-' * 74}")
print(f"  Win Rate   : {t['win'].mean()*100:.1f}%  ({int(t['win'].sum())}/{len(t)})")
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
if rev_tp_entry:
    _tag += "_revtpentry"
if _dma_arg:
    _tag += f"_divage{regime_check.DIV_MAX_AGE_BARS}"
if div_no_vol:
    _tag += "_divnovol"
if _1rk_arg:
    _tag += f"_1rkeep{em.RULE_1R_KEEP:g}"
if _hwk_arg:
    _tag += f"_hwkeep{em.RULE_HALFWAY_KEEP:g}"
if no_trend_inval:
    _tag += "_notrendinval"
if "--structure-break" in sys.argv and not no_struct_break:
    _tag += "_structbreak"
if _runup_arg:
    _tag += f"_runup{max_runup:g}"
t.to_csv(f"replay_trades_{symbol}{_tag}.csv", index=False)
print(f"  เขียนไม้ทั้งหมดลง replay_trades_{symbol}{_tag}.csv")
