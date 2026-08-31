#!/bin/zsh
# รัน script ของโปรเจกต์ด้วย Python ที่อยู่ใน Wine prefix ของ MetaTrader 5 (macOS)
# — MetaTrader5 เป็น extension ของ Windows import จาก python ฝั่ง mac ไม่ได้
# ใช้: ./run_wine.sh backtest_replay.py BTCUSDm 730 --drop="OBV 4H" --min-score=5
export WINEPREFIX="/Users/jjay/Library/Application Support/net.metaquotes.wine.metatrader5"
export WINEDEBUG=-all
export PYTHONUTF8=1
cd "$(dirname "$0")"
exec "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine" \
     'C:\Python311\python.exe' "$@"
