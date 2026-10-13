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
                       require_key_level: bool = True) -> tuple[str | None, str]:
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
    if got != need:
        return None, f"ไม่มี divergence {need} (เจอ {got})"
    return bias, f"Bias 1D {bias} สวน Structure 4H {struct} + Key Level + {need} divergence"


def range_position(bars: pd.DataFrame, price: float, direction: str) -> float:
    """ตำแหน่งราคาในกรอบ high/low ของ bars (scheduler: 24 แท่ง 1H ที่ปิดแล้ว รวมแท่งล่าสุด)
    Long: 0 = ก้นกรอบ (ยังไม่เด้ง) · 1 = ยอด · Short กลับด้าน · กรอบแบน = 0.5"""
    hi, lo = float(bars["high"].max()), float(bars["low"].min())
    if hi <= lo:
        return 0.5
    return (price - lo) / (hi - lo) if direction == "Long" else (hi - price) / (hi - lo)


