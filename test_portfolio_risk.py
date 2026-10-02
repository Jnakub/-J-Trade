"""ทดสอบ scheduler.check_portfolio_risk() ด้วย position ปลอม
+ (2026-09-28) ช่องถือไม้ของไม้ Breakout ใน scheduler.scan_symbol และเกณฑ์ส่งคำสั่งขยับ SL/TP
  ของ exit_monitor.execute_decision — ทั้งคู่เป็นบั๊กที่ backtest มองไม่เห็นเพราะ replay ไม่ได้
  เดินผ่านโค้ดสองจุดนี้ (ดู config.slot_of และ exit_monitor._price_moved)

รัน: ./run_wine.sh test_portfolio_risk.py
(ต้องรันผ่าน wine เพราะ config.py import MetaTrader5)

ไม่ต้องต่อ MT5 จริง — แทน mt5.positions_get / mt5.symbol_info ด้วยของปลอม
"""
import logging
import os

import MetaTrader5 as mt5

# 🔴 2026-09-24: ต้องอยู่ **ก่อน** import scheduler — ไม่งั้น tee_print ของ scheduler เขียน warning
# จากไม้ปลอมลง logs/scheduler.log ของบอทจริง (เจอ 112 บรรทัด "#1 (XAUUSDm) ไม่มี SL" ปนอยู่ใน
# log ตั้งแต่ 2026-09-12 หน้าตาเหมือนตอน SL หลุดจริงทุกตัวอักษร) — logger_setup.get_logger ข้าม
# การติด file handler เมื่อ logger มี handler อยู่แล้ว จึงจองด้วย NullHandler ไว้ก่อน
# (scheduler import exit_monitor ด้วย จึงจองทั้งคู่) ตรวจผลจริงที่เคส "ไม่แตะ log จริง" ด้านล่าง
for _name in ("scheduler", "exit_monitor"):
    logging.getLogger(_name).addHandler(logging.NullHandler())

import scheduler  # noqa: E402
import order      # noqa: E402

BALANCE = 10_000.0          # 1R = 2% = 200 USD

# (tick_size, tick_value เป็น USD) — ค่าจริงจากโบรก 2026-09-24 ยกเว้น USDJPY ที่คิดที่ราคา 150
# ให้ตรงกับ entry ของ pos_at  HK50m กำไรเป็น HKD: 0.1 จุด = $0.01275 ไม่ใช่ $0.1 (ดู
# order.money_per_price_unit — เดิมสูตรใช้ contract size ล้วน แล้ว HK50 เล็กไป 7.8 เท่า)
SPEC = {"BTCUSDm": (0.01, 0.01), "ETHUSDm": (0.01, 0.01), "XAUUSDm": (0.001, 0.1),
        "EURUSDm": (1e-5, 1.0), "GBPUSDm": (1e-5, 1.0),
        "USDJPYm": (0.001, 0.001 * 100_000 / 150.0), "US500m": (0.01, 0.01),
        "UKOILm": (0.001, 1.0), "HK50m": (0.1, 0.01274770381984945),
        # GBPCHFm กำไรเป็น CHF — tick_value จากโบรก 2026-09-28 (ขยับตาม USDCHF) ตอนเพิ่ม symbol
        # เทียบ order.position_risk_amount กับ order_calc_profit สดแล้วตรงถึงเซ็นต์ ($198.97 ทั้งคู่)
        "GBPCHFm": (1e-5, 1.2013022115973717),
        # AUDNZDm กำไรเป็น NZD — tick_value 2026-09-29 · สูตรเทียบ order_calc_profit สดแล้วตรง ($203.35)
        "AUDNZDm": (1e-5, 0.5653600000000001)}


DIGITS = {"BTCUSDm": 2, "ETHUSDm": 2, "XAUUSDm": 3, "EURUSDm": 5, "GBPUSDm": 5,
          "USDJPYm": 3, "US500m": 2, "UKOILm": 3, "HK50m": 1,
          "GBPCHFm": 5, "AUDNZDm": 5}   # ค่าจริงจากโบรก 2026-09-28


class FakeInfo:
    def __init__(self, sym):
        self.name = sym
        self.trade_tick_size, self.trade_tick_value = SPEC[sym]
        self.trade_tick_value_loss = self.trade_tick_value
        self.volume_step = 0.01
        self.volume_min = 0.01
        self.digits = DIGITS[sym]
        self.point = self.trade_tick_size      # tick_size = point ทุก symbol (ตรวจแล้ว 2026-09-28)
        self.trade_mode = mt5.SYMBOL_TRADE_MODE_FULL


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
             "US500m": 6_000.0, "UKOILm": 95.0, "HK50m": 24_000.0, "GBPCHFm": 1.1, "AUDNZDm": 1.24}[symbol]
    lot = lot if lot is not None else 0.1
    risk_money = BALANCE * 0.02 * r
    tick_size, tick_value = SPEC[symbol]
    dist = risk_money / (lot * tick_value / tick_size)
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
_formula = []
for s in SPEC:
    p = pos_at(s, "Long", 1.0)
    got = order.position_risk_amount(s, "Long", p.price_open, p.sl, p.volume)
    _formula.append(abs(got - 200) < 0.01)
    print(f"  {s:9} 1R = {got:8.2f} USD  {'ok' if _formula[-1] else 'FAIL'}")


def money_check(label, got, want, tol=0.01):
    _formula.append(abs(got - want) <= tol)
    print(f"  [{'ok  ' if _formula[-1] else 'FAIL'}] {label:52} -> {got:.2f} (ต้องได้ {want:g})")


# เทียบกับเงินที่โบรกลงบัญชีจริง — ตัวเลขจากดีลที่เกิดขึ้นแล้ว ไม่ใช่สูตรตรวจสูตรตัวเอง
print("\nเทียบกับดีลจริงในบัญชี:")
money_check("HK50 ดีล #4193547532 0.18 lot ห่าง 84.3 จุด = −$1.93",
            order.position_risk_amount("HK50m", "Long", 24755.7, 24671.4, 0.18), 1.93)
money_check("UKOIL #4741691609 โดน SL 0.06 lot = −$178.32",
            order.position_risk_amount("UKOILm", "Long", 99.95, 96.978, 0.06), 178.32)
_lot, _ = order.calculate_lot_size("HK50m", 24755.7, 24155.7, BALANCE, 0.02)
money_check(f"HK50 SL 600 จุด ได้ lot {_lot} เสี่ยง ~2% (สูตรเดิมได้ 0.33)",
            order.risk_pct_of("HK50m", 24755.7, 24155.7, _lot, BALANCE), 1.975, tol=0.025)

# ทดสอบ "กลไก" ของเพดาน ไม่ใช่ "ค่าที่ตั้ง" — ตรึงไว้ที่ 3.0R ตามเคสที่เขียนไว้ แล้วคืนค่าจริง
# ทีหลัง (ค่าใน config เปลี่ยนเป็น 6.0R เมื่อ 2026-09-24 ถ้าไม่ตรึง เคส "3.5R เกิน" จะพังเพราะ
# เพดานสูงขึ้น ไม่ใช่เพราะกลไกผิด)
_LIVE_CAP = scheduler.MAX_PORTFOLIO_RISK_R
scheduler.MAX_PORTFOLIO_RISK_R = 3.0
print("\nเพดานรวม (ตรึงที่ 3.0R เพื่อทดสอบกลไก):")
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

scheduler.MAX_PORTFOLIO_RISK_R = _LIVE_CAP
print(f"\nเพดานรวมค่าจริงใน config ({_LIVE_CAP:g}R):")
# กระจายความเสี่ยง (cap-1)R ไว้ 4 symbol ที่อยู่คนละกลุ่ม (XAU/US500/USDJPY/EUR) แล้วให้ไม้ใหม่
# เป็น BTC ซึ่งกลุ่ม CRYPTO ยังว่าง -> ทดสอบเพดานรวมล้วน ไม่ปนเพดานกลุ่ม
_each = (_LIVE_CAP - 1) / 4
_base = [pos_at(x, "Long", _each) for x in ("XAUUSDm", "US500m", "USDJPYm", "EURUSDm")]
results += [
    check(f"เปิดอยู่ {_LIVE_CAP-1:g}R + ไม้ใหม่ 1R = {_LIVE_CAP:g}R พอดี", _base, "BTCUSDm", True),
    check(f"เปิดอยู่ {_LIVE_CAP-0.5:g}R + ไม้ใหม่ 1R เกิน",
          _base + [pos_at("ETHUSDm", "Long", 0.5)], "BTCUSDm", False),
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

# เคส "ไม่มี SL = 1R" พิสูจน์ด้วยการให้ 2R + 0.5R + ไม้ใหม่ 1R = 3.5R ทะลุเพดาน -> ต้องตรึงที่ 3.0R
# เหมือนชุดกลไกด้านบน ไม่งั้นที่เพดาน 6R มันผ่านเสมอและไม่ได้ทดสอบอะไรเลย
scheduler.MAX_PORTFOLIO_RISK_R = 3.0
print("\nเคสขอบ (ตรึงที่ 3.0R):")
results += [
    check("ไม้ที่ไม่มี SL ถูกนับเป็น 1R เต็ม (2 ไม้ = 2R)", [
        FakePos("XAUUSDm", "Long", 4000.0, 0.0, 0.1),
        FakePos("US500m", "Long", 6000.0, 0.0, 1.0),
        pos_at("USDJPYm", "Short", 0.5)], "BTCUSDm", False),
    check("balance = 0 -> บล็อก ไม่ใช่หารด้วยศูนย์", [
        pos_at("XAUUSDm", "Long", 1.0)], "BTCUSDm", False, balance=0.0),
]

scheduler.MAX_PORTFOLIO_RISK_R = _LIVE_CAP


# ── ช่องถือไม้ของไม้ Breakout (2026-09-28) ─────────────────────────────────────────────────────
# เดิน scheduler.scan_symbol ตัวจริงจนถึงด่านเช็คช่อง ของที่ต้องใช้ MT5/เน็ต/CSV ถูกแทนด้วยของปลอม
# ทั้งหมด (แล้วคืนค่าเดิมทุกตัว) — ถ้าหลุดด่านช่องมาได้ scan จะไปหยุดที่ "ดึงราคาไม่ได้" เพราะ
# symbol_info_tick ปลอมคืน None = ไม่มีทางส่ง order ได้จริงระหว่างเทสต์
import contextlib  # noqa: E402
import io          # noqa: E402
import journal       # noqa: E402
import exit_monitor  # noqa: E402

SLOT_SKIP = "ช่อง Reversal มีไม้เปิดอยู่แล้ว"


def scan_output(on_book: list[str], regime: str, regime_extra: dict = None, bias_1d: str = None) -> str:
    """รัน scan_symbol("XAUUSDm") ตอนที่มีไม้กลยุทธ์ on_book เปิดอยู่ แล้วคืนข้อความที่พิมพ์"""
    global _positions
    _positions = []
    for i in range(len(on_book)):
        p = FakePos("XAUUSDm", "Long", 4000.0, 3900.0, 0.1)
        p.ticket = 700 + i
        _positions.append(p)
    patches = {
        (journal, "get_trade_strategy"): lambda t: on_book[t - 700],
        (journal, "check_daily_loss"): lambda b, m: True,
        (journal, "check_cooldown"): lambda s, h: (True, "OK"),
        (journal, "check_tp_cooldown"): lambda s, h: (True, "OK"),
        (scheduler, "analyze_position"): lambda pos: {},
        (scheduler, "print_report"): lambda m: None,
        (scheduler, "execute_decision"): lambda m: None,
        (scheduler, "get_account_balance"): lambda: BALANCE,
        (scheduler, "check_portfolio_risk"): lambda s, b: (True, ""),
        (scheduler, "check_upcoming_news"): lambda hours_ahead=None: (False, "", None),
        (scheduler, "get_regime"): lambda s: {"regime": regime, "action": "ทดสอบ", **(regime_extra or {})},
        (scheduler, "get_trend_bias"): lambda s, df: (bias_1d, "ทดสอบ"),
        (scheduler, "get_ohlcv"): lambda *a, **k: None,
        (scheduler.mt5, "symbol_info_tick"): lambda s: None,
    }
    saved = {k: getattr(*k) for k in patches}
    for (obj, name), fn in patches.items():
        setattr(obj, name, fn)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            scheduler.scan_symbol("XAUUSDm")
    finally:
        for (obj, name), fn in saved.items():
            setattr(obj, name, fn)
        _positions = []
    return buf.getvalue()


def slot_check(label, on_book, regime, want_skip):
    out = scan_output(on_book, regime)
    skipped = SLOT_SKIP in out or "ช่องเต็มทั้งสองกลยุทธ์" in out
    ok = skipped == want_skip
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {'ข้าม (ช่องไม่ว่าง)' if skipped else 'ไปต่อ'}")
    return ok


print("\nช่องถือไม้ — ไม้ Breakout ครองช่อง Reversal (config.slot_of):")
results += [
    slot_check("ถือ Breakout + REVERSAL-READY -> ห้ามเปิดซ้อน", ["Breakout"], "REVERSAL-READY", True),
    slot_check("ถือ Reversal + REVERSAL-READY -> ห้ามเปิดซ้อน", ["Reversal"], "REVERSAL-READY", True),
    slot_check("ถือ Scoring + REVERSAL-READY -> ช่อง Reversal ว่าง", ["Scoring"], "REVERSAL-READY", False),
    slot_check("ถือ Breakout + TREND -> ช่อง Scoring ว่าง", ["Breakout"], "TREND", False),
    slot_check("ถือ Scoring + Breakout -> ช่องเต็มทั้งสอง", ["Scoring", "Breakout"], "TREND", True),
]


# ── Breakout ข้ามด่าน peak ADX (2026-09-30 · config.BREAKOUT_IGNORES_MIN_PEAK) ─────────────────────
# ถือไม้ Reversal ไว้ -> ถ้ารอบนี้ถูกยกเป็น REVERSAL-READY จะติดด่าน "ช่อง Reversal" (มองเห็นได้จาก output)
# ถ้าไม่ถูกยก regime ยังเป็น TREND -> ไปช่อง Scoring ที่ว่าง
_NOPEAK = {"reversal_nopeak": ("REVERSAL-READY", "", ""), "peak": {"peak": 25.0}}


def bnp_check(label, extra, bias, want_promoted):
    out = scan_output(["Reversal"], "TREND", regime_extra=extra, bias_1d=bias)
    promoted = SLOT_SKIP in out
    ok = promoted == want_promoted
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {'ยกเป็น REVERSAL-READY' if promoted else 'regime เดิม'}")
    return ok


print("\nBreakout ข้ามด่าน peak ADX:")
results += [
    bnp_check("nopeak READY + bearish + 1D Long -> ยก", {**_NOPEAK, "divergence": {"divergence": "bearish"}},
              "Long", True),
    bnp_check("nopeak READY + bearish + 1D Short -> ไม่ยก (เป็น Reversal Short)",
              {**_NOPEAK, "divergence": {"divergence": "bearish"}}, "Short", False),
    bnp_check("nopeak READY + bullish -> ไม่ยก (เป็น Reversal Long)",
              {**_NOPEAK, "divergence": {"divergence": "bullish"}}, "Long", False),
    bnp_check("ไม่มี nopeak READY -> ไม่ยก", {"reversal_nopeak": None, "divergence": {"divergence": "bearish"}},
              "Long", False),
]


# ── Sideway (2026-10-02 · config.SIDEWAY_*) ─────────────────────────────────────────────────────────
# เปิดสวิตช์ชั่วคราวในเทสต์ (ค่าจริงอาจปิดอยู่) แล้วดูว่า scan_symbol ไปถึงด่านไหน
import config as _config  # noqa: E402


def sw_check(label, on_book, regime, adx, enabled, want):
    _saved = scheduler.SIDEWAY_ENABLED
    scheduler.SIDEWAY_ENABLED = enabled
    try:
        out = scan_output(on_book, regime, regime_extra={"adx_now": adx})
    finally:
        scheduler.SIDEWAY_ENABLED = _saved
    got = ("ช่อง Scoring เต็ม" if "ช่อง Scoring มีไม้เปิดอยู่แล้ว" in out else
           "ไม่เข้า (regime)" if "regime ยังไม่พร้อม" in out else
           "ผ่านด่านช่อง" if "ดึงราคาไม่ได้" in out else "อื่นๆ")
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {got}")
    return ok


print("\nSideway — ช่องร่วมกับ Scoring · เข้าเฉพาะ regime ห้ามเทรด + ADX < 20:")
results += [
    sw_check("CHOPPY ADX 15 + ว่าง -> ไปต่อ", [], "CHOPPY", 15.0, True, "ผ่านด่านช่อง"),
    sw_check("CHOPPY ADX 15 + ถือ Scoring -> ช่องเต็ม", ["Scoring"], "CHOPPY", 15.0, True, "ช่อง Scoring เต็ม"),
    sw_check("CHOPPY ADX 15 + ถือ Sideway -> ช่องเต็ม", ["Sideway"], "CHOPPY", 15.0, True, "ช่อง Scoring เต็ม"),
    sw_check("CHOPPY ADX 15 + ถือ Reversal -> ไปต่อ", ["Reversal"], "CHOPPY", 15.0, True, "ผ่านด่านช่อง"),
    sw_check("เขตเทา ADX 21 -> ไม่เข้า", [], "เขตเทา", 21.0, True, "ไม่เข้า (regime)"),
    sw_check("CHOPPY แต่สวิตช์ปิด -> ไม่เข้า", [], "CHOPPY", 15.0, False, "ไม่เข้า (regime)"),
]
_ok = _config.slot_of("Sideway") == "Scoring"
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'config.slot_of(Sideway) == Scoring':52} -> {_config.slot_of('Sideway')}")
results.append(_ok)
_ti = exit_monitor.check_trend_invalidation("XAUUSDm", "Long", None, strategy="Sideway")
_ok = _ti["keep_pct"] == 100 and not _ti["active"]
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'trend invalidation ข้ามไม้ Sideway':52} -> keep {_ti['keep_pct']}")
results.append(_ok)


# ── เกณฑ์ส่งคำสั่งขยับ SL/TP (2026-09-28) ──────────────────────────────────────────────────────
# เรียก execute_decision ตัวจริง แทน modify_sltp ด้วยตัวจดคำสั่ง — ไม่มีการส่งคำสั่งไป broker
_sent = []
_saved_em = {"modify_sltp": exit_monitor.modify_sltp, "is_demo_account": exit_monitor.is_demo_account}
exit_monitor.modify_sltp = lambda ticket, new_sl=None, new_tp=None: _sent.append((new_sl, new_tp))
exit_monitor.is_demo_account = lambda: True


def move_check(label, symbol, sl, tp, desired_sl, desired_tp, want):
    _sent.clear()
    m = {"ticket": 1, "symbol": symbol, "lot": 0.2, "entry": sl, "sl": sl, "tp": tp,
         "recommended_keep_pct": 100, "desired_sl": desired_sl, "desired_tp": desired_tp,
         "final_decision": ("ถือต่อ — ทดสอบ", "")}
    with contextlib.redirect_stdout(io.StringIO()):
        exit_monitor.execute_decision(m)
    ok = _sent == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {_sent or 'ไม่ส่ง'}")
    return ok


print("\nส่งคำสั่งขยับ SL/TP เมื่อราคาเปลี่ยนอย่างน้อย 1 point ของ symbol:")
results += [
    move_check("EUR ดึง TP เข้า 4 pip -> ส่ง (เดิมเงียบ เพราะ < 0.001)",
               "EURUSDm", 1.16200, 1.18500, 1.16200, 1.18460, [(None, 1.18460)]),
    move_check("EUR ถอย SL ออก 2 pip -> ส่ง", "EURUSDm", 1.16200, 1.18500, 1.16180, 1.18500,
               [(1.16180, None)]),
    move_check("EUR ต่างไม่ถึงครึ่ง point -> ไม่ส่ง", "EURUSDm", 1.16200, 1.18500,
               1.162002, 1.185003, []),
    move_check("XAU ต่าง 0.0004 (< 1 point) -> ไม่ส่ง", "XAUUSDm", 3900.0, 4200.0,
               3900.0004, 4200.0, []),
    move_check("XAU ขยับ SL 0.002 -> ส่ง", "XAUUSDm", 3900.0, 4200.0, 3899.998, 4200.0,
               [(3899.998, None)]),
    move_check("ยังไม่มี TP บน broker (0) -> ตั้งได้เสมอ", "EURUSDm", 1.16200, 0.0,
               1.16200, 1.18460, [(None, 1.18460)]),
]
for _k, _v in _saved_em.items():
    setattr(exit_monitor, _k, _v)


# ── จังหวะรอบสแกน + สรุปรายวัน (2026-09-28) ──────────────────────────────────────────────────
# เดิม sleep 3600 วิหลังสแกนเสร็จ -> รอบเลื่อนจนข้ามชั่วโมง 00 แล้วสรุปรายวันหาย 2 วันติด
import datetime as _dt  # noqa: E402
import tempfile          # noqa: E402


def _ts(h, m, s):
    return _dt.datetime(2026, 9, 28, h, m, s, tzinfo=_dt.timezone.utc).timestamp()


def wait_check(label, now_ts, want_ts):
    got = now_ts + scheduler.seconds_until_next_scan(now_ts)
    ok = abs(got - want_ts) < 1e-6
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> "
          f"{_dt.datetime.fromtimestamp(got, _dt.timezone.utc):%H:%M:%S} UTC")
    return ok


print(f"\nรอบสแกนตรงต้นชั่วโมง (+{scheduler.SCAN_DELAY_SECONDS} วิ) ไม่เลื่อนตามเวลาที่สแกน:")
results += [
    wait_check("สแกนเสร็จ 04:01:40 -> รอบถัดไป 05:01:00", _ts(4, 1, 40), _ts(5, 1, 0)),
    wait_check("สแกนนาน เสร็จ 04:13:00 -> ยัง 05:01:00", _ts(4, 13, 0), _ts(5, 1, 0)),
    wait_check("สแกนเสร็จ 23:59:50 -> 00:01:00 ไม่ข้ามชั่วโมง 00", _ts(23, 59, 50),
               _ts(23, 59, 50) + 70),
]

_sum_sent = []
_saved_sum = (scheduler._SUMMARY_STATE_FILE, journal.get_daily_statistics,
              scheduler.notify.notify_daily_summary)
scheduler._SUMMARY_STATE_FILE = os.path.join(tempfile.mkdtemp(), "daily_summary_state.json")
journal.get_daily_statistics = lambda day: {"total_trades": 0}
scheduler.notify.notify_daily_summary = lambda stats, day: _sum_sent.append(day)


def summary_check(label, today, want):
    _sum_sent.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        scheduler.send_daily_summary_if_due(today)
    ok = _sum_sent == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {_sum_sent or 'ไม่ส่ง'}")
    return ok


print("\nสรุปรายวัน — ส่งของเมื่อวานครั้งเดียวในรอบแรกที่สำเร็จของวันใหม่:")
results += [
    summary_check("รอบแรกของ 09-28 (ตอนไหนก็ได้) -> ส่งสรุป 09-27",
                  _dt.date(2026, 9, 28), ["2026-09-27"]),
    summary_check("รอบถัดไป/รีสตาร์ทวันเดียวกัน -> ไม่ส่งซ้ำ", _dt.date(2026, 9, 28), []),
    summary_check("วันถัดไป ไม่มีรอบชั่วโมง 00 เลย -> ยังส่งสรุป 09-28",
                  _dt.date(2026, 9, 29), ["2026-09-28"]),
]
(scheduler._SUMMARY_STATE_FILE, journal.get_daily_statistics,
 scheduler.notify.notify_daily_summary) = _saved_sum

# 2026-09-30: ไม้ใหม่เปิดสวนทิศ -> ย้าย TP ไม้เก่าที่ขาดทุนอยู่ไป entry (config.TP_TO_ENTRY_ON_OPPOSITE)
# ตัวเลขจากคู่ HK50 จริง: Reversal Long 24,755.7 ราคา 24,503.4 · ไม้ใหม่ Short #2
from types import SimpleNamespace as _NS  # noqa: E402
_modified = []


def tpe_check(label, old, new_dir, want):
    _saved = (scheduler.mt5.positions_get, scheduler.modify_sltp)
    scheduler.mt5.positions_get = lambda symbol=None: [old, _NS(ticket=2)]
    scheduler.modify_sltp = lambda t, new_sl=None, new_tp=None: _modified.append((t, new_tp))
    _modified.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            scheduler._tp_to_entry_on_opposite("HK50m", new_dir, 2)
    finally:
        scheduler.mt5.positions_get, scheduler.modify_sltp = _saved
    ok = _modified == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {_modified or 'ไม่แตะ'}")
    return ok


def _hk(type_, cur, tp=25618.2):
    return _NS(ticket=1, type=type_, price_open=24755.7, price_current=cur, tp=tp)


_B, _S = mt5.POSITION_TYPE_BUY, mt5.POSITION_TYPE_SELL
print("\nไม้ใหม่เปิดสวนทิศ -> ย้าย TP ไม้เก่าที่ขาดทุนไป entry:")
results += [
    tpe_check("Long ขาดทุน + ไม้ใหม่ Short -> TP = entry", _hk(_B, 24503.4), "Short", [(1, 24755.7)]),
    tpe_check("Long กำไร + ไม้ใหม่ Short -> ไม่แตะ", _hk(_B, 24900.0), "Short", []),
    tpe_check("Long ขาดทุน + ไม้ใหม่ Long (ทิศเดียวกัน) -> ไม่แตะ", _hk(_B, 24503.4), "Long", []),
    tpe_check("Short ขาดทุน + ไม้ใหม่ Long -> TP = entry", _hk(_S, 24900.0, 23000.0), "Long",
              [(1, 24755.7)]),
    tpe_check("TP อยู่ที่ entry แล้ว -> ไม่ส่งซ้ำ", _hk(_B, 24503.4, 24755.7), "Short", []),
]
# กฎ TP -> entry ไม่แตะไม้เก่าที่เป็น Sideway
_saved_gs = journal.get_trade_strategy
journal.get_trade_strategy = lambda t: "Sideway"
results.append(tpe_check("ไม้เก่าเป็น Sideway ขาดทุน + ไม้ใหม่สวน -> ไม่แตะ", _hk(_B, 24503.4), "Short", []))
journal.get_trade_strategy = _saved_gs

# เคสข้างบนยิง WARNING "ไม่มี SL" จริง 2 บรรทัด — ต้องไม่มี handler ตัวไหนเขียนลงไฟล์ในโฟลเดอร์ logs/
# (ถ้า get_logger เปลี่ยนวิธีเช็ค handler ซ้ำเมื่อไหร่ เคสนี้จะพังก่อนที่ log จริงจะถูกปนอีก)
_file_logs = [h.baseFilename for n in ("scheduler", "exit_monitor")
              for h in logging.getLogger(n).handlers if isinstance(h, logging.FileHandler)]
_ok = not _file_logs
print(f"\n  [{'ok  ' if _ok else 'FAIL'}] {'ไม่แตะ log จริงของบอท':52} -> "
      f"{'ไม่มี file handler' if _ok else ', '.join(os.path.basename(f) for f in _file_logs)}")
results.append(_ok)
results += _formula   # เดิมบรรทัด "1R = 200 USD" พิมพ์ FAIL ได้แต่ไม่ถูกนับในยอดรวม

print(f"\n{sum(results)}/{len(results)} ผ่าน")
