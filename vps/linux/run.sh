#!/usr/bin/env bash
# เทียบเท่า run_wine.sh บน Mac — รันสคริปต์ใดๆ ด้วย python ใน wine ของบอท
# ใช้ (ในฐานะ trader):  vps/linux/run.sh exit_monitor.py --rules
cd "$(dirname "$0")/../.."
export DISPLAY=:99 WINEPREFIX=$HOME/.wine-mt5 WINEDEBUG=-all PYTHONUTF8=1
exec wine "$WINEPREFIX/drive_c/Python311/python.exe" "$@"
