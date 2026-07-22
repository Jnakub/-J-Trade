from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import find_swing_highs, has_rejection, has_high_volume, is_lower_high, calc_atr
from binance import merge_real_volume

load_dotenv()
connect()

rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df = merge_real_volume(df, "BTCUSDm", "1D")

atr      = calc_atr(df)
highs    = find_swing_highs(df)

print("Swing Highs ล่าสุด 5 จุด — เกณฑ์แต่ละข้อ:")
print(f"  {'วันที่':<12} {'High':>8}  {'Vol':>8}  {'Avg5':>8}  {'Ratio':>6}  {'Rej':>5}  {'Vol':>5}  {'Struct':>6}  {'ผ่าน':>5}")
print("  " + "-" * 80)
for idx in highs[-5:]:
    rej_ok, rej_name = has_rejection(df, idx, atr)
    vol_ok           = has_high_volume(df, idx)
    struct_ok        = is_lower_high(highs, df)
    passed           = rej_ok and vol_ok and struct_ok
    date             = df["time"].iloc[idx].date()
    high             = df["high"].iloc[idx]
    vol              = df["tick_volume"].iloc[idx]
    avg5             = df["tick_volume"].iloc[idx - 5:idx].mean()
    ratio            = vol / avg5 if avg5 > 0 else 0
    print(f"  {str(date):<12} {high:>8.0f}  {vol:>8.0f}  {avg5:>8.0f}  {ratio:>6.2f}x  {'✅' if rej_ok else '❌':>5}  {'✅' if vol_ok else '❌':>5}  {'✅' if struct_ok else '❌':>6}  {'✅' if passed else '❌':>5}")

mt5.shutdown()
