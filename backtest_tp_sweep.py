"""
backtest_tp_sweep.py — หา TP_FIB_RATIO ที่เหมาะกับแต่ละ symbol แยกกัน (เหมือนที่
backtest_trend_flip_ksweep.py ทำกับ TREND_FLIP_K) แทนการเดาค่าเดียวใช้ทุก symbol

เหตุผลที่ต้องมี: config.TP_FIB_RATIO=1.618 ใช้ค่าเดียวทั้งระบบ แต่ comment ที่ config.py
เขียนไว้เองว่าตอนเปลี่ยนจาก 0.786 -> 1.618 (backtest 180 วัน) BTC ดีขึ้นชัดเจนแต่ XAU แย่ลง
เพราะ TP ยืดไกลเกินจริง — ยังไม่เคยมีเครื่องมือ sweep ค่านี้แยก symbol เหมือน TREND_FLIP_K

วิธีทำงาน — เรียก scoring.compute_score(as_of=...) ตรงๆ ไม่ copy logic มาเขียนซ้ำ (ตามหลัก
เดียวกับ backtest_score.py/backtest_exit_compare.py) แล้ว "monkey-patch" swing.TP_FIB_RATIO
ก่อนเรียกแต่ละ ratio — เพราะ find_tp_from_fibonacci() อ่านชื่อ TP_FIB_RATIO จาก global
namespace ของ swing.py เอง (import ตอนโหลดโมดูล) การตั้งค่า swing.TP_FIB_RATIO = x จึงมีผล
ทันทีโดยไม่ต้องแตะ config.py จริง และทำให้ hard block ทั้งหมด (MIN/MAX_RR_HARD_BLOCK,
MAX_TP_DISTANCE_PCT, WEIGHT_RR ในสกอร์การ์ด) ถูกประเมินใหม่ตาม ratio นั้นๆ เหมือนระบบจริง
ทุกประการ ไม่ใช่แค่เปลี่ยนตัวเลข TP เฉยๆ

One-position-at-a-time แยกอิสระต่อ ratio (แต่ละ ratio จำลองเป็นระบบของตัวเอง มี cooldown/
"ช่องเดียว" ของตัวเอง — ไม้ที่ ratio หนึ่งเปิดได้ อีก ratio อาจถูกบล็อกไปเลยเพราะ R:R เปลี่ยน
ก็เป็นธรรมชาติของการเปลี่ยน parameter ตัวนี้จริง ไม่ใช่ bug)

แบ่ง 2 หน้าต่างเวลาไม่ทับกัน (IN-sample = ช่วงหลังสุด, OOS = ช่วงก่อนหน้า) กัน overfit
เหมือนที่ใช้ตอน sweep MIN_SL_DISTANCE_PCT/MAX_TP_DISTANCE_PCT (ดู config.py)

ใช้: python backtest_tp_sweep.py <SYMBOL> [DAYS=360] [ratios คั่นด้วย , เช่น 1.0,1.272,1.618,2.0]
     python backtest_tp_sweep.py XAUUSDm 360 1.0,1.272,1.414,1.618,2.0
"""
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from mt5_connect import connect
from config import MT5_TIMEFRAMES, MAX_TP_DISTANCE_PCT
import swing   # แก้ swing.TP_FIB_RATIO ตรงๆ ตอน sweep — ดู docstring ด้านบน
from scoring import compute_score, get_trend_bias, calc_rr
from binance import merge_real_volume

SCAN_STEP_H   = 4     # สแกนหา entry ทุกกี่ชม. (เหมือน backtest_exit_compare.py — ลด MT5 calls
                      # เทียบกับ scheduler จริงที่รันทุก 1 ชม. อาจพลาด entry ที่เกิดแล้วหายไป
                      # ภายใน 4 ชม. แต่ scoring อ้างอิงแท่ง 4H เป็นหลักอยู่แล้วผลกระทบต่ำ)
SIM_TIMEFRAME = "4H"  # กริดจำลอง exit — ตรงกับ TF ที่ scoring ใช้หา SL/Structure ตอนเข้า
COOLDOWN_BARS = 6     # กันเข้าซ้อนไม้เดิมที่ยังไม่ปิด (6 แท่ง 4H = 1 วัน — ประมาณ เหมือน
                      # backtest_exit_compare.py ไม่ใช่ COOLDOWN_HOURS_BY_SYMBOL ตัวจริง)
DEFAULT_RATIOS = [1.0, 1.272, 1.414, 1.618, 2.0]


def get_hist(symbol, tf, dt, bars):
    rates = mt5.copy_rates_from(symbol, tf, dt, bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def score_entry(symbol: str, snap_dt: datetime, ratio: float):
    """เหมือน backtest_exit_compare.score_entry แต่ตั้ง swing.TP_FIB_RATIO=ratio ก่อนเรียก
    compute_score — ทำให้ TP/R:R/hard block ทั้งหมดถูกคำนวณด้วย ratio นี้เป๊ะเหมือนระบบจริง
    คืน None ถ้าไม่มี bias หรือไม่ผ่านสกอร์การ์ด/hard block (= ratio นี้ไม่เปิดไม้ที่จุดนี้)"""
    df_1d = get_hist(symbol, MT5_TIMEFRAMES["1D"], snap_dt, 800)
    if df_1d is None or len(df_1d) < 205:
        return None
    df_1d = merge_real_volume(df_1d, symbol, "1D")
    direction, _ = get_trend_bias(symbol, df_1d)
    if direction is None:
        return None

    df_1h_snap = get_hist(symbol, MT5_TIMEFRAMES["1H"], snap_dt, 2)
    if df_1h_snap is None or df_1h_snap.empty:
        return None
    entry = df_1h_snap["close"].iloc[-1]

    swing.TP_FIB_RATIO = ratio
    try:
        total, criteria, passed, sl_info = compute_score(symbol, direction, entry, as_of=snap_dt)
    except ValueError:
        return None
    if not passed:
        return None

    sl, tp = sl_info["sl"], sl_info["tp"]
    rr = calc_rr(entry, sl, tp, direction)
    tp_dist_pct = abs(tp - entry) / entry * 100
    return {"direction": direction, "entry": entry, "sl": sl, "tp": tp, "rr": rr,
            "score": total, "time": snap_dt, "tp_dist_pct": tp_dist_pct}


def simulate_fixed(df: pd.DataFrame, start_idx: int, direction: str,
                   entry: float, sl: float, tp: float) -> dict:
    """เดินแท่งไปข้างหน้าจน SL/TP โดน หรือหมดข้อมูล (ตัดที่ close ล่าสุด = ยังไม่ปิด)"""
    risk = abs(entry - sl)
    for i in range(start_idx, len(df)):
        bar = df.iloc[i]
        if direction == "Long":
            hit_sl = bar["low"]  <= sl
            hit_tp = bar["high"] >= tp
        else:
            hit_sl = bar["high"] >= sl
            hit_tp = bar["low"]  <= tp
        if hit_sl:
            return {"result": "SL", "r_multiple": -1.0, "bars_held": i - start_idx}
        if hit_tp:
            r = (tp - entry) / risk if direction == "Long" else (entry - tp) / risk
            return {"result": "TP", "r_multiple": r, "bars_held": i - start_idx}
    last_close = df["close"].iloc[-1]
    r = (last_close - entry) / risk if direction == "Long" else (entry - last_close) / risk
    return {"result": "Open", "r_multiple": r, "bars_held": len(df) - start_idx}


def run_ratio(symbol: str, ratio: float, big_4h: pd.DataFrame,
             window_start: datetime, now: datetime) -> list[dict]:
    """สแกนทั้งช่วงเวลาด้วย ratio เดียว แบบ one-position-at-a-time เป็นของตัวเอง"""
    trades = []
    last_entry_bar_idx = -10_000
    snap_dt = window_start
    while snap_dt <= now - timedelta(hours=1):
        sig = score_entry(symbol, snap_dt, ratio)
        if sig:
            idx_arr = big_4h.index[big_4h["time"] <= snap_dt]
            if len(idx_arr) == 0:
                snap_dt += timedelta(hours=SCAN_STEP_H)
                continue
            start_idx = idx_arr[-1]
            if start_idx - last_entry_bar_idx >= COOLDOWN_BARS:
                s = start_idx + 1
                res = simulate_fixed(big_4h, s, sig["direction"], sig["entry"], sig["sl"], sig["tp"])
                trades.append({**sig, **res,
                              "in_sample": None,   # เติมทีหลังตาม split point
                              "tp_near_max": sig["tp_dist_pct"] > MAX_TP_DISTANCE_PCT * 0.8})
                last_entry_bar_idx = start_idx
        snap_dt += timedelta(hours=SCAN_STEP_H)
    return trades


def summarize(label: str, trades: list[dict]) -> dict:
    if not trades:
        return {"label": label, "n": 0, "total_r": 0.0, "win_rate": 0.0}
    rs = [t["r_multiple"] for t in trades]
    total = sum(rs)
    wins = len([r for r in rs if r > 0])
    return {
        "label": label, "n": len(trades), "total_r": total,
        "avg_r": total / len(trades), "win_rate": wins / len(trades) * 100,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python backtest_tp_sweep.py <SYMBOL> [DAYS=360] [ratios คั่นด้วย ,]")
        sys.exit(1)
    symbol = sys.argv[1]
    days   = int(sys.argv[2]) if len(sys.argv) > 2 else 360
    ratios = [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else DEFAULT_RATIOS
    orig_ratio = swing.TP_FIB_RATIO   # เก็บค่าเดิมไว้คืนตอนจบ กันสคริปต์ตัวอื่นในโปรเซสเดียวกันเพี้ยน

    load_dotenv()
    connect()

    now = datetime.now()
    window_start = now - timedelta(days=days)
    # แบ่งครึ่งเวลาไม่ทับกัน: ครึ่งหลัง (ใกล้ปัจจุบัน) = IN-sample, ครึ่งแรก = OOS
    split_dt = window_start + (now - window_start) / 2

    print(f"\nTP_FIB_RATIO sweep — {symbol}  ({days} วัน: {window_start:%Y-%m-%d} -> {now:%Y-%m-%d})")
    print(f"  OOS (out-of-sample) : {window_start:%Y-%m-%d} -> {split_dt:%Y-%m-%d}")
    print(f"  IN  (in-sample)     : {split_dt:%Y-%m-%d} -> {now:%Y-%m-%d}")
    print(f"  ratios ที่ทดสอบ      : {ratios}")
    print(f"  scan step={SCAN_STEP_H}h  sim timeframe={SIM_TIMEFRAME}  cooldown={COOLDOWN_BARS} แท่ง\n")

    big_4h = get_hist(symbol, MT5_TIMEFRAMES[SIM_TIMEFRAME], now, (days + 10) * 6 + 60)
    if big_4h is None:
        print(f"[ERROR] ดึงข้อมูล {SIM_TIMEFRAME} ของ {symbol} ไม่ได้")
        mt5.shutdown()
        return

    results = {}
    try:
        for ratio in ratios:
            print(f"--- ratio = {ratio} ---")
            trades = run_ratio(symbol, ratio, big_4h, window_start, now)
            for t in trades:
                t["in_sample"] = t["time"] >= split_dt
            in_trades  = [t for t in trades if t["in_sample"]]
            oos_trades = [t for t in trades if not t["in_sample"]]
            all_s  = summarize("ALL", trades)
            in_s   = summarize("IN",  in_trades)
            oos_s  = summarize("OOS", oos_trades)
            near_max = sum(1 for t in trades if t["tp_near_max"])
            results[ratio] = {"all": all_s, "in": in_s, "oos": oos_s,
                              "near_max_tp": near_max, "trades": trades}
            print(f"  ALL: {all_s['n']:>3} ไม้  TotalR={all_s['total_r']:+7.2f}  "
                  f"Win%={all_s.get('win_rate', 0):5.1f}%")
            print(f"  IN : {in_s['n']:>3} ไม้  TotalR={in_s['total_r']:+7.2f}  "
                  f"Win%={in_s.get('win_rate', 0):5.1f}%")
            print(f"  OOS: {oos_s['n']:>3} ไม้  TotalR={oos_s['total_r']:+7.2f}  "
                  f"Win%={oos_s.get('win_rate', 0):5.1f}%")
            if near_max:
                print(f"  ⚠️  {near_max} ไม้มี TP เกิน 80% ของเพดาน MAX_TP_DISTANCE_PCT "
                      f"({MAX_TP_DISTANCE_PCT}%) — เสี่ยงโดน hard block ถ้าราคาขยับแคบลงอีกนิด")
            print()
    finally:
        swing.TP_FIB_RATIO = orig_ratio   # คืนค่าเดิมเสมอ แม้ error กลางทาง
        mt5.shutdown()

    print("=" * 78)
    print(f"  สรุปเทียบทุก ratio — {symbol}")
    print("=" * 78)
    print(f"  {'ratio':>8}  {'ALL n':>6}{'ALL TotalR':>12}{'ALL Win%':>10}  "
          f"{'IN TotalR':>11}{'IN Win%':>9}  {'OOS TotalR':>12}{'OOS Win%':>10}  {'TP หนา':>8}")
    for ratio in ratios:
        r = results[ratio]
        print(f"  {ratio:>8}  {r['all']['n']:>6}{r['all']['total_r']:>+12.2f}"
              f"{r['all'].get('win_rate', 0):>9.1f}%  "
              f"{r['in']['total_r']:>+11.2f}{r['in'].get('win_rate', 0):>8.1f}%  "
              f"{r['oos']['total_r']:>+12.2f}{r['oos'].get('win_rate', 0):>9.1f}%  "
              f"{r['near_max_tp']:>8}")
    print("=" * 78)
    print("  เกณฑ์เลือก: TotalR ทั้ง IN และ OOS เป็นบวกพร้อมกัน (ไม่ overfit ไปช่วงเดียว)")
    print("  แล้วดู Win% + จำนวนไม้ประกอบ — ระวัง sample เล็ก (<10 ไม้) ยังไม่มีนัยสำคัญทางสถิติ")
    print("=" * 78)


if __name__ == "__main__":
    main()
