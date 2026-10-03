#!/usr/bin/env bash
# เปิดหน้าจอ MT5 ให้ดูจาก Mac — ฟังเฉพาะ localhost เข้าได้ผ่าน ssh tunnel เท่านั้น
# ครั้งแรกตั้งรหัส VNC ก่อน (ในฐานะ trader):  x11vnc -storepasswd
# ปิด: Ctrl+C
exec x11vnc -display :99 -localhost -rfbport 5900 -rfbauth "$HOME/.vnc/passwd" -forever -shared
