"""
entry_features.py — คัดกรอง "ตัวแปร ณ ตอนเข้าไม้" ว่ามีอะไรแยกไม้ที่ไม่เคยไปไหน
                    (MFE < 0.5R · 55 ไม้ · −46.67R) ออกจากไม้ที่ทำงานได้บ้างไหม

ทำไมต้องมี (2026-09-18): แบ่งไม้ตาม "เคยขึ้นไปถึงไหน" แทนวิธีจบ ได้ภาพที่ชัดกว่ามาก
  ไม่เคยถึง +0.5R   55 ไม้  −46.67R  WR 0%     <- ก้อนนี้ใหญ่กว่ากำไรสุทธิทั้งระบบ (+32.43R)
  0.5-1.0R          18 ไม้  −10.92R  WR 6%
  ถึง +1R ขึ้นไป    97 ไม้  +90.02R  WR 79%
72% ของไม้ที่จบ SL ไม่เคยแตะ +0.5R = **กฎ exit ช่วยไม่ได้ ต้องไม่เข้าตั้งแต่แรก**
และตัวแปรทั้ง 12 ตัวที่ replay บันทึกไว้แล้ว (สกอร์การ์ด/score/ระยะ SL/ทิศ) **ไม่มีตัวไหน
แยกได้เลย** (dead rate 30-35% ทุกช่อง · OBV 1D กับ MACD 4H ดูมีสัญญาณแต่กลับด้าน ซึ่งเคย
บันทึกไว้แล้วใน memory scorecard-has-no-discriminative-power)

ไฟล์นี้จึงไปหาตัวแปร **ตระกูลที่ยังไม่เคยลอง** — "เรขาคณิตของจุดเข้า" ไม่ใช่ "อินดิเคเตอร์
เห็นตรงกันกี่ตัว" เหตุผล: ด่านเดียวที่เคยได้ผลจริงคือ MAX_RUNUP_24H_R (−1.81R -> +3.20R)
ซึ่งวัด "ราคาวิ่งไปแล้วแค่ไหนก่อนเข้า" = ตระกูลนี้

⚠️ ไม่ต้องรัน replay ใหม่ — ทุกตัวคำนวณย้อนหลังได้จาก replay_trades_*.csv + แท่งราคา
   (pinned_atr_entry / pinned_swing มีอยู่ในไฟล์แล้ว ที่เหลือดึงจากแท่งที่ปิดก่อนเวลาเข้าไม้)
   ประหยัดไป ~1.5 ชม. เทียบกับการเพิ่มคอลัมน์ใน replay แล้วรันใหม่ทั้งชุด

🔴 เกณฑ์ตัดสิน (คำนวณจาก base 170 ไม้): ตัดไม้ dead ได้ +0.848R · ตัดไม้ good เสีย −0.928R
   -> ด่านต้องตัด **dead:good >= 1.09:1** ถึงจะเสมอตัว ขณะที่อัตราส่วนฐานคือ 0.57:1
   = ต้องเก่งกว่าการสุ่ม **1.93 เท่า**  ตัวไหนไม่ถึงเส้นนี้ไม่ต้องเอาไปรัน replay

ใช้:  ./run_wine.sh entry_features.py          (ต้องมี mfe_trades.csv จาก backtest_mfe.py ก่อน)
"""
import sys

import numpy as np
import pandas as pd

from mt5_connect import connect
from config import MT5_TIMEFRAMES, SYMBOLS
from scoring import get_ohlcv, get_ohlcv_real
import regime_check as rc

DEAD_MFE = 0.5      # "ไม่เคยไปไหน" = MFE ต่ำกว่านี้
GOOD_MFE = 1.0      # "ทำงานได้" = MFE ตั้งแต่นี้ขึ้นไป (ช่วงกลางถือเป็นกลาง ไม่นับทั้งสองฝั่ง)
RUNUP_BARS = 24     # หน้าต่างเดียวกับ config.MAX_RUNUP_24H_R

mfe = pd.read_csv("mfe_trades.csv", parse_dates=["time", "exit_time"])
connect()

rows = []
for symbol in SYMBOLS:
    tr = mfe[mfe.symbol == symbol]
    if tr.empty:
        continue
    span_h = int((pd.Timestamp.now() - tr.time.min()).total_seconds() / 3600) + 500
    h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=span_h).reset_index(drop=True)
    t1 = h1["time"]

    for t in tr.itertuples():
        risk = abs(t.entry - t.sl0)
        if not risk:
            continue
        # ── แท่ง 1H ที่ปิดแล้วทั้งหมดก่อนเวลาเข้าไม้ (กัน lookahead) ──
        prev = h1[t1 < t.time]
        if len(prev) < RUNUP_BARS + 1:
            continue
        win = prev.tail(RUNUP_BARS)
        px = float(prev["close"].iloc[-1])

        # ราคาวิ่งไปทางที่เราจะเข้าแล้วกี่ R ใน 24 ชม. (ตระกูลเดียวกับ MAX_RUNUP_24H_R)
        if t.direction == "Long":
            runup = (px - float(win["low"].min())) / risk
        else:
            runup = (float(win["high"].max()) - px) / risk
        # ช่วงแกว่งของ 24 ชม. ล่าสุด เทียบ R — ไม้บรรทัด "ตลาดมีแรงพอไหม"
        span24 = (float(win["high"].max()) - float(win["low"].min())) / risk

        # Key Level ที่ใกล้ที่สุด ณ เวลานั้น (ใช้ฟังก์ชันตัวเดียวกับ regime_check ไม่ hand-roll)
        try:
            df4 = get_ohlcv_real(symbol, "4H", bars=rc.KEY_LEVEL_BARS, as_of=t.time)
            kl = rc.check_key_level(symbol, px, df=df4)
            key_pct = float(kl.get("distance_pct") or np.nan)
        except Exception:
            key_pct = np.nan

        rows.append(dict(
            symbol=symbol, time=t.time, strategy=t.strategy, direction=t.direction,
            R=t.R, MFE=t.MFE,
            hour=t.time.hour,
            atr_pct=t.pinned_atr_entry / t.entry * 100,          # ATR 1H ตอนเข้า เทียบราคา
            atr_per_R=t.pinned_atr_entry / risk,                 # 1R = กี่ ATR (ยิ่งน้อย SL ยิ่งแคบเทียบ noise)
            swing_dist_R=abs(t.entry - t.pinned_swing) / risk,   # swing ที่วาง SL ห่างแค่ไหน (R)
            sl_pct=risk / t.entry * 100,
            tp_dist_atr=abs(t.tp0 - t.entry) / t.pinned_atr_entry,
            runup_R=runup, span24_R=span24, key_pct=key_pct,
        ))

df = pd.DataFrame(rows)
df["dead"] = df.MFE < DEAD_MFE
df["good"] = df.MFE >= GOOD_MFE
n_dead, n_good = df.dead.sum(), df.good.sum()
r_dead, r_good = df[df.dead].R.mean(), df[df.good].R.mean()
bar = -r_good / r_dead                       # dead:good ที่ต้องได้ถึงจะเสมอตัว
base_ratio = n_dead / n_good
print(f"\nไม้ที่คำนวณได้ {len(df)} ไม้ · dead {n_dead} ({df.dead.mean()*100:.0f}%) · good {n_good}")
print(f"เกณฑ์: ต้องตัด dead:good >= {bar:.2f}:1 · ฐาน {base_ratio:.2f}:1 "
      f"-> ต้องเก่งกว่าสุ่ม {bar/base_ratio:.2f} เท่า\n")

FEATURES = ["runup_R", "span24_R", "key_pct", "atr_pct", "atr_per_R",
            "swing_dist_R", "sl_pct", "tp_dist_atr", "hour"]


def cut_report(name, s, frac):
    """ถ้าตัดไม้ frac% ที่ค่าสูงสุด / ต่ำสุดของตัวแปรนี้ทิ้ง จะได้อัตราส่วน dead:good เท่าไหร่"""
    d = df[[*("dead", "good", "R")]].assign(v=s).dropna(subset=["v"])
    k = max(int(len(d) * frac), 5)
    out = []
    for lab, sub in (("ตัดฝั่งสูง", d.nlargest(k, "v")), ("ตัดฝั่งต่ำ", d.nsmallest(k, "v"))):
        nd, ng = sub.dead.sum(), sub.good.sum()
        ratio = nd / ng if ng else float("inf")
        out.append((lab, len(sub), nd, ng, ratio, -sub.R.sum()))
    return out


print(f"{'ตัวแปร':<14}{'ทิศที่ตัด':<12}{'ตัด':>5}{'dead':>6}{'good':>6}{'dead:good':>11}{'ΔR ถ้าตัด':>11}")
print("-" * 68)
best = []
for f in FEATURES:
    for lab, n, nd, ng, ratio, dR in cut_report(f, df[f], 0.25):
        flag = " ✅" if ratio >= bar else ""
        print(f"{f:<14}{lab:<12}{n:>5}{nd:>6}{ng:>6}{ratio:>11.2f}{dR:>+11.2f}{flag}")
        best.append((ratio, dR, f, lab, n, nd, ng))
    print()

best.sort(reverse=True)
print("เรียงตาม dead:good (ตัด 25% บน/ล่าง):")
for ratio, dR, f, lab, n, nd, ng in best[:5]:
    print(f"  {f:<14}{lab:<12} {nd}/{ng} = {ratio:.2f}  ΔR {dR:+.2f}R")
print(f"\n⚠️ คัดกรอง {len(FEATURES)} ตัวแปร x 2 ทิศ = {len(FEATURES)*2} การทดสอบ — ตัวที่ดูดีที่สุด")
print("   เป็นผลของการเลือกเองเสมอ ต้อง permutation test ก่อนเชื่อ (ดูเคส Reversal-FX ที่ได้")
print("   |t| 2.51 แต่ p = 0.606) และตัวที่ผ่านต้องยืนยันด้วย backtest_replay เต็มอีกชั้น")
df.to_csv("entry_features.csv", index=False)
print("\nเขียน entry_features.csv แล้ว")
