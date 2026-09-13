"""backtest_exit_rules.py — คัดกรองกฎ Position Sizing ของ exit_monitor หลายตัวในรอบเดียว

เดินไม้ชุดเดิมจาก replay_trades_<symbol>.csv ซ้ำหลายรอบ รอบละค่า keep% ชุดหนึ่ง (100 = ปิดกฎ)
แล้วเทียบกับ control — ใช้ได้เพราะกฎ exit ไม่เปลี่ยนชุดไม้ (ดู backtest_trade_sim docstring
สำหรับข้อจำกัดเรื่องช่องถือไม้ ซึ่งเป็นเหตุผลที่ตัวชนะต้องยืนยันด้วย backtest_replay เต็ม)

ทำไมต้องวัด "ปิดทั้งสาม" คู่กับการปิดทีละตัวเสมอ: analyze_position เอาทุกกฎที่ยิงมา min() หา
keep ต่ำสุด ปิดกฎเดียวกฎที่เหลือรับช่วงตัดแทนทันที การวัดทีละตัวจึงประเมินค่าของกฎต่ำกว่าจริง
อย่างเป็นระบบ (BTC: ปิดทีละตัวรวมกัน +0.33R แต่ปิดพร้อมกัน +1.86R)

ใช้: ./run_wine.sh backtest_exit_rules.py BTCUSDm

ผลรอบแรก (2026-09-13, 7 symbol 202 ไม้ — control ตรงกับไฟล์ 202/202):
  hot100 +0.57R (บวก 3/7 symbol = noise) · 1r100 +1.91R (5/7) · hw100 +1.12R (5/7, ไม้เปลี่ยน
  16 ไม้ ดีขึ้น 14 แย่ลง 2) · ปิดทั้งสาม +7.73R (6/7 แต่ไม้ Scoring แย่ลง 69 ไม้ ดีขึ้น 48)
  แยกตามกลยุทธ์ (ปิดทั้งสาม): Scoring +3.15R/177 ไม้ · Reversal +4.60R/25 ไม้ = ต่อไม้ต่างกัน
  10 เท่า และฝั่ง Reversal บวก 7/7 symbol โดย 1r100/hw100 ไม่มีไม้ Reversal แย่ลงเลยสักไม้
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

LIVE = {"1r": em.RULE_1R_KEEP, "hot": em.RULE_HOT_KEEP, "hw": em.RULE_HALFWAY_KEEP}
VARIANTS = {
    "control":    dict(LIVE),
    "hot100":     {**LIVE, "hot": 100},
    "1r100":      {**LIVE, "1r": 100},
    "hw100":      {**LIVE, "hw": 100},
    "ปิดทั้งสาม": {"1r": 100, "hot": 100, "hw": 100},
}

connect()
sim = TradeSim(symbol, days=DAYS)
base = load_trades(symbol)
print(f"{symbol}: {len(base)} ไม้ ({(base.strategy=='Scoring').sum()} Scoring) "
      f"x {len(VARIANTS)} รอบ", flush=True)

rows = []
for k, b in base.iterrows():
    rec = {"time": b.time, "strategy": b.strategy, "direction": b.direction,
           "R_file": b.R, "how_file": b.how}
    for name, cfg in VARIANTS.items():
        em.RULE_1R_KEEP, em.RULE_HOT_KEEP, em.RULE_HALFWAY_KEEP = cfg["1r"], cfg["hot"], cfg["hw"]
        s = sim.run_row(b)
        rec[f"R_{name}"] = s["R"] if s else None
        rec[f"how_{name}"] = s["how"] if s else None
        if name == "control" and s:
            rec["MFE"] = s["MFE"]
    em.RULE_1R_KEEP, em.RULE_HOT_KEEP, em.RULE_HALFWAY_KEEP = LIVE["1r"], LIVE["hot"], LIVE["hw"]
    rows.append(rec)
    if k % 10 == 0:
        print(f"  ... {k+1}/{len(base)}", flush=True)

mt5.shutdown()
d = pd.DataFrame(rows)
d.to_csv(f"exit_rules_{symbol}.csv", index=False)

print(f"\n=== {symbol} ===")
print(control_check(d))
for name in VARIANTS:
    if name == "control":
        continue
    col = f"R_{name}"
    m = d[d[col].notna() & d.R_control.notna()]
    delta = m[col] - m.R_control
    sc = m[m.strategy == "Scoring"]
    print(f"{name:<12} ทั้งพอร์ต {delta.sum():+.2f}R  "
          f"(Scoring {(sc[col]-sc.R_control).sum():+.2f}R จาก {len(sc)} ไม้)  "
          f"ไม้ที่ผลเปลี่ยน {int(delta.abs().gt(0.01).sum())}  "
          f"ดีขึ้น {int(delta.gt(0.01).sum())} / แย่ลง {int(delta.lt(-0.01).sum())}")

w = d[d.R_control > 0.5]
if len(w):
    print(f"ช่องว่าง MFE: ไม้กำไร >0.5R {len(w)} ไม้ เคยขึ้นถึง {w.MFE.mean():.2f}R "
          f"เก็บได้ {w.R_control.mean():.2f}R = {w.R_control.mean()/w.MFE.mean()*100:.0f}%")
