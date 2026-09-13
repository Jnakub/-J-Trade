"""backtest_entry_lead.py — "ถ้าเข้าไม้เร็วขึ้น N ชม. จะดีขึ้นไหม" วัดแบบแยกตัวแปรเดียว

เอาไม้ Scoring ที่ระบบเข้าจริงทั้งชุดมาเลื่อนเวลาเข้าให้เร็วขึ้น โดย **คงทิศเดิม ไม่เพิ่มไม้ใหม่
แม้แต่ไม้เดียว** — SL/TP คิดใหม่ตามเวลานั้นจริงด้วย compute_score(as_of=t−N, force=True)
force=True ข้าม hard block R:R/SL ไว้ ไม่งั้นไม้ที่เข้าเร็วขึ้นบางไม้จะหายไป = เพิ่มตัวแปร

ใช้: ./run_wine.sh backtest_entry_lead.py BTCUSDm [--leads=4,8,12] [--days=730]

ผลรอบแรก (2026-09-13, 7 symbol 177 ไม้): ΔR −4.20 / −16.38 / −23.20 ที่ 4/8/12 ชม.
ข้อสรุปและกลไกอยู่ที่ config.MAX_RUNUP_24H_R — **สายนี้ปิดแล้ว** สคริปต์เก็บไว้เพื่อวัดซ้ำ
เมื่อโครงสร้าง TP เปลี่ยน (กลไกที่ทำให้แพ้คือ fib ยังไม่มีขาให้ฉายตอนเข้าเร็ว)
"""
import sys
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

import config
import scoring
from mt5_connect import connect
from config import MT5_TIMEFRAMES
from scoring import compute_score, get_ohlcv, merge_real_volume
from backtest_trade_sim import TradeSim, load_trades, control_check

symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
_l = next((a for a in sys.argv if a.startswith("--leads=")), None)
LEADS = [int(x) for x in (_l.split("=", 1)[1].split(",") if _l else ["4", "8", "12"])]
_d = next((a for a in sys.argv if a.startswith("--days=")), None)
DAYS = int(_d.split("=", 1)[1]) if _d else 730

connect()
sim = TradeSim(symbol, days=DAYS)
_df1d_cache = {}


def df1d_at(t):
    key = t.date()
    if key not in _df1d_cache:
        _df1d_cache.clear()
        _df1d_cache[key] = merge_real_volume(
            get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t), symbol, "1D", as_of=t)
    return _df1d_cache[key]


base = load_trades(symbol, "Scoring")
print(f"{symbol}: ไม้ Scoring {len(base)} ไม้  leads={LEADS} ชม.", flush=True)

rows = []
for k, b in base.iterrows():
    rec = {"time": b.time, "direction": b.direction, "R_file": b.R, "how_file": b.how}
    c = sim.run_row(b)
    rec["R_control"] = c["R"] if c else None
    rec["how_control"] = c["how"] if c else None
    for N in LEADS:
        t2 = b.time - timedelta(hours=N)
        i2 = sim.idx.get(t2 - timedelta(hours=1))
        if i2 is None:
            continue
        entry2 = float(sim.h1["close"].iloc[i2])
        try:
            _, _, _, si = compute_score(symbol, b.direction, entry2, as_of=t2,
                                        force=True, df_1d=df1d_at(t2))
        except Exception as exc:
            rec[f"R_lead{N}"] = None
            rec[f"err_lead{N}"] = f"{type(exc).__name__}: {exc}"[:80]
            continue
        sl2, tp2, atr2 = si["exec_sl"], si["tp"], si["atr_entry"]
        if config.TP_MAX_ATR and atr2:                 # เพดาน TP เดียวกับระบบจริง
            cap = atr2 * config.TP_MAX_ATR
            tp2 = min(tp2, entry2 + cap) if b.direction == "Long" else max(tp2, entry2 - cap)
        pin = (sl2 + scoring.EXEC_SL_ATR_MULT * atr2) if (atr2 and b.direction == "Long") \
            else ((sl2 - scoring.EXEC_SL_ATR_MULT * atr2) if atr2 else sl2)
        s = sim.run(b.direction, entry2, sl2, tp2, pin, atr2, t2, "Scoring")
        rec[f"R_lead{N}"] = s["R"] if s else None
        rec[f"how_lead{N}"] = s["how"] if s else None
        rec[f"rr_lead{N}"] = abs(tp2 - entry2) / abs(entry2 - sl2) if sl2 != entry2 else None
    rows.append(rec)
    if k % 5 == 0:
        print(f"  ... {k+1}/{len(base)}", flush=True)

mt5.shutdown()
d = pd.DataFrame(rows)
d.to_csv(f"entry_lead_{symbol}.csv", index=False)

print(f"\n=== {symbol} ===")
print(control_check(d))
for N in LEADS:
    col = f"R_lead{N}"
    if col not in d:
        continue
    m = d[d[col].notna() & d.R_control.notna()]
    if not len(m):
        continue
    print(f"เข้าเร็วขึ้น {N:>2} ชม.: {len(m)} ไม้  ΣR {m[col].sum():+.2f} เทียบ control "
          f"{m.R_control.sum():+.2f} = {m[col].sum()-m.R_control.sum():+.2f}R   "
          f"WR {(m[col]>0).mean()*100:.1f}% เทียบ {(m.R_control>0).mean()*100:.1f}%   "
          f"ดีขึ้น {int((m[col]>m.R_control+0.01).sum())} / แย่ลง {int((m[col]<m.R_control-0.01).sum())} ไม้")
    ec = f"err_lead{N}"
    if ec in d and d[ec].notna().any():
        print(f"     เข้าก่อนไม่ได้ {int(d[ec].notna().sum())} ไม้: "
              f"{d[ec].dropna().value_counts().head(3).to_dict()}")
    win, lose = m[m.R_control > 0.5], m[m.R_control <= 0.5]
    print(f"     ไม้ที่เดิมกำไร >0.5R ({len(win)} ไม้) {win[col].sum()-win.R_control.sum():+.2f}R  |  "
          f"ไม้ที่เหลือ ({len(lose)} ไม้) {lose[col].sum()-lose.R_control.sum():+.2f}R")
