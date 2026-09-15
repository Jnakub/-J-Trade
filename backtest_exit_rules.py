"""backtest_exit_rules.py — คัดกรองกฎ exit ของ exit_monitor หลายชุดในรอบเดียว

เดินไม้ชุดเดิมจาก replay_trades_<symbol><base>.csv ซ้ำหลายรอบ รอบละค่าคงที่ชุดหนึ่ง แล้วเทียบกับ
control — ใช้ได้เพราะกฎ exit ไม่เปลี่ยนชุดไม้ (ดู backtest_trade_sim docstring สำหรับข้อจำกัด
เรื่องช่องถือไม้ ซึ่งเป็นเหตุผลที่ตัวชนะต้องยืนยันด้วย backtest_replay เต็ม)

⚠️ `--base` ต้องชี้ไปที่ไฟล์ที่สร้างด้วยค่าคงที่ **ชุดเดียวกับที่ระบบใช้อยู่ตอนนี้** ไม่งั้น
control จะไม่ตรงกับไฟล์และเทียบอะไรไม่ได้ — บรรทัด control บอกให้เองว่าตรงกี่ไม้

ทำไมต้องวัดชุดรวมคู่กับการปิดทีละตัวเสมอ: analyze_position เอาทุกกฎที่ยิงมา min() หา keep ต่ำสุด
ปิดกฎเดียวกฎที่เหลือรับช่วงตัดแทนทันที การวัดทีละตัวจึงประเมินค่าของกฎต่ำกว่าจริงอย่างเป็นระบบ
(BTC: ปิดทีละตัวรวมกัน +0.33R แต่ปิดพร้อมกัน +1.86R)

ใช้: ./run_wine.sh backtest_exit_rules.py BTCUSDm [--set=sizing|trend] [--base=_tag]

ชุด sizing (กฎปิดบางส่วน) — วัดแล้ว 2026-09-13 บน base ก่อนแก้ 7 symbol 202 ไม้:
  hot100 +0.57R (บวก 3/7 = noise) · 1r100 +1.91R (5/7) · hw100 +1.12R (5/7) ·
  ปิดทั้งสาม +7.73R (6/7 แต่ไม้ Scoring แย่ลง 69 ดีขึ้น 48)
  -> ยืนยันด้วย replay เต็มแล้วเอา 1r100+hw100 เข้าระบบจริง (+4.92R, commit 234700a)
  RULE_HOT_KEEP ไม่แตะเพราะเป็น noise

ชุด trend (กฎ trend invalidation — กฎเดียวในระบบที่ตัดได้ถึง 100% และมีผลเฉพาะ Scoring)
"""
import sys

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from mt5_connect import connect
import exit_monitor as em
from backtest_trade_sim import TradeSim, load_trades, control_check

symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
_d = next((a for a in sys.argv if a.startswith("--days=")), None)
DAYS = int(_d.split("=", 1)[1]) if _d else 730
_s = next((a for a in sys.argv if a.startswith("--set=")), None)
SET = _s.split("=", 1)[1] if _s else "sizing"
_b = next((a for a in sys.argv if a.startswith("--base=")), None)
BASE_TAG = _b.split("=", 1)[1] if _b else ""

LIVE_SIZING = {"RULE_1R_KEEP": em.RULE_1R_KEEP, "RULE_HOT_KEEP": em.RULE_HOT_KEEP,
               "RULE_HALFWAY_KEEP": em.RULE_HALFWAY_KEEP}
LIVE_TREND = {"TREND_CHECK_KEEP_BY_CONSEC": dict(em.TREND_CHECK_KEEP_BY_CONSEC)}
LIVE_SLOW = {"SLOW_TRADE_DAYS": em.SLOW_TRADE_DAYS, "SLOW_TRADE_R": em.SLOW_TRADE_R,
             "SLOW_TRADE_KEEP": em.SLOW_TRADE_KEEP}

SETS = {
    # ปิดกฎปิดบางส่วนทีละตัวและพร้อมกัน (100 = ไม่ตัดเลย)
    "sizing": {
        "control":    LIVE_SIZING,
        "hot100":     {**LIVE_SIZING, "RULE_HOT_KEEP": 100},
        "1r100":      {**LIVE_SIZING, "RULE_1R_KEEP": 100},
        "hw100":      {**LIVE_SIZING, "RULE_HALFWAY_KEEP": 100},
        "ปิดทั้งสาม": {"RULE_1R_KEEP": 100, "RULE_HOT_KEEP": 100, "RULE_HALFWAY_KEEP": 100},
    },
    # กฎ trend invalidation: {Daily ปิดสวนติดกันกี่แท่ง -> เหลือกี่ %}  ของจริง {1:75, 2:50, 3:0}
    "trend": {
        "control":       LIVE_TREND,
        "ปิดทั้งกฎ":      {"TREND_CHECK_KEEP_BY_CONSEC": {1: 100, 2: 100, 3: 100}},
        "ตัดขาระดับ100": {"TREND_CHECK_KEEP_BY_CONSEC": {1: 75, 2: 50, 3: 100}},
        "เหลือแต่ระดับ100": {"TREND_CHECK_KEEP_BY_CONSEC": {1: 100, 2: 100, 3: 0}},
    },
    # กฎ slow trade: ถือครบ SLOW_TRADE_DAYS วันแล้วยังไม่ถึง SLOW_TRADE_R -> base_keep = 50%
    # ⚠️ ตัว 50% **hardcode อยู่ในโค้ด** (analyze_position: elif slow_trade: base_keep_pct = 50)
    # ไม่มีค่าคงที่ให้ทับ จึงทดสอบได้แค่ "เมื่อไหร่กฎยิง" ไม่ใช่ "ยิงแล้วตัดเท่าไหร่"
    # ปิดกฎ = ตั้ง SLOW_TRADE_DAYS สูงจนไม้ทุกตัวปิดไปก่อน (ไม่ต้องแตะ production)
    "slow": {
        "control": LIVE_SLOW,
        "ปิดกฎ":   {"SLOW_TRADE_DAYS": 9999},
        "5 วัน":   {"SLOW_TRADE_DAYS": 5},
        "7 วัน":   {"SLOW_TRADE_DAYS": 7},
        "R0.2":    {**LIVE_SLOW, "SLOW_TRADE_R": 0.2},
    },
    # ที่มา: ไม้ Scoring ถือจริงมัธยฐาน 5.2 วัน เฉลี่ย 7.6 วัน และ 71% ถือเกิน 3 วัน — เกณฑ์ 3 วัน
    # จึงยิงใส่ไม้ปกติ 60% ของทั้งหมด ไม่ใช่ "ไม้ที่ตายแล้ว" ตามเจตนาเดิม  ชุดนี้ถามว่าถ้าเลื่อน
    # ให้พ้นจังหวะธรรมชาติของระบบไปเลย มันกลับมาเป็นตาข่ายที่มีประโยชน์ไหม
    "slow2": {
        "control":  LIVE_SLOW,
        "10 วัน":   {"SLOW_TRADE_DAYS": 10},
        "14 วัน":   {"SLOW_TRADE_DAYS": 14},
        "ปิดกฎ":    {"SLOW_TRADE_DAYS": 9999},
    },
    # ตัดเท่าไหร่ตอนกฎยิง (ของจริง 50) — keep=0 คือ **กฎคนละตัว** เพราะปิดไม้หมดแล้วช่องว่างจริง
    # ⚠️ เครื่องจำลองไม่มีช่อง จึงเห็นแต่ "สิ่งที่เสียไปจากการปิดไม้" ไม่เห็น "สิ่งที่ช่องว่างได้มา"
    #    ค่าของช่องวัดแยกไว้แล้วที่ backtest_blocked_value --min-age=72 (+3.79R / 2 ปี / 7 symbol
    #    ตัด 2 ไม้ใหญ่สุดเหลือ −2.01R) เอามาบวกเองตอนสรุป
    "slowkeep": {
        "control":  LIVE_SLOW,
        "ปิด 100%": {**LIVE_SLOW, "SLOW_TRADE_KEEP": 0},
    },
    # ปิด 100% แต่เลื่อนวันออกไป — รวมสองแกนที่เคยวัดแยกกัน (วัน x ขนาดที่ตัด)
    # เหตุผล: ปิดเต็มไม้ที่ 3 วันแพ้เพราะไปตัดไม้ปกติ (71% ของไม้ถือเกิน 3 วัน) ถ้าเลื่อนให้พ้น
    # จังหวะธรรมชาติของระบบ (มัธยฐาน 5.2 วัน เฉลี่ย 7.6) มันจะกลายเป็น "ตัดไม้ที่ตายจริง" ไหม
    # ทำให้กฎ "แม่นขึ้น" แทนที่จะเอาออก — แกนที่สามที่ไม่เคยแตะ: ยิงใส่ไม้แบบไหน
    # ที่มา: ไม้ที่กฎยิงใส่ AvgR +0.118 เทียบไม้ที่ไม่โดน +0.201 = จับถูกทางแต่จาง เพราะเกณฑ์
    # "ต่ำกว่า 0.5R" เหมารวมไม้ที่ติดลบจริง (ควรตัด) กับไม้ที่ได้ +0.3R แล้วค่อย ๆ ไป (ไม่ควรตัด)
    "slowaim": {
        "control":       LIVE_SLOW,
        "R<0":           {**LIVE_SLOW, "SLOW_TRADE_R": 0.0},
        "เหลือ75%":      {**LIVE_SLOW, "SLOW_TRADE_KEEP": 75},
        "R<0+เหลือ75%":  {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 75},
    },
    # ช่องสุดท้ายของตาราง: ตัดหนักขึ้นแต่เฉพาะไม้ที่ติดลบจริง — ต่างจาก "ปิด 100%" ที่ R<0.5
    # (ได้ −6.49R) ตรงที่ไม่ไปฆ่าไม้ที่กำไรอยู่แต่ไปช้า  และควร **ลด** ไม้ขาดทุนเต็ม 1R ไม่ใช่เพิ่ม
    "slowaim2": {
        "control":        LIVE_SLOW,
        "R<0+ปิด100%":    {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 0},
        "R<0+เหลือ25%":   {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 25},
    },
    # กวาดแกน "วัน" ใหม่ บนกฎที่เล็งแม่นแล้ว (ตัดเฉพาะไม้ติดลบ ตัดทั้งไม้)
    # ⚠️ การกวาดวันรอบก่อน ๆ ทำบนกฎที่เล็งมั่ว (ตัดทั้งไม้ที่ติดลบและไม้ที่กำไรช้า) ผลจึงบอกได้
    # แค่ "ยิงน้อยลงดีกว่า" ซึ่งจริงเสมอสำหรับกฎที่เล็งมั่ว ไม่ว่าเส้นวันจะอยู่ตรงไหน
    # รอบนี้ถามคำถามที่มีคำตอบจริง: ไม้ที่ติดลบอยู่ ควรให้เวลากี่วันก่อนตัดทิ้ง
    "slowaim3": {
        "control":   LIVE_SLOW,
        "R<0@3วัน":  {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 3},
        "R<0@5วัน":  {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 5},
        "R<0@7วัน":  {**LIVE_SLOW, "SLOW_TRADE_R": 0.0, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 7},
    },
    # เพดานการขยาย SL (exit_monitor.MAX_SL_WIDEN_R = 1.5) — ตัวเดียวในระบบที่ยอมให้ขาดทุนจริง
    # เกิน RISK_PER_TRADE ได้ เพราะ lot ถูกคิดจากระยะ SL ตอนเปิดไม้แล้วไม่เปลี่ยนอีก
    # ⚠️ ข้อมูลก่อนกวาด: ไม้แย่สุดใน 730 วัน 7 symbol = **−1.107R** (gross) = เพดาน 1.5 ไม่เคย
    # ถูกแตะเลยสักครั้ง ระดับ 1.5/1.3/1.2 จึงคาดว่าเป็น no-op เป๊ะ ใส่ไว้เพื่อ **ยืนยันว่าเป็น
    # no-op จริง** ไม่ใช่เพื่อหาผู้ชนะ — ตัวที่กัดจริงคือ 1.0 (ผูก SL ไม่ให้กว้างกว่าตอนเข้า)
    # ซึ่งไปลบ "การหายใจตาม ATR" ที่เป็นเจตนาของดีไซน์เดิมทิ้ง (ดู comment ที่ MAX_SL_WIDEN_R)
    # ต้นทุนของมันจึงไม่ได้อยู่ในไม้ที่จบ SL แต่อยู่ในไม้ที่เคยโดนเขี่ยแล้วรอดกลับมาชนะ
    "widen": {
        "control": {"MAX_SL_WIDEN_R": em.MAX_SL_WIDEN_R},
        "1.3":     {"MAX_SL_WIDEN_R": 1.3},
        "1.2":     {"MAX_SL_WIDEN_R": 1.2},
        "1.1":     {"MAX_SL_WIDEN_R": 1.1},
        "1.0":     {"MAX_SL_WIDEN_R": 1.0},
    },
    "slowkeep2": {
        "control":     LIVE_SLOW,
        "100%@5วัน":   {**LIVE_SLOW, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 5},
        "100%@7วัน":   {**LIVE_SLOW, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 7},
        "100%@10วัน":  {**LIVE_SLOW, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 10},
        "100%@14วัน":  {**LIVE_SLOW, "SLOW_TRADE_KEEP": 0, "SLOW_TRADE_DAYS": 14},
    },
}
if SET not in SETS:
    sys.exit(f"--set: เลือกได้ {list(SETS)}")
VARIANTS = SETS[SET]
LIVE = VARIANTS["control"]


def apply(cfg):
    for k, v in cfg.items():
        setattr(em, k, v)


connect()
sim = TradeSim(symbol, days=DAYS)
base = load_trades(symbol, tag=BASE_TAG)
print(f"{symbol}: {len(base)} ไม้ ({(base.strategy=='Scoring').sum()} Scoring) "
      f"x {len(VARIANTS)} รอบ  [set={SET}  base='{BASE_TAG or 'ค่าเริ่มต้น'}']", flush=True)

rows = []
for k, b in base.iterrows():
    rec = {"time": b.time, "strategy": b.strategy, "direction": b.direction,
           "R_file": b.R, "how_file": b.how}
    for name, cfg in VARIANTS.items():
        apply(cfg)
        s = sim.run_row(b)
        rec[f"R_{name}"] = s["R"] if s else None
        rec[f"how_{name}"] = s["how"] if s else None
        if name == "control" and s:
            rec["MFE"] = s["MFE"]
    apply(LIVE)
    rows.append(rec)
    if k % 10 == 0:
        print(f"  ... {k+1}/{len(base)}", flush=True)

mt5.shutdown()
d = pd.DataFrame(rows)
d.to_csv(f"exit_rules_{SET}_{symbol}.csv", index=False)

print(f"\n=== {symbol} [{SET}] ===")
print(control_check(d))
for name in VARIANTS:
    if name == "control":
        continue
    col = f"R_{name}"
    m = d[d[col].notna() & d.R_control.notna()]
    delta = m[col] - m.R_control
    sc = m[m.strategy == "Scoring"]
    print(f"{name:<16} ทั้งพอร์ต {delta.sum():+.2f}R  "
          f"(Scoring {(sc[col]-sc.R_control).sum():+.2f}R จาก {len(sc)} ไม้)  "
          f"ไม้ที่ผลเปลี่ยน {int(delta.abs().gt(0.01).sum())}  "
          f"ดีขึ้น {int(delta.gt(0.01).sum())} / แย่ลง {int(delta.lt(-0.01).sum())}")

w = d[d.R_control > 0.5]
if len(w):
    print(f"ช่องว่าง MFE: ไม้กำไร >0.5R {len(w)} ไม้ เคยขึ้นถึง {w.MFE.mean():.2f}R "
          f"เก็บได้ {w.R_control.mean():.2f}R = {w.R_control.mean()/w.MFE.mean()*100:.0f}%")
