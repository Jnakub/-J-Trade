"""sideway.py — กลยุทธ์ที่ 4: เทรดกรอบตอน ADX 4H ต่ำ (เข้าระบบ 2026-10-02 · คำสั่งผู้ใช้)

บ้านเดียวของกฎเข้าไม้ Sideway — scheduler.scan_symbol และ backtest_replay เรียกฟังก์ชันเดียวกันนี้
ส่วนกฎออก (ไม่มี ATR trailing / TP trailing · ออกเมื่อ ADX ≥ 22 · เพดาน 120 ชม.) อยู่ที่ exit_monitor
ค่าคงที่ทั้งหมดและที่มา/ผลทดสอบ (รวมถึงที่ **ตกการทดสอบนอกข้อมูล**) อยู่ที่ config.SIDEWAY_*

กฎ (ทุกข้อตรงกับสคริปต์คัดกรองที่วัดไว้):
  1. ADX 4H < regime_check.ADX_CHOPPY ติดกัน ≥ SIDEWAY_MIN_RUN_BARS แท่ง (แท่ง 4H ที่ปิดแล้ว)
     และ ADX แท่งปิดล่าสุด ≥ SIDEWAY_MIN_ENTRY_ADX (2026-10-08 · ตลาดนิ่งสนิทเกินไม่เข้า)
  2. กรอบ = high P90 / low P10 ของแท่ง 4H ทั้งช่วง sideway · กว้าง ≥ SIDEWAY_MIN_WIDTH_PCT % ของราคา
  3. Long: ราคาอยู่ใน SIDEWAY_ZONE ล่างของกรอบ + structure (slope regression ราคาปิด 4H ย้อน
     STRUCT_REG_N แท่ง) ขึ้น + แท่ง 1H ปิดล่าสุดเป็นแท่งเขียวและปิดสูงกว่าแท่งก่อน (Short กลับด้าน)
  4. TP = กลางกรอบ · SL = เลยขอบ SIDEWAY_SL_EXT ของกว้าง · SL ใกล้กว่า SIDEWAY_MIN_SL_ATR × ATR1H(14) = ไม่เข้า

⚠️ ATR1H ที่นี่ = ค่าเฉลี่ยธรรมดา (rolling mean) ของ True Range 14 แท่ง ตรงกับสคริปต์ที่วัดมา
   ไม่ใช่ calc_atr ของ swing.py — เปลี่ยนแล้วด่าน SL ขั้นต่ำจะคัดไม้คนละชุดกับที่วัดไว้
"""
import numpy as np
import pandas as pd

import config
import regime_check as rc
from scoring import get_ohlcv
from indicators import calc_atr
from config import MT5_TIMEFRAMES

# แท่ง 4H ที่ดึงมาคำนวณ ADX + หาจุดเริ่มช่วง sideway — ADX เป็น EWM ต้องมีแท่งนำหน้าพอ และช่วง
# sideway ยาวได้หลายสิบแท่ง (median ~40) จึงดึงเผื่อมากกว่า regime_check.BARS (210)
SIDEWAY_4H_BARS = 600
SIDEWAY_1H_BARS = 40
_H4 = pd.Timedelta(hours=4)
_H1 = pd.Timedelta(hours=1)


def _now(as_of):
    if as_of is not None:
        return pd.Timestamp(as_of)
    from mt5_connect import mt5_now          # import ช้า: โมดูลนี้ถูก import จากเทสต์ที่ไม่มี MT5 สด
    return pd.Timestamp(mt5_now())


def adx_run_below(adx: np.ndarray, idx: int, level: float) -> int:
    """ADX < level ติดกันมากี่แท่ง นับถอยหลังจาก idx (รวม idx)"""
    run = 0
    while idx - run >= 0 and adx[idx - run] < level:
        run += 1
    return run


def compute_sideway_entry(symbol: str, entry: float, as_of=None) -> dict:
    """คืน dict ของไม้ Sideway ถ้าผ่านทุกด่าน ไม่งั้น raise ValueError พร้อมเหตุผล
    คีย์หน้าตาเดียวกับ scoring.compute_entry ที่ scheduler อ่าน (sl / exec_sl / tp / rr / atr_entry)
    + direction (ทิศมาจากตำแหน่งราคาในกรอบ ไม่ได้มาจาก bias 1D)"""
    now = _now(as_of)

    d4 = rc.get_adx_bars(symbol, bars=SIDEWAY_4H_BARS, as_of=as_of)
    d4 = d4[pd.to_datetime(d4["time"]) + _H4 <= now].reset_index(drop=True)   # แท่ง 4H ที่ปิดแล้วเท่านั้น
    if len(d4) < 100:
        raise ValueError("Sideway: ข้อมูล 4H ไม่พอ")
    adx = rc.calc_adx(d4, rc.ADX_PERIOD).to_numpy()
    k = len(d4) - 1
    if config.SIDEWAY_DETECT == "r2":                   # หาช่วง sideway ด้วย R² แทน ADX (ดู config.SIDEWAY_DETECT)
        _w = config.SIDEWAY_R2_WINDOW
        _y = d4["close"].to_numpy(dtype=float)[k - _w + 1:k + 1]
        _x = np.arange(_w, dtype=float)
        _b, _a = np.polyfit(_x, _y, 1)
        _tot = ((_y - _y.mean()) ** 2).sum()
        _r2 = 1 - ((_y - (_b * _x + _a)) ** 2).sum() / _tot if _tot > 0 else 1.0
        if _r2 >= config.SIDEWAY_R2_MAX:
            raise ValueError(f"Sideway: R² {_w} แท่ง 4H = {_r2:.2f} ≥ {config.SIDEWAY_R2_MAX:g} (มีเทรนด์)")
        run = _w
    else:
        run = adx_run_below(adx, k, rc.ADX_CHOPPY)
    if config.SIDEWAY_DETECT != "r2" and run < config.SIDEWAY_MIN_RUN_BARS:
        _need = config.SIDEWAY_MIN_RUN_BARS
        raise ValueError(f"Sideway: ADX 4H < {rc.ADX_CHOPPY} ติดกันแค่ {run} แท่ง 4H (~{run * 4 / 24:.1f} วัน) "
                         f"ต้อง ≥ {_need} แท่ง (~{_need * 4 / 24:.1f} วัน) · อีก {_need - run} แท่ง "
                         f"(~{(_need - run) * 4 / 24:.1f} วัน) ถ้า ADX ยังต่ำต่อเนื่อง")
    if config.SIDEWAY_DETECT != "r2" and adx[k] < config.SIDEWAY_MIN_ENTRY_ADX:
        # ตลาดนิ่งสนิทเกิน — ไม่มีการไหลให้ราคากลับมาหาค่ากลาง (เหตุผล/ผลทดสอบที่ config.SIDEWAY_MIN_ENTRY_ADX)
        raise ValueError(f"Sideway: ADX 4H {adx[k]:.1f} < {config.SIDEWAY_MIN_ENTRY_ADX:g} "
                         f"(ตลาดนิ่งเกิน — กรอบแบบนี้แพ้มากสุดใน backtest)")
    k0 = k - run + 1
    hi, lo, cl = (d4[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    rh = float(np.percentile(hi[k0:k + 1], config.SIDEWAY_EDGE_PCTL))
    rl = float(np.percentile(lo[k0:k + 1], 100 - config.SIDEWAY_EDGE_PCTL))
    w = rh - rl
    if w <= 0:
        raise ValueError("Sideway: กรอบกว้าง 0")
    if config.SIDEWAY_MIN_WIDTH_ATR:                 # เทียบความเหวี่ยงของ symbol เอง (แทนเกณฑ์ % · ดู config)
        _atr4 = float(calc_atr(d4, 14).iloc[k])
        if w < config.SIDEWAY_MIN_WIDTH_ATR * _atr4:
            raise ValueError(f"Sideway: กรอบแคบ {w / _atr4:.2f} ATR 4H < {config.SIDEWAY_MIN_WIDTH_ATR:g}")
    elif w / entry * 100 < config.SIDEWAY_MIN_WIDTH_PCT:
        raise ValueError(f"Sideway: กรอบแคบ {w / entry * 100:.2f}% < {config.SIDEWAY_MIN_WIDTH_PCT:g}% ของราคา")

    pos = (entry - rl) / w
    if pos < 0 or pos > 1:
        raise ValueError(f"Sideway: ราคาอยู่นอกกรอบ ({pos:+.2f})")
    _zone = 0.5 if config.SIDEWAY_ENTRY_WICK_ATR else config.SIDEWAY_ZONE   # แบบไส้: แท่งไส้ปิดกลับขึ้นมาได้ไกลกว่าโซน
    if pos < _zone:
        direction = "Long"
    elif pos > 1 - _zone:
        direction = "Short"
    else:
        raise ValueError(f"Sideway: ราคาอยู่กลางกรอบ ({pos:.2f}) ไม่อยู่ในโซน {config.SIDEWAY_ZONE:g} จากขอบ")
    d = 1 if direction == "Long" else -1

    n = rc.STRUCT_REG_N.get(symbol, rc.STRUCT_REG_N_DEFAULT)
    if k + 1 < n:
        raise ValueError("Sideway: ข้อมูล 4H ไม่พอสำหรับ structure")
    struct = int(np.sign(np.polyfit(np.arange(n), cl[k - n + 1:k + 1], 1)[0]))
    if struct != d:
        raise ValueError(f"Sideway: structure {'ขึ้น' if struct > 0 else 'ลง'} สวนทิศ {direction}")

    h1 = get_ohlcv(symbol, MT5_TIMEFRAMES["1H"], bars=SIDEWAY_1H_BARS, as_of=as_of)
    h1 = h1[pd.to_datetime(h1["time"]) + _H1 <= now].reset_index(drop=True)  # แท่ง 1H ที่ปิดแล้วเท่านั้น
    if len(h1) < 16:
        raise ValueError("Sideway: ข้อมูล 1H ไม่พอ")
    o, h, l, c = (h1[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr_1h = float(pd.Series(tr).rolling(14).mean().iloc[-1])
    if config.SIDEWAY_ENTRY_WICK_ATR:
        # แบบไส้ (ดู config.SIDEWAY_ENTRY_WICK_ATR): แท่ง 1H ปิดล่าสุดแทงทะลุขอบกรอบแล้วปิดกลับเข้ากรอบ ไส้ยาว ≥ K ATR1H
        _wick = (min(o[-1], c[-1]) - l[-1]) if d == 1 else (h[-1] - max(o[-1], c[-1]))
        _pierce = (l[-1] < rl < c[-1]) if d == 1 else (h[-1] > rh > c[-1])
        if not _pierce or _wick < config.SIDEWAY_ENTRY_WICK_ATR * atr_1h:
            raise ValueError(f"Sideway: ยังไม่มีแท่ง 1H ไส้ทะลุขอบแล้วปิดกลับ (ไส้ {_wick / atr_1h:.2f} ATR "
                             f"· ทะลุขอบ {'ใช่' if _pierce else 'ไม่'})")
    elif d == 1 and not (c[-1] > o[-1] and c[-1] > c[-2]):
        raise ValueError("Sideway: เตรียม Long แต่ยังไม่มีแท่ง 1H ยืนยัน (ต้องเขียวและปิดสูงกว่าแท่งก่อน)")
    elif d == -1 and not (c[-1] < o[-1] and c[-1] < c[-2]):
        raise ValueError("Sideway: เตรียม Short แต่ยังไม่มีแท่ง 1H ยืนยัน (ต้องแดงและปิดต่ำกว่าแท่งก่อน)")

    sl = rl - config.SIDEWAY_SL_EXT * w if d == 1 else rh + config.SIDEWAY_SL_EXT * w
    if config.SIDEWAY_ENTRY_WICK_ATR:                # SL ต้องอยู่เลยปลายไส้เสมอ
        sl = min(sl, l[-1] - 0.1 * atr_1h) if d == 1 else max(sl, h[-1] + 0.1 * atr_1h)
    tp = rl + w / 2
    if config.SIDEWAY_SL_WIDEN_ATR and abs(entry - sl) < config.SIDEWAY_SL_WIDEN_ATR * atr_1h:
        sl = entry - d * config.SIDEWAY_SL_WIDEN_ATR * atr_1h      # ขยาย SL ให้ถึง K ATR1H (lot ลดตาม · ดู config)
    if abs(entry - sl) < config.SIDEWAY_MIN_SL_ATR * atr_1h:
        raise ValueError(f"Sideway: SL ห่างแค่ {abs(entry - sl) / atr_1h:.2f} ATR1H < {config.SIDEWAY_MIN_SL_ATR:g}")
    if d * (tp - entry) <= 0:
        raise ValueError("Sideway: TP อยู่ผิดฝั่งราคา")
    rr = abs(tp - entry) / abs(entry - sl)

    return {"direction": direction, "sl": sl, "exec_sl": sl, "tp": tp, "rr": rr,
            "atr_entry": None,            # None = ไม่ใช้ ATR trailing / TP_MAX_ATR กับไม้นี้
            "atr_1h": atr_1h, "range_high": rh, "range_low": rl, "range_pos": pos,
            "sideway_bars": run, "adx_now": float(adx[k])}


def adx_now_closed(symbol: str, as_of=None) -> float:
    """ADX 4H ของแท่งปิดล่าสุด — exit_monitor ใช้ตัดสินว่า sideway จบหรือยัง (≥ SIDEWAY_EXIT_ADX)"""
    now = _now(as_of)
    d4 = rc.get_adx_bars(symbol, bars=SIDEWAY_4H_BARS, as_of=as_of)
    d4 = d4[pd.to_datetime(d4["time"]) + _H4 <= now].reset_index(drop=True)
    return float(rc.calc_adx(d4, rc.ADX_PERIOD).iloc[-1])
