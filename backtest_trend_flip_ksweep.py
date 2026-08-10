"""
backtest_trend_flip_ksweep.py — ขั้นตอน E: หา k* ที่เหมาะสมสำหรับ trend_flip.compute_trend_regime
ด้วยข้อมูลย้อนหลังจริง ไม่ใช่เดาเอง

สำหรับ k = 0.20, 0.25, ..., 0.60:
    รัน compute_trend_regime บนข้อมูลย้อนหลังทั้งหมด
    วัด:
      Flip_total(k)  = จำนวนครั้งที่พลิกทั้งหมด
      FalseFlip(k)   = พลิกแล้วกลับทิศเดิมภายใน <=3 แท่ง (สัญญาณหลอก)
      LagVsEMA(k)    = ระยะห่าง (แท่ง) จากจุด EMA50/200 cross ที่ใกล้สุดฝั่งเดียวกัน
                        (ลบ = พลิกเร็วกว่า EMA cross, บวก = ช้ากว่า)

เลือก k* ที่ FalseFlip(k) ต่ำสุด โดย LagVsEMA(k) ยังเป็นลบ (เร็วกว่า EMA cross จริง)
ไม่งั้นไม่มีประโยชน์ที่จะใช้แทน EMA — เป็นเกณฑ์เลือกอัตโนมัติเบื้องต้น ดูตารางเต็มประกอบ
การตัดสินใจเองได้เสมอ

ใช้: python backtest_trend_flip_ksweep.py <SYMBOL> <BARS>
     python backtest_trend_flip_ksweep.py BTCUSDm 2000
"""
import sys

import MetaTrader5 as mt5
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from mt5_connect import connect
from config import MT5_TIMEFRAMES
from scoring import ema
from trend_flip import compute_trend_regime

K_VALUES = [round(0.20 + 0.05 * i, 2) for i in range(9)]   # 0.20 .. 0.60
FALSE_FLIP_BARS = 3
EMA_MATCH_WINDOW = 60   # ถ้าไม่มี EMA cross ทิศเดียวกันภายในกี่แท่ง ถือว่าไม่มีคู่เทียบ


def get_hist(symbol: str, bars: int, tf: str = "4H") -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES[tf], 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"ดึงข้อมูลไม่ได้ {symbol}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.iloc[:len(df) - 1].reset_index(drop=True)   # ตัดแท่งยังไม่ปิด


def ema_cross_events(df: pd.DataFrame) -> list[dict]:
    """หาแท่งที่ EMA50 ตัด EMA200 คืน [{"idx","dir"}] — dir="Bull" ถ้า EMA50 ขึ้นเหนือ EMA200"""
    e50 = ema(df["close"], 50)
    e200 = ema(df["close"], 200)
    diff = e50 - e200
    events = []
    for i in range(1, len(diff)):
        prev_sign = 1 if diff.iloc[i - 1] > 0 else -1
        cur_sign = 1 if diff.iloc[i] > 0 else -1
        if cur_sign != prev_sign:
            events.append({"idx": i, "dir": "Bull" if cur_sign > 0 else "Bear"})
    return events


def count_false_flips(flips: list[dict], max_bars: int = FALSE_FLIP_BARS) -> int:
    """นับ flip ที่กลับทิศเดิมภายใน <=max_bars แท่งหลังจากพลิก (สัญญาณหลอก)"""
    count = 0
    for i in range(len(flips) - 1):
        cur_f, next_f = flips[i], flips[i + 1]
        if next_f["from"] == cur_f["to"] and (next_f["idx"] - cur_f["idx"]) <= max_bars:
            count += 1
    return count


def avg_lag_vs_ema(flips: list[dict], ema_events: list[dict], window: int = EMA_MATCH_WINDOW):
    """เทียบ flip แต่ละอันกับ EMA cross ทิศเดียวกันที่ใกล้ที่สุด (ภายใน window แท่ง)
    คืน (mean_signed_lag, matched_count) — signed: ลบ = flip เร็วกว่า EMA"""
    lags = []
    for f in flips:
        candidates = [e for e in ema_events if e["dir"] == f["to"] and abs(e["idx"] - f["idx"]) <= window]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda e: abs(e["idx"] - f["idx"]))
        lags.append(f["idx"] - nearest["idx"])
    if not lags:
        return None, 0
    return sum(lags) / len(lags), len(lags)


def sweep(symbol: str, bars: int, tf: str = "4H"):
    df = get_hist(symbol, bars, tf)
    ema_events = ema_cross_events(df)
    print(f"\n{'=' * 78}")
    print(f"  {symbol}  ({len(df)} แท่ง {tf})   EMA50/200 cross ทั้งหมด: {len(ema_events)} ครั้ง")
    print(f"{'=' * 78}")
    print(f"  {'k':>5}  {'Flip_total':>10}  {'FalseFlip':>10}  {'FalseFlip%':>10}  "
          f"{'LagVsEMA':>10}  {'matched':>8}")

    rows = []
    for k in K_VALUES:
        _, flips = compute_trend_regime(df, k=k)
        total = len(flips)
        false_n = count_false_flips(flips)
        false_pct = (false_n / total * 100) if total else float("nan")
        lag, matched = avg_lag_vs_ema(flips, ema_events)
        lag_str = f"{lag:+.1f}" if lag is not None else "n/a"
        print(f"  {k:>5.2f}  {total:>10}  {false_n:>10}  {false_pct:>9.1f}%  "
              f"{lag_str:>10}  {matched:>8}")
        rows.append({"k": k, "total": total, "false_n": false_n,
                     "false_pct": false_pct, "lag": lag, "matched": matched})

    # เลือก k*: FalseFlip% ต่ำสุด (ไม่ใช่ false_n ดิบ) ในกลุ่มที่ lag เป็นลบ (เร็วกว่า EMA cross
    # จริง) และมี matched >= 1 — 2026-08-09: เดิมเลือกจาก false_n (จำนวนดิบ) ทำให้ k ที่ flip
    # น้อยครั้งเกินไป (นิ่งเกิน, สัญญาณช้า/พลาดจุดกลับตัวจริง) ถูกเลือกได้ง่ายๆ แค่เพราะมี flip
    # ทั้งหมดน้อยอยู่แล้ว ไม่ใช่เพราะแต่ละ flip แม่นกว่าจริง — บั๊กนี้ไม่เคยแสดงผลกระทบมาก่อนเพราะ
    # k ที่เลือกได้ในอดีตทุก symbol (BTC/XAU/ETH/XRP) บังเอิญได้ FalseFlip=0/N (0%) พอดีอยู่แล้ว
    # ซึ่งเป็นค่าต่ำสุดทั้งแบบดิบและเปอร์เซ็นต์เหมือนกัน จึงไม่มีทางเลือกผิดได้ในเคสนั้น
    candidates = [r for r in rows if r["lag"] is not None and r["lag"] < 0 and r["total"] > 0]
    if candidates:
        best = min(candidates, key=lambda r: r["false_pct"])
        print(f"\n  >>> k* แนะนำ = {best['k']:.2f}  "
              f"(FalseFlip={best['false_n']}/{best['total']} = {best['false_pct']:.1f}%, "
              f"เร็วกว่า EMA cross เฉลี่ย {abs(best['lag']):.1f} แท่ง)")
    else:
        print(f"\n  >>> ไม่มี k ไหนเร็วกว่า EMA cross จริง (lag เป็นบวกหมด หรือไม่มีคู่เทียบ) "
              f"— ดูตารางเต็มประกอบการตัดสินใจเอง ค่านี้อาจไม่คุ้มใช้แทน EMA")
    return rows


if __name__ == "__main__":
    connect()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
    bars   = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    tf     = sys.argv[3] if len(sys.argv) > 3 else "4H"
    sweep(symbol, bars, tf)
