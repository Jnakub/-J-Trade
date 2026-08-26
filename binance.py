import requests
import pandas as pd

from bars import BAR_OFFSET_H

BITSTAMP_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"

# MT5 symbols → Bitstamp pairs (crypto)
BITSTAMP_MAP = {
    "BTCUSDm": "btcusd",
    "ETHUSDm": "ethusd",
    "XRPUSDm": "xrpusd",
}

# MT5 symbols → yfinance tickers (commodities)
# XAUUSDm ใช้ tick_volume จาก MT5 เพราะ yfinance GC=F พังที่ 4H (fetch_comex_volume ไม่รองรับ intraday) — ไปใช้ FXCM แทน
YFINANCE_MAP = {
    "XAGUSDm": "SI=F",   # COMEX Silver Futures (ไว้ขยายต่อ)
}

STEP_MAP = {
    "1D": 86400,
    "4H": 14400,
    "1H": 3600,
}

YFINANCE_INTERVAL_MAP = {
    "1D": "1d",
    "4H": "1h",
    "1H": "1h",
}


# ---------------------------------------------------------------------------
# Bitstamp — สำหรับ Crypto (BTC, ETH, XRP)
# ---------------------------------------------------------------------------

def _fetch_bitstamp_ohlc_paged(pair: str, step: int, need: int) -> pd.DataFrame:
    """ดึงแท่งดิบจาก Bitstamp ให้ครบ `need` แท่ง โดย page ย้อนหลังด้วย `end`
    (Bitstamp limit สูงสุด 1000 แท่ง/request) — ใช้ตอนต้อง resample เอง (4H offset != 0)
    ที่ต้องใช้แท่ง 1H จำนวนมากกว่า limit เดียวรองรับไหว"""
    url    = BITSTAMP_URL.format(pair=pair)
    frames = []
    end_ts = None
    remaining = need
    while remaining > 0:
        limit  = min(remaining, 1000)
        params = {"step": step, "limit": limit}
        if end_ts is not None:
            params["end"] = end_ts
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("ohlc", [])
        if not data:
            break
        frames.append(pd.DataFrame(data))
        oldest_ts = int(data[0]["timestamp"])
        end_ts    = oldest_ts - 1
        remaining -= len(data)
        if len(data) < limit:   # ไม่มีข้อมูลเก่ากว่านี้แล้ว
            break

    if not frames:
        raise RuntimeError("Bitstamp ไม่มีข้อมูล")

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_bitstamp_volume(mt5_symbol: str, timeframe: str = "1D",
                          bars: int = 210, offset_h: int = 0) -> pd.DataFrame:
    """ดึง Volume จริงจาก Bitstamp — คืน DataFrame [date, volume] (1D) หรือ [time, volume] (อื่นๆ)

    offset_h != 0 (เฉพาะ timeframe="4H"): symbol นี้ใน bars.BAR_OFFSET_H เลื่อนขอบแท่ง 4H จาก
    MT5 ไปแล้ว (ประกอบจากแท่ง 1H ด้วย resample("4h", offset=f"{offset_h}h") — ดู bars.py) ถ้ายัง
    ขอ Bitstamp step=14400 แบบเดิม (ขอบเวลายึด UTC เป๊ะ 00/04/08...) จะไม่มีวันตรงกับขอบแท่งที่
    เลื่อนแล้วเลยสักแท่ง (merge ได้ 0/N ทุกครั้ง) ต้องดึง Bitstamp เป็นแท่ง 1H แล้ว resample ด้วย
    offset เดียวกันก่อน ให้ขอบแท่งทั้งสองฝั่งตรงกัน"""
    pair = BITSTAMP_MAP.get(mt5_symbol)
    if pair is None:
        raise ValueError(f"ไม่มี symbol '{mt5_symbol}' ใน Bitstamp")

    if timeframe == "4H" and offset_h:
        h1_need = bars * 4 + 40   # เผื่อ headroom สำหรับ resample ตัดแท่งขอบ
        raw = _fetch_bitstamp_ohlc_paged(pair, STEP_MAP["1H"], h1_need)
        raw["time"]   = pd.to_datetime(raw["timestamp"].astype(int), unit="s", utc=True)
        raw["volume"] = raw["volume"].astype(float)
        g = (raw.set_index("time")["volume"]
                .resample("4h", offset=f"{offset_h}h")
                .sum()
                .reset_index())
        return g.tail(bars).reset_index(drop=True)

    step   = STEP_MAP.get(timeframe, 86400)
    df = _fetch_bitstamp_ohlc_paged(pair, step, bars)

    df["time"]   = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df["date"]   = df["time"].dt.date
    df["volume"] = df["volume"].astype(float)

    # 1D → คืน date (merge รายวัน), อื่นๆ → คืน time (merge รายแท่ง)
    if timeframe == "1D":
        return df[["date", "volume"]].reset_index(drop=True)
    else:
        return df[["time", "volume"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# yfinance — สำหรับ Gold/Silver (COMEX Futures)
# ---------------------------------------------------------------------------

def fetch_comex_volume(mt5_symbol: str, timeframe: str = "1D",
                       bars: int = 210) -> pd.DataFrame:
    """
    ดึง Volume จาก COMEX Futures ผ่าน yfinance
    GC=F = Gold Futures (ตรงกับที่ TradingView แสดงบน XAUUSD)
    คืน DataFrame [date, volume]
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("ต้องติดตั้ง yfinance ก่อน: pip install yfinance")

    ticker   = YFINANCE_MAP.get(mt5_symbol)
    if ticker is None:
        raise ValueError(f"ไม่มี symbol '{mt5_symbol}' ใน COMEX map")

    interval = YFINANCE_INTERVAL_MAP.get(timeframe, "1d")

    # ดึงข้อมูลย้อนหลังให้พอกับ bars ที่ต้องการ
    period = "2y" if timeframe == "1D" else "60d"
    hist   = yf.Ticker(ticker).history(period=period, interval=interval)

    if hist.empty:
        raise RuntimeError(f"yfinance ไม่มีข้อมูล {ticker}")

    # 4H → resample จาก 1H
    if timeframe == "4H":
        hist = hist["Volume"].resample("4h").sum().reset_index()
        hist.columns = ["Datetime", "Volume"]
        dt_col = pd.to_datetime(hist["Datetime"])
    else:
        hist = hist.reset_index()
        date_col = "Date" if "Date" in hist.columns else "Datetime"
        dt_col = pd.to_datetime(hist[date_col])

    # normalize ทุก timezone → UTC แล้วดึงเฉพาะวันที่
    if dt_col.dt.tz is not None:
        dt_col = dt_col.dt.tz_convert("UTC")
    hist["date"] = dt_col.dt.date

    df = hist[["date", "Volume"]].copy()
    df.columns = ["date", "volume"]
    df = df.dropna().tail(bars).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# merge_real_volume — router หลัก
# ---------------------------------------------------------------------------

# cache ของ volume series สำหรับ backtest (as_of != None) เท่านั้น — key = (symbol, tf, offset_h)
# เก็บชุดที่ "ยาวที่สุดที่เคยดึง" ไว้ใช้ซ้ำ ไม่ต้องยิง API ใหม่ทุกแท่งที่สแกน
# path รันสด (as_of=None) ไม่แตะ cache นี้เลย — ต้องได้ volume สดใหม่ทุกครั้งเสมอ
_VOL_CACHE: dict = {}


def _volume_series(mt5_symbol: str, timeframe: str, need: int, offset_h: int,
                   as_of=None) -> tuple[pd.DataFrame, str]:
    if mt5_symbol in BITSTAMP_MAP:
        source  = "Bitstamp"
        fetcher = lambda n: fetch_bitstamp_volume(mt5_symbol, timeframe, bars=n, offset_h=offset_h)
    elif mt5_symbol in YFINANCE_MAP:
        source  = f"COMEX ({YFINANCE_MAP[mt5_symbol]})"
        fetcher = lambda n: fetch_comex_volume(mt5_symbol, timeframe, bars=n)
    else:
        raise ValueError(f"ไม่รู้จัก '{mt5_symbol}'")

    if as_of is None:
        return fetcher(need), source

    key    = (mt5_symbol, timeframe, offset_h)
    cached = _VOL_CACHE.get(key)
    if cached is None or len(cached) < need:
        cached = fetcher(need)
        _VOL_CACHE[key] = cached
    return cached, source


def merge_real_volume(df_mt5: pd.DataFrame, mt5_symbol: str,
                      timeframe: str = "1D", as_of=None) -> pd.DataFrame:
    """
    แทนที่ tick_volume ด้วย real volume จากแหล่งที่เหมาะสม:
      - Crypto (BTC/ETH/XRP) → Bitstamp
      - Gold/Silver      → COMEX Futures ผ่าน yfinance
    ถ้าดึงไม่ได้ → คง tick_volume เดิมไว้

    as_of (backtest) แก้สองอย่างที่เคยทำให้ backtest ไม่ตรงกับระบบจริงแบบเงียบๆ:
      1) แหล่งข้อมูลคืนแต่แท่ง "ล่าสุด" เสมอ — เดิมขอแค่ len(df)+10 แท่ง (4H = ~210 แท่ง
         ≈ 35 วัน) พอ backtest ย้อนไกลกว่านั้น merge ไม่ติดสักแท่งแล้วตกกลับไปใช้ tick_volume
         ของโบรกเกอร์ทั้งชุด (คนละตัวเลขกับที่ระบบจริงใช้หา SL/OBV) — ตอนนี้ขอเผื่อระยะจาก
         as_of ถึงปัจจุบันด้วย แล้ว cache ไว้ใช้ซ้ำทั้งการสแกน
      2) แท่งท้ายสุดของเฟรมใน as_of mode คือแท่งที่ "ยังไม่ปิด" (bars.get_bars ประกอบให้)
         แต่แหล่งข้อมูลมีแท่งนั้นแบบจบแล้ว — merge ตรงๆ = ได้ volume ทั้งแท่งของอนาคตมาใส่
         จึงข้ามแท่งที่ยังไม่ปิด ปล่อยให้ใช้ tick_volume ที่ประกอบมาถูกต้องแล้วแทน
    """
    df = df_mt5.copy()

    offset_h = BAR_OFFSET_H.get(mt5_symbol, 0) if timeframe == "4H" else 0
    need     = len(df) + 10
    if as_of is not None:
        # เผื่อระยะจาก as_of ถึง "ตอนนี้" เพราะแหล่งข้อมูลนับถอยหลังจากแท่งล่าสุดเสมอ
        step  = STEP_MAP.get(timeframe, 86400)
        gap_s = max((pd.Timestamp.utcnow().tz_localize(None) - pd.Timestamp(as_of)).total_seconds(), 0)
        need += int(gap_s // step)

    try:
        df_vol, source = _volume_series(mt5_symbol, timeframe, need, offset_h, as_of=as_of)
        df_vol = df_vol.copy()
    except ValueError as exc:
        print(f"  [Volume] {exc} — ใช้ tick_volume")
        return df
    except Exception as exc:
        print(f"  [Volume] ดึงไม่ได้ ใช้ tick_volume แทน — {exc}")
        return df

    if timeframe == "1D":
        # merge รายวัน
        df["date"] = pd.to_datetime(df["time"]).dt.date
        df = df.merge(df_vol, on="date", how="left")
        df = df.drop_duplicates(subset=["time"]).reset_index(drop=True)
        drop_cols = ["date", "volume"]
    else:
        # merge รายแท่ง — normalize time ให้ตรงกัน (ทั้งคู่เป็น UTC ไม่มี tz)
        df["time_utc"] = pd.to_datetime(df["time"], utc=False).dt.tz_localize(None)
        df_vol["time"] = df_vol["time"].dt.tz_localize(None)
        df = df.merge(df_vol, left_on="time_utc", right_on="time", how="left",
                      suffixes=("", "_bs"))
        df = df.drop_duplicates(subset=["time"]).reset_index(drop=True)
        drop_cols = ["time_utc", "time_bs", "volume"]

    df["tick_volume"] = df["tick_volume"].astype(float)
    matched = df["volume"].notna()
    if as_of is not None:
        # ข้ามแท่งที่ยังไม่ปิด ณ as_of — แหล่งข้อมูลมีแท่งนั้นแบบจบแล้ว เอามาใส่ = volume อนาคต
        step_td = pd.Timedelta(seconds=STEP_MAP.get(timeframe, 86400))
        matched &= (pd.to_datetime(df["time"]) + step_td <= pd.Timestamp(as_of))
    df.loc[matched, "tick_volume"] = df.loc[matched, "volume"]

    total     = len(df)
    unmatched = int((~matched).sum())
    matched_n = total - unmatched
    if as_of is not None:
        pass          # backtest สแกนหลายพันแท่ง — ไม่ต้องพิมพ์บรรทัดนี้ซ้ำทุกครั้ง
    elif matched_n == 0:
        print(f"  [{source}] merge ไม่ได้เลย — ใช้ tick_volume ทั้งหมด")
    elif unmatched > 0:
        print(f"  [{source}] real volume โหลดสำเร็จ ✅  ({matched_n}/{total} แท่ง, {unmatched} แท่งไม่ตรง→tick_volume)")
    else:
        print(f"  [{source}] real volume โหลดสำเร็จ ✅  ({matched_n}/{total} แท่ง)")

    df = df.drop(columns=drop_cols, errors="ignore")
    return df
