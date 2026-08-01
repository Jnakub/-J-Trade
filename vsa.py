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
# 2026-08-01: check_vsa / check_vsa_trend (เส้นทาง Scoring) ถูกลบออก — VSA ไม่มีผลต่อ
# คะแนนใน scoring.py แล้ว (ตัดออกจาก scorecard 2026-07-27) และไม่มีใครเรียกใช้ผลอีก
# เส้นทาง Reversal ยังใช้ detect_vsa_pattern/PATTERNS ผ่าน exit_monitor.is_climax_bar
# (คนละ path ไม่กระทบกัน) — helper ด้านบนของไฟล์นี้จึงยังจำเป็นอยู่
# ---------------------------------------------------------------------------
