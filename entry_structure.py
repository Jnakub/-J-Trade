"""
entry_structure.py — คัดกรอง "โครงสร้างของ setup" ณ จุดเข้าไม้ ว่าแยกไม้ที่ไม่เคยไปไหน
                     (MFE < 0.5R) ออกจากไม้ที่ทำงานได้ไหม

ทำไมต้องมีแยกจาก entry_features.py: ตัวนั้นคัดกรอง **เรขาคณิตชั่วขณะ** (ราคาอยู่ตรงไหนเทียบ
จุดสุดขั้ว/ATR/ระยะ SL) ไปแล้ว 21 ตัวแปร **ไม่มีตัวไหนแยกได้** และตัวที่ผ่าน permutation
(turn, p=0.021) พอ replay จริงก็ถูก backfill กลืนเหลือ +1.74R เพราะมันเป็นตัวแปรที่
**ดีขึ้นเองเมื่อเวลาผ่านไป** ด่านจึงกลายเป็นตัวหน่วง (ดู memory entry-gates-are-delays-not-filters)

ไฟล์นี้ไปหาตัวแปรอีกตระกูล: **คุณสมบัติของ setup เอง ที่ไม่คลี่คลายตามเวลา**
  swing_age   อายุของ swing ที่ใช้วาง SL (แท่ง 4H) — รอไปก็ยิ่งเก่า ไม่มีวันสดขึ้น
  struct_n    จำนวน swing ในหน้าต่าง = โครงสร้างชัดหรือสับสน
  kl_touches  key level ที่ใกล้ที่สุดถูกแตะมาแล้วกี่ครั้ง
  kl_dist     ห่างจาก key level กี่ %
  move_atr    ขนาด impulse เดิมที่ Fibonacci ใช้ฉาย (ATR) = "รอบนี้ตลาดเดินทีละเท่าไหร่"
  ext_ratio   ราคาเดินจากจุดฉาย (B) มาแล้วกี่เท่าของ impulse เดิม = กินทางไปแล้วแค่ไหน
  tp_over_move ระยะ TP เทียบ impulse เดิม

⚠️ ข้อจำกัดเดียวกับ entry_features.py: เห็นเฉพาะไม้ที่ผ่านด่านมาแล้ว · ชุดไม้คงที่ (ไม่เห็น
   ไม้ที่จะ backfill เข้ามา) ตัวเลขที่ได้จึงเป็น **ขอบบนที่หลวมมาก** — เคสจริงเคยสูงเกิน 5 เท่า
🔴 เกณฑ์ตัดสิน (คิดจาก base): ตัดไม้ dead ได้ +0.848R · ตัดไม้ good เสีย −0.928R
   -> ต้องได้ dead:good >= 1.09 เทียบฐาน ~0.5 = เก่งกว่าสุ่มราว 2 เท่า
   และต้องผ่าน permutation test เพราะคัดหลายตัวแปรพร้อมกัน

ใช้:  ./run_wine.sh entry_structure.py      (ต้องมี mfe_trades.csv จาก backtest_mfe.py ก่อน)
"""
import numpy as np
import pandas as pd

from mt5_connect import connect
from config import MT5_TIMEFRAMES, SYMBOLS
import regime_check as rc
from scoring import get_ohlcv, merge_real_volume
from swing import (find_sl_from_structure, find_tp_from_fibonacci,
                   swing_vol_multiplier, swing_wick_ratio_min)

DEAD, GOOD = 0.5, 1.0
mfe = pd.read_csv("mfe_trades.csv", parse_dates=["time", "exit_time"])
connect()

rows = []
for symbol in SYMBOLS:
    tr = mfe[mfe.symbol == symbol]
    vm, wr = swing_vol_multiplier(symbol), swing_wick_ratio_min(symbol)
    for t in tr.itertuples():
        try:
            df = merge_real_volume(get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=200,
                                             as_of=t.time), symbol, "4H", as_of=t.time)
            df_full = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=rc.KEY_LEVEL_BARS, as_of=t.time)
            last = len(df) - 2                      # แท่ง 4H ปิดล่าสุด ณ เวลาตัดสินใจ
            sl_i = find_sl_from_structure(df, t.direction, left=4, right=4, tolerance_atr=0.22,
                                          vol_multiplier=vm, wick_ratio_min=wr)
            fib = find_tp_from_fibonacci(df, t.direction, left=4, right=4, tolerance_atr=0.22,
                                         vol_multiplier=vm, wick_ratio_min=wr)
            st = rc.check_structure(df.iloc[:len(df) - 1].reset_index(drop=True),
                                    vol_multiplier=vm, wick_ratio_min=wr)
            kl = rc.check_key_level(symbol, t.entry, df=df_full)
        except Exception:
            continue
        if not sl_i.get("passed") or not fib.get("passed"):
            continue
        atr = float(sl_i["atr"]) or np.nan
        move = float(fib["move"])
        origin = float(fib["origin"])
        rows.append(dict(
            symbol=symbol, time=t.time, strategy=t.strategy, R=t.R, MFE=t.MFE,
            swing_age=last - int(sl_i["swing_idx"]),
            struct_n=int(st.get("n_highs", 0)) + int(st.get("n_lows", 0)),
            kl_touches=int(kl.get("touches") or 0),
            kl_dist=float(kl.get("distance_pct") or np.nan),
            move_atr=move / atr if atr else np.nan,
            ext_ratio=abs(t.entry - origin) / move if move else np.nan,
            tp_over_move=abs(t.tp0 - t.entry) / move if move else np.nan,
        ))

df = pd.DataFrame(rows)
df["dead"] = df.MFE < DEAD
df["good"] = df.MFE >= GOOD
nd, ng = df.dead.sum(), df.good.sum()
rd, rg = df[df.dead].R.mean(), df[df.good].R.mean()
bar, base = -rg / rd, nd / ng
print(f"\nคำนวณได้ {len(df)}/{len(mfe)} ไม้ · dead {nd} · good {ng}")
print(f"เกณฑ์: dead:good >= {bar:.2f} · ฐาน {base:.2f} -> ต้องเก่งกว่าสุ่ม {bar/base:.2f} เท่า\n")

FEAT = ["swing_age", "struct_n", "kl_touches", "kl_dist", "move_atr", "ext_ratio", "tp_over_move"]
k = max(int(len(df) * 0.25), 5)


def sides(col, dead, good, R):
    v = df[col].values
    ok = ~pd.isna(v)
    idx = np.argsort(v[ok]); pos = np.where(ok)[0]
    for lab, sub in (("ตัดฝั่งสูง", pos[idx[-k:]]), ("ตัดฝั่งต่ำ", pos[idx[:k]])):
        yield lab, sub, dead[sub].sum(), good[sub].sum(), -R[sub].sum()


dead, good, R = df.dead.values, df.good.values, df.R.values
print(f"{'ตัวแปร':<14}{'ทิศที่ตัด':<12}{'ตัด':>5}{'dead':>6}{'good':>6}{'dead:good':>11}{'ΔR ถ้าตัด':>11}")
print("-" * 68)
best = []
for f in FEAT:
    for lab, sub, a, b, dR in sides(f, dead, good, R):
        ratio = a / b if b else float("inf")
        print(f"{f:<14}{lab:<12}{len(sub):>5}{a:>6}{b:>6}{ratio:>11.2f}{dR:>+11.2f}"
              + (" ✅" if ratio >= bar else ""))
        best.append((ratio, dR, f, lab))
    print()


def best_stat(dead, good, R):
    br, bd = -1, -99
    for f in FEAT:
        for _, _, a, b, dR in sides(f, dead, good, R):
            if b:
                br = max(br, a / b)
            bd = max(bd, dR)
    return br, bd


obs_r, obs_d = best_stat(dead, good, R)
rng = np.random.default_rng(0)
cr = cd = 0
N = 2000
for _ in range(N):
    p = rng.permutation(len(df))
    r, d = best_stat(dead[p], good[p], R[p])
    cr += r >= obs_r; cd += d >= obs_d
print(f"ดีที่สุดใน {len(FEAT)*2} การทดสอบ: dead:good {obs_r:.2f} · ΔR {obs_d:+.2f}R")
print(f"permutation {N} รอบ -> P(dead:good) = {cr/N:.3f} · P(ΔR) = {cd/N:.3f}")
df.to_csv("entry_structure.csv", index=False)
print("\nเขียน entry_structure.csv แล้ว")
