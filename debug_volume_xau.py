from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing_xau import find_swing_highs_xau

load_dotenv()
connect()

for tf_name, tf in [("1D", mt5.TIMEFRAME_D1), ("4H", mt5.TIMEFRAME_H4)]:
    rates = mt5.copy_rates_from_pos("XAUUSDm", tf, 0, 210)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    print(f"\n===== XAUUSDm {tf_name} — tick_volume (ล่าสุด 15 แท่ง) =====")
    tail = df.tail(15)
    for _, row in tail.iterrows():
        print(f"  {row['time']}  Close={row['close']:.2f}  tick_volume={row['tick_volume']:.0f}")

    highs = find_swing_highs_xau(df)
    print(f"\n  Swing Highs พบ {len(highs)} จุด (median-based, lookback=10):")
    for i in highs[-5:]:
        window  = df["tick_volume"].iloc[i - 10:i]
        median  = window.median()
        ratio   = df["tick_volume"].iloc[i] / median if median > 0 else 0
        date    = df["time"].iloc[i]
        print(f"    {date}  High={df['high'].iloc[i]:.2f}  Vol={df['tick_volume'].iloc[i]:.0f}  Median={median:.0f}  Ratio={ratio:.2f}x  {'✅' if ratio >= 1.2 else '❌'}")

mt5.shutdown()
