"""
scheduler.py — รันค้างไว้ เช็คทุก 1 ชั่วโมงอัตโนมัติ
ใช้: python scheduler.py
"""
import sys
import time
from datetime import datetime

import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import journal
from config import (
    SYMBOLS, RISK_PER_TRADE,
    MAX_DAILY_LOSS, MIN_SCORE, TOTAL_WEIGHT, MT5_TIMEFRAMES,
    COOLDOWN_HOURS_BY_SYMBOL, MAX_RUNUP_24H_R, TP_MAX_ATR,
    SLOT_PER_STRATEGY, REVERSAL_SHORT_NEEDS_1D_TREND,
    MAX_PORTFOLIO_RISK_R, MAX_GROUP_RISK_R, CORRELATION_GROUPS,
    SCORING_NEEDS_STRUCTURE_MATCH, REVERSAL_NEEDS_CHOCH,
)
from mt5_connect import connect, get_account_balance
from scoring import compute_score, calc_rr, get_ohlcv, get_trend_bias
from order import calculate_lot_size, clamp_lot, place_order, position_risk_amount
from binance import merge_real_volume
from exit_monitor import (
    check_structure_break,
    analyze_position, print_report, execute_decision,
    check_upcoming_news, NEWS_IMMINENT_H, NEWS_IMPACT, NEWS_CURRENCY,
)
from regime_check import get_regime
import reversal
import notify
from logger_setup import get_logger, tee_print

log = get_logger("scheduler")
print = tee_print(log)   # เขียนทุกอย่างที่ print ลง logs/scheduler.log ด้วย (ดู logger_setup.py)

# Regime ที่ "ไม่เปิด" scorecard ใดๆ — รอความชัดเจนก่อน (ตาม Mutual Exclusivity ที่ตั้งไว้)
#
# 2026-08-31: **เพิ่ม "TREND แรงจัด" เข้าด่านห้ามเข้าไม้** ตามคำสั่งผู้ใช้ (เดิมอยู่ใน
# REGIME_TREND = เปิด Scoring ตามเทรนด์) เหตุผลเชิงกลไก: ด่านนี้ถูกเขียนไว้เพื่อ "ห้ามสวน"
# ตอน ADX 40+ ที่ยังพุ่ง (ดู regime_check.py:507) แต่ผลข้างเคียงคือมันกลายเป็นไฟเขียวให้
# เข้าตามเทรนด์ตอน ADX แรงที่สุด = entry ท้ายขา ซึ่งไม่ใช่เจตนาเดิมของด่าน
#
# ⚠️ **หลักฐานบางมาก — เป็นการตัดสินใจของผู้ใช้ ไม่ใช่ข้อสรุปจาก backtest**
# ไม้ที่เคยเข้าใน regime นี้มีแค่ 4 ไม้ (BTC 3 + XAU 1) แพ้ทั้ง 4 ไม้ รวม -2.35R
# replay BTCUSDm 730 วัน (สะอาด ไม่มี regime error/volume fallback):
#   baseline          28 ไม้ +3.43R | Scoring 22 ไม้ Win 50% -0.37R
#   ปิด regime นี้     26 ไม้ +5.02R | Scoring 20 ไม้ Win 55% +1.23R   (สุทธิ +1.60R)
#   ตัดออก 3 ไม้ (-2.21R) รับเพิ่ม 1 ไม้ (-0.61R)
# แต่: P(ดีกว่า) = 61%  bootstrap 95% CI [-9.4, +12.6]R = แยกจาก noise ไม่ได้
# และกลุ่มนี้ถูกพบจากการไล่ดูข้อมูลหลังเห็นผลแล้ว (post-hoc) ไม่ได้ตั้งสมมติฐานไว้ก่อน —
# regime มี 4 กลุ่มให้เลือกมอง ปรับ multiple comparison แล้วโอกาสเกิดเองอยู่ราว 16%
# **ยังไม่ได้ทดสอบ out-of-sample เลย** (XAU/ETH/XRP/US500 ยังไม่ได้รันด้วย --skip-regime)
# 👉 เฝ้าดูผลจริงใกล้ชิด — ถอยกลับ = ย้าย "TREND แรงจัด" กลับไป REGIME_TREND (แก้ที่นี่
#    ที่ config.REGIME_NO_TRADE ที่เดียว — ทุกไฟล์อ่านจากที่นั่น ไม่มีสำเนาให้ลืมแก้อีกแล้ว)
from config import REGIME_NO_TRADE, REGIME_TREND, REGIME_REVERSAL   # noqa: E402  (ดู config)

INTERVAL_SECONDS = 3600   # เช็คทุก 1 ชั่วโมง


# ---------------------------------------------------------------------------
# สแกนและส่ง order ถ้าผ่าน
# ---------------------------------------------------------------------------

def check_portfolio_risk(symbol: str, balance: float) -> tuple[bool, str]:
    """เพดานความเสี่ยงที่เปิดค้างอยู่ *ทั้งพอร์ต* — ด่านเดียวที่มองข้าม symbol

    ด่านอื่นทุกตัวมองทีละ symbol: scan_symbol เรียก positions_get(symbol=...),
    SLOT_PER_STRATEGY คุมแค่ช่องในตัวเอง, MAX_DAILY_LOSS นับเฉพาะไม้ที่ปิดไปแล้ววันนี้
    ไม่มีใครเห็นภาพรวมว่าตอนนี้เปิดความเสี่ยงค้างอยู่เท่าไหร่ — ดู comment ที่
    config.MAX_PORTFOLIO_RISK_R สำหรับตัวเลขที่วัดมา

    คิดจาก "ความเสี่ยงที่ยังมีชีวิต" (SL ปัจจุบันเทียบ entry) ไม่ใช่จำนวนไม้ ไม้ที่ขยับ
    SL ไป breakeven แล้วจึงคืนโควตาให้ไม้ใหม่เองอัตโนมัติ

    ไม้ใหม่ถูกคิดเป็น 1R เต็มเสมอ (= RISK_PER_TRADE) ตรงกับตอนคิด lot ที่ยังไม่ถูก
    clamp_lot() ปัด — ด่านนี้อยู่ก่อนการวิเคราะห์ทั้งหมดจึงยังไม่รู้ lot จริง ซึ่งตรงกับ
    backtest_portfolio.py ที่วัดค่า 3.0R มา
    """
    risk_unit = balance * RISK_PER_TRADE
    if risk_unit <= 0:
        return False, f"risk unit ไม่ถูกต้อง (balance={balance:.2f})"

    positions = mt5.positions_get()
    if not positions:
        return True, ""

    total_r, group_r = 0.0, 0.0
    my_group = CORRELATION_GROUPS.get(symbol)
    for pos in positions:
        direction = "Long" if pos.type == mt5.POSITION_TYPE_BUY else "Short"
        if not pos.sl:
            # SL หลุด/ยังไม่ได้ตั้ง = ความเสี่ยงไม่มีขอบเขต ไม่ใช่ศูนย์ — คิดเป็น 1R ไว้ก่อน
            # แล้วเตือน (ของจริงแย่กว่านี้ แต่ประเมินให้เกินไปกว่านี้ก็เดาเอาทั้งนั้น)
            print(f"  [{symbol}] WARNING — #{pos.ticket} ({pos.symbol}) ไม่มี SL "
                  f"— นับเป็น 1R ในเพดานความเสี่ยง")
            r = 1.0
        else:
            try:
                r = position_risk_amount(pos.symbol, direction, pos.price_open,
                                         pos.sl, pos.volume) / risk_unit
            except RuntimeError as exc:
                print(f"  [{symbol}] WARNING — คิดความเสี่ยงของ #{pos.ticket} ไม่ได้ "
                      f"({exc}) — นับเป็น 1R")
                r = 1.0
        total_r += r
        if my_group and CORRELATION_GROUPS.get(pos.symbol) == my_group:
            group_r += r

    if total_r + 1.0 > MAX_PORTFOLIO_RISK_R + 1e-9:
        return False, (f"เพดานความเสี่ยงรวมทั้งพอร์ต — เปิดค้างอยู่ {total_r:.2f}R "
                       f"+ ไม้ใหม่ 1R > {MAX_PORTFOLIO_RISK_R}R "
                       f"({MAX_PORTFOLIO_RISK_R * RISK_PER_TRADE * 100:.0f}% ของพอร์ต)")

    if my_group and group_r + 1.0 > MAX_GROUP_RISK_R + 1e-9:
        return False, (f"เพดานความเสี่ยงกลุ่ม {my_group} — เปิดค้างอยู่ {group_r:.2f}R "
                       f"+ ไม้ใหม่ 1R > {MAX_GROUP_RISK_R}R")

    return True, ""


def scan_symbol(symbol: str) -> None:
    print(f"\n  [{symbol}] กำลังวิเคราะห์...")

    # 1. มี position เปิดอยู่แล้ว? -> รัน Exit Monitor ดูแลไม้เดิมก่อนเสมอ
    #    SLOT_PER_STRATEGY=True: ช่องแยกตามกลยุทธ์ ไม้ Scoring ที่เปิดอยู่จึงไม่บล็อกไม้ Reversal
    #    (และกลับกัน) ด่านจริงอยู่ที่ข้อ 4b หลัง regime บอกแล้วว่ารอบนี้จะเปิดกลยุทธ์ไหน
    #    occupied ถูกอ่าน *ก่อน* รัน Exit Monitor โดยตั้งใจ — ตรงกับ backtest_replay ที่ snapshot
    #    ช่องก่อนเดินไม้ ผลคือไม้ที่เพิ่งถูกปิดในรอบนี้จะยังไม่เปิดไม้ใหม่ทับทันทีในชั่วโมงเดียวกัน
    positions = mt5.positions_get(symbol=symbol)
    occupied = set()
    if positions:
        for pos in positions:
            occupied.add(journal.get_trade_strategy(pos.ticket))
        print(f"  [{symbol}] มี position เปิดอยู่ {len(positions)} ไม้ "
              f"({', '.join(sorted(occupied))}) -> รัน Exit Monitor")
        for pos in positions:
            try:
                m = analyze_position(pos)
                print_report(m)
                execute_decision(m)
            except Exception as exc:
                print(f"  [{symbol}] Exit Monitor ERROR — {exc}")
                log.error(f"[{symbol}] Exit Monitor ERROR", exc_info=True)
        if not SLOT_PER_STRATEGY:
            return
        if occupied >= {"Scoring", "Reversal"}:
            print(f"  [{symbol}] ช่องเต็มทั้งสองกลยุทธ์ -> ไม่หา entry ใหม่")
            return

    # 1b. โบรกปิดเทรด symbol นี้ไว้ไหม (trade_mode != FULL) — เช็คก่อนวิเคราะห์อะไรเลย
    # 2026-08-15: เจอ XRPUSDm ถูก Exness ปิดเทรดบนเซิร์ฟเวอร์ trial (trade_mode=DISABLED)
    # ทั้งที่ยังส่ง quote ปกติ — ระบบเคยวิเคราะห์เต็มรูปแบบทุกชั่วโมง (ดึง 1D/4H/1H + merge
    # real volume) แล้วพังตอนส่ง order เป็น retcode=10017 [Trade disabled] ทุกรอบ กันไว้ตรงนี้
    # ก่อนเสียเวลาวิเคราะห์ — ไม่เอา symbol ออกจาก SYMBOLS เพราะถ้าโบรกเปิดกลับมาจะได้เทรดต่อ
    # อัตโนมัติทันทีโดยไม่ต้องแก้ config
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"  [{symbol}] ERROR — หา symbol info ไม่ได้")
        return
    if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        print(f"  [{symbol}] SKIP — โบรกปิดเทรด symbol นี้ไว้ (trade_mode={info.trade_mode}) "
              f"— เช็คสถานะที่ MT5 Market Watch หรือถามโบรกเรื่อง instrument นี้")
        return

    # 2. ดึงยอดเงินจริงจากบัญชี
    try:
        balance = get_account_balance()
    except RuntimeError as exc:
        print(f"  [{symbol}] ERROR — {exc}")
        return

    # 3. Daily loss guard (เช็คเฉพาะตอนจะหา entry ใหม่)
    if not journal.check_daily_loss(balance, MAX_DAILY_LOSS):
        print(f"  [{symbol}] SKIP — daily loss limit reached")
        return

    # 3b. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่นาน (ดู config.COOLDOWN_HOURS_BY_SYMBOL)
    #     2026-08-13: **บั๊กที่เพิ่งเจอ** — เดิม check_cooldown() ถูกเรียกจาก bot.py เท่านั้น
    #     แต่ไม่มีไฟล์ไหน import bot.py เลย (dead code) ส่วน scheduler.py ที่รันจริงไม่เคยเรียก
    #     => cooldown ไม่เคยทำงานจริงเลยตั้งแต่ตั้งค่ามา COOLDOWN_HOURS_BY_SYMBOL เป็นแค่ตัวเลข
    #     ประดับ เคสที่ทำให้เจอ: XAUUSDm ปิดไม้ 00:20 (Manual Cut, -2.00) แล้วเปิดไม้ใหม่ 01:30
    #     วันเดียวกัน = ห่างแค่ 70 นาที ทั้งที่ตั้ง cooldown ไว้ 48 ชม.
    #     วางไว้ก่อน News guard เพราะอ่าน CSV อย่างเดียว (ถูกกว่า) ส่วน News ต้องยิงเน็ตไป
    #     ForexFactory — ตัดจบตรงนี้ได้ก็ไม่ต้องเสียเวลาไปเรียก
    #     ต้องอยู่หลัง reconcile_closed_positions() (เรียกใน run_scheduler ต้นรอบ) เสมอ ไม่งั้น
    #     ไม้ที่ชน SL/TP เองจะยังค้างเป็น 'Open' และมองไม่เห็นเวลาปิดจริง
    cooldown_hours = COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)
    ok, cooldown_reason = journal.check_cooldown(symbol, cooldown_hours)
    if not ok:
        print(f"  [{symbol}] SKIP — {cooldown_reason}")
        return

    # 4. Balance ต้องมากกว่า 0 (risk amount = balance * RISK_PER_TRADE เป็นสัดส่วนของ balance เอง
    # เสมออยู่แล้ว จุดที่พังจริงคือ balance <= 0 ไม่ใช่สัดส่วน)
    if balance <= 0:
        print(f"  [{symbol}] SKIP — balance ไม่พอ ({balance:.2f})")
        return

    # 4a. เพดานความเสี่ยงระดับพอร์ต — ต้องอยู่หลังเช็ค balance (หารด้วย risk unit) แต่ก่อน
    #     News guard ที่ต้องยิงเน็ต และก่อน regime/สกอร์การ์ดที่ต้องดึงบาร์ 1D/4H/1H
    #     อ่าน positions_get() ของทั้งพอร์ตอย่างเดียว ถูกกว่าทุกด่านที่ตามมา
    ok, risk_reason = check_portfolio_risk(symbol, balance)
    if not ok:
        print(f"  [{symbol}] SKIP — {risk_reason}")
        return

    # 4b. News guard — ไม่เปิดไม้ใหม่ถ้าข่าว High Impact (USD) จะออกภายใน NEWS_IMMINENT_H ชม.
    #     2026-08-02: เดิมเช็คข่าวแค่ตอนมีไม้เปิดอยู่แล้ว (exit_monitor) ไม่มีจุดไหนกันตอน
    #     "จะเปิดไม้ใหม่" เลย — เปิดก่อนข่าวใหญ่ 5 นาทีก็เปิดได้ปกติ กว่า Exit Monitor จะเห็น
    #     ก็รอบสแกนถัดไปซึ่งอาจสายไปแล้ว (ข่าวออกไปแล้ว)
    has_news, news_detail, _ = check_upcoming_news(hours_ahead=NEWS_IMMINENT_H)
    if has_news:
        print(f"  [{symbol}] SKIP — ใกล้ข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ภายใน {NEWS_IMMINENT_H} ชม. — {news_detail}")
        return

    # 4. Regime Check → เลือกว่าจะเปิด Scoring (ตามเทรนด์) หรือ Reversal (สวนเทรนด์) หรือพักก่อน
    #    Mutual Exclusivity: เปิดได้แค่ scorecard เดียวต่อรอบสแกน ตาม regime ที่เจอ
    try:
        regime_info = get_regime(symbol)
        regime      = regime_info["regime"]
        print(f"  [{symbol}] Regime={regime}  ({regime_info['action']})")

        if regime in REGIME_NO_TRADE:
            print(f"  [{symbol}] SKIP — regime ยังไม่พร้อมเปิด scorecard ใดๆ")
            return

        # 4b. ช่องของกลยุทธ์ที่ regime รอบนี้จะเปิด ว่างไหม — regime เป็นตัวเลือกกลยุทธ์ตัวเดียว
        #     (Mutual Exclusivity ตามข้อ 4) จึงรู้ได้ตั้งแต่ตรงนี้โดยไม่ต้องคำนวณสกอร์การ์ดก่อน
        #     occupied อ่านไว้ตั้งแต่ข้อ 1 ก่อนรัน Exit Monitor — ตรงลำดับกับ backtest_replay
        #     (ถ้า SLOT_PER_STRATEGY=False ข้อ 1 return ไปตั้งแต่มีไม้ใดๆ แล้ว มาไม่ถึงตรงนี้)
        want = "Scoring" if regime in REGIME_TREND else "Reversal"
        if want in occupied:
            print(f"  [{symbol}] SKIP — ช่อง {want} มีไม้เปิดอยู่แล้ว")
            return

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"  [{symbol}] ERROR — ดึงราคาไม่ได้")
            return
        entry = tick.bid

        if regime in REGIME_TREND:
            # ── เปิด Scoring (trend-following) ── ใช้ get_trend_bias ตัวเดียวกับที่
            # compute_score เรียกภายใน (trend_flip เท่านั้น ไม่มี EMA fallback) กัน bias
            # สองจุดขัดกันเอง (เดิม scheduler ใช้ EMA แยกจาก compute_score ที่ใช้ trend_flip)
            df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
            df_1d = merge_real_volume(df_1d, symbol, "1D")
            direction, bias_source = get_trend_bias(symbol, df_1d)
            if direction is None:
                print(f"  [{symbol}] SKIP — หา Bias ไม่ได้ ({bias_source})")
                return
            # ทิศจาก trend_flip (1D) ต้องตรงกับโครงสร้าง 4H — ดู config ที่ค่านั้น
            # structure["trend"] = "Long (HH/HL)" / "Short (LL/LH)" ตอน regime = TREND เสมอ
            _struct = regime_info["structure"]["trend"]
            if SCORING_NEEDS_STRUCTURE_MATCH and not _struct.startswith(direction):
                print(f"  [{symbol}] SKIP — Bias={direction} สวนโครงสร้าง 4H ({_struct})")
                return
            print(f"  [{symbol}] เปิด Scoring — Bias={direction} ({bias_source})  Entry={entry:.5f}")
            # ส่ง df_1d ที่ดึงไปแล้วข้างบน (สำหรับ get_trend_bias) ให้ compute_score ใช้ซ้ำ —
            # กันดึง+merge_real_volume 1D ซ้ำสองรอบข้อมูลชุดเดียวกันเป๊ะ (2026-08-11)
            score, criteria, passed, sl_info = compute_score(symbol, direction, entry, df_1d=df_1d)
            sl, tp = sl_info["sl"], sl_info["tp"]
            rr = calc_rr(entry, sl, tp, direction)
            score_total = TOTAL_WEIGHT
            strategy = "Scoring"

        else:  # REGIME_REVERSAL — "REVERSAL-READY"
            # ── เปิด Reversal (ทิศตามขั้ว divergence ที่ทำให้ REVERSAL-READY ยิง ไม่ใช่แค่กลับ bias) ──
            div_polarity = regime_info["divergence"]["divergence"]
            direction = "Long" if div_polarity == "bullish" else "Short"
            # ── ด่านฝั่ง Short: ต้องมีเทรนด์ 1D หนุนด้วย (ดูที่มา/ตัวเลขที่ config.py) ──
            # ใช้ get_trend_bias ตัวเดียวกับที่ฝั่ง Scoring ใช้ ไม่เพิ่มนิยามเทรนด์ตัวที่สอง
            # เข้าระบบ และดึง 1D แบบเดียวกันเป๊ะ (bars=800 + merge_real_volume)
            if direction == "Short" and REVERSAL_SHORT_NEEDS_1D_TREND:
                df_1d_rev = merge_real_volume(
                    get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800), symbol, "1D")
                bias_1d, bias_src = get_trend_bias(symbol, df_1d_rev)
                if bias_1d != "Short":
                    print(f"  [{symbol}] SKIP — Reversal Short แต่เทรนด์ 1D = {bias_1d} "
                          f"({bias_src}) ต้องเป็น Short ถึงจะเข้าได้")
                    return
                print(f"  [{symbol}] Reversal Short — เทรนด์ 1D = Short ({bias_src}) ผ่านด่าน")
            # CHoCH — โครงสร้างเดิม (ฝั่งตรงข้ามกับที่จะเข้า) ต้องพังแล้ว ดู config ที่ค่านั้น
            if REVERSAL_NEEDS_CHOCH:
                _opp = "Short" if direction == "Long" else "Long"
                if not check_structure_break(symbol, _opp):
                    print(f"  [{symbol}] SKIP — ยังไม่เห็น CHoCH (โครงสร้าง {_opp} ยังไม่พัง) "
                          f"ยังไม่เข้า Reversal {direction}")
                    return
            print(f"  [{symbol}] เปิด Reversal — Divergence={div_polarity} -> เข้าเป็น {direction}  Entry={entry:.5f}")
            # 2026-09-05: รับเข้า `sl_info` ตัวเดียวกับทาง Scoring — เดิมรับเป็น `info` แล้วโค้ด
            # ด้านล่าง (exec_sl / pinned_swing) อ่านจาก `sl_info` แบบไม่แยก branch ทำให้ไม้
            # Reversal โยน NameError: name 'sl_info' is not defined ทุกครั้งแล้วโดน except
            # ด้านล่างกลืนไปเป็น "ERROR — ..." = **ระบบจริงเปิดไม้ Reversal ไม่ได้เลยตั้งแต่
            # 2026-08-31** (รอบที่ย้าย exec_sl เข้า compute_score แล้วไม่ได้แก้ทาง Reversal ตาม)
            # backtest ไม่เจอเพราะ backtest_replay.py มีโค้ดคำนวณ exec_sl ของตัวเองแยกต่างหาก
            score, criteria, passed, sl_info = reversal.compute_reversal_score(
                symbol, direction, entry, key_level=regime_info["key_level"],
                df_4h=regime_info["df_4h"])
            sl, tp = sl_info["sl"], sl_info["tp"]
            rr = sl_info["rr"]
            score_total = reversal.TOTAL_WEIGHT
            strategy = "Reversal"

        # แสดงผลสรุป
        failed = [name for name, p, _ in criteria if not p]
        print(f"  [{symbol}] Score={score:.1f}/{score_total:.0f}  R:R={rr:.2f}  SL={sl:.2f}  TP={tp:.2f}")
        for name, p, weight in criteria:
            status = "✅" if p else "❌"
            print(f"    {status} {name:<15} {weight:.0f}pt")

        if not passed:
            print(f"  [{symbol}] NO ENTRY — ไม่ผ่าน: {', '.join(failed)}")
            return

        # 4b-2. เพดานระยะ TP เป็นเท่าของ ATR ตอนเข้าไม้ (ดูที่มา/ตัวเลข/คำเตือนที่ config.TP_MAX_ATR)
        #     วางไว้ **หลังด่านสกอร์การ์ดผ่านแล้ว** โดยตั้งใจ ตรงกับลำดับใน backtest_replay.py:
        #     ไม้ต้องผ่าน MIN_RR_HARD_BLOCK ด้วย TP โครงสร้างจริงก่อน แล้วค่อยดึงเข้า — ถ้าดึงก่อน
        #     จะกลายเป็นการปล่อยไม้ที่โครงสร้างไม่มีที่ไปให้ผ่านด่านเพราะเป้ามันใกล้ (คนละเรื่องกัน)
        #     ใช้ atr_entry จาก sl_info = ตัวเดียวกับที่ compute_score ใช้คิด exec_sl (ทั้งทาง
        #     Scoring และ Reversal คืนคีย์นี้) ไม่คำนวณ ATR ใหม่ กันสองที่ได้คนละค่าแบบที่เคยเจอ
        _atr_entry = sl_info.get("atr_entry")
        if TP_MAX_ATR and _atr_entry:
            _cap = _atr_entry * TP_MAX_ATR
            _tp_capped = (min(tp, entry + _cap) if direction == "Long" else max(tp, entry - _cap))
            if _tp_capped != tp:
                print(f"  [{symbol}] ดึง TP เข้า — เป้าเดิม {tp:.5f} ห่าง "
                      f"{abs(tp - entry) / _atr_entry:.1f} ATR เกินเพดาน {TP_MAX_ATR:g} "
                      f"-> TP = {_tp_capped:.5f}  R:R จริง = "
                      f"{calc_rr(entry, sl_info.get('exec_sl') or sl, _tp_capped, direction):.2f}")
                tp = _tp_capped

        # 4c. ด่านกันเข้า "ตอนปลายทาง" — ราคาวิ่งไปทางที่จะเข้ามาแล้วเกิน MAX_RUNUP_24H_R เท่าของ
        #     ระยะเสี่ยง ภายใน 24 แท่ง 1H ที่ผ่านมา ให้ข้ามรอบนี้ (ดูที่มา/ตัวเลขที่ config.py)
        #     ใช้เฉพาะ Scoring — Reversal เข้าสวนเทรนด์โดยดีไซน์ ตัวเลขนี้ตีความคนละแบบ
        #     วัดด้วย "ระยะเสี่ยงจริงของไม้นี้" (entry -> SL ที่ส่ง broker) ให้เทียบข้าม symbol ได้
        if MAX_RUNUP_24H_R is not None and strategy == "Scoring":
            _exec_sl_for_runup = sl_info.get("exec_sl") or sl
            _risk = abs(entry - _exec_sl_for_runup)
            _h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=27)
            if _risk and len(_h1) >= 26:
                # iloc[-1] = แท่งที่ยังไม่ปิด, iloc[-2] = แท่งที่ปิดล่าสุด (ราคา ~ตอนนี้)
                # ย้อนไปอีก 24 แท่งจึงเป็น iloc[-2-24] = iloc[-26]
                # 2026-09-05: เดิมเขียน iloc[-25] = ย้อนแค่ 23 แท่ง ไม่ตรงกับ backtest_replay ที่
                # เทียบ clock["close"].iloc[n-24] กับราคาเข้าที่ index n (24 แท่งพอดี) — ผลต่างเล็ก
                # แต่เป็นความไม่ตรงกันระหว่างเครื่องมือวัดกับระบบจริง ซึ่งเป็นบั๊กแบบเดียวกับ
                # lookahead ที่เพิ่งแก้ไปวันนี้
                _past = float(_h1["close"].iloc[-26])
                _runup = ((entry - _past) if direction == "Long" else (_past - entry)) / _risk
                if _runup > MAX_RUNUP_24H_R:
                    print(f"  [{symbol}] NO ENTRY — ราคาวิ่งไปทาง {direction} มาแล้ว {_runup:.2f}R "
                          f"ใน 24 ชม. (เกิน {MAX_RUNUP_24H_R:g}R) — เข้าตอนปลายทาง")
                    return

        # 5. Execute
        # ── ฐานตรึงของ ATR Trailing SL — คำนวณก่อนส่ง order เพื่อส่งเข้า place_order() รวดเดียว
        # (2026-08-09: ย้าย journal logging เข้าไปอยู่ใน place_order() เอง ไม่แยกเรียกทีหลังอีก —
        # กันเคสไม้จริงหลุดไม่ถูกบันทึก ดู comment เต็มที่ order.place_order()) exit_monitor.py จะ
        # อ่านค่านี้กลับมาใช้ตลอดการถือ แทนคำนวณ swing/atr_entry ใหม่ทุกชั่วโมง (กัน anchor สลับถ้า
        # ถือยาวจน swing เดิมหลุดขอบ rolling window — ดู known bug ATR trailing anchor)
        #
        # 2026-08-07: ฐานตรึงใช้ `sl` (SL จริงที่คำนวณตอนเข้า = Swing + ATR4H×0.1 buffer, ตัวเดียว
        # กับที่เอาไปคิด R:R และส่งเป็น Broker SL) แทน trail["swing"] (Swing ดิบ ไม่มี buffer) เดิม
        # — เดิมสองระบบนี้คำนวณจุดยึดคนละจุดกันเอง ทำให้ระยะเสี่ยงจริง (trailing) ไม่ตรงกับ R:R
        # ที่ใช้กรองตอนเข้า พอ anchor เดียวกัน ทั้ง R:R ตอนเข้า และ Trailing SL ระหว่างถือ จะไปทาง
        # เดียวกันเสมอ — ยังคง roll ตาม ATR1H รายชั่วโมงเหมือนเดิมทุกอย่าง เปลี่ยนแค่จุดเริ่มต้น
        # 2026-08-31: exec_sl / atr_entry มาจาก compute_score แล้ว (sl_info) ไม่คำนวณซ้ำที่นี่ —
        # เดิมคำนวณตรงนี้ *หลัง* ด่าน R:R ผ่านไปแล้ว ทำให้ด่านตรวจคนละระยะเสี่ยงกับที่ส่งจริง
        # และถ้าคำนวณสองที่ก็มีโอกาสได้ ATR คนละค่า (คนละวินาที/คนละจำนวนแท่ง) — ดู scoring.py
        pinned_swing = pinned_atr_entry = None
        exec_sl = sl_info.get("exec_sl") or sl   # SL ที่ส่ง broker จริง
        try:
            if sl_info.get("atr_entry") is not None:
                pinned_swing, pinned_atr_entry = sl, sl_info["atr_entry"]
                # 2026-08-27: ส่ง SL แรกไปที่จุดเดียวกับที่ ATR trailing จะเลื่อนไปอยู่ดีในรอบแรก
                # (exit_monitor คำนวณ initial_sl = pinned_swing ∓ 2×ATR แล้วสั่งขยับทันทีที่รันรอบ
                # แรกภายใน 1 ชม.) เดิมส่ง `sl` แคบๆ ไปก่อนแล้วค่อยโดนขยับออก = ระบบคิด lot จาก
                # ระยะแคบแต่ไปรับความเสี่ยงจริงตามระยะกว้าง
                #
                # วัดจาก backtest_replay.py (BTC 730 วัน, exit_monitor ตัวจริง): ไม้ที่จบด้วย SL
                # 6/6 ไม้มี SL ถูกขยับออกก่อนโดน เฉลี่ยกว้างขึ้น 59% ของ 1R -> ขาดทุนจริงเฉลี่ย
                # 1.17R แย่สุด 1.59R ทั้งที่ position size คิดไว้ที่ 1R = RISK_PER_TRADE (2%)
                # = ไม้เดียวเสียได้ถึง ~3% ของพอร์ต
                #
                # ผลของการแก้: lot เล็กลงตามสัดส่วน (ระยะเสี่ยงกว้างขึ้น) ขาดทุนสูงสุดกลับมาเป็น
                # 1R จริง และกฎ exit ทุกข้อที่อิง r_multiple (ปิดครึ่งที่ 1R, breakeven, slow-trade)
                # ยิงที่ระยะเดียวกับที่คิด lot ไม่ใช่ 1.34-1.6 เท่าเหมือนเดิม
                #
                # pinned_swing ยังเป็น `sl` เท่าเดิม -> สูตร trailing ทั้งหมดไม่เปลี่ยนเลย
                # เปลี่ยนแค่ "จุดเริ่ม" ให้ตรงกับที่มันจะไปอยู่แล้ว
                #
                # 2026-08-31: ช่องว่างที่เคยเขียนเตือนไว้ตรงนี้ ("ด่านคัดเข้ายังใช้ R:R ที่สูงกว่า
                # ความเป็นจริงราว 1.3 เท่า") ปิดแล้ว — compute_score คำนวณ exec_sl เองและใช้ตรวจ
                # R:R วัดจริงบนไม้ replay 38 ไม้: ความเสี่ยงจริงกว้างกว่าที่ด่านเคยคิด 1.49 เท่า
                # และ 11/38 ไม้ (29%) เคยผ่านด่าน 1.5 มาได้ทั้งที่ R:R จริงต่ำกว่า 1.5
        except Exception as exc:
            print(f"  [{symbol}] WARNING — บันทึกฐานตรึงไม่ได้ ({exc}) — exit_monitor จะ fallback คำนวณเองภายหลัง")

        lot, _ = calculate_lot_size(symbol, entry, exec_sl, balance, RISK_PER_TRADE)
        lot    = clamp_lot(symbol, lot)
        if exec_sl != sl:
            rr_exec = calc_rr(entry, exec_sl, tp, direction)
            print(f"  [{symbol}] SL ที่ส่ง broker = {exec_sl:.2f} (SL โครงสร้าง {sl:.2f} "
                  f"ขยาย 2xATR ให้ตรงกับ ATR trailing)  R:R จริง = {rr_exec:.2f}")

        ticket = place_order(symbol, direction, entry, exec_sl, tp, lot,
                             score=score, strategy=strategy,
                             pinned_swing=pinned_swing, pinned_atr_entry=pinned_atr_entry)
        print(f"  [{symbol}] ORDER SENT ✅  Ticket=#{ticket}  Lot={lot}")

    except ValueError as exc:
        print(f"  [{symbol}] BLOCKED — {exc}")
    except Exception as exc:
        print(f"  [{symbol}] ERROR — {exc}")
        log.error(f"[{symbol}] scan_symbol ERROR", exc_info=True)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run_scheduler() -> None:
    load_dotenv()
    print("=" * 52)
    print("  AUTO TRADER SCHEDULER  (กด Ctrl+C เพื่อหยุด)")
    print(f"  Symbols  : {', '.join(SYMBOLS)}")
    print(f"  Interval : {INTERVAL_SECONDS // 60} นาที")
    print("=" * 52)

    # 2026-08-02: ส่งสรุปรายวันตอนเที่ยงคืน — loop นี้สแกนทุก INTERVAL_SECONDS (1 ชม.) นับจาก
    # เวลาที่โปรเซสเริ่ม ไม่ได้ sync กับนาฬิกาจริง แต่เพราะ 3600s x 24 = 1 วันพอดี รอบที่ตรง
    # ชั่วโมง 0 (เที่ยงคืน) จะมาแค่ 1 ครั้งต่อวันเสมอ (นาทีอาจไม่ตรง 00:00 เป๊ะ แต่ชั่วโมงตรง) —
    # เก็บวันที่ส่งล่าสุดไว้กันส่งซ้ำถ้า loop ดันมาชนชั่วโมง 0 มากกว่า 1 รอบ (เช่น restart)
    last_summary_date = None

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 52}")
        print(f"  รอบใหม่  {now}")
        print(f"{'=' * 52}")

        try:
            connect()
            # sync journal กับ MT5 ก่อนสแกน — ไม้ที่ชน SL/TP ไปเองต้องถูก mark ปิดก่อน
            # ไม่งั้น journal.check_daily_loss() ใน scan_symbol จะมองไม่เห็นการขาดทุนพวกนั้น
            try:
                journal.reconcile_closed_positions()
            except Exception as exc:
                print(f"[journal] reconcile ล้มเหลว — {exc}")
                log.error("reconcile_closed_positions ERROR", exc_info=True)

            today = datetime.now().date()
            if datetime.now().hour == 0 and last_summary_date != today:
                try:
                    stats = journal.get_daily_statistics()
                    notify.notify_daily_summary(stats)
                    print(f"[journal] ส่งสรุปรายวันแล้ว — {stats}")
                except Exception as exc:
                    print(f"[journal] ส่งสรุปรายวันล้มเหลว — {exc}")
                    log.error("notify_daily_summary ERROR", exc_info=True)
                last_summary_date = today

            for symbol in SYMBOLS:
                scan_symbol(symbol)
        except Exception as exc:
            print(f"[ERROR] MT5 connection: {exc}")
            notify.notify_error("scheduler — MT5 connection", str(exc))
            log.error("MT5 connection ERROR", exc_info=True)
        finally:
            mt5.shutdown()

        next_run = datetime.fromtimestamp(
            time.time() + INTERVAL_SECONDS
        ).strftime("%H:%M:%S")
        print(f"\n  รอบถัดไป : {next_run}  (อีก {INTERVAL_SECONDS // 60} นาที)")
        print("-" * 52)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\n\n  หยุด Scheduler แล้ว")
