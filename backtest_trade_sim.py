"""backtest_trade_sim.py — เดินไม้ที่ "รู้อยู่แล้วว่าระบบเข้า" ไปข้างหน้าทีละชั่วโมง

ต่างจาก backtest_replay.py ตรงที่ **ไม่สแกนหาสัญญาณเลย** — รับไม้ที่มีอยู่แล้ว (ปกติมาจาก
replay_trades_<symbol>.csv) แล้วจำลองแค่ช่วงที่ถือไม้ ใช้ exit_monitor.analyze_position()
ตัวจริงเหมือน replay ทุกประการ ลอจิกในนี้ลอกมาจาก backtest_replay.step_position() ข้อต่อข้อ

ทำไมถึงคุ้ม: replay ต้องเดิน 17,520 รอบ/symbol (~10-15 นาที) ตัวนี้เดินเฉพาะชั่วโมงที่ถือไม้อยู่
(~3-4 นาที ต่อ 5 ชุดเงื่อนไข) จึงวัดหลายเงื่อนไขในรอบเดียวได้ ใช้ได้กับทุกคำถามที่ **ไม่เปลี่ยน
ชุดไม้**: กฎ exit, ATR trailing, เพดาน TP, หรือการเลื่อนเวลาเข้าไม้ของไม้เดิม

⚠️ สิ่งที่ตัวนี้มองไม่เห็น (ต้องยืนยันด้วย backtest_replay เต็มเสมอก่อนแก้ระบบจริง):
  - ช่องถือไม้ — ไม้ที่ออกช้าลงจะครองช่องนานขึ้นจนไม้ถัดไปหายไป ที่นี่แต่ละไม้อิสระจากกัน
  - cooldown / daily loss guard ที่ขยับตามเวลาปิดไม้
ตอนปิดกฎ Climax (2026-09-10) ผลออกมาว่าชุดไม้ไม่เปลี่ยนเลยสักไม้ แต่นั่นไม่การันตีกฎอื่น

ตัวตรวจว่าเครื่องนี้ตรงกับ replay: จำลองไม้เดิมด้วยพารามิเตอร์ที่บันทึกไว้แล้วเทียบ R กับไฟล์
(2026-09-13: ตรงกัน 202/202 ไม้ 7 symbol)
"""
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5

from config import MT5_TIMEFRAMES
from scoring import get_ohlcv
import exit_monitor as em

# เพดานถือไม้ — อ่านจาก exit_monitor (เข้าระบบจริงแล้วตั้งแต่ 2026-09-14) ตั้งค่าที่นั่นที่เดียว
MAX_HOLD_DAYS = em.MAX_HOLD_DAYS


class SimPos:
    """แทน position object ของ MT5 ให้ analyze_position() อ่านได้ (ไม้จำลองไม่มีใน MT5/journal)"""

    def __init__(self, symbol, direction, entry, sl, tp, lot, t):
        self.symbol, self.price_open, self.sl, self.tp = symbol, entry, sl, tp
        self.volume, self.ticket = lot, 0
        self.time = int(pd.Timestamp(t).timestamp())
        self.type = mt5.POSITION_TYPE_BUY if direction == "Long" else mt5.POSITION_TYPE_SELL


class TradeSim:
    """เรียก connect() ให้เรียบร้อยก่อนสร้าง — ดึงแท่ง 1H ครั้งเดียวแล้วใช้ซ้ำทุกไม้ทุกเงื่อนไข"""

    def __init__(self, symbol: str, days: int = 730, use_cost: bool = True):
        self.symbol = symbol
        info = mt5.symbol_info(symbol)
        spread = (info.ask - info.bid) if info else 0.0
        if spread <= 0 and info:
            spread = info.spread * info.point
        self.cost_pct = (spread / info.bid * 100) if (use_cost and info and info.bid) else 0.0
        self.h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=days * 24 + 500)
        self.idx = {t: i for i, t in enumerate(self.h1["time"])}

    def run(self, direction, entry, sl0, tp0, pinned_swing, pinned_atr, t0, strategy):
        """t0 = เวลาที่ระบบตัดสินใจ (คอลัมน์ time ของ replay_trades) — ราคาเข้าคือ close ของแท่ง
        ที่ปิดพอดีตอนนั้น คืน dict(R, how, exit_time, cuts, MFE) หรือ None ถ้าเดินไม่ได้"""
        i0 = self.idx.get(t0 - timedelta(hours=1))
        if i0 is None:
            return None
        long_ = direction == "Long"
        risk = abs(entry - sl0)
        if risk <= 0:
            return None
        sl, tp, booked, rem, cuts, mfe = sl0, tp0, 0.0, 1.0, 0, 0.0

        for i in range(i0 + 1, len(self.h1)):
            bar = self.h1.iloc[i]
            now = bar["time"] + timedelta(hours=1)
            mfe = max(mfe, ((bar["high"] - entry) if long_ else (entry - bar["low"])) / risk)

            # 1) broker: SL/TP ทำงานระหว่างแท่ง ไม่ต้องรอ monitor
            if (bar["low"] <= sl) if long_ else (bar["high"] >= sl):
                r = ((sl - entry) if long_ else (entry - sl)) / risk
                how = "BE" if abs(sl - entry) < 1e-9 else "SL"
                return self._done(booked + rem * r, how, entry, sl0, now, cuts, mfe)
            if (bar["high"] >= tp) if long_ else (bar["low"] <= tp):
                r = ((tp - entry) if long_ else (entry - tp)) / risk
                return self._done(booked + rem * r, "TP", entry, sl0, now, cuts, mfe)

            # 2) exit_monitor ตัวจริงที่ปลายชั่วโมง (ตรงกับ INTERVAL_SECONDS ของระบบจริง)
            sim = SimPos(self.symbol, direction, entry, sl, tp, rem, t0)
            try:
                m = em.analyze_position(sim, as_of=now, ctx={
                    "pinned_swing": pinned_swing, "pinned_atr_entry": pinned_atr,
                    "strategy": strategy, "original_lot": 1.0, "original_tp": tp0})
            except Exception:
                continue                       # ข้อมูลไม่พอรอบนี้ — ถือต่อ
            keep = m["recommended_keep_pct"] / 100
            if keep < rem - 1e-9:
                r_now = ((m["current_price"] - entry) if long_
                         else (entry - m["current_price"])) / risk
                booked += (rem - keep) * r_now
                rem = keep
                cuts += 1
                if rem <= 1e-9:
                    return self._done(booked, m["final_decision"][0][:24], entry, sl0, now, cuts, mfe)
            if m["desired_sl"] is not None:
                sl = m["desired_sl"]
            if m["desired_tp"] is not None:
                tp = m["desired_tp"]
            if (now - t0) >= timedelta(days=MAX_HOLD_DAYS):
                r = ((bar["close"] - entry) if long_ else (entry - bar["close"])) / risk
                return self._done(booked + rem * r, "HOLD-CAP", entry, sl0, now, cuts, mfe)
        return None

    def run_row(self, b):
        """เดินไม้จากแถวของ replay_trades_<symbol>.csv ตรง ๆ (ใช้ทำ control)"""
        return self.run(b.direction, b.entry, b.sl0, b.tp0, b.pinned_swing,
                        b.pinned_atr_entry, b.time, b.strategy)

    def _done(self, r, how, entry, sl0, t, cuts, mfe):
        r -= self.cost_pct / 100 * entry / abs(entry - sl0)   # spread เข้า+ออก ~1 ครั้ง
        return {"R": r, "how": how, "exit_time": t, "cuts": cuts, "MFE": mfe}


def load_trades(symbol: str, strategy: str = None, tag: str = "") -> pd.DataFrame:
    """tag = ส่วนท้ายชื่อไฟล์ผลของ backtest_replay (เช่น "_1rkeep100_hwkeep100") — ต้องเลือกไฟล์
    ที่สร้างด้วยค่าคงที่ชุดเดียวกับที่ระบบใช้อยู่ ไม่งั้น control จะไม่ตรงกับไฟล์"""
    t = pd.read_csv(f"replay_trades_{symbol}{tag}.csv", parse_dates=["time"])
    return t[t.strategy == strategy].reset_index(drop=True) if strategy else t


def control_check(d: pd.DataFrame) -> str:
    """บรรทัดสรุปว่าเครื่องจำลองตรงกับไฟล์ replay ไหม — ถ้าไม่ตรง ผลทั้งรอบใช้ไม่ได้"""
    ok = d[d.R_control.notna()]
    return (f"control เทียบไฟล์: ΣR ไฟล์ {ok.R_file.sum():+.2f}  control {ok.R_control.sum():+.2f}  "
            f"(ตรงกัน {int((ok.R_file - ok.R_control).abs().lt(0.01).sum())}/{len(ok)} ไม้)")
