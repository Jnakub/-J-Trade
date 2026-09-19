"""news_sensitivity.py — "กฎข่าว 2 ตัวที่ backtest ไม่เคยจำลอง มันขยับ R ได้แค่ไหน"

ปัญหา: `analyze_position` ปิด news guard ทิ้งเมื่อมี `as_of` (คืน news_simulated=False)
เพราะ ForexFactory ให้แค่ปฏิทิน**สัปดาห์ปัจจุบัน** (`ff_calendar_thisweek.json`) ไม่มีย้อนหลัง
=> ทุกตัวเลข backtest ของโปรเจกต์นี้ไม่นับกฎ exit 2 ตัวที่ระบบจริงใช้อยู่:
  A. checklist ข้อ 6  — มีข่าว High/USD ออก "หลังเข้าไม้" ภายใน NEWS_POST_H (6) ชม.
                        และตอนนั้นยังไม่กำไร -> ปิด 100%
  B. Position Sizing ข้อ 5 — ข่าว High/USD จะออกภายใน NEWS_IMMINENT_H (1) ชม. -> เหลือ 50%

ไฟล์นี้ **ไม่พยายามหาปฏิทินย้อนหลัง** (นั่นคืองานเป็นวัน) แต่ตอบคำถามที่ใช้ตัดสินใจได้จริงกว่า:
**"ต่อให้ไม่รู้ว่าข่าวออกวันไหน กฎพวกนี้มีโอกาสขยับ R ได้มากแค่ไหน"**
วิธี: โปรยหน้าต่างข่าวแบบสุ่มที่อัตราจริง (กวาด 2-10 ครั้ง/เดือน) ลงบนเส้นทางราคารายชั่วโมง
ของไม้ทั้ง 166 ไม้ แล้วทำซ้ำหลายร้อยรอบ -> ได้การกระจายของ ΔR
ถ้าการกระจายแคบรอบศูนย์ = ปิดแกนนี้ได้ถาวรโดยไม่ต้องหาข้อมูล · ถ้ากว้าง = ต้องไปหาปฏิทินจริง

⚠️ ข้อจำกัดที่ต้องรู้:
  - ใช้ `booked`/`rem` ตามที่ไฟล์ replay บันทึกไว้ = สมมติว่า partial ที่เกิดขึ้นจริงยังเกิดเหมือนเดิม
    ถ้ากฎข่าวยิงก่อนที่ partial นั้นจะเกิด ของจริงจะต่างไป (รายงานจำนวนไม้ที่มี cuts > 0 ไว้ให้ดู)
  - ไม่จำลองด่านห้าม "เข้า" ไม้ใหม่ตอนใกล้ข่าว (scheduler 4b) — นั่นเปลี่ยนชุดไม้ ต้อง replay เต็ม
  - หน้าต่างข่าวสุ่มแบบกระจายเท่ากันในวันทำการ ไม่ได้อิงเวลาประกาศจริง (13:30 UTC ฯลฯ)
    -> มีโหมด us_session ให้เทียบว่าการวางตำแหน่งมีผลไหม

🔴 ผลรอบแรก 2026-09-19 (base 166 ไม้ +40.85R · หน้าต่าง 23.7 เดือน · 400 รอบ/ค่า):
   **ทั้งสองกฎขาดทุน และขาดทุนที่ทุกอัตราที่กวาด** — ตำแหน่งที่วางข่าว (uniform vs
   ตรึงเวลาประกาศสหรัฐ) ไม่มีผลเลย = ไม่ต้องไปหาเวลาประกาศจริงให้เสียเวลา
     ข่าว 2/เดือน  ΔR -3.5   ·  4 -> -6.0  ·  6 -> -7.4  ·  8 -> -9.5  ·  10 -> -10.3
   ที่อัตราจริง ~5 ครั้ง/เดือน: **-6.95R = 17% ของกำไรทั้งระบบ** (p5 -15.7 · p95 +0.9)
     กฎ A เดี่ยว -4.33R (sd 5.18)  ·  กฎ B เดี่ยว -4.57R (sd 2.93)  ·  รวม -6.95R
   ทำไมถึงแพ้ทั้งที่ A ดูเหมือนคัดถูกทาง: A โดนไม้เสีย 26.8% ไม้ดี 17.4% (dead:good 3.1
   เทียบฐาน 2.0 = คัดถูกทางจริง) **แต่มันปิดกลางทาง ไม่ใช่ไม่เข้าตั้งแต่แรก** — ตอนกฎยิง
   ไม้เสียขาดทุนไปเกือบเต็มแล้ว ประหยัดได้นิดเดียว ส่วนไม้ดีที่โดนฆ่าเสียทั้งขาขึ้น
   => เกณฑ์ dead:good 1.09 ของ entry_features **ใช้กับกฎ exit ไม่ได้** มันคิดจากการ
      "ไม่เข้าไม้ตั้งแต่แรก" (ตัดไม้ dead ได้เต็ม +0.848R) ซึ่งกฎกลางทางทำไม่ได้
   ส่วนกฎ B แย่แบบสม่ำเสมอกว่า (sd ต่ำสุด): มันหั่นครึ่ง **51.8% ของไม้ดี** แล้วครึ่งนั้น
   หายถาวร (position_rules ใช้ min() เติมกลับไม่ได้) = ภาษีสุ่มบนระบบที่ expectancy เป็นบวก
⚠️ กติกาข้อ 3: 5 ไม้ใหญ่สุดกินไป -6.00R จาก -6.95R (ตัดออกเหลือ -0.95R) ทั้ง 5 เป็นไม้ชนะ
   ก้อนโต (+3.02 ถึง +4.18R) = การกระจุกนี้เป็นเรื่องเชิงกลไก (กฎที่หั่น position ย่อมเจ็บ
   ที่ไม้ชนะใหญ่ที่สุดเสมอ) ไม่ใช่เรื่องบังเอิญ — แต่แปลว่า **ผลจริงบนปฏิทินจริงใบเดียว
   แกว่งได้มาก** ตามที่ p5/p95 บอก
⚠️ และนี่คือ **ขอบบนของความเสียหาย** (กติกาข้อ 3c): ชุดไม้คงที่ = ปิดไม้เร็วขึ้นแล้วช่องว่าง
   ที่คืนมาไม่ถูกนับว่ามีไม้ใหม่เข้ามาแทน ของจริงจะเบากว่านี้เท่าไหร่ ต้อง replay เต็มถึงจะรู้

🔴 ทางเลือกแก้กฎ B (วัด 2026-09-19 · ยังไม่ได้เอาเข้าระบบ รอผู้ใช้ตัดสิน):
     ของเดิม เหลือ 50% ทุกไม้           -6.95R
     เหลือ 75% ทุกไม้                    -5.64R   (+1.31)
     เหลือ 50% เฉพาะไม้ที่ยังไม่กำไร     -4.43R   (+2.53)
     ปิดกฎ B ทิ้ง                        -4.33R   (+2.62)
   "เฉพาะไม้ที่ยังไม่กำไร" ได้ 97% ของผลที่ปิดทิ้งเลย โดยยังเหลือกฎไว้ในเคสที่เสี่ยงจริง
🔑 **แล้วกฎ B กันอะไรอยู่จริงๆ — วัดแล้วได้ว่า "ไม่ได้กันอะไรเลย"**
   เหตุผลที่กฎนี้มีอยู่คือกลัวราคากระโดดข้าม SL ตอนข่าวออก แต่วัด 21,374 แท่ง-ชั่วโมงที่ระบบ
   ถือไม้อยู่จริงตอนตลาดเปิดต่อเนื่อง: **gap > 0.25R เกิด 0 ครั้ง · gap ใหญ่ที่สุด 0.19R**
   gap ที่เกิน 0.5R มี 15 ครั้งและ **ทุกครั้งมีตลาดปิดคั่น** (สุดสัปดาห์) ซึ่งกฎ B ที่อิง
   "1 ชม.ก่อนข่าว" กันไม่ได้อยู่แล้ว · ส่วนแท่งที่วิ่งเร็วเกิน 1R ในชั่วโมงเดียวมี 0.20%
   แต่ SL ก็ยังปิดที่ -1R ตามเดิมเพราะไม่มี gap => **ความเสี่ยงถูกกั้นด้วย SL อยู่แล้ว**
   การหั่นครึ่งก่อนข่าวจึงแค่ทำให้ไม้เสียได้ -0.5R แทน -1R และไม้ดีได้ครึ่งเดียว
   ซึ่งบนระบบที่ expectancy เป็นบวก = ขาดทุนล้วน
   ⚠️ สิ่งเดียวที่วัดไม่ได้คือ **slippage ของโบรกตอนข่าว** (OHLC ไม่บอก) — ไม้จริงที่โดน SL
      มีแค่ 2 ไม้ n น้อยเกินจะดู

ใช้:  ./run_wine.sh news_sensitivity.py
      ต้องมี news_paths.csv จาก ./run_wine.sh news_paths.py ก่อน
      (ตัวมันเองอ่าน CSV อย่างเดียว ไม่แตะ MT5 — แต่ต้องผ่าน wine เพราะ `from config import
       SYMBOLS` ลากเอา MetaTrader5 มาด้วย และ config คือแหล่งเดียวของ SYMBOLS ห้าม glob
       ไฟล์ replay_trades_*.csv แทน — ของ symbol ที่ถอดออกแล้วยังค้างอยู่)
"""
import numpy as np
import pandas as pd

from config import SYMBOLS

POST_H     = 6      # exit_monitor.NEWS_POST_H     — ข่าวสงบภายในกี่ชม.
IMMINENT_H = 1      # exit_monitor.NEWS_IMMINENT_H — ใกล้ข่าวภายในกี่ชม.
KEEP       = 0.50   # exit_monitor.NEWS_IMMINENT_KEEP / 100
RATES      = [2, 4, 6, 8, 10]      # หน้าต่างข่าว High/USD ต่อเดือน (ค่าจริงน่าจะ 4-6)
DRAWS      = 400


def to_hours(x) -> np.ndarray:
    """datetime -> ชั่วโมงตั้งแต่ epoch — **บังคับหน่วยเป็นวินาทีก่อนเสมอ**

    🔴 2026-09-19: เวอร์ชันแรกใช้ `.astype("int64") / (3600*1e9)` โดยเดาว่า pandas ให้ ns
    แต่ `read_csv(parse_dates=...)` ให้ **datetime64[us]** -> สเกลผิดไป 1000 เท่า หน้าต่าง
    2 ปีกลายเป็น 0.02 เดือน ทุกอัตราในชุดกวาดเลยได้ข่าว 1 ครั้งเท่ากันหมด
    **แล้วตารางออกมาหน้าตาปกติทุกช่อง** ไม่มี error สักตัว (ตรงกับที่ CLAUDE.md เตือนไว้ว่า
    ความเสี่ยงจริงของโปรเจกต์นี้คือตัวเลขที่ผิดแบบเงียบๆ) — และ ENTRY ที่ใช้ Timestamp.value
    ซึ่งเป็น ns เสมอ ก็เลยอยู่คนละหน่วยกับ T โดยไม่มีใครรู้"""
    return np.asarray(x, dtype="datetime64[s]").astype("int64") / 3600.0

paths = pd.read_csv("news_paths.csv", parse_dates=["entry_time", "t"])
trades = []
for sym in SYMBOLS:
    tr = pd.read_csv(f"replay_trades_{sym}.csv", parse_dates=["time", "exit_time"])
    tr["symbol"], tr["trade"] = sym, range(len(tr))
    trades.append(tr)
trades = pd.concat(trades, ignore_index=True)

# เตรียมข้อมูลต่อไม้เป็น array ล้วน — วนลูป python ต่อชั่วโมงจะช้าเกินไป (166 x 135 x 400 x 5)
T, R_END, BOOKED, REM, ENTRY, KEY = [], [], [], [], [], []
for (sym, idx), g in paths.groupby(["symbol", "trade"], sort=False):
    row = trades[(trades.symbol == sym) & (trades.trade == idx)].iloc[0]
    g = g.sort_values("t")
    T.append(to_hours(g.t.values))
    R_END.append(g.r.values)
    BOOKED.append(float(row.booked))
    REM.append(float(row.rem))
    ENTRY.append(float(to_hours(np.datetime64(row.time))))
    KEY.append((sym, idx))
BOOKED, REM, ENTRY = np.array(BOOKED), np.array(REM), np.array(ENTRY)
R_ACTUAL = trades.R.values
# r ที่ส่วนที่เหลือได้จริงตอนจบ — ถอดกลับจาก R ที่บันทึกไว้
R_FINAL = np.where(REM > 0, (R_ACTUAL - BOOKED) / np.where(REM > 0, REM, 1), 0.0)

lo = min(t[0] for t in T)
hi = max(t[-1] for t in T)
span_months = (hi - lo) / 24 / 30.44
n_cuts = int((trades.cuts.fillna(0) > 0).sum())
print(f"ไม้ {len(T)} ไม้ · หน้าต่าง {span_months:.1f} เดือน · TotalR ฐาน {R_ACTUAL.sum():+.2f}R")
print(f"ไม้ที่มี partial มาก่อน {n_cuts}/{len(T)} — ไม้กลุ่มนี้คือข้อจำกัดที่เขียนไว้ใน docstring\n")


def draw_events(rng, rate, mode):
    """คืนเวลาข่าว (ชม.ตั้งแต่ epoch) เรียงแล้ว — วันทำการเท่านั้น"""
    n = max(int(round(rate * span_months)), 1)
    ev = rng.uniform(lo, hi, n * 3)
    dow = ((ev / 24) + 4) % 7                 # epoch = วันพฤหัส -> 0=พฤ
    ev = ev[(dow < 5) | (dow >= 6)]           # ตัดเสาร์-อาทิตย์แบบหยาบ
    if mode == "us_session":
        ev = ev - (ev % 24) + 13.5            # ตรึงไปเวลาประกาศข้อมูลสหรัฐ
    return np.sort(ev[:n])


def simulate(ev, use_a=True, use_b=True, tally=None, per_trade=False,
             b_keep=KEEP, b_only_if_losing=False):
    """คืน ΔR รวม บนหน้าต่างข่าวชุดนี้ — เปิด/ปิดกฎแต่ละตัวได้เพื่อดูว่าตัวไหนเป็นตัวการ
    tally = dict ไว้เก็บสถิติว่าไม้ที่โดนแต่ละกฎ เป็นไม้แบบไหน (ดี/ตาย)
    per_trade=True = คืน array รายไม้แทนผลรวม (ใช้หา 5 ไม้ใหญ่สุดตามกติกาข้อ 3)"""
    tot = 0.0
    each = np.zeros(len(T))
    for i in range(len(T)):
        t, r = T[i], R_END[i]
        rem, f = REM[i], REM[i]
        if rem <= 0:
            continue
        j = np.searchsorted(ev, t, side="right") - 1        # ข่าวล่าสุดที่ <= ชั่วโมงนี้
        ruleA = (j >= 0) & (t - ev[np.clip(j, 0, None)] <= POST_H) & \
                (ev[np.clip(j, 0, None)] >= ENTRY[i]) & (r <= 0)
        k = np.searchsorted(ev, t, side="right")            # ข่าวถัดไปที่ > ชั่วโมงนี้
        ruleB = (k < len(ev)) & (ev[np.clip(k, 0, len(ev) - 1)] - t <= IMMINENT_H)
        if b_only_if_losing:
            ruleB = ruleB & (r <= 0)
        iA = int(np.argmax(ruleA)) if (use_a and ruleA.any()) else len(t)
        iB = int(np.argmax(ruleB)) if (use_b and ruleB.any()) else len(t)
        realized = 0.0
        if iB < iA:                       # โดนหั่นครึ่งก่อน แล้วค่อยโดนปิดทิ้ง
            realized += f * (1 - b_keep) * r[iB]
            f *= b_keep
        if iA < len(t):
            realized += f * r[iA]
            f = 0.0
        realized += f * R_FINAL[i]        # ส่วนที่รอดไปจบตามเดิม
        each[i] = realized - rem * R_FINAL[i]
        tot += each[i]
        if tally is not None:
            good = R_FINAL[i] >= 1.0
            if iA < len(t): tally["A_good" if good else "A_dead"] += 1
            if iB < len(t): tally["B_good" if good else "B_dead"] += 1
    return each if per_trade else tot


print(f"{'ข่าว/เดือน':>10}{'วาง':>12}{'ΔR mean':>10}{'sd':>8}{'p5':>8}{'p50':>8}{'p95':>8}"
      f"{'แย่สุด':>9}{'ดีสุด':>8}")
print("-" * 82)
for mode in ("uniform", "us_session"):
    for rate in RATES:
        rng = np.random.default_rng(0)
        d = np.array([simulate(draw_events(rng, rate, mode)) for _ in range(DRAWS)])
        print(f"{rate:>10}{mode:>12}{d.mean():>+10.2f}{d.std():>8.2f}{np.percentile(d,5):>+8.2f}"
              f"{np.percentile(d,50):>+8.2f}{np.percentile(d,95):>+8.2f}{d.min():>+9.2f}{d.max():>+8.2f}")
    print()

# ── กฎไหนเป็นตัวการ + ไม้ที่โดนเป็นไม้แบบไหน ────────────────────────────────────
print("แยกทีละกฎ ที่อัตราจริง 5 ครั้ง/เดือน (uniform)\n")
print(f"{'':<34}{'ΔR mean':>10}{'sd':>8}")
print("-" * 52)
for lab, a, b in (("A: ข่าวสงบแล้วไม่กำไร -> ปิด 100%", True, False),
                  ("B: ใกล้ข่าว 1 ชม. -> เหลือ 50%",   False, True),
                  ("A + B (ของจริง)",                   True, True)):
    rng = np.random.default_rng(0)
    d = np.array([simulate(draw_events(rng, 5, "uniform"), a, b) for _ in range(DRAWS)])
    print(f"{lab:<34}{d.mean():>+10.2f}{d.std():>8.2f}")

# กติกาข้อ 3 ของ CLAUDE.md — ดู 5 ไม้ใหญ่สุด กัน "ตัดสินจากไม้ไม่กี่ไม้"
rng = np.random.default_rng(0)
per = np.zeros(len(T))
for _ in range(DRAWS):
    per += simulate(draw_events(rng, 5, "uniform"), True, True, per_trade=True)
per /= DRAWS
big = np.argsort(np.abs(per))[-5:][::-1]
print(f"\n5 ไม้ที่โดนกระทบหนักสุด (ΔR เฉลี่ยต่อรอบ) รวม {per[big].sum():+.2f}R "
      f"จาก {per.sum():+.2f}R  ->  ตัดออกเหลือ {per.sum() - per[big].sum():+.2f}R")
for i in big:
    print(f"    {KEY[i][0]:<9} #{KEY[i][1]:<3} R จริง {R_ACTUAL[i]:>+6.2f}  ->  ΔR {per[i]:>+6.2f}")

# ── ทางเลือกในการแก้กฎ B (A คงไว้ทุกแถวเพื่อให้เทียบกันได้) ────────────────────
print("\nทางเลือกแก้กฎ B — ที่อัตราจริง 5 ครั้ง/เดือน (uniform) · A เปิดไว้ทุกแถว\n")
print(f"{'':<40}{'ΔR mean':>10}{'sd':>8}{'ดีขึ้นจากเดิม':>14}")
print("-" * 72)
BASE_B = None
for lab, kw in (
        ("ของเดิม: เหลือ 50% ทุกไม้",          dict()),
        ("เหลือ 75% ทุกไม้",                    dict(b_keep=0.75)),
        ("เหลือ 50% เฉพาะไม้ที่ยังไม่กำไร",     dict(b_only_if_losing=True)),
        ("ปิดกฎ B ทิ้ง (เหลือแต่ A)",           dict(use_b=False)),
):
    rng = np.random.default_rng(0)
    d = np.array([simulate(draw_events(rng, 5, "uniform"), **kw) for _ in range(DRAWS)])
    if BASE_B is None: BASE_B = d.mean()
    print(f"{lab:<40}{d.mean():>+10.2f}{d.std():>8.2f}{d.mean()-BASE_B:>+14.2f}")

tally = dict(A_good=0, A_dead=0, B_good=0, B_dead=0)
rng = np.random.default_rng(0)
for _ in range(DRAWS):
    simulate(draw_events(rng, 5, "uniform"), True, True, tally)
n_good = int((R_FINAL >= 1.0).sum()); n_dead = len(R_FINAL) - n_good
print(f"\nไม้ที่แต่ละกฎไปโดน (เฉลี่ยต่อรอบ · ฐานคือ ไม้ดี {n_good} / ไม้เสีย {n_dead}):")
for k in ("A", "B"):
    g, b = tally[f"{k}_good"] / DRAWS, tally[f"{k}_dead"] / DRAWS
    print(f"  กฎ {k}: โดนไม้ดี {g:>5.1f} ไม้ ({g/n_good*100:>4.1f}% ของไม้ดี) · "
          f"โดนไม้เสีย {b:>5.1f} ไม้ ({b/n_dead*100:>4.1f}% ของไม้เสีย)")
