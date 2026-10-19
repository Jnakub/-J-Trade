"""
test_fvg.py — เทสต์ fvg.find_fvgs ด้วยแท่งปลอมที่รู้คำตอบ (ไม่แตะ MT5 · รันบน Mac ได้: python3 test_fvg.py)
"""
import pandas as pd

import fvg

_fails = 0


def check(name, cond):
    global _fails
    print(("PASS " if cond else "FAIL ") + name)
    _fails += 0 if cond else 1


def bars(rows):
    """rows = [(high, low), ...]"""
    return pd.DataFrame(rows, columns=["high", "low"])


g = fvg.find_fvgs(bars([(101, 99), (108, 100), (110, 105)]))
check("Bullish: low แท่ง 3 (105) > high แท่ง 1 (101) -> ช่อง 101-105",
      len(g) == 1 and g[0]["type"] == "Bullish" and g[0]["bottom"] == 101 and g[0]["top"] == 105 and g[0]["i"] == 2)

g = fvg.find_fvgs(bars([(101, 99), (100, 92), (95, 90)]))
check("Bearish: high แท่ง 3 (95) < low แท่ง 1 (99) -> ช่อง 95-99",
      len(g) == 1 and g[0]["type"] == "Bearish" and g[0]["bottom"] == 95 and g[0]["top"] == 99)

check("ไส้แตะกันพอดี = ไม่ใช่ช่อง", fvg.find_fvgs(bars([(101, 99), (108, 100), (110, 101)])) == [])
check("ไส้ทับกัน = ไม่ใช่ช่อง", fvg.find_fvgs(bars([(101, 99), (108, 100), (110, 100.5)])) == [])
check("น้อยกว่า 3 แท่ง = []", fvg.find_fvgs(bars([(1, 0), (2, 1)])) == [])

g = fvg.find_fvgs(bars([(101, 99), (108, 100), (110, 105), (120, 112)]))
check("แท่งต่อกันหาได้หลายช่อง (1-3 และ 2-4)", [x["i"] for x in g] == [2, 3])

# --- fvg_bias — แท่งปลอมสร้างจากลำดับช่องที่ต้องการ ("U" = Bullish · "D" = Bearish · "." = วันไม่มีช่อง)
# แต่ละช่องตามด้วยแท่งเกาะกลุ่ม 2 แท่ง (ไส้ทับกันหมด) กันช่องแถม · แท่งสุดท้ายถูกตัดทิ้งเสมอ (= ยังไม่ปิด)
def make(seq, live=True):
    p, rows = 100.0, []
    for e in seq:
        if e == "U":
            rows += [(p + 1, p - 1), (p + 6, p), (p + 8, p + 3), (p + 8.5, p + 2.5), (p + 8.2, p + 2.8)]
            p += 5
        elif e == "D":
            rows += [(p + 1, p - 1), (p, p - 6), (p - 3, p - 8), (p - 2.5, p - 8.5), (p - 2.8, p - 8.2)]
            p -= 5
        else:
            rows += [(p + 1, p - 1)]
    return bars(rows + ([(p + 1, p - 1)] if live else []))


for s in ("UU", "UDU", "U.U", "UUD", "UUDD", "DU"):
    got = "".join("U" if g["type"] == "Bullish" else "D" for g in fvg.find_fvgs(make(s)))
    check(f"แท่งปลอม '{s}' มีช่องตรงที่ตั้งใจ ({got})", got == s.replace(".", ""))

bias_of = lambda s, live=True: fvg.fvg_bias("X", make(s, live))[0]
check("bias: Bullish 1 ช่อง = ยังไม่มี", bias_of("U") is None)
check("bias: Bullish 2 ติดกัน (มีวันไม่มีช่องคั่น) = Long", bias_of("U..U") == "Long")
check("bias: Long แล้วเจอ Bearish 1 ช่อง = ยัง Long", bias_of("UUD") == "Long")
check("bias: Bearish ครบ 2 ติดกัน = Short", bias_of("UUDD") == "Short")
check("bias: Bullish, Bearish, Bullish = ไม่ติดกัน ยังไม่มี", bias_of("UDU") is None)
check("bias: Short แล้ว U D U = ยัง Short (ไม่มีฝั่งไหนครบ 2 ติดกันใหม่)", bias_of("DDUDU") == "Short")
# แท่งที่ยังไม่ปิดถูกตัด: ถ้าช่อง U ที่ 2 จบที่แท่งสุดท้ายพอดี ต้องยังไม่นับ
rows = make("UU", live=False)
cut = rows.iloc[:-2].reset_index(drop=True)          # แท่งสุดท้ายของ cut = แท่งที่ 3 ของช่อง U ที่ 2
check("bias: ช่องที่แท่งที่ 3 ยังไม่ปิด ไม่ถูกนับ", fvg.fvg_bias("X", cut)[0] is None
      and fvg.fvg_bias("X", rows.iloc[:-1].reset_index(drop=True))[0] == "Long")

# --- merge_sunday (2026-10-12 = จันทร์)
def tb(rows):
    """rows = [(วันที่, open, high, low, close), ...]"""
    return pd.DataFrame([{"time": pd.Timestamp(d), "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows])


fx = tb([("2026-10-09", 100, 101, 99, 100.5),     # ศุกร์
         ("2026-10-11", 103, 104, 102.5, 103.5),  # อาทิตย์ (เปิดกระโดด)
         ("2026-10-12", 103.5, 106, 101, 105)])   # จันทร์
m = fvg.merge_sunday(fx)
check("รวมอาทิตย์: เหลือ 2 แท่ง", len(m) == 2)
r = m.iloc[1]
check("รวมอาทิตย์: open อา. · high/low สุดขั้ว · close/time จันทร์",
      r["open"] == 103 and r["high"] == 106 and r["low"] == 101 and r["close"] == 105
      and r["time"] == pd.Timestamp("2026-10-12"))
cr = tb([("2026-10-10", 1, 2, 0, 1), ("2026-10-11", 1, 2, 0, 1), ("2026-10-12", 1, 2, 0, 1)])   # มีเสาร์ = crypto
check("มีแท่งวันเสาร์ (crypto) ไม่แตะ", len(fvg.merge_sunday(cr)) == 3)
check("แท่งอาทิตย์เป็นแท่งสุดท้าย = ตัดทิ้ง (รอวันจันทร์ปิด)", len(fvg.merge_sunday(fx.iloc[:2])) == 1)
# Bullish หลอกจากแท่งอาทิตย์ (แบบ XAU 2026-06-14): ศุกร์ H 101 -> อา. L 102.5 = ช่อง · พอรวมกับจันทร์ (L 100.5 < H พฤหัส 100.8) ช่องหาย
fx3 = tb([("2026-10-08", 99, 100.8, 98, 100), ("2026-10-09", 100, 101, 99, 100.5),
          ("2026-10-11", 103, 104, 102.5, 103.5), ("2026-10-12", 103.5, 106, 100.5, 105)])
check("ช่องที่เกิดจากแท่งอาทิตย์ หายหลังรวม",
      len(fvg.find_fvgs(fx3)) == 1 and fvg.find_fvgs(fvg.merge_sunday(fx3)) == [])

# --- to_weekly (2026-10-12 = จันทร์)
def daily(start, n, skip_sat=True, live=True):
    rows, d = [], pd.Timestamp(start)
    while len(rows) < n:
        if not (skip_sat and d.dayofweek == 5):
            k = len(rows)
            rows.append({"time": d, "open": 100 + k, "high": 101 + k, "low": 99 + k, "close": 100.5 + k})
        d += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


fxd = daily("2026-10-04", 12)                 # อา. 10-04 .. ศ. 10-16 ไม่มีเสาร์ = 2 สัปดาห์ (อา.+จ.-ศ.) + แท่งอาทิตย์ 10-11 อยู่สัปดาห์ 2
w = fvg.to_weekly(pd.concat([fxd, daily("2026-10-19", 1).assign(time=pd.Timestamp("2026-10-19"))], ignore_index=True))
check("1W ไม่ใช่ crypto: 2 สัปดาห์ที่จบแล้ว · แท่งอาทิตย์นับเป็นต้นสัปดาห์ใหม่",
      len(w) == 2 and w["time"].iloc[0] == pd.Timestamp("2026-10-04") and w["time"].iloc[1] == pd.Timestamp("2026-10-11")
      and w["open"].iloc[1] == fxd["open"].iloc[6] and w["close"].iloc[1] == fxd["close"].iloc[-1])
check("1W: สัปดาห์ของแท่ง live ถูกตัดทั้งสัปดาห์", len(fvg.to_weekly(fxd)) == 1)
cr = daily("2026-10-05", 15, skip_sat=False)   # จ. 10-05 .. จ. 10-19 (live) = crypto
w = fvg.to_weekly(cr)
check("1W crypto: จันทร์-อาทิตย์ 2 สัปดาห์ · สัปดาห์ละ 7 แท่ง",
      len(w) == 2 and w["high"].iloc[0] == cr["high"].iloc[6] and w["low"].iloc[1] == cr["low"].iloc[7])

print(f"\n{'ผ่านทั้งหมด' if not _fails else f'FAIL {_fails} เคส'}")
raise SystemExit(1 if _fails else 0)
