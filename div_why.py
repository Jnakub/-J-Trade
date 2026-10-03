"""div_why.py — "Divergence ติดตรงไหน" ตอบด้วยของที่โค้ดคำนวณอยู่แล้ว ไม่ใช่การเดา

check_divergence สร้าง result["fail"] ไว้เพื่อตอบคำถามนี้โดยเฉพาะ (regime_check.py:556)
แต่ไม่มีใครพิมพ์มันออกมาเลย — run_check พิมพ์แค่ divergence["detail"] ซึ่งเป็นข้อความรวม
("ไม่พบ divergence") ที่ไม่บอกว่าเงื่อนไข AND 4 ข้อ ตัวไหนคือตัวที่ตก

ใช้:  ./run_wine.sh div_why.py USDJPYm
      ./run_wine.sh div_why.py                 (ทุก symbol ใน config.SYMBOLS)

🔴 สคริปต์นี้ **อ่านอย่างเดียว** ไม่แตะการตัดสินใจ ไม่เปลี่ยนค่าคงที่ใดๆ
   จูนอะไรก็ตามหลังจากนี้ ยังต้องวัดด้วย replay เต็มตามกติกาข้อ 4 (divergence เปลี่ยนชุดไม้)
"""
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
import MetaTrader5 as mt5

from mt5_connect import connect
from config import SYMBOLS
import regime_check as rc


def report(symbol: str) -> None:
    df = rc.get_adx_bars(symbol, bars=rc.BARS)
    div = rc.check_divergence(df, symbol=symbol)

    print(f"\n{'='*64}\n  {symbol}")
    print(f"{'-'*64}")
    if div["divergence"]:
        print(f"  ✅ เจอ {div['divergence']} — {div['detail']}")
        return

    print(f"  ❌ ไม่เจอ — เงื่อนไขที่ตก (AND ทุกข้อต้องผ่านพร้อมกัน):")
    for f in div["fail"] or ["(ไม่มีรายการ — แปลว่าไม่มีคู่ swing ให้ตรวจเลยทั้งสองฝั่ง)"]:
        print(f"      · {f}")

    # ค่าคงที่ที่คุมแต่ละข้อ — ไว้ดูคู่กับรายการข้างบนว่าปุ่มไหนตรงกับอะไร
    print(f"{'-'*64}")
    print(f"  ค่าที่ใช้อยู่: RSI({rc.DIV_RSI_PERIOD}) · โซน {rc.DIV_ZONE_OVERSOLD}/"
          f"{rc.DIV_ZONE_OVERBOUGHT} · อายุ<={rc.DIV_MAX_AGE_BARS} แท่ง · "
          f"stall<={rc.DIV_STALL_THRESHOLD} · ห่าง>={rc.DIV_MIN_SPACING_BARS} แท่ง")
    print(f"  swing filter: vol={rc.DIV_SWING_VOL_FILTER}  wick ทุก symbol={rc.DIV_WICK_ALL_SYMBOLS}")


def main() -> None:
    load_dotenv()
    symbols = [a for a in sys.argv[1:] if not a.startswith("-")] or SYMBOLS
    connect()
    try:
        for s in symbols:
            try:
                report(s)
            except Exception as exc:
                print(f"  [{s}] ERROR — {exc}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
