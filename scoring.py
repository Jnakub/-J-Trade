import sys
import os
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import (
    WEIGHT_TREND_1D, WEIGHT_OBV_1D,
    WEIGHT_TREND_4H, WEIGHT_OBV_4H,
    WEIGHT_TREND_1H, WEIGHT_OBV_1H,
    WEIGHT_VSA, WEIGHT_MACD, WEIGHT_RR, WEIGHT_CONFIRMATION,
    TOTAL_WEIGHT, MT5_TIMEFRAMES, MIN_RR, MIN_SCORE,
)
from mt5_connect import connect
from swing import (find_sl_from_structure, find_tp_from_fibonacci, check_confirmation,
                   find_swing_lows, find_swing_highs, swing_vol_multiplier, swing_wick_ratio_min)
from binance import merge_real_volume
from vsa import check_vsa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_ohlcv(symbol: str, timeframe, bars: int = 100) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงข้อมูล {symbol} ไม่ได้  [{code}] {msg}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_ohlcv_real(symbol: str, tf_name: str, bars: int = 100) -> pd.DataFrame:
    """get_ohlcv + แทนที่ volume ด้วย real volume (Bitstamp/COMEX ตาม merge_real_volume)
    ใช้ตัวนี้เสมอถ้า logic ปลายทางแตะ volume (swing filter, VSA ฯลฯ) — ไม่งั้น BTC จะได้
    tick_volume ของโบรกเกอร์ซึ่งไม่ตรงกับที่ระบบใช้หา SL จริง"""
    df = get_ohlcv(symbol, MT5_TIMEFRAMES[tf_name], bars=bars)
    return merge_real_volume(df, symbol, tf_name)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_obv(df: pd.DataFrame) -> pd.Series:
    sign = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (sign * df["tick_volume"]).cumsum()


def calc_macd(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """คืน (macd_line, signal_line, histogram)"""
    macd_line   = ema(df["close"], 12) - ema(df["close"], 26)
    signal_line = ema(macd_line, 9)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_ok_for_direction(macd_line: pd.Series, signal_line: pd.Series,
                          histogram: pd.Series, direction: str) -> bool:
    """
    Short:
      เส้น MACD + Signal อยู่ใต้ 0 → ดู Histogram ลดลง (momentum อ่อน) → PASS
      เส้น MACD + Signal อยู่เหนือ 0 → Histogram < 0 → PASS

    Long:
      เส้น MACD + Signal อยู่เหนือ 0 → ดู Histogram เพิ่มขึ้น (momentum แรง) → PASS
      เส้น MACD + Signal อยู่ใต้ 0 → Histogram > 0 → PASS
    """
    is_short    = direction.capitalize() == "Short"
    macd_val    = macd_line.iloc[-1]
    signal_val  = signal_line.iloc[-1]
    hist_now    = histogram.iloc[-2]   # แท่งที่ปิดแล้ว
    hist_prev   = histogram.iloc[-3]   # แท่งก่อนหน้าที่ปิดแล้ว

    if is_short:
        both_below_zero = macd_val < 0 and signal_val < 0
        if both_below_zero:
            return hist_now < hist_prev   # Histogram ลดลง = momentum อ่อนลง
        else:
            return hist_now < 0           # อยู่เหนือ 0 → แค่ Histogram ติดลบ
    else:
        both_above_zero = macd_val > 0 and signal_val > 0
        if both_above_zero:
            return hist_now > hist_prev   # Histogram เพิ่มขึ้น = momentum แรงขึ้น
        else:
            return hist_now > 0           # อยู่ใต้ 0 → แค่ Histogram ติดบวก


def calc_rr(entry: float, sl: float, tp: float, direction: str) -> float:
    if direction.lower() == "long":
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    return reward / risk if risk > 0 else 0.0


# ---------------------------------------------------------------------------
# Reusable scoring function (MT5 must already be initialized by caller)
# ---------------------------------------------------------------------------

def compute_score(symbol: str, direction: str, entry: float,
                  sl: float = None, tp: float = None,
                  force: bool = False) -> tuple[float, list, bool, dict]:
    """Return (total_score, criteria_list, passed, sl_info).
    ถ้าไม่ส่ง sl จะหาจาก swing structure อัตโนมัติ
    ถ้าไม่ส่ง tp จะคำนวณจาก SL × MIN_RR อัตโนมัติ
    """
    is_long = direction.capitalize() == "Long"

    df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
    df_1d = merge_real_volume(df_1d, symbol, "1D")
    df_4h = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=200)
    df_4h = merge_real_volume(df_4h, symbol, "4H")
    df_1h = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"])

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงราคา {symbol} ไม่ได้  [{code}] {msg}")
    price = tick.bid

    ema50_1d  = ema(df_1d["close"], 50).iloc[-1]
    ema200_1d = ema(df_1d["close"], 200).iloc[-1]
    ema50_4h  = ema(df_4h["close"], 50).iloc[-1]
    ema50_1h  = ema(df_1h["close"], 50).iloc[-1]

    # Bias check: EMA50 vs EMA200 บน 1D
    trend_bias = "Long" if ema50_1d > ema200_1d else "Short"
    if trend_bias != direction.capitalize():
        bias_label = "Downtrend (EMA50 < EMA200)" if trend_bias == "Short" else "Uptrend (EMA50 > EMA200)"
        raise ValueError(
            f"Direction ไม่ตรง Bias — กราฟ 1D เป็น {bias_label} "
            f"รับแค่ {trend_bias} เท่านั้น"
        )

    # Volume multiplier สำหรับหา Swing — ค่ากลางจาก swing.py (XAU 1.6x, อื่นๆ 1.9x)
    vol_multiplier = swing_vol_multiplier(symbol)
    wick_ratio_min = swing_wick_ratio_min(symbol)

    # หา SL อัตโนมัติจาก swing structure (4H) ถ้าไม่ได้กรอกมา
    sl_info = {}
    if sl is None:
        sl_info = find_sl_from_structure(df_4h, direction, left=4, right=4, tolerance_atr=0.25,
                                         vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if not sl_info.get("passed"):
            raise ValueError(f"หา SL ไม่ได้ — {sl_info.get('reason', 'unknown')}")
        sl = sl_info["sl"]

    # หา TP อัตโนมัติจาก Fibonacci (4H) ถ้าไม่ได้กรอกมา
    fib_info = {}
    if tp is None:
        swing_idx = sl_info.get("swing_idx")
        fib_info  = find_tp_from_fibonacci(df_4h, direction, swing_idx, left=4, right=4, tolerance_atr=0.25,
                                           vol_multiplier=vol_multiplier, wick_ratio_min=wick_ratio_min)
        if fib_info.get("passed"):
            tp = fib_info["levels"]["0.886"]   # 2026-07-23: เปลี่ยนจาก 0.786 — ยังไม่มี backtest ยืนยัน
        else:
            tp = (entry + abs(entry - sl) * MIN_RR) if is_long else (entry - abs(entry - sl) * MIN_RR)

    # VSA อัตโนมัติ — Step1=1D, Step2=4H
    vsa_result  = check_vsa(df_1d, df_4h, direction)
    vsa_ok      = vsa_result["vsa_ok"]

    # Confirmation อัตโนมัติ — ราคาทะลุ Swing Low/High บน 4H
    conf_result = check_confirmation(price, df_4h, direction, symbol)
    conf_ok     = conf_result["conf_ok"]

    obv_1d                          = calc_obv(df_1d)
    obv_4h                          = calc_obv(df_4h)
    obv_1h                          = calc_obv(df_1h)
    macd_line, signal_line, macd_hist = calc_macd(df_4h)
    rr                              = calc_rr(entry, sl, tp, direction)

    def obv_rising(obv: pd.Series, lookback: int = 10) -> bool:
        # เทียบ OBV ล่าสุดกับ 5 แท่งก่อน — จับ trend ไม่ใช่ noise
        return (obv.iloc[-1] > obv.iloc[-1 - lookback]) if is_long else (obv.iloc[-1] < obv.iloc[-1 - lookback])

    criteria = [
        ("Trend 1D",     (price > ema50_1d) if is_long else (price < ema50_1d), WEIGHT_TREND_1D),
        ("OBV 1D",       obv_rising(obv_1d),                                    WEIGHT_OBV_1D),
        ("Trend 4H",     (price > ema50_4h) if is_long else (price < ema50_4h), WEIGHT_TREND_4H),
        ("OBV 4H",       obv_rising(obv_4h),                                    WEIGHT_OBV_4H),
        ("Trend 1H",     (price > ema50_1h) if is_long else (price < ema50_1h), WEIGHT_TREND_1H),
        ("OBV 1H",       obv_rising(obv_1h, lookback=50),                       WEIGHT_OBV_1H),
        ("MACD 4H",      macd_ok_for_direction(macd_line, signal_line, macd_hist, direction), WEIGHT_MACD),
        ("R:R",          rr >= MIN_RR - 1e-9,                                    WEIGHT_RR),
        ("VSA",          vsa_ok,                                                 WEIGHT_VSA),
        ("Confirmation", conf_ok,                                                WEIGHT_CONFIRMATION),
    ]

    sl_info["sl"]           = sl
    sl_info["tp"]           = tp
    sl_info["fib_info"]     = fib_info
    sl_info["vsa_result"]   = vsa_result
    sl_info["conf_result"]  = conf_result

    # Hard block: R:R ต้องผ่านก่อนเสมอ — ไม่สนคะแนนรวม
    if rr < MIN_RR - 1e-9 and not force:
        raise ValueError(
            f"R:R = {rr:.2f} ต่ำกว่าขั้นต่ำ {MIN_RR}  — ห้ามเข้า trade"
        )

    total  = sum(w for _, passed, w in criteria if passed)
    passed = total >= MIN_SCORE
    return total, criteria, passed, sl_info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) not in (4, 6):
        print("Usage : python scoring.py <SYMBOL> <Long/Short> <entry>")
        print("        python scoring.py <SYMBOL> <Long/Short> <entry> <sl> <tp>")
        print("Example (auto SL/TP) : python scoring.py BTCUSDm Short 65000")
        print("Example (manual SL/TP): python scoring.py BTCUSDm Short 65000 67500 59000")
        sys.exit(1)

    symbol    = sys.argv[1]
    direction = sys.argv[2].capitalize()
    entry     = float(sys.argv[3])
    sl        = float(sys.argv[4]) if len(sys.argv) == 6 else None
    tp        = float(sys.argv[5]) if len(sys.argv) == 6 else None

    load_dotenv()

    try:
        connect()

        # ---- Bias preview -----------------------------------------------
        df_1d_bias  = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
        ema50_bias  = ema(df_1d_bias["close"], 50).iloc[-1]
        ema200_bias = ema(df_1d_bias["close"], 200).iloc[-1]
        bias        = "Long" if ema50_bias > ema200_bias else "Short"
        print(f"\n  Bias 1D : {bias}  (EMA50={ema50_bias:.2f}  EMA200={ema200_bias:.2f})")

        # ---- Scoring (อัตโนมัติทั้งหมด) ---------------------------------
        score, criteria, passed, sl_info = compute_score(
            symbol, direction, entry, sl, tp
        )

        sl    = sl_info["sl"]
        tp    = sl_info["tp"]
        rr    = calc_rr(entry, sl, tp, direction)
        price = mt5.symbol_info_tick(symbol).bid
        failed = [name for name, p, _ in criteria if not p]

        # ---- SL Detail -------------------------------------------------
        print()
        print(f"{'=' * 48}")
        if sl_info.get("swing_price"):
            auto = "(auto)" if len(sys.argv) == 4 else "(manual)"
            print(f"  SL {auto}")
            print(f"  Swing High   : {sl_info['swing_price']}")
            print(f"  ATR buffer   : {sl_info['atr']}")
            print(f"  Rejection    : {sl_info.get('rejection', '-')}")
            print(f"  Volume OK    : {sl_info.get('volume_ok', '-')}")
            print(f"  Structure OK : {sl_info.get('structure_ok', '-')}")
            print(f"  SL           : {sl}")

        conf = sl_info.get("conf_result", {})
        if conf:
            print(f"\n  Confirmation (auto)")
            print(f"  ราคาปัจจุบัน : {conf['current_price']}")
            print(f"  Key Level    : {conf['key_level']}")
            print(f"  Result       : {conf['reason']}")

        vsa = sl_info.get("vsa_result", {})
        if vsa:
            print(f"\n  VSA (auto)")
            print(f"  [Step 1 — 1D] Price Pos : {vsa['price_pos']}  →  {'✅' if vsa['step1_ok'] else '❌'}")
            print(f"  [Step 2 — 4H] Pattern   : {vsa['pattern']}  →  {'✅' if vsa['step2_ok'] else '❌'}")
            print(f"  Color {vsa['color']} | Vol {vsa['vol']} | Spread {vsa['spread']} | Close {vsa['close_pos']} | Wick {vsa['wick']}")
            print(f"  VSA OK     : {'✅ YES' if vsa['vsa_ok'] else '❌ NO'}")

        fib = sl_info.get("fib_info", {})
        if fib.get("passed"):
            print(f"\n  TP (Fibonacci)  จุด 0 = {fib['origin']}  Move = {fib['move']}")
            print(f"  TP1 (0.382)  : {fib['tp1']}")
            print(f"  TP2 (0.618)  : {fib['tp2']}")
            print(f"  TP (0.886)   : {fib['levels']['0.886']}  ← ใช้เป็น TP หลัก")
            print(f"  TP3 (1.000)  : {fib['tp3']}")
            print(f"  TP4 (1.618)  : {fib['tp4']}")
        else:
            print(f"  TP (R:R)     : {tp}")

        # ---- Score Display ---------------------------------------------
        print(f"{'=' * 48}")
        print(f"  {symbol}  |  {direction}  |  Entry {entry}")
        print(f"  Current Price: {price:.5f}   R:R = {rr:.2f}")
        print(f"{'=' * 48}")

        for name, p, weight in criteria:
            got    = weight if p else 0.0
            status = "PASS" if p else "FAIL"
            tag    = f"  (x{weight:.0f})" if weight > 1 else ""
            print(f"  {name:<15} {status:<5}  {got:.1f} / {weight:.1f}{tag}")

        print(f"{'-' * 48}")
        print(f"  Total Score  :  {score:.1f} / {TOTAL_WEIGHT:.0f}")
        print(f"  MIN_SCORE    :  {MIN_SCORE:.1f}")
        print(f"{'=' * 48}")

        if passed:
            print("  >>> ENTER TRADE <<<")
        else:
            print("  >>> NO ENTRY <<<")
            if failed:
                print(f"  ข้อที่ไม่ผ่าน: {', '.join(failed)}")

        print(f"{'=' * 48}")

    except ValueError as exc:
        print(f"[BLOCKED] {exc}")
    except (EnvironmentError, ConnectionError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
