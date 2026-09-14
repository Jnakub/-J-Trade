"""
backtest_mfe.py — วัด "กำไรสูงสุดที่เคยมีแล้วคืนกลับไป" ของไม้ที่ replay เปิดไปแล้ว

ทำไมต้องมีแยก: replay_trades_*.csv บันทึกแต่ผลลัพธ์สุดท้าย (R, how) ไม่มีอะไรบอกเลยว่าไม้นั้น
ระหว่างทางเคยขึ้นไปเท่าไหร่ ไม้ที่จบ BE/SL จึงถูกอ่านเหมือนกันหมดทั้งที่ "ไม่เคยไปไหนเลย" กับ
"เคยขึ้น 3R แล้วถอยกลับมาโดน BE" เป็นคนละปัญหาคนละทางแก้ — ตัวหลังคือกำไรที่ระบบจับได้แล้ว
ปล่อยหลุดมือ ซึ่งกฎ exit ต้องรับผิดชอบ ไม่ใช่ด่านเข้าไม้

ไม่รันกลยุทธ์ซ้ำ ไม่แตะ replay เลย — อ่านชุดไม้เดิม แล้วเดินราคา 1H ตามช่วงที่ถือไม้จริง
(bar.time ใน [entry, exit) ตรงกับที่ step_position เห็น) เพื่อคำนวณ:
  MFE = (ราคาที่ดีที่สุดระหว่างถือ - entry) / risk    [R]
  MAE = (ราคาที่แย่ที่สุดระหว่างถือ - entry) / risk    [R, ติดลบ]
โดย risk = |entry - sl0| ตัวเดียวกับที่ replay ใช้เป็นฐาน 1R

หมายเหตุ MFE เป็นค่า "ก่อนต้นทุน" (replay หัก spread ออกจาก R ตอนปิด) ส่วนต่างระดับ 0.01-0.05R
เทียบกันได้ไม่ต้องแก้ และ MFE นับจากราคาเต็มไม้เสมอ ไม่ลดตามการปิดบางส่วน = "ถ้าถือเต็มไม้ไว้
เฉยๆ จะเคยได้เท่าไหร่" ซึ่งเป็นเพดานที่กฎ exit ควรถูกวัดเทียบ

ใช้:  ./run_wine.sh backtest_mfe.py                 # ทุก symbol ใน config.SYMBOLS
      ./run_wine.sh backtest_mfe.py BTCUSDm XAUUSDm # เฉพาะบางตัว
      ./run_wine.sh backtest_mfe.py --tag=_slotper  # อ่านไฟล์ผลรอบที่ติด tag แทนรอบปกติ
"""
import os
import sys

import pandas as pd

from mt5_connect import connect
from config import MT5_TIMEFRAMES, SYMBOLS
from scoring import get_ohlcv

TAG = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tag=")), "")
_args = [a for a in sys.argv[1:] if not a.startswith("--")]

# ระดับ MFE ที่ใช้ตัดว่า "ไม้นี้เคยไปถึงจริง" — 1.0R คือจุดที่ไม้คุ้มความเสี่ยงพอดี และเป็นจุดที่
# exit_monitor บังคับ SL ไป breakeven พอดี (ดู desired_sl ท้าย exit_monitor.py) ระดับนี้จึงเป็น
# เส้นที่ตอบตรงๆ ว่า "กฎ BE กินไม้ไปกี่ไม้"
LEVELS = (1.0, 1.5, 2.0, 3.0)


def files_for(symbols):
    """default = config.SYMBOLS เท่านั้น ไม่ใช่ glob ทุกไฟล์ที่เจอ — replay_trades_*.csv ของ symbol
    ที่ถูกถอดออกจากระบบไปแล้ว (เช่น XRPUSDm) ยังค้างอยู่ในโฟลเดอร์ และเป็นไฟล์เก่าคนละรอบโค้ด
    การ glob จึงเอาไม้ของระบบที่ไม่มีอยู่จริงมาปนกับผลปัจจุบันเงียบๆ"""
    return [(s, f"replay_trades_{s}{TAG}.csv") for s in (symbols or SYMBOLS)]


def excursions(df, trades):
    """เดินราคา 1H ของทุกไม้ในตารางเดียว — คืน MFE/MAE เป็น R ต่อไม้"""
    times = df["time"].values
    mfe, mae = [], []
    for tr in trades.itertuples():
        risk = abs(tr.entry - tr.sl0)
        # ช่วงที่ไม้ถืออยู่จริง: replay เปิดไม้ที่ปลายแท่ง (entry = เวลาเปิดของแท่งถัดไปพอดี)
        # และปิดที่ปลายแท่งสุดท้ายที่มันเห็น = แท่ง time ใน [entry, exit)
        seg = df[(times >= tr.time.to_datetime64()) & (times < tr.exit_time.to_datetime64())]
        if seg.empty or not risk:
            mfe.append(float("nan")); mae.append(float("nan"))
            continue
        if tr.direction == "Long":
            mfe.append((seg["high"].max() - tr.entry) / risk)
            mae.append((seg["low"].min() - tr.entry) / risk)
        else:
            mfe.append((tr.entry - seg["low"].min()) / risk)
            mae.append((tr.entry - seg["high"].max()) / risk)
    return mfe, mae


connect()
frames = []
for symbol, path in files_for(_args):
    if not os.path.exists(path):
        print(f"  ข้าม {symbol}: ไม่มี {path}")
        continue
    tr = pd.read_csv(path, parse_dates=["time", "exit_time"])
    # ดึงแท่งให้คลุมไม้ที่เก่าที่สุด (+headroom) — replay ใช้ days*24+500 ด้วยเหตุผลเดียวกัน
    span_h = int((tr["exit_time"].max() - tr["time"].min()).total_seconds() / 3600) + 500
    h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=span_h)
    tr["symbol"] = symbol
    tr["MFE"], tr["MAE"] = excursions(h1, tr)
    frames.append(tr)
    print(f"  {symbol:<10} {len(tr):>3} ไม้   แท่ง 1H {h1['time'].iloc[0]} -> {h1['time'].iloc[-1]}")

if not frames:
    sys.exit("ไม่เจอไฟล์ replay_trades_*.csv เลย — รัน backtest_replay.py ก่อน")

t = pd.concat(frames, ignore_index=True)
miss = int(t["MFE"].isna().sum())
t = t.dropna(subset=["MFE"])

# "คืนกลับ" มีความหมายเฉพาะไม้ที่เคยมีกำไรให้คืน — ไม้ที่ไม่เคยขึ้นเลยแล้วโดน SL ค่า MFE-R ของมัน
# คือ "ขาดทุนที่วางแผนไว้" ไม่ใช่รูรั่ว การเอามารวมทำให้ตัวเลขพองขึ้นเป็นเท่าตัวโดยไม่ชี้อะไรเลย
# (SL 83 ไม้ MFE เฉลี่ย 0.38R รวมกันได้ 110R ซึ่งเกือบทั้งหมดคือ -1R ของมันเอง)
t["คืนกลับ"] = (t["MFE"] - t["R"]).where(t["MFE"] >= 1.0, 0.0)

win = t[t["R"] > 0]
print(f"\n{'=' * 78}")
print(f"  MFE ของไม้ที่ replay เปิดจริง {len(t)} ไม้"
      f"{f'   (วัดไม่ได้ {miss} ไม้ — ไม่มีแท่งคลุม)' if miss else ''}")
print(f"  {'-' * 74}")
print(f"  MFE เฉลี่ย  : {t['MFE'].mean():+.2f}R      R จริงเฉลี่ย: {t['R'].mean():+.2f}R"
      f"      Total {t['R'].sum():+.1f}R")
print(f"  ไม้ที่ชนะเก็บได้ {win['R'].sum() / win['MFE'].sum() * 100:.0f}% ของกำไรสูงสุดที่เคยมี"
      f"   ({win['R'].sum():+.1f}R จากเพดาน {win['MFE'].sum():+.1f}R, {len(win)} ไม้)")

# แกนหลัก: ไม้ที่ "เคยขึ้นไปถึง X" แล้วเอากลับบ้านไม่ได้ = รูรั่วที่กฎ exit ต้องรับผิดชอบ
print(f"  {'-' * 74}")
print("  ไม้ที่เคยขึ้นถึง X แล้วเอากลับบ้านไม่ได้:")
print(f"    {'ระดับ':<8}{'ถึง':>6}{'คืนกลับรวม':>13}{'จบ<=0':>9}{'R ที่หลุดมือ':>15}")
for lv in LEVELS:
    hit = t[t["MFE"] >= lv]
    lost = hit[hit["R"] <= 0]
    if len(hit):
        print(f"    {lv:<8.1f}{len(hit):>6}{(hit['MFE'] - hit['R']).sum():>12.1f}R{len(lost):>9}"
              f"{(lost['MFE'] - lost['R']).sum():>14.1f}R")

print(f"  {'-' * 74}")
print(f"  {'จบแบบ':<14}{'ไม้':>5}{'MFE เฉลี่ย':>12}{'R เฉลี่ย':>10}{'TotalR':>9}{'คืนกลับ':>11}")
for how, g in sorted(t.groupby("how"), key=lambda x: -x[1]["คืนกลับ"].sum()):
    print(f"  {str(how)[:13]:<14}{len(g):>5}{g['MFE'].mean():>+11.2f}R{g['R'].mean():>+9.2f}R"
          f"{g['R'].sum():>+9.1f}{g['คืนกลับ'].sum():>10.1f}R")

print(f"  {'-' * 74}")
print(f"  {'symbol':<14}{'ไม้':>5}{'MFE เฉลี่ย':>12}{'R เฉลี่ย':>10}{'TotalR':>9}{'คืนกลับ':>11}")
for sym, g in sorted(t.groupby("symbol"), key=lambda x: -x[1]["คืนกลับ"].sum()):
    print(f"  {sym:<14}{len(g):>5}{g['MFE'].mean():>+11.2f}R{g['R'].mean():>+9.2f}R"
          f"{g['R'].sum():>+9.1f}{g['คืนกลับ'].sum():>10.1f}R")

# รายไม้ที่รั่วหนักสุด — ไว้เปิดดูทีละไม้ว่ากฎไหนตัด/SL ขยับตอนไหน
worst = t[t["R"] <= 0].nlargest(15, "MFE")
print(f"  {'-' * 74}")
print(f"  15 ไม้ที่เคยขึ้นสูงสุดแล้วจบไม่ได้กำไร:")
print(f"    {'symbol':<10}{'เข้า':<18}{'กลยุทธ์':<11}{'MFE':>7}{'R':>7}{'  จบแบบ'}")
for r in worst.itertuples():
    print(f"    {r.symbol:<10}{str(r.time)[:16]:<18}{r.strategy:<11}"
          f"{r.MFE:>+6.2f}R{r.R:>+6.2f}R  {r.how}")
print(f"{'=' * 78}")

out = f"mfe_trades{TAG}.csv"
t.to_csv(out, index=False)
print(f"  เขียนไม้ทั้งหมดพร้อม MFE/MAE ลง {out}")
