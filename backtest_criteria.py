"""
backtest_criteria.py — วัดรายเกณฑ์ในสกอร์การ์ด (scoring.compute_score) ว่า
  1) "active บ่อยแค่ไหน"  = ผ่านเกณฑ์กี่ % ของ setup ที่เข้าเทรดได้จริง
  2) "active win rate"    = ถ้าเข้าไม้ทุกครั้งที่เกณฑ์นั้นผ่าน จะชนะกี่ %  (เทียบกับตอนที่ไม่ผ่าน)

ต่างจาก backtest_scorecard.py ตรงที่ **ไม่กรองด้วย MIN_SCORE** — เก็บทุกแท่งที่ระบบ
"เข้าไม้ได้" (bias ตรง + หา SL/TP ได้ + ไม่ติด hard block) เป็น 1 sample แล้วจำลองผลไป
ข้างหน้าทุก sample แบบอิสระ (ทับซ้อนกันได้ ไม่ได้จำกัดถือทีละไม้) เพื่อให้มีจำนวนตัวอย่าง
พอจะแยกผลของเกณฑ์แต่ละตัวออกจากกัน — เป็นสถิติเชิงวินิจฉัย ไม่ใช่ equity curve ที่เทรดได้จริง

as_of ที่ส่งให้ compute_score = "เวลาเปิดแท่ง 4H" ตรงๆ ซึ่งหลัง 2026-08-26 หมายถึงเห็นเฉพาะ
แท่งที่ปิดแล้ว ณ วินาทีนั้น (ดู bars.get_bars) — entry = ราคาปิด ณ เวลานั้น = ราคาเปิดแท่งพอดี

ปิดไม้ที่ SL/TP เป๊ะ หรือ time-exit ที่ MAX_HOLD_BARS แท่ง (เหมือน backtest_scorecard.py) แท่งที่
ชนทั้ง SL และ TP นับเป็น SL (conservative) — ยังไม่จำลอง Exit Monitor (trailing/partial close)

ใช้: py backtest_criteria.py BTCUSDm [days=730]
"""
import sys
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from mt5_connect import connect
from config import (MT5_TIMEFRAMES, MIN_SCORE, TOTAL_WEIGHT, MIN_RR_HARD_BLOCK,
                    MAX_RR_HARD_BLOCK, MAX_TP_DISTANCE_PCT, get_min_sl_distance_pct)
from scoring import compute_score, get_trend_bias, get_ohlcv, ema, TREND_FLIP_K, calc_rr
from binance import merge_real_volume
from swing import calc_atr
from trend_flip import compute_trend_regime
from regime_check import get_regime

# regime พวกนี้ scheduler จะ SKIP ไม่เปิด scorecard เลย — อ่านจาก config ที่เดียว
# 2026-09-15: เดิมเป็นสำเนา literal ที่ **ค้างไว้ 3 ตัว** ตั้งแต่ "TREND แรงจัด" ถูกย้ายเข้า
# NO_TRADE (2026-08-31) = ไฟล์นี้วัดเกณฑ์บนไม้ที่ระบบจริงไม่เทรดอยู่ 2 สัปดาห์
from config import REGIME_NO_TRADE

MAX_HOLD_BARS = 60   # ถือไม้นานสุดกี่แท่ง 4H (=10 วัน) ก่อน time-exit — ตรงกับ backtest_scorecard.py
samples = []
skipped = {}

last_date, day_ctx = None, None


SKIP_PATTERNS = [
    ("Direction ไม่ตรง Bias",  "bias 1D สวนทาง"),
    ("หา SL ไม่ได้",            "หา SL จาก swing ไม่ได้"),
    ("ระยะ SL ห่างจาก entry",   "SL แคบกว่าขั้นต่ำ (hard block)"),
    ("ต่ำกว่าขั้นต่ำ",           "R:R ต่ำกว่าขั้นต่ำ (hard block)"),
    ("สูงเกินขั้นสูงสุด",        "R:R สูงเกินขั้นสูงสุด (hard block)"),
    ("TP ห่างจาก entry",       "TP ไกลเกินขั้นสูงสุด (hard block)"),
]


def note_skip(reason: str) -> None:
    key = next((label for pat, label in SKIP_PATTERNS if pat in reason), reason[:46])
    skipped[key] = skipped.get(key, 0) + 1


def build_day_context(t):
    """ข้อมูลระดับวันที่ใช้ซ้ำได้ทั้งวัน — เฟรม 1D, bias, EMA50, ATR และ "อายุ" ของ regime
    (จำนวนแท่ง 1D นับจากครั้งล่าสุดที่ trend_flip เปลี่ยนทิศ) ใช้ตอบว่าเกณฑ์ Trend 1D ที่ผลติดลบ
    เป็นเพราะ "เข้าไม้ตอนราคาวิ่งไปไกลจาก EMA แล้ว" หรือเพราะ "trend แก่แล้ว" กันแน่"""
    df_1d = merge_real_volume(
        get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t), symbol, "1D", as_of=t)
    bias, _ = get_trend_bias(symbol, df_1d)
    if bias is None:
        return None

    closed_1d = df_1d.iloc[:-1].reset_index(drop=True)   # เหมือนที่ get_trend_bias ใช้
    flip_df, _ = compute_trend_regime(closed_1d, k=TREND_FLIP_K[symbol])
    regime = flip_df["regime"]
    same   = regime == regime.iloc[-1]
    age    = int(len(regime) - same[::-1].idxmin() - 1) if (~same).any() else len(regime)

    return {"df_1d": df_1d, "bias": bias,
            "ema50": float(ema(df_1d["close"], 50).iloc[-1]),
            "atr":   float(calc_atr(df_1d, 14).iloc[-1]),
            "regime_age": age}



# ---------------------------------------------------------------------------
# สรุปผล
# ---------------------------------------------------------------------------

def stats(rows: list) -> tuple:
    if not rows:
        return 0, 0.0, 0.0
    wins = sum(1 for r in rows if r["r"] > 0)
    return len(rows), wins / len(rows) * 100, sum(r["r"] for r in rows) / len(rows)


def z_two_prop(on: list, off: list) -> float:
    """z ของผลต่าง win rate ระหว่างสองกลุ่ม — |z| >= 1.96 คือต่างอย่างมีนัยสำคัญที่ 95%
    (ใช้กับชุด non-overlap เท่านั้น ชุดเต็มมี sample ทับซ้อนกัน z จะสูงเกินจริง)"""
    if not on or not off:
        return 0.0
    n1, n2 = len(on), len(off)
    p1 = sum(1 for r in on if r["r"] > 0) / n1
    p2 = sum(1 for r in off if r["r"] > 0) / n2
    p  = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return (p1 - p2) / se if se > 0 else 0.0


def pick_non_overlap(rows: list) -> list:
    """เลือกเฉพาะไม้ที่ไม่ทับซ้อนกันตามเวลา (ไล่จากเก่าไปใหม่ ข้ามไม้ที่เปิดก่อนไม้ก่อนหน้าปิด)
    — ได้ตัวอย่างที่ "อิสระ" ต่อกันจริง ใช้ทดสอบนัยสำคัญได้ไม่หลอกตัวเอง"""
    out, free_at = [], None
    for s in sorted(rows, key=lambda x: x["time"]):
        if free_at is None or s["time"] >= free_at:
            out.append(s)
            free_at = s["exit_time"]
    return out



# main guard (2026-09-15) — ก่อนหน้านี้ `import backtest_criteria` = รัน backtest ทั้งไฟล์
# (ต่อ MT5 + เดิน 730 วัน) สคริปต์ตรวจเล็กๆ ที่แค่อยาก import ค่าคงที่จึงค้างไปเลย
# ตัวแปรที่ helper ด้านบนอ่าน (symbol / skipped) ยังเป็น global เหมือนเดิม เพราะบล็อก if
# ไม่สร้าง scope ใหม่ — พฤติกรรมตอนรันจากบรรทัดคำสั่งจึงไม่เปลี่ยนแม้แต่บรรทัดเดียว
if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
    days   = int(sys.argv[2]) if len(sys.argv) > 2 else 730
    connect()

    # แท่ง 4H ชุดใหญ่ไว้ (ก) ไล่เวลาสแกน (ข) เดินจำลองผลไปข้างหน้า — ดึงครั้งเดียว
    d4h = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=days * 6 + 400)

    end_time   = d4h["time"].iloc[-1]
    start_time = end_time - timedelta(days=days)
    scan_idx   = d4h.index[d4h["time"] >= start_time].tolist()

    print(f"\n{symbol}  criteria backtest  {d4h['time'][scan_idx[0]]} -> {end_time}  "
          f"({len(scan_idx)} แท่ง 4H)")
    print(f"MAX_HOLD_BARS={MAX_HOLD_BARS}   MIN_SCORE={MIN_SCORE}/{TOTAL_WEIGHT:.0f}\n")

    for n, i in enumerate(scan_idx):
        t = d4h["time"].iloc[i]

        # ข้อมูลระดับ 1D เปลี่ยนวันละครั้ง — คำนวณซ้ำเฉพาะตอนข้ามวัน
        if t.date() != last_date:
            try:
                day_ctx = build_day_context(t)
            except Exception:
                day_ctx = None
            last_date = t.date()
        if day_ctx is None:
            note_skip("no bias / ดึง 1D ไม่ได้")
            continue

        df_1d_cache, bias = day_ctx["df_1d"], day_ctx["bias"]

        # regime gate — ระบบจริงเช็คก่อนทุกครั้ง (scheduler.py) แต่ backtest ก่อนหน้านี้ไม่เคยใส่
        try:
            regime = get_regime(symbol, as_of=t)["regime"]
        except Exception:
            regime = "ERROR"
        regime_ok = regime not in REGIME_NO_TRADE

        # force=True ข้าม hard block ทั้งหมด แล้วมาตรวจเองว่า "ถ้าไม่ force จะโดนบล็อกด้วยข้อไหน"
        # — เก็บไม้ที่ถูกบล็อกไว้วัดผลด้วย ตอบคำถามว่า hard block แต่ละข้อกันไม้แย่ได้จริงไหม
        # หรือกันไม้ดีทิ้งไปเปล่าๆ (ไม้พวกนี้ไม่เคยปรากฏในสถิติมาก่อนเลย เพราะ raise ทิ้งไปก่อน)
        try:
            entry = float(get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=2, as_of=t)["close"].iloc[-1])
            total, criteria, passed, sl_info = compute_score(symbol, bias, entry, as_of=t,
                                                             df_1d=df_1d_cache, force=True)
        except ValueError as exc:
            note_skip(str(exc))     # เหลือแค่ bias ไม่ตรง / หา SL ไม่ได้ ซึ่ง force ก็ช่วยไม่ได้
            continue
        except Exception as exc:
            note_skip(f"ERROR {type(exc).__name__}")
            continue

        sl, tp = sl_info["sl"], sl_info["tp"]
        risk   = abs(entry - sl)
        rr     = calc_rr(entry, sl, tp, bias)

        blocks = []
        if abs(entry - sl) / entry * 100 < get_min_sl_distance_pct(symbol) - 1e-9:
            blocks.append("SL แคบ")
        if rr < MIN_RR_HARD_BLOCK - 1e-9:
            blocks.append("R:R ต่ำ")
        if rr > MAX_RR_HARD_BLOCK + 1e-9:
            blocks.append("R:R สูง")
        if abs(tp - entry) / entry * 100 > MAX_TP_DISTANCE_PCT + 1e-9:
            blocks.append("TP ไกล")
        for b in blocks:
            note_skip(f"[hard block] {b}")

        # จำลองผลไปข้างหน้าจากแท่ง i (แท่งที่เพิ่งเปิดที่เวลา t)
        outcome, exit_price, exit_time = None, None, None
        for j in range(i, min(i + MAX_HOLD_BARS, len(d4h))):
            bar = d4h.iloc[j]
            hit_sl = (bar["low"] <= sl) if bias == "Long" else (bar["high"] >= sl)
            hit_tp = (bar["high"] >= tp) if bias == "Long" else (bar["low"] <= tp)
            if hit_sl:
                outcome, exit_price = "SL", sl
            elif hit_tp:
                outcome, exit_price = "TP", tp
            elif j == i + MAX_HOLD_BARS - 1:
                outcome, exit_price = "TIME", float(bar["close"])
            if outcome:
                exit_time = bar["time"]
                break
        if outcome is None:
            continue   # ไม้ที่ยังไม่จบภายในข้อมูลที่มี — ตัดทิ้ง ไม่เอามาคิดสถิติ

        r_mult = ((exit_price - entry) / risk) if bias == "Long" else ((entry - exit_price) / risk)
        # ระยะห่างจาก EMA50 1D "ตามทิศที่เทรด" (+ = ราคาวิ่งไปทางเดียวกับไม้แล้ว = ไล่ราคา)
        gap    = (entry - day_ctx["ema50"]) * (1 if bias == "Long" else -1)
        samples.append({"time": t, "direction": bias, "score": total, "passed": passed,
                        "outcome": outcome, "r": r_mult, "exit_time": exit_time,
                        "entry": entry, "sl": sl, "tp": tp, "rr": rr,
                        "blocked": ";".join(blocks), "tradeable": not blocks,
                        "regime": regime, "regime_ok": regime_ok,
                        "ema_gap_pct": gap / entry * 100,
                        "ema_gap_atr": gap / day_ctx["atr"] if day_ctx["atr"] else 0.0,
                        "regime_age": day_ctx["regime_age"],
                        "crit": {name: bool(p) for name, p, _ in criteria},
                        "weights": {name: w for name, _, w in criteria}})

        if n % 200 == 0:
            print(f"  ... {t}  ({n}/{len(scan_idx)})  เก็บได้ {len(samples)} setup", flush=True)

    mt5.shutdown()
    print(f"\n{'=' * 78}")
    print(f"  แท่งที่สแกน {len(scan_idx)}  →  setup ที่เข้าเทรดได้ {len(samples)} ครั้ง")
    if skipped:
        print(f"  {'-' * 74}")
        print("  เหตุผลที่ข้าม (นับรวมทั้งช่วง):")
        for k, v in sorted(skipped.items(), key=lambda x: -x[1]):
            print(f"    {v:>5}  {k}")

    if not samples:
        print(f"{'=' * 78}")
        sys.exit(0)

    all_rows     = list(samples)          # เก็บไว้เขียน CSV (รวมไม้ที่ hard block กันไว้ด้วย)
    blocked_rows = [s for s in samples if not s["tradeable"]]
    samples      = [s for s in samples if s["tradeable"]]
    print(f"  {'-' * 74}")
    print(f"  ไม้ที่ hard block กันไว้ (เก็บใน CSV เพื่อวิเคราะห์ต่อ): {len(blocked_rows)} ครั้ง")
    for b in ("R:R ต่ำ", "SL แคบ", "TP ไกล", "R:R สูง"):
        rows = [s for s in blocked_rows if b in s["blocked"]]
        if rows:
            n, wr, r = stats(rows)
            print(f"    {b:<10} n={n:<5} Win {wr:>5.1f}%  Avg {r:+.2f}R")

    print(f"  {'-' * 74}")
    print("  regime ตอนเข้าไม้ (ระบบจริงเทรดเฉพาะที่ไม่อยู่ใน REGIME_NO_TRADE):")
    for rg in sorted({s["regime"] for s in samples}):
        rows = [s for s in samples if s["regime"] == rg]
        n, wr, r = stats(rows)
        mark = "" if rg not in REGIME_NO_TRADE else "   <- ระบบจริง SKIP"
        print(f"    {rg:<16} n={n:<5} Win {wr:>5.1f}%  Avg {r:+.2f}R{mark}")
    ok = [s for s in samples if s["regime_ok"]]
    n_ok, wr_ok, r_ok = stats(ok)
    print(f"    {'รวมที่เทรดได้':<16} n={n_ok:<5} Win {wr_ok:>5.1f}%  Avg {r_ok:+.2f}R")

    n_all, wr_all, r_all = stats(samples)
    print(f"  {'-' * 74}")
    print(f"  Baseline (เข้าทุก setup ไม่สนคะแนน) : Win {wr_all:.1f}%  Avg {r_all:+.2f}R  (n={n_all})")

    names = list(samples[0]["crit"].keys())

    print(f"\n  {'เกณฑ์':<18}{'น้ำหนัก':>7}{'Active':>9}  |{'  WR active':>11}{'  R active':>10}"
          f"  |{'  WR ไม่ active':>15}{'  R':>8}  |{'  ΔWR':>8}")
    print(f"  {'-' * 74}")
    for name in names:
        w   = samples[0]["weights"][name]
        on  = [s for s in samples if s["crit"][name]]
        off = [s for s in samples if not s["crit"][name]]
        n_on, wr_on, r_on    = stats(on)
        n_off, wr_off, r_off = stats(off)
        d_wr = (wr_on - wr_off) if (on and off) else 0.0
        print(f"  {name:<18}{w:>7.0f}{n_on / n_all * 100:>8.1f}%  |{wr_on:>10.1f}%{r_on:>+10.2f}R"
              f"  |{wr_off:>14.1f}%{r_off:>+8.2f}R  |{d_wr:>+7.1f}")

    # --- ชุด non-overlap: ตัวอย่างที่อิสระต่อกันจริง + ทดสอบนัยสำคัญ ---
    indep = pick_non_overlap(samples)
    n_ind, wr_ind, r_ind = stats(indep)
    print(f"\n  ชุด non-overlap (ไม้ไม่ทับเวลากัน — ใช้ทดสอบนัยสำคัญ): n={n_ind}  "
          f"Win {wr_ind:.1f}%  Avg {r_ind:+.2f}R")
    print(f"  {'เกณฑ์':<18}{'Active':>9}{'  WR active':>12}{'  WR ไม่ active':>16}{'  ΔWR':>8}{'  z':>7}{'  นัยสำคัญ':>11}")
    print(f"  {'-' * 74}")
    for name in names:
        on  = [s for s in indep if s["crit"][name]]
        off = [s for s in indep if not s["crit"][name]]
        n_on, wr_on, _  = stats(on)
        n_off, wr_off, _ = stats(off)
        z = z_two_prop(on, off)
        sig = "ใช่" if abs(z) >= 1.96 else "ไม่"
        print(f"  {name:<18}{(n_on / n_ind * 100 if n_ind else 0):>8.1f}%{wr_on:>11.1f}%"
              f"{wr_off:>15.1f}%{(wr_on - wr_off):>+8.1f}{z:>+7.2f}{sig:>10}")

    # --- คะแนนรวม vs ผลลัพธ์ ---
    print(f"\n  {'Score':<10}{'n':>6}{'WinRate':>10}{'AvgR':>9}")
    print(f"  {'-' * 34}")
    for sc in sorted({round(s['score'], 1) for s in samples}):
        rows = [s for s in samples if abs(s["score"] - sc) < 1e-9]
        n, wr, r = stats(rows)
        mark = "  <= MIN_SCORE" if abs(sc - MIN_SCORE) < 1e-9 else ""
        print(f"  {sc:<10.1f}{n:>6}{wr:>9.1f}%{r:>+8.2f}R{mark}")

    print(f"\n  {'Score >=':<10}{'n':>6}{'WinRate':>10}{'AvgR':>9}")
    print(f"  {'-' * 34}")
    for thr in sorted({round(s["score"], 1) for s in samples}):
        rows = [s for s in samples if s["score"] >= thr - 1e-9]
        n, wr, r = stats(rows)
        print(f"  {thr:<10.1f}{n:>6}{wr:>9.1f}%{r:>+8.2f}R")

    # --- แยกทิศทาง / outcome ---
    print(f"\n  {'-' * 74}")
    for d in ("Long", "Short"):
        rows = [s for s in samples if s["direction"] == d]
        n, wr, r = stats(rows)
        print(f"  {d:<6} n={n:<5} Win {wr:.1f}%  Avg {r:+.2f}R")
    for oc in ("TP", "SL", "TIME"):
        rows = [s for s in samples if s["outcome"] == oc]
        print(f"  exit {oc:<5} {len(rows):>5} ครั้ง ({len(rows) / n_all * 100:.1f}%)")
    print(f"{'=' * 78}")

    # --- dump ทุก setup ลง CSV เพื่อวิเคราะห์ต่อแบบไม่ต้องรัน MT5 ซ้ำ (เช่น เทียบสูตรให้คะแนน
    #     แบบต่างๆ กับ sample ชุดเดียวกันเป๊ะ) ---
    csv_path = f"criteria_samples_{symbol}.csv"
    pd.DataFrame([{"time": s["time"], "direction": s["direction"], "score": s["score"],
                   "passed": s["passed"], "outcome": s["outcome"], "r": s["r"],
                   "entry": s["entry"], "sl": s["sl"], "tp": s["tp"], "rr": s["rr"],
                   "blocked": s["blocked"], "tradeable": s["tradeable"],
                   "regime": s["regime"], "regime_ok": s["regime_ok"], "rr": s["rr"],
                   "ema_gap_pct": s["ema_gap_pct"], "ema_gap_atr": s["ema_gap_atr"],
                   "regime_age": s["regime_age"], "exit_time": s["exit_time"],
                   **{f"c_{k}": v for k, v in s["crit"].items()}} for s in all_rows]
                ).to_csv(csv_path, index=False)
    print(f"\n  เขียน {len(all_rows)} setup ลง {csv_path} แล้ว "
          f"(เข้าเทรดได้จริง {len(samples)} + hard block กันไว้ {len(blocked_rows)})")
