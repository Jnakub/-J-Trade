"""backtest_blocked_value.py — ค่าเสียโอกาสของการถือช่องไว้ (ไม้ที่ระบบไม่ได้เปิดเพราะช่องไม่ว่าง)

รับไฟล์ replay_blocked_<symbol>.csv ที่ backtest_replay.py --log-blocked สร้างไว้ (ทุกรอบสแกน
ที่ผ่านด่านครบทุกด่านแล้วแต่ช่องถือไม้ไม่ว่าง) แล้วเดินไม้เงาพวกนั้นด้วยเครื่องจำลองตัวเดียว
กับที่ใช้ทั้งโปรเจกต์ — ได้คำตอบตรง ๆ ว่า **"ถ้ามีช่องที่สองจะได้กี่ไม้ กี่ R"**

ทำไมต้องมี: ทุกกฎที่ "ออกเร็วขึ้น" (slow trade, trend invalidation, กฎปิดบางส่วน) มีเหตุผล
ข้อเดียวกันคือคืนช่องให้ไม้ถัดไป แต่ไม่มีเครื่องมือไหนในโปรเจกต์เคยวัดว่าช่องนั้นมีค่าเท่าไหร่
— replay รายตัวข้ามรอบที่ช่องไม่ว่าง *ก่อน* คิดสัญญาณ จึงไม่เคยรู้ว่ารอบพวกนั้นมีของหรือเปล่า

การต่อคิว: ไม้เงาที่เกิดระหว่างที่ไม้เงาตัวก่อนยังถืออยู่ **ถูกข้าม** เหมือนช่องจริง ไม่งั้นจะได้
ไม้ทับซ้อนกันเป็นสิบและตัวเลขเฟ้อ

⚠️ สิ่งที่ตัวเลขนี้ไม่ใช่:
  - ไม่ใช่ "กำไรที่จะได้ถ้าเปิดช่องที่สอง" — การถือ 2 ไม้พร้อมกันต่อ symbol = เสี่ยง 2 เท่า
    ต้องคิด sizing ใหม่ ไม่ใช่บวก R ตรง ๆ (ดู comment ที่ config.SLOT_PER_STRATEGY)
  - 🔴 **ไม่ใช่คำตอบของ "ถือไม้นานขึ้นทำให้เสียโอกาสเท่าไหร่"** — โหมด default ต่อคิวจากสัญญาณ
    แรกสุด ซึ่งมักเกิดในชั่วโมงแรกหลังไม้จริงเปิด ไม้เงาตัวนั้นก็ถือยาวต่อไปแล้วกลืนสัญญาณ
    ช่วงหลังไปด้วย ตัวเลข "ไม้เงาที่เกิดหลัง 3 วัน" จากโหมดนี้จึงต่ำกว่าความจริงมาก
    (วัดจริง 2026-09-14: ต่อคิวได้ 7 ไม้ แต่ข้อมูลดิบมี 136 ช่วงสัญญาณ)
    ถามคำถามนั้นด้วย --min-age=72 ซึ่งเริ่มต่อคิวจากสัญญาณที่ช่องถูกครองมาแล้วเกิน N ชม.
    = จำลองว่า "ถ้าไม้แรกออกตอนชั่วโมงที่ N พอดี ช่องที่ว่างจะได้อะไร"

ผลที่วัดไว้ (7 symbol 730 วัน, base หลังปิดกฎ 1R/halfway/trend):
  default (ช่องที่สอง)  172 ไม้ +30.36R — แต่ 86% เปิดใน 4 ชม.แรก = เข้าซ้ำ move เดิม
  --min-age=72          62 ไม้  +3.79R — บวก 3/7 symbol ตัด 2 ไม้ใหญ่สุดออกเหลือ −2.01R
                        (XAU +5.58 · ETH +5.38 · EUR +1.20 · USDJPY −0.37 · US500 −2.05 ·
                         GBP −2.30 · BTC −3.65)

ใช้: ./run_wine.sh backtest_blocked_value.py BTCUSDm [--min-age=72]
"""
import sys

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from mt5_connect import connect
from backtest_trade_sim import TradeSim

symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
_d = next((a for a in sys.argv if a.startswith("--days=")), None)
DAYS = int(_d.split("=", 1)[1]) if _d else 730
_t = next((a for a in sys.argv if a.startswith("--tag=")), None)
TAG = _t.split("=", 1)[1] if _t else ""
# --min-age=N : นับเฉพาะสัญญาณที่ช่องถูกครองมาแล้วเกิน N ชม. (ดู 🔴 ใน docstring)
_a = next((a for a in sys.argv if a.startswith("--min-age=")), None)
MIN_AGE = float(_a.split("=", 1)[1]) if _a else 0.0

b = pd.read_csv(f"replay_blocked_{symbol}{TAG}.csv", parse_dates=["time", "ไม้ที่ครองช่องอยู่"])
b = b.sort_values("time").reset_index(drop=True)
if MIN_AGE:
    b["_age"] = (b.time - b["ไม้ที่ครองช่องอยู่"]).dt.total_seconds() / 3600
    _before = len(b)
    b = b[b._age > MIN_AGE].reset_index(drop=True)
    print(f"{symbol}: --min-age={MIN_AGE:g} ชม. -> เหลือ {len(b)} รอบ จาก {_before}", flush=True)
print(f"{symbol}: สัญญาณเงา {len(b)} รอบ "
      f"({b.strategy.value_counts().to_dict()})", flush=True)

connect()
sim = TradeSim(symbol, days=DAYS)

rows, busy_until = [], None
for _, s in b.iterrows():
    if busy_until is not None and s.time < busy_until:
        continue                                   # ช่องที่สองยังไม่ว่าง — ข้ามเหมือนของจริง
    r = sim.run(s.direction, s.entry, s.sl0, s.tp0, s.pinned_swing,
                s.pinned_atr_entry, s.time, s.strategy)
    if r is None:
        continue
    busy_until = r["exit_time"]
    rows.append({"time": s.time, "direction": s.direction, "strategy": s.strategy,
                 "entry": s.entry, "R": r["R"], "how": r["how"], "MFE": r["MFE"],
                 "exit_time": r["exit_time"],
                 "ชม.ที่ช่องถูกครองมาแล้ว": round(
                     (s.time - s["ไม้ที่ครองช่องอยู่"]).total_seconds() / 3600, 1)})

mt5.shutdown()
d = pd.DataFrame(rows)
d.to_csv(f"blocked_value_{symbol}{'_age'+str(int(MIN_AGE)) if MIN_AGE else ''}.csv", index=False)

print(f"\n=== {symbol} — ค่าของช่อง" + (f" (เริ่มนับหลังช่องถูกครอง {MIN_AGE:g} ชม.)" if MIN_AGE else "ที่สอง") + " ===")
if not len(d):
    print("  ไม่มีไม้เงาที่เดินได้เลย")
    sys.exit()
print(f"  ไม้เงาที่ได้จริงหลังต่อคิว {len(d)} ไม้ (จาก {len(b)} รอบที่เข้าเงื่อนไข)")
print(f"  Total R {d.R.sum():+.2f}   WR {(d.R>0).mean()*100:.1f}%   AvgR {d.R.mean():+.3f}")
for st, g in d.groupby("strategy"):
    print(f"    {st:<9} {len(g)} ไม้  {g.R.sum():+.2f}R  WR {(g.R>0).mean()*100:.0f}%")
print(f"  วิธีออก: {d.how.value_counts().to_dict()}")
print(f"  ช่องถูกครองมาแล้วตอนสัญญาณเกิด: มัธยฐาน {d['ชม.ที่ช่องถูกครองมาแล้ว'].median():.0f} ชม. "
      f"(ควอไทล์ {d['ชม.ที่ช่องถูกครองมาแล้ว'].quantile(.25):.0f}-{d['ชม.ที่ช่องถูกครองมาแล้ว'].quantile(.75):.0f})")
_e = d[d["ชม.ที่ช่องถูกครองมาแล้ว"] <= 72]
print(f"  เฉพาะที่ช่องถูกครองมา <= 72 ชม. (ช่วงที่กฎ slow trade เอื้อมถึง): "
      f"{len(_e)} ไม้ {_e.R.sum():+.2f}R")
