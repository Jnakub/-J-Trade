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
df = merge_real_volume(df, "BTCUSDm", "1D")

highs = find_swing_highs(df)
target = [i for i in highs if abs(df["high"].iloc[i] - 65567) < 500][0]

print(f"แท่ง 65,567 อยู่ที่ index {target} วันที่ {df['time'].iloc[target].date()}")
print(f"\n5 แท่งก่อนหน้า (index {target-5} ถึง {target-1}):")
for i in range(target - 5, target):
    print(f"  {df['time'].iloc[i].date()}  Vol={df['tick_volume'].iloc[i]:.0f}")

avg = df["tick_volume"].iloc[target - 5:target].mean()
print(f"\nAvg5 = {avg:.0f}")
print(f"Vol  = {df['tick_volume'].iloc[target]:.0f}")
print(f"Ratio = {df['tick_volume'].iloc[target] / avg:.2f}x")

mt5.shutdown()
