"""
inspect_swings.py — โชว์ทุกจุด Swing High/Low ที่เจอ พร้อมเวลา ราคา vol_ratio/wick_ratio
และผ่านเกณฑ์ไหนบ้าง (volume / wick / ทั้งคู่) ไว้เช็คด้วยตาก่อนปรับ vol_multiplier /
wick_ratio_min ของ symbol ใหม่ (เช่น US500m) — left/right/tolerance_atr ใช้ค่าเดียวกับ
find_sl_from_structure (4, 4, 0.22) เพราะเป็นค่าที่ใช้เทรดจริง ไม่ใช้ default ของ
find_swing_highs/lows เฉยๆ (3, 3, 0.0) ซึ่งไม่ตรงกับพฤติกรรมจริงของระบบ

ใช้: python inspect_swings.py <SYMBOL> [TIMEFRAME=4H] [BARS=400]
     python inspect_swings.py US500m 4H 400
"""
import sys

import MetaTrader5 as mt5
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from config import MT5_TIMEFRAMES
from mt5_connect import connect
from swing import (swing_vol_multiplier, swing_wick_ratio_min, find_swing_highs,
                    find_swing_lows, collapse_swing_runs, _wick_ratio)

STRUCT_LEFT_RIGHT = 4
STRUCT_TOLERANCE_ATR = 0.22
VOL_LOOKBACK = 5


def get_hist(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES[timeframe], 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"ดึงข้อมูล {symbol} {timeframe} ไม่ได้ (เช็คว่าเปิด MT5 terminal "
                            f"และชื่อ symbol ถูกต้องหรือยัง)")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def vol_ratio(df: pd.DataFrame, idx: int, lookback: int = VOL_LOOKBACK) -> float:
    avg = df["tick_volume"].iloc[max(0, idx - lookback + 1): idx + 1].mean()
    this = df["tick_volume"].iloc[idx]
    return this / avg if avg > 0 else 0.0


def show(df: pd.DataFrame, idx_list: list[int], is_high: bool, clean_set: set[int],
         vmult: float, wmin: float | None) -> None:
    label = "High" if is_high else "Low"
    print(f"--- Swing {label} ({len(idx_list)} จุด) ---")
    print(f"{'time':<20}{'price':>14}{'vol_ratio':>12}{'wick_ratio':>12}   vol  wick  clean(ใช้จริง)")
    for idx in idx_list:
        row = df.iloc[idx]
        price = row["high"] if is_high else row["low"]
        vr = vol_ratio(df, idx)
        wr = _wick_ratio(df, idx, is_high=is_high)
        vol_pass = vr >= vmult
        wick_pass = (wmin is not None) and (wr >= wmin)
        clean_mark = "✓" if idx in clean_set else ""
        print(f"{str(row['time']):<20}{price:>14.5f}{vr:>11.2f}x{wr:>11.2f}    "
              f"{'Y' if vol_pass else '.'}    {'Y' if wick_pass else '.'}     {clean_mark}")
    print()


def main():
    symbol    = sys.argv[1] if len(sys.argv) > 1 else "US500m"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4H"
    bars      = int(sys.argv[3]) if len(sys.argv) > 3 else 400
    # arg 4 (optional): override wick_ratio_min ไว้ทดลองค่าอื่นโดยไม่ต้องแก้ swing.py ก่อน
    wmin_override = float(sys.argv[4]) if len(sys.argv) > 4 else None

    connect()
    df = get_hist(symbol, timeframe, bars)

    vmult = swing_vol_multiplier(symbol)
    wmin  = wmin_override if wmin_override is not None else swing_wick_ratio_min(symbol)

    raw_highs = find_swing_highs(df, left=STRUCT_LEFT_RIGHT, right=STRUCT_LEFT_RIGHT,
                                 tolerance_atr=STRUCT_TOLERANCE_ATR,
                                 vol_multiplier=vmult, wick_ratio_min=wmin)
    raw_lows  = find_swing_lows(df, left=STRUCT_LEFT_RIGHT, right=STRUCT_LEFT_RIGHT,
                                tolerance_atr=STRUCT_TOLERANCE_ATR,
                                vol_multiplier=vmult, wick_ratio_min=wmin)
    clean_highs, clean_lows = collapse_swing_runs(raw_highs, raw_lows, df)

    print(f"\n{symbol} {timeframe} ({len(df)} แท่ง) | vol_multiplier={vmult}x "
          f"wick_ratio_min={wmin}\n")
    show(df, raw_highs, True, set(clean_highs), vmult, wmin)
    show(df, raw_lows, False, set(clean_lows), vmult, wmin)


if __name__ == "__main__":
    main()
