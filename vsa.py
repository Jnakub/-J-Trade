import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _volume_class(df: pd.DataFrame, idx: int, lookback: int = 5) -> str:
    if idx < lookback - 1:
        return "Normal"
    avg = df["tick_volume"].iloc[idx - lookback + 1:idx + 1].mean()
    if avg == 0:
        return "Normal"
    ratio = df["tick_volume"].iloc[idx] / avg
    if ratio >= 2.0:   return "Very High"
    if ratio >= 1.5:   return "High"
    if ratio >= 0.8:   return "Normal"
    if ratio >= 0.5:   return "Low"
    return "Very Low"


def _spread_class(df: pd.DataFrame, idx: int, lookback: int = 5) -> str:
    if idx < lookback:
        return "Normal"
    spreads = (df["high"] - df["low"]).iloc[idx - lookback:idx]
    avg = spreads.mean()
    if avg == 0:
        return "Normal"
    ratio = (df["high"].iloc[idx] - df["low"].iloc[idx]) / avg
    if ratio >= 1.5:  return "Wide"
    if ratio >= 0.7:  return "Normal"
    return "Narrow"


def _close_position(row) -> str:
    rng = row["high"] - row["low"]
    if rng == 0:
        return "Middle"
    pct = (row["close"] - row["low"]) / rng
    if pct > 0.67:   return "Top"
    if pct > 0.50:   return "Upper-Mid"
    if pct > 0.33:   return "Lower-Mid"
    return "Bottom"


def _wick_side(row) -> str:
    body = abs(row["close"] - row["open"])
    upper = row["high"] - max(row["close"], row["open"])
    lower = min(row["close"], row["open"]) - row["low"]
    threshold = body * 2 if body > 0 else 1e-9
    has_upper = upper >= threshold
    has_lower = lower >= threshold
    if has_upper and has_lower: return "Both"
    if has_upper:               return "Long Upper Wick"
    if has_lower:               return "Long Lower Wick"
    return "None"


def _price_position(df: pd.DataFrame, idx: int, lookback: int = 10) -> str:
    """ราคาอยู่ที่ Top / Middle / Bottom ของ 10 แท่งหลัง"""
    start = max(0, idx - lookback)
    hi = df["high"].iloc[start:idx + 1].max()
    lo = df["low"].iloc[start:idx + 1].min()
    rng = hi - lo
    if rng == 0:
        return "Middle"
    pct = (df["close"].iloc[idx] - lo) / rng
    if pct > 0.67: return "Top"
    if pct > 0.33: return "Middle"
    return "Bottom"


# ---------------------------------------------------------------------------
# Pattern Detection
# ---------------------------------------------------------------------------

PATTERNS = {
    # (color, vol, spread, close_pos, wick) → (name, bullish, bearish)
    # Short-friendly patterns
    "Shooting Star":      {"close_pos": "Bottom", "wick": "Long Upper Wick", "color": "Red",   "short": True,  "long": False},
    "Bearish Pin Bar":    {"close_pos": "Bottom", "wick": "Long Upper Wick", "color": None,    "short": True,  "long": False},
    "Buying Climax":      {"price_pos": "Top",    "vol": ["Very High", "High"], "spread": "Wide", "close_pos": "Bottom", "short": True, "long": False},
    "Healthy Down Move":  {"color": "Red",  "vol": ["Very High", "High", "Normal"], "close_pos": "Bottom", "spread": "Wide",   "short": True,  "long": False},
    # Long-friendly patterns
    "Hammer":             {"close_pos": "Top",    "wick": "Long Lower Wick", "color": "Green", "short": False, "long": True},
    "Bullish Pin Bar":    {"close_pos": "Top",    "wick": "Long Lower Wick", "color": None,    "short": False, "long": True},
    "Selling Climax":     {"price_pos": "Bottom", "vol": ["Very High", "High"], "spread": "Wide", "close_pos": "Top",    "short": False, "long": True},
    "Healthy Up Move":    {"color": "Green", "vol": ["Very High", "High", "Normal"], "close_pos": "Top",    "spread": "Wide",   "short": False, "long": True},
    # Warning patterns (negative signal)
    "No Supply":          {"color": "Red",   "vol": ["Low", "Very Low"], "spread": "Narrow", "short": False, "long": False, "warn": True},
    "No Demand":          {"color": "Green", "vol": ["Low", "Very Low"], "spread": "Narrow", "short": False, "long": False, "warn": True},
    "Effort vs No Result":{"vol": ["Very High", "High"], "spread": "Narrow",                 "short": False, "long": False, "warn": True},
    "Doji":               {"wick": "Both",                                                    "short": False, "long": False, "neutral": True},
}


def detect_vsa_pattern(color: str, vol: str, spread: str,
                       close_pos: str, wick: str, price_pos: str) -> tuple[str, bool, bool]:
    """
    คืน (pattern_name, is_bullish, is_bearish)
    """
    def vol_match(rule_vol, actual_vol):
        if isinstance(rule_vol, list):
            return actual_vol in rule_vol
        return actual_vol == rule_vol

    for name, rule in PATTERNS.items():
        if "color"     in rule and rule["color"]     is not None and rule["color"]     != color:     continue
        if "vol"       in rule and not vol_match(rule["vol"], vol):                                  continue
        if "spread"    in rule and rule["spread"]    != spread:                                      continue
        if "close_pos" in rule and rule["close_pos"] != close_pos:                                  continue
        if "wick"      in rule and rule["wick"]      != wick:                                        continue
        if "price_pos" in rule and rule["price_pos"] != price_pos:                                   continue

        return name, rule.get("long", False), rule.get("short", False)

    return "No Pattern", False, False


# ---------------------------------------------------------------------------
# Main Function
# ---------------------------------------------------------------------------

def check_vsa(df_1d: pd.DataFrame, df_4h: pd.DataFrame, direction: str) -> dict:
    """
    Step 1 (Context)  — ดูจาก 1D: Trend + Price Position (10 แท่งหลัง)
    Step 2 (Pattern)  — ดูจาก 4H: แท่งปิดล่าสุด

    คืน:
        vsa_ok      : bool — ผ่านทั้ง Step 1 และ Step 2
        step1_ok    : bool
        step2_ok    : bool
        price_pos   : Top/Middle/Bottom (1D)
        pattern     : ชื่อ pattern (4H)
        color/vol/spread/close_pos/wick : ค่าจาก 4H
    """
    is_short = direction.capitalize() == "Short"

    # ── Step 1: Context จาก 1D ──────────────────────────────────────────
    idx_1d    = len(df_1d) - 2
    price_pos = _price_position(df_1d, idx_1d)

    # Short ต้องการ Top / Long ต้องการ Bottom
    if is_short:
        step1_ok = price_pos == "Top"
    else:
        step1_ok = price_pos == "Bottom"

    # ── Step 2: Pattern จาก 4H ──────────────────────────────────────────
    idx_4h    = len(df_4h) - 2   # แท่งปิดล่าสุด
    row       = df_4h.iloc[idx_4h]

    color     = "Green" if row["close"] >= row["open"] else "Red"
    vol       = _volume_class(df_4h, idx_4h)
    spread    = _spread_class(df_4h, idx_4h)
    close_pos = _close_position(row)
    wick      = _wick_side(row)
    price_pos_4h = _price_position(df_4h, idx_4h)

    pattern, is_long_ok, is_short_ok = detect_vsa_pattern(
        color, vol, spread, close_pos, wick, price_pos_4h
    )

    step2_ok = is_short_ok if is_short else is_long_ok
    vsa_ok   = step1_ok and step2_ok

    return {
        "vsa_ok":      vsa_ok,
        "step1_ok":    step1_ok,
        "step2_ok":    step2_ok,
        "price_pos":   price_pos,       # 1D context
        "pattern":     pattern,         # 4H pattern
        "color":       color,
        "vol":         vol,
        "spread":      spread,
        "close_pos":   close_pos,
        "wick":        wick,
        "price_pos_4h": price_pos_4h,
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    import MetaTrader5 as mt5
    from mt5_connect import connect
    from binance import merge_real_volume

    load_dotenv()
    connect()

    symbol    = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
    direction = sys.argv[2] if len(sys.argv) > 2 else "Short"

    rates_1d = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 50)
    df_1d    = pd.DataFrame(rates_1d)
    df_1d["time"] = pd.to_datetime(df_1d["time"], unit="s")
    df_1d    = merge_real_volume(df_1d, symbol, "1D")

    rates_4h = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 50)
    df_4h    = pd.DataFrame(rates_4h)
    df_4h["time"] = pd.to_datetime(df_4h["time"], unit="s")

    result = check_vsa(df_1d, df_4h, direction)

    print(f"\n{'=' * 40}")
    print(f"  VSA  |  {symbol}  |  {direction}")
    print(f"{'=' * 40}")
    print(f"  [Step 1 — 1D Context]")
    print(f"  Price Pos (1D) : {result['price_pos']}")
    print(f"  Step 1 OK      : {'✅' if result['step1_ok'] else '❌'}")
    print(f"{'─' * 40}")
    print(f"  [Step 2 — 4H Pattern]")
    print(f"  Color      : {result['color']}")
    print(f"  Volume     : {result['vol']}")
    print(f"  Spread     : {result['spread']}")
    print(f"  Close Pos  : {result['close_pos']}")
    print(f"  Wick       : {result['wick']}")
    print(f"  Price Pos  : {result['price_pos_4h']}")
    print(f"  Pattern    : {result['pattern']}")
    print(f"  Step 2 OK  : {'✅' if result['step2_ok'] else '❌'}")
    print(f"{'=' * 40}")
    print(f"  VSA OK     : {'✅ YES' if result['vsa_ok'] else '❌ NO'}")
    print(f"{'=' * 40}")

    mt5.shutdown()
