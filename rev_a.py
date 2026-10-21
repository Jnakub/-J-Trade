"""
rev_a.py — Reversal แบบ A (2026-10-15 · config.REV_A_* · กลับตัวตามตำรา)

บ้านเดียวของกฎเข้า ทั้ง scheduler (ระบบจริง) และ backtest_replay --rev-a — แบบเดียวกับ pullback.py
ขั้นตอน: สัญญาณ 4H (signal_direction) -> เฝ้ายืนยันบนแท่ง 1H (confirm) -> limit ที่ระดับยืนยัน
ต้นขาเดิมของ TP = จุดเริ่มชุด lower high / higher low บน 4H (swing_leg) · ตัวเลข/หลักฐานทั้งหมดที่ config.REV_A_*
state ระหว่างรอ (เฝ้า/limit ค้าง) อยู่ใน scheduler (rev_a_state.json) และใน backtest_replay (_ra) — ไฟล์นี้ไม่จำอะไร
"""
import pandas as pd

from config import (REV_A_ADX_MIN_PEAK, REV_A_BREAK_BARS, REV_A_EXT_BACK_HOURS, REV_A_SL_BUFFER,
                    REV_A_TP_FIB, REV_A_MIN_RR, REV_A_LEG_MAX_SWINGS)

LEG_WINDOW_BARS = 120   # ต้นขาแบบหน้าต่าง (ก่อน 2026-10-15) — เหลือไว้ให้ backtest_replay --rev-a-window เท่านั้น


def signal_direction(regime_info: dict, bias: str | None, adx_ok: bool | None = None) -> tuple[str | None, str]:
    """ทิศไม้แบบ A (ตรงข้ามเทรนด์) หรือ (None, เหตุผล)
    regime_info = get_regime() · bias = get_trend_bias() (Bias 1D)
    adx_ok: None = กฎระบบจริง (ADX 4H peak ≥ REV_A_ADX_MIN_PEAK แล้วโค้งลง) · bool = ผลจากกฎทางเลือกของ replay"""
    if adx_ok is None:
        pk = regime_info.get("peak") or {}
        adx_ok = bool(pk.get("declining")) and (pk.get("peak") or 0) >= REV_A_ADX_MIN_PEAK
    if not adx_ok:
        return None, f"ADX 4H ยังไม่ peak ≥ {REV_A_ADX_MIN_PEAK:g} แล้วโค้งลง"
    sd = ((regime_info.get("structure") or {}).get("trend") or "")[:5].strip()
    if bias not in ("Long", "Short") or bias != sd:
        return None, f"Bias 1D {bias} ไม่ตรง Structure 4H {sd or '-'}"
    dv = (regime_info.get("divergence") or {}).get("divergence")
    if bias == "Short" and dv == "bullish":
        return "Long", "เทรนด์ Short (1D+4H) + bullish divergence"
    if bias == "Long" and dv == "bearish":
        return "Short", "เทรนด์ Long (1D+4H) + bearish divergence"
    return None, f"ไม่มี divergence สวนเทรนด์ ({dv or 'ไม่มี'})"


LEG_MAX_SWINGS = REV_A_LEG_MAX_SWINGS   # ค่าจาก config (ระบบจริง) · replay --rev-a-leg-max ทับได้ (0 = ไม่จำกัด)


def swing_leg(regime_info: dict, symbol: str, long_: bool) -> float | None:
    """ต้นขาเดิม: ไล่ swing ฝั่งตรงข้ามก่อนก้นของ divergence ย้อนหลังทีละตัว ตราบที่ตัวก่อนหน้ายังสูงกว่า (Long)
    / ต่ำกว่า (Short) -> ตัวสุดขั้วของชุด = จุดที่ขาเดิมเริ่ม · None = หาไม่ได้ (ทิ้งสัญญาณ)
    swing ใช้ find_swing_highs/lows ค่าตั้งเดียวกับ check_divergence บนแท่ง 4H ชุดเดียวกับที่มันเห็น"""
    import regime_check as rc        # import ตรงนี้: regime_check ลาก MetaTrader5 มาด้วย (ไฟล์นี้ต้อง import บน Mac ได้)
    from swing import find_swing_highs, find_swing_lows, swing_vol_multiplier, swing_wick_ratio_min
    pts = (regime_info.get("divergence") or {}).get("points") or []
    df4 = regime_info.get("df_4h")
    if len(pts) < 2 or df4 is None:
        return None
    d4 = df4.iloc[:len(df4) - 1].reset_index(drop=True)          # ตัดแท่งยังไม่ปิด = ชุดที่ check_divergence เห็น
    vm, wr = swing_vol_multiplier(symbol), swing_wick_ratio_min(symbol)
    if not rc.DIV_SWING_VOL_FILTER:
        vm, wr = 0.0, None
    lr, tol = rc.SWING_LEFT_RIGHT, rc.SWING_TOLERANCE
    if long_:
        opp = find_swing_highs(d4, left=lr, right=rc.DIV_SWING_RIGHT_HIGHS or lr, tolerance_atr=tol,
                               vol_multiplier=vm, wick_ratio_min=wr)
    else:
        opp = find_swing_lows(d4, left=lr, right=rc.DIV_SWING_RIGHT_LOWS or lr, tolerance_atr=tol,
                              vol_multiplier=vm, wick_ratio_min=wr)
    m = d4.index[d4["time"] == pts[1][0]]                       # ก้นของ divergence (l2 / h2)
    if not len(m):
        return None
    prev = [i for i in opp if i < m[0]]
    if not prev:
        return None
    px = d4["high"] if long_ else d4["low"]
    leg = float(px.iloc[prev[-1]])
    n = 1
    for i in reversed(prev[:-1]):
        v = float(px.iloc[i])
        if not ((v > leg) if long_ else (v < leg)):
            break
        if LEG_MAX_SWINGS and n >= LEG_MAX_SWINGS:
            break
        leg, n = v, n + 1
    return leg


def confirm(h1: pd.DataFrame, i_conf: int, i_sig: int, direction: str, leg: float | None,
            tp_fib: float = REV_A_TP_FIB, min_rr: float = REV_A_MIN_RR,
            deep: float = 0.0, tp_exec: float = 0.0, bk: int | None = None,
            entry_px: float | None = None) -> dict | None:
    """เช็คแท่ง 1H ที่ปิดแล้ว index i_conf ว่ายืนยันการกลับตัวไหม — None = ยังไม่ยืนยัน
    h1: แท่ง 1H เรียงเวลา (คอลัมน์ high/low/close · index 0..n) · i_sig = แท่งแรกที่นับว่า "หลังสัญญาณ"
    leg: ต้นขาจาก swing_leg · None = แบบหน้าต่าง LEG_WINDOW_BARS (replay --rev-a-window)
    คืน dict: ok (ผ่าน R:R + ระดับถูกฝั่ง) · entry (ราคา limit) · sl · tp · rr · lvl · ext · leg
    deep / tp_exec / bk มีไว้ให้ replay (--rev-a-deep / --rev-a-tpx / --rev-a-bk) · ระบบจริงใช้ค่า default
    entry_px: เข้า market ที่ราคานี้แทน limit ที่ระดับยืนยัน (replay --rev-a-market · R:R คิดจากราคานี้)"""
    bk = bk or REV_A_BREAK_BARS
    if i_conf < bk:
        return None
    long_ = direction == "Long"
    ref = h1.iloc[i_conf - bk:i_conf]
    c = float(h1["close"].iloc[i_conf])
    hi, lo = float(ref["high"].max()), float(ref["low"].min())
    if not ((c > hi) if long_ else (c < lo)):
        return None
    lvl = hi if long_ else lo
    seg = h1.iloc[max(0, i_sig - REV_A_EXT_BACK_HOURS):i_conf + 1]
    ext = float(seg["low"].min() if long_ else seg["high"].max())
    if leg is None:
        lseg = h1.iloc[max(0, i_sig - LEG_WINDOW_BARS):i_sig + 1]
        leg = float(lseg["high"].max() if long_ else lseg["low"].min())
    d = abs(lvl - ext)
    sl = ext - REV_A_SL_BUFFER * d if long_ else ext + REV_A_SL_BUFFER * d
    tp = ext + tp_fib * (leg - ext) if long_ else ext - tp_fib * (ext - leg)
    ok = d > 0 and ((lvl > ext and tp > lvl) if long_ else (lvl < ext and tp < lvl))
    e = entry_px if entry_px is not None else lvl - deep * (lvl - ext)
    ok = ok and ((tp > e > sl) if long_ else (tp < e < sl))
    rr = abs(tp - e) / abs(e - sl) if ok else 0.0
    out = {"lvl": lvl, "ext": ext, "leg": leg, "entry": e, "sl": sl, "tp": tp, "rr": rr}
    if not ok or rr < min_rr:
        why = "ระดับผิดฝั่ง" if not ok else f"R:R {rr:.2f} < {min_rr:g}"
        return {**out, "ok": False, "why": why}
    if tp_exec:
        out["tp"] = ext + tp_exec * (leg - ext) if long_ else ext - tp_exec * (ext - leg)
    return {**out, "ok": True, "why": ""}
