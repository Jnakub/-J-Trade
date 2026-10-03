"""
tp_headroom.py — ไม้ที่ "ชน TP แล้วปิด" ราคาวิ่งต่อไปอีกเท่าไหร่ (เพดานของการคลาย TP)

ทำไมต้องมี: 2026-09-17 ทำ trailing จริง (TRAIL_FROM_EXTREME) แล้วผลแย่ลง −14.95R
สาเหตุอ่านออกจากตารางทางออก — TP หายไป 33 ไม้จาก 56 คือ trailing ไปเขี่ยไม้ที่กำลังจะถึง TP
ทิ้งก่อน (ดู memory mfe-leak-is-the-breakeven-rule) ข้อสรุปตอนนั้นคือ "ถ้าจะรื้อ ต้องแตะ TP ก่อน"
แต่ก่อนจะจ่าย 1.5 ชม./ค่า ไปรัน replay เต็ม (เปลี่ยน TP = เปลี่ยน R:R = เปลี่ยนว่าไม้ไหนผ่าน
MIN_RR_HARD_BLOCK = เปลี่ยนชุดไม้ ลัดด้วย exit_rules ไม่ได้ ดู CLAUDE.md ข้อ 4) ต้องรู้ก่อนว่า
**มีที่ให้วิ่งจริงไหม** — ถ้าไม้ที่ชน TP ไม่ไปไหนต่อ การคลาย TP ก็ไม่มีอะไรให้เก็บ

ตอบคำถามเดียว: ไม้ที่จบด้วย TP ถ้าไม่ปิดตรงนั้น ราคาวิ่งต่อไปทางเราได้อีกกี่ R ภายในเวลา
ที่ไม้ยังถือได้ (เหลือถึง MAX_HOLD_DAYS นับจากเวลาเข้าไม้)

วัด 2 ค่าต่อไม้ — ทั้งคู่คูณด้วย `rem` (ส่วนที่ยังเหลือตอนชน TP) แล้ว เพราะไม้พวกนี้
ปิดบางส่วนไปก่อนแล้ว (median rem = 0.75) กำไรส่วนที่ booked ไปแล้วไม่ได้วิ่งต่อด้วย:
  headroom = MFE หลัง TP − ระยะ TP  → **เพดาน** ถ้าจับจังหวะออกได้สมบูรณ์แบบ (ทำไม่ได้จริง)
  trail    = เดินต่อด้วย trailing (extreme ∓ MULT×ATR14 1H + ratchet ไม่ถอย) สูตรเดียวกับ
             exit_monitor.TRAIL_FROM_EXTREME → ของที่ **เก็บได้จริง** ถ้ายกเลิก TP แล้วใช้ trailing

⚠️ ทั้งคู่เป็น "ขอบบนของรางวัล" ไม่ใช่ ΔR ของระบบ — ไม้ที่ถือต่อจะครองช่องนานขึ้น ไม้ถัดไป
   หายไป และไม่หัก spread รอบสอง ตัวเลขจริงต้อง replay เต็มเท่านั้น ที่นี่ใช้ตัดสินแค่ว่า
   "คุ้มจะไปรัน replay ไหม"

ใช้:  ./run_wine.sh tp_headroom.py            # MULT=2.0 (ค่าที่เคยทดลอง)
      ./run_wine.sh tp_headroom.py --mult=3
"""
import os
import sys

import pandas as pd

from mt5_connect import connect
from config import MT5_TIMEFRAMES, SYMBOLS
from scoring import get_ohlcv
from indicators import calc_atr
from exit_monitor import MAX_HOLD_DAYS, TRAIL_ATR_PERIOD

MULT = float(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--mult=")), 2.0))
_args = [a for a in sys.argv[1:] if not a.startswith("--")]

connect()
out, skipped = [], 0
for symbol in (_args or SYMBOLS):
    path = f"replay_trades_{symbol}.csv"
    if not os.path.exists(path):
        print(f"  ข้าม {symbol}: ไม่มี {path}")
        continue
    tr = pd.read_csv(path, parse_dates=["time", "exit_time"])
    span_h = int((tr["exit_time"].max() - tr["time"].min()).total_seconds() / 3600) + 500
    h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=span_h + 24 * MAX_HOLD_DAYS)
    h1 = h1.reset_index(drop=True)
    h1["atr"] = calc_atr(h1, TRAIL_ATR_PERIOD)
    times = h1["time"].values

    for t in tr.itertuples():
        risk = abs(t.entry - t.sl0)
        if not risk:
            continue
        # ไม่เชื่อ label — ยืนยัน "ชน TP" ด้วยตัวเลข: R ที่บันทึกต้องอธิบายได้ครบด้วย
        # (กำไรที่ปิดไปก่อนแล้ว + ส่วนที่เหลือคูณระยะ TP) เหตุผล: CLAUDE.md เตือนว่า
        # เครื่องมือวัดของโปรเจกต์นี้พังเพราะผูกกับสตริงที่ย้ายบ้านเงียบๆ มาแล้ว 2 รอบ
        r_tp = abs(t.tp - t.entry) / risk       # ระยะ TP ที่ใช้จริงตอนปิด เป็น R (ก่อน spread)
        if abs(t.R - (t.booked + t.rem * r_tp)) > 0.10:
            continue
        seg = h1[(times > t.exit_time.to_datetime64()) &
                 (times < (t.time + pd.Timedelta(days=MAX_HOLD_DAYS)).to_datetime64())]
        if seg.empty:
            skipped += 1
            continue

        if t.direction == "Long":
            fav = (seg["high"].max() - t.entry) / risk
        else:
            fav = (t.entry - seg["low"].min()) / risk
        headroom = t.rem * (fav - r_tp)

        # เดินต่อด้วย trailing — ratchet เดียวกับ exit_monitor (SL ถอยหลังไม่ได้)
        # ตั้งต้นที่ SL ที่มีผลอยู่ตอนชน TP (t.sl) = หลังกฎ BE ทำงานแล้ว
        ext = t.tp
        stop = t.sl
        r_cont = r_tp
        for b in seg.itertuples():
            if pd.isna(b.atr):
                continue
            if t.direction == "Long":
                ext = max(ext, b.high)
                stop = max(stop, ext - MULT * b.atr)
                if b.low <= stop:
                    r_cont = (stop - t.entry) / risk
                    break
                r_cont = (b.close - t.entry) / risk
            else:
                ext = min(ext, b.low)
                stop = min(stop, ext + MULT * b.atr)
                if b.high >= stop:
                    r_cont = (t.entry - stop) / risk
                    break
                r_cont = (t.entry - b.close) / risk
        d_trail = t.rem * (r_cont - r_tp)

        out.append(dict(symbol=symbol, time=t.time, direction=t.direction, R=t.R, rem=t.rem,
                        r_tp=r_tp, headroom=headroom, d_trail=d_trail, bars=len(seg)))

df = pd.DataFrame(out)
if df.empty:
    print("ไม่พบไม้ที่จบด้วย TP"); sys.exit()

print(f"\nไม้ที่จบด้วย TP (ยืนยันด้วยตัวเลข): {len(df)} ไม้  ข้ามเพราะไม่มีแท่งต่อ {skipped} ไม้")
print(f"MULT={MULT:g} · เพดานเวลา {MAX_HOLD_DAYS} วันนับจากเข้าไม้ · "
      f"ระยะ TP เฉลี่ย {df['r_tp'].mean():+.2f}R · rem เฉลี่ย {df['rem'].mean():.2f}\n")
print(f"headroom (เพดานถ้าออกจุดสูงสุดพอดี): รวม {df['headroom'].sum():+.1f}R · "
      f"median {df['headroom'].median():+.2f}R · ไม้ที่วิ่งต่อ ≥0.5R: {(df['headroom'] >= 0.5).sum()}/{len(df)}")
print(f"trail   (ของที่เก็บได้จริง)          : ΔR รวม {df['d_trail'].sum():+.1f}R · "
      f"median {df['d_trail'].median():+.2f}R · ดีขึ้น {(df['d_trail'] > 0.01).sum()} ไม้ / "
      f"แย่ลง {(df['d_trail'] < -0.01).sum()} ไม้")

# เส้น noise — ชุดไม้เดิมทั้งหมด (paired) จึงไม่มี churn เลย มีแต่ direct:
# SE = sd(Δ ต่อไม้) × √(จำนวนไม้ที่เปลี่ยนจริง) ตาม CLAUDE.md ข้อ 2
chg = df[df["d_trail"].abs() > 0.01]
se = chg["d_trail"].std() * (len(chg) ** 0.5)
print(f"\ndirect: เปลี่ยน {len(chg)} ไม้ · sd/ไม้ {chg['d_trail'].std():.2f} · SE {se:.2f}R · "
      f"|t| = {abs(df['d_trail'].sum() / se):.2f}")

print("\nรายsymbol:")
g = df.groupby("symbol").agg(n=("R", "size"), headroom=("headroom", "sum"), d_trail=("d_trail", "sum"))
print(g.round(2).to_string())
print(f"symbol ที่บวก: {(g['d_trail'] > 0).sum()}/{len(g)}")

cols = ["symbol", "time", "direction", "rem", "r_tp", "headroom", "d_trail"]
print("\n5 ไม้ที่วิ่งต่อไกลสุด:")
print(df.nlargest(5, "headroom")[cols].round(2).to_string(index=False))
print("\n5 ไม้ที่ trailing ให้ผลแย่สุด:")
print(df.nsmallest(5, "d_trail")[cols].round(2).to_string(index=False))
print(f"\nตัด 5 ไม้ใหญ่สุดของ d_trail ออก: {df['d_trail'].sum() - df.nlargest(5, 'd_trail')['d_trail'].sum():+.2f}R")
