import sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()
from mt5_connect import connect
from scoring import get_ohlcv
from binance import merge_real_volume
from swing import find_swing_highs, find_swing_lows, calc_atr
from config import MT5_TIMEFRAMES
import MetaTrader5 as mt5
from datetime import timedelta

connect()
df = get_ohlcv("BTCUSDm", MT5_TIMEFRAMES["1D"], bars=210)
df = merge_real_volume(df, "BTCUSDm", "1D")
atr = calc_atr(df)

swing_highs = find_swing_highs(df, left=3, right=3, tolerance_atr=0.05, vol_multiplier=1.2)
swing_lows  = find_swing_lows(df,  left=3, right=3, tolerance_atr=0.05, vol_multiplier=1.2)

print(f"Swing Highs ทั้งหมด: {len(swing_highs)} จุด")
print(f"Swing Lows  ทั้งหมด: {len(swing_lows)} จุด")
print()

print("=== Swing Highs — ทุกจุด (รวมที่ไม่ผ่าน rejection) ===")
print(f"  {'Time (TH)':<20}  {'High':>10}  {'Ratio':>6}  {'UWick':>8}  {'ATR':>8}  {'ATR*0.5':>8}  Rej")
for idx in swing_highs[-20:]:
    row   = df.iloc[idx]
    th    = str(row["time"] + timedelta(hours=7))[:10]
    avg   = df["tick_volume"].iloc[max(0,idx-4):idx+1].mean()
    ratio = row["tick_volume"] / avg if avg > 0 else 0
    c     = df.iloc[idx]
    uw    = c["high"] - max(c["close"], c["open"])
    atr_v = atr.iloc[idx]
    rej   = "✅" if (uw >= atr_v*0.5 or ratio >= 2.0) else "❌"
    print(f"  {th:<20}  {row['high']:>10.0f}  {ratio:>6.2f}x  {uw:>8.0f}  {atr_v:>8.0f}  {atr_v*0.5:>8.0f}  {rej}")

mt5.shutdown()
