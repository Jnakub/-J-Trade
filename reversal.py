"""
reversal.py — แผนเข้าไม้ทาง Reversal: เทรด "สวน" ทิศเทรนด์เดิม (คู่กับ scoring.py ที่เทรด "ตาม" เทรนด์)

ใช้ได้ต่อเมื่อ regime_check.py บอกว่า REVERSAL-READY เท่านั้น (ADX peak โค้งลงจริง
+ อยู่ที่ Key Level + มี Divergence) — ทิศมาจากขั้ว divergence ที่ทำให้ regime ยิง

compute_reversal_entry() หา SL (swing โครงสร้าง 4H) / TP (Fibonacci) แล้วตรวจ hard block
(ระยะ SL ขั้นต่ำ · R:R ขั้นต่ำ/สูงสุด · ระยะ TP สูงสุด) — ไม่ผ่าน = raise ValueError
2026-09-25: สกอร์การ์ด Reversal (Key Level/Divergence/RSI extreme/VSA Climax/ADX/R:R รวม 10 แต้ม)
ถูกลบทิ้งทั้งใบ — เลิกใช้กรองมาตั้งแต่ 2026-09-13 (MIN_SCORE_REVERSAL = 0) ดูเหตุผลที่ config.py

ใช้: python reversal.py <SYMBOL> <Long/Short> <entry>
     python reversal.py <SYMBOL> <Long/Short> <entry> <sl> <tp>
"""
import sys
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import (MT5_TIMEFRAMES, RISK_PER_TRADE, MAX_RR_HARD_BLOCK,
                    MIN_RR_HARD_BLOCK_REVERSAL, REVERSAL_TP_FIB_RATIO,
                    get_min_sl_distance_pct, MAX_TP_DISTANCE_PCT, TP_FIB_RATIO)
from mt5_connect import connect, get_account_balance
import scoring
from scoring import get_ohlcv_real, calc_rr
from swing import find_sl_from_structure, find_tp_from_fibonacci, swing_vol_multiplier, swing_wick_ratio_min
from exit_monitor import calc_atr_trailing_sl, BARS as TRAIL_BARS
from order import calculate_lot_size, clamp_lot

MIN_RR_REVERSAL    = 2.0   # ตัวคูณของสูตร fallback TP ตอนหา Fibonacci ไม่ได้ (TP = entry ± risk×2.0)
# R:R ขั้นต่ำที่ยอมเทรดของ Reversal คือ config.MIN_RR_HARD_BLOCK_REVERSAL (คนละตัวกับค่านี้)

# ── สวิตช์ทดลอง (2026-09-01) — ค่า default = พฤติกรรมระบบจริงเป๊ะ ห้ามแก้ค่าตรงนี้ ────────
# backtest_replay.py ตั้งค่าให้เฉพาะรอบที่รันด้วย --rev-min-sl / --rev-tp-from-entry
# เพื่อตอบว่าด่านสองตัวที่ตัดโอกาส Reversal ทิ้งมากที่สุดควรปรับไหม โดยไม่ต้องแก้ config.py แล้วเผลอมีผลกับระบบจริง
#
# MIN_SL_OVERRIDE: ทับ get_min_sl_distance_pct() เฉพาะทาง Reversal — วัดจากไม้จริง 2 ปี พบว่า
#   setup ที่ SL แคบกว่า 0.9% (BTC 23 แท่ง) คือกลุ่มที่ R:R ดีที่สุดในกองทั้งหมด (มัธยฐาน 8.8)
#   เพราะเป็น entry ที่ชิดโครงสร้างที่สุด — ซึ่งเป็นรูปแบบปกติของไม้สวน (SL ชิดเหนือยอดกลับตัว)
#   ต่างจากฝั่ง Scoring ที่ SL อยู่ที่ swing ของ pullback ซึ่งค่า 0.9% ถูก backtest มาแล้ว
# TP_FROM_ENTRY: ฉาย Fibonacci extension จาก "ราคาเข้า" แทน swing B — สูตรเดิมฉายจาก B ทำให้
#   reward หดและ risk โตพร้อมกัน 1:1 ตามระยะที่ราคาห่างจาก B มาแล้ว (TP ไปโผล่หลัง entry
#   28% ของ setup บน BTC / 22% บน XAU) ตัวนี้ทำให้ TP ไกลกว่าเดิมเสมอ = ผ่อนด่าน R:R
#   ⚠️ ไม่ใช่การแก้บั๊ก แต่เป็นการผ่อนเกณฑ์ ต้องดู WR ควบคู่กับ Total R เสมอ
#
# ผล backtest_replay 730 วัน (2026-09-01) — **ทั้งคู่แย่กว่าของเดิม ยังไม่เอาเข้าระบบจริง**:
#   ของเดิม            BTC +2.74R (26 ไม้, Reversal 6 WR 83%)   XAU -1.46R (15 ไม้)
#   --rev-min-sl=0.5   BTC +3.15R (27 ไม้, Reversal 7 WR 86%)   XAU -2.35R (16 ไม้) => รวม -0.48R
#   --rev-tp-from-entry BTC +2.59R (26 ไม้, Reversal 9 WR 67%)  XAU -1.99R (16 ไม้) => รวม -0.68R
#   ไม้ที่เพิ่มมามีแค่ 1-3 ไม้/symbol/2 ปี (ยังตัดสินทางสถิติไม่ได้) แต่ทั้งสองทางเอียงไปทางลบเหมือนกัน
#   ข้อสังเกตที่สำคัญกว่าตัวเลข: ผ่อนด่าน Reversal แล้ว "ไม้ Scoring หายไปแทน" (BTC 20 -> 17 ไม้)
#   เพราะระบบถือได้ทีละไม้ต่อ symbol — ด่านจริงที่ตัดโอกาส Reversal ทิ้งมากที่สุดคือ "ถือไม้อื่นอยู่"
#   (195-205 จาก 304 รอบ REVERSAL-READY ของ BTC) ไม่ใช่ตัวกรองเข้าไม้ การผ่อนตัวกรองจึงได้แค่
#   ย้ายไม้จากทาง Scoring มาทาง Reversal ไม่ได้เพิ่มจำนวนไม้รวม
MIN_SL_OVERRIDE    = None
TP_FROM_ENTRY      = False
_MIN_RR_OVERRIDE   = None   # backtest_replay --rev-min-rr ตั้งให้ (None = ใช้ค่าจาก config)
_TP_RATIO_OVERRIDE = None   # backtest_replay --rev-tp-ratio ตั้งให้ (None = config.REVERSAL_TP_FIB_RATIO)
# แท่ง 4H ฝั่งขวาที่ยืนยัน swing ของ SL/TP แยกตามทิศไม้ — backtest_replay --rev-swing-right[-lows] ตั้งคู่กับ regime_check.DIV_SWING_RIGHT_*
SWING_RIGHT_LONG   = 2      # 2026-10-10 คำสั่งผู้ใช้ (เดิม 4) — เหตุผล/ตัวเลขที่ regime_check.DIV_SWING_RIGHT_LOWS
SWING_RIGHT_SHORT  = 4

GREEN, YELLOW, RED, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)


# ---------------------------------------------------------------------------
# แผนเข้าไม้ Reversal (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def compute_reversal_entry(symbol: str, direction: str, entry: float,
                           sl: float = None, tp: float = None,
                           force: bool = False,
                           df_4h: pd.DataFrame = None, as_of: datetime = None) -> dict:
    """คืน info dict ของไม้ Reversal หรือ raise ValueError ถ้าติดด่าน (= ห้ามเข้าไม้นี้)
    ถ้าไม่ส่ง sl/tp จะหาจาก swing structure/Fibonacci อัตโนมัติ (เกณฑ์เดียวกับ scoring.py)
    คีย์: sl (SL โครงสร้าง = pinned_swing) · exec_sl (ส่ง broker) · atr_entry · tp · rr (จาก
    exec_sl) · sl_info · fib_info

    2026-09-25: เดิมชื่อ compute_reversal_score คืน (score, criteria, passed, info) — ลบสกอร์การ์ด
    ออกแล้ว (ดู docstring หัวไฟล์) พร้อมกับพารามิเตอร์ key_level ที่มีไว้ให้เกณฑ์ Key Level เท่านั้น

    df_4h: ส่ง get_regime()["df_4h"] มาใช้ซ้ำได้ (real volume, BARS แท่ง, ยังไม่ตัดแท่งฟอร์มมิ่ง)
    กันดึง+merge real volume จาก Bitstamp ซ้ำสองรอบต่อรอบสแกน — ถ้าไม่ส่งมาจะดึงเองเหมือนเดิม

    as_of=None (ปกติ) = เช็คสด ณ ตอนนี้ — as_of=datetime = จำลองเช็ค ณ เวลานั้นในอดีต
    (2026-08-02, ตามแบบ scoring.compute_entry) ให้ backtest เรียกตัวนี้ตรงๆ ได้ ไม่ต้อง copy
    logic มาเขียนซ้ำ — มีผลเฉพาะตอนไม่ได้ส่ง df_4h มาเอง (จะ fetch ย้อนหลังแทนสด)"""
    is_long = direction.capitalize() == "Long"
    _swing_right = SWING_RIGHT_LONG if is_long else SWING_RIGHT_SHORT

    if df_4h is None:
        df_4h = get_ohlcv_real(symbol, "4H", bars=210, as_of=as_of)
    # ตัดแท่งยังไม่ปิดทิ้ง — ทั้งสองโหมด เพราะแท่งท้ายสุดของเฟรมคือแท่งฟอร์มมิ่งเสมอ ไม่ว่า
    # as_of จะเป็นอะไร (bars.get_bars ประกอบแท่งฟอร์มมิ่งจาก TF ย่อยต่อกลับเข้าไปให้ในโหมด
    # backtest ด้วย เพื่อให้หน้าตาเฟรมเหมือนตอนรันสดเป๊ะ — ดู docstring bars.get_bars)
    #
    # 2026-09-01: เดิมบรรทัดนี้เป็น `if as_of is None:` ตามความเข้าใจของ 2026-08-09 ว่าโหมด
    # backtest get_ohlcv_real ตัดแท่งฟอร์มมิ่งให้เองแล้ว — จริงตอนนั้น แต่หมดอายุไปตั้งแต่
    # 2026-08-26 ที่ย้ายตัวดึงแท่งไป bars.get_bars() (ตัวใหม่ต่อแท่งฟอร์มมิ่งกลับเข้ามา) ผลคือ
    # โหมด backtest มองเห็นแท่งเกินมา 1 แท่งเทียบกับ get_regime() ที่ตัดแท่งท้ายทิ้งเสมอ
    # (regime_check.get_regime ส่ง df_4h.iloc[:-1] ให้ check_divergence) วัดจริงบน 2 ปี:
    # divergence ที่สกอร์การ์ดคำนวณเองไม่ตรงกับตอนที่ regime ปลดล็อก REVERSAL-READY
    # 24/205 แท่ง (BTC) และ 39/379 (XAU) = เสียแต้ม Divergence (3 จาก 10) ฟรีๆ ทั้งที่ regime
    # เพิ่งยืนยันว่ามี ทำให้ตัวเลข Reversal ใน backtest ต่ำกว่าที่ระบบจริงทำได้
    # โหมดรันสดไม่เคยโดนบั๊กนี้ (scheduler ส่ง df_4h มาพร้อม as_of=None -> ตัดอยู่แล้ว)
    # (ตอนนั้นกระทบเกณฑ์สกอร์การ์ดที่ลบไปแล้ว — ตอนนี้เหลือผลต่อ SL/TP ที่หาจาก df_4h ตัวนี้)
    df_4h = df_4h.iloc[:len(df_4h) - 1].reset_index(drop=True)
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    # หา SL อัตโนมัติจาก swing structure (4H) ถ้าไม่ได้กรอกมา — เกณฑ์เดียวกับ scoring.py
    sl_info = {}
    if sl is None:
        # ส่ง entry เป็น current_price ด้วยเหตุผลเดียวกับ scoring.py (ดู swing.py)
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=_swing_right, tolerance_atr=0.22,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min,
                                         current_price=entry)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # ── SL ที่จะส่ง broker จริง (exec_sl) ────────────────────────────────────────────────
    # 2026-09-05: ยกวิธีเดียวกับ scoring.compute_score (แก้ไปเมื่อ 2026-08-31) มาใช้กับฝั่ง
    # Reversal ที่ตอนนั้นไม่ได้แก้ตาม — scheduler/backtest ขยับ SL ออกอีก EXEC_SL_ATR_MULT×ATR
    # *หลัง* compute_reversal_score จบไปแล้ว ด่าน R:R จึงตรวจด้วยไม้บรรทัดที่แคบกว่าของจริง
    # วัดจากไม้จริง 8 symbol (123 ไม้): R:R ที่ด่านเห็นเฉลี่ย 4.67 แต่ R:R จริง 2.83 และ
    # **19 จาก 39 ไม้ Reversal (49%) ผ่านด่าน MIN_RR_HARD_BLOCK=1.5 มาได้ทั้งที่ R:R จริง < 1.5**
    # (เฉลี่ย 1.17) ฝั่ง Scoring ไม่มีเคสแบบนี้เลยเพราะแก้ไปแล้ว — ความเสียหายกระจุกที่ EURUSDm
    # (4 ไม้ -2.91R จากทั้ง symbol -3.64R) แต่รวมทุก symbol กลุ่มนี้ยังเป็นบวก (+0.70R) จึงต้อง
    # วัดผลรวมหลังแก้ ไม่ใช่ถือว่าแก้แล้วดีขึ้นแน่นอน
    # เกณฑ์ไม่เปลี่ยน เปลี่ยนแค่ไม้บรรทัดที่ใช้วัดให้ตรงกับสิ่งที่ระบบทำจริง
    atr_entry, exec_sl = None, sl
    if scoring.EXEC_SL_ATR_MULT:
        try:
            _t = as_of if as_of is not None else datetime.now()
            _tr = calc_atr_trailing_sl(get_ohlcv_real(symbol, "4H", bars=TRAIL_BARS, as_of=as_of),
                                       symbol, _t, direction.capitalize(), as_of=as_of)
            if _tr:
                atr_entry = _tr["atr_entry"]
                exec_sl = (sl - scoring.EXEC_SL_ATR_MULT * atr_entry) if is_long else \
                          (sl + scoring.EXEC_SL_ATR_MULT * atr_entry)
        except Exception:
            pass

    # หา TP อัตโนมัติจาก Fibonacci (4H) ถ้าไม่ได้กรอกมา
    fib_info = {}
    if tp is None:
        _ratio = REVERSAL_TP_FIB_RATIO if _TP_RATIO_OVERRIDE is None else _TP_RATIO_OVERRIDE
        fib_info  = find_tp_from_fibonacci(df_4h, direction, left=4, right=_swing_right, tolerance_atr=0.22,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min,
                                           ratio=_ratio)
        if fib_info.get("passed"):
            tp = fib_info["tp"]   # อัตราส่วนมาจาก config.REVERSAL_TP_FIB_RATIO (แยกจาก Scoring 2026-09-26)
            if TP_FROM_ENTRY:     # โหมดทดลอง — ระยะเท่าเดิม (move × ratio) แต่ตั้งต้นที่ราคาเข้า
                _proj = fib_info["move"] * _ratio
                tp = (entry + _proj) if is_long else (entry - _proj)
        else:
            # อิง exec_sl (ระยะเสี่ยงจริง) เหมือน scoring.py — ไม่งั้น TP ที่ตั้งจากสูตร fallback
            # จะให้ R:R จริงต่ำกว่าที่ตั้งใจไว้
            tp = (entry + abs(entry - exec_sl) * MIN_RR_REVERSAL) if is_long else \
                 (entry - abs(entry - exec_sl) * MIN_RR_REVERSAL)

    # R:R วัดจาก exec_sl = ระยะเสี่ยงจริงที่จะส่ง broker (ดู comment ที่คำนวณ exec_sl ด้านบน)
    rr = calc_rr(entry, exec_sl, tp, direction)

    info = {
        "sl": sl, "tp": tp, "rr": rr,
        # SL ที่ต้องส่ง broker จริง — ผู้เรียกใช้ตัวนี้ อย่าคำนวณเองซ้ำ ไม่งั้นสองที่จะได้ ATR
        # คนละค่าแล้ว R:R เพี้ยนอีก (บทเรียนเดียวกับ scoring.py 2026-08-31)
        "exec_sl": exec_sl, "atr_entry": atr_entry,
        "sl_info": sl_info, "fib_info": fib_info,
    }

    # Hard block: ระยะ entry->SL แคบเกิน — ดู comment ที่ config.MIN_SL_DISTANCE_PCT /
    # config.get_min_sl_distance_pct() (Forex/Index ใช้เกณฑ์ต่ำกว่า — ดู scoring.py comment)
    min_sl_pct = MIN_SL_OVERRIDE if MIN_SL_OVERRIDE is not None else get_min_sl_distance_pct(symbol)
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct < min_sl_pct - 1e-9 and not force:
        raise ValueError(f"ระยะ SL ห่างจาก entry แค่ {sl_distance_pct:.2f}% ต่ำกว่าขั้นต่ำ "
                         f"{min_sl_pct}% — ห้ามเข้า trade")

    # เกณฑ์แยกของ Reversal (ไม่ใช่ MIN_RR_HARD_BLOCK ที่ Scoring ใช้) — ดูที่มาที่ config.py
    min_rr = MIN_RR_HARD_BLOCK_REVERSAL if _MIN_RR_OVERRIDE is None else _MIN_RR_OVERRIDE
    if rr < min_rr - 1e-9 and not force:
        raise ValueError(f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {min_rr} — ห้ามเข้า trade")

    # Hard block: R:R สูงผิดปกติ — ดู comment ที่ config.MAX_RR_HARD_BLOCK
    if rr > MAX_RR_HARD_BLOCK + 1e-9 and not force:
        raise ValueError(f"R:R = {rr:.2f} สูงเกินขั้นสูงสุด {MAX_RR_HARD_BLOCK} — ห้ามเข้า trade")

    # Hard block: TP ไกลเกินจนราคาไปไม่ถึง — ดู comment ที่ config.MAX_TP_DISTANCE_PCT
    tp_distance_pct = abs(tp - entry) / entry * 100
    if tp_distance_pct > MAX_TP_DISTANCE_PCT + 1e-9 and not force:
        raise ValueError(f"TP ห่างจาก entry {tp_distance_pct:.1f}% เกินขั้นสูงสุด "
                         f"{MAX_TP_DISTANCE_PCT}% — ห้ามเข้า trade")

    return info


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_report(symbol: str, direction: str, entry: float, info: dict, balance: float) -> None:
    sl, tp, rr = info["sl"], info["tp"], info["rr"]
    risk_pct   = abs(entry - sl) / entry * 100
    reward_pct = abs(tp - entry) / entry * 100
    lot, decimals = calculate_lot_size(symbol, entry, sl, balance, RISK_PER_TRADE)

    print()
    print("=" * 66)
    print(f"{BOLD}  REVERSAL — {symbol}  ({direction}){RESET}")
    print("=" * 66)
    print(f"  Entry Price : {entry:,.3f}")
    print(f"  Stop Loss   : {sl:,.3f}   (ส่ง broker {info['exec_sl']:,.3f})")
    print(f"  Take Profit : {tp:,.3f}")
    print(f"  Risk %      : {risk_pct:.2f}%")
    print(f"  Reward %    : {reward_pct:.2f}%")
    print(f"  R:R Ratio   : {rr:.2f}x  (จาก SL ส่ง broker)")
    print(f"  Position Size (Lot): {lot:.{decimals}f}")
    print("-" * 66)
    print(f"  {GREEN}ผ่านทุกด่าน — เปิดไม้ได้{RESET}")
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
        balance = get_account_balance()
        info = compute_reversal_entry(symbol, direction, entry, sl, tp)
        print_report(symbol, direction, entry, info, balance)
    except ValueError as exc:
        print(f"{RED}[ไม่ผ่าน]{RESET} {exc}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
