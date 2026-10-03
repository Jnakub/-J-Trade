# รันบอทบน Ubuntu VPS (Hostinger) — ผ่าน wine

ใช้ทางนี้เมื่อ VPS เป็น Linux (Hostinger ไม่มี Windows) · ถ้าเป็น Windows VPS ใช้ `vps/README.md` แทน
ใช้ได้ทั้ง Ubuntu 24.04 และ 26.04 — ถ้า WineHQ ยังไม่มีแพ็กเกจให้รุ่นนั้น setup.sh จะใช้ wine ของ Ubuntu แทนเอง
หลักการเหมือนบน Mac ทุกอย่าง: MT5 + Python 3.11 **ฝั่ง Windows** รันใน wine (`MetaTrader5` import
จาก python ของ Linux ไม่ได้ เหมือนฝั่ง mac) ต่างแค่ VPS ไม่มีจอ จึงใช้จอเสมือน Xvfb `:99`

| ไฟล์ | ทำอะไร |
|---|---|
| `setup.sh` | ติดตั้งทั้งหมดครั้งเดียว (root): timezone UTC+7 · wine · Xvfb · user `trader` · clone repo · Python 3.11 + MT5 ใน wine |
| `jtrade-xvfb.service` | จอเสมือน `:99` |
| `jtrade-mt5.service` | MT5 terminal (ตายแล้วเริ่มใหม่ใน 30 วิ) |
| `jtrade@.service` | บอท: `jtrade@scheduler` · `jtrade@exit_monitor` (ตายแล้วเริ่มใหม่ใน 60 วิ — แทน `start_bot.bat`) |
| `run.sh` | เทียบเท่า `run_wine.sh` — รันสคริปต์อื่น เช่น `--rules` หรือเทสต์ |
| `vnc.sh` | เปิดหน้าจอ MT5 ให้ดูจาก Mac (ฟังแค่ localhost — เข้าผ่าน ssh tunnel เท่านั้น) |

ทุกคำสั่งข้างล่างที่ขึ้นต้นด้วย **[Mac]** พิมพ์ใน Terminal บน Mac · **[VPS]** พิมพ์หลัง ssh เข้าไปแล้ว
`<IP>` = IP ของ VPS จากหน้า Hostinger

## 1. ติดตั้ง

**[Mac]** เข้า VPS: `ssh root@<IP>` (รหัส root ที่ตั้งตอนซื้อ)

**[VPS]** รันใน `tmux` เสมอ — ssh หลุดแล้วสคริปต์ยังรันต่อ (เกิดจริงครั้งแรก: หลุดกลางขั้น 6) · กลับเข้าไปดูด้วย `tmux attach`
```
tmux new -s setup
```
**[VPS]**
```
curl -fsSLo /tmp/setup.sh https://raw.githubusercontent.com/Jnakub/-J-Trade/main/vps/linux/setup.sh
bash /tmp/setup.sh 2>&1 | tee /tmp/setup.log
```
ใช้เวลาราว 10-20 นาที (ดาวน์โหลด wine + Python + MT5)
🔴 ขั้น 7 ต้อง **กด Next ในตัวติดตั้ง MT5 ผ่าน VNC** (ข้อ 3) — ตัวติดตั้งแบบเงียบ `/auto` ค้างโดยไม่มีอะไรบอก (เจอจริง 2026-10-03)
และต้องเป็น wine **staging** + Windows 11 + WebView2 ตามสคริปต์ทางการของ MetaQuotes — กับ wine stable
ตัวติดตั้งฟ้อง "A debugger has been found running in your system" (setup.sh ทำครบให้แล้ว)

## 2. ก๊อป `.env` + state จาก Mac

🔴 **ถ้าจะสลับจริงวันนี้: หยุดบอทบน Mac ก่อน** แล้วค่อยก๊อป (ไฟล์ต้องเป็นฉบับสุดท้าย)
ถ้าแค่ทดสอบ ก๊อปแค่ `.env` ไปก่อนได้ แล้วค่อยก๊อปที่เหลือวันสลับ

**[Mac]** (ในโฟลเดอร์ `-J-Trade` บน Mac)
```
scp .env trades_log.csv reject_cooldown.json daily_summary_state.json cuts_log.csv root@<IP>:/home/trader/-J-Trade/
```
**[VPS]** `chown trader:trader /home/trader/-J-Trade/.env /home/trader/-J-Trade/*.csv /home/trader/-J-Trade/*.json`

## 3. ล็อกอิน MT5 ครั้งเดียว (ผ่านหน้าจอ VNC)

**[VPS]** เริ่ม MT5 แล้วตั้งรหัส VNC (ครั้งแรกครั้งเดียว) และเปิด VNC:
```
systemctl start jtrade-mt5
sudo -iu trader x11vnc -storepasswd
sudo -iu trader /home/trader/-J-Trade/vps/linux/vnc.sh
```
**[Mac]** เปิด Terminal อีกหน้าต่าง ทำ tunnel แล้วเปิด Screen Sharing ที่ติดมากับ Mac:
```
ssh -L 5901:localhost:5900 root@<IP>
open vnc://localhost:5901
```
(ใช้ 5901 ฝั่ง Mac กันชนกับ Screen Sharing ของ Mac เอง)
ในหน้าจอ MT5: File -> Login to Trade Account -> ใส่บัญชี Exness · Tools -> Options -> Expert Advisors ->
ติ๊ก **Allow algorithmic trading** · ปุ่ม **Algo Trading** บนแถบเครื่องมือต้องเป็นสีเขียว
เสร็จแล้วปิด VNC ได้ (Ctrl+C ที่หน้าต่าง vnc.sh) — MT5 ยังรันต่อ

## 4. ตรวจก่อนเทรด

**[VPS]**
```
sudo -iu trader /home/trader/-J-Trade/vps/linux/run.sh mt5_connect.py
sudo -iu trader /home/trader/-J-Trade/vps/linux/run.sh exit_monitor.py --rules
sudo -iu trader /home/trader/-J-Trade/vps/linux/run.sh test_portfolio_risk.py
sudo -iu trader /home/trader/-J-Trade/vps/linux/run.sh test_exit_labels.py
```
connect ได้ · ตาราง `--rules` ตรงกับบน Mac · เทสต์ผ่าน 38 + 26 เคส

## 5. เริ่มบอท — 🔴 หลังหยุดบน Mac แล้วเท่านั้น

scheduler สองเครื่องจะเปิดไม้ซ้ำ · exit_monitor สองตัวจะแย่งกันขยับ SL/TP

**[VPS]**
```
systemctl enable --now jtrade-mt5 jtrade@scheduler jtrade@exit_monitor
```
`enable` = เริ่มเองทุกครั้งที่ VPS รีบูต ไม่ต้องล็อกอิน

## ใช้งานประจำ

| | คำสั่ง [VPS] |
|---|---|
| ดูสถานะ | `systemctl status jtrade@scheduler jtrade@exit_monitor jtrade-mt5` |
| ดู log สด | `tail -f /home/trader/-J-Trade/logs/scheduler.log` |
| หยุดบอท | `systemctl stop jtrade@scheduler jtrade@exit_monitor` |
| อัปเดตโค้ด | หยุดบอท -> `sudo -iu trader git -C /home/trader/-J-Trade pull` -> `systemctl start jtrade@scheduler jtrade@exit_monitor` |
| ดูหน้าจอ MT5 | ทำข้อ 3 ซ้ำ (ไม่ต้อง storepasswd) |

⚠️ `trades_log.csv` ยัง track อยู่ใน git แต่ฉบับจริงจะอยู่บน VPS — **อย่า commit ไฟล์นี้จาก Mac อีก**
ไม่งั้น `git pull` บน VPS จะชน
