"""news_paths.py — ดึง "เส้นทางราคารายชั่วโมง" ของทุกไม้ในไฟล์ base ออกมาเก็บไว้
ให้ news_sensitivity.py เอาไปจำลองกฎข่าวได้โดยไม่ต้องรัน replay ใหม่

ทำไมต้องมี: กฎข่าว 2 ตัวที่ระบบจริงใช้ตัดสินจาก "ณ ชั่วโมงนั้นไม้กำไรหรือยัง" ซึ่งไฟล์
replay ไม่ได้เก็บไว้ (มีแค่ entry/exit) — ต้องประกอบเส้นทางกลับมาจากแท่ง 1H
ใช้ close ของแท่ง 1H ที่ปิดล่าสุด = ตัวเดียวกับที่ analyze_position ใช้เป็น "ราคาปัจจุบัน"
ตอน as_of (ดู docstring ของมัน) ไม่ใช่ high/low

ใช้:  ./run_wine.sh news_paths.py     ->  news_paths.csv
"""
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from mt5_connect import connect
from config import MT5_TIMEFRAMES, SYMBOLS
from scoring import get_ohlcv

connect()
rows = []
for sym in SYMBOLS:
    tr = pd.read_csv(f"replay_trades_{sym}.csv", parse_dates=["time", "exit_time"])
    h1 = get_ohlcv(sym, MT5_TIMEFRAMES["1H"], bars=20000)[["time", "close"]]
    h1 = h1.set_index("time").close
    for i, t in enumerate(tr.itertuples()):
        seg = h1[(h1.index > t.time) & (h1.index <= t.exit_time)]
        if seg.empty:
            print(f"  ⚠️ {sym} #{i} ไม่มีแท่ง 1H ในช่วง {t.time} -> {t.exit_time}")
            continue
        sl_range = abs(t.entry - t.sl0)
        sign = 1 if t.direction == "Long" else -1
        rows.append(pd.DataFrame({
            "symbol": sym, "trade": i, "entry_time": t.time, "t": seg.index,
            "r": (seg.values - t.entry) * sign / sl_range,
        }))
    print(f"{sym:<9} {len(tr):>3} ไม้")
out = pd.concat(rows, ignore_index=True)
out.to_csv("news_paths.csv", index=False)
print(f"\nเขียน news_paths.csv — {len(out):,} แถว "
      f"({out.groupby(['symbol','trade']).ngroups} ไม้)")
