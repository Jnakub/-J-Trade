"""
notify.py — ส่งข้อความแจ้งเตือนขึ้น Telegram (เปิด/ปิดไม้, ขยับ SL/TP, error สำคัญ)

ตั้งค่า TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID ใน .env — ถ้าไม่ได้ตั้งไว้ จะแค่ print เตือน
ครั้งเดียวแล้วข้ามการส่งทุกครั้งถัดไป (ไม่ throw error ไม่ให้กระทบการทำงานหลักของบอท)
"""
import os

import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SEC = 8

_warned_missing_config = False


def _get_credentials() -> tuple[str, str] | None:
    global _warned_missing_config
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        if not _warned_missing_config:
            print("[notify] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ไม่ได้ตั้งใน .env — ข้ามการแจ้งเตือน")
            _warned_missing_config = True
        return None
    return token, chat_id


def send(message: str) -> bool:
    """ส่งข้อความไป Telegram — คืน True ถ้าส่งสำเร็จ, False ถ้าส่งไม่ได้ (ไม่ raise error
    เพราะการแจ้งเตือนพังไม่ควรทำให้บอทเทรดหยุดทำงานตาม)"""
    creds = _get_credentials()
    if creds is None:
        return False
    token, chat_id = creds

    try:
        resp = requests.post(
            TELEGRAM_API_URL.format(token=token),
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=TIMEOUT_SEC,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[notify] ส่ง Telegram ไม่ได้ — {exc}")
        return False


# ---------------------------------------------------------------------------
# ข้อความสำเร็จรูปสำหรับเหตุการณ์หลักๆ ของบอท
# ---------------------------------------------------------------------------

def notify_order_opened(symbol: str, direction: str, entry: float,
                        sl: float, tp: float, lot: float, ticket: int,
                        score: float = None, is_demo: bool = True) -> None:
    account_tag = "DEMO" if is_demo else "⚠️ REAL"
    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
    score_line = f"\nScore: {score:.1f}" if score is not None else ""
    send(
        f"🟢 <b>เปิดไม้ใหม่</b> [{account_tag}]\n"
        f"{symbol}  {direction}\n"
        f"Entry: {entry:,.3f}\n"
        f"SL: {sl:,.3f}   TP: {tp:,.3f}\n"
        f"R:R: {rr:.2f}   Lot: {lot}"
        f"{score_line}\n"
        f"Ticket: #{ticket}"
    )


def notify_order_closed(symbol: str, direction: str, ticket: int,
                        result: str, net_pnl: float, is_demo: bool = True) -> None:
    account_tag = "DEMO" if is_demo else "⚠️ REAL"
    emoji = "✅" if net_pnl >= 0 else "❌"
    sign  = "+" if net_pnl >= 0 else ""
    send(
        f"{emoji} <b>ปิดไม้</b> [{account_tag}]\n"
        f"{symbol}  {direction}  ({result})\n"
        f"P/L: {sign}{net_pnl:.2f} USD\n"
        f"Ticket: #{ticket}"
    )


def notify_partial_close(symbol: str, ticket: int, close_vol: float,
                         keep_pct: float, is_demo: bool = True) -> None:
    account_tag = "DEMO" if is_demo else "⚠️ REAL"
    send(
        f"🟡 <b>ปิดบางส่วน</b> [{account_tag}]\n"
        f"{symbol}  ปิด {close_vol} lot (เหลือ {keep_pct}%)\n"
        f"Ticket: #{ticket}"
    )


def notify_sl_moved(symbol: str, ticket: int, new_sl: float, is_demo: bool = True) -> None:
    account_tag = "DEMO" if is_demo else "⚠️ REAL"
    send(f"🔵 [{account_tag}] {symbol} #{ticket} ขยับ SL -> {new_sl:,.3f}")


def notify_error(context: str, error: str) -> None:
    send(f"🔴 <b>ERROR</b>\n{context}\n{error}")


def notify_daily_summary(stats: dict) -> None:
    send(
        f"📊 <b>สรุปรายวัน</b>\n"
        f"เทรดทั้งหมด: {stats['total_trades']}\n"
        f"Win Rate: {stats['win_rate']:.1f}%\n"
        f"Total P&L: {stats['total_pnl']:+.2f} USD\n"
        f"Expectancy: {stats['expectancy']:+.2f} USD/เทรด"
    )
