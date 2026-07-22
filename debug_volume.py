from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import find_swing_highs
from binance import merge_real_volume

load_dotenv()
connect()

rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")

print("ก่อน merge (tick_volume):")
highs = find_swing_highs(df)
for i in highs[-5:]:
    avg_vol = df["tick_volume"].iloc[i - 5:i].mean()
    ratio   = df["tick_volume"].iloc[i] / avg_vol
    date    = df["time"].iloc[i].date()
    print(f"  {date}  High={df['high'].iloc[i]:.0f}  Vol={df['tick_volume'].iloc[i]:.0f}  Avg={avg_vol:.0f}  Ratio={ratio:.2f}x  {'✅' if ratio >= 1.5 else '❌'}")

print()
df = merge_real_volume(df, "BTCUSDm", "1D")

print("หลัง merge (Binance real volume):")
for i in highs[-5:]:
    avg_vol = df["tick_volume"].iloc[i - 5:i].mean()
    ratio   = df["tick_volume"].iloc[i] / avg_vol
    date    = df["time"].iloc[i].date()
    print(f"  {date}  High={df['high'].iloc[i]:.0f}  Vol={df['tick_volume'].iloc[i]:.0f}  Avg={avg_vol:.0f}  Ratio={ratio:.2f}x  {'✅' if ratio >= 1.5 else '❌'}")

mt5.shutdown()
