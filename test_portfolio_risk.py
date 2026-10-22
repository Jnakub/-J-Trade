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
import tempfile as _tf_ra  # noqa: E402
# 2026-10-15: scan_symbol เรียก rev_a_step ทุกรอบ -> ไฟล์สถานะแบบ A ต้องไม่ใช่ของบอทจริงตั้งแต่เคสแรก
scheduler._REV_A_FILE = os.path.join(_tf_ra.mkdtemp(), "rev_a_state.json")

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

# กลไกเพดานกลุ่มตรึงที่ 2.0R (ค่าจริงใน config อาจสูงกว่า) — ทดสอบค่าจริงแยกด้านล่าง
_LIVE_GROUP = scheduler.MAX_GROUP_RISK_R
scheduler.MAX_GROUP_RISK_R = 2.0
print("\nเพดานกลุ่ม (ตรึงที่ 2.0R เพื่อทดสอบกลไก) — CRYPTO = BTC+ETH:")
results += [
    check("ETH เปิด 1.0R + BTC ใหม่ 1R = 2.0R พอดี", [
        pos_at("ETHUSDm", "Long", 1.0)], "BTCUSDm", True),
    check("ETH 1.0R + BTC 0.5R + BTC ใหม่ = 2.5R เกินเพดานกลุ่ม", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 0.5)],
        "BTCUSDm", False),
    check("เท่ากันเป๊ะแต่ไม้ใหม่เป็น XAU (คนละกลุ่ม) -> ผ่าน", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 0.5)],
        "XAUUSDm", True),
    check("EUR ไม่อยู่กลุ่มแล้ว: EUR 1.12R + EUR ใหม่ -> ผ่าน", [
        pos_at("EURUSDm", "Short", 1.12)], "EURUSDm", True),
    check("US500 1.5R + XAU ใหม่ (ไม่อยู่กลุ่มไหน) -> ผ่าน", [
        pos_at("US500m", "Long", 1.5)], "XAUUSDm", True),
]
scheduler.MAX_GROUP_RISK_R = _LIVE_GROUP
print(f"\nเพดานกลุ่มค่าจริงใน config ({_LIVE_GROUP:g}R):")
results += [
    check("ETH 1R + BTC 1R + BTC ใหม่ = 3R พอดี", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 1.0)], "BTCUSDm", _LIVE_GROUP >= 3.0),
    check("ETH 1R + BTC 1.2R + ETH ใหม่ = 3.2R เกิน", [
        pos_at("ETHUSDm", "Long", 1.0), pos_at("BTCUSDm", "Long", 1.2)], "ETHUSDm", False),
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


def scan_output(on_book: list[str], regime: str, regime_extra: dict = None, bias_1d: str = None,
                ohlcv=None, tick=None, extra_patches: dict = None) -> str:
    """รัน scan_symbol("XAUUSDm") ตอนที่มีไม้กลยุทธ์ on_book เปิดอยู่ แล้วคืนข้อความที่พิมพ์
    ohlcv / tick / extra_patches (2026-10-13 · Pullback): แทน get_ohlcv · symbol_info_tick · อื่นๆ ได้
    ไม่ส่ง = ของเดิม (แท่ง None · tick None = scan หยุดที่ "ดึงราคาไม่ได้" ไม่มีทางส่ง order)"""
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
        (scheduler, "get_ohlcv"): ohlcv or (lambda *a, **k: None),
        (scheduler.mt5, "symbol_info_tick"): lambda s: tick,
        **(extra_patches or {}),
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
    skipped = "มีไม้เปิดอยู่แล้ว" in out or "ช่องเต็มทั้งสองกลยุทธ์" in out   # ช่องไหนก็ได้ (Reversal/Scoring)
    ok = skipped == want_skip
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {'ข้าม (ช่องไม่ว่าง)' if skipped else 'ไปต่อ'}")
    return ok


# 2026-10-22: Breakout ย้ายไปช่อง Scoring (config.BREAKOUT_SLOT) — REVERSAL-READY (Reversal เดิมปิด) เช็คช่อง Scoring
print("\nช่องถือไม้ — ไม้ Breakout ครองช่อง Scoring (config.slot_of · BREAKOUT_SLOT):")
results += [
    slot_check("ถือ Breakout + REVERSAL-READY -> ห้ามเปิดซ้อน", ["Breakout"], "REVERSAL-READY", True),
    slot_check("ถือ Scoring + REVERSAL-READY -> ห้ามซ้อน Scoring", ["Scoring"], "REVERSAL-READY", True),
    slot_check("ถือ Reversal/Pullback + REVERSAL-READY -> ช่องว่าง", ["Reversal"], "REVERSAL-READY", False),
    slot_check("ถือ Breakout + TREND -> ช่อง Scoring เต็ม", ["Breakout"], "TREND", True),
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


print("\nBreakout ข้ามด่าน peak ADX (ทดสอบการยก regime ที่ BREAKOUT_SLOT=Reversal — ช่อง Scoring แยกยก/ไม่ยกไม่ได้):")
import config as _config  # noqa: E402
_saved_bs2, _config.BREAKOUT_SLOT = _config.BREAKOUT_SLOT, "Reversal"
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
_config.BREAKOUT_SLOT = _saved_bs2


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


# ── แจ้ง Telegram ตอนเลื่อน SL ไป BE (2026-10-05) ────────────────────────────────────────────
# แทน notify ด้วยตัวจด — ไม่มีข้อความไป Telegram จริง
_be_sent = []
_saved_be = exit_monitor.notify.notify_breakeven
exit_monitor.notify.notify_breakeven = lambda *a, **k: _be_sent.append(k.get("lock_r", 0.0))


def be_check(label, sl, desired_sl, be_lock, be_price, want_notify, be_level=0.0, want_lock=0.0):
    _sent.clear(); _be_sent.clear()
    m = {"ticket": 7, "symbol": "XAUUSDm", "direction": "Long", "lot": 0.2, "entry": 4000.0,
         "sl": sl, "tp": 4300.0, "recommended_keep_pct": 100, "desired_sl": desired_sl,
         "desired_tp": None, "be_lock": be_lock, "be_price": be_price, "be_level": be_level,
         "r_multiple": 1.52,
         "final_decision": ("ทดสอบ", "")}
    with contextlib.redirect_stdout(io.StringIO()):
        exit_monitor.execute_decision(m)
    ok = (len(_be_sent) == 1) == want_notify and (not _be_sent or abs(_be_sent[0] - want_lock) < 1e-9)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> "
          f"{('แจ้ง lock ' + format(_be_sent[0], '+g')) if _be_sent else 'ไม่แจ้ง'}")
    return ok


print("\nแจ้ง Telegram ตอนเลื่อน SL ไป BE:")
results += [
    be_check("ถึง 1.5R ขยับ SL 3900 -> entry -> แจ้ง", 3900.0, 4000.0, True, 4000.0, True),
    be_check("รอบถัดไป SL อยู่ที่ BE แล้ว -> ไม่ส่ง ไม่แจ้งซ้ำ", 4000.0, 4000.0, True, 4000.0, False),
    be_check("ขยับ SL ที่ไม่ใช่ BE (ยังไม่ถึง 1.5R) -> ไม่แจ้ง", 3900.0, 3890.0, False, 4000.0, False),
    be_check("be_lock แต่ SL ไปที่อื่น ไม่ใช่ be_price -> ไม่แจ้ง", 3900.0, 3950.0, True, 4000.0, False),
    # 2026-10-07 BREAKEVEN_LADDER ((3.0, 1.5),): ถึง 3R -> SL ไป +1.5R (entry 4000 · 1R = 100) -> แจ้ง "ล็อกกำไร"
    be_check("ถึง 3R ขยับ SL entry -> +1.5R -> แจ้งล็อกกำไร +1.5", 4000.0, 4150.0, True, 4150.0, True,
             be_level=1.5, want_lock=1.5),
]
_ladder_ok = exit_monitor.BREAKEVEN_LADDER == ((3.0, 1.5),)
print(f"  [{'ok  ' if _ladder_ok else 'FAIL'}] {'BREAKEVEN_LADDER = ((3.0, 1.5),) (ระบบจริง 2026-10-07)':52} "
      f"-> {exit_monitor.BREAKEVEN_LADDER}")
results.append(_ladder_ok)
exit_monitor.notify.notify_breakeven = _saved_be
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

# ── Pullback (2026-10-13 · config.PULLBACK_ENABLED · เดิมชื่อ Reversal ใหม่) ─────────────────────────────────────────────────
# กฎเข้า (pullback.pullback_direction) · ตำแหน่งในกรอบ · ทางของ scan_symbol · ไม้เงา (ไฟล์ state ชี้ไป temp)
# replay ตรวจกฎเดียวกันได้ (เรียกฟังก์ชันเดียวกัน) แต่ไม่เดินโค้ด scheduler ข้อ 4a-2 / 4e / ไม้เงาบนไฟล์ — เทสต์นี้แทน
import tempfile           # noqa: E402
from types import SimpleNamespace  # noqa: E402
import pandas as pd       # noqa: E402
import reversal           # noqa: E402
import pullback           # noqa: E402

_RI = {"structure": {"trend": "Short (reg)"}, "adx_now": 18.0, "adx_direction": "ทรง",
       "key_level": {"at_key_level": True}, "divergence": {"divergence": "bullish"},
       "peak": {"declining": False}}


def rn_check(label, over, bias, want, rule="nofall"):
    got, why = pullback.pullback_direction({**_RI, **over}, bias, adx_rule=rule)
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {got} ({why})")
    return ok


for _nm in ("Retracement", "Reversal"):   # 2026-10-21 Reversal A เปลี่ยนชื่อ — ไม้เก่าใน trades_log ยังชื่อ Reversal
    _ok = _config.slot_of(_nm) == "Reversal"
    print(f"  [{'ok  ' if _ok else 'FAIL'}] {'config.slot_of(' + _nm + ') == Reversal':52} -> {_config.slot_of(_nm)}")
    results.append(_ok)
import exit_monitor as _em_t  # noqa: E402
for _nm in ("Retracement", "Reversal"):
    _r = _em_t.check_trend_invalidation("XAUUSDm", "Long", pd.Timestamp("2026-10-01"), _nm)
    _ok = _r["active"] is False and "ข้าม" in _r["reason"]
    print(f"  [{'ok  ' if _ok else 'FAIL'}] {'trend check ข้ามไม้ ' + _nm:52} -> {_r['reason']}")
    results.append(_ok)
_ok = _config.slot_of("Pullback") == "Reversal"
print(f"\n  [{'ok  ' if _ok else 'FAIL'}] {'config.slot_of(Pullback) == Reversal (ช่องร่วม Breakout)':52} -> {_config.slot_of('Pullback')}")
results.append(_ok)
# config.BREAKOUT_SLOT (2026-10-22) — สลับช่องแล้ว Breakout ย้ายไปช่อง Scoring · Pullback/Retracement ยังอยู่ช่อง Reversal
_saved_bs = _config.BREAKOUT_SLOT
try:
    _config.BREAKOUT_SLOT = "Scoring"
    _ok = (_config.slot_of("Breakout") == "Scoring" == _config.slot_of("Sideway")
           and _config.slot_of("Pullback") == "Reversal" == _config.slot_of(_config.REV_A_STRATEGY))
finally:
    _config.BREAKOUT_SLOT = _saved_bs
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'BREAKOUT_SLOT=Scoring -> Breakout ช่อง Scoring':52}")
results.append(_ok)
_ok = _config.BREAKOUT_SLOT == "Scoring" and _config.slot_of("Breakout") == "Scoring"
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ค่าระบบจริง: Breakout ช่อง Scoring (2026-10-22)':52} -> {_config.slot_of('Breakout')}")
results.append(_ok)
print("\nPullback — กฎเข้า (pullback.pullback_direction):")
results += [
    rn_check("Bias Long + Structure Short + ADX ทรง + KL + bullish", {}, "Long", "Long"),
    rn_check("ADX ขึ้น -> เข้า", {"adx_direction": "ขึ้น"}, "Long", "Long"),
    rn_check("ADX ลง -> ไม่เข้า", {"adx_direction": "ลง"}, "Long", None),
    rn_check("ADX ลง แต่กฎ any -> เข้า (ไว้ให้ replay)", {"adx_direction": "ลง"}, "Long", "Long", rule="any"),
    rn_check("Bias ตรง Structure -> ทาง Scoring", {"structure": {"trend": "Long (reg)"}}, "Long", None),
    rn_check("Bias Short + Structure Long + bearish -> Short",
             {"structure": {"trend": "Long (reg)"}, "divergence": {"divergence": "bearish"}}, "Short", "Short"),
    rn_check("divergence สวนทิศ -> ไม่เข้า", {"divergence": {"divergence": "bearish"}}, "Long", None),
    rn_check("ไม่อยู่ที่ Key Level -> ไม่เข้า", {"key_level": {"at_key_level": False}}, "Long", None),
    rn_check("Structure ไม่ชัด -> ไม่เข้า", {"structure": {"trend": "ไม่ชัด / กำลังพัง"}}, "Long", None),
    rn_check("หา Bias ไม่ได้ -> ไม่เข้า", {}, None, None),
]
_got = pullback.pullback_direction({"regime": "TREND"}, "Long")[0]
print(f"  [{'ok  ' if _got is None else 'FAIL'}] {'regime_info ไม่ครบคีย์ -> ไม่เข้า (ไม่ crash)':52} -> {_got}")
results.append(_got is None)

# Pullback ใช้แนวแทน ADX (config.PULLBACK_LEVEL_INSTEAD_OF_ADX · levels.py · 2026-10-22)
print("\nPullback — แนวแทน ADX (levels.py):")
for _lbl, _ri, _lv, _want in [
    ("ADX ลง + div ที่แนว -> เข้า", {"adx_direction": "ลง"}, True, "Long"),
    ("div ไม่อยู่ที่แนว -> ไม่เข้า", {}, False, None),
]:
    _got = pullback.pullback_direction({**_RI, **_ri}, "Long", adx_rule="any", level_ok=_lv)[0]
    print(f"  [{'ok  ' if _got == _want else 'FAIL'}] {_lbl:52} -> {_got}")
    results.append(_got == _want)
import levels as _lvm  # noqa: E402
# แท่ง 1D สังเคราะห์: ก้นชัดที่ 90 (วันที่ 50) แล้วขึ้น 3 ATR ใน 10 วัน · วันนี้ = วันที่ 75 (อายุแนว ~14 วัน < 60)
_t = pd.date_range("2026-01-01", periods=76, freq="D")
_c = [100.0] * 45 + [97, 95, 93, 91, 90.5] + [90.0] + [92, 95, 98, 101, 104, 106, 107, 107, 107, 107] + [105.0] * 15
_df = pd.DataFrame({"time": _t, "open": _c, "high": [x + 1 for x in _c], "low": [x - 1 for x in _c], "close": _c})
_lv, _atr = _lvm.key_levels(_df)
_ok = any(abs(x - 89.0) < 1e-9 for x in _lv) and _atr is not None
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'key_levels เจอก้น 89 ที่กลับตัว ≥ 2 ATR':52} -> {sorted(set(round(x, 2) for x in _lv))[:6]}")
results.append(_ok)
_ri_lv = {"divergence": {"points": [(None, 95.0, 30), (None, 89.2, 35)]}}
_ok = _lvm.divergence_at_level(_ri_lv, _df) and not _lvm.divergence_at_level({"divergence": {"points": [(None, 99.0, 30), (None, 96.5, 35)]}}, _df)
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'div ก้น 89.2 ที่แนว · 96.5 ไม่อยู่':52}")
results.append(_ok)
_ok = not _lvm.divergence_at_level({"divergence": {}}, _df)
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ไม่มี divergence -> ไม่อยู่ที่แนว':52}")
results.append(_ok)
# Retracement: divergence ต้องอยู่ที่แนว (config.REV_A_REQUIRE_LEVEL)
import rev_a as _rev_a_t  # noqa: E402
_ri_ra = {"peak": {"declining": True, "peak": 30.0}, "structure": {"trend": "Long (reg)"}, "divergence": {"divergence": "bearish"}}
for _lbl, _lv, _want in [("Retracement: div ที่แนว -> เข้า Short", True, "Short"),
                         ("Retracement: div ไม่อยู่ที่แนว -> ไม่เข้า", False, None),
                         ("Retracement: ไม่ดูแนว (ระบบจริง) -> เข้า Short", None, "Short")]:
    _got = _rev_a_t.signal_direction(_ri_ra, "Long", level_ok=_lv)[0]
    print(f"  [{'ok  ' if _got == _want else 'FAIL'}] {_lbl:52} -> {_got}")
    results.append(_got == _want)
_ok = _config.REV_A_REQUIRE_LEVEL is True
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ค่าระบบจริง: Retracement ต้องอยู่ที่แนว (2026-10-23)':52}")
results.append(_ok)
_ok = _config.PULLBACK_LEVEL_INSTEAD_OF_ADX is False
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ค่าระบบจริง: ยังใช้ ADX (สวิตช์ปิด)':52}")
results.append(_ok)

# ด่านอายุการสวน (config.PULLBACK_MIN_DISAGREE_BARS · ระบบจริง 6 ตั้งแต่ 2026-10-20) — Structure ย้อนหลังทีละแท่งปลอมด้วย
# check_structure_reg ที่คืนตามความยาว df (แท่ง 4H ปิดแล้ว 20 แท่ง + แท่ง live 1 = แท่งล่าสุดคือความยาว 20)
import regime_check as _rc  # noqa: E402
_saved_csr, _saved_usr = _rc.check_structure_reg, _rc.USE_STRUCT_REG


def dis_check(label, flip_len, want_n, need=6):
    """Structure สวน Bias (Short) ที่ความยาว df ≥ flip_len · ก่อนหน้านั้นตรง Bias (Long)"""
    _rc.USE_STRUCT_REG = True
    _rc.check_structure_reg = lambda d, sym="": {"trend": "Short (reg)" if len(d) >= flip_len else "Long (reg)"}
    ri = {**_RI, "df_4h": pd.DataFrame({"close": [1.0] * 21}), "symbol": "X"}
    n = pullback.disagree_bars(ri, "Long", need)
    got = pullback.pullback_direction(ri, "Long", min_disagree_bars=need)[0]
    ok = n == want_n and got == ("Long" if want_n >= need else None)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> สวน {n} แท่ง · {got}")
    return ok


results += [
    dis_check("สวนมา 6 แท่งพอดี -> เข้า", 15, 6),
    dis_check("สวนมานานกว่านั้น -> เข้า (นับถึง 6 พอ)", 1, 6),
    dis_check("เพิ่งสวน 2 แท่ง -> ไม่เข้า", 19, 2),
]
_got = pullback.pullback_direction(_RI, "Long", min_disagree_bars=6)[0]
print(f"  [{'ok  ' if _got is None else 'FAIL'}] {'ด่านเปิด แต่ไม่มี df_4h -> ไม่เข้า (ไม่ crash)':52} -> {_got}")
results.append(_got is None)
_rc.check_structure_reg, _rc.USE_STRUCT_REG = _saved_csr, _saved_usr

_bars = pd.DataFrame({"high": [110.0, 108.0], "low": [100.0, 104.0]})
for _lbl, _args, _want in (("Long ราคา 103 ในกรอบ 100-110 -> 0.3", (_bars, 103.0, "Long"), 0.3),
                           ("Short ราคา 107 ในกรอบ 100-110 -> 0.3", (_bars, 107.0, "Short"), 0.3),
                           ("กรอบแบน -> 0.5", (pd.DataFrame({"high": [5.0], "low": [5.0]}), 5.0, "Long"), 0.5)):
    _v = pullback.range_position(*_args)
    _ok = abs(_v - _want) < 1e-9
    print(f"  [{'ok  ' if _ok else 'FAIL'}] {_lbl:52} -> {_v:.2f}")
    results.append(_ok)

# ไม้เงา — ไฟล์ state ชี้ไป temp · นาฬิกา MT5 ตรึง · แท่ง 1H ปลอม (แท่งสุดท้าย = ยังไม่ปิด)
_ghost_tmp = os.path.join(tempfile.mkdtemp(), "pullback_ghost.json")
_saved_rn = (scheduler._PULLBACK_GHOST_FILE, scheduler.mt5_now, scheduler.get_ohlcv, scheduler.PULLBACK_ENABLED,
             scheduler.PULLBACK_MIN_RANGE_POS, scheduler.PULLBACK_MIN_DISAGREE_BARS)
# regime ปลอมของเทสต์ scan_symbol ไม่มีแท่ง 4H -> ปิดด่านอายุการสวนไว้ (ทดสอบแยกข้างบนแล้ว)
scheduler.PULLBACK_MIN_DISAGREE_BARS = 0
# กลไกไม้เงา/ด่าน 4e ทดสอบที่เส้น 0.3 เสมอ (ค่าระบบจริงเป็น 0 ตั้งแต่ 2026-10-19 · กลไกยังเก็บไว้ถอยกลับได้)
scheduler._PULLBACK_GHOST_FILE, scheduler.PULLBACK_ENABLED, scheduler.PULLBACK_MIN_RANGE_POS = _ghost_tmp, True, 0.3
_T0 = pd.Timestamp("2026-10-13 10:05").to_pydatetime()


def _h1(rows):
    """rows = [(เวลา, high, low)] — ต่อท้ายแท่งที่ยังไม่ปิดให้เอง (แตะ SL/TP ได้แต่ต้องไม่ถูกนับ)"""
    df = pd.DataFrame(rows, columns=["time", "high", "low"])
    df["time"] = pd.to_datetime(df["time"])
    return pd.concat([df, pd.DataFrame({"time": [df["time"].iloc[-1] + pd.Timedelta(hours=1)],
                                        "high": [9e9], "low": [-9e9]})], ignore_index=True)


def ghost_check(label, direction, now, bars, want_active):
    with contextlib.redirect_stdout(io.StringIO()):
        scheduler.mt5_now = lambda: _T0
        scheduler.pullback_ghost_set("XAUUSDm", direction, 4000.0 if direction == "Long" else 4300.0,
                                   4300.0 if direction == "Long" else 4000.0)
        scheduler.mt5_now = lambda: now
        scheduler.get_ohlcv = lambda *a, **k: bars
        g = scheduler.pullback_ghost_active("XAUUSDm")
    active = g is not None
    kept = "XAUUSDm" in scheduler._ghost_load()
    ok = active == want_active and kept == want_active
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {'ยังไม่จบ' if active else 'จบ (ลบจากไฟล์)' if not kept else 'จบแต่ไฟล์ค้าง'}")
    return ok


_T5 = _T0 + pd.Timedelta(hours=5)
print("\nPullback — ไม้เงา (scheduler.pullback_ghost_*):")
results += [
    ghost_check("Long ยังไม่แตะ SL/TP -> ยังไม่จบ", "Long", _T5,
                _h1([("2026-10-13 10:00", 4100, 4050), ("2026-10-13 11:00", 4120, 4060)]), True),
    ghost_check("Long แท่งปิดแล้วแตะ SL -> จบ", "Long", _T5,
                _h1([("2026-10-13 10:00", 4100, 4050), ("2026-10-13 11:00", 4080, 3999)]), False),
    ghost_check("Long แตะ TP -> จบ", "Long", _T5,
                _h1([("2026-10-13 10:00", 4300, 4050), ("2026-10-13 11:00", 4120, 4060)]), False),
    ghost_check("แตะ SL ก่อนแท่งที่สร้างไม้เงา -> ไม่นับ", "Long", _T5,
                _h1([("2026-10-13 09:00", 4100, 3990), ("2026-10-13 10:00", 4100, 4050)]), True),
    ghost_check("แท่งที่ยังไม่ปิดแตะ -> ไม่นับ", "Long", _T5,
                _h1([("2026-10-13 10:00", 4100, 4050)]), True),
    ghost_check("Short แตะ SL (high ≥ SL) -> จบ", "Short", _T5,
                _h1([("2026-10-13 10:00", 4301, 4150)]), False),
    ghost_check("ครบ MAX_HOLD_DAYS -> จบ", "Long", _T0 + pd.Timedelta(days=31),
                _h1([("2026-10-13 10:00", 4100, 4050)]), False),
    ghost_check("ดึงแท่ง 1H ไม่ได้ -> ถือว่ายังไม่จบ", "Long", _T5, None, True),
]


def rn_scan(on_book, bias, ghost):
    """scan_symbol ที่ regime CHOPPY + สัญญาณ Pullbackครบ (ADX 15 = เขต Sideway ด้วย)"""
    if ghost:
        scheduler.mt5_now = lambda: _T0
        scheduler.pullback_ghost_set("XAUUSDm", "Long", 4000.0, 4300.0)
    else:
        scheduler._ghost_save({})
    scheduler.mt5_now = lambda: _T5
    return scan_output(on_book, "CHOPPY", regime_extra={**_RI, "adx_now": 15.0}, bias_1d=bias)


def route_check(label, on_book, bias, ghost, want):
    out = rn_scan(on_book, bias, ghost)
    got = ("Pullback" if "Pullback Long —" in out else
           "ไม้เงาบล็อก" if "ไม้เงา Long ยังไม่จบ" in out else
           "ช่อง Scoring เต็ม" if "ช่อง Scoring มีไม้เปิดอยู่แล้ว" in out else
           "Sideway/อื่น" if "ดึงราคาไม่ได้" in out else "ไม่เข้า")
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {got}")
    return ok


print("\nPullback — ทางของ scan_symbol:")
results += [
    route_check("ช่องว่าง + สัญญาณครบ -> Pullback (ก่อน Sideway)", [], "Long", False, "Pullback"),
    route_check("ถือ Scoring อยู่ -> ช่อง Reversal ว่าง -> Pullback", ["Scoring"], "Long", False, "Pullback"),
    route_check("ถือ Breakout (ช่อง Scoring) -> ช่อง Reversal ว่าง -> Pullback", ["Breakout"], "Long", False, "Pullback"),
    route_check("ไม้เงายังไม่จบ -> ไม่เข้า Pullback", [], "Long", True, "ไม้เงาบล็อก"),
    route_check("Bias ตรง Structure -> ไม่ใช่ Pullback", [], "Short", False, "Sideway/อื่น"),
]
scheduler.PULLBACK_MIN_RANGE_POS = 0.0
results.append(route_check("ด่านปิด (0) + ไม้เงาค้างในไฟล์ -> ข้ามไม้เงา -> Pullback", [], "Long", True, "Pullback"))
scheduler.PULLBACK_MIN_RANGE_POS = 0.3


def gate_scan(close_pos):
    """เดิน scan_symbol จนถึงด่าน 4e — tick/SL/TP/แท่ง 1H/lot/order ปลอมทั้งหมด (ไม่มีคำสั่งไป broker)
    กรอบ 24 แท่ง 1H = 100-110 · ราคาเข้า = 100 + 10 × close_pos · คืน (ข้อความ, สิ่งที่ถูกเรียก, ไม้เงาในไฟล์)"""
    scheduler._ghost_save({})
    scheduler.mt5_now = lambda: _T5
    calls = []
    h1 = pd.DataFrame({"time": pd.date_range("2026-10-12 08:00", periods=27, freq="h"),
                       "high": [110.0] * 27, "low": [100.0] * 27})
    px = 100.0 + 10.0 * close_pos

    def fake_entry(*a, **k):
        calls.append(("compute", k.get("tp_ratio"), k.get("fallback_rr"), k.get("min_rr")))
        return {"sl": 97.0, "exec_sl": 95.0, "tp": 120.0, "rr": 1.6, "atr_entry": 2.0}

    def fake_order(*a, **k):
        calls.append(("order", k.get("strategy")))
        return 999

    out = scan_output(
        [], "CHOPPY", regime_extra={**_RI, "adx_now": 15.0, "df_4h": None}, bias_1d="Long",
        ohlcv=lambda s, tf=None, bars=None, **k: h1 if tf == _config.MT5_TIMEFRAMES["1H"] else None,
        tick=SimpleNamespace(bid=px, ask=px),
        extra_patches={(reversal, "compute_reversal_entry"): fake_entry,
                       (scheduler, "calculate_lot_size"): lambda *a, **k: (0.1, None),
                       (scheduler, "clamp_lot"): lambda s, l: l,
                       (scheduler, "risk_pct_of"): lambda *a, **k: 2.0,
                       (scheduler, "place_order"): fake_order})
    return out, calls, scheduler._ghost_load().get("XAUUSDm")


print("\nPullback — ด่านสุดท้าย 4e (ตำแหน่งในกรอบ 24 ชม.):")
_want_compute = [("compute", _config.PULLBACK_TP_FIB_RATIO, _config.PULLBACK_FALLBACK_RR,
                  _config.PULLBACK_MIN_RR)]
_out, _calls, _g = gate_scan(0.2)
_orders = [c for c in _calls if c[0] == "order"]
_ok = (not _orders and _g is not None and (_g["direction"], _g["sl"], _g["tp"]) == ("Long", 95.0, 120.0)
       and "เริ่มไม้เงา" in _out)
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ตำแหน่ง 0.2 -> ไม่ส่ง order · ไม้เงา SL=exec_sl TP=tp':52} -> "
      f"{'ส่ง order' if _orders else 'ไม่ส่ง'} · ไม้เงา {_g}")
results.append(_ok)
_out, _calls, _g = gate_scan(0.5)
_orders = [c for c in _calls if c[0] == "order"]
_comp = [c for c in _calls if c[0] == "compute"]
_ok = _orders == [("order", "Pullback")] and _g is None and _comp == _want_compute
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ตำแหน่ง 0.5 -> ส่ง order Pullback · TP/สำรองค่าใหม่':52} -> "
      f"{_orders or 'ไม่ส่ง'} · {_comp}")
results.append(_ok)
scheduler.PULLBACK_MIN_RANGE_POS = 0.0
_out, _calls, _g = gate_scan(0.2)
_orders = [c for c in _calls if c[0] == "order"]
_ok = _orders == [("order", "Pullback")] and _g is None and "เริ่มไม้เงา" not in _out
print(f"  [{'ok  ' if _ok else 'FAIL'}] {'ด่านปิด (0): ตำแหน่ง 0.2 -> ส่ง order · ไม่มีไม้เงา':52} -> "
      f"{_orders or 'ไม่ส่ง'} · ไม้เงา {_g}")
results.append(_ok)
(scheduler._PULLBACK_GHOST_FILE, scheduler.mt5_now, scheduler.get_ohlcv, scheduler.PULLBACK_ENABLED,
 scheduler.PULLBACK_MIN_RANGE_POS, scheduler.PULLBACK_MIN_DISAGREE_BARS) = _saved_rn

# ---------------------------------------------------------------------------
# Reversal แบบ A (2026-10-15 · rev_a.py + scheduler.rev_a_*)
# ---------------------------------------------------------------------------
import rev_a  # noqa: E402

print("\nReversal A — กฎสัญญาณ (rev_a.signal_direction):")


def _ri(peak=30.0, declining=True, struct="Short (reg)", div="bullish"):
    return {"peak": {"peak": peak, "declining": declining}, "structure": {"trend": struct},
            "divergence": {"divergence": div}}


for _lab, _ri_, _b, _want in [
        ("เทรนด์ Short + bullish div + ADX peak 30 โค้งลง -> Long", _ri(), "Short", "Long"),
        ("เทรนด์ Long + bearish div -> Short", _ri(struct="Long (reg)", div="bearish"), "Long", "Short"),
        ("peak 25 < 28.5 -> ไม่เข้า", _ri(peak=25.0), "Short", None),
        ("ADX ยังไม่โค้งลง -> ไม่เข้า", _ri(declining=False), "Short", None),
        ("Bias สวน Structure -> ไม่เข้า (ทาง Pullback)", _ri(), "Long", None),
        ("divergence ทางเดียวกับเทรนด์ -> ไม่เข้า", _ri(div="bearish"), "Short", None),
        ("regime ไม่ครบคีย์ -> ไม่เข้า (ไม่ crash)", {}, "Short", None)]:
    _got = rev_a.signal_direction(_ri_, _b)[0]
    _ok = _got == _want
    print(f"  [{'ok  ' if _ok else 'FAIL'}] {_lab:52} -> {_got}")
    results.append(_ok)


_RA_PRE = 100   # แท่งนำหน้า — rev_a_step ต้องมีแท่ง 1H ≥ REV_A_CONFIRM_HOURS + 60 ไม่งั้นข้ามรอบ


def _ra_h1(n_closed, breakout_close=101.0, forming=True, pre=0):
    """แท่ง 1H: 0..29 กรอบ 95-100 (แท่ง 25 ก้น 90) · แท่ง 30 ปิด breakout_close · ต่อจากนั้นกรอบเดิม
    pre = แท่งกรอบเดิมนำหน้า (เลขแท่งในข้อความ = นับหลัง pre)"""
    rows = []
    for i in range(-pre, n_closed + (1 if forming else 0)):
        hi, lo, c = 100.0, 95.0, 97.0
        if i == 25:
            lo = 90.0
        if i == 30:
            hi, c = max(100.0, breakout_close + 0.5), breakout_close
        rows.append((pd.Timestamp("2026-10-01 00:00") + pd.Timedelta(hours=i), hi, lo, c))
    return pd.DataFrame(rows, columns=["time", "high", "low", "close"])


print("\nReversal A — ยืนยันบนแท่ง 1H (rev_a.confirm):")
_h = _ra_h1(31, forming=False)
_c29 = rev_a.confirm(_h, 29, 20, "Long", 150.0)
_c30 = rev_a.confirm(_h, 30, 20, "Long", 150.0)
_c30b = rev_a.confirm(_h, 30, 20, "Long", 130.0)
for _lab, _ok, _got in [
        ("แท่งยังไม่ปิดเหนือ high 12 แท่ง -> ยังไม่ยืนยัน", _c29 is None, _c29),
        ("ปิดทะลุ -> limit 100 · SL 89 · TP 120 · R:R 1.82", _c30 is not None and _c30["ok"]
         and (_c30["entry"], round(_c30["sl"], 6), round(_c30["tp"], 6)) == (100.0, 89.0, 120.0),
         _c30 and (_c30["entry"], _c30["sl"], _c30["tp"], round(_c30["rr"], 2))),
        ("ต้นขาใกล้ (TP 110 · R:R 0.91) -> ยืนยันแต่ไม่เข้า", _c30b is not None and not _c30b["ok"],
         _c30b and _c30b["why"])]:
    print(f"  [{'ok  ' if _ok else 'FAIL'}] {_lab:52} -> {_got}")
    results.append(_ok)


class _FakeOrder:
    def __init__(self, t):
        self.ticket = t


_ra_calls = []
_ra_state = {"positions": [], "orders": [555]}


def ra_scan(n_closed, occupied=frozenset(), state=None, enabled=True, leg=150.0, breakout_close=101.0,
            entry_mode="limit", tick_px=101.0, at_level=True):
    """รัน scheduler.rev_a_step("XAUUSDm") หนึ่งรอบด้วยแท่งปลอม n_closed แท่งที่ปิดแล้ว · คืน (สถานะหลังรอบ, ข้อความ)
    entry_mode: วงจร limit (เดิม · ยังดูแล limit ที่ค้างตอน deploy) ทดสอบด้วย "limit" · ระบบจริงตั้งแต่ 2026-10-21 = market"""
    _ra_calls.clear()
    if state is not None:
        scheduler._rev_a_save({"XAUUSDm": state} if state else {})
    h1 = _ra_h1(n_closed, breakout_close, pre=_RA_PRE)
    patches = {
        (scheduler, "get_ohlcv"): lambda s, tf, bars=None, **k: h1 if tf == scheduler.MT5_TIMEFRAMES["1H"] else None,
        (scheduler, "get_regime"): lambda s: {**_ri(), "df_4h": None},
        (scheduler, "get_trend_bias"): lambda s, df: ("Short", "ทดสอบ"),
        (scheduler.rev_a, "swing_leg"): lambda r, s, lg: leg,
        # config.REV_A_REQUIRE_LEVEL (2026-10-23): ชุดนี้ทดสอบวงจรเฝ้า/ยืนยัน -> ถือว่าอยู่ที่แนว · กรณีไม่อยู่ที่แนวทดสอบแยก
        (scheduler.levels, "divergence_at_level"): lambda r, d: at_level,
        (scheduler, "REV_A_ENABLED"): enabled,
        (scheduler.mt5, "positions_get"): lambda **k: [p for p in _ra_state["positions"] if p == k.get("ticket")],
        (scheduler.mt5, "orders_get"): lambda **k: [_FakeOrder(t) for t in _ra_state["orders"]
                                                    if "ticket" not in k or t == k["ticket"]],
        (scheduler.mt5, "history_deals_get"): lambda **k: [],
        (scheduler, "place_limit_order"): lambda *a, **k: (_ra_calls.append(("limit",) + a), 555)[1],
        (scheduler, "cancel_pending"): lambda t: (_ra_calls.append(("cancel", t)), True)[1],
        (journal, "log_trade_open"): lambda *a, **k: _ra_calls.append(("journal", k.get("strategy"))),
        (journal, "remove_unfilled"): lambda t: _ra_calls.append(("unjournal", t)),
        (journal, "check_daily_loss"): lambda b, m: True,
        (journal, "check_cooldown"): lambda s, h: (True, "OK"),
        (journal, "check_tp_cooldown"): lambda s, h: (True, "OK"),
        (scheduler, "check_portfolio_risk"): lambda s, b: (True, ""),
        (scheduler, "get_account_balance"): lambda: BALANCE,
        (scheduler, "calculate_lot_size"): lambda *a, **k: (_ra_calls.append(("risk", a[-1])), (0.1, None))[1],
        (scheduler, "clamp_lot"): lambda s, l: l,
        (scheduler.notify, "send"): lambda *a, **k: True,
        (scheduler.notify, "notify_order_opened"): lambda *a, **k: None,
        (scheduler, "is_demo_account"): lambda: True,
        (scheduler, "REV_A_ENTRY"): entry_mode,
        (scheduler, "place_order"): lambda *a, **k: (_ra_calls.append(("market",) + a + (k.get("strategy"), k.get("label"))),
                                                     777)[1],
        (scheduler.mt5, "symbol_info_tick"): lambda s: SimpleNamespace(ask=tick_px, bid=tick_px),
    }
    saved = {k: getattr(*k) for k in patches}
    for (obj, name), fn in patches.items():
        setattr(obj, name, fn)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            scheduler.rev_a_step("XAUUSDm", occupied if isinstance(occupied, set) else set(occupied))
    finally:
        for (obj, name), fn in saved.items():
            setattr(obj, name, fn)
    return scheduler._rev_a_load().get("XAUUSDm"), buf.getvalue()


def ra_check(label, ok, got):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label:52} -> {got}")
    results.append(bool(ok))


print("\nReversal A — วงจร watch -> limit -> เติม/ยกเลิก (scheduler.rev_a_step):")
_t = lambda i: (pd.Timestamp("2026-10-01 00:00") + pd.Timedelta(hours=i)).isoformat()
_st_nl, _out_nl = ra_scan(30, state={}, at_level=False)
ra_check("divergence ไม่อยู่ที่แนว -> ไม่เริ่มเฝ้า (REV_A_REQUIRE_LEVEL)", _st_nl is None or not _st_nl.get("watch"),
         _st_nl)
_st, _ = ra_scan(30, state={})
ra_check("สัญญาณ -> เริ่มเฝ้า (sig_bar = แท่งปิดล่าสุด)", _st and _st.get("watch", {}).get("sig_bar") == _t(29)
         and _st["watch"]["direction"] == "Long", _st)
_st2, _ = ra_scan(30)
ra_check("รอบเดียวกันซ้ำ -> ยังไม่เช็ค (เริ่มแท่งถัดไป)", _st2 == _st and not _ra_calls, _ra_calls)
_st, _ = ra_scan(31)
ra_check("แท่งถัดไปปิดทะลุ -> ตั้ง limit Long 100 / SL 89 / TP 120",
         [c for c in _ra_calls if c[0] == "limit"] == [("limit", "XAUUSDm", "Long", 100.0, 89.0, 120.0, 0.1)]
         and ("journal", "Retracement") in _ra_calls and _st.get("pend", {}).get("exp_bar") == _t(54), _ra_calls)
_pend = dict(_st)
_st, _ = ra_scan(32, state=_pend)
ra_check("limit ค้าง ไม่มีด่านปิด -> ไม่ทำอะไร", _st == _pend and not _ra_calls, _ra_calls)
_st, _ = ra_scan(32, state=_pend, occupied={"Reversal"})
ra_check("ช่อง Reversal มีไม้แล้ว -> ยกเลิก limit + ลบแถว journal",
         _st is None and ("cancel", 555) in _ra_calls and ("unjournal", 555) in _ra_calls, _ra_calls)
_st, _ = ra_scan(55, state=_pend)
ra_check("ครบ 24 ชม. -> ยกเลิก", _st is None and ("cancel", 555) in _ra_calls, _ra_calls)
_ra_state["positions"] = [555]
_st, _ = ra_scan(33, state=_pend, occupied={"Reversal"})      # เติมแล้ว = position อยู่ในช่อง Reversal ตั้งแต่ต้นรอบ
ra_check("ถูกเติมแล้ว (มี position เลขเดียวกัน) -> ล้างสถานะ ไม่ยกเลิก",
         _st is None and not any(c[0] == "cancel" for c in _ra_calls), _ra_calls)
_ra_state["positions"] = []
_st, _ = ra_scan(33, state=_pend, enabled=False)
ra_check("ปิดกลยุทธ์ (REV_A_ENABLED False) -> ยกเลิก limit ที่ค้าง", _st is None and ("cancel", 555) in _ra_calls,
         _ra_calls)
_watch = {"watch": {"direction": "Long", "sig_bar": _t(29), "exp_bar": _t(77), "checked_to": _t(29), "leg": 130.0}}
_st, _out = ra_scan(31, state=_watch, leg=130.0)
ra_check("ยืนยันแต่ R:R 0.91 -> ทิ้ง ไม่ตั้ง limit", not any(c[0] == "limit" for c in _ra_calls)
         and (_st is None or "pend" not in _st) and "ไม่เข้า" in _out, _ra_calls)
_st, _ = ra_scan(80, state={**_watch, "watch": {**_watch["watch"], "leg": 150.0}})
ra_check("เกิน 48 ชม. ไม่ยืนยัน -> ทิ้ง (หาสัญญาณใหม่ต่อได้)",
         not any(c[0] == "limit" for c in _ra_calls) and (_st is None or _st.get("watch", {}).get("sig_bar") == _t(79)),
         _st)
_st, _out = ra_scan(33, state={**_watch, "watch": {**_watch["watch"], "leg": 150.0}})
ra_check("สแกนหลุด แท่งยืนยันอยู่ในช่วงที่หลุด -> ทิ้ง ไม่ตั้งย้อนหลัง",
         not any(c[0] == "limit" for c in _ra_calls) and "สแกนหลุด" in _out, _ra_calls)
_ra_state["orders"] = []
_st, _ = ra_scan(32, state=_pend)
ra_check("order หายไปเอง ไม่มี deal -> ลบแถว journal + ล้าง limit", (_st is None or "pend" not in _st)
         and ("unjournal", 555) in _ra_calls, _ra_calls)
_ra_state["orders"] = [555]

print("\nReversal A — เข้า market ตอนยืนยัน (REV_A_ENTRY = market · ระบบจริงตั้งแต่ 2026-10-21):")
_w150 = {**_watch, "watch": {**_watch["watch"], "leg": 150.0}}
_st, _ = ra_scan(31, state=_w150, entry_mode="market", tick_px=101.0)
_mk = [c for c in _ra_calls if c[0] == "market"]
ra_check("ยืนยัน -> market Long @ 101 / SL 89 / TP 120 · journal Retracement",
         _mk == [("market", "XAUUSDm", "Long", 101.0, 89.0, 120.0, 0.1, "Retracement", None)]
         and not any(c[0] == "limit" for c in _ra_calls) and _st is None, _ra_calls)
ra_check("ขนาดไม้คิดที่ความเสี่ยง 1% (REV_A_RISK_PER_TRADE · 2026-10-21)", ("risk", 0.01) in _ra_calls, _ra_calls)
_occ = set()
_st, _ = ra_scan(31, state=_w150, entry_mode="market", tick_px=101.0, occupied=_occ)
ra_check("เข้าแล้ว -> ช่อง Reversal ถูกจองในรอบนี้ (กันกลยุทธ์อื่นซ้อน)", _occ == {"Reversal"}, _occ)
_st, _out = ra_scan(31, state=_w150, entry_mode="market", tick_px=104.0)
ra_check("ราคาตลาดวิ่งไป 104 -> R:R 1.07 < 1.5 -> ไม่เข้า (R:R คิดจากราคาจริง)",
         not any(c[0] == "market" for c in _ra_calls) and "ไม่เข้า" in _out, _ra_calls)
_st, _out = ra_scan(31, state=_w150, entry_mode="market", tick_px=101.0, occupied={"Reversal"})
ra_check("ช่อง Reversal มีไม้แล้ว -> ไม่เข้า ไม่ตั้ง limit",
         not any(c[0] in ("market", "limit") for c in _ra_calls) and "ช่อง Reversal" in _out, _ra_calls)
_saved_ra_file = scheduler._REV_A_FILE


class _FakeLimit:
    """limit ของแบบ A ที่ค้าง — ความเสี่ยง = ระยะ limit -> SL × lot (เหมือน position)"""
    def __init__(self, p, magic):
        self.symbol, self.price_open, self.sl, self.volume_current = p.symbol, p.price_open, p.sl, p.volume
        self.type = mt5.ORDER_TYPE_BUY_LIMIT if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT
        self.magic = magic


print("\nReversal A — limit ที่ค้างนับในเพดานความเสี่ยงรวม (check_portfolio_risk):")
_saved_og = scheduler.mt5.orders_get
_cap = scheduler.MAX_PORTFOLIO_RISK_R
for _lab, _orders, _want in [
        ("เปิดอยู่ 5R + limit ค้าง 1R + ไม้ใหม่ -> บล็อก", [_FakeLimit(pos_at("XAUUSDm", "Long", 1.0), order.MAGIC_NUMBER)], False),
        ("เปิดอยู่ 5R + order ของคนอื่น (magic อื่น) -> ผ่าน", [_FakeLimit(pos_at("XAUUSDm", "Long", 1.0), 1)], True)]:
    scheduler.mt5.orders_get = lambda o=_orders, **k: o
    results.append(check(_lab, [pos_at("US500m", "Long", 1.0) for _ in range(int(_cap) - 1)], "BTCUSDm", _want))
scheduler.mt5.orders_get = _saved_og
ra_check("ไฟล์สถานะไม่ใช่ของบอทจริง", os.path.dirname(_saved_ra_file) != os.path.dirname(scheduler.__file__),
         _saved_ra_file)

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
