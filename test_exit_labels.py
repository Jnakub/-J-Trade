"""ทดสอบการบันทึก label ของ exit — ใครปิด และปิดด้วยกฎข้อไหน

รัน: ./run_wine.sh test_exit_labels.py

ไม่แตะ trades_log.csv/cuts_log.csv ของจริง — ชี้ journal ไปที่ไฟล์ชั่วคราวก่อน
"""
import os
import shutil
import tempfile
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

import journal

TMP = tempfile.mkdtemp(prefix="jtrade_test_")
journal.LOG_FILE = os.path.join(TMP, "trades_log.csv")
journal.CUTS_FILE = os.path.join(TMP, "cuts_log.csv")

results = []


def ok(cond, label, extra=""):
    results.append(bool(cond))
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}{('  — ' + extra) if extra else ''}")


class Deal:
    def __init__(self, reason, entry=1, profit=0.0):
        self.reason, self.entry, self.profit = reason, entry, profit
        self.swap = self.commission = 0.0
        self.time = 1_757_000_000


# ---------------------------------------------------------------------------
print("\n1) deal reason -> status / exit_by  (หัวใจของการแยก 'บอทปิด' ออกจาก 'คนกดปิด')")
# ---------------------------------------------------------------------------
expect = {
    mt5.DEAL_REASON_SL:     ("Stop Loss",     "broker"),
    mt5.DEAL_REASON_TP:     ("Take Profit",   "broker"),
    mt5.DEAL_REASON_EXPERT: ("Bot Exit",      "bot"),
    mt5.DEAL_REASON_CLIENT: ("Manual Cut",    "manual"),
    mt5.DEAL_REASON_MOBILE: ("Manual Cut",    "manual"),
    mt5.DEAL_REASON_WEB:    ("Manual Cut",    "manual"),
    mt5.DEAL_REASON_SO:     ("Stop Out",      "broker"),
}
for reason, (want_status, want_by) in expect.items():
    got_s = journal._RESULT_BY_DEAL_REASON.get(reason)
    got_b = journal._EXIT_BY_DEAL_REASON.get(reason)
    ok(got_s == want_status and got_b == want_by,
       f"reason={reason} -> {want_status} / {want_by}", f"ได้ {got_s} / {got_b}")

ok("Manual Cut" not in {journal._RESULT_BY_DEAL_REASON[mt5.DEAL_REASON_EXPERT]},
   "บอทปิดเองไม่ถูกเรียกว่า Manual Cut อีกแล้ว")

# ---------------------------------------------------------------------------
print("\n2) reconcile เขียน exit_by ลง log จริง")
# ---------------------------------------------------------------------------
journal.log_trade_open("BTCUSDm", "Long", 60000.0, 58000.0, 64000.0, 0.1, 8.0,
                       ticket=555001, strategy="Scoring")

_open_tickets = set()
mt5.positions_get = lambda **kw: () if kw.get("ticket") not in _open_tickets else (object(),)
mt5.history_deals_get = lambda **kw: (Deal(mt5.DEAL_REASON_CLIENT, entry=0),
                                      Deal(mt5.DEAL_REASON_CLIENT, entry=1, profit=-120.0))
journal.notify = type("N", (), {"notify_order_closed": staticmethod(lambda *a, **k: None)})()
journal.is_demo_account = lambda: True

journal.reconcile_closed_positions()
row = journal._load().iloc[0]
ok(row["status"] == "Manual Cut" and row["exit_by"] == "manual",
   "ไม้ที่คนกดปิดใน MT5 -> status=Manual Cut, exit_by=manual",
   f"ได้ {row['status']} / {row['exit_by']!r}")
ok(str(row["exit_rule"]) == "",
   "exit_rule ว่าง เพราะ exit_monitor ไม่ได้เป็นคนสั่ง")

journal.log_trade_open("ETHUSDm", "Long", 3000.0, 2900.0, 3300.0, 1.0, 7.0,
                       ticket=555002, strategy="Scoring")
mt5.history_deals_get = lambda **kw: (Deal(mt5.DEAL_REASON_EXPERT, entry=0),
                                      Deal(mt5.DEAL_REASON_EXPERT, entry=1, profit=88.0))
journal.reconcile_closed_positions()
row = journal._load().iloc[1]
ok(row["status"] == "Bot Exit" and row["exit_by"] == "bot",
   "ไม้ที่ EA ปิด -> status=Bot Exit, exit_by=bot",
   f"ได้ {row['status']} / {row['exit_by']!r}")

# ---------------------------------------------------------------------------
print("\n3) log_trade_close เก็บชื่อกฎที่ยิง")
# ---------------------------------------------------------------------------
journal.log_trade_open("XAUUSDm", "Long", 4000.0, 3950.0, 4200.0, 0.05, 9.0,
                       ticket=555003, strategy="Reversal")
journal.log_trade_close(555003, "Bot Exit", -33.0, exit_by="bot",
                        exit_rule="ออก 100% ทันที — Trend/Structure พัง")
row = journal._load().query("ticket == '555003'").iloc[0]
ok(row["exit_rule"] == "ออก 100% ทันที — Trend/Structure พัง",
   "exit_rule ถูกบันทึกครบทั้งประโยค", repr(row["exit_rule"]))

# ---------------------------------------------------------------------------
print("\n4) log_cut — การปิดบางส่วนมีร่องรอยแล้ว")
# ---------------------------------------------------------------------------
m = {
    "ticket": 555003, "symbol": "XAUUSDm", "direction": "Long",
    "entry": 4000.0, "sl": 3950.0, "current_price": 4060.0,
    "r_multiple": 1.2, "base_keep_pct": 100, "stage_keep_pct": 50,
    "entry_time": datetime.now() - timedelta(hours=9),
    "position_rules": [{"name": "ถึง 1R", "trigger": True, "keep_pct": 50},
                       {"name": "แท่ง Climax", "trigger": False, "keep_pct": 50},
                       {"name": "Indicator ร้อน", "trigger": True, "keep_pct": 60}],
    "final_decision": ("ออก 50% (Time exit) — รอ setup ใหม่", ""),
}
journal.log_cut(m, closed_lot=0.02, remaining_lot=0.03)
journal.log_cut(m, closed_lot=0.01, remaining_lot=0.02)

cuts = pd.read_csv(journal.CUTS_FILE)
ok(len(cuts) == 2, "ปิดบางส่วน 2 ครั้ง = 2 แถว (ไม่ทับกัน)", f"ได้ {len(cuts)} แถว")
ok(list(cuts.columns) == journal.CUT_COLUMNS, "คอลัมน์ตรงกับ CUT_COLUMNS")
c = cuts.iloc[0]
ok(c["กฎที่ยิง"] == "ถึง 1R|Indicator ร้อน",
   "บันทึกเฉพาะกฎที่ trigger จริง คั่นด้วย | เหมือน replay_cuts", repr(c["กฎที่ยิง"]))
ok(c["final"] == "ออก 50% (Time exit) — รอ setup ใหม่", "final ตรงกับ final_decision")
ok(abs(c["R_ตอนตัด"] - 1.2) < 1e-9 and c["ตัดไป_lot"] == 0.02 and c["เหลือ_lot"] == 0.03,
   "R / lot ที่ตัด / lot ที่เหลือ ถูกต้อง")
ok(8.5 < c["ชม.ที่ถือมา"] < 9.5, "ชม.ที่ถือมา คำนวณจาก entry_time", str(c["ชม.ที่ถือมา"]))
ok(c["strategy"] == "Reversal", "ดึง strategy จาก trades_log ได้", repr(c["strategy"]))

# ---------------------------------------------------------------------------
print("\n5) ของเดิมต้องไม่พัง")
# ---------------------------------------------------------------------------
ok(journal.check_daily_loss(10_000.0, 0.06) in (True, False),
   "check_daily_loss ยังรันผ่าน status ใหม่ได้")
ok(journal.get_trade_strategy(555003) == "Reversal", "get_trade_strategy ยังทำงาน")
ok(journal.get_original_lot(555003) == 0.05, "get_original_lot ยังทำงาน")
ok(journal.check_cooldown("XAUUSDm", 48)[0] is False,
   "check_cooldown เห็นไม้ที่ปิดด้วย status ใหม่ (Bot Exit) ว่าปิดแล้ว")

# ไฟล์เก่าที่ยังไม่มีคอลัมน์ใหม่ต้องอ่านได้
legacy = os.path.join(TMP, "legacy.csv")
pd.DataFrame([{
    "date": "2026-06-01", "time": "08:00:00", "symbol": "BTCUSDm", "direction": "Short",
    "entry": 60000, "sl": 62000, "tp": 56000, "lot": 0.01, "score": 9.0,
    "ticket": "101", "status": "Manual Cut", "net_pnl": 200.0, "note": "",
    "pinned_swing": "", "pinned_atr_entry": "", "close_date": "2026-06-02",
    "close_time": "09:00:00", "strategy": "Scoring",
}]).to_csv(legacy, index=False)
journal.LOG_FILE = legacy
df = journal._load()
ok("exit_by" in df.columns and "exit_rule" in df.columns,
   "CSV เก่าที่ไม่มีคอลัมน์ใหม่ ถูกเติมให้อัตโนมัติตอนโหลด")
ok(df.iloc[0]["status"] == "Manual Cut",
   "แถวเก่ายังอ่านค่าเดิมได้ (Manual Cut แบบถังรวม — แยกย้อนหลังไม่ได้)")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} ผ่าน")
