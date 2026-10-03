# รันบอทบน Windows VPS

บน Windows `MetaTrader5` import ได้ตรงๆ — **ไม่ต้องใช้ wine / `run_wine.sh`** (นั่นมีไว้สำหรับ Mac เท่านั้น)
ระบบจริงคือ 2 โปรเซสที่รันค้างไว้: `scheduler.py` (เปิดไม้) + `exit_monitor.py` (จัดการไม้)

| ไฟล์ | ทำอะไร |
|---|---|
| `start_bot.bat` | รันสคริปต์ 1 ตัวแล้ว **รีสตาร์ทเองทุกครั้งที่มันตาย** (รอ 60 วิ) · บันทึกการเริ่ม/ตายลง `logs\launcher.log` |
| `start_scheduler.bat` / `start_exit_monitor.bat` | ตัวเรียก `start_bot.bat` — ดับเบิลคลิกเพื่อรันเองด้วยมือได้ |
| `install_tasks.ps1` | ลงทะเบียน Task Scheduler 2 ตัว (`JTrade-Scheduler` · `JTrade-ExitMonitor`) ให้เริ่มเองตอน logon |

ไฟล์ `.bat`/`.ps1` เขียนเป็น ASCII ล้วนโดยตั้งใจ — cmd อ่าน `.bat` ด้วย OEM code page และ
PowerShell 5.1 อ่านไฟล์ที่ไม่มี BOM เป็น ANSI ภาษาไทยในไฟล์พวกนั้นจะเพี้ยน (คำอธิบายจึงอยู่ที่นี่)
และต้องเป็น CRLF (`.gitattributes` บังคับไว้แล้ว) — `goto`/label ของ cmd พังได้ถ้าเป็น LF

## ติดตั้งครั้งแรก

1. ติดตั้ง **MT5 ของ Exness** · ล็อกอินบัญชีจริงด้วยมือ 1 ครั้ง · เปิดปุ่ม **Algo Trading**
2. ติดตั้ง **Python 3.11 64-bit** (ติ๊ก "Add to PATH" + py launcher) แล้ว
   `py -3.11 -m pip install MetaTrader5 pandas python-dotenv requests yfinance`
3. 🔴 **ตั้ง timezone เป็น UTC+7**: `Set-TimeZone -Id 'SE Asia Standard Time'`
   — `trades_log.csv`, ขอบวันของ `MAX_DAILY_LOSS`, cooldown และสรุปรายวันใช้ `datetime.now()`
   (เวลาเครื่อง) และแถวเก่าทั้งหมดบันทึกเป็น UTC+7 · VPS มักมาเป็น UTC = ตัวเลขผิดแบบเงียบๆ ไม่มี error
4. `git clone https://github.com/Jnakub/-J-Trade.git`
5. **ก๊อปจาก Mac (ฉบับล่าสุด)**: `.env` · `trades_log.csv` · `reject_cooldown.json` ·
   `daily_summary_state.json` · `cuts_log.csv`
   — `trades_log.csv` สำคัญสุด: มีฐาน SL ตรึง (`pinned_swing`) ของไม้ที่เปิดค้าง ฉบับใน git ล้าหลังเสมอ
6. ตรวจ: `py -3.11 mt5_connect.py` · `py -3.11 exit_monitor.py --rules` (ต้องตรงกับบน Mac) ·
   `py -3.11 test_portfolio_risk.py` · `py -3.11 test_exit_labels.py`
7. `powershell -ExecutionPolicy Bypass -File vps\install_tasks.ps1` — มันเตือนถ้า timezone ผิด
   หรือไม่มี `.env`/`trades_log.csv` (เตือนอย่างเดียว ไม่แก้การตั้งค่าระบบให้)
8. เปิด **auto-logon** ของ Windows (task ผูกกับ logon เพราะ MT5 terminal ต้องมี desktop session)
   และตั้ง Windows Update ไม่ให้รีสตาร์ทเอง

## 🔴 ตอนสลับจาก Mac -> VPS ห้ามรันสองเครื่องพร้อมกัน

scheduler สองตัวจะเปิดไม้ซ้ำ · exit_monitor สองตัวจะแย่งกันขยับ SL/TP — ไม่มีอะไรในโค้ดกันข้ามเครื่องได้
ลำดับ: **หยุดบน Mac ทั้งสองตัว** -> ก๊อป state ข้อ 5 -> เริ่มบน VPS
(`Start-ScheduledTask JTrade-Scheduler; Start-ScheduledTask JTrade-ExitMonitor`) -> ดูรอบแรกใน
`logs\scheduler.log` ว่า reconcile ไม้ที่ค้างถูก · ไม้ที่เปิดอยู่อยู่ที่โบรก ไม่ได้รับผลกระทบจากการย้าย

## ใช้งานประจำ

- **หยุด**: ปิดหน้าต่าง console ทั้งสองบาน (ปิดหน้าต่าง = ตัด python ด้วย ·
  `Stop-ScheduledTask` อย่างเดียวอาจทิ้ง python ไว้)
- **อัปเดตโค้ด**: หยุดทั้งสองตัว -> `git pull` -> เริ่มใหม่
  ⚠️ `trades_log.csv` ยัง track อยู่ใน git แต่บอทเขียนทับตลอด — pull ที่แตะไฟล์นี้จะชน
  **อย่า commit ไฟล์นี้จาก Mac อีก** หลังย้ายแล้ว (ฉบับจริงอยู่บน VPS)
- **ถอน task**: `powershell -ExecutionPolicy Bypass -File vps\install_tasks.ps1 -Uninstall`
- Task ตั้ง `ExecutionTimeLimit = 0` ไว้แล้ว — ค่า default ของ Windows (72 ชม.) จะฆ่าบอทเงียบๆ ทุก 3 วัน
