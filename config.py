import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Account & Risk Management
# ---------------------------------------------------------------------------
ACCOUNT_BALANCE = 10000   # เงินทุน (ปรับได้)
RISK_PER_TRADE  = 0.02    # 2% ต่อไม้
MAX_DAILY_LOSS  = 0.06    # ขาดทุนสูงสุดต่อวัน 6%
MIN_RR          = 1.5     # เกณฑ์ "R:R ดีจริง" ในสกอร์การ์ด + สูตร fallback TP
MIN_RR_HARD_BLOCK = 1.0   # 2026-07-23: เกณฑ์ขั้นต่ำสุดที่บล็อกได้ไม่ว่าคะแนนรวมเท่าไหร่
                          # (แยกจาก MIN_RR แล้ว — เดิมใช้ค่าเดียวกันทำให้ hard block กับ
                          # คะแนนสกอร์การ์ดซ้ำซ้อนกัน เหมือนที่แก้ไปแล้วใน reversal.py)
MIN_SCORE       = 8.0     # คะแนนขั้นต่ำจาก 10

# ---------------------------------------------------------------------------
# Scoring Weights  (รวม = 11)
# ---------------------------------------------------------------------------
WEIGHT_TREND_1D      = 2.0
WEIGHT_OBV_1D        = 1.0
WEIGHT_TREND_4H      = 1.0
WEIGHT_OBV_4H        = 1.0
WEIGHT_TREND_1H      = 1.0
WEIGHT_OBV_1H        = 1.0
WEIGHT_VSA           = 1.0
WEIGHT_MACD          = 1.0
WEIGHT_RR            = 1.0
WEIGHT_CONFIRMATION  = 1.0

TOTAL_WEIGHT = (
    WEIGHT_TREND_1D + WEIGHT_OBV_1D +
    WEIGHT_TREND_4H + WEIGHT_OBV_4H +
    WEIGHT_TREND_1H + WEIGHT_OBV_1H +
    WEIGHT_VSA + WEIGHT_MACD +
    WEIGHT_RR + WEIGHT_CONFIRMATION
)

# ---------------------------------------------------------------------------
# Symbols ที่ trade
# ---------------------------------------------------------------------------
SYMBOLS = ["BTCUSDm", "XAUUSDm"]

# ---------------------------------------------------------------------------
# MT5 Timeframe mapping
# ---------------------------------------------------------------------------
MT5_TIMEFRAMES = {
    "1D": mt5.TIMEFRAME_D1,
    "4H": mt5.TIMEFRAME_H4,
    "1H": mt5.TIMEFRAME_H1,
}
