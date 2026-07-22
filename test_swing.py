from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import find_swing_highs, find_swing_lows
from binance import merge_real_volume

load_dotenv()
connect()

rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df = merge_real_volume(df, "BTCUSDm", "1D")

highs = find_swing_highs(df)
lows  = find_swing_lows(df)

print("Swing Highs:")
for i in highs[-8:]:
    avg   = df["tick_volume"].iloc[i-4:i+1].mean()
    ratio = df["tick_volume"].iloc[i] / avg
    print(f"  {df['time'].iloc[i].date()}  High={df['high'].iloc[i]:.0f}  Ratio={ratio:.2f}x")

print("\nSwing Lows:")
for i in lows[-8:]:
    avg   = df["tick_volume"].iloc[i-4:i+1].mean()
    ratio = df["tick_volume"].iloc[i] / avg
    print(f"  {df['time'].iloc[i].date()}  Low={df['low'].iloc[i]:.0f}  Ratio={ratio:.2f}x")

mt5.shutdown()
