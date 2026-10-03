import os
import sys
from datetime import datetime, timezone
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Windows terminal อาจใช้ cp1252 — บังคับ UTF-8 เพื่อรองรับภาษาไทย
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

LOGIN   = os.getenv("MT5_LOGIN")
PASSWD  = os.getenv("MT5_PASSWORD")
SERVER  = os.getenv("MT5_SERVER")

from config import SYMBOLS


def connect() -> bool:
    if not all([LOGIN, PASSWD, SERVER]):
        raise EnvironmentError(
            "ไม่พบ MT5_LOGIN / MT5_PASSWORD / MT5_SERVER ใน .env — กรุณากรอกค่าให้ครบ"
        )

    if not LOGIN.isdigit():
        raise EnvironmentError(
            f"MT5_LOGIN ต้องเป็นตัวเลข Account Number เท่านั้น (ค่าปัจจุบัน: '{LOGIN}')"
        )

    if not mt5.initialize(login=int(LOGIN), password=PASSWD, server=SERVER):
        code, msg = mt5.last_error()
        raise ConnectionError(f"mt5.initialize() ล้มเหลว  [{code}] {msg}")

    # ใส่ทุก symbol เข้า Market Watch — symbol ที่ไม่อยู่ในนั้น symbol_info_tick/copy_rates คืน None
    # ("[-4] Terminal: Not found") แล้ว scan ข้ามไปเงียบๆ · เจอจริง 2026-10-03 บน VPS ที่ลง MT5 ใหม่:
    # 5/10 symbol หาย (XAU/USDJPY/US500/GBPCHF/AUDNZD) ส่วนบน Mac ไม่เคยเจอเพราะเคยเปิดไว้ด้วยมือ
    # ไม่ raise — symbol เดียวที่โบรกถอดไม่ควรหยุดอีก 9 ตัว
    for symbol in SYMBOLS:
        if not mt5.symbol_select(symbol, True):
            code, msg = mt5.last_error()
            print(f"[WARN] ใส่ {symbol} เข้า Market Watch ไม่ได้  [{code}] {msg}")

    return True


def print_account_info() -> None:
    info = mt5.account_info()
    if info is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึง account info ไม่ได้  [{code}] {msg}")

    print(f"Account  : {info.name}  (#{info.login})")
    print(f"Balance  : {info.balance:,.2f} {info.currency}")
    print(f"Equity   : {info.equity:,.2f} {info.currency}")


def get_account_balance() -> float:
    """ดึงยอดเงินจริงจากบัญชี MT5 ที่ connect() อยู่ (ต้องเรียก connect() ก่อนเสมอ)."""
    info = mt5.account_info()
    if info is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึง account balance ไม่ได้  [{code}] {msg}")
    return info.balance


def is_demo_account() -> bool:
    """เช็คว่าบัญชีที่ connect() อยู่เป็น DEMO หรือไม่ — ใช้เป็น hard guard ก่อนสั่งเทรดอัตโนมัติ
    (exit_monitor.execute_decision) และเป็น label ในข้อความแจ้งเตือน (notify.py)"""
    info = mt5.account_info()
    return info is not None and info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO


def mt5_now() -> datetime:
    """"ตอนนี้" บนนาฬิกาเดียวกับเวลาที่ MT5 คืนมา (pos.time, deal.time, คอลัมน์ time ของแท่ง)
    — naive datetime เพื่อลบกับ `pd.to_datetime(pos.time, unit="s")` ได้ตรงๆ

    🔴 2026-09-24: เดิม exit_monitor ใช้ `datetime.now()` (เวลาเครื่อง = UTC+7) ลบกับเวลาเข้าไม้
    จาก MT5 (UTC) → เวลาที่ถือไม้ในระบบจริง**เกินไป 7 ชม. ทุกไม้** (HK50 #4741691730 รายงาน 3.3 วัน
    ทั้งที่ถือจริง 2.97) ผลจริง: กฎ slow trade ตัด HK50 ครึ่งไม้ตอน 00:49 ทั้งที่ถือจริงแค่ 2.73
    วันทำการ  ถ้านับถูกมันจะยิง 07:13 ซึ่งเลยเวลารีสตาร์ทที่โหลดโค้ดปิดกฎนั้นไปแล้ว
    backtest ไม่โดนเพราะ as_of มาจากเวลาแท่งซึ่งเป็นนาฬิกาเดียวกับ entry อยู่แล้ว

    ใช้ UTC ได้เพราะ**เซิร์ฟเวอร์ Exness = UTC+0** — ตรวจจากดีลจริง: deal.time 1789949607 =
    2026-09-21 00:13:27 UTC ตรงกับ trades_log 07:13:27 (เวลาเครื่อง +7) เป๊ะ
    (check_recent_news กับ binance.py ก็ถือว่า MT5 = UTC อยู่แล้ว) ย้ายโบรกเมื่อไหร่ต้องตรวจใหม่"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_tick_or_raise(symbol: str):
    """ดึง tick ราคาปัจจุบันของ symbol — raise RuntimeError ถ้าดึงไม่ได้ (แทนการเช็ค
    None ซ้ำๆ ทุกจุดที่ต้องใช้ราคา)"""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงราคา {symbol} ไม่ได้  [{code}] {msg}")
    return tick


def get_position_or_raise(ticket: int):
    """ดึง position จาก ticket — raise RuntimeError ถ้าหาไม่เจอ (แทนการเช็ค None
    ซ้ำๆ ทุกจุดที่ต้องแก้ไข/ปิด position ที่มีอยู่)"""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        code, msg = mt5.last_error()
        raise RuntimeError(f"หา position ticket #{ticket} ไม่เจอ  [{code}] {msg}")
    return positions[0]


def print_prices(symbols: list[str]) -> None:
    print()
    for symbol in symbols:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            code, msg = mt5.last_error()
            print(f"  {symbol:<10}  ดึงราคาไม่ได้  [{code}] {msg}")
        else:
            print(f"  {symbol:<10}  Bid: {tick.bid:.5f}   Ask: {tick.ask:.5f}")


def main() -> None:
    try:
        connect()
        print("เชื่อมต่อ MT5 สำเร็จ")
        print("-" * 40)
        print_account_info()
        print_prices(SYMBOLS)

    except (EnvironmentError, ConnectionError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()
        print("\nปิดการเชื่อมต่อ MT5 แล้ว")


if __name__ == "__main__":
    main()
