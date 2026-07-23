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
    SYMBOLS, ACCOUNT_BALANCE, RISK_PER_TRADE,
    MAX_DAILY_LOSS, MIN_SCORE, TOTAL_WEIGHT, MT5_TIMEFRAMES,
)
from mt5_connect import connect
from scoring import compute_score, calc_rr, get_ohlcv, get_ohlcv_real, ema
from order import calculate_lot_size, clamp_lot, place_order
from binance import merge_real_volume
from exit_monitor import (
    analyze_position, print_report, execute_decision,
    calc_atr_trailing_sl, BARS as TRAIL_STRUCTURE_BARS,
)
from regime_check import get_regime
import reversal

# Regime ที่ "ไม่เปิด" scorecard ใดๆ — รอความชัดเจนก่อน (ตาม Mutual Exclusivity ที่ตั้งไว้)
REGIME_NO_TRADE = ("CHOPPY", "เขตเทา", "REVERSAL-WATCH")
REGIME_TREND    = ("TREND", "TREND แรงจัด")
REGIME_REVERSAL = ("REVERSAL-READY",)

INTERVAL_SECONDS = 3600   # เช็คทุก 1 ชั่วโมง


# ---------------------------------------------------------------------------
# Auto-detect direction จาก Bias
# ---------------------------------------------------------------------------

def get_bias(symbol: str) -> str:
    df = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
    df = merge_real_volume(df, symbol, "1D")
    ema50  = ema(df["close"], 50).iloc[-1]
    ema200 = ema(df["close"], 200).iloc[-1]
    return "Long" if ema50 > ema200 else "Short"


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
        return

    # 2. Daily loss guard (เช็คเฉพาะตอนจะหา entry ใหม่)
    if not journal.check_daily_loss(ACCOUNT_BALANCE, MAX_DAILY_LOSS):
        print(f"  [{symbol}] SKIP — daily loss limit reached")
        return

    # 3. Balance พอ
    account = mt5.account_info()
    if account and account.balance < ACCOUNT_BALANCE * RISK_PER_TRADE:
        print(f"  [{symbol}] SKIP — balance ไม่พอ")
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
            # ── เปิด Scoring (trend-following) ──
            direction = get_bias(symbol)
            print(f"  [{symbol}] เปิด Scoring — Bias={direction}  Entry={entry:.5f}")
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
        lot, _ = calculate_lot_size(symbol, entry, sl, ACCOUNT_BALANCE, RISK_PER_TRADE)
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
            for symbol in SYMBOLS:
                scan_symbol(symbol)
        except Exception as exc:
            print(f"[ERROR] MT5 connection: {exc}")
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
