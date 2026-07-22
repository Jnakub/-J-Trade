from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd
from mt5_connect import connect
from swing import find_tp_from_fibonacci
from binance import merge_real_volume

load_dotenv()
connect()

rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_D1, 0, 210)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df = merge_real_volume(df, "BTCUSDm", "1D")

fib = find_tp_from_fibonacci(df, "Long")

if fib["passed"]:
    print(f"จุด 0 (Low ล่าสุด) : {fib['swing_low']}")
    print(f"Move               : {fib['move']}")
    print()
    print(f"TP1 (0.382) : {fib['tp1']}")
    print(f"TP2 (0.618) : {fib['tp2']}")
    print(f"TP (0.786)  : {fib['levels']['0.786']}  ← TP หลัก")
    print(f"TP3 (1.000) : {fib['tp3']}")
    print(f"TP4 (1.618) : {fib['tp4']}")
else:
    print(f"ไม่ได้: {fib['reason']}")

mt5.shutdown()
