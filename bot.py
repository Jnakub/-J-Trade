import sys
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import journal
from config import (
    ACCOUNT_BALANCE, RISK_PER_TRADE, MAX_DAILY_LOSS,
    MIN_SCORE, TOTAL_WEIGHT,
)
from mt5_connect import connect
from order import calculate_lot_size, clamp_lot, place_order
from scoring import compute_score, calc_rr


# ---------------------------------------------------------------------------
# Agent: Scanner
# ---------------------------------------------------------------------------

class ScannerAgent:
    def scan(self, symbol: str, direction: str,
             entry: float, sl: float = None, tp: float = None,
             force: bool = False) -> tuple[float, dict, bool, float, float]:
        score, criteria, passed, sl_info = compute_score(
            symbol, direction, entry, sl, tp, force
        )

        sl = sl_info["sl"]
        tp = sl_info["tp"]

        details = {
            "score":    score,
            "criteria": criteria,
            "failed":   [name for name, p, _ in criteria if not p],
            "rr":       calc_rr(entry, sl, tp, direction),
            "sl_info":  sl_info,
        }
        return score, details, passed, sl, tp


# ---------------------------------------------------------------------------
# Agent: Risk Checker
# ---------------------------------------------------------------------------

class RiskCheckerAgent:
    def check(self, symbol: str) -> tuple[bool, str]:
        # 1. Daily loss guard
        if not journal.check_daily_loss(ACCOUNT_BALANCE, MAX_DAILY_LOSS):
            return False, f"Daily loss limit ({MAX_DAILY_LOSS*100:.0f}%) reached for today"

        # 2. No open position for this symbol
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            return False, f"Already have {len(positions)} open position(s) for {symbol}"

        # 3. Sufficient balance
        account = mt5.account_info()
        if account is None:
            code, msg = mt5.last_error()
            return False, f"Cannot get account info  [{code}] {msg}"
        risk_amount = ACCOUNT_BALANCE * RISK_PER_TRADE
        if account.balance < risk_amount:
            return False, (
                f"Balance {account.balance:.2f} insufficient "
                f"for risk amount {risk_amount:.2f}"
            )

        return True, "OK"


# ---------------------------------------------------------------------------
# Agent: Executor
# ---------------------------------------------------------------------------

class ExecutorAgent:
    def execute(self, symbol: str, direction: str,
                entry: float, sl: float, tp: float,
                lot: float, score: float) -> tuple[bool, int]:
        try:
            ticket = place_order(symbol, direction, entry, sl, tp, lot)
            journal.log_trade_open(symbol, direction, entry, sl, tp, lot, score, ticket)
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

        # Step 1: Risk Check
        risk = RiskCheckerAgent()
        ok, reason = risk.check(symbol)
        if not ok:
            print(f"BLOCKED by Risk Checker: {reason}")
            return

        # Step 2: Scan + Score (หา SL/TP อัตโนมัติถ้าไม่ได้กรอก)
        scanner = ScannerAgent()
        score, details, passed, sl, tp = scanner.scan(symbol, direction, entry, sl, tp, force)

        # แสดง SL detail ถ้าหามาจาก swing
        sl_info = details.get("sl_info", {})
        if sl_info.get("swing_price"):
            print(f"\n  SL (auto from structure)")
            print(f"  Swing High   : {sl_info['swing_price']}")
            print(f"  Rejection    : {sl_info.get('rejection', '-')}")
            print(f"  Volume OK    : {sl_info.get('volume_ok', '-')}")
            print(f"  Structure OK : {sl_info.get('structure_ok', '-')}")
            print(f"  SL           : {sl}   TP : {tp}")

        print()
        print(f"{'=' * 48}")
        print(f"  {symbol}  |  {direction}  |  R:R {details['rr']:.2f}")
        print(f"{'=' * 48}")
        for name, p, weight in details["criteria"]:
            got    = weight if p else 0.0
            status = "PASS" if p else "FAIL"
            tag    = f"  (x{weight:.0f})" if weight > 1 else ""
            print(f"  {name:<15} {status:<5}  {got:.1f} / {weight:.1f}{tag}")
        print(f"  {'-'*44}")
        print(f"  Total Score  :  {score:.1f} / {TOTAL_WEIGHT:.0f}  (min {MIN_SCORE:.0f})")
        print(f"{'=' * 48}")

        if not passed:
            if force:
                print(f"[--force] ข้าม score check — Score {score:.1f}/{TOTAL_WEIGHT:.0f}")
            else:
                print(f"NO ENTRY — Score {score:.1f}/{TOTAL_WEIGHT:.0f} (ต่ำกว่า {MIN_SCORE})")
                if details["failed"]:
                    print(f"ข้อที่ไม่ผ่าน: {', '.join(details['failed'])}")
                return

        # Step 3: Execute
        lot, _ = calculate_lot_size(symbol, entry, sl, ACCOUNT_BALANCE, RISK_PER_TRADE)
        lot     = clamp_lot(symbol, lot)

        print(f"\n  >>> ส่ง order อัตโนมัติ <<<")
        executor = ExecutorAgent()
        success, ticket = executor.execute(symbol, direction, entry, sl, tp, lot, score)
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
        print("  --force  ข้ามการเช็ค score และ R:R (สำหรับทดสอบเท่านั้น)")
        sys.exit(1)

    if force:
        print("[WARNING] --force mode: ข้าม score และ R:R check")

    run_bot(
        args[0],
        args[1],
        float(args[2]),
        float(args[3]) if len(args) == 5 else None,
        float(args[4]) if len(args) == 5 else None,
        force=force,
    )
