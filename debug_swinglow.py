from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import find_swing_lows
from binance import merge_real_volume

load_dotenv()
connect()

rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df = merge_real_volume(df, "BTCUSDm", "1D")

lows = find_swing_lows(df)

print("Swing Lows ล่าสุด 8 จุด:")
for i in lows[-8:]:
    avg_vol = df["tick_volume"].iloc[i - 5:i].mean()
    ratio   = df["tick_volume"].iloc[i] / avg_vol if avg_vol > 0 else 0
    vol_ok  = ratio >= 1.2
    print(f"  {df['time'].iloc[i].date()}  Low={df['low'].iloc[i]:.0f}  Vol={df['tick_volume'].iloc[i]:.0f}  Avg5={avg_vol:.0f}  Ratio={ratio:.2f}x  {'✅' if vol_ok else '❌'}")

# เช็คแถว 5 มิ.ย.
target = df[df["time"].dt.date.astype(str).str.startswith("2026-06-05")]
if not target.empty:
    idx = target.index[0]
    print(f"\nแท่ง 5 มิ.ย. (index {idx}):")
    print(f"  Low = {df['low'].iloc[idx]:.0f}")
    window_left  = df["low"].iloc[idx-3:idx]
    window_right = df["low"].iloc[idx+1:idx+4]
    print(f"  3 แท่งซ้าย : {window_left.values}")
    print(f"  3 แท่งขวา : {window_right.values}")
    print(f"  ต่ำกว่าซ้ายทั้งหมด : {(df['low'].iloc[idx] < window_left).all()}")
    print(f"  ต่ำกว่าขวาทั้งหมด : {(df['low'].iloc[idx] < window_right).all()}")
    print(f"  เป็น Swing Low : {idx in lows}")

mt5.shutdown()
