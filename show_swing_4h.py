"""
show_swing_4h.py — แสดง Swing High/Low บน 4H ของ BTC พร้อม Volume และ Avg Volume
ใช้: python show_swing_4h.py
"""
import sys
import pandas as pd
from datetime import timedelta
from dotenv import load_dotenv

TZ_OFFSET = timedelta(hours=7)   # UTC → UTC+7 (ไทย)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import MetaTrader5 as mt5
from mt5_connect import connect
from scoring import get_ohlcv
from binance import merge_real_volume
from swing import find_swing_highs, find_swing_lows
from config import MT5_TIMEFRAMES

SYMBOL      = "BTCUSDm"
VOL_LOOKBACK = 5

load_dotenv()
connect()

df = get_ohlcv(SYMBOL, MT5_TIMEFRAMES["4H"], bars=200)
df = merge_real_volume(df, SYMBOL, "4H")

swing_highs = find_swing_highs(df, left=4, right=4, tolerance_atr=0.05)
swing_lows  = find_swing_lows(df, left=4, right=4, tolerance_atr=0.05)


def fmt_time(t) -> str:
    return str(pd.to_datetime(t) + TZ_OFFSET)[:19] + " (TH)"


def avg_vol(df: pd.DataFrame, idx: int, lookback: int = VOL_LOOKBACK) -> float:
    # รวมแท่งปัจจุบัน — ตรงกับ TradingView Volume MA Length=5
    return df["tick_volume"].iloc[max(0, idx - lookback + 1): idx + 1].mean()


# ---------------------------------------------------------------------------
print(f"\n{'=' * 72}")
print(f"  BTC 4H — Swing Highs ({len(swing_highs)} จุด)  [ใช้ Bitstamp volume รายแท่ง]")
print(f"{'=' * 72}")
print(f"  {'idx':>4}  {'Time (TH)':<24}  {'High':>10}  {'Volume':>10}  {'Avg(5)':>10}  {'Ratio':>6}")
print(f"  {'-'*4}  {'-'*24}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*6}")
for idx in swing_highs:
    row   = df.iloc[idx]
    vol   = row["tick_volume"]
    avg   = avg_vol(df, idx)
    ratio = vol / avg if avg > 0 else 0.0
    print(f"  [{idx:3d}]  {fmt_time(row['time']):<24}  {row['high']:>10.2f}  {vol:>10,.0f}  {avg:>10,.0f}  {ratio:>6.2f}x")

# ---------------------------------------------------------------------------
print(f"\n{'=' * 72}")
print(f"  BTC 4H — Swing Lows ({len(swing_lows)} จุด)  [ใช้ Bitstamp volume รายแท่ง]")
print(f"{'=' * 72}")
print(f"  {'idx':>4}  {'Time (TH)':<24}  {'Low':>10}  {'Volume':>10}  {'Avg(5)':>10}  {'Ratio':>6}")
print(f"  {'-'*4}  {'-'*24}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*6}")
for idx in swing_lows:
    row   = df.iloc[idx]
    vol   = row["tick_volume"]
    avg   = avg_vol(df, idx)
    ratio = vol / avg if avg > 0 else 0.0
    print(f"  [{idx:3d}]  {fmt_time(row['time']):<24}  {row['low']:>10.2f}  {vol:>10,.0f}  {avg:>10,.0f}  {ratio:>6.2f}x")

# ---------------------------------------------------------------------------
print(f"\n{'=' * 72}")
print("  สรุป 5 จุดล่าสุด (ใหม่ → เก่า)")
print(f"{'=' * 72}")
print("  SWING HIGHS:")
for idx in reversed(swing_highs[-5:]):
    row = df.iloc[idx]
    print(f"    {fmt_time(row['time'])}  High={row['high']:>10.2f}  Vol={row['tick_volume']:>10,.2f}")
print("  SWING LOWS:")
for idx in reversed(swing_lows[-5:]):
    row = df.iloc[idx]
    print(f"    {fmt_time(row['time'])}  Low ={row['low']:>10.2f}  Vol={row['tick_volume']:>10,.2f}")

mt5.shutdown()
