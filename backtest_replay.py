"""
backtest_replay.py — จำลอง scheduler.scan_symbol() ย้อนหลังทีละชั่วโมง ให้ "เงื่อนไขการเข้าไม้"
ตรงกับระบบจริงมากที่สุดเท่าที่ backtest จะทำได้

ต่างจาก backtest_criteria.py (เครื่องมือวินิจฉัยรายเกณฑ์ ที่จงใจตัดตัวกรองบางอย่างออกเพื่อให้มี
ตัวอย่างพอวัดผลรายเกณฑ์) — ตัวนี้ตั้งใจ **ไม่ตัดอะไรออกเลย** เดินตามลำดับด่านเดียวกับ
scheduler.scan_symbol() เป๊ะ เพื่อให้ตัวเลขที่ได้เอาไปตัดสินใจแทนผลเทรดจริงได้

ลำดับด่าน (ตรงกับ scheduler.scan_symbol ข้อต่อข้อ):
  1. มี position เปิดอยู่ -> ไม่หา entry ใหม่ (ดูแลไม้เดิมแทน)
  3. Daily loss guard — ขาดทุนรวมของ "ไม้ที่ปิดวันนี้" ถึง MAX_DAILY_LOSS แล้วหยุดหาไม้ใหม่
  3b. Cooldown guard — เพิ่งปิดไม้ symbol นี้ไปไม่ถึง COOLDOWN_HOURS_BY_SYMBOL ชม.
  4b. News guard — ⚠️ จำลองไม่ได้ (ดู "สิ่งที่ยังต่างจากของจริง" ด้านล่าง)
  4. Regime check — SKIP ถ้า regime อยู่ใน REGIME_NO_TRADE
     REGIME_TREND    -> Scoring  (get_trend_bias + compute_score)
     REGIME_REVERSAL -> Reversal (compute_reversal_score ทิศตามขั้ว divergence)
  5. ผ่านสกอร์การ์ด -> เปิดไม้ที่ราคา ณ ชั่วโมงนั้น + spread

รอบสแกน = ทุก 1 ชั่วโมง ตรงกับ scheduler.INTERVAL_SECONDS (backtest_criteria.py สแกนทุก 4 ชม.
ซึ่งทำให้พลาดจังหวะที่ระบบจริงเข้าได้ 3 ใน 4 ของโอกาส)

⚠️ สิ่งที่ยังต่างจากของจริง — อ่านก่อนเชื่อตัวเลข:
  1. **Exit ใช้ exit_monitor.analyze_position(as_of=...) ตัวจริง** (2026-08-27) — ได้ ATR
     trailing SL, structure break, trend invalidation, climax, RSI/BB ร้อน, slow-trade 3 วัน,
     TP trailing, การคูณ keep% เป็นทอด ครบตามระบบจริง เรียกทุก 1 ชม.ตรงกับ INTERVAL_SECONDS
     สิ่งที่ยังต่าง: broker fill SL/TP ถือว่าได้ราคาเป๊ะ และ execute_decision() ตัวจริงจะปิด
     บางส่วนตาม lot ที่ปัดแล้ว (clamp_lot) ส่วนที่นี่คิดเป็นสัดส่วนล้วน
  2. **News guard จำลองไม่ได้** — check_upcoming_news() ยิง ForexFactory แบบ real-time
     ไม่มีข้อมูลย้อนหลัง ผลคือ backtest จะ "เข้าไม้ก่อนข่าว" ในจังหวะที่ระบบจริงหลบ
  3. ราคาที่ใช้เป็น close ของแท่ง 1H ที่ปิดพอดี ณ เวลานั้น (ของจริงคือ tick.bid ระหว่างชั่วโมง)
  4. SL/TP ถือว่า fill ได้ที่ราคานั้นเป๊ะ ไม่มี slippage / gap / requote
  5. ไม่ได้จำลอง broker trade_mode, balance จริง, lot rounding — R-multiple ไม่ขึ้นกับ lot อยู่แล้ว
     แต่ daily loss guard ที่คิดจาก R จึงเป็นค่าประมาณ (MAX_DAILY_LOSS / RISK_PER_TRADE = กี่ R)

ใช้: py backtest_replay.py BTCUSDm [days=730] [--no-cost] [--min-sl=X]
     --min-sl=X  ทับค่า MIN_SL_DISTANCE_PCT ของ symbol นี้ (ไว้เทียบว่าเกณฑ์ไหนดีกว่าบนไม้จริง)
     --no-widen  ห้าม SL ขยับออกไปไกลกว่า SL ตอนเข้า (ยังขยับเข้าหาราคาได้ตามปกติ) — ไว้ตอบว่า
                 การที่ ATR trailing สั่ง SL แรกกว้างกว่า SL ตอนเข้า เป็นตัวทำให้ขาดทุนเกิน 1R
                 หรือมันช่วยให้ไม้รอดจากการย่อจนไปต่อได้ (ทดลองใน backtest เท่านั้น ไม่แตะระบบจริง)
     --legacy-sl ใช้ SL โครงสร้างเป็น SL ที่ส่ง broker แบบเดิม (ก่อน 2026-08-27) — ไว้เทียบผล
                 ของการส่ง SL แรกไปที่จุดเดียวกับ ATR trailing ตามที่ scheduler.py ทำตอนนี้
"""
import sys
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from mt5_connect import connect
import config
from config import (MT5_TIMEFRAMES, MAX_DAILY_LOSS, RISK_PER_TRADE,
                    COOLDOWN_HOURS_BY_SYMBOL, get_min_sl_distance_pct)
from scoring import compute_score, get_trend_bias, get_ohlcv, get_ohlcv_real, calc_rr
from binance import merge_real_volume
from regime_check import get_regime
import exit_monitor as em
import reversal

REGIME_NO_TRADE = ("CHOPPY", "เขตเทา", "REVERSAL-WATCH")   # ตรงกับ scheduler.py
REGIME_TREND    = ("TREND", "TREND แรงจัด")
REGIME_REVERSAL = ("REVERSAL-READY",)

# เพดานถือไม้ของ backtest เอง — ระบบจริงไม่มีเพดานเวลา (กฎ slow-trade ตัดแค่ 50%) ตั้งไว้กัน
# ไม้ค้างกินเวลารันเท่านั้น ถ้ามีไม้ชนเพดานบ่อยแปลว่าต้องขยาย
MAX_HOLD_DAYS   = 30


class SimPos:
    """แทน position object ของ MT5 ให้ analyze_position() อ่านได้ — ไม้จำลองไม่มีใน MT5/journal"""
    def __init__(self, symbol, direction, entry, sl, tp, lot, t):
        self.symbol, self.price_open, self.sl, self.tp = symbol, entry, sl, tp
        self.volume, self.ticket = lot, 0
        self.time = int(pd.Timestamp(t).timestamp())
        self.type = mt5.POSITION_TYPE_BUY if direction == "Long" else mt5.POSITION_TYPE_SELL

symbol   = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
days     = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 730
use_cost  = "--no-cost" not in sys.argv
no_widen  = "--no-widen" in sys.argv
legacy_sl = "--legacy-sl" in sys.argv

# --min-sl=X : ทับเกณฑ์ระยะ SL ขั้นต่ำของ symbol นี้ — get_min_sl_distance_pct() อ่าน dict
# ตอนถูกเรียกทุกครั้ง การแก้ตรงนี้จึงมีผลกับ compute_score/reversal ทันทีโดยไม่ต้องแก้ config.py
_min_sl_arg = next((a for a in sys.argv if a.startswith("--min-sl=")), None)
if _min_sl_arg:
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL = dict(config.MIN_SL_DISTANCE_PCT_BY_SYMBOL)
    config.MIN_SL_DISTANCE_PCT_BY_SYMBOL[symbol] = float(_min_sl_arg.split("=")[1])

connect()

info = mt5.symbol_info(symbol)
spread_price = (info.ask - info.bid) if info else 0.0
if spread_price <= 0 and info:
    spread_price = info.spread * info.point
cost_pct = (spread_price / info.bid * 100) if info and info.bid else 0.0

h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=days * 24 + 500)
h4 = get_ohlcv(symbol, MT5_TIMEFRAMES["4H"], bars=days * 6 + 400)
end_time   = h1["time"].iloc[-1]
start_time = end_time - timedelta(days=days)
clock = h1[h1["time"] >= start_time].reset_index(drop=True)
h4_idx = {t: i for i, t in enumerate(h4["time"])}

max_daily_loss_r = MAX_DAILY_LOSS / RISK_PER_TRADE   # 6% / 2% = 3R ต่อวัน

print(f"\n{symbol}  replay backtest  {clock['time'].iloc[0]} -> {end_time}")
print(f"  สแกนทุก 1 ชม. ({len(clock)} รอบ)   spread ปัจจุบัน {spread_price:.2f} "
      f"({cost_pct:.4f}% ของราคา){'  [คิดต้นทุน]' if use_cost else '  [--no-cost]'}")
print(f"  Daily loss guard {MAX_DAILY_LOSS*100:.0f}% / risk {RISK_PER_TRADE*100:.0f}% ต่อไม้ "
      f"= หยุดหาไม้ใหม่เมื่อวันนั้นขาดทุนรวมถึง {max_daily_loss_r:.1f}R")
print(f"  Cooldown {COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)} ชม.   "
      f"MIN_SL {get_min_sl_distance_pct(symbol)}%{'  [--no-widen]' if no_widen else ''}"
      f"{'  [ทับด้วย --min-sl]' if _min_sl_arg else ''}\n")

pos = None
trades, skips = [], {}
daily_r = {}
last_close_time = None

# cache: regime คำนวณจากแท่ง 4H ที่ปิดแล้วเท่านั้น จึงคงที่ตลอดช่วง 4 ชม.เดียวกัน — ไม่ต้อง
# คำนวณซ้ำทุกชั่วโมง (ลด MT5 call + ADX/structure/key level/divergence ลง 4 เท่า)
# เช่นเดียวกับเฟรม 1D ที่เปลี่ยนวันละครั้ง
_regime_cache, _df1d_cache = {}, {}


def regime_at(t):
    key = t.floor("4h")
    if key not in _regime_cache:
        _regime_cache.clear()
        _regime_cache[key] = get_regime(symbol, as_of=t)
    return _regime_cache[key]


def df1d_at(t):
    key = t.date()
    if key not in _df1d_cache:
        _df1d_cache.clear()
        _df1d_cache[key] = merge_real_volume(
            get_ohlcv(symbol, MT5_TIMEFRAMES["1D"], bars=800, as_of=t), symbol, "1D", as_of=t)
    return _df1d_cache[key]


def note(reason):
    skips[reason] = skips.get(reason, 0) + 1


def step_position(t, bar):
    """เดินไม้ที่ถืออยู่ไป 1 ชั่วโมง — broker เช็ค SL/TP ก่อน แล้ว exit_monitor ตัวจริงทำงาน
    ที่ปลายชั่วโมง (ตรงกับ INTERVAL_SECONDS=3600 ของระบบจริง)"""
    global pos
    long_ = pos["direction"] == "Long"
    risk = abs(pos["entry"] - pos["sl0"])      # ระยะเสี่ยงตอนเข้า = ฐาน 1R ของบัญชี

    # 1) broker: SL/TP ทำงานระหว่างแท่งเสมอ ไม่ต้องรอ monitor
    if (bar["low"] <= pos["sl"]) if long_ else (bar["high"] >= pos["sl"]):
        r = ((pos["sl"] - pos["entry"]) if long_ else (pos["entry"] - pos["sl"])) / risk
        how = "BE" if abs(pos["sl"] - pos["entry"]) < 1e-9 else "SL"
        return close_pos(t, pos["booked"] + pos["rem"] * r, how)
    if (bar["high"] >= pos["tp"]) if long_ else (bar["low"] <= pos["tp"]):
        r = ((pos["tp"] - pos["entry"]) if long_ else (pos["entry"] - pos["tp"])) / risk
        return close_pos(t, pos["booked"] + pos["rem"] * r, "TP")

    # 2) exit_monitor ตัวจริง — เรียกด้วย as_of + ctx (ไม้จำลองไม่มีใน journal)
    sim = SimPos(symbol, pos["direction"], pos["entry"], pos["sl"], pos["tp"], pos["rem"], pos["time"])
    try:
        m = em.analyze_position(sim, as_of=t, ctx={
            "pinned_swing": pos["pinned_swing"], "pinned_atr_entry": pos["pinned_atr_entry"],
            "strategy": pos["strategy"], "original_lot": 1.0, "original_tp": pos["tp0"]})
    except Exception:
        return None                            # ข้อมูลไม่พอรอบนี้ — ถือต่อ

    price = m["current_price"]
    keep = m["recommended_keep_pct"] / 100
    if keep < pos["rem"] - 1e-9:               # ปิดบางส่วน/ทั้งหมดตามที่ระบบสั่ง
        cut = pos["rem"] - keep
        r_now = ((price - pos["entry"]) if long_ else (pos["entry"] - price)) / risk
        pos["booked"] += cut * r_now
        pos["rem"] = keep
        pos["cuts"] += 1
        if pos["rem"] <= 1e-9:
            return close_pos(t, pos["booked"], m["final_decision"][0][:24])

    if m["desired_sl"] is not None:            # ATR trailing / breakeven
        new_sl = m["desired_sl"]
        if no_widen:                           # ห้ามถอย SL ออกไกลกว่าตอนเข้า
            new_sl = max(new_sl, pos["sl0"]) if long_ else min(new_sl, pos["sl0"])
        pos["sl"] = new_sl
    if m["desired_tp"] is not None:            # TP trailing
        pos["tp"] = m["desired_tp"]

    if (t - pos["time"]) >= timedelta(days=MAX_HOLD_DAYS):
        r = ((bar["close"] - pos["entry"]) if long_ else (pos["entry"] - bar["close"])) / risk
        return close_pos(t, pos["booked"] + pos["rem"] * r, "HOLD-CAP")
    return None


def close_pos(t, r, how):
    global pos, last_close_time
    if use_cost:
        r -= cost_pct / 100 * pos["entry"] / abs(pos["entry"] - pos["sl0"])   # spread ขาเข้า+ออก ~1 ครั้ง
    rec = {**pos, "exit_time": t, "R": r, "how": how}
    daily_r[t.date()] = daily_r.get(t.date(), 0.0) + r
    last_close_time = t
    pos = None
    return rec


for n, row in enumerate(clock.to_dict("records")):
    t, bar = row["time"], row

    if pos is not None:                                   # ด่าน 1
        rec = step_position(t, bar)
        if rec:
            trades.append(rec)
        continue

    if daily_r.get(t.date(), 0.0) <= -max_daily_loss_r:    # ด่าน 3
        note("daily loss guard")
        continue

    cd = COOLDOWN_HOURS_BY_SYMBOL.get(symbol, 0)           # ด่าน 3b
    if cd and last_close_time is not None and (t - last_close_time) < timedelta(hours=cd):
        note("cooldown")
        continue

    try:                                                   # ด่าน 4
        rinfo = regime_at(t)
    except Exception:
        note("regime error")
        continue
    regime = rinfo["regime"]
    if regime in REGIME_NO_TRADE:
        note(f"regime = {regime}")
        continue

    entry = float(bar["close"])
    try:
        if regime in REGIME_TREND:
            df_1d = df1d_at(t)
            direction, _ = get_trend_bias(symbol, df_1d)
            if direction is None:
                note("หา bias ไม่ได้")
                continue
            score, criteria, passed, sl_info = compute_score(symbol, direction, entry,
                                                            as_of=t, df_1d=df_1d)
            sl, tp, strategy = sl_info["sl"], sl_info["tp"], "Scoring"
        elif regime in REGIME_REVERSAL:
            pol = rinfo["divergence"]["divergence"]
            direction = "Long" if pol == "bullish" else "Short"
            score, criteria, passed, inf = reversal.compute_reversal_score(
                symbol, direction, entry, key_level=rinfo["key_level"],
                df_4h=rinfo["df_4h"], as_of=t)
            sl, tp, strategy = inf["sl"], inf["tp"], "Reversal"
        else:
            note(f"regime ไม่รู้จัก = {regime}")
            continue
    except ValueError as exc:
        note(str(exc).split("—")[0].strip()[:38])
        continue
    except Exception as exc:
        note(f"ERROR {type(exc).__name__}")
        continue

    if not passed:
        note(f"ไม่ผ่านสกอร์การ์ด ({strategy})")
        continue

    # เหมือน scheduler.py: ส่ง SL แรกไปที่จุดเดียวกับที่ ATR trailing จะเลื่อนไปในรอบแรก
    # (pinned_swing ยังเป็น sl โครงสร้างเท่าเดิม สูตร trailing จึงไม่เปลี่ยน)
    atr_entry = None
    if not legacy_sl:
        try:
            _tr = em.calc_atr_trailing_sl(get_ohlcv_real(symbol, "4H", bars=210, as_of=t),
                                          symbol, t, direction, as_of=t)
            if _tr:
                atr_entry = _tr["atr_entry"]
                sl = (sl - 2 * atr_entry) if direction == "Long" else (sl + 2 * atr_entry)
        except Exception:
            pass

    pos = {"time": t, "direction": direction, "entry": entry, "sl": sl, "sl0": sl,
           "tp": tp, "tp0": tp, "score": score, "strategy": strategy, "regime": regime,
           "booked": 0.0, "rem": 1.0, "cuts": 0,
           "pinned_swing": sl + 2 * atr_entry if (atr_entry and direction == "Long")
                           else (sl - 2 * atr_entry if atr_entry else sl),
           "pinned_atr_entry": atr_entry}

    if n % 2000 == 0:
        print(f"  ... {t}  ({n}/{len(clock)})  ปิดไปแล้ว {len(trades)} ไม้", flush=True)

mt5.shutdown()

# ---------------------------------------------------------------------------
t = pd.DataFrame(trades)
print(f"\n{'=' * 78}")
print(f"  รอบสแกน {len(clock)}  ->  เข้าไม้จริง {len(t)} ไม้"
      f"{'  (ยังถือค้าง 1 ไม้)' if pos else ''}")
print(f"  {'-' * 74}")
print("  เหตุผลที่ไม่เข้า (นับรอบสแกน):")
for k, v in sorted(skips.items(), key=lambda x: -x[1])[:10]:
    print(f"    {v:>6}  {k}")

if t.empty:
    print(f"{'=' * 78}")
    sys.exit(0)

t["win"] = t["R"] > 0
print(f"  {'-' * 74}")
print(f"  Win Rate   : {t['win'].mean()*100:.1f}%  ({int(t['win'].sum())}/{len(t)})")
print(f"  Avg R      : {t['R'].mean():+.2f}R      Total R: {t['R'].sum():+.1f}R")
print(f"  ถือเฉลี่ย   : {(t['exit_time']-t['time']).mean().total_seconds()/86400:.1f} วัน")

print(f"\n  {'กลุ่ม':<22}{'ไม้':>6}{'Win':>8}{'AvgR':>8}{'TotalR':>9}")
print(f"  {'-' * 53}")
for col in ("strategy", "direction", "how", "regime"):
    for k, g in t.groupby(col):
        print(f"  {str(k):<22}{len(g):>6}{g['win'].mean()*100:>7.1f}%{g['R'].mean():>+8.2f}{g['R'].sum():>+9.1f}")
    print(f"  {'-' * 53}")

mid = t["time"].min() + (t["time"].max() - t["time"].min()) / 2
for lbl, g in (("ครึ่งแรก", t[t["time"] < mid]), ("ครึ่งหลัง", t[t["time"] >= mid])):
    if len(g):
        print(f"  {lbl:<22}{len(g):>6}{g['win'].mean()*100:>7.1f}%{g['R'].mean():>+8.2f}{g['R'].sum():>+9.1f}")
print(f"{'=' * 78}")
t.to_csv(f"replay_trades_{symbol}.csv", index=False)
print(f"  เขียนไม้ทั้งหมดลง replay_trades_{symbol}.csv")
