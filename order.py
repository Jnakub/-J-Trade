import sys
import math
import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import ACCOUNT_BALANCE, RISK_PER_TRADE
from mt5_connect import connect


# ---------------------------------------------------------------------------
# Lot size calculation
# ---------------------------------------------------------------------------

def calculate_lot_size(symbol: str, entry: float, sl: float,
                       balance: float, risk_pct: float) -> tuple[float, int]:
    """Return (lot, decimal_places)."""
    risk_amount  = balance * risk_pct
    sym_upper    = symbol.upper()
    distance     = abs(entry - sl)

    if any(x in sym_upper for x in ["BTC", "ETH"]):
        risk_distance_pct = distance / entry
        raw_lot   = risk_amount / (risk_distance_pct * entry)
        decimals  = 3
    elif "XAU" in sym_upper or "GOLD" in sym_upper:
        raw_lot   = risk_amount / (distance * 100)
        decimals  = 2
    elif "JPY" in sym_upper:
        raw_lot   = risk_amount / (distance * 100000 / entry)
        decimals  = 2
    else:
        raw_lot   = risk_amount / (distance * 100000)
        decimals  = 2

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
                comment: str = "auto-trader") -> int:
    is_long    = direction.lower() == "long"
    order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงราคา {symbol} ไม่ได้  [{code}] {msg}")

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
        "magic":        234000,
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
    return result.order


# ---------------------------------------------------------------------------
# Close position
# ---------------------------------------------------------------------------

def close_order(ticket: int) -> None:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        code, msg = mt5.last_error()
        raise RuntimeError(f"หา position ticket #{ticket} ไม่เจอ  [{code}] {msg}")

    pos       = positions[0]
    symbol    = pos.symbol
    lot       = pos.volume
    is_long   = pos.type == mt5.ORDER_TYPE_BUY
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
    direction = "Long" if is_long else "Short"

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงราคา {symbol} ไม่ได้  [{code}] {msg}")

    price = tick.bid if is_long else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        234000,
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


# ---------------------------------------------------------------------------
# Partial close (ปิดบางส่วน)
# ---------------------------------------------------------------------------

def partial_close_order(ticket: int, close_volume: float, comment: str = "partial exit auto") -> None:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        code, msg = mt5.last_error()
        raise RuntimeError(f"หา position ticket #{ticket} ไม่เจอ  [{code}] {msg}")

    pos     = positions[0]
    symbol  = pos.symbol
    is_long = pos.type == mt5.ORDER_TYPE_BUY
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY

    if close_volume <= 0 or close_volume >= pos.volume:
        raise ValueError(
            f"close_volume ต้องมากกว่า 0 และน้อยกว่า volume เดิม ({pos.volume})"
        )

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise RuntimeError(f"ดึงราคา {symbol} ไม่ได้  [{code}] {msg}")
    price = tick.bid if is_long else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       close_volume,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        234000,
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


# ---------------------------------------------------------------------------
# Modify SL/TP (ขยับ Stop Loss / Take Profit)
# ---------------------------------------------------------------------------

def modify_sltp(ticket: int, new_sl: float = None, new_tp: float = None) -> None:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        code, msg = mt5.last_error()
        raise RuntimeError(f"หา position ticket #{ticket} ไม่เจอ  [{code}] {msg}")

    pos = positions[0]
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
        lot, decimals = calculate_lot_size(symbol, entry, sl,
                                           ACCOUNT_BALANCE, RISK_PER_TRADE)
        risk_amount   = ACCOUNT_BALANCE * RISK_PER_TRADE

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
              f"({RISK_PER_TRADE*100:.0f}% of ${ACCOUNT_BALANCE:,.0f})")
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

        connect()
        lot = clamp_lot(symbol, lot)
        place_order(symbol, direction, entry, sl, tp, lot)

    except (EnvironmentError, ConnectionError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
