"""
adx_dir_at_entry.py — ตอน "เข้าไม้" เส้น ADX อยู่ในสภาพไหน แล้วสภาพนั้นแยกไม้ดี/ไม้เสียได้ไหม

ทำไมต้องมี: ด่าน regime รับทั้ง "ขึ้น" และ "ทรง" เท่ากันเป๊ะ (classify_regime: direction in
("ขึ้น","ทรง")) โดย "ทรง" คือถัง else — อะไรก็ตามที่ ADX ขยับไม่ถึง ADX_DIR_EPS (1.0) ใน
ADX_DIR_BARS (4) แท่ง ตกถังนี้หมด รวมถึงเส้นที่กำลังไหลลงช้าๆ คำถามคือถังนี้ควรถูกรับเท่ากับ
"ขึ้น" จริงไหม

วิธี: อ่านไม้จาก replay_trades_<sym>.csv แล้วคำนวณ ADX ย้อนหลังที่แท่ง 4H ที่ปิดล่าสุดก่อนเข้าไม้
(ตัวเดียวกับที่ get_regime ใช้ — closed_idx) ไม่รันกลยุทธ์ซ้ำ ~1 นาทีทั้ง 7 symbol
calc_adx เป็น EWM (causal) ค่าที่จุดเดิมจึงไม่เปลี่ยนแม้ดึงแท่งมาเกิน — ดึงทีเดียวต่อ symbol ได้

⚠️ เห็นเฉพาะไม้ที่ **ผ่านด่านมาแล้ว** — ตอบได้แค่ "ในกลุ่มที่เข้าได้ ทิศแยกผลได้ไหม"
   ตอบไม่ได้ว่า "ถ้าปล่อยให้ ADX ต่ำกว่า 22 เข้าด้วยจะเป็นยังไง" (นั่นต้อง replay เต็ม)

ใช้:  ./run_wine.sh adx_dir_at_entry.py
"""
import os
import sys

import pandas as pd

from mt5_connect import connect
from config import SYMBOLS
import regime_check as rc

connect()
rows = []
for symbol in SYMBOLS:
    path = f"replay_trades_{symbol}.csv"
    if not os.path.exists(path):
        print(f"  ข้าม {symbol}: ไม่มี {path}")
        continue
    tr = pd.read_csv(path, parse_dates=["time", "exit_time"])
    span_bars = int((pd.Timestamp.now() - tr["time"].min()).total_seconds() / 3600 / 4) + 300
    df = rc.get_adx_bars(symbol, bars=span_bars)
    adx = rc.calc_adx(df, rc.ADX_PERIOD)
    times = df["time"]
    bar_dur = pd.Timedelta(hours=4)

    for t in tr.itertuples():
        # แท่ง 4H ที่ปิดล่าสุด ณ เวลาที่ตัดสินใจ = แท่งที่ closed_idx ชี้ตอนสแกน
        closed = times.index[(times + bar_dur) <= t.time]
        if len(closed) == 0:
            continue
        i = closed[-1]
        if i < rc.ADX_PEAK_LOOKBACK + rc.ADX_DIR_BARS:
            continue
        peak = rc.adx_peak_info(adx, i)
        row = dict(symbol=symbol, time=t.time, strategy=t.strategy, R=t.R,
                   adx=float(adx.iloc[i]), dir=rc.adx_direction(adx, i),
                   diff=float(adx.iloc[i] - adx.iloc[i - rc.ADX_DIR_BARS]),
                   peak=float(peak["peak"]), declining=peak["declining"])
        # เก็บส่วนต่างที่ระยะเทียบอื่นไว้ด้วย — ใช้ตรวจความเปราะของไม้บรรทัดท้ายไฟล์
        for b in (3, 4, 5, 6, 8):
            row[f"d{b}"] = float(adx.iloc[i] - adx.iloc[i - b])
        rows.append(row)

df = pd.DataFrame(rows)
sc = df[df["strategy"] == "Scoring"]          # ด่าน TREND/ทิศ ใช้กับ Scoring เท่านั้น
print(f"\nไม้ทั้งหมด {len(df)} · Scoring {len(sc)} (ที่เหลือคือ Reversal ซึ่งเข้าคนละ branch)")


def block(name, d):
    if d.empty:
        print(f"  {name:22s} —")
        return
    print(f"  {name:22s} {len(d):3d} ไม้  ΣR {d.R.sum():+7.2f}  avgR {d.R.mean():+.3f}  "
          f"WR {(d.R > 0).mean() * 100:4.1f}%  sd {d.R.std():.2f}")


print("\n── Scoring แยกตามทิศ ADX ตอนเข้า ──")
for k in ("ขึ้น", "ทรง", "ลง"):
    block(k, sc[sc["dir"] == k])

up, flat = sc[sc["dir"] == "ขึ้น"], sc[sc["dir"] == "ทรง"]
if len(up) > 1 and len(flat) > 1:
    d = up.R.mean() - flat.R.mean()
    se = (up.R.var() / len(up) + flat.R.var() / len(flat)) ** 0.5
    sd = ((up.R.var() * (len(up) - 1) + flat.R.var() * (len(flat) - 1)) /
          (len(up) + len(flat) - 2)) ** 0.5
    print(f"\n  ต่าง {d:+.3f}R/ไม้ · SE {se:.3f} · t = {d / se:+.2f}")
    # n ที่ต้องใช้ถึงจะตอบได้ (CLAUDE.md ข้อ 8) — ถ้าผลต่างจริงเท่าที่วัดได้ตอนนี้
    if abs(d) > 1e-9:
        n = 2 * (1.96 + 0.84) ** 2 * sd ** 2 / d ** 2
        print(f"  ต้องใช้ ~{n:,.0f} ไม้/กลุ่ม ถึงจะยืนยันผลต่างขนาดนี้ได้ "
              f"(ตอนนี้มี {min(len(up), len(flat))})")

print("\n── Scoring แยกตามระดับ ADX ตอนเข้า ──")
for lo, hi in ((22, 24), (24, 27), (27, 30), (30, 40), (40, 999)):
    block(f"ADX {lo}-{hi}", sc[(sc["adx"] >= lo) & (sc["adx"] < hi)])

print("\n── ไม้ที่ ADX 'กำลังลงติดกัน' อยู่แล้วตอนเข้า (declining=True) ──")
block("declining", sc[sc["declining"]])
block("ไม่ declining", sc[~sc["declining"]])

# ── ไม้บรรทัดวัดทิศมันแข็งแรงแค่ไหน — ตรวจได้โดยไม่ใช้ R เลย จึงไม่ติดปัญหา n ──
# adx_direction ใช้ **2 จุด** ห่างกัน ADX_DIR_BARS แท่ง ไม่ได้ fit ความชัน จุดเทียบจึงตกลงบน
# หลังโก่ง/ก้นกระเด้งได้ (เคสจริง XAU 2026-09-17: จุดเทียบตกที่ก้นพอดี diff เหลือ −0.16
# ทั้งที่ 4 แท่งก่อนหน้าไหลลงมาตลอด — ถ้าเทียบ 5 แท่งจะได้ −1.05 = "ลง" = ไม่เข้าไม้)
# ตารางนี้ถามว่า "ถ้าขยับไม้บรรทัดนิดเดียว ไม้กี่ตัวจะไม่ได้เกิด"
print("\n── ความเปราะของไม้บรรทัดวัดทิศ ──")
print("   ไม้ Scoring กี่ตัวจะถูกตีเป็น 'ลง' (= ไม่ได้เข้าเลย) ถ้าขยับระยะเทียบ/เกณฑ์")
print(f"   ของจริงตอนนี้ = BARS {rc.ADX_DIR_BARS} · EPS {rc.ADX_DIR_EPS}  (ตอนนี้โดน 0 ไม้)")
print(f"\n   {'':6s}" + "".join(f"EPS {e:<8g}" for e in (0.5, 1.0, 1.5)))
for b in (3, 4, 5, 6, 8):
    cells = []
    for eps in (0.5, 1.0, 1.5):
        blocked = sc[sc[f"d{b}"] <= -eps]
        cells.append(f"{len(blocked):2d} ไม้ {blocked.R.sum():+6.2f}R")
    print(f"   BARS {b:<2d}" + "  ".join(cells))

print("\n5 ไม้ที่ใหญ่สุดของกลุ่ม 'ทรง':")
print(flat.reindex(flat.R.abs().sort_values(ascending=False).index)
          .head(5)[["symbol", "time", "adx", "diff", "R"]].round(2).to_string(index=False))
