"""
test_dashboard.py — unit tests สำหรับ dashboard.py
รัน: pytest test_dashboard.py -v
"""
import pandas as pd
import pytest

from dashboard import (
    calc_max_drawdown,
    calc_streak,
    print_summary,
    print_pnl_table,
    print_monthly,
    print_by_symbol,
    print_open,
    print_equity_curve,
    load,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_closed(*pnl_values, symbol="BTCUSDm", direction="Short") -> pd.DataFrame:
    """สร้าง DataFrame ของ closed trades จาก list ของ P&L"""
    rows = []
    for i, pnl in enumerate(pnl_values):
        rows.append({
            "date":      f"2026-0{(i // 28) + 1}-{(i % 28) + 1:02d}",
            "time":      "08:00:00",
            "symbol":    symbol,
            "direction": direction,
            "entry":     60000.0,
            "sl":        62000.0,
            "tp":        56000.0,
            "lot":       0.01,
            "score":     9.0,
            "ticket":    str(100 + i),
            "status":    "Take Profit" if pnl > 0 else "Stop Loss",
            "net_pnl":   float(pnl),
            "note":      "",
        })
    return pd.DataFrame(rows)


def make_open(n=1) -> pd.DataFrame:
    rows = [
        {
            "date": "2026-06-29", "time": "10:00:00",
            "symbol": "BTCUSDm", "direction": "Short",
            "entry": 59000.0, "sl": 62000.0, "tp": 55000.0,
            "lot": 0.01, "score": 9.5,
            "ticket": str(999 + i), "status": "Open",
            "net_pnl": "", "note": "",
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# calc_max_drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_all_wins(self):
        pnl = pd.Series([100.0, 200.0, 150.0])
        assert calc_max_drawdown(pnl) == 0.0

    def test_all_losses(self):
        # cumsum=[-100,-150,-350], cummax=[-100,-100,-100], drawdown=[0,-50,-250]
        pnl = pd.Series([-100.0, -50.0, -200.0])
        assert calc_max_drawdown(pnl) == pytest.approx(-250.0)

    def test_mixed(self):
        # equity: 100 → 200 → 50 → 150  drawdown ที่ลึกสุด = 200 → 50 = -150
        pnl = pd.Series([100.0, 100.0, -150.0, 100.0])
        assert calc_max_drawdown(pnl) == pytest.approx(-150.0)

    def test_single_trade_win(self):
        assert calc_max_drawdown(pd.Series([500.0])) == 0.0

    def test_single_trade_loss(self):
        # cumsum=[-300], cummax=[-300], drawdown=[0] — ไม่มี peak ก่อนหน้าให้วัด
        assert calc_max_drawdown(pd.Series([-300.0])) == pytest.approx(0.0)

    def test_recover_after_drawdown(self):
        pnl = pd.Series([100.0, -300.0, 500.0])
        assert calc_max_drawdown(pnl) == pytest.approx(-300.0)


# ---------------------------------------------------------------------------
# calc_streak
# ---------------------------------------------------------------------------

class TestStreak:
    def test_all_wins(self):
        pnl = pd.Series([10.0, 20.0, 5.0])
        w, l = calc_streak(pnl)
        assert w == 3 and l == 0

    def test_all_losses(self):
        pnl = pd.Series([-10.0, -20.0, -5.0])
        w, l = calc_streak(pnl)
        assert w == 0 and l == 3

    def test_alternating(self):
        pnl = pd.Series([10.0, -10.0, 10.0, -10.0])
        w, l = calc_streak(pnl)
        assert w == 1 and l == 1

    def test_win_streak_in_middle(self):
        pnl = pd.Series([-10.0, 5.0, 5.0, 5.0, -10.0])
        w, l = calc_streak(pnl)
        assert w == 3 and l == 1

    def test_loss_streak_at_end(self):
        pnl = pd.Series([10.0, -5.0, -5.0, -5.0, -5.0])
        w, l = calc_streak(pnl)
        assert w == 1 and l == 4

    def test_empty(self):
        w, l = calc_streak(pd.Series([], dtype=float))
        assert w == 0 and l == 0


# ---------------------------------------------------------------------------
# print_summary — เช็คว่าไม่ crash และ output มี keyword สำคัญ
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_no_crash_with_data(self, capsys):
        closed = make_closed(100.0, -50.0, 200.0, -30.0)
        print_summary(closed)
        out = capsys.readouterr().out
        assert "Win Rate" in out
        assert "Total P&L" in out
        assert "Max Drawdown" in out
        assert "Profit Factor" in out

    def test_no_crash_empty(self, capsys):
        print_summary(pd.DataFrame())
        out = capsys.readouterr().out
        assert "ยังไม่มี" in out

    def test_win_rate_100_percent(self, capsys):
        closed = make_closed(100.0, 200.0, 50.0)
        print_summary(closed)
        out = capsys.readouterr().out
        assert "100.0%" in out

    def test_all_losses(self, capsys):
        closed = make_closed(-100.0, -200.0)
        print_summary(closed)
        out = capsys.readouterr().out
        assert "0.0%" in out

    def test_single_trade_win(self, capsys):
        closed = make_closed(150.0)
        print_summary(closed)
        out = capsys.readouterr().out
        assert "WIN" in out.upper() or "100.0%" in out


# ---------------------------------------------------------------------------
# print_pnl_table
# ---------------------------------------------------------------------------

class TestPrintPnlTable:
    def test_shows_each_trade(self, capsys):
        closed = make_closed(100.0, -50.0, 200.0)
        print_pnl_table(closed)
        out = capsys.readouterr().out
        assert out.count("BTCUSDm") == 3

    def test_win_loss_labels(self, capsys):
        closed = make_closed(100.0, -50.0)
        print_pnl_table(closed)
        out = capsys.readouterr().out
        assert "WIN" in out
        assert "LOSS" in out

    def test_empty_no_crash(self, capsys):
        print_pnl_table(pd.DataFrame())
        # ไม่ควร crash


# ---------------------------------------------------------------------------
# print_monthly
# ---------------------------------------------------------------------------

class TestPrintMonthly:
    def test_groups_by_month(self, capsys):
        closed = make_closed(100.0, -50.0, 200.0)
        # วันที่ใน fixture อยู่ month เดียวกัน
        print_monthly(closed)
        out = capsys.readouterr().out
        assert "2026" in out

    def test_empty_no_crash(self, capsys):
        print_monthly(pd.DataFrame())


# ---------------------------------------------------------------------------
# print_by_symbol
# ---------------------------------------------------------------------------

class TestPrintBySymbol:
    def test_multi_symbol(self, capsys):
        btc = make_closed(100.0, -50.0, symbol="BTCUSDm")
        eth = make_closed(200.0, symbol="ETHUSDm")
        closed = pd.concat([btc, eth], ignore_index=True)
        print_by_symbol(closed)
        out = capsys.readouterr().out
        assert "BTCUSDm" in out
        assert "ETHUSDm" in out

    def test_single_symbol_skips(self, capsys):
        closed = make_closed(100.0, -50.0)
        print_by_symbol(closed)
        # single symbol → ไม่แสดง section นี้
        out = capsys.readouterr().out
        assert "BY SYMBOL" not in out

    def test_empty_no_crash(self, capsys):
        print_by_symbol(pd.DataFrame())


# ---------------------------------------------------------------------------
# print_open
# ---------------------------------------------------------------------------

class TestPrintOpen:
    def test_shows_open_positions(self, capsys):
        open_pos = make_open(2)
        print_open(open_pos)
        out = capsys.readouterr().out
        assert "OPEN POSITIONS" in out
        assert "BTCUSDm" in out

    def test_empty_no_crash(self, capsys):
        print_open(pd.DataFrame())
        out = capsys.readouterr().out
        assert out == ""


# ---------------------------------------------------------------------------
# print_equity_curve
# ---------------------------------------------------------------------------

class TestEquityCurve:
    def test_shows_curve(self, capsys):
        closed = make_closed(100.0, 200.0, -50.0, 150.0)
        print_equity_curve(closed)
        out = capsys.readouterr().out
        assert "EQUITY CURVE" in out

    def test_single_trade_no_curve(self, capsys):
        closed = make_closed(100.0)
        print_equity_curve(closed)
        out = capsys.readouterr().out
        assert out == ""

    def test_empty_no_crash(self, capsys):
        print_equity_curve(pd.DataFrame())


# ---------------------------------------------------------------------------
# load — ทดสอบด้วย temp CSV
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_filters_symbol(self, tmp_path, monkeypatch):
        csv = tmp_path / "trades_log.csv"
        csv.write_text(
            "date,time,symbol,direction,entry,sl,tp,lot,score,ticket,status,net_pnl,note\n"
            "2026-06-01,08:00:00,BTCUSDm,Short,60000,62000,56000,0.01,9.0,101,Take Profit,200.0,\n"
            "2026-06-02,08:00:00,ETHUSDm,Long,3000,2900,3200,0.1,8.5,102,Stop Loss,-50.0,\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("dashboard.LOG_FILE", str(csv))
        _, closed = load(symbol="BTCUSDm")
        assert len(closed) == 1
        assert closed.iloc[0]["symbol"] == "BTCUSDm"

    def test_load_date_range(self, tmp_path, monkeypatch):
        csv = tmp_path / "trades_log.csv"
        csv.write_text(
            "date,time,symbol,direction,entry,sl,tp,lot,score,ticket,status,net_pnl,note\n"
            "2026-01-15,08:00:00,BTCUSDm,Short,60000,62000,56000,0.01,9.0,101,Take Profit,200.0,\n"
            "2026-06-15,08:00:00,BTCUSDm,Short,59000,61000,55000,0.01,9.0,102,Stop Loss,-50.0,\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("dashboard.LOG_FILE", str(csv))
        _, closed = load(date_from="2026-06-01")
        assert len(closed) == 1
        assert closed.iloc[0]["ticket"] == "102"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dashboard.LOG_FILE", str(tmp_path / "nonexistent.csv"))
        open_pos, closed = load()
        assert open_pos.empty
        assert closed.empty

    def test_load_separates_open_closed(self, tmp_path, monkeypatch):
        csv = tmp_path / "trades_log.csv"
        csv.write_text(
            "date,time,symbol,direction,entry,sl,tp,lot,score,ticket,status,net_pnl,note\n"
            "2026-06-01,08:00:00,BTCUSDm,Short,60000,62000,56000,0.01,9.0,101,Take Profit,200.0,\n"
            "2026-06-02,08:00:00,BTCUSDm,Short,59000,61000,55000,0.01,9.0,102,Open,,\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("dashboard.LOG_FILE", str(csv))
        open_pos, closed = load()
        assert len(open_pos) == 1
        assert len(closed) == 1
