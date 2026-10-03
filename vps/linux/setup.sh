#!/usr/bin/env bash
# ติดตั้งบอทบน Ubuntu VPS (24.04 / 26.04) — MT5 + Python 3.11 ฝั่ง Windows รันผ่าน wine
# แบบเดียวกับบน Mac (run_wine.sh) ต่างกันแค่ไม่มีจอ จึงใช้จอเสมือน Xvfb :99
#
# รันด้วย root ครั้งเดียว:   sudo bash vps/linux/setup.sh
# รันซ้ำได้ — ขั้นที่ทำไปแล้วจะถูกข้าม
set -euo pipefail

BOT_USER=trader
REPO_URL=https://github.com/Jnakub/-J-Trade.git
HOME_DIR=/home/$BOT_USER
REPO_DIR=$HOME_DIR/-J-Trade
PREFIX=$HOME_DIR/.wine-mt5
PY_VER=3.11.9
PY_URL=https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe
MT5_URL=https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
# ลิงก์เดียวกับใน mt5linux.sh ของ MetaQuotes
WEBVIEW_URL=https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/f2910a1e-e5a6-4f17-b52d-7faf525d17f8/MicrosoftEdgeWebview2Setup.exe

[ "$(id -u)" = 0 ] || { echo "ต้องรันด้วย root (sudo)"; exit 1; }

echo "== 1/7 timezone UTC+7 =="
# 🔴 trades_log.csv / ขอบวัน MAX_DAILY_LOSS / cooldown ใช้เวลาเครื่อง และแถวเก่าบันทึกเป็น UTC+7
timedatectl set-timezone Asia/Bangkok

echo "== 2/7 wine + Xvfb + x11vnc =="
command -v wget >/dev/null || { apt-get update; apt-get install -y wget; }
# 🔴 ต้องเป็น wine **staging** (เหมือนสคริปต์ทางการ mt5linux.sh ของ MetaQuotes) — กับ wine-11.0 stable
# mt5setup.exe ฟ้อง "A debugger has been found running in your system" แล้วค้าง (เจอจริง 2026-10-03)
if ! wine --version 2>/dev/null | grep -qi staging; then
    dpkg --add-architecture i386
    . /etc/os-release
    codename=$VERSION_CODENAME
    winehq_src="https://dl.winehq.org/wine-builds/ubuntu/dists/$codename/winehq-$codename.sources"
    if wget -q --spider "$winehq_src"; then
        mkdir -pm755 /etc/apt/keyrings
        # apt ของ 26.04 ไม่รับ key แบบ ASCII-armored ในไฟล์ .key ("unsupported filetype" -> repo ไม่ถูก sign)
        # -> แปลงเป็น binary ด้วย gpg --dearmor (ชื่อไฟล์ต้องคงเดิม เพราะไฟล์ .sources ของ WineHQ อ้างชื่อนี้)
        wget -qO- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor --yes -o /etc/apt/keyrings/winehq-archive.key
        wget -qNP /etc/apt/sources.list.d/ "$winehq_src"
        apt-get update
        apt-get install -y --install-recommends winehq-staging
    else
        # WineHQ ยังไม่ออกแพ็กเกจให้ Ubuntu รุ่นใหม่เสมอ (เจอจริง 2026-10-03: Hostinger ลง 26.04 มาให้)
        # -> ใช้ wine ของ Ubuntu เอง · wine32 ใส่ไว้เผื่อ installer 32-bit (mt5setup) ไม่มีก็ไปต่อได้
        echo "WineHQ ไม่มีแพ็กเกจสำหรับ $codename — ใช้ wine ของ Ubuntu แทน"
        apt-get update
        apt-get install -y wine
        apt-get install -y wine32:i386 || echo "ไม่มี wine32 — ข้าม"
    fi
fi
wine --version
apt-get install -y xvfb x11vnc git wget

echo "== 3/7 user $BOT_USER =="
id $BOT_USER >/dev/null 2>&1 || useradd -m -s /bin/bash $BOT_USER

echo "== 4/7 clone repo =="
if [ ! -d "$REPO_DIR/.git" ]; then
    sudo -u $BOT_USER git clone "$REPO_URL" "$REPO_DIR"
fi

echo "== 5/7 Xvfb service (จอเสมือนให้ MT5) =="
install -m644 "$REPO_DIR/vps/linux/jtrade-xvfb.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jtrade-xvfb

# ทุกคำสั่ง wine ด้านล่างรันเป็น trader บนจอ :99 ใน prefix แยกของบอท
# WINEDLLOVERRIDES ปิดหน้าต่าง "ติดตั้ง Mono ไหม" — บนจอเสมือนไม่มีใครกด = ค้างตลอดไป (บอทไม่ใช้ .NET)
# ไม่ปิด mshtml (Gecko) เพราะตัวติดตั้ง MT5 อาจต้องใช้
as_bot() { sudo -u $BOT_USER env DISPLAY=:99 WINEPREFIX="$PREFIX" WINEDEBUG=-all WINEARCH=win64 \
    WINEDLLOVERRIDES="mscoree=" "$@"; }

echo "== 6/7 Python $PY_VER (Windows) ใน wine =="
if [ ! -f "$PREFIX/drive_c/Python311/python.exe" ]; then
    as_bot wineboot -u
    as_bot wget -qO /tmp/python-installer.exe "$PY_URL"
    # TargetDir = C:\Python311 ให้ตรงกับบน Mac
    as_bot wine /tmp/python-installer.exe /quiet InstallAllUsers=1 PrependPath=0 \
        Include_test=0 TargetDir='C:\Python311'
    as_bot wineserver -w
fi
as_bot wine "$PREFIX/drive_c/Python311/python.exe" -m pip install --upgrade \
    MetaTrader5 pandas python-dotenv requests yfinance

echo "== 7/7 MetaTrader 5 ใน wine =="
if [ ! -f "$PREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
    # ลำดับตาม mt5linux.sh ของ MetaQuotes: Windows 11 -> WebView2 -> mt5setup
    as_bot winecfg -v=win11
    as_bot wget -qO /tmp/webview2.exe "$WEBVIEW_URL"
    as_bot wine /tmp/webview2.exe /silent /install
    as_bot wget -qO /tmp/mt5setup.exe "$MT5_URL"
    # ไม่ใช้ /auto — ตัวติดตั้งแบบเงียบค้างโดยไม่มีอะไรบอก · รันแบบปกติแล้วกด Next ผ่าน VNC
    echo ">>> เปิด VNC (vps/linux/vnc.sh) แล้วกด Next ในตัวติดตั้ง MT5 — ล็อกอินบัญชีในหน้าจอเดียวกันได้เลย"
    as_bot wine /tmp/mt5setup.exe &
    for _ in $(seq 1 180); do
        [ -f "$PREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ] && break
        sleep 5
    done
fi
if [ -f "$PREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
    echo "MT5 ติดตั้งแล้ว"
else
    echo "❌ ยังไม่มี terminal64.exe — MT5 ติดตั้งไม่สำเร็จ ดูหน้าจอผ่าน VNC" >&2
    exit 1
fi

# services ของ MT5 + บอท — ติดตั้งแต่ยัง **ไม่ enable** จนกว่าจะล็อกอิน MT5 และก๊อป state จาก Mac แล้ว
install -m644 "$REPO_DIR/vps/linux/jtrade-mt5.service" "$REPO_DIR/vps/linux/jtrade@.service" \
    /etc/systemd/system/
systemctl daemon-reload

cat <<MSG

ติดตั้งเสร็จ — ขั้นต่อไป (ดู vps/linux/README.md):
  1. ก๊อป .env + state files จาก Mac ไปที่ $REPO_DIR
  2. ล็อกอิน MT5 ผ่าน VNC ครั้งเดียว
  3. systemctl enable --now jtrade-mt5 jtrade@scheduler jtrade@exit_monitor
MSG
