"""
show_swing_all.py — แสดง Swing High/Low ของ BTC ทุก TF (1D, 4H, 1H)
ใช้: python show_swing_all.py
"""
import sys
import pandas as pd
from datetime import timedelta
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import MetaTrader5 as mt5
from mt5_connect import connect
from scoring import get_ohlcv
from binance import merge_real_volume
from swing import find_swing_highs, find_swing_lows, calc_atr
from config import MT5_TIMEFRAMES

SYMBOL     = "BTCUSDm"
TZ_OFFSET  = timedelta(hours=7)

# เงื่อนไขแต่ละ TF
# atr_rej = threshold สำหรับ wick (wick >= ATR * atr_rej = ผ่าน rejection)
TF_CONFIG = {
    "1D": {"tf_key": "1D", "bars": 210, "left": 3, "right": 3, "tol": 0.05, "vol": 1.2, "atr_rej": 0.3},
    "4H": {"tf_key": "4H", "bars": 200, "left": 5, "right": 5, "tol": 0.05, "vol": 1.9, "atr_rej": 0.5},
    "1H": {"tf_key": "1H", "bars": 300, "left": 5, "right": 5, "tol": 0.05, "vol": 1.9, "atr_rej": 0.5},
}

load_dotenv()
connect()


def fmt_time(t) -> str:
    return str(pd.to_datetime(t) + TZ_OFFSET)[:16] + " (TH)"


def avg_vol(df: pd.DataFrame, idx: int, lookback: int = 5) -> float:
    return df["tick_volume"].iloc[max(0, idx - lookback + 1): idx + 1].mean()


def has_rejection(df: pd.DataFrame, idx: int, atr: pd.Series) -> tuple[bool, str]:
    c          = df.iloc[idx]
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    atr_val    = atr.iloc[idx]
    avg        = df["tick_volume"].iloc[max(0, idx - 4): idx + 1].mean()
    vol_ratio  = df["tick_volume"].iloc[idx] / avg if avg > 0 else 0.0
    return upper_wick, lower_wick, atr_val, vol_ratio


def _rej_high(df: pd.DataFrame, idx: int, atr: pd.Series, atr_rej: float) -> bool:
    c = df.iloc[idx]; uw = c["high"] - max(c["close"], c["open"])
    avg = df["tick_volume"].iloc[max(0, idx-4):idx+1].mean()
    vr  = df["tick_volume"].iloc[idx] / avg if avg > 0 else 0.0
    return uw >= atr.iloc[idx] * atr_rej or vr >= 2.0

def _rej_low(df: pd.DataFrame, idx: int, atr: pd.Series, atr_rej: float) -> bool:
    c = df.iloc[idx]; lw = min(c["close"], c["open"]) - c["low"]
    avg = df["tick_volume"].iloc[max(0, idx-4):idx+1].mean()
    vr  = df["tick_volume"].iloc[idx] / avg if avg > 0 else 0.0
    return lw >= atr.iloc[idx] * atr_rej or vr >= 2.0


def show_swings(label: str, df: pd.DataFrame, left: int, right: int, tol: float,
                vol: float = 1.9, atr_rej: float = 0.5):
    atr         = calc_atr(df)
    swing_highs = find_swing_highs(df, left=left, right=right, tolerance_atr=tol, vol_multiplier=vol)
    swing_lows  = find_swing_lows(df,  left=left, right=right, tolerance_atr=tol, vol_multiplier=vol)

    print(f"\n{'='*80}")
    print(f"  BTC {label} — Swing Highs ({len(swing_highs)} จุด)  [rej=ATR×{atr_rej}]")
    print(f"{'='*80}")
    print(f"  {'Time (TH)':<22}  {'High':>10}  {'Volume':>8}  {'Avg(5)':>8}  {'Ratio':>6}  Rejection")
    print(f"  {'-'*22}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*6}  ---------")
    for idx in swing_highs[-10:]:
        row                              = df.iloc[idx]
        v                                = row["tick_volume"]
        avg                              = avg_vol(df, idx)
        ratio                            = v / avg if avg > 0 else 0.0
        upper_wick, _, atr_val, vr       = has_rejection(df, idx, atr)
        rej  = "✅ wick" if upper_wick >= atr_val * atr_rej else (f"✅ vol={vr:.1f}x" if vr >= 2.0 else "❌")
        print(f"  {fmt_time(row['time']):<22}  {row['high']:>10.2f}  {v:>8,.0f}  {avg:>8,.0f}  {ratio:>6.2f}x  {rej}")

    print(f"\n{'='*80}")
    print(f"  BTC {label} — Swing Lows ({len(swing_lows)} จุด)  [rej=ATR×{atr_rej}]")
    print(f"{'='*80}")
    print(f"  {'Time (TH)':<22}  {'Low':>10}  {'Volume':>8}  {'Avg(5)':>8}  {'Ratio':>6}  Rejection")
    print(f"  {'-'*22}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*6}  ---------")
    for idx in swing_lows[-10:]:
        row                              = df.iloc[idx]
        v                                = row["tick_volume"]
        avg                              = avg_vol(df, idx)
        ratio                            = v / avg if avg > 0 else 0.0
        _, lower_wick, atr_val, vr       = has_rejection(df, idx, atr)
        rej  = "✅ wick" if lower_wick >= atr_val * atr_rej else (f"✅ vol={vr:.1f}x" if vr >= 2.0 else "❌")
        print(f"  {fmt_time(row['time']):<22}  {row['low']:>10.2f}  {v:>8,.0f}  {avg:>8,.0f}  {ratio:>6.2f}x  {rej}")

    # สรุป: เฉพาะจุดที่ผ่าน rejection
    pass_h = [i for i in swing_highs if _rej_high(df, i, atr, atr_rej)]
    pass_l = [i for i in swing_lows  if _rej_low(df,  i, atr, atr_rej)]
    print(f"\n  --- ผ่าน Rejection: Highs={len(pass_h)} Lows={len(pass_l)} ---")
    print(f"  HIGHS (pass): ", end="")
    for idx in reversed(pass_h[-5:]):
        print(f"  {fmt_time(df.iloc[idx]['time'])} H={df.iloc[idx]['high']:.0f}", end="")
    print()
    print(f"  LOWS  (pass): ", end="")
    for idx in reversed(pass_l[-5:]):
        print(f"  {fmt_time(df.iloc[idx]['time'])} L={df.iloc[idx]['low']:.0f}", end="")
    print()


# ---------------------------------------------------------------------------
for label, cfg in TF_CONFIG.items():
    df = get_ohlcv(SYMBOL, MT5_TIMEFRAMES[cfg["tf_key"]], bars=cfg["bars"])
    df = merge_real_volume(df, SYMBOL, cfg["tf_key"])
    show_swings(label, df, cfg["left"], cfg["right"], cfg["tol"], cfg["vol"], cfg.get("atr_rej", 0.5))

mt5.shutdown()
