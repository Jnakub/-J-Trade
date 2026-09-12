"""ทดสอบ scheduler.check_portfolio_risk() ด้วย position ปลอม

รัน: ./run_wine.sh test_portfolio_risk.py
(ต้องรันผ่าน wine เพราะ config.py import MetaTrader5)

ไม่ต้องต่อ MT5 จริง — แทน mt5.positions_get / mt5.symbol_info ด้วยของปลอม
"""
import MetaTrader5 as mt5

import scheduler
import order

BALANCE = 10_000.0          # 1R = 2% = 200 USD

CONTRACT = {"BTCUSDm": 1.0, "ETHUSDm": 1.0, "XAUUSDm": 100.0,
            "EURUSDm": 100_000.0, "GBPUSDm": 100_000.0,
            "USDJPYm": 100_000.0, "US500m": 1.0}


class FakeInfo:
    def __init__(self, sym):
        self.trade_contract_size = CONTRACT[sym]


class FakePos:
    def __init__(self, symbol, direction, entry, sl, lot):
        self.symbol, self.price_open, self.sl, self.volume = symbol, entry, sl, lot
        self.type = mt5.POSITION_TYPE_BUY if direction == "Long" else mt5.POSITION_TYPE_SELL
        self.ticket = 1


_positions = []
mt5.symbol_info = lambda s: FakeInfo(s)
mt5.positions_get = lambda **kw: tuple(_positions)
order.mt5.symbol_info = mt5.symbol_info
scheduler.mt5.positions_get = mt5.positions_get


def pos_at(symbol, direction, r, lot=None):
    """สร้าง position ที่มีความเสี่ยงคงเหลือ = r R พอดี (r=0 คือ SL ที่ breakeven)"""
    entry = {"BTCUSDm": 60_000.0, "ETHUSDm": 3_000.0, "XAUUSDm": 4_000.0,
             "EURUSDm": 1.1, "GBPUSDm": 1.3, "USDJPYm": 150.0,
             "US500m": 6_000.0}[symbol]
    lot = lot if lot is not None else 0.1
    risk_money = BALANCE * 0.02 * r
    cs = CONTRACT[symbol]
    dist = risk_money / (lot * cs) * (entry / 1.0 if "JPY" in symbol else 1.0)
    if "JPY" in symbol:
        dist = risk_money * entry / (lot * cs)
    sl = entry - dist if direction == "Long" else entry + dist
    return FakePos(symbol, direction, entry, sl, lot)


def check(label, positions, symbol, want_ok, balance=BALANCE):
    global _positions
    _positions = positions
    ok, reason = scheduler.check_portfolio_risk(symbol, balance)
    mark = "ok  " if ok == want_ok else "FAIL"
    print(f"  [{mark}] {label:52} -> {'ผ่าน' if ok else 'บล็อก'}"
          f"{'  (' + reason[:60] + ')' if reason else ''}")
    return ok == want_ok


print("\nconfig: MAX_PORTFOLIO_RISK_R =", scheduler.MAX_PORTFOLIO_RISK_R,
      " MAX_GROUP_RISK_R =", scheduler.MAX_GROUP_RISK_R)
print("\nสูตรความเสี่ยง (ต้องได้ 1R = 200 USD ทุก symbol):")
for s in CONTRACT:
    p = pos_at(s, "Long", 1.0)
    got = order.position_risk_amount(s, "Long", p.price_open, p.sl, p.volume)
    print(f"  {s:9} 1R = {got:8.2f} USD  {'ok' if abs(got-200) < 0.01 else 'FAIL'}")

print("\nเพดานรวม (3.0R):")
results = [
    check("ไม่มีไม้เปิดอยู่เลย", [], "BTCUSDm", True),
    check("เปิดอยู่ 2.0R + ไม้ใหม่ 1R = 3.0R พอดี", [
        pos_at("XAUUSDm", "Long", 1.0), pos_at("US500m", "Long", 1.0)], "BTCUSDm", True),
    check("เปิดอยู่ 2.5R + ไม้ใหม่ 1R = 3.5R เกิน", [
        pos_at("XAUUSDm", "Long", 1.0), pos_at("US500m", "Long", 1.0),
        pos_at("USDJPYm", "Short", 0.5)], "BTCUSDm", False),
    check("3 ไม้ที่ BE หมดแล้ว (0R) -> ยังเปิดได้", [
        pos_at("XAUUSDm", "Long", 0.0), pos_at("US500m", "Long", 0.0),
        pos_at("USDJPYm", "Short", 0.0)], "BTCUSDm", True),
    check("ไม้ที่ล็อกกำไรแล้ว (SL เลย entry) นับเป็น 0R ไม่ใช่ติดลบ", [
        FakePos("XAUUSDm", "Long", 4000.0, 4100.0, 0.1),
        pos_at("US500m", "Long", 1.0), pos_at("USDJPYm", "Short", 1.0)],
        "BTCUSDm", True),
]

print("\nเพดานกลุ่ม (2.0R) — CRYPTO = BTC+ETH, EURGBP = EUR+GBP:")
results += [
    check("ETH เปิด 1.0R + BTC ใหม่ 1R = 2.0R พอดี", [
        pos_at("ETHUSDm", "Long", 1.0)], "BTCUSDm", True),
    check("ETH 1.0R + BTC 0.5R + BTC ใหม่ = 2.5R เกินเพดานกลุ่ม", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 0.5)],
        "BTCUSDm", False),
    check("เท่ากันเป๊ะแต่ไม้ใหม่เป็น XAU (คนละกลุ่ม) -> ผ่าน", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 0.5)],
        "XAUUSDm", True),
    check("GBP 1.5R + EUR ใหม่ -> เกินเพดานกลุ่ม", [
        pos_at("GBPUSDm", "Long", 1.5)], "EURUSDm", False),
    check("US500 1.5R + XAU ใหม่ (ไม่อยู่กลุ่มไหน) -> ผ่าน", [
        pos_at("US500m", "Long", 1.5)], "XAUUSDm", True),
]

print("\nเคสขอบ:")
results += [
    check("ไม้ที่ไม่มี SL ถูกนับเป็น 1R เต็ม (2 ไม้ = 2R)", [
        FakePos("XAUUSDm", "Long", 4000.0, 0.0, 0.1),
        FakePos("US500m", "Long", 6000.0, 0.0, 1.0),
        pos_at("USDJPYm", "Short", 0.5)], "BTCUSDm", False),
    check("balance = 0 -> บล็อก ไม่ใช่หารด้วยศูนย์", [
        pos_at("XAUUSDm", "Long", 1.0)], "BTCUSDm", False, balance=0.0),
]

print(f"\n{sum(results)}/{len(results)} ผ่าน")
