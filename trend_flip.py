"""
trend_flip.py — ตัวตัดสิน "ตอนนี้ตลาดเป็นขาขึ้นหรือขาลง" ที่ไวกว่า EMA cross แต่กัน noise
ด้วยรัศมีปลอดภัยที่ปรับตามความผันผวนจริง (ATR) — ไม่ได้ต่อกับระบบเทรดจริงเลย (ไฟล์แยก
ตามที่ขอ) มีไว้ทดลอง/หา k ที่เหมาะสมก่อน (ดู backtest_trend_flip_ksweep.py สำหรับขั้นตอน E)

หมายเหตุ ATR: โปรเจกต์นี้ไม่มีการเชื่อมต่อ TradingView เลย ATR คำนวณเองจาก swing.calc_atr()
(Wilder TR สูตรมาตรฐาน + EWM span=period) ซึ่งตอบสนองไวกว่า RMA ที่ TradingView ใช้เป็น
default ประมาณ 2 เท่า (alpha ต่างกัน) — เป็นสูตรเดียวกับที่ใช้ทั่วทั้งโปรเจกต์อยู่แล้ว
(SL buffer, swing tolerance) ไม่ได้เปลี่ยนเฉพาะที่นี่

=== 3 ชั้น ===
B — Fractal Swing (find_fractal_swings)
    SwingLow_i  = MIN(Low_{i-window}..Low_{i+window})   ยืนยันได้ที่แท่ง i+window
    SwingHigh_i = MAX(High_{i-window}..High_{i+window})  ยืนยันได้ที่แท่ง i+window
    เป็น fractal ดิบ ไม่มี volume/tolerance filter (ต่างจาก find_swing_highs/lows ใน
    swing.py ที่มี filter พวกนั้น) — ตั้งใจให้เรียบง่ายและไวตามสเปกที่ขอ

C — Ratchet (ล็อกทิศทาง)
    ขณะ Bull:  RefLow_t  = MAX(RefLow_{t-1}, SwingLow ล่าสุดที่ยืนยันแล้ว)   ขยับขึ้นอย่างเดียว
    ขณะ Bear:  RefHigh_t = MIN(RefHigh_{t-1}, SwingHigh ล่าสุดที่ยืนยันแล้ว)  ขยับลงอย่างเดียว

D — กฎพลิกเทรนด์
    Bull -> Bear:  Close_t < RefLow_t  - k * ATR_t
    Bear -> Bull:  Close_t > RefHigh_t + k * ATR_t
    พลิกแล้วเริ่ม track ฝั่งตรงข้ามใหม่จากจุดยืนยันล่าสุดที่มีตอนนั้น

ใช้: from trend_flip import compute_trend_regime
     result_df, flips = compute_trend_regime(df, k=0.3)
"""
import pandas as pd

from swing import calc_atr

DEFAULT_WINDOW = 5        # ตามสเปก B (i-5..i+5)
DEFAULT_ATR_PERIOD = 14
DEFAULT_K = 0.3


def find_fractal_swings(df: pd.DataFrame, window: int = DEFAULT_WINDOW) -> tuple[list[int], list[int]]:
    """B — Fractal swing ดิบ ไม่มี filter ใดๆ คืน (highs, lows) เรียงตามเวลา
    จุดที่ i จะถูกนับเป็น swing ก็ต่อเมื่อเป็นค่าสูงสุด/ต่ำสุดในหน้าต่าง [i-window, i+window]
    (ยืนยันได้จริงก็ต่อเมื่อมีข้อมูลถึงแท่ง i+window แล้ว — ผู้เรียกต้องเช็คเองถ้าทำ walk-forward)"""
    highs, lows = [], []
    n = len(df)
    for i in range(window, n - window):
        win_low  = df["low"].iloc[i - window:i + window + 1]
        win_high = df["high"].iloc[i - window:i + window + 1]
        if df["low"].iloc[i] == win_low.min():
            lows.append(i)
        if df["high"].iloc[i] == win_high.max():
            highs.append(i)
    return highs, lows


def compute_trend_regime(df: pd.DataFrame, k: float = DEFAULT_K,
                         window: int = DEFAULT_WINDOW,
                         atr_period: int = DEFAULT_ATR_PERIOD) -> tuple[pd.DataFrame, list[dict]]:
    """รวม B+C+D เป็นฟังก์ชันเดียว เดินไปทีละแท่งแบบ causal (รู้แค่ข้อมูลถึงแท่งนั้นจริงๆ
    ไม่แอบดูอนาคต) — สำคัญเพราะ swing ที่แท่ง i ต้องรอถึงแท่ง i+window ถึงจะ "รู้" ว่ายืนยันแล้ว

    Bootstrap (ก่อนมี regime ครั้งแรก): รอจนกว่าจะมีทั้ง swing high และ low ที่ยืนยันแล้ว
    อย่างละ 1 จุด แล้วเริ่ม regime จากฝั่งที่ราคาปิดตอนนั้น "ใกล้กว่า" (heuristic เริ่มต้น
    เท่านั้น ไม่กระทบผลระยะยาวเพราะจะพลิกตามกฎ D ทันทีที่เข้าเงื่อนไขจริง)

    คืน:
        (df ใหม่ที่มีคอลัมน์เพิ่ม regime/ref_low/ref_high, flips)
        flips = [{"idx", "time", "from", "to", "close", "level"}, ...]
    """
    atr = calc_atr(df, atr_period)
    highs, lows = find_fractal_swings(df, window)
    n = len(df)

    regime_arr   = [None] * n
    ref_low_arr  = [float("nan")] * n
    ref_high_arr = [float("nan")] * n
    flips: list[dict] = []

    ref_low = ref_high = None
    cur = None                              # "Bull" / "Bear" / None (ยังไม่เริ่ม)
    last_conf_high = last_conf_low = None   # จุดยืนยันล่าสุด ไม่ว่า regime ไหน (ใช้ bootstrap + reset ตอนพลิก)
    hi_ptr = lo_ptr = 0

    for t in range(n):
        # รับ swing ที่เพิ่งยืนยัน ณ แท่งนี้ (i + window <= t)
        while hi_ptr < len(highs) and highs[hi_ptr] + window <= t:
            price = df["high"].iloc[highs[hi_ptr]]
            last_conf_high = price
            if cur == "Bear":
                ref_high = price if ref_high is None else min(ref_high, price)
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr] + window <= t:
            price = df["low"].iloc[lows[lo_ptr]]
            last_conf_low = price
            if cur == "Bull":
                ref_low = price if ref_low is None else max(ref_low, price)
            lo_ptr += 1

        if cur is None and last_conf_high is not None and last_conf_low is not None:
            close = df["close"].iloc[t]
            cur = "Bull" if abs(close - last_conf_low) <= abs(close - last_conf_high) else "Bear"
            ref_low  = last_conf_low  if cur == "Bull" else None
            ref_high = last_conf_high if cur == "Bear" else None

        if cur is not None:
            close = df["close"].iloc[t]
            atr_t = atr.iloc[t]
            if cur == "Bull" and ref_low is not None and close < ref_low - k * atr_t:
                flips.append({"idx": t, "time": df["time"].iloc[t] if "time" in df.columns else None,
                             "from": "Bull", "to": "Bear", "close": close, "level": ref_low - k * atr_t})
                cur = "Bear"
                ref_high, ref_low = last_conf_high, None
            elif cur == "Bear" and ref_high is not None and close > ref_high + k * atr_t:
                flips.append({"idx": t, "time": df["time"].iloc[t] if "time" in df.columns else None,
                             "from": "Bear", "to": "Bull", "close": close, "level": ref_high + k * atr_t})
                cur = "Bull"
                ref_low, ref_high = last_conf_low, None

        regime_arr[t]   = cur
        ref_low_arr[t]  = ref_low if ref_low is not None else float("nan")
        ref_high_arr[t] = ref_high if ref_high is not None else float("nan")

    out = df.copy()
    out["regime"]   = regime_arr
    out["ref_low"]  = ref_low_arr
    out["ref_high"] = ref_high_arr
    return out, flips


if __name__ == "__main__":
    import sys
    from mt5_connect import connect
    from config import MT5_TIMEFRAMES
    import MetaTrader5 as mt5

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
    k      = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_K
    bars   = int(sys.argv[3]) if len(sys.argv) > 3 else 500

    connect()
    rates = mt5.copy_rates_from_pos(symbol, MT5_TIMEFRAMES["4H"], 0, bars)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.iloc[:len(df) - 1].reset_index(drop=True)   # ตัดแท่งยังไม่ปิด

    result, flips = compute_trend_regime(df, k=k)
    print(f"\n  {symbol}  k={k}  ({len(df)} แท่ง 4H)")
    print(f"  regime ปัจจุบัน: {result['regime'].iloc[-1]}")
    print(f"  จำนวน flip ทั้งหมด: {len(flips)}\n")
    for f in flips[-10:]:
        print(f"    {f['time']}  {f['from']} -> {f['to']}   close={f['close']:.2f}  level={f['level']:.2f}")
