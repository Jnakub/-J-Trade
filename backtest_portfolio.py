"""backtest_portfolio.py — วัดเพดานความเสี่ยงระดับพอร์ต

backtest_replay.py เดินทีละ symbol ตามด่านจริงชั่วโมงต่อชั่วโมง ซึ่งถูกต้องสำหรับทุกคำถาม
ที่อยู่ใน symbol เดียว แต่มองไม่เห็นอย่างเดียว: ตอนนั้นอีก 6 symbol ถือไม้อะไรอยู่
ไฟล์นี้เอา output ของ replay ทั้ง 7 ตัวมาต่อเป็นเส้นเวลาเดียว แล้วถามว่าเพดานความเสี่ยง
รวมระดับพอร์ตจะบล็อกไม้ไหนทิ้งบ้าง และราคาของมันกี่ R

    python3 backtest_portfolio.py                    # ตารางเปรียบเทียบเพดานทุกระดับ
    python3 backtest_portfolio.py --cap=3 --group=2  # ดูค่าที่ตั้งไว้จริงใน config
    python3 backtest_portfolio.py --daily            # MAX_DAILY_LOSS ระดับพอร์ต (replay เห็นแค่ต่อ symbol)

⚠️ **ตัวเลข ΔR ที่ได้คือขอบบนของต้นทุน ไม่ใช่ต้นทุนจริง**
ไฟล์นี้ไม่ได้ replay ใหม่ — มันหยิบไม้ที่ replay รายตัวสร้างไว้แล้วมาคัดออก ไม้ที่ถูก
บล็อกจึงหายไปเฉยๆ ของจริงช่องของ symbol นั้นจะว่างและมีสิทธิ์รับไม้ถัดไปแทน ซึ่งเป็นไม้
ที่ replay รายตัวไม่เคยสร้างให้ดู ต้นทุนจริงจึงต่ำกว่าที่รายงานตรงนี้เสมอ
ใช้ตัดสินใจได้เฉพาะคำถาม "เพดานนี้แพงเกินไปไหม" (ถ้าขอบบนยังถูก ของจริงก็ถูก)
ห้ามใช้ตอบ "เพดานไหนให้ R สูงสุด" — ดู memory เรื่องผลต่าง 2-4R คือ noise

ต้องรัน backtest_replay.py --log-cuts ให้ครบทุก symbol ก่อน เพราะความเสี่ยงที่ยังมีชีวิต
ของแต่ละไม้สร้างจาก replay_cuts_*.csv (ปิดบางส่วน) + คอลัมน์ be_time ของ replay_trades_*.csv
(ไม้ที่ขยับ SL ไป breakeven แล้ว = เสี่ยง 0 · คอลัมน์นี้มีตั้งแต่ 2026-09-28 ไฟล์ที่เก่ากว่าจะถูกเตือน)
"""
import csv
import sys
import datetime
from collections import defaultdict

import exit_monitor as em
from config import (SYMBOLS, RISK_PER_TRADE, MAX_PORTFOLIO_RISK_R,
                    MAX_GROUP_RISK_R, CORRELATION_GROUPS, MAX_DAILY_LOSS)

P = datetime.datetime.fromisoformat
# อ่านจาก exit_monitor ไม่ hardcode — สตริงนี้เคยถูก rename แล้วที่นี่ไม่รู้ตัว ผลคือไม้ที่ล็อก
# BE แล้วถูกนับว่ายังมีความเสี่ยงเต็ม (เพดานความเสี่ยงพอร์ตจึงเข้มกว่าจริง) โดยไม่มี error ใดๆ
# "ขยับ SL ไปจุด Entry" = ข้อความเวอร์ชันก่อน 2026-09-15 ที่ยังค้างในไฟล์ replay_cuts_*.csv เก่า
BE_PREFIXES = (em.BE_DECISION_PREFIX, "ขยับ SL ไปจุด Entry")


def load():
    """คืน list ของไม้ พร้อม sched = [(เวลา, ความเสี่ยงที่เหลือเป็น R), ...]"""
    trades = []
    _no_be_col = set()     # symbol ที่ไฟล์ replay ยังไม่มีคอลัมน์ be_time (รุ่นก่อน 2026-09-28)
    for sym in SYMBOLS:
        try:
            fh = open(f"replay_trades_{sym}.csv")
        except FileNotFoundError:
            sys.exit(f"ไม่มี replay_trades_{sym}.csv — รัน backtest_replay.py {sym} ก่อน")
        for row in csv.DictReader(fh):
            if not row.get("exit_time"):
                continue        # ไม้ที่ยังค้างตอน replay จบ — ไม่รู้ผล ตัดทิ้ง
            try:
                r = float(row["R"])
            except ValueError:
                continue
            if "be_time" not in row:
                _no_be_col.add(sym)
            trades.append(dict(sym=sym, t_in=P(row["time"]), t_out=P(row["exit_time"]),
                               R=r, strategy=row["strategy"], key=row["time"],
                               grp=CORRELATION_GROUPS.get(sym, sym),
                               be=P(row["be_time"]) if row.get("be_time") else None))

    cuts = defaultdict(list)
    for sym in SYMBOLS:
        try:
            fh = open(f"replay_cuts_{sym}.csv")
        except FileNotFoundError:
            print(f"  ! ไม่มี replay_cuts_{sym}.csv — ไม้ของ {sym} จะถูกคิดเป็น 1R เต็ม"
                  f" ตลอดอายุไม้ (รัน replay ด้วย --log-cuts เพื่อให้แม่นขึ้น)")
            continue
        for row in csv.DictReader(fh):
            cuts[(sym, row["entry_time"])].append(row)

    if _no_be_col:
        print(f"  ! replay_trades ของ {', '.join(sorted(_no_be_col))} ไม่มีคอลัมน์ be_time (ไฟล์ก่อน "
              f"2026-09-28) — ไม้ที่ล็อก SL ที่ทุนแล้วจะถูกนับว่ายังเสี่ยงเต็มจนปิด · รัน replay ใหม่ก่อน")

    for t in trades:
        # เหตุการณ์ของไม้เรียงตามเวลา: ปิดบางส่วน (rem ลด) กับ SL ถึงทุน (ความเสี่ยงเหลือ 0 จนปิด)
        # 🔴 2026-09-28: เดิมรู้ว่าไม้ล็อกทุนจาก final ของแถว cut เท่านั้น ซึ่งเกิดพร้อมการปิดบางส่วน
        # พอเลิกปิดบางส่วน แถวพวกนั้นก็หาย — base 176 ไม้มีไม้ที่ SL เคยถึงทุน 62 ไม้ แต่มีแถว cut ที่
        # บอก BE แค่ 4 แถว (ตอนชนเพดาน 30 วัน) = ไม้ที่ล็อกทุนแล้วถูกนับว่ายังเสี่ยงเต็มจนปิด
        # ตอนนี้ใช้ be_time ที่ replay บันทึกตรงจากจังหวะที่ SL ถึง entry (backtest_replay.step_position)
        events = [(P(c["cut_time"]), "cut", c) for c in cuts[(t["sym"], t["key"])]]
        if t["be"] is not None:
            events.append((t["be"], "be", None))
        sched, rem, be = [(t["t_in"], 1.0)], 1.0, False
        for when, kind, c in sorted(events, key=lambda e: e[0]):
            if kind == "be":
                be = True
            else:
                try:
                    rem = float(c["เหลือ"])     # สัดส่วนที่เหลือหลังปิดบางส่วน
                except (ValueError, KeyError):
                    pass
                if c["final"].startswith(BE_PREFIXES):   # ไฟล์รุ่นเก่าที่ยังไม่มี be_time
                    be = True
            sched.append((when, 0.0 if be else rem))
        sched.append((t["t_out"], 0.0))
        t["sched"] = sched
    return trades


def risk_of(t, now):
    cur = 0.0
    for when, risk in t["sched"]:
        if when > now:
            break
        cur = risk
    return cur


def run(trades, cap_r=None, cap_n=None, grp_r=None, daily_r=None):
    """daily_r = MAX_DAILY_LOSS เป็นหน่วย R — net R ของไม้ที่ **ปิดไปแล้ววันเดียวกัน**
    รวมทุก symbol ถึง −daily_r เมื่อไหร่ ห้ามเปิดไม้ใหม่ทั้งวัน (แบบเดียวกับ scheduler)
    ⚠️ ขอบวันใช้วันที่ของเวลาปิดในไฟล์ replay ส่วนระบบจริงใช้ date.today() ของเครื่อง
    ถ้า timezone ต่างกัน ไม้ที่ปิดใกล้เที่ยงคืนจะตกคนละวัน"""
    taken, blocked, opens = [], [], []
    for t in sorted(trades, key=lambda t: t["t_in"]):
        now = t["t_in"]
        opens = [p for p in opens if p["t_out"] > now]
        if daily_r is not None:
            day_net = sum(p["R"] for p in taken
                          if p["t_out"] <= now and p["t_out"].date() == now.date())
            if day_net <= -daily_r + 1e-9:
                blocked.append(t)
                continue
        risks = [(p, risk_of(p, now)) for p in opens]
        if cap_n is not None and len(opens) + 1 > cap_n:
            blocked.append(t)
            continue
        if cap_r is not None and sum(r for _, r in risks) + 1.0 > cap_r + 1e-9:
            blocked.append(t)
            continue
        if grp_r is not None:
            g = sum(r for p, r in risks if p["grp"] == t["grp"])
            if g + 1.0 > grp_r + 1e-9:
                blocked.append(t)
                continue
        taken.append(t)
        opens.append(t)
    return taken, blocked


def peak_risk(trades):
    """ความเสี่ยงรวมสูงสุดที่เคยเปิดค้างพร้อมกัน + สัดส่วนเวลาที่อยู่เหนือแต่ละระดับ"""
    marks = sorted({w for t in trades for w, _ in t["sched"]})
    peak, above = 0.0, defaultdict(float)
    for i, now in enumerate(marks[:-1]):
        span = (marks[i + 1] - now).total_seconds() / 3600
        tot = sum(risk_of(t, now) for t in trades
                  if t["t_in"] <= now < t["t_out"])
        peak = max(peak, tot)
        for lvl in (1, 2, 3, 4):
            if tot >= lvl:
                above[lvl] += span
    total_h = (marks[-1] - marks[0]).total_seconds() / 3600
    return peak, {k: v / total_h * 100 for k, v in above.items()}


def main():
    trades = load()
    base = sum(t["R"] for t in trades)
    span = f'{min(t["t_in"] for t in trades):%Y-%m-%d} → {max(t["t_out"] for t in trades):%Y-%m-%d}'
    print(f"\n{len(trades)} ไม้  {span}  TotalR {base:+.2f}  "
          f"(1R = RISK_PER_TRADE {RISK_PER_TRADE*100:.0f}%)")

    peak, above = peak_risk(trades)
    print(f"\nความเสี่ยงรวมที่เปิดค้างพร้อมกัน — สูงสุดที่เคยแตะ {peak:.2f}R "
          f"({peak*RISK_PER_TRADE*100:.0f}% ของพอร์ต)")
    for lvl, pct in sorted(above.items()):
        print(f"  อยู่เหนือ {lvl}.0R  {pct:5.1f}% ของเวลา")

    def show(label, taken, blocked):
        tr = sum(t["R"] for t in taken)
        print(f"  {label:34}{len(taken):>4} ไม้  บล็อก {len(blocked):>3}   "
              f"TotalR {tr:>7.2f}   ΔR {tr - base:>+6.2f}")

    args = [a for a in sys.argv[1:] if a.startswith("--")]
    cap = next((float(a.split("=")[1]) for a in args if a.startswith("--cap=")), None)
    grp = next((float(a.split("=")[1]) for a in args if a.startswith("--group=")), None)

    if "--daily" in args:
        daily_r = MAX_DAILY_LOSS / RISK_PER_TRADE
        print(f"\n── MAX_DAILY_LOSS {MAX_DAILY_LOSS*100:g}% = {daily_r:g}R "
              f"(net ของไม้ที่ปิดวันนั้น รวมทุก symbol) + group {MAX_GROUP_RISK_R}R ──")
        for c in sorted({3.0, MAX_PORTFOLIO_RISK_R}):
            taken, _ = run(trades, cap_r=c, grp_r=MAX_GROUP_RISK_R)
            show(f"cap {c:g}R ไม่มี daily", taken, _)
            taken_d, blocked_d = run(trades, cap_r=c, grp_r=MAX_GROUP_RISK_R,
                                     daily_r=daily_r)
            show(f"cap {c:g}R + daily", taken_d, blocked_d)
            byday = defaultdict(float)
            for t in taken:
                byday[t["t_out"].date()] += t["R"]
            worst = min(byday.values())
            extra = [t for t in taken if t not in taken_d]
            print(f"    วันที่แย่สุด {worst:+.2f}R ({worst*RISK_PER_TRADE*100:+.1f}%) · "
                  f"วัน ≤ −{daily_r:g}R: {sum(v <= -daily_r + 1e-9 for v in byday.values())} · "
                  f"วัน ≤ −2R: {sum(v <= -2 + 1e-9 for v in byday.values())}"
                  + (f" · daily ตัดไม้ {len(extra)} ไม้ ΣR {sum(t['R'] for t in extra):+.2f}"
                     if extra else ""))
        print()
        return

    if cap is not None or grp is not None:
        print(f"\n── cap={cap}R  group={grp}R ──")
        show("ผลรวม", *run(trades, cap_r=cap, grp_r=grp))
        return

    print("\n── เพดานความเสี่ยงรวม (ไม้ที่ BE แล้วนับเป็น 0R) ──")
    for c in (2.0, 2.5, 3.0, 3.5, 4.0):
        show(f"cap {c}R = {c*RISK_PER_TRADE*100:.0f}% ของพอร์ต", *run(trades, cap_r=c))

    print("\n── เทียบกับเพดานที่นับหัวไม้ดิบ (ทำไมถึงไม่ใช้) ──")
    for n in (3, 4, 5, 6):
        show(f"max {n} ไม้พร้อมกัน", *run(trades, cap_n=n))

    print(f"\n── เพดานต่อกลุ่มที่วิ่งด้วยกัน {sorted(set(CORRELATION_GROUPS.values()))} ──")
    for g in (1.0, 1.5, 2.0):
        show(f"group cap {g}R", *run(trades, grp_r=g))

    print(f"\n── ค่าที่ config ตั้งไว้จริง: {MAX_PORTFOLIO_RISK_R}R "
          f"+ group {MAX_GROUP_RISK_R}R ──")
    show("ผลรวมสองด่าน", *run(trades, cap_r=MAX_PORTFOLIO_RISK_R,
                              grp_r=MAX_GROUP_RISK_R))
    print()


if __name__ == "__main__":
    main()
