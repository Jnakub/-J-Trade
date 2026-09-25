import sys
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import journal
from config import (
    RISK_PER_TRADE, MAX_DAILY_LOSS,
    COOLDOWN_HOURS_BY_SYMBOL,
)
from mt5_connect import connect, get_account_balance
from order import calculate_lot_size, clamp_lot, place_order
from scoring import compute_entry
from exit_monitor import check_upcoming_news, NEWS_IMMINENT_H, NEWS_IMPACT, NEWS_CURRENCY


# ---------------------------------------------------------------------------
# Agent: Scanner
# ---------------------------------------------------------------------------

class ScannerAgent:
    def scan(self, symbol: str, direction: str,
             entry: float, sl: float = None, tp: float = None,
             force: bool = False) -> dict:
        # ไม่ผ่านด่าน (bias/SL/R:R/ระยะ TP) = raise ValueError — --force ข้ามด่านตัวเลขได้
        # (ด่าน bias ข้ามไม่ได้) · สกอร์การ์ดถูกลบแล้ว 2026-09-25 ดูเหตุผลที่ config.py
        return compute_entry(symbol, direction, entry, sl, tp, force)


# ---------------------------------------------------------------------------
# Agent: Risk Checker
# ---------------------------------------------------------------------------

class RiskCheckerAgent:
    def check(self, symbol: str, balance: float) -> tuple[bool, str]:
        # 0. sync journal กับ MT5 ก่อน — ไม้ที่ชน SL/TP เองยังค้างเป็น 'Open' อยู่ ถ้าไม่ sync
        #    daily loss guard ข้อถัดไปจะมองไม่เห็นการขาดทุนพวกนั้นเลย
        try:
            journal.reconcile_closed_positions()
        except Exception as exc:
            print(f"[journal] reconcile ล้มเหลว — {exc}")

        # 1. Daily loss guard
        if not journal.check_daily_loss(balance, MAX_DAILY_LOSS):
            return False, f"Daily loss limit ({MAX_DAILY_LOSS*100:.0f}%) reached for today"

        # 2. No open position for this symbol
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            return False, f"Already have {len(positions)} open position(s) for {symbol}"

        # 2a. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่นาน (ดู config.COOLDOWN_HOURS_BY_SYMBOL)
        cooldown_hours = COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)
        ok, reason = journal.check_cooldown(symbol, cooldown_hours)
        if not ok:
            return False, reason

        # 2b. News guard — ไม่เปิดไม้ใหม่ถ้าข่าว High Impact (USD) จะออกภายใน NEWS_IMMINENT_H ชม.
        has_news, news_detail, _ = check_upcoming_news(hours_ahead=NEWS_IMMINENT_H)
        if has_news:
            return False, f"ใกล้ข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ภายใน {NEWS_IMMINENT_H} ชม. — {news_detail}"

        # 3. Sufficient balance
        risk_amount = balance * RISK_PER_TRADE
        if balance < risk_amount:
            return False, (
                f"Balance {balance:.2f} insufficient "
                f"for risk amount {risk_amount:.2f}"
            )

        return True, "OK"


# ---------------------------------------------------------------------------
# Agent: Executor
# ---------------------------------------------------------------------------

class ExecutorAgent:
    def execute(self, symbol: str, direction: str,
                entry: float, sl: float, tp: float,
                lot: float) -> tuple[bool, int]:
        try:
            # 2026-08-09: place_order() บันทึก journal ให้เองแล้ว (ดู order.py) ไม่ต้องเรียกแยกอีก
            ticket = place_order(symbol, direction, entry, sl, tp, lot)
            return True, ticket
        except RuntimeError as exc:
            print(f"[ExecutorAgent] {exc}")
            return False, 0


# ---------------------------------------------------------------------------
# run_bot
# ---------------------------------------------------------------------------

def run_bot(symbol: str, direction: str,
            entry: float, sl: float = None, tp: float = None,
            force: bool = False) -> None:
    print(f"\n===== AUTO TRADER: {symbol} {direction} =====")

    load_dotenv()
    try:
        connect()
        balance = get_account_balance()
        print(f"  Account Balance : {balance:,.2f}")

        # Step 1: Risk Check
        risk = RiskCheckerAgent()
        ok, reason = risk.check(symbol, balance)
        if not ok:
            print(f"BLOCKED by Risk Checker: {reason}")
            return

        # Step 2: หา SL/TP อัตโนมัติถ้าไม่ได้กรอก + ตรวจด่าน (ไม่ผ่าน = ValueError ด้านล่าง)
        plan = ScannerAgent().scan(symbol, direction, entry, sl, tp, force)
        sl, tp = plan["sl"], plan["tp"]
        if plan.get("swing_price"):
            print(f"\n  SL (auto from structure)  Swing {plan['swing_price']}")
        print()
        print(f"{'=' * 48}")
        print(f"  {symbol}  |  {direction}  |  SL {sl}  TP {tp}  |  R:R {plan['rr']:.2f}")
        print(f"{'=' * 48}")

        # Step 3: Execute
        lot, _ = calculate_lot_size(symbol, entry, sl, balance, RISK_PER_TRADE)
        lot     = clamp_lot(symbol, lot)

        print(f"\n  >>> ส่ง order อัตโนมัติ <<<")
        executor = ExecutorAgent()
        success, ticket = executor.execute(symbol, direction, entry, sl, tp, lot)
        if success:
            print(f"\nOrder sent!  Ticket: #{ticket}")

    except (EnvironmentError, ConnectionError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv

    if len(args) not in (3, 5):
        print("Usage  : python bot.py <SYMBOL> <Long/Short> <entry>")
        print("         python bot.py <SYMBOL> <Long/Short> <entry> <sl> <tp>")
        print("         python bot.py <SYMBOL> <Long/Short> <entry> <sl> <tp> --force")
        print()
        print("  --force  ข้ามด่านระยะ SL / R:R / ระยะ TP (สำหรับทดสอบเท่านั้น)")
        sys.exit(1)

    if force:
        print("[WARNING] --force mode: ข้ามด่านระยะ SL / R:R / ระยะ TP")

    run_bot(
        args[0],
        args[1],
        float(args[2]),
        float(args[3]) if len(args) == 5 else None,
        float(args[4]) if len(args) == 5 else None,
        force=force,
    )
