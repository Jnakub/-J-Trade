"""
reversal.py — Reversal Scoring: scorecard สำหรับเทรด "สวน" ทิศเทรนด์เดิม (คู่กับ scoring.py ที่เทรด "ตาม" เทรนด์)

ใช้ได้ต่อเมื่อ regime_check.py บอกว่า REVERSAL-READY เท่านั้น (ADX peak โค้งลงจริง
+ อยู่ที่ Key Level + มี Divergence) — ห้ามเปิดพร้อมกับ Scoring กับ setup เดียวกัน
(ห้าม scorecard shopping)

Reversal Scoring Checklist (weight รวม = 10, ผ่านเกณฑ์ที่ >= 7):
  1. Price อยู่ที่ Key Level สำคัญ?        weight 2  (ใช้ check_key_level เดียวกับ regime_check.py)
  2. มี Divergence ใน RSI?                 weight 3  (ใช้ check_divergence เดียวกับ regime_check.py —
                                                       backtest แม่นสุดในบรรดาเงื่อนไขทั้งหมด 82-93%
                                                       เลยเพิ่มน้ำหนักจาก 2 เป็น 3)
  3. RSI extreme (>70 Short, <30 Long)?    weight 1
  4. มี VSA Climax pattern?                weight 2  (Buying Climax=Short, Selling Climax=Long)
  5. [ตัดออก] Reversal Candlestick ที่ Key Level — backtest ทุกแบบ (เปล่า/+volume/+key level)
     ไม่พบว่าแม่นกว่าเดา (45-62%, บาง combo sample เหลือ 1-10 ครั้งแกว่ง 0-100%) จึงตัดทิ้ง
     น้ำหนักที่หายไป (1) ย้ายไปเพิ่มให้ข้อ 2 (Divergence) แทน ตามผลทดสอบจริง
  6. ADX peak แล้วเริ่มลงจริง?             weight 1  (relative peak & decline เหมือน regime_check.py
                                                       ไม่ใช้ threshold ตายตัว 40 — สอดคล้องกันทั้งระบบ)
  7. R:R >= 2.0?                           weight 1  (Reversal เสี่ยงกว่า ต้อง R:R สูงกว่า trend-follow)

ใช้: python reversal.py <SYMBOL> <Long/Short> <entry>
     python reversal.py <SYMBOL> <Long/Short> <entry> <sl> <tp>
"""
import sys

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import MT5_TIMEFRAMES, ACCOUNT_BALANCE, RISK_PER_TRADE
from mt5_connect import connect
from scoring import get_ohlcv, get_ohlcv_real, calc_rr
from swing import find_sl_from_structure, find_tp_from_fibonacci, swing_vol_multiplier, swing_wick_ratio_min
from regime_check import (
    calc_adx, adx_peak_info, check_key_level, check_divergence, calc_rsi,
    ADX_PERIOD, REGIME_TIMEFRAME,
)
from exit_monitor import is_climax_bar
from order import calculate_lot_size, clamp_lot

WEIGHT_KEY_LEVEL   = 2
WEIGHT_DIVERGENCE  = 3
WEIGHT_RSI_EXTREME = 1
WEIGHT_VSA_CLIMAX  = 2
WEIGHT_ADX_DECLINE = 1
WEIGHT_RR          = 1
TOTAL_WEIGHT       = (WEIGHT_KEY_LEVEL + WEIGHT_DIVERGENCE + WEIGHT_RSI_EXTREME +
                      WEIGHT_VSA_CLIMAX + WEIGHT_ADX_DECLINE + WEIGHT_RR)   # = 10

MIN_SCORE_REVERSAL = 7
MIN_RR_REVERSAL    = 2.0
RSI_OVERBOUGHT     = 70
RSI_OVERSOLD       = 30

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)


# ---------------------------------------------------------------------------
# Reusable scoring function (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def compute_reversal_score(symbol: str, direction: str, entry: float,
                           sl: float = None, tp: float = None,
                           force: bool = False, key_level: dict = None) -> tuple[float, list, bool, dict]:
    """Return (total_score, criteria_list, passed, info).
    ถ้าไม่ส่ง sl/tp จะหาจาก swing structure/Fibonacci อัตโนมัติ (เกณฑ์เดียวกับ scoring.py)
    key_level: ส่งผลลัพธ์ check_key_level ที่ regime_check.get_regime() คำนวณไว้แล้วมาใช้ซ้ำได้
    (ราคาปิดแท่ง 4H เดียวกับที่ปลดล็อก REVERSAL-READY) — ถ้าไม่ส่งมา จะคำนวณใหม่ด้วย `entry` เอง"""
    is_long = direction.capitalize() == "Long"

    df_4h = get_ohlcv_real(symbol, "4H", bars=210)
    df_4h = df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True)   # ตัดแท่งยังไม่ปิด
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    # หา SL อัตโนมัติจาก swing structure (4H) ถ้าไม่ได้กรอกมา — เกณฑ์เดียวกับ scoring.py
    sl_info = {}
    if sl is None:
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.05,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # หา TP อัตโนมัติจาก Fibonacci (4H) ถ้าไม่ได้กรอกมา
    fib_info = {}
    if tp is None:
        swing_idx = sl_info.get("swing_idx")
        fib_info  = find_tp_from_fibonacci(df_4h, direction, swing_idx, left=4, right=4, tolerance_atr=0.05,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if fib_info.get("passed"):
            tp = fib_info["levels"]["0.786"]
        else:
            tp = (entry + abs(entry - sl) * MIN_RR_REVERSAL) if is_long else (entry - abs(entry - sl) * MIN_RR_REVERSAL)

    rr = calc_rr(entry, sl, tp, direction)

    # ── ข้อ 1: Key Level ──
    if key_level is None:
        key_level = check_key_level(symbol, entry)
    at_key = bool(key_level.get("at_key_level"))

    # ── ข้อ 2: Divergence RSI ──
    divergence = check_divergence(df_4h)
    div_dir    = divergence.get("divergence")
    div_ok     = (div_dir == "bullish") if is_long else (div_dir == "bearish")

    # ── ข้อ 3: RSI extreme ──
    rsi_now  = calc_rsi(df_4h["close"]).iloc[-1]
    rsi_ok   = (rsi_now <= RSI_OVERSOLD) if is_long else (rsi_now >= RSI_OVERBOUGHT)

    # ── ข้อ 4: VSA Climax (Selling Climax=Long, Buying Climax=Short) ──
    climax, climax_pattern = is_climax_bar(df_4h, len(df_4h) - 1)
    climax_ok = climax and (
        (is_long and climax_pattern == "Selling Climax") or
        (not is_long and climax_pattern == "Buying Climax")
    )

    # ── ข้อ 6: ADX peak แล้วเริ่มลงจริง (relative — ไม่ใช้ threshold 40 ตายตัว) ──
    df_adx     = get_ohlcv(symbol, REGIME_TIMEFRAME, bars=210)
    adx_series = calc_adx(df_adx, ADX_PERIOD)
    peak       = adx_peak_info(adx_series, len(df_adx) - 2)
    adx_ok     = peak["declining"]

    criteria = [
        ("Key Level",   at_key,    WEIGHT_KEY_LEVEL),
        ("Divergence",  div_ok,    WEIGHT_DIVERGENCE),
        ("RSI extreme", rsi_ok,    WEIGHT_RSI_EXTREME),
        ("VSA Climax",  climax_ok, WEIGHT_VSA_CLIMAX),
        ("ADX peak+decline", adx_ok, WEIGHT_ADX_DECLINE),
        ("R:R",         rr >= MIN_RR_REVERSAL - 1e-9, WEIGHT_RR),
    ]

    info = {
        "sl": sl, "tp": tp, "rr": rr,
        "sl_info": sl_info, "fib_info": fib_info,
        "key_level": key_level, "divergence": divergence,
        "rsi_now": rsi_now, "climax": climax, "climax_pattern": climax_pattern,
        "adx_peak": peak,
    }

    # Hard block: R:R ต้องผ่านก่อนเสมอ — ไม่สนคะแนนรวม (Reversal เสี่ยงกว่า ไม่ยอมผ่อน)
    if rr < MIN_RR_REVERSAL - 1e-9 and not force:
        raise ValueError(f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {MIN_RR_REVERSAL} — ห้ามเข้า trade")

    total  = sum(w for _, passed, w in criteria if passed)
    passed = total >= MIN_SCORE_REVERSAL
    return total, criteria, passed, info


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_report(symbol: str, direction: str, entry: float, score: float,
                 criteria: list, passed: bool, info: dict) -> None:
    sl, tp, rr = info["sl"], info["tp"], info["rr"]
    risk_pct   = abs(entry - sl) / entry * 100
    reward_pct = abs(tp - entry) / entry * 100
    lot, decimals = calculate_lot_size(symbol, entry, sl, ACCOUNT_BALANCE, RISK_PER_TRADE)

    print()
    print("=" * 66)
    print(f"{BOLD}  REVERSAL SCORING — {symbol}  ({direction}){RESET}")
    print(f"  {YELLOW}⚠ Reversal เสี่ยงกว่า Trend Following — Win Rate ต่ำกว่า ต้อง R:R สูงกว่า{RESET}")
    print("=" * 66)
    print(f"  Entry Price : {entry:,.3f}")
    print(f"  Stop Loss   : {sl:,.3f}")
    print(f"  Take Profit : {tp:,.3f}")
    print(f"  Risk %      : {risk_pct:.2f}%")
    print(f"  Reward %    : {reward_pct:.2f}%")
    print(f"  R:R Ratio   : {rr:.2f}x")
    print(f"  Position Size (Lot): {lot:.{decimals}f}")
    print("-" * 66)
    print(f"{BOLD}  Reversal Scoring Checklist  (เกณฑ์ผ่าน: {MIN_SCORE_REVERSAL}/{TOTAL_WEIGHT}){RESET}")
    print("-" * 66)
    for name, ok, weight in criteria:
        ans_str = f"{GREEN}YES{RESET}" if ok else f"{RED}No {RESET}"
        print(f"  [{ans_str}]  {name:<16} weight={weight}  score={weight if ok else 0}")
    print("-" * 66)
    score_color = GREEN if passed else RED
    print(f"  {BOLD}TOTAL SCORE{RESET} : {score_color}{score:.0f} / {TOTAL_WEIGHT}{RESET}")
    print(f"  {BOLD}ผลลัพธ์{RESET}     : {score_color}{'ผ่าน — เปิดไม้ได้' if passed else 'ไม่ผ่าน — ห้ามเข้า'}{RESET}")
    print("=" * 66)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) not in (4, 6):
        print("Usage : python reversal.py <SYMBOL> <Long/Short> <entry>")
        print("        python reversal.py <SYMBOL> <Long/Short> <entry> <sl> <tp>")
        print("Example (auto SL/TP) : python reversal.py XAUUSDm Long 4060")
        sys.exit(1)

    symbol    = sys.argv[1]
    direction = sys.argv[2].capitalize()
    entry     = float(sys.argv[3])
    sl        = float(sys.argv[4]) if len(sys.argv) == 6 else None
    tp        = float(sys.argv[5]) if len(sys.argv) == 6 else None

    load_dotenv()

    try:
        connect()
        score, criteria, passed, info = compute_reversal_score(symbol, direction, entry, sl, tp)
        print_report(symbol, direction, entry, score, criteria, passed, info)
    except ValueError as exc:
        print(f"{RED}[ไม่ผ่าน]{RESET} {exc}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
