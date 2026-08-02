import os
import sys
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
