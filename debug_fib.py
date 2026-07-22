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

print("Swing Highs ล่าสุด 5 จุด:")
for i in highs[-5:]:
    print(f"  {df['time'].iloc[i].date()}  High={df['high'].iloc[i]:.0f}")

print("\nSwing Lows ล่าสุด 5 จุด:")
for i in lows[-5:]:
    print(f"  {df['time'].iloc[i].date()}  Low={df['low'].iloc[i]:.0f}")

# High ก่อนล่าสุด และ Low ล่าสุด
sh_idx       = highs[-2]  # High ก่อนล่าสุด
swing_high   = df["high"].iloc[sh_idx]

lows_before  = [i for i in lows if i < highs[-1]]
sl_idx       = lows_before[-1] if lows_before else lows[-1]
swing_low    = df["low"].iloc[sl_idx]

move = swing_high - swing_low

print(f"\n--- Fibonacci ---")
print(f"Swing High (ก่อนล่าสุด) : {df['time'].iloc[sh_idx].date()}  {swing_high:.0f}")
print(f"Swing Low  (ล่าสุด)     : {df['time'].iloc[sl_idx].date()}  {swing_low:.0f}")
print(f"Move = {move:.0f}")
print()
for level, ratio in [("0", 0), ("0.236", 0.236), ("0.382", 0.382),
                     ("0.5", 0.5), ("0.618", 0.618), ("0.786", 0.786),
                     ("1.000", 1.0), ("1.272", 1.272), ("1.618", 1.618)]:
    price = swing_high - move * ratio
    print(f"  {level:<6} = {price:>10.2f}")

mt5.shutdown()
