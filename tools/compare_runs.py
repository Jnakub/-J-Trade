"""compare_runs.py — เทียบผล replay สองชุด (control vs ทดลอง) ตามกติกาข้อ 2-3 ของ CLAUDE.md
ใช้: python3 tools/compare_runs.py <tag_control> <tag_ทดลอง>
     เช่น python3 tools/compare_runs.py c1020p1016_reva_mkt c1022P1016_reva_pbdis4
อ่าน results/replay_trades_<sym>_<tag>.csv ของทุก symbol ใน config.SYMBOLS (ไม่ glob)
พิมพ์: ยอดรวม · แยกกลยุทธ์ · direct/churn พร้อม SE และ |t| · ตัด 5 รายการใหญ่ · นับ symbol ที่ดีขึ้น
(ย้ายมาจากสคริปต์ scratchpad cmp2.py 2026-10-22 · ไม่ต่อ MT5 รันด้วย python3 ได้)"""
import math
import os
import re
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
_cfg = open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
SYMS = re.findall(r'"(\w+)"', re.search(r"^SYMBOLS\s*=\s*\[(.*?)\]", _cfg, re.S | re.M).group(1))


def load(tag):
    out = []
    for s in SYMS:
        df = pd.read_csv(os.path.join(RES, f"replay_trades_{s}_{tag}.csv"), parse_dates=["time", "exit_time"])
        df["sym"] = s
        out.append(df)
    return pd.concat(out, ignore_index=True)


ctag, ttag = sys.argv[1], sys.argv[2]
a, b = load(ctag), load(ttag)
key = ["sym", "time", "direction"]
m = a.merge(b, on=key, how="outer", suffixes=("_a", "_b"), indicator=True)
both = m[m._merge == "both"]
lost, new = m[m._merge == "left_only"], m[m._merge == "right_only"]
chg = both[(both.R_a - both.R_b).abs() > 1e-3]
sd = pd.concat([a.R, b.R]).std()
print(f"=== control {len(a)} ไม้ {a.R.sum():+.2f}R  ->  ทดลอง {len(b)} ไม้ {b.R.sum():+.2f}R  ΔR {b.R.sum()-a.R.sum():+.2f}")
for nm, df in (("control", a), ("ทดลอง", b)):
    g = df.groupby("strategy").R.agg(["count", "sum", "mean"])
    print(f"  {nm}: " + " · ".join(f"{k} {int(r['count'])} {r['sum']:+.2f} ({r['mean']:+.2f}/ไม้)" for k, r in g.iterrows()))
d = (chg.R_b - chg.R_a)
se_d = d.std() * math.sqrt(len(d)) if len(d) > 1 else float("nan")
print(f"  direct: เปลี่ยน {len(chg)} ไม้ Σ{d.sum():+.2f}R  SE {se_d:.2f}  |t| {abs(d.sum())/se_d if se_d else float('nan'):.2f}")
churn = new.R_b.sum() - lost.R_a.sum()
se_c = sd * math.sqrt(len(new) + len(lost))
print(f"  churn: ใหม่ {len(new)} ไม้ {new.R_b.sum():+.2f}R · หาย {len(lost)} ไม้ {lost.R_a.sum():+.2f}R -> {churn:+.2f}R"
      f"  SE {se_c:.2f}  |t| {abs(churn)/se_c if se_c else float('nan'):.2f}")
print("  ไม้ใหม่ตามกลยุทธ์:", new.groupby("strategy_b").R_b.agg(["count", "sum", "mean"]).round(2).to_dict("index"))
print("  ไม้หายตามกลยุทธ์:", lost.groupby("strategy_a").R_a.agg(["count", "sum", "mean"]).round(2).to_dict("index"))
items = pd.concat([new.R_b, -lost.R_a, d])
top = items.abs().sort_values(ascending=False).index[:5]
tot = b.R.sum() - a.R.sum()
print(f"  ตัด 5 รายการใหญ่ ({items.loc[top].sum():+.2f}) เหลือ {tot - items.loc[top].sum():+.2f}R")
per = {s: b[b.sym == s].R.sum() - a[a.sym == s].R.sum() for s in SYMS}
print(f"  ดีขึ้น {sum(v > 1e-9 for v in per.values())}/{len(SYMS)} · เท่าเดิม {sum(abs(v) <= 1e-9 for v in per.values())} : "
      + " ".join(f"{s[:-1]} {v:+.2f}" for s, v in per.items()))
