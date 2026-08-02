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
)
from mt5_connect import connect, get_account_balance
from scoring import compute_score, calc_rr, get_ohlcv, get_ohlcv_real, get_trend_bias
from order import calculate_lot_size, clamp_lot, place_order
from binance import merge_real_volume
from exit_monitor import (
    analyze_position, print_report, execute_decision,
    calc_atr_trailing_sl, BARS as TRAIL_STRUCTURE_BARS,
    check_upcoming_news, NEWS_IMMINENT_H, NEWS_IMPACT, NEWS_CURRENCY,
)
from regime_check import get_regime
import reversal
from logger_setup import get_logger, tee_print

log = get_logger("scheduler")
print = tee_print(log)   # เขียนทุกอย่างที่ print ลง logs/scheduler.log ด้วย (ดู logger_setup.py)

# Regime ที่ "ไม่เปิด" scorecard ใดๆ — รอความชัดเจนก่อน (ตาม Mutual Exclusivity ที่ตั้งไว้)
REGIME_NO_TRADE = ("CHOPPY", "เขตเทา", "REVERSAL-WATCH")
REGIME_TREND    = ("TREND", "TREND แรงจัด")
REGIME_REVERSAL = ("REVERSAL-READY",)

INTERVAL_SECONDS = 3600   # เช็คทุก 1 ชั่วโมง


# ---------------------------------------------------------------------------
# สแกนและส่ง order ถ้าผ่าน
# ---------------------------------------------------------------------------

def scan_symbol(symbol: str) -> None:
    print(f"\n  [{symbol}] กำลังวิเคราะห์...")

    # 1. มี position เปิดอยู่แล้ว? -> ไม่หา entry ใหม่ รัน Exit Monitor ดูแลไม้เดิมแทน
    positions = mt5.positions_get(symbol=symbol)
    if positions:
        print(f"  [{symbol}] มี position เปิดอยู่ {len(positions)} ไม้ -> รัน Exit Monitor")
        for pos in positions:
            try:
                m = analyze_position(pos)
                print_report(m)
                execute_decision(m)
            except Exception as exc:
                print(f"  [{symbol}] Exit Monitor ERROR — {exc}")
                log.error(f"[{symbol}] Exit Monitor ERROR", exc_info=True)
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

    # 4. Balance ต้องมากกว่า 0 (risk amount = balance * RISK_PER_TRADE เป็นสัดส่วนของ balance เอง
    # เสมออยู่แล้ว จุดที่พังจริงคือ balance <= 0 ไม่ใช่สัดส่วน)
    if balance <= 0:
        print(f"  [{symbol}] SKIP — balance ไม่พอ ({balance:.2f})")
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
            print(f"  [{symbol}] เปิด Scoring — Bias={direction} ({bias_source})  Entry={entry:.5f}")
            score, criteria, passed, sl_info = compute_score(symbol, direction, entry)
            sl, tp = sl_info["sl"], sl_info["tp"]
            rr = calc_rr(entry, sl, tp, direction)
            score_total = TOTAL_WEIGHT

        else:  # REGIME_REVERSAL — "REVERSAL-READY"
            # ── เปิด Reversal (ทิศตามขั้ว divergence ที่ทำให้ REVERSAL-READY ยิง ไม่ใช่แค่กลับ bias) ──
            div_polarity = regime_info["divergence"]["divergence"]
            direction = "Long" if div_polarity == "bullish" else "Short"
            print(f"  [{symbol}] เปิด Reversal — Divergence={div_polarity} -> เข้าเป็น {direction}  Entry={entry:.5f}")
            score, criteria, passed, info = reversal.compute_reversal_score(
                symbol, direction, entry, key_level=regime_info["key_level"],
                df_4h=regime_info["df_4h"])
            sl, tp = info["sl"], info["tp"]
            rr = info["rr"]
            score_total = reversal.TOTAL_WEIGHT

        # แสดงผลสรุป
        failed = [name for name, p, _ in criteria if not p]
        print(f"  [{symbol}] Score={score:.1f}/{score_total:.0f}  R:R={rr:.2f}  SL={sl:.2f}  TP={tp:.2f}")
        for name, p, weight in criteria:
            status = "✅" if p else "❌"
            print(f"    {status} {name:<15} {weight:.0f}pt")

        if not passed:
            print(f"  [{symbol}] NO ENTRY — ไม่ผ่าน: {', '.join(failed)}")
            return

        # 5. Execute
        lot, _ = calculate_lot_size(symbol, entry, sl, balance, RISK_PER_TRADE)
        lot    = clamp_lot(symbol, lot)

        ticket = place_order(symbol, direction, entry, sl, tp, lot)

        # ── บันทึกฐานตรึงของ ATR Trailing SL ณ ตอนนี้ (entry_time = now) ──
        # exit_monitor.py จะอ่านค่านี้กลับมาใช้ตลอดการถือ แทนคำนวณ swing/atr_entry ใหม่ทุกชั่วโมง
        # (กัน anchor สลับถ้าถือยาวจน swing เดิมหลุดขอบ rolling window — ดู known bug ATR trailing anchor)
        pinned_swing = pinned_atr_entry = None
        try:
            df_4h_trail = get_ohlcv_real(symbol, "4H", bars=TRAIL_STRUCTURE_BARS)
            trail = calc_atr_trailing_sl(df_4h_trail, symbol, datetime.now(), direction)
            if trail:
                pinned_swing, pinned_atr_entry = trail["swing"], trail["atr_entry"]
        except Exception as exc:
            print(f"  [{symbol}] WARNING — บันทึกฐานตรึงไม่ได้ ({exc}) — exit_monitor จะ fallback คำนวณเองภายหลัง")

        journal.log_trade_open(symbol, direction, entry, sl, tp, lot, score, ticket,
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

            for symbol in SYMBOLS:
                scan_symbol(symbol)
        except Exception as exc:
            print(f"[ERROR] MT5 connection: {exc}")
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
