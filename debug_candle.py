import sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()
from mt5_connect import connect
from scoring import get_ohlcv
from binance import merge_real_volume
from config import MT5_TIMEFRAMES
import MetaTrader5 as mt5
from datetime import timedelta

connect()
df = get_ohlcv("BTCUSDm", MT5_TIMEFRAMES["4H"], bars=200)
df = merge_real_volume(df, "BTCUSDm", "4H")

idx = 128  # Jun 18 19:00 TH
print(f"{'='*72}")
print(f"  window 11 แท่ง รอบ idx={idx} (Jun 18 19:00 TH)")
print(f"{'='*72}")
print(f"  {'idx':>4}  {'Time (TH)':<24}  {'Low':>10}  {'Volume':>10}")
print(f"  {'-'*4}  {'-'*24}  {'-'*10}  {'-'*10}")

window_low = df["low"].iloc[idx-5:idx+6].min()
for i in range(idx-5, idx+6):
    row    = df.iloc[i]
    th     = str(row["time"] + timedelta(hours=7))[:19]
    marker = " ← Jun 18 19:00 (target)" if i == idx else ""
    flag   = " ★ LOWEST" if row["low"] == window_low else ""
    print(f"  [{i:3d}]  {th:<24}  {row['low']:>10.2f}  {int(row['tick_volume']):>10,}{marker}{flag}")

print(f"\n  Low ต่ำสุดใน window = {window_low:.2f}")
print(f"  Low ของ Jun 18 19:00 = {df.iloc[idx]['low']:.2f}")
print(f"  ผ่าน price structure = {df.iloc[idx]['low'] == window_low}")

mt5.shutdown()
