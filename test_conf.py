from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import check_confirmation, find_swing_lows
from binance import merge_real_volume

load_dotenv()
connect()

rates_1d = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df_1d = pd.DataFrame(rates_1d)
df_1d["time"] = pd.to_datetime(df_1d["time"], unit="s")
df_1d = merge_real_volume(df_1d, "BTCUSDm", "1D")

tick  = mt5.symbol_info_tick("BTCUSDm")
price = tick.bid

lows = find_swing_lows(df_1d)
print(f"Swing Low ล่าสุด : {df_1d['time'].iloc[lows[-1]].date()}  Low={df_1d['low'].iloc[lows[-1]]:.2f}")
print(f"ราคาปัจจุบัน    : {price:.2f}")

r = check_confirmation(price, df_1d, "Short", "BTCUSDm")
print(f"Key Level : {r['key_level']}")
print(f"Result    : {r['reason']}")
print(f"Conf OK   : {r['conf_ok']}")
mt5.shutdown()
