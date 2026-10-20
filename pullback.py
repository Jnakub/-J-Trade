"""
pullback.py — กลยุทธ์ Pullback (2026-10-13 · config.PULLBACK_ENABLED · เดิมเรียก "Reversal ใหม่")

เข้าตามเทรนด์ใหญ่ (Bias 1D) ตอนที่ 4H ย่อสวนจนหมดแรง — ไม่ใช่การกลับเทรนด์ จึงเปลี่ยนชื่อจาก Reversal (ผู้ใช้)
ไฟล์นี้เก็บแค่กฎเข้า (บ้านเดียวของ scheduler + backtest_replay) · SL/TP ใช้ reversal.compute_reversal_entry
ด้วยค่าของ Pullback (config.PULLBACK_*) · ไม้เงา (ด่านตำแหน่ง) อยู่ใน scheduler.pullback_ghost_* / replay._rn_ghost
ไม่ import MetaTrader5 — เทสต์บน Mac ได้
"""
import pandas as pd


def pullback_direction(regime_info: dict, bias: str | None,
                       adx_rule: str = "nofall", adx_min: float = 0.0,
                       require_key_level: bool = True,
                       require_divergence: bool = True,
                       min_disagree_bars: int = 0) -> tuple[str | None, str]:
    """ทิศไม้ Pullback หรือ (None, เหตุผล) — กฎเต็มที่ config.PULLBACK_ENABLED
    regime_info = get_regime() · bias = get_trend_bias() (Bias 1D)
    adx_rule: "nofall" (ระบบจริง · ห้าม ADX ลง) / "any" / "rising" / "declining" (peak แล้วโค้งลง) — ตัวอื่นมีไว้ให้ replay
    ใช้ .get ทุกคีย์โดยตั้งใจ: regime ที่ข้อมูลไม่ครบ = ไม่เข้า (เทสต์ scan_symbol ส่ง dict ไม่ครบมา)"""
    if bias not in ("Long", "Short"):
        return None, "หา Bias 1D ไม่ได้"
    struct = (regime_info.get("structure") or {}).get("trend") or ""
    if not (struct.startswith("Long") or struct.startswith("Short")):
        return None, "Structure 4H ไม่ชัด"
    if struct.startswith(bias):
        return None, f"Bias {bias} ตรง Structure (ทาง Scoring)"
    if min_disagree_bars:
        n = disagree_bars(regime_info, bias, min_disagree_bars)
        if n < min_disagree_bars:
            return None, (f"Structure 4H เพิ่งสวน Bias {n} แท่ง 4H (< {min_disagree_bars}) — การย่อเพิ่งเริ่ม "
                          f"(config.PULLBACK_MIN_DISAGREE_BARS)")
    adx_now, adx_dir = regime_info.get("adx_now"), regime_info.get("adx_direction")
    if adx_now is None or adx_dir is None:
        return None, "ไม่มีข้อมูล ADX"
    if not adx_now > adx_min:
        return None, f"ADX 4H {adx_now:.1f} ≤ {adx_min:g}"
    if adx_rule == "nofall" and adx_dir == "ลง":
        return None, "ADX 4H กำลังลง"
    if adx_rule == "rising" and adx_dir != "ขึ้น":
        return None, f"ADX 4H ไม่ได้ขึ้น ({adx_dir})"
    if adx_rule == "declining" and not (regime_info.get("peak") or {}).get("declining"):
        return None, "ADX 4H ไม่ได้โค้งลงจาก peak"
    if require_key_level and not (regime_info.get("key_level") or {}).get("at_key_level"):
        return None, "ไม่อยู่ที่ Key Level"
    need = "bullish" if bias == "Long" else "bearish"
    got = (regime_info.get("divergence") or {}).get("divergence")
    if require_divergence and got != need:
        return None, f"ไม่มี divergence {need} (เจอ {got})"
    return bias, (f"Bias 1D {bias} สวน Structure 4H {struct}"
                  + (f" + {need} divergence" if require_divergence else " (ไม่บังคับ divergence)"))


def disagree_bars(regime_info: dict, bias: str, need: int) -> int:
    """Structure 4H สวน Bias ติดกันมากี่แท่ง 4H ที่ปิดแล้ว (นับถึง need ก็พอ) — ใช้ check_structure_reg ตัวเดียวกับ get_regime
    บนแท่งชุดเดียวกัน (regime_info["df_4h"] ตัดแท่งที่ยังไม่ปิด) · Bias ใช้ค่าปัจจุบัน · "ไม่ชัด" = ไม่นับว่าสวน
    ที่มา (2026-10-20): ไม้ Pullback ที่เข้าภายใน 8 ชม.หลัง Structure เพิ่งพลิกสวน Bias แพ้ 11/13 ไม้ — ตอนนั้นการย่อเพิ่งเริ่ม"""
    import regime_check as rc                     # import ช้า: regime_check ลาก MetaTrader5 มาด้วย
    df = regime_info.get("df_4h")
    if df is None:
        return 0
    df = df.iloc[:len(df) - 1].reset_index(drop=True)
    other = "Short" if bias == "Long" else "Long"
    n = 0
    for i in range(need):
        st = rc.check_structure_reg(df.iloc[:len(df) - i].reset_index(drop=True), regime_info.get("symbol", ""))["trend"] \
            if rc.USE_STRUCT_REG else ""
        if not st.startswith(other):
            break
        n += 1
    return n


def range_position(bars: pd.DataFrame, price: float, direction: str) -> float:
    """ตำแหน่งราคาในกรอบ high/low ของ bars (scheduler: 24 แท่ง 1H ที่ปิดแล้ว รวมแท่งล่าสุด)
    Long: 0 = ก้นกรอบ (ยังไม่เด้ง) · 1 = ยอด · Short กลับด้าน · กรอบแบน = 0.5"""
    hi, lo = float(bars["high"].max()), float(bars["low"].min())
    if hi <= lo:
        return 0.5
    return (price - lo) / (hi - lo) if direction == "Long" else (hi - price) / (hi - lo)


