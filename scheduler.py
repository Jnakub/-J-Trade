"""
scheduler.py — รันค้างไว้ เช็คทุก 1 ชั่วโมงอัตโนมัติ
ใช้: python scheduler.py
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import journal
import config
from config import (
    SYMBOLS, RISK_PER_TRADE,
    MAX_DAILY_LOSS, MT5_TIMEFRAMES,
    COOLDOWN_HOURS_BY_SYMBOL, TP_COOLDOWN_HOURS, MAX_RUNUP_24H_R, MIN_TURN_FROM_EXTREME_R,
    REJECT_COOLDOWN_HOURS, LOT_RISK_WARN_PCT, TP_MAX_ATR,
    SLOT_PER_STRATEGY, REVERSAL_SHORT_NEEDS_1D_TREND,
    MAX_PORTFOLIO_RISK_R, MAX_GROUP_RISK_R, CORRELATION_GROUPS,
    SCORING_NEEDS_STRUCTURE_MATCH, REVERSAL_NEEDS_CHOCH,
    BREAKOUT_ENABLED, BREAKOUT_TP_FIB_RATIO, slot_of, TP_TO_ENTRY_ON_OPPOSITE, TP_TO_ENTRY_REVERSAL_TRIGGERS,
    BREAKOUT_IGNORES_MIN_PEAK, SIDEWAY_ENABLED, SIDEWAY_RISK_PER_TRADE,
    PULLBACK_ENABLED, REVERSAL_ENABLED, PULLBACK_ADX_RULE, PULLBACK_MIN_RANGE_POS, PULLBACK_MIN_DISAGREE_BARS,
    PULLBACK_TP_FIB_RATIO, PULLBACK_FALLBACK_RR, PULLBACK_MIN_RR,
    REV_A_ENABLED, REV_A_CONFIRM_HOURS, REV_A_LIMIT_HOURS, REV_A_BREAK_BARS, REV_A_ENTRY, REV_A_RISK_PER_TRADE, REV_A_STRATEGY,
)
from mt5_connect import connect, get_account_balance, mt5_now, is_demo_account
from scoring import compute_entry, calc_rr, get_ohlcv, get_trend_bias, EXEC_SL_ATR_MULT
from indicators import calc_atr
from order import (calculate_lot_size, clamp_lot, place_order, position_risk_amount,
                   risk_pct_of, modify_sltp, place_limit_order, cancel_pending, MAGIC_NUMBER)
import swing
from exit_monitor import (
    check_structure_break, MAX_HOLD_DAYS,
    analyze_position, print_report, execute_decision,
    check_upcoming_news, NEWS_IMMINENT_H, NEWS_IMPACT, NEWS_CURRENCY,
)
from regime_check import get_regime
import regime_check
import sideway
import reversal
import pullback
import rev_a
import notify
from logger_setup import get_logger, tee_print

log = get_logger("scheduler")

# ---------------------------------------------------------------------------
# state ของ cooldown หลังถูกด่านปฏิเสธ (config.REJECT_COOLDOWN_HOURS)
# ---------------------------------------------------------------------------
# เก็บลงไฟล์ ไม่ใช่ตัวแปรในหน่วยความจำ — scheduler รันเป็น while True ก็จริง แต่ถ้าโปรเซส
# รีสตาร์ท (crash / reboot / แก้โค้ดแล้วรันใหม่) cooldown ที่ค้างอยู่จะหายเงียบๆ แล้วสัญญาณ
# ที่เพิ่งถูกปฏิเสธจะเข้าได้ทันทีในรอบถัดไป = พฤติกรรมต่างจาก backtest โดยไม่มีอะไรฟ้อง
# ซึ่งเป็นรูปแบบความผิดพลาดที่แพงที่สุดของโปรเจกต์นี้ (ตัวเลขผิดแบบเงียบ ไม่ใช่โค้ด crash)
_REJECT_CD_FILE = os.path.join(os.path.dirname(__file__), "reject_cooldown.json")


def _reject_cd_load() -> dict:
    try:
        with open(_REJECT_CD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}      # ไฟล์ยังไม่มี/พัง = ถือว่าไม่มีใครติด cooldown (fail-open ตามพฤติกรรมเดิม)


def reject_cd_blocked(symbol: str, direction: str):
    """คืนเวลาที่จะปลดล็อก ถ้า symbol+ทิศนี้ยังติด cooldown อยู่ · None = เข้าได้"""
    if not REJECT_COOLDOWN_HOURS:
        return None
    raw = _reject_cd_load().get(f"{symbol}|{direction}")
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return until if datetime.now() < until else None


def reject_cd_set(symbol: str, direction: str) -> None:
    """ตั้ง cooldown ให้ symbol+ทิศนี้ — เรียกตอนด่านปฏิเสธเท่านั้น"""
    if not REJECT_COOLDOWN_HOURS:
        return
    data = _reject_cd_load()
    data[f"{symbol}|{direction}"] = (datetime.now()
                                     + timedelta(hours=REJECT_COOLDOWN_HOURS)).isoformat()
    # ล้างรายการที่หมดอายุแล้วทิ้งไปด้วย กันไฟล์โตไม่รู้จบ
    now = datetime.now()
    data = {k: v for k, v in data.items()
            if (lambda t: t is not None and t > now)(_parse_iso(v))}
    try:
        with open(_REJECT_CD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"  ⚠️ เขียน {_REJECT_CD_FILE} ไม่ได้ ({exc}) — cooldown รอบนี้จะไม่ถูกจำ")


# ---------------------------------------------------------------------------
# ไม้เงาของ Pullback (config.PULLBACK_MIN_RANGE_POS · 2026-10-13)
# ---------------------------------------------------------------------------
# ด่านตำแหน่งในกรอบ 24 ชม. บล็อกไม้ Pullback -> จำ SL/TP ของไม้ที่ไม่ได้เข้า ("ไม้เงา") แล้วห้ามเข้า
# Pullback ใน symbol นั้นจนกว่าราคาแตะ SL หรือ TP ของมัน (หรือครบ MAX_HOLD_DAYS)
# ไม่มีไม้เงา = สัญญาณเดิมยังค้าง ไม้ที่ถูกบล็อกกลับมาเข้าใน median 3 ชม. ผลเท่าเดิม (replay 2026-10-12: ด่านได้ 0)
# ตรงกับ backtest_replay._rn_ghost: เช็คแท่ง 1H ที่ปิดแล้วตั้งแต่แท่งที่เปิดตอนสร้างไม้เงา (replay เช็คจากแท่งถัดไป
# = แท่งที่เปิดตรง "now" ของรอบที่บล็อก) · เก็บลงไฟล์เหตุผลเดียวกับ reject_cooldown.json (รีสตาร์ทแล้วต้องไม่หาย)
_PULLBACK_GHOST_FILE = os.path.join(os.path.dirname(__file__), "pullback_ghost.json")


def _ghost_load() -> dict:
    try:
        with open(_PULLBACK_GHOST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ghost_save(data: dict) -> None:
    try:
        with open(_PULLBACK_GHOST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"  ⚠️ เขียน {_PULLBACK_GHOST_FILE} ไม่ได้ ({exc}) — ไม้เงารอบนี้จะไม่ถูกจำ")


def pullback_ghost_set(symbol: str, direction: str, sl: float, tp: float) -> None:
    """เริ่มไม้เงาของ symbol นี้ (ทับของเดิม) — เรียกตอนด่านตำแหน่งบล็อกเท่านั้น · เวลาเป็นนาฬิกา MT5 (UTC)"""
    now = mt5_now()
    data = _ghost_load()
    data[symbol] = {"direction": direction, "sl": float(sl), "tp": float(tp),
                    "since": now.replace(minute=0, second=0, microsecond=0).isoformat(),
                    "until": (now + timedelta(days=MAX_HOLD_DAYS)).isoformat()}
    _ghost_save(data)


def pullback_ghost_active(symbol: str):
    """ไม้เงาที่ยังไม่จบของ symbol นี้ (dict) หรือ None — ถ้าราคาแตะ SL/TP แล้วหรือครบเพดาน จะลบทิ้งให้เลย
    ดึงแท่ง 1H ไม่ได้ = ถือว่ายังไม่จบ (ไม่เปิด Pullback ดีกว่าเปิดไม้ที่ด่านตั้งใจกันไว้)"""
    data = _ghost_load()
    g = data.get(symbol)
    if not g:
        return None
    since, until, now = _parse_iso(g.get("since")), _parse_iso(g.get("until")), mt5_now()
    done = since is None or until is None or now >= until
    if not done:
        bars = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=int((now - since).total_seconds() // 3600) + 3)
        if bars is None or len(bars) < 2:
            return g
        seg = bars.iloc[:-1]                       # แท่งสุดท้าย = แท่งที่ยังไม่ปิด
        seg = seg[seg["time"] >= since]
        if g["direction"] == "Long":
            done = bool(((seg["low"] <= g["sl"]) | (seg["high"] >= g["tp"])).any())
        else:
            done = bool(((seg["high"] >= g["sl"]) | (seg["low"] <= g["tp"])).any())
    if done:
        data.pop(symbol, None)
        _ghost_save(data)
        print(f"  [{symbol}] ไม้เงา Pullback {g.get('direction')} จบแล้ว (แตะ SL/TP หรือครบเพดาน) — ปลดล็อก")
        return None
    return g


def _parse_iso(raw: str):
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Reversal แบบ A (config.REV_A_* · กฎเข้าที่ rev_a.py · 2026-10-15)
# ---------------------------------------------------------------------------
# สามสถานะต่อ symbol (ทีละอย่าง) เก็บใน rev_a_state.json — รีสตาร์ทแล้วต้องไม่หาย (เหตุผลเดียวกับ reject_cooldown.json):
#   watch = เจอสัญญาณ 4H แล้ว รอแท่ง 1H ปิดทะลุ (ไม่เกิน REV_A_CONFIRM_HOURS)
#   pend  = ตั้ง limit ที่ระดับยืนยันแล้ว รอเติม (ไม่เกิน REV_A_LIMIT_HOURS) — เฉพาะ REV_A_ENTRY = "limit"
#   🔴 ตั้งแต่ 2026-10-21 REV_A_ENTRY = "market": ยืนยันแล้วส่ง market ทันที (rev_a_place) ไม่มีสถานะ pend ใหม่
#      โค้ด pend ข้างล่างเหลือไว้ดูแล limit ที่ค้างตอน deploy + ถอยกลับเป็น "limit" ได้
# เวลาทั้งหมดเป็นเวลาแท่ง MT5 (UTC) · ลำดับในรอบ = backtest_replay (limit ค้าง -> เฝ้ายืนยัน -> หาสัญญาณใหม่)
# ความต่างจาก replay ที่รู้อยู่ (ตั้งใจ · ไม่มีทางทำให้ตรงกว่านี้ด้วย limit จริง):
#   - replay เช็คด่าน (ช่อง/daily loss/cooldown) ตอนราคาแตะ limit · ระบบจริงโบรกเติมเอง -> ยกเลิก limit ทันทีที่
#     เห็นด่านปิดตอนสแกน และยกเลิกตอนเปิดไม้อื่นในช่อง Reversal
#   - ฐานตรึง ATR (pinned) คิดตอนตั้ง limit · replay คิดตอนเติม (ต่างกัน ≤ REV_A_LIMIT_HOURS แท่ง)
#   - สแกนหลุดหลายชั่วโมง (รีสตาร์ท/MT5 หลุด) แล้วแท่งยืนยันไม่ใช่แท่งล่าสุด -> ทิ้งสัญญาณ ไม่ตั้ง limit ย้อนหลัง
_REV_A_FILE = os.path.join(os.path.dirname(__file__), "rev_a_state.json")


def _rev_a_load() -> dict:
    try:
        with open(_REV_A_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _rev_a_save(data: dict) -> None:
    try:
        with open(_REV_A_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"  ⚠️ เขียน {_REV_A_FILE} ไม่ได้ ({exc}) — สถานะ Reversal แบบ A รอบนี้จะไม่ถูกจำ")


def _rev_a_set(symbol: str, st: dict | None) -> None:
    data = _rev_a_load()
    if st:
        data[symbol] = st
    else:
        data.pop(symbol, None)
    _rev_a_save(data)


def rev_a_cancel_pending(symbol: str, reason: str) -> None:
    """ยกเลิก limit ของแบบ A ที่ค้างใน symbol นี้ (ถ้ามี) + ลบแถว journal + ล้างสถานะ"""
    st = _rev_a_load().get(symbol) or {}
    p = st.get("pend")
    if not p:
        return
    if mt5.positions_get(ticket=p["ticket"]):
        return                                     # เติมไปแล้ว — เป็นไม้จริง ไม่ใช่ของที่ต้องยกเลิก
    if cancel_pending(p["ticket"]):
        journal.remove_unfilled(p["ticket"])
        _rev_a_set(symbol, None)
        print(f"  [{symbol}] Retracement: ยกเลิก limit #{p['ticket']} — {reason}")
        log.info(f"[{symbol}] rev-a cancel #{p['ticket']} {reason}")


def _rev_a_blocked(symbol: str, occupied: set) -> str:
    """ด่านที่ replay เช็คตอนเติม limit (ช่อง · daily loss · cooldown · TP cooldown) — "" = ผ่าน"""
    if slot_of("Reversal") in occupied:
        return "ช่อง Reversal มีไม้แล้ว"
    try:
        if not journal.check_daily_loss(get_account_balance(), MAX_DAILY_LOSS):
            return "daily loss limit"
    except RuntimeError as exc:
        return f"อ่านยอดเงินไม่ได้ ({exc})"
    ok, why = journal.check_cooldown(symbol, COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0))
    if not ok:
        return why
    ok, why = journal.check_tp_cooldown(symbol, TP_COOLDOWN_HOURS)
    return "" if ok else why


def rev_a_step(symbol: str, occupied: set) -> None:
    """หนึ่งรอบของแบบ A ใน symbol นี้ — เรียกจาก scan_symbol หลัง Exit Monitor ก่อนด่านอื่นทั้งหมด
    (replay ทำบล็อกนี้ก่อนเลือกกลยุทธ์ของรอบ และไม่ผ่าน news/portfolio/regime ของกลยุทธ์อื่น)"""
    st = _rev_a_load().get(symbol) or {}
    if not REV_A_ENABLED:
        if st:
            rev_a_cancel_pending(symbol, "ปิดกลยุทธ์ (config.REV_A_ENABLED)")
            _rev_a_set(symbol, None)
        return
    h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=REV_A_CONFIRM_HOURS + 120)
    if h1 is None or len(h1) < REV_A_CONFIRM_HOURS + 60:
        print(f"  [{symbol}] Retracement: ดึงแท่ง 1H ไม่พอ — ข้ามรอบนี้")
        return
    h1 = h1.iloc[:-1].reset_index(drop=True)                 # ตัดแท่งที่ยังไม่ปิด
    last_t = h1["time"].iloc[-1]

    # 1) limit ค้าง
    p = st.get("pend")
    if p:
        if mt5.positions_get(ticket=p["ticket"]):
            print(f"  [{symbol}] Retracement: limit #{p['ticket']} ถูกเติมแล้ว ({p['direction']} @ {p['entry']})")
            notify.notify_order_opened(symbol, p["direction"], p["entry"], p["sl"], p["tp"], p["lot"],
                                       p["ticket"], is_demo=is_demo_account(), strategy=f"{REV_A_STRATEGY} (limit)")
            _rev_a_set(symbol, None)
            st = {}
        elif not mt5.orders_get(ticket=p["ticket"]):
            # ไม่มีทั้ง order และ position: เติมแล้วปิดไปในชั่วโมงเดียว (journal reconcile จัดการเอง) หรือถูกลบจากที่อื่น
            if not mt5.history_deals_get(position=p["ticket"]):
                journal.remove_unfilled(p["ticket"])
            print(f"  [{symbol}] Retracement: limit #{p['ticket']} ไม่ค้างแล้ว — ล้างสถานะ")
            _rev_a_set(symbol, None)
            st = {}
        else:
            exp = _parse_iso(p.get("exp_bar"))
            why = ("ครบ {}ชม. ราคาไม่ย่อมาแตะ".format(REV_A_LIMIT_HOURS)
                   if exp is None or last_t >= exp else _rev_a_blocked(symbol, occupied))
            if why:
                rev_a_cancel_pending(symbol, why)
            return                                           # limit ยังค้าง = ไม่เฝ้า/หาสัญญาณใหม่ (ตรง replay)

    # 2) เฝ้ายืนยัน
    w = st.get("watch")
    if w:
        sig_t, exp, chk = (_parse_iso(w.get("sig_bar")), _parse_iso(w.get("exp_bar")),
                           _parse_iso(w.get("checked_to")))
        idx = h1.index[h1["time"] == sig_t] if sig_t is not None else []
        if exp is None or chk is None or last_t > exp or not len(idx):
            print(f"  [{symbol}] Retracement: ไม่ยืนยันภายใน {REV_A_CONFIRM_HOURS} ชม. — ทิ้งสัญญาณ {w.get('direction')}")
            w = None
        elif last_t == sig_t:
            return                                           # สัญญาณเพิ่งเกิดรอบนี้ — เริ่มเช็คแท่งถัดไป (replay i_sig = n+1)
        else:
            i_sig, i_conf = int(idx[0]) + 1, len(h1) - 1
            # สแกนหลุดไปหลายชั่วโมง (แท่งระหว่าง checked_to กับแท่งล่าสุดไม่เคยถูกเช็ค): ถ้าแท่งไหนในนั้นยืนยันแล้ว
            # replay จะยืนยันที่แท่งนั้น (ระดับ/limit คนละชุด) -> ทิ้งสัญญาณ ไม่ตั้งย้อนหลัง
            skipped = [j for j in range(i_sig, i_conf) if h1["time"].iloc[j] > chk]
            if any(rev_a.confirm(h1, j, i_sig, w["direction"], w.get("leg")) is not None for j in skipped):
                print(f"  [{symbol}] Retracement: ยืนยันไปแล้วระหว่างที่สแกนหลุด — ทิ้งสัญญาณ {w['direction']}")
                w = None
            else:
                px = None
                if REV_A_ENTRY == "market":                  # R:R คิดจากราคาที่จะเข้าจริง (replay: ราคาปิดแท่งยืนยัน)
                    tick = mt5.symbol_info_tick(symbol)
                    px = (tick.ask if w["direction"] == "Long" else tick.bid) if tick else None
                    if not px:
                        print(f"  [{symbol}] Retracement: อ่านราคาตลาดไม่ได้ — ข้ามรอบนี้ (ยังเฝ้าอยู่)")
                        return
                cf = rev_a.confirm(h1, i_conf, i_sig, w["direction"], w.get("leg"), entry_px=px)
                if cf is None:
                    w["checked_to"] = last_t.isoformat()
                    _rev_a_set(symbol, {"watch": w})
                    return
                if cf["ok"]:
                    rev_a_place(symbol, w["direction"], cf, h1, last_t, occupied)
                    return
                print(f"  [{symbol}] Retracement {w['direction']}: ยืนยันแล้วแต่ไม่เข้า — {cf['why']} "
                      f"(ระดับ {cf['lvl']:.5f} · ก้น {cf['ext']:.5f} · ต้นขา {cf['leg']:.5f} · TP {cf['tp']:.5f})")
                w = None
        _rev_a_set(symbol, None)                             # สัญญาณจบ -> หาสัญญาณใหม่ต่อในรอบเดียวกันได้ (ตรง replay)

    # 3) หาสัญญาณใหม่ — ช่อง Reversal ต้องว่าง (replay: เฉพาะตอนไม่มีทั้ง watch/pend)
    if w is None and slot_of("Reversal") not in occupied:
        regime_info = get_regime(symbol)
        bias, _ = get_trend_bias(symbol, get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800))
        direction, why = rev_a.signal_direction(regime_info, bias)
        if not direction:
            return
        leg = rev_a.swing_leg(regime_info, symbol, direction == "Long")
        if leg is None:
            print(f"  [{symbol}] Retracement {direction}: {why} แต่หาต้นขา (swing) ไม่ได้ — ทิ้ง")
            return
        _rev_a_set(symbol, {"watch": {"direction": direction, "sig_bar": last_t.isoformat(),
                                      "exp_bar": (last_t + timedelta(hours=REV_A_CONFIRM_HOURS)).isoformat(),
                                      "checked_to": last_t.isoformat(), "leg": leg}})
        print(f"  [{symbol}] Retracement: สัญญาณ {direction} — {why} · ต้นขา {leg:.5f} · "
              f"รอปิดทะลุ {REV_A_BREAK_BARS} แท่ง 1H ภายใน {REV_A_CONFIRM_HOURS} ชม.")
        log.info(f"[{symbol}] rev-a signal {direction} leg={leg}")


def rev_a_place(symbol: str, direction: str, cf: dict, h1, last_t, occupied: set = frozenset()) -> None:
    """ยืนยันแล้ว + R:R ผ่าน -> market ทันที (REV_A_ENTRY = "market") หรือตั้ง limit
    ผ่านด่านของ replay (ช่อง · daily loss · cooldown) + เพดานความเสี่ยงพอร์ตก่อน"""
    _rev_a_set(symbol, None)                                 # สัญญาณจบที่นี่ไม่ว่าตั้งได้หรือไม่ (replay: watch = None)
    if REV_A_ENTRY == "market":
        _rev_a_market(symbol, direction, cf, h1, occupied)
        return
    try:
        balance = get_account_balance()
        ok, why = check_portfolio_risk(symbol, balance)
        if not ok:
            print(f"  [{symbol}] Retracement: ไม่ตั้ง limit — {why}")
            return
        entry, sl, tp = cf["entry"], cf["sl"], cf["tp"]
        atr = float(calc_atr(h1.iloc[-41:], 14).iloc[-1])   # ATR1H ชุดเดียวกับ replay (41 แท่ง · period 14)
        if TP_MAX_ATR and atr:
            tp = min(tp, entry + atr * TP_MAX_ATR) if direction == "Long" else max(tp, entry - atr * TP_MAX_ATR)
        lot, _ = calculate_lot_size(symbol, entry, sl, balance, REV_A_RISK_PER_TRADE)
        lot = clamp_lot(symbol, lot)
        ticket = place_limit_order(symbol, direction, entry, sl, tp, lot)
        pin = sl + EXEC_SL_ATR_MULT * atr if direction == "Long" else sl - EXEC_SL_ATR_MULT * atr
        journal.log_trade_open(symbol, direction, entry, sl, tp, lot, ticket,
                               pinned_swing=pin, pinned_atr_entry=atr, strategy=REV_A_STRATEGY)
        _rev_a_set(symbol, {"pend": {"ticket": ticket, "direction": direction, "entry": entry, "sl": sl,
                                     "tp": tp, "lot": lot,
                                     "exp_bar": (last_t + timedelta(hours=REV_A_LIMIT_HOURS)).isoformat()}})
        print(f"  [{symbol}] Retracement {direction}: ตั้ง limit #{ticket} @ {entry:.5f}  SL {sl:.5f}  "
              f"TP {tp:.5f}  R:R {cf['rr']:.2f}  lot {lot}  (ค้างไม่เกิน {REV_A_LIMIT_HOURS} ชม.)")
        notify.send(f"🟠 <b>{REV_A_STRATEGY} ตั้ง limit</b>\n{symbol}  {direction}\nLimit: {entry:,.5f}\n"
                    f"SL: {sl:,.5f}   TP: {tp:,.5f}\nR:R: {cf['rr']:.2f}   Lot: {lot}\nOrder: #{ticket}")
        log.info(f"[{symbol}] rev-a limit #{ticket} {direction} @ {entry} sl={sl} tp={tp} lot={lot}")
    except Exception as exc:
        print(f"  [{symbol}] Retracement: ตั้ง limit ไม่สำเร็จ — {exc}")
        log.error(f"[{symbol}] rev-a place ERROR", exc_info=True)


def _rev_a_market(symbol: str, direction: str, cf: dict, h1, occupied: set) -> None:
    """market ตอนยืนยัน (2026-10-21) — ด่านเดียวกับที่ replay เช็คตอนเข้า (_rev_a_blocked) + เพดานพอร์ต
    SL/TP = ของ rev_a.confirm ส่ง broker ตรงๆ (ไม่ขยาย 2×ATR เหมือน Scoring) · ATR ตรึงคิดจาก 41 แท่ง 1H ชุดเดียวกับ replay"""
    try:
        why = _rev_a_blocked(symbol, occupied)
        if why:
            print(f"  [{symbol}] Retracement {direction}: ยืนยันแล้วแต่ไม่เข้า — {why}")
            return
        balance = get_account_balance()
        ok, why = check_portfolio_risk(symbol, balance)
        if not ok:
            print(f"  [{symbol}] Retracement {direction}: ยืนยันแล้วแต่ไม่เข้า — {why}")
            return
        entry, sl, tp = cf["entry"], cf["sl"], cf["tp"]
        atr = float(calc_atr(h1.iloc[-41:], 14).iloc[-1])
        if TP_MAX_ATR and atr:
            tp = min(tp, entry + atr * TP_MAX_ATR) if direction == "Long" else max(tp, entry - atr * TP_MAX_ATR)
        lot, _ = calculate_lot_size(symbol, entry, sl, balance, REV_A_RISK_PER_TRADE)
        lot = clamp_lot(symbol, lot)
        pin = sl + EXEC_SL_ATR_MULT * atr if direction == "Long" else sl - EXEC_SL_ATR_MULT * atr
        print(f"  [{symbol}] Retracement {direction}: ยืนยันแล้ว -> market @ ~{entry:.5f}  SL {sl:.5f}  "
              f"TP {tp:.5f}  R:R {cf['rr']:.2f}  lot {lot}")
        ticket = place_order(symbol, direction, entry, sl, tp, lot, strategy=REV_A_STRATEGY,
                             pinned_swing=pin, pinned_atr_entry=atr)
        log.info(f"[{symbol}] rev-a market #{ticket} {direction} @ {entry} sl={sl} tp={tp} lot={lot}")
        if ticket and isinstance(occupied, set):
            occupied.add(slot_of("Reversal"))          # รอบนี้ห้ามกลยุทธ์อื่นเข้าช่อง Reversal ซ้ำ (replay: _occupied.add)
    except Exception as exc:
        print(f"  [{symbol}] Retracement: ส่ง market ไม่สำเร็จ — {exc}")
        log.error(f"[{symbol}] rev-a market ERROR", exc_info=True)
print = tee_print(log)   # เขียนทุกอย่างที่ print ลง logs/scheduler.log ด้วย (ดู logger_setup.py)

# Regime ที่ "ไม่เปิด" scorecard ใดๆ — รอความชัดเจนก่อน (ตาม Mutual Exclusivity ที่ตั้งไว้)
#
# 🔻 "TREND แรงจัด" (ADX ≥ 40 ยังพุ่ง) อยู่ในด่านห้ามเข้าตั้งแต่ 2026-08-31 (หลักฐานบาง 4 ไม้) · วัดซ้ำ 2026-10-01 ปลดด่าน −2.95R = คงไว้
#   ถอยกลับ = ย้าย "TREND แรงจัด" ออกจาก config.REGIME_NO_TRADE ที่เดียว · replay --adx-strong=999
# 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "scheduler.py — TREND แรงจัด ใน REGIME_NO_TRADE (2026-08-31..10-01)"
from config import REGIME_NO_TRADE, REGIME_TREND, REGIME_REVERSAL   # noqa: E402  (ดู config)

INTERVAL_SECONDS = 3600   # เช็คทุก 1 ชั่วโมง
# สแกนหลังต้นชั่วโมงกี่วินาที — รอให้แท่ง 1H ของชั่วโมงใหม่มี tick แรกก่อน (แท่งท้ายสุดที่ MT5 คืนต้อง
# เป็นแท่งฟอร์มมิ่งของชั่วโมงใหม่ ไม่ใช่แท่งที่เพิ่งปิด) = ใกล้กับที่ replay สแกนตรงขอบแท่งที่สุด
SCAN_DELAY_SECONDS = 60


def seconds_until_next_scan(now_ts: float) -> float:
    """วินาทีจนถึงรอบถัดไป = ต้นชั่วโมงถัดไป + SCAN_DELAY_SECONDS

    🔴 2026-09-28: เดิม sleep(INTERVAL_SECONDS) *หลัง* สแกนเสร็จ รอบจึงเลื่อนช้าลงเท่ากับเวลาที่ใช้
    สแกนทุกรอบ (log จริงห่างกัน 61-73 นาที) แล้วข้ามไปทั้งชั่วโมงเป็นระยะ (09-27 23:51 -> 09-28
    01:00) = ชั่วโมงนั้นไม่มีทั้งการหาไม้และ exit monitor ขณะที่ replay สแกนครบทุกแท่ง 1H
    ขอบชั่วโมงคิดจาก Unix time = ขอบแท่ง 1H ของ MT5 (UTC) = ต้นชั่วโมงเวลาเครื่อง (UTC+7) ด้วย"""
    return INTERVAL_SECONDS - (now_ts % INTERVAL_SECONDS) + SCAN_DELAY_SECONDS


def sleep_until(target_ts: float) -> None:
    """หลับจนถึงเวลา target ตามนาฬิกาจริง ทีละไม่เกิน 60 วิ — ถ้าเครื่อง sleep ไปกลางทาง
    time.sleep ก้อนเดียวยาวๆ จะนับที่เหลือต่อหลังเครื่องตื่น (เลยเวลาไปอีกเป็นชั่วโมง) การเช็ค
    นาฬิกาทุกนาทีทำให้รอบถัดไปเริ่มภายใน ~1 นาทีหลังเครื่องตื่น"""
    while (left := target_ts - time.time()) > 0:
        time.sleep(min(left, 60))


# สรุปรายวันส่งไปแล้วถึงวันไหน — เก็บลงไฟล์ (ไม่ใช่ตัวแปร) ด้วยเหตุผลเดียวกับ reject_cooldown.json:
# รีสตาร์ทแล้วต้องไม่ส่งซ้ำ และต้องไม่ลืมวันที่ยังไม่ได้ส่ง
_SUMMARY_STATE_FILE = os.path.join(os.path.dirname(__file__), "daily_summary_state.json")


def send_daily_summary_if_due(today) -> None:
    """ส่งสรุปของ "เมื่อวาน" ครั้งเดียว ในรอบแรกที่ต่อ MT5 + reconcile สำเร็จของวันใหม่

    🔴 2026-09-28: เดิมส่งเฉพาะรอบที่ตรงชั่วโมง 00 — สรุป 09-26 หายเพราะรอบ 00:14 ของ 09-27 ต่อ MT5
    ไม่ได้ (Authorization failed ทั้งรอบ) ส่วนสรุป 09-27 หายเพราะรอบเลื่อนข้ามชั่วโมง 00 ไปเลย
    ต้องเรียกหลัง reconcile_closed_positions() เสมอ ไม่งั้นไม้ที่ broker ปิดเมื่อวานยังค้าง 'Open'"""
    day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        with open(_SUMMARY_STATE_FILE, encoding="utf-8") as f:
            last = json.load(f).get("last_day")
    except Exception:
        last = None                        # ไฟล์ยังไม่มี/พัง = ถือว่ายังไม่เคยส่ง
    if last is not None and last >= day:   # วันที่แบบ ISO เทียบเป็นสตริงได้ตรงๆ
        return
    stats = journal.get_daily_statistics(day)
    notify.notify_daily_summary(stats, day)
    print(f"[journal] ส่งสรุปรายวัน {day} แล้ว — {stats}")
    try:
        with open(_SUMMARY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_day": day}, f)
    except Exception as exc:
        print(f"  ⚠️ เขียน {_SUMMARY_STATE_FILE} ไม่ได้ ({exc}) — สรุปวันนี้อาจถูกส่งซ้ำรอบหน้า")


# ---------------------------------------------------------------------------
# สแกนและส่ง order ถ้าผ่าน
# ---------------------------------------------------------------------------

def check_portfolio_risk(symbol: str, balance: float) -> tuple[bool, str]:
    """เพดานความเสี่ยงที่เปิดค้างอยู่ *ทั้งพอร์ต* — ด่านเดียวที่มองข้าม symbol

    ด่านอื่นทุกตัวมองทีละ symbol: scan_symbol เรียก positions_get(symbol=...),
    SLOT_PER_STRATEGY คุมแค่ช่องในตัวเอง, MAX_DAILY_LOSS นับเฉพาะไม้ที่ปิดไปแล้ววันนี้
    ไม่มีใครเห็นภาพรวมว่าตอนนี้เปิดความเสี่ยงค้างอยู่เท่าไหร่ — ดู comment ที่
    config.MAX_PORTFOLIO_RISK_R สำหรับตัวเลขที่วัดมา

    คิดจาก "ความเสี่ยงที่ยังมีชีวิต" (SL ปัจจุบันเทียบ entry) ไม่ใช่จำนวนไม้ ไม้ที่ขยับ
    SL ไป breakeven แล้วจึงคืนโควตาให้ไม้ใหม่เองอัตโนมัติ

    ไม้ใหม่ถูกคิดเป็น 1R เต็มเสมอ (= RISK_PER_TRADE) ตรงกับตอนคิด lot ที่ยังไม่ถูก
    clamp_lot() ปัด — ด่านนี้อยู่ก่อนการวิเคราะห์ทั้งหมดจึงยังไม่รู้ lot จริง ซึ่งตรงกับ
    backtest_portfolio.py ที่วัดค่า 3.0R มา
    """
    risk_unit = balance * RISK_PER_TRADE
    if risk_unit <= 0:
        return False, f"risk unit ไม่ถูกต้อง (balance={balance:.2f})"

    positions = mt5.positions_get()
    if not positions:
        return True, ""

    total_r, group_r = 0.0, 0.0
    my_group = CORRELATION_GROUPS.get(symbol)
    for pos in positions:
        direction = "Long" if pos.type == mt5.POSITION_TYPE_BUY else "Short"
        if not pos.sl:
            # SL หลุด/ยังไม่ได้ตั้ง = ความเสี่ยงไม่มีขอบเขต ไม่ใช่ศูนย์ — คิดเป็น 1R ไว้ก่อน
            # แล้วเตือน (ของจริงแย่กว่านี้ แต่ประเมินให้เกินไปกว่านี้ก็เดาเอาทั้งนั้น)
            print(f"  [{symbol}] WARNING — #{pos.ticket} ({pos.symbol}) ไม่มี SL "
                  f"— นับเป็น 1R ในเพดานความเสี่ยง")
            r = 1.0
        else:
            try:
                r = position_risk_amount(pos.symbol, direction, pos.price_open,
                                         pos.sl, pos.volume) / risk_unit
            except RuntimeError as exc:
                print(f"  [{symbol}] WARNING — คิดความเสี่ยงของ #{pos.ticket} ไม่ได้ "
                      f"({exc}) — นับเป็น 1R")
                r = 1.0
        total_r += r
        if my_group and CORRELATION_GROUPS.get(pos.symbol) == my_group:
            group_r += r

    # limit ของ Reversal แบบ A ที่ค้าง = ความเสี่ยงที่โบรกเปิดให้ได้ทุกเมื่อโดยไม่ผ่านด่านนี้อีก -> นับเหมือนเปิดแล้ว
    for od in mt5.orders_get() or []:
        if od.magic != MAGIC_NUMBER or not od.sl:
            continue
        _d = "Long" if od.type == mt5.ORDER_TYPE_BUY_LIMIT else "Short"
        try:
            r = position_risk_amount(od.symbol, _d, od.price_open, od.sl, od.volume_current) / risk_unit
        except RuntimeError:
            r = 1.0
        total_r += r
        if my_group and CORRELATION_GROUPS.get(od.symbol) == my_group:
            group_r += r

    if total_r + 1.0 > MAX_PORTFOLIO_RISK_R + 1e-9:
        return False, (f"เพดานความเสี่ยงรวมทั้งพอร์ต — เปิดค้างอยู่ {total_r:.2f}R "
                       f"+ ไม้ใหม่ 1R > {MAX_PORTFOLIO_RISK_R}R "
                       f"({MAX_PORTFOLIO_RISK_R * RISK_PER_TRADE * 100:.0f}% ของพอร์ต)")

    if my_group and group_r + 1.0 > MAX_GROUP_RISK_R + 1e-9:
        return False, (f"เพดานความเสี่ยงกลุ่ม {my_group} — เปิดค้างอยู่ {group_r:.2f}R "
                       f"+ ไม้ใหม่ 1R > {MAX_GROUP_RISK_R}R")

    return True, ""


def _tp_to_entry_on_opposite(symbol: str, direction: str, new_ticket) -> None:
    """ไม้ใหม่เพิ่งเปิดสวนทิศไม้เก่าใน symbol เดียวกัน -> ย้าย TP ไม้เก่าที่ขาดทุนอยู่มาไว้ที่ entry ของมัน
    ที่มา/ตัวเลขที่ config.TP_TO_ENTRY_ON_OPPOSITE · ตัวเดียวกับ backtest_replay --tp-entry-on-opposite"""
    for pos in mt5.positions_get(symbol=symbol) or []:
        if pos.ticket == new_ticket:
            continue
        if journal.get_trade_strategy(pos.ticket) == "Sideway":
            continue                               # ไม่ใช้กฎนี้กับไม้ Sideway (คำสั่งผู้ใช้ 2026-10-02)
        old_long = pos.type == mt5.POSITION_TYPE_BUY
        if (direction == "Long") == old_long:
            continue                               # ทิศเดียวกัน — ไม่ใช่คู่สวน
        in_loss = pos.price_current < pos.price_open if old_long else pos.price_current > pos.price_open
        if not in_loss:
            continue                               # กำไรอยู่ — TP ที่ entry จะอยู่ผิดฝั่งราคา
        if pos.tp and abs(pos.tp - pos.price_open) < 1e-9 * abs(pos.price_open):
            continue                               # ย้ายไว้แล้ว
        try:
            modify_sltp(pos.ticket, new_tp=pos.price_open)
            print(f"  [{symbol}] ย้าย TP ไม้เก่า #{pos.ticket} ({'Long' if old_long else 'Short'}) "
                  f"-> entry {pos.price_open} เพราะไม้ใหม่ #{new_ticket} เปิดสวนทิศ")
            log.info(f"[{symbol}] TP #{pos.ticket} -> entry {pos.price_open} (opposite #{new_ticket})")
        except Exception as exc:
            print(f"  [{symbol}] ย้าย TP ไม้เก่า #{pos.ticket} ไป entry ไม่สำเร็จ — {exc}")
            log.error(f"[{symbol}] TP-to-entry #{pos.ticket} ERROR", exc_info=True)


def scan_symbol(symbol: str) -> None:
    print(f"\n  [{symbol}] กำลังวิเคราะห์...")

    # 1. มี position เปิดอยู่แล้ว? -> รัน Exit Monitor ดูแลไม้เดิมก่อนเสมอ
    #    SLOT_PER_STRATEGY=True: ช่องแยกตามกลยุทธ์ ไม้ Scoring ที่เปิดอยู่จึงไม่บล็อกไม้ Reversal
    #    (และกลับกัน) ด่านจริงอยู่ที่ข้อ 4b หลัง regime บอกแล้วว่ารอบนี้จะเปิดกลยุทธ์ไหน
    #    occupied ถูกอ่าน *ก่อน* รัน Exit Monitor โดยตั้งใจ — ตรงกับ backtest_replay ที่ snapshot
    #    ช่องก่อนเดินไม้ ผลคือไม้ที่เพิ่งถูกปิดในรอบนี้จะยังไม่เปิดไม้ใหม่ทับทันทีในชั่วโมงเดียวกัน
    positions = mt5.positions_get(symbol=symbol)
    occupied = set()
    if positions:
        strategies = [journal.get_trade_strategy(pos.ticket) for pos in positions]
        # เก็บเป็น "ช่อง" ไม่ใช่ชื่อกลยุทธ์ — ไม้ Breakout ครองช่อง Reversal (config.slot_of ตัวเดียว
        # กับ backtest_replay) 🔴 2026-09-28: เดิมเก็บชื่อดิบ ไม้ Breakout จึงไม่บล็อกช่อง Reversal
        # แล้วระบบเปิด Breakout ซ้ำได้ทุกชั่วโมงที่สัญญาณยังค้าง (ดู docstring ที่ config.slot_of)
        occupied = {slot_of(s) for s in strategies}
        print(f"  [{symbol}] มี position เปิดอยู่ {len(positions)} ไม้ "
              f"({', '.join(sorted(set(strategies)))}) -> รัน Exit Monitor")
        for pos in positions:
            try:
                m = analyze_position(pos)
                print_report(m)
                execute_decision(m)
            except Exception as exc:
                print(f"  [{symbol}] Exit Monitor ERROR — {exc}")
                log.error(f"[{symbol}] Exit Monitor ERROR", exc_info=True)

    # 1a. Reversal แบบ A — limit ค้าง / เฝ้ายืนยัน / หาสัญญาณ (ไม่ผ่านด่านของกลยุทธ์อื่น ตรงกับ replay)
    #     ใช้ occupied ชุดเดียวกับข้อ 1 (snapshot ก่อน Exit Monitor) · พังแล้วไม่ล้มทั้ง scan_symbol
    try:
        rev_a_step(symbol, occupied)
    except Exception as exc:
        print(f"  [{symbol}] Retracement ERROR — {exc}")
        log.error(f"[{symbol}] Retracement ERROR", exc_info=True)

    if positions:
        if not SLOT_PER_STRATEGY:
            return
        if occupied >= {"Scoring", "Reversal"}:
            print(f"  [{symbol}] ช่องเต็มทั้งสองกลยุทธ์ -> ไม่หา entry ใหม่")
            return

    # 1b. โบรกปิดเทรด symbol นี้ไว้ไหม (trade_mode != FULL) — เช็คก่อนวิเคราะห์อะไรเลย
    # 2026-08-15: เจอ XRPUSDm ถูก Exness ปิดเทรดบนเซิร์ฟเวอร์ trial (trade_mode=DISABLED)
    # ทั้งที่ยังส่ง quote ปกติ — ระบบเคยวิเคราะห์เต็มรูปแบบทุกชั่วโมง (ดึง 1D/4H/1H + merge
    # real volume) แล้วพังตอนส่ง order เป็น retcode=10017 [Trade disabled] ทุกรอบ กันไว้ตรงนี้
    # ก่อนเสียเวลาวิเคราะห์ — ไม่เอา symbol ออกจาก SYMBOLS เพราะถ้าโบรกเปิดกลับมาจะได้เทรดต่อ
    # อัตโนมัติทันทีโดยไม่ต้องแก้ config
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"  [{symbol}] ERROR — หา symbol info ไม่ได้")
        return
    if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        print(f"  [{symbol}] SKIP — โบรกปิดเทรด symbol นี้ไว้ (trade_mode={info.trade_mode}) "
              f"— เช็คสถานะที่ MT5 Market Watch หรือถามโบรกเรื่อง instrument นี้")
        return

    # 2. ดึงยอดเงินจริงจากบัญชี
    try:
        balance = get_account_balance()
    except RuntimeError as exc:
        print(f"  [{symbol}] ERROR — {exc}")
        return

    # 3. Daily loss guard (เช็คเฉพาะตอนจะหา entry ใหม่)
    if not journal.check_daily_loss(balance, MAX_DAILY_LOSS):
        print(f"  [{symbol}] SKIP — daily loss limit reached")
        return

    # 3b. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่นาน (ดู config.COOLDOWN_HOURS_BY_SYMBOL)
    #     2026-08-13: **บั๊กที่เพิ่งเจอ** — เดิม check_cooldown() ถูกเรียกจาก bot.py เท่านั้น
    #     แต่ไม่มีไฟล์ไหน import bot.py เลย (dead code) ส่วน scheduler.py ที่รันจริงไม่เคยเรียก
    #     => cooldown ไม่เคยทำงานจริงเลยตั้งแต่ตั้งค่ามา COOLDOWN_HOURS_BY_SYMBOL เป็นแค่ตัวเลข
    #     ประดับ เคสที่ทำให้เจอ: XAUUSDm ปิดไม้ 00:20 (Manual Cut, -2.00) แล้วเปิดไม้ใหม่ 01:30
    #     วันเดียวกัน = ห่างแค่ 70 นาที ทั้งที่ตั้ง cooldown ไว้ 48 ชม.
    #     วางไว้ก่อน News guard เพราะอ่าน CSV อย่างเดียว (ถูกกว่า) ส่วน News ต้องยิงเน็ตไป
    #     ForexFactory — ตัดจบตรงนี้ได้ก็ไม่ต้องเสียเวลาไปเรียก
    #     ต้องอยู่หลัง reconcile_closed_positions() (เรียกใน run_scheduler ต้นรอบ) เสมอ ไม่งั้น
    #     ไม้ที่ชน SL/TP เองจะยังค้างเป็น 'Open' และมองไม่เห็นเวลาปิดจริง
    cooldown_hours = COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)
    ok, cooldown_reason = journal.check_cooldown(symbol, cooldown_hours)
    if not ok:
        print(f"  [{symbol}] SKIP — {cooldown_reason}")
        return

    # 3c. Cooldown หลัง TP (config.TP_COOLDOWN_HOURS) — ทั้ง symbol ทุกกลยุทธ์ · ต้องอยู่หลัง
    #     reconcile เหมือน 3b (ไม้ที่ broker ปิดด้วย TP ถูก mark เป็น Take Profit ที่นั่น)
    #     ตำแหน่งต้องตรงกับ backtest_replay (หลัง 3b ก่อน regime/min-turn) ไม่งั้นวัดคนละระบบ
    ok, cooldown_reason = journal.check_tp_cooldown(symbol, TP_COOLDOWN_HOURS)
    if not ok:
        print(f"  [{symbol}] SKIP — {cooldown_reason}")
        return

    # 4. Balance ต้องมากกว่า 0 (risk amount = balance * RISK_PER_TRADE เป็นสัดส่วนของ balance เอง
    # เสมออยู่แล้ว จุดที่พังจริงคือ balance <= 0 ไม่ใช่สัดส่วน)
    if balance <= 0:
        print(f"  [{symbol}] SKIP — balance ไม่พอ ({balance:.2f})")
        return

    # 4a. เพดานความเสี่ยงระดับพอร์ต — ต้องอยู่หลังเช็ค balance (หารด้วย risk unit) แต่ก่อน
    #     News guard ที่ต้องยิงเน็ต และก่อน regime/สกอร์การ์ดที่ต้องดึงบาร์ 1D/4H/1H
    #     อ่าน positions_get() ของทั้งพอร์ตอย่างเดียว ถูกกว่าทุกด่านที่ตามมา
    ok, risk_reason = check_portfolio_risk(symbol, balance)
    if not ok:
        print(f"  [{symbol}] SKIP — {risk_reason}")
        return

    # 4b. News guard — ไม่เปิดไม้ใหม่ถ้าข่าว High Impact (USD) จะออกภายใน NEWS_IMMINENT_H ชม.
    #     2026-08-02: เดิมเช็คข่าวแค่ตอนมีไม้เปิดอยู่แล้ว (exit_monitor) ไม่มีจุดไหนกันตอน
    #     "จะเปิดไม้ใหม่" เลย — เปิดก่อนข่าวใหญ่ 5 นาทีก็เปิดได้ปกติ กว่า Exit Monitor จะเห็น
    #     ก็รอบสแกนถัดไปซึ่งอาจสายไปแล้ว (ข่าวออกไปแล้ว)
    has_news, news_detail, _ = check_upcoming_news(hours_ahead=NEWS_IMMINENT_H)
    if has_news:
        print(f"  [{symbol}] SKIP — ใกล้ข่าว {NEWS_IMPACT} ({NEWS_CURRENCY}) ภายใน {NEWS_IMMINENT_H} ชม. — {news_detail}")
        return

    # 4. Regime Check → เลือกว่าจะเปิด Scoring (ตามเทรนด์) หรือ Reversal (สวนเทรนด์) หรือพักก่อน
    #    Mutual Exclusivity: เปิดได้แค่ scorecard เดียวต่อรอบสแกน ตาม regime ที่เจอ
    try:
        regime_info = get_regime(symbol)
        regime      = regime_info["regime"]
        print(f"  [{symbol}] Regime={regime}  ({regime_info['action']})")

        # 4a. Breakout ไม่ต้องผ่านด่าน peak ADX (ดู config.BREAKOUT_IGNORES_MIN_PEAK) — รอบที่จะเป็น
        #     REVERSAL-READY ถ้าไม่มีเกณฑ์ peak + divergence bearish + เทรนด์ 1D Long = ทาง flip เป็น Breakout
        #     -> ถือเป็น REVERSAL-READY แล้วไหลเข้าทาง flip ด้านล่าง (ตรงกับ backtest_replay.regime_eff)
        if (BREAKOUT_IGNORES_MIN_PEAK and BREAKOUT_ENABLED and REVERSAL_SHORT_NEEDS_1D_TREND
                and regime != "REVERSAL-READY"
                and (regime_info.get("reversal_nopeak") or ("",))[0] == "REVERSAL-READY"
                and regime_info["divergence"].get("divergence") == "bearish"):
            _bias, _ = get_trend_bias(symbol, get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800))
            if _bias == "Long":
                print(f"  [{symbol}] Breakout ข้ามด่าน peak ADX ({regime_info['peak']['peak']:.1f} < "
                      f"{regime_check.ADX_MIN_PEAK_REVERSAL:g}) — ถือเป็น REVERSAL-READY เพื่อเข้าทาง Breakout")
                regime = "REVERSAL-READY"

        # 4a-2. Pullback (config.PULLBACK_ENABLED) — ทุก regime · สัญญาณครบ + ช่อง Reversal ว่าง = มาก่อนทุกกลยุทธ์
        #       ไม่ครบ = เดินทางเดิมทุกอย่าง (Sideway/Scoring ไม่เสียรอบ) · ตรงกับ backtest_replay.revnew_candidate
        #       ที่ประเมินเฉพาะตอนช่องว่าง · กฎเข้าบ้านเดียวที่ pullback.pullback_direction
        pb_dir = None
        if PULLBACK_ENABLED and slot_of("Pullback") not in occupied:
            # ด่านตำแหน่งปิด (0) = ไม่มีไม้เงา — ข้ามของที่ค้างใน pullback_ghost.json ด้วย ให้ตรงกับ replay (2026-10-19)
            _ghost = pullback_ghost_active(symbol) if PULLBACK_MIN_RANGE_POS else None
            if _ghost:
                print(f"  [{symbol}] Pullback: ไม้เงา {_ghost['direction']} ยังไม่จบ "
                      f"(SL {_ghost['sl']:.5f} / TP {_ghost['tp']:.5f}) — ยังไม่เข้า Pullback")
            else:
                _bias_rn, _ = get_trend_bias(symbol, get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800))
                pb_dir, _pb_why = pullback.pullback_direction(
                    regime_info, _bias_rn, adx_rule=PULLBACK_ADX_RULE,
                    require_key_level=regime_check.KEY_LEVEL_REQUIRED,
                    min_disagree_bars=PULLBACK_MIN_DISAGREE_BARS)
                if pb_dir:
                    print(f"  [{symbol}] Pullback {pb_dir} — {_pb_why}")
                elif "เพิ่งสวน" in _pb_why:     # ด่านอายุการสวน (2026-10-20) — พิมพ์ไว้ให้ตามดูใน scheduler.log
                    print(f"  [{symbol}] Pullback รอ: {_pb_why}")

        # Sideway (config.SIDEWAY_*): เข้าได้เฉพาะรอบที่ regime ห้ามเทรดและ ADX 4H < ADX_CHOPPY
        #   = Reversal (REVERSAL-READY ไม่อยู่ใน NO_TRADE · Pullback ข้างบน) มาก่อนเสมอตามคำสั่งผู้ใช้ 2026-10-02
        sideway_mode = (not pb_dir and regime in REGIME_NO_TRADE and SIDEWAY_ENABLED
                        and (config.SIDEWAY_DETECT == "r2"
                             or regime_info.get("adx_now", 99) < regime_check.ADX_CHOPPY))
        if regime in REGIME_NO_TRADE and not sideway_mode and not pb_dir:
            print(f"  [{symbol}] SKIP — regime ยังไม่พร้อมเปิด scorecard ใดๆ")
            return

        # 4b. ช่องของกลยุทธ์ที่ regime รอบนี้จะเปิด ว่างไหม — regime เป็นตัวเลือกกลยุทธ์ตัวเดียว
        #     (Mutual Exclusivity ตามข้อ 4) จึงรู้ได้ตั้งแต่ตรงนี้โดยไม่ต้องคำนวณสกอร์การ์ดก่อน
        #     occupied อ่านไว้ตั้งแต่ข้อ 1 ก่อนรัน Exit Monitor — ตรงลำดับกับ backtest_replay
        #     (ถ้า SLOT_PER_STRATEGY=False ข้อ 1 return ไปตั้งแต่มีไม้ใดๆ แล้ว มาไม่ถึงตรงนี้)
        # REVERSAL-READY: Reversal เดิมปิด = ทางนี้เปิดได้แค่ Breakout -> เช็คช่องของ Breakout (config.BREAKOUT_SLOT)
        # Reversal เดิมเปิด = เช็คช่อง Reversal ก่อน แล้วเช็คช่อง Breakout ซ้ำหลังรู้ว่า flip (ดูข้อ flip ด้านล่าง)
        want = ("Reversal" if pb_dir else slot_of("Sideway") if sideway_mode
                else "Scoring" if regime in REGIME_TREND
                else slot_of("Reversal") if REVERSAL_ENABLED else slot_of("Breakout"))
        if want in occupied:
            print(f"  [{symbol}] SKIP — ช่อง {want} มีไม้เปิดอยู่แล้ว")
            return

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"  [{symbol}] ERROR — ดึงราคาไม่ได้")
            return
        entry = tick.bid

        if pb_dir:
            # ── เปิด Pullback — ทิศ = Bias 1D · SL/TP ทาง compute_reversal_entry แต่ TP Fib ×3 / สำรอง 1.5R
            #    (config.PULLBACK_*) · R:R ขั้นต่ำ PULLBACK_MIN_RR · ด่านตำแหน่งอยู่ข้อ 4e
            direction, strategy = pb_dir, "Pullback"
            sl_info = reversal.compute_reversal_entry(
                symbol, direction, entry, df_4h=regime_info["df_4h"],
                tp_ratio=PULLBACK_TP_FIB_RATIO, fallback_rr=PULLBACK_FALLBACK_RR,
                min_rr=PULLBACK_MIN_RR)
            print(f"  [{symbol}] เปิด Pullback {direction}  Entry={entry:.5f}")
        elif sideway_mode:
            # ── เปิด Sideway — ทิศมาจากตำแหน่งราคาในกรอบ (กฎทั้งหมดที่ sideway.compute_sideway_entry) ──
            sl_info = sideway.compute_sideway_entry(symbol, entry)   # ไม่ผ่าน = ValueError -> BLOCKED
            direction, strategy = sl_info["direction"], "Sideway"
            print(f"  [{symbol}] เปิด Sideway {direction} — กรอบ {sl_info['range_low']:.5f}-"
                  f"{sl_info['range_high']:.5f} · sideway {sl_info['sideway_bars']} แท่ง  Entry={entry:.5f}")
        elif regime in REGIME_TREND:
            # ── เปิด Scoring (trend-following) ── ใช้ get_trend_bias ตัวเดียวกับที่
            # compute_entry เรียกภายใน (trend_flip เท่านั้น ไม่มี EMA fallback) กัน bias
            # สองจุดขัดกันเอง (เดิม scheduler ใช้ EMA แยกจาก compute_score ที่ใช้ trend_flip)
            # ไม่ merge real volume แล้ว (2026-09-25) — ผู้ใช้ volume 1D ตัวเดียวคือ OBV 1D
            # ในสกอร์การ์ดที่ถูกลบ ส่วน trend_flip ใช้แค่ราคา
            df_1d = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
            direction, bias_source = get_trend_bias(symbol, df_1d)
            if direction is None:
                print(f"  [{symbol}] SKIP — หา Bias ไม่ได้ ({bias_source})")
                return
            # ทิศจาก trend_flip (1D) ต้องตรงกับโครงสร้าง 4H — ดู config ที่ค่านั้น
            # structure["trend"] = "Long (reg)" / "Short (reg)" (USE_STRUCT_REG) ตอน regime = TREND เสมอ
            _struct = regime_info["structure"]["trend"]
            if SCORING_NEEDS_STRUCTURE_MATCH and not _struct.startswith(direction):
                print(f"  [{symbol}] SKIP — Bias 1D={direction} สวน Structure 4H={_struct}")
                return
            print(f"  [{symbol}] เปิด Scoring — Bias 1D={direction} ({bias_source}) · Structure 4H={_struct}  Entry={entry:.5f}")
            # ส่ง df_1d ที่ดึงไปแล้วข้างบน (สำหรับ get_trend_bias) ให้ compute_entry ใช้ซ้ำ
            sl_info = compute_entry(symbol, direction, entry, df_1d=df_1d)
            strategy = "Scoring"

        else:  # REGIME_REVERSAL — "REVERSAL-READY"
            # ── เปิด Reversal (ทิศตามขั้ว divergence ที่ทำให้ REVERSAL-READY ยิง ไม่ใช่แค่กลับ bias) ──
            div_polarity = regime_info["divergence"]["divergence"]
            direction = "Long" if div_polarity == "bullish" else "Short"
            breakout_flip = False   # True เมื่อไม้นี้ถูกกลับข้างเป็น Breakout (ข้าม CHoCH ด้านล่าง)
            # ── ด่านฝั่ง Short: ต้องมีเทรนด์ 1D หนุนด้วย (ดูที่มา/ตัวเลขที่ config.py) ──
            # ใช้ get_trend_bias ตัวเดียวกับที่ฝั่ง Scoring ใช้ ไม่เพิ่มนิยามเทรนด์ตัวที่สอง
            # เข้าระบบ และดึง 1D แบบเดียวกันเป๊ะ (bars=800)
            if direction == "Short" and REVERSAL_SHORT_NEEDS_1D_TREND:
                df_1d_rev = get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800)
                bias_1d, bias_src = get_trend_bias(symbol, df_1d_rev)
                if bias_1d != "Short":
                    # ── Breakout: กลับข้างเป็น Long แทนการทิ้ง (ดู config.BREAKOUT_ENABLED) ──
                    if BREAKOUT_ENABLED and bias_1d == "Long":
                        direction = "Long"
                        _struct = regime_info["structure"]["trend"]
                        if SCORING_NEEDS_STRUCTURE_MATCH and not _struct.startswith(direction):
                            print(f"  [{symbol}] SKIP — Breakout flip -> Long แต่สวนโครงสร้าง "
                                  f"4H ({_struct})")
                            return
                        if slot_of("Breakout") in occupied:   # ช่อง Breakout ≠ ช่องที่เช็คไว้ตอน Reversal เดิมเปิด
                            print(f"  [{symbol}] SKIP — Breakout flip แต่ช่อง {slot_of('Breakout')} มีไม้เปิดอยู่แล้ว")
                            return
                        print(f"  [{symbol}] Breakout — Reversal Short ถูกกัก (เทรนด์ 1D = Long) "
                              f"-> กลับข้างเข้า Long แทน  Entry={entry:.5f}")
                        _saved_fib = swing.TP_FIB_RATIO
                        swing.TP_FIB_RATIO = BREAKOUT_TP_FIB_RATIO
                        try:
                            sl_info = compute_entry(symbol, direction, entry, df_1d=df_1d_rev)
                        finally:
                            swing.TP_FIB_RATIO = _saved_fib
                        strategy = "Breakout"
                        breakout_flip = True
                    else:
                        print(f"  [{symbol}] SKIP — Reversal Short แต่เทรนด์ 1D = {bias_1d} "
                              f"({bias_src}) ต้องเป็น Short ถึงจะเข้าได้")
                        return
                else:
                    print(f"  [{symbol}] Reversal Short — เทรนด์ 1D = Short ({bias_src}) ผ่านด่าน")
            # Reversal เดิมปิดตั้งแต่ 2026-10-13 (config.REVERSAL_ENABLED) — ทาง REVERSAL-READY เหลือแค่ flip เป็น Breakout
            # เปิดกลับ / ใช้คู่กับ Pullback ได้ด้วย config.REVERSAL_ENABLED (คู่กันยังไม่เคยวัด)
            if not REVERSAL_ENABLED and not breakout_flip:
                print(f"  [{symbol}] SKIP — Reversal เดิมปิดแล้ว (config.REVERSAL_ENABLED) · "
                      f"READY {div_polarity} ไม่ใช่ทาง Breakout")
                return
            # CHoCH — โครงสร้างเดิม (ฝั่งตรงข้ามกับที่จะเข้า) ต้องพังแล้ว ดู config ที่ค่านั้น
            # ไม้ Breakout ข้ามด่านนี้: CHoCH ถามว่า "โครงสร้างเดิมพังหรือยัง" ซึ่งเป็นคำถามของ
            # การกลับตัว ส่วน Breakout เดิมพันว่าโครงสร้างเดิม **ไม่พัง** แล้วไปต่อ (ตรงกับ
            # backtest_replay ที่ข้ามด่านนี้เหมือนกัน — ต้องตรงกันสองฝั่งเสมอ)
            if REVERSAL_NEEDS_CHOCH and not breakout_flip:
                _opp = "Short" if direction == "Long" else "Long"
                if not check_structure_break(symbol, _opp):
                    print(f"  [{symbol}] SKIP — ยังไม่เห็น CHoCH (โครงสร้าง {_opp} ยังไม่พัง) "
                          f"ยังไม่เข้า Reversal {direction}")
                    return
            # ไม้ Breakout คิด sl/tp ไปแล้วตอนกลับข้าง (ทาง compute_entry) — ข้ามบล็อกนี้
            if not breakout_flip:
                print(f"  [{symbol}] เปิด Reversal — Divergence={div_polarity} -> เข้าเป็น {direction}  Entry={entry:.5f}")
                # 2026-09-05: รับเข้า `sl_info` ตัวเดียวกับทาง Scoring — เดิมรับเป็น `info` แล้วโค้ด
                # ด้านล่าง (exec_sl / pinned_swing) อ่านจาก `sl_info` แบบไม่แยก branch ทำให้ไม้
                # Reversal โยน NameError: name 'sl_info' is not defined ทุกครั้งแล้วโดน except
                # ด้านล่างกลืนไปเป็น "ERROR — ..." = **ระบบจริงเปิดไม้ Reversal ไม่ได้เลยตั้งแต่
                # 2026-08-31** (รอบที่ย้าย exec_sl เข้า compute_score แล้วไม่ได้แก้ทาง Reversal ตาม)
                # backtest ไม่เจอเพราะ backtest_replay.py มีโค้ดคำนวณ exec_sl ของตัวเองแยกต่างหาก
                sl_info = reversal.compute_reversal_entry(
                    symbol, direction, entry, df_4h=regime_info["df_4h"])
                strategy = "Reversal"

        # แสดงผลสรุป — R:R วัดจาก SL ที่ส่ง broker จริง (exec_sl) ตัวเดียวกับที่ด่าน R:R ใช้
        sl, tp, rr = sl_info["sl"], sl_info["tp"], sl_info["rr"]
        print(f"  [{symbol}] ผ่านด่าน {strategy}  R:R={rr:.2f}  SL={sl:.2f}  TP={tp:.2f}")

        # 4b-2. เพดานระยะ TP เป็นเท่าของ ATR ตอนเข้าไม้ (ดูที่มา/ตัวเลข/คำเตือนที่ config.TP_MAX_ATR)
        #     วางไว้ **หลังด่าน R:R ใน compute_entry ผ่านแล้ว** โดยตั้งใจ ตรงกับลำดับใน backtest_replay.py:
        #     ไม้ต้องผ่าน MIN_RR_HARD_BLOCK ด้วย TP โครงสร้างจริงก่อน แล้วค่อยดึงเข้า — ถ้าดึงก่อน
        #     จะกลายเป็นการปล่อยไม้ที่โครงสร้างไม่มีที่ไปให้ผ่านด่านเพราะเป้ามันใกล้ (คนละเรื่องกัน)
        #     ใช้ atr_entry จาก sl_info = ตัวเดียวกับที่ compute_entry ใช้คิด exec_sl (ทั้งทาง
        #     Scoring และ Reversal คืนคีย์นี้) ไม่คำนวณ ATR ใหม่ กันสองที่ได้คนละค่าแบบที่เคยเจอ
        _atr_entry = sl_info.get("atr_entry")
        if TP_MAX_ATR and _atr_entry:
            _cap = _atr_entry * TP_MAX_ATR
            _tp_capped = (min(tp, entry + _cap) if direction == "Long" else max(tp, entry - _cap))
            if _tp_capped != tp:
                print(f"  [{symbol}] ดึง TP เข้า — เป้าเดิม {tp:.5f} ห่าง "
                      f"{abs(tp - entry) / _atr_entry:.1f} ATR เกินเพดาน {TP_MAX_ATR:g} "
                      f"-> TP = {_tp_capped:.5f}  R:R จริง = "
                      f"{calc_rr(entry, sl_info.get('exec_sl') or sl, _tp_capped, direction):.2f}")
                tp = _tp_capped

        # 4c. ด่านกันเข้า "ตอนปลายทาง" — ราคาวิ่งไปทางที่จะเข้ามาแล้วเกิน MAX_RUNUP_24H_R เท่าของ
        #     ระยะเสี่ยง ภายใน 24 แท่ง 1H ที่ผ่านมา ให้ข้ามรอบนี้ (ดูที่มา/ตัวเลขที่ config.py)
        #     ใช้เฉพาะ Scoring — Reversal เข้าสวนเทรนด์โดยดีไซน์ ตัวเลขนี้ตีความคนละแบบ
        #     วัดด้วย "ระยะเสี่ยงจริงของไม้นี้" (entry -> SL ที่ส่ง broker) ให้เทียบข้าม symbol ได้
        if MAX_RUNUP_24H_R is not None and strategy == "Scoring":
            _exec_sl_for_runup = sl_info.get("exec_sl") or sl
            _risk = abs(entry - _exec_sl_for_runup)
            _h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=27)
            if _risk and len(_h1) >= 26:
                # iloc[-1] = แท่งที่ยังไม่ปิด, iloc[-2] = แท่งที่ปิดล่าสุด (ราคา ~ตอนนี้)
                # ย้อนไปอีก 24 แท่งจึงเป็น iloc[-2-24] = iloc[-26]
                # 2026-09-05: เดิมเขียน iloc[-25] = ย้อนแค่ 23 แท่ง ไม่ตรงกับ backtest_replay ที่
                # เทียบ clock["close"].iloc[n-24] กับราคาเข้าที่ index n (24 แท่งพอดี) — ผลต่างเล็ก
                # แต่เป็นความไม่ตรงกันระหว่างเครื่องมือวัดกับระบบจริง ซึ่งเป็นบั๊กแบบเดียวกับ
                # lookahead ที่เพิ่งแก้ไปวันนี้
                _past = float(_h1["close"].iloc[-26])
                _runup = ((entry - _past) if direction == "Long" else (_past - entry)) / _risk
                if _runup > MAX_RUNUP_24H_R:
                    print(f"  [{symbol}] NO ENTRY — ราคาวิ่งไปทาง {direction} มาแล้ว {_runup:.2f}R "
                          f"ใน 24 ชม. (เกิน {MAX_RUNUP_24H_R:g}R) — เข้าตอนปลายทาง")
                    return

                # 4d. ด่านฝาแฝดคนละด้าน — กันเข้าไม้ "ตรงจุดสุดขั้วพอดี" (ยังไม่เด้งให้เห็น)
                #     ดูที่มา/ตัวเลขคัดกรองทั้งหมดที่ config.MIN_TURN_FROM_EXTREME_R
                #     หน้าต่าง = 24 แท่ง 1H ที่ปิดแล้ว **รวมแท่งปิดล่าสุด** (iloc[-25:-1] เพราะ
                #     iloc[-1] คือแท่งที่ยังไม่ปิด) = ชุดเดียวกับที่ backtest_replay ใช้
                #     (clock.iloc[n-24:n]) และตรงกับที่ entry_features.py คัดกรองไว้เป๊ะ
                #     ⚠️ บล็อกนี้ซ้อนอยู่ใต้การดึง _h1 ของข้อ 4c — ถ้าตั้ง MAX_RUNUP_24H_R = None
                #     ด่านนี้จะหยุดทำงานตามไปด้วยเงียบๆ (ถ้าจะปิด 4c ต้องย้ายการดึง _h1 ออกมา)
                if MIN_TURN_FROM_EXTREME_R is not None:
                    # เช็ค cooldown **ก่อน** ตัวด่านเสมอ — ถ้าเช็คทีหลัง การถูกปฏิเสธซ้ำทุก
                    # ชั่วโมงจะไปต่ออายุ cooldown ของตัวเองไปเรื่อยๆ กลายเป็นบล็อกถาวร
                    # (ลำดับเดียวกับ backtest_replay ต้องตรงกันเป๊ะ ไม่งั้นวัดคนละระบบ)
                    _cd_until = reject_cd_blocked(symbol, direction)
                    if _cd_until:
                        print(f"  [{symbol}] SKIP — ติด cooldown หลังถูกด่านปฏิเสธ "
                              f"(ถึง {_cd_until:%Y-%m-%d %H:%M})")
                        return
                    _w = _h1.iloc[-25:-1]
                    _turn = ((entry - float(_w["low"].min())) if direction == "Long"
                             else (float(_w["high"].max()) - entry)) / _risk
                    if _turn < MIN_TURN_FROM_EXTREME_R:
                        print(f"  [{symbol}] NO ENTRY — เข้าตรงจุดสุดขั้ว 24 ชม. "
                              f"(เด้งมาแค่ {_turn:.2f}R < {MIN_TURN_FROM_EXTREME_R:g}R)")
                        reject_cd_set(symbol, direction)
                        return

        # 4e. Pullback — ด่านสุดท้าย: ราคาต้องอยู่ ≥ PULLBACK_MIN_RANGE_POS ของกรอบ 24 แท่ง 1H ที่ปิดแล้ว
        #     (รวมแท่งปิดล่าสุด = clock.iloc[n-23:n+1] ของ replay) ไม่งั้นเริ่ม "ไม้เงา" ด้วย SL ที่ส่ง broker + TP หลังเพดาน
        #     ต้องอยู่หลังทุกด่าน — ถ้าบล็อกรอบที่ไม้จะติดด่านอื่นอยู่แล้ว ไม้เงาจะกันไม้ดีที่มาทีหลังไปด้วย (replay --rev-new-lock −16.77R)
        if pb_dir and PULLBACK_MIN_RANGE_POS:
            _h1p = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=27)
            if _h1p is None or len(_h1p) < 26:
                print(f"  [{symbol}] SKIP — Pullback: ดึงแท่ง 1H ไม่พอคิดตำแหน่งในกรอบ 24 ชม.")
                return
            _rpos = pullback.range_position(_h1p.iloc[-25:-1], entry, direction)
            if _rpos < PULLBACK_MIN_RANGE_POS:
                pullback_ghost_set(symbol, direction, sl_info.get("exec_sl") or sl, tp)
                print(f"  [{symbol}] NO ENTRY — Pullback: ราคาอยู่ {_rpos:.2f} ของกรอบ 24 ชม. "
                      f"(< {PULLBACK_MIN_RANGE_POS:g}) -> เริ่มไม้เงา {direction} "
                      f"SL {sl_info.get('exec_sl') or sl:.5f} / TP {tp:.5f}")
                log.info(f"[{symbol}] revnew ghost {direction} pos={_rpos:.2f}")
                return

        # 5. Execute
        # ── ฐานตรึงของ ATR Trailing SL — คำนวณก่อนส่ง order เพื่อส่งเข้า place_order() รวดเดียว
        # (2026-08-09: ย้าย journal logging เข้าไปอยู่ใน place_order() เอง ไม่แยกเรียกทีหลังอีก —
        # กันเคสไม้จริงหลุดไม่ถูกบันทึก ดู comment เต็มที่ order.place_order()) exit_monitor.py จะ
        # อ่านค่านี้กลับมาใช้ตลอดการถือ แทนคำนวณ swing/atr_entry ใหม่ทุกชั่วโมง (กัน anchor สลับถ้า
        # ถือยาวจน swing เดิมหลุดขอบ rolling window — ดู known bug ATR trailing anchor)
        #
        # 2026-08-07: ฐานตรึงใช้ `sl` (SL จริงที่คำนวณตอนเข้า = Swing + ATR4H×0.1 buffer, ตัวเดียว
        # กับที่เอาไปคิด R:R และส่งเป็น Broker SL) แทน trail["swing"] (Swing ดิบ ไม่มี buffer) เดิม
        # — เดิมสองระบบนี้คำนวณจุดยึดคนละจุดกันเอง ทำให้ระยะเสี่ยงจริง (trailing) ไม่ตรงกับ R:R
        # ที่ใช้กรองตอนเข้า พอ anchor เดียวกัน ทั้ง R:R ตอนเข้า และ Trailing SL ระหว่างถือ จะไปทาง
        # เดียวกันเสมอ — ยังคง roll ตาม ATR1H รายชั่วโมงเหมือนเดิมทุกอย่าง เปลี่ยนแค่จุดเริ่มต้น
        # 2026-08-31: exec_sl / atr_entry มาจาก compute_entry แล้ว (sl_info) ไม่คำนวณซ้ำที่นี่ —
        # เดิมคำนวณตรงนี้ *หลัง* ด่าน R:R ผ่านไปแล้ว ทำให้ด่านตรวจคนละระยะเสี่ยงกับที่ส่งจริง
        # และถ้าคำนวณสองที่ก็มีโอกาสได้ ATR คนละค่า (คนละวินาที/คนละจำนวนแท่ง) — ดู scoring.py
        pinned_swing = pinned_atr_entry = None
        if strategy == "Sideway":
            pinned_swing = sl_info["sl"]   # exit_monitor ใช้เป็นระยะ 1R (ไม่มี ATR trailing)
        exec_sl = sl_info.get("exec_sl") or sl   # SL ที่ส่ง broker จริง
        try:
            if sl_info.get("atr_entry") is not None:
                pinned_swing, pinned_atr_entry = sl, sl_info["atr_entry"]
                # SL ที่ส่ง broker = จุดเดียวกับที่ ATR trailing จะเลื่อนไปในรอบแรก (pinned_swing ∓ 2×ATR) — เดิมส่ง SL แคบไปแล้ว exit_monitor ขยายใน 1 ชม.
                #   = lot คิดจากความเสี่ยงจริง · ด่าน R:R วัดด้วย exec SL ตั้งแต่ 2026-08-31 (เดิมวัดด้วย SL โครงสร้างที่แคบกว่า 1.49 เท่า)
                # 📜 ผลวัด/ประวัติเดิม: HISTORY.md § "scheduler.py — exec SL ตอนเปิดไม้ (2026-08-27..31)"
        except Exception as exc:
            print(f"  [{symbol}] WARNING — บันทึกฐานตรึงไม่ได้ ({exc}) — exit_monitor จะ fallback คำนวณเองภายหลัง")

        _risk_frac = SIDEWAY_RISK_PER_TRADE if strategy == "Sideway" else RISK_PER_TRADE
        lot, _ = calculate_lot_size(symbol, entry, exec_sl, balance, _risk_frac)
        lot    = clamp_lot(symbol, lot)
        # ทำให้การปัดเศษ lot "มองเห็นได้" — volume_step ของโบรกหยาบกว่าที่ต้องการบาง symbol
        # (XAU: 0.02 = 1.67% · 0.03 = 2.50% ไม่มีค่าไหนได้ 2%) เดิมมันเงียบสนิทเพราะ backtest
        # รายงานเป็น R ซึ่งไม่ขึ้นกับขนาดไม้ ดูที่มา/ตัวเลขทั้งหมดที่ config.LOT_ROUNDING
        _risk_pct = risk_pct_of(symbol, entry, exec_sl, lot, balance)
        _target = _risk_frac * 100
        if _risk_pct == _risk_pct and abs(_risk_pct - _target) / _target * 100 > LOT_RISK_WARN_PCT:
            print(f"  [{symbol}] ⚠️ lot {lot} ทำให้เสี่ยงจริง {_risk_pct:.2f}% "
                  f"(เป้า {_target:.1f}% · เพี้ยน {(_risk_pct - _target) / _target * 100:+.0f}%) "
                  f"— volume_step {mt5.symbol_info(symbol).volume_step:g} หยาบเกินไปสำหรับระยะ SL นี้")
        if exec_sl != sl:
            rr_exec = calc_rr(entry, exec_sl, tp, direction)
            print(f"  [{symbol}] SL ที่ส่ง broker = {exec_sl:.2f} (SL โครงสร้าง {sl:.2f} "
                  f"ขยาย 2xATR ให้ตรงกับ ATR trailing)  R:R จริง = {rr_exec:.2f}")

        ticket = place_order(symbol, direction, entry, exec_sl, tp, lot,
                             strategy=strategy,
                             pinned_swing=pinned_swing, pinned_atr_entry=pinned_atr_entry)
        print(f"  [{symbol}] ORDER SENT ✅  Ticket=#{ticket}  Lot={lot}")
        if ticket and slot_of(strategy) == slot_of("Reversal"):
            # ไม้นี้ครองช่อง Reversal แล้ว -> limit ของแบบ A ที่ค้างจะเติมไม่ได้ (replay: "เติมแต่ช่องไม่ว่าง")
            rev_a_cancel_pending(symbol, f"ช่อง Reversal ถูกไม้ {strategy} #{ticket} ใช้แล้ว")
        if TP_TO_ENTRY_ON_OPPOSITE and ticket and strategy != "Sideway" \
                and (strategy not in ("Reversal", "Pullback", REV_A_STRATEGY) or TP_TO_ENTRY_REVERSAL_TRIGGERS):   # ดู config (Pullback ทำตาม Reversal = ตอนวัด)
            _tp_to_entry_on_opposite(symbol, direction, ticket)

    except ValueError as exc:
        print(f"  [{symbol}] BLOCKED — {exc}")
    except Exception as exc:
        print(f"  [{symbol}] ERROR — {exc}")
        log.error(f"[{symbol}] scan_symbol ERROR", exc_info=True)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run_scheduler() -> None:
    load_dotenv()
    print("=" * 52)
    print("  AUTO TRADER SCHEDULER  (กด Ctrl+C เพื่อหยุด)")
    print(f"  Symbols  : {', '.join(SYMBOLS)}")
    print(f"  Interval : {INTERVAL_SECONDS // 60} นาที  (สแกนที่ต้นชั่วโมง +{SCAN_DELAY_SECONDS} วิ)")
    print("=" * 52)

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 52}")
        print(f"  รอบใหม่  {now}")
        print(f"{'=' * 52}")

        try:
            connect()
            # sync journal กับ MT5 ก่อนสแกน — ไม้ที่ชน SL/TP ไปเองต้องถูก mark ปิดก่อน
            # ไม่งั้น journal.check_daily_loss() ใน scan_symbol จะมองไม่เห็นการขาดทุนพวกนั้น
            try:
                journal.reconcile_closed_positions()
            except Exception as exc:
                print(f"[journal] reconcile ล้มเหลว — {exc}")
                log.error("reconcile_closed_positions ERROR", exc_info=True)

            # สรุป **เมื่อวาน** ในรอบแรกที่สำเร็จของวันใหม่ (ดู send_daily_summary_if_due)
            # 🔴 2026-09-25: เดิมสรุป "วันนี้" ตอน 00:xx ได้ "เทรด 0" ทุกฉบับ (log 09-17 ถึง 09-25
            # ทั้งที่ 09-21 มี SL −178.32 และ 09-24 มี TP +236.76) · close_date ใน CSV เป็นเวลาเครื่อง
            try:
                send_daily_summary_if_due(datetime.now().date())
            except Exception as exc:
                print(f"[journal] ส่งสรุปรายวันล้มเหลว — {exc}")
                log.error("notify_daily_summary ERROR", exc_info=True)

            for symbol in SYMBOLS:
                scan_symbol(symbol)
        except Exception as exc:
            print(f"[ERROR] MT5 connection: {exc}")
            notify.notify_error("scheduler — MT5 connection", str(exc))
            log.error("MT5 connection ERROR", exc_info=True)
        finally:
            mt5.shutdown()

        target = time.time() + seconds_until_next_scan(time.time())
        print(f"\n  รอบถัดไป : {datetime.fromtimestamp(target):%H:%M:%S}  "
              f"(อีก {(target - time.time()) / 60:.0f} นาที)")
        print("-" * 52)
        sleep_until(target)


if __name__ == "__main__":
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\n\n  หยุด Scheduler แล้ว")
