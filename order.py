import sys
import math
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import RISK_PER_TRADE
from mt5_connect import connect, get_account_balance, get_tick_or_raise, get_position_or_raise, is_demo_account
import notify

MAGIC_NUMBER = 234000


# ---------------------------------------------------------------------------
# Lot size calculation
# ---------------------------------------------------------------------------

def _decimals_from_step(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def calculate_lot_size(symbol: str, entry: float, sl: float,
                       balance: float, risk_pct: float) -> tuple[float, int]:
    """Return (lot, decimal_places). ใช้ trade_contract_size จริงจาก MT5
    แทนการเดาจากชื่อ symbol — กันเดาผิดถ้า broker เปลี่ยน spec หรือเพิ่ม symbol ใหม่"""
    info = mt5.symbol_info(symbol)
    if info is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"หา symbol info ของ {symbol} ไม่ได้  [{code}] {msg}")

    risk_amount   = balance * risk_pct
    distance      = abs(entry - sl)
    contract_size = info.trade_contract_size
    decimals      = _decimals_from_step(info.volume_step)

    if "JPY" in symbol.upper():
        # คู่ที่ quote currency เป็น JPY ไม่ใช่ account currency (USD) — แปลงคร่าวๆ ด้วย entry
        raw_lot = risk_amount / (distance * contract_size / entry)
    else:
        raw_lot = risk_amount / (distance * contract_size)

    lot = math.floor(raw_lot * 10 ** decimals) / 10 ** decimals
    return lot, decimals


def clamp_lot(symbol: str, lot: float) -> float:
    """Snap lot to broker's min/max/step constraints."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return lot
    step     = info.volume_step
    min_lot  = info.volume_min
    max_lot  = info.volume_max
    lot = max(min_lot, min(max_lot, lot))
    lot = round(round(lot / step) * step, 10)
    return lot


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def _filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    mode = info.filling_mode
    if mode & mt5.ORDER_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if mode & mt5.ORDER_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def place_order(symbol: str, direction: str, entry: float,
                sl: float, tp: float, lot: float,
                comment: str = "auto-trader",
                score: float = 0.0, strategy: str = "Scoring",
                pinned_swing: float = None, pinned_atr_entry: float = None) -> int:
    # 2026-08-09: บันทึก journal ในนี้เสมอ (ดูท้ายฟังก์ชัน) แทนที่จะปล่อยให้ผู้เรียกแยกไปเรียก
    # journal.log_trade_open() เอง — เดิม scheduler.py/bot.py ต่างก็เรียกแยกหลัง place_order()
    # สำเร็จ ทำให้ไม้ที่เข้าจริงผ่านทางอื่น (เช่น dry-run test script ที่ดันผ่านเกณฑ์จริงแล้ว
    # ส่ง order จริงบนบัญชี demo) ไม่ถูกบันทึกเลยถ้าคนเขียนสคริปต์นั้นลืมเรียก journal เอง — เกิด
    # ขึ้นจริงแล้วครั้งหนึ่ง (ดู commit cde559c) จน journal เสียแถวไม้จริงไปเพราะเข้าใจผิดว่าเป็น
    # แค่ "dry-run ไม่ใช่ record ที่ควรเก็บ" ทั้งที่จริงเป็นไม้จริงบนบัญชี DEMO — ย้ายมาไว้จุดเดียว
    # ในนี้แทน รับประกันว่า order ที่ยิงสำเร็จทุกครั้งจะถูกบันทึกเสมอ ไม่ว่าใครจะเป็นคนเรียก

    # Hard guard: เปิดไม้อัตโนมัติได้เฉพาะบัญชี DEMO เท่านั้น — เดิม guard นี้มีแค่ฝั่ง
    # exit_monitor.execute_decision (ดูแลไม้ที่เปิดอยู่) ทำให้ไม่สมมาตร: ถ้าสลับ .env ไปบัญชีจริง
    # (ตั้งใจหรือพลาด) บอทจะยังเปิดไม้ใหม่ให้ได้ปกติ แต่ไม่มีการ trail SL / ปิดบางส่วน / จัดการ
    # ออกให้เลย เพราะฝั่งนั้นถูกบล็อกไปแล้ว — เข้าได้แต่ไม่มีใครดูแลออก 2026-08-02: เติม guard
    # ฝั่งนี้ให้ตรงกัน กันเงินจริงเสี่ยงแบบควบคุมไม่ได้
    if not is_demo_account():
        raise RuntimeError("ปฏิเสธ — บัญชีนี้ไม่ใช่ DEMO ห้ามเปิดไม้อัตโนมัติ")

    is_long    = direction.lower() == "long"
    order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL

    tick = get_tick_or_raise(symbol)
    price = tick.ask if is_long else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    20,
        "magic":        MAGIC_NUMBER,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(symbol),
    }

    result = mt5.order_send(request)

    if result is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"order_send() ล้มเหลว  [{code}] {msg}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"Order rejected  retcode={result.retcode}  {result.comment}"
        )

    print()
    print("  Order สำเร็จ!")
    print(f"  Ticket    : #{result.order}")
    print(f"  Symbol    : {symbol}  ({direction})")
    print(f"  Volume    : {lot}")
    print(f"  Price     : {result.price}")
    print(f"  SL        : {sl}   TP: {tp}")
    print(f"  Comment   : {comment}")

    import journal
    journal.log_trade_open(symbol, direction, result.price, sl, tp, lot, score, result.order,
                           pinned_swing=pinned_swing, pinned_atr_entry=pinned_atr_entry,
                           strategy=strategy)

    notify.notify_order_opened(symbol, direction, result.price, sl, tp, lot,
                               result.order, is_demo=is_demo_account())
    return result.order


# ---------------------------------------------------------------------------
# Close position
# ---------------------------------------------------------------------------

def close_order(ticket: int) -> None:
    pos       = get_position_or_raise(ticket)
    symbol    = pos.symbol
    lot       = pos.volume
    is_long   = pos.type == mt5.ORDER_TYPE_BUY
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
    direction = "Long" if is_long else "Short"

    tick = get_tick_or_raise(symbol)
    price = tick.bid if is_long else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC_NUMBER,
        "comment":      "close auto-trader",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(symbol),
    }

    result = mt5.order_send(request)

    if result is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"order_send() ล้มเหลว  [{code}] {msg}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"Close rejected  retcode={result.retcode}  {result.comment}"
        )

    print()
    print("  ปิด Position สำเร็จ!")
    print(f"  Ticket      : #{ticket}")
    print(f"  Symbol      : {symbol}  ({direction})")
    print(f"  Volume      : {lot}")
    print(f"  Close Price : {result.price}")
    print(f"  P/L         : {pos.profit:+.2f} USD")

    import journal
    result_label = "Manual Cut"
    try:
        journal.log_trade_close(ticket, result_label, round(pos.profit, 2))
    except ValueError:
        journal.log_trade_open(symbol, direction, pos.price_open,
                               pos.sl, pos.tp, lot, 0.0, ticket)
        journal.log_trade_close(ticket, result_label, round(pos.profit, 2))

    notify.notify_order_closed(symbol, direction, ticket, result_label,
                               round(pos.profit, 2), is_demo=is_demo_account())


# ---------------------------------------------------------------------------
# Partial close (ปิดบางส่วน)
# ---------------------------------------------------------------------------

def partial_close_order(ticket: int, close_volume: float, comment: str = "partial exit auto") -> None:
    pos     = get_position_or_raise(ticket)
    symbol  = pos.symbol
    is_long = pos.type == mt5.ORDER_TYPE_BUY
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY

    if close_volume <= 0 or close_volume >= pos.volume:
        raise ValueError(
            f"close_volume ต้องมากกว่า 0 และน้อยกว่า volume เดิม ({pos.volume})"
        )

    tick = get_tick_or_raise(symbol)
    price = tick.bid if is_long else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       close_volume,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC_NUMBER,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(symbol),
    }

    result = mt5.order_send(request)

    if result is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"order_send() ล้มเหลว  [{code}] {msg}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"Partial close rejected  retcode={result.retcode}  {result.comment}"
        )

    print()
    print("  ปิดบางส่วนสำเร็จ!")
    print(f"  Ticket      : #{ticket}")
    print(f"  Symbol      : {symbol}")
    print(f"  Volume ปิด  : {close_volume}  (เหลือ {pos.volume - close_volume})")
    print(f"  Close Price : {result.price}")

    keep_pct = round((pos.volume - close_volume) / pos.volume * 100, 1)
    notify.notify_partial_close(symbol, ticket, close_volume, keep_pct, is_demo=is_demo_account())


# ---------------------------------------------------------------------------
# Modify SL/TP (ขยับ Stop Loss / Take Profit)
# ---------------------------------------------------------------------------

def modify_sltp(ticket: int, new_sl: float = None, new_tp: float = None) -> None:
    pos = get_position_or_raise(ticket)
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol":   pos.symbol,
        "sl":       new_sl if new_sl is not None else pos.sl,
        "tp":       new_tp if new_tp is not None else pos.tp,
    }

    result = mt5.order_send(request)

    if result is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"order_send() ล้มเหลว  [{code}] {msg}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"Modify SL/TP rejected  retcode={result.retcode}  {result.comment}"
        )

    print()
    print("  ขยับ SL/TP สำเร็จ!")
    print(f"  Ticket : #{ticket}")
    print(f"  SL ใหม่: {request['sl']}   TP ใหม่: {request['tp']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if "--close" in sys.argv:
        idx = sys.argv.index("--close")
        if idx + 1 >= len(sys.argv):
            print("Usage  : python order.py --close <ticket>")
            sys.exit(1)
        ticket = int(sys.argv[idx + 1])
        load_dotenv()
        try:
            connect()
            close_order(ticket)
        except (EnvironmentError, ConnectionError, RuntimeError, ValueError) as exc:
            print(f"[ERROR] {exc}")
        finally:
            mt5.shutdown()
        return

    args    = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    if len(args) != 5:
        print("Usage  : python order.py <SYMBOL> <Long/Short> <entry> <sl> <tp> [--dry-run]")
        print("         python order.py --close <ticket>")
        print("Example: python order.py BTCUSDm Long 76500 75000 80000 --dry-run")
        print("Example: python order.py --close 3196695798")
        sys.exit(1)

    symbol    = args[0]
    direction = args[1].capitalize()
    entry     = float(args[2])
    sl        = float(args[3])
    tp        = float(args[4])
    is_long   = direction == "Long"

    load_dotenv()

    try:
        connect()
        balance       = get_account_balance()
        lot, decimals = calculate_lot_size(symbol, entry, sl,
                                           balance, RISK_PER_TRADE)
        risk_amount   = balance * RISK_PER_TRADE

        # reward ประมาณจาก lot × distance_tp (ใช้หน่วยเดียวกับ risk)
        risk_distance = abs(entry - sl)
        tp_distance   = abs(tp - entry)
        reward_amount = risk_amount * (tp_distance / risk_distance) if risk_distance else 0

        print()
        print("=" * 45)
        label = "DRY RUN" if dry_run else "Order Preview"
        print(f"  {label}: {symbol}  {direction}")
        print("=" * 45)
        print(f"  Entry      : {entry}")
        print(f"  SL         : {sl}")
        print(f"  TP         : {tp}")
        print(f"  Lot Size   : {lot:.{decimals}f}")
        print(f"  Risk       : ${risk_amount:,.2f}  "
              f"({RISK_PER_TRADE*100:.0f}% of ${balance:,.0f})")
        print(f"  Reward     : ${reward_amount:,.2f}")
        print(f"  R:R        : {tp_distance/risk_distance:.2f}" if risk_distance else "  R:R        : N/A")
        print("=" * 45)

        if dry_run:
            print("  [DRY RUN] ไม่ได้ส่ง order จริง")
            return

        confirm = input("ยืนยันส่ง order? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("ยกเลิก — ไม่ได้ส่ง order")
            return

        lot = clamp_lot(symbol, lot)
        place_order(symbol, direction, entry, sl, tp, lot)

    except (EnvironmentError, ConnectionError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
