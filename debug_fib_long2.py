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

low_latest_idx  = lows[-1]
low_prev_idx    = lows[-2]
highs_before    = [i for i in highs if i < low_latest_idx]
high_latest_idx = highs_before[-1]

print(f"Low ก่อนล่าสุด : {df['time'].iloc[low_prev_idx].date()}  Low={df['low'].iloc[low_prev_idx]:.0f}")
print(f"High ล่าสุด    : {df['time'].iloc[high_latest_idx].date()}  High={df['high'].iloc[high_latest_idx]:.0f}")
print(f"Low ล่าสุด     : {df['time'].iloc[low_latest_idx].date()}  Low={df['low'].iloc[low_latest_idx]:.0f}")

mt5.shutdown()
