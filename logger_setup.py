"""
logger_setup.py — logging ส่วนกลางสำหรับสคริปต์ที่รันค้างไว้แบบ unattended
(scheduler.py, exit_monitor.py) เขียนซ้ำทุกอย่างที่ print() ลงไฟล์ logs/<name>.log
พร้อม timestamp (rotate อัตโนมัติไม่ให้ไฟล์บวม) เพื่อให้ย้อนดูได้แม้ console ปิดไปแล้ว
โดยหน้าจอยังเห็นผลลัพธ์เหมือนเดิมทุกประการ (สี ANSI ไม่หาย)
"""
import builtins
import logging
import os
import re
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class _StripAnsiFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_RE.sub("", super().format(record))


def get_logger(name: str) -> logging.Logger:
    """Logger เดียวต่อชื่อ — เรียกซ้ำได้ (เช่น import ซ้ำ) ไม่เพิ่ม handler ซ้อน"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    os.makedirs(LOG_DIR, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, f"{name}.log"),
        maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(_StripAnsiFormatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(file_handler)
    return logger


def tee_print(logger: logging.Logger):
    """คืนฟังก์ชันไว้แทนที่ print() ในตัวโมดูล — ยังแสดงผลหน้าจอเหมือนเดิมทุกอย่าง
    (สี ANSI ปกติ) พร้อมเขียนสำเนา (ตัดโค้ดสี ANSI ออก) ลงไฟล์ log แบบมี timestamp
    ไปด้วยทุกบรรทัด — ใช้: `print = tee_print(log)` ต่อจาก `log = get_logger(...)`
    """
    _real_print = builtins.print

    def _print(*args, sep=" ", end="\n", **kwargs):
        _real_print(*args, sep=sep, end=end, **kwargs)
        text = sep.join(str(a) for a in args)
        for line in text.split("\n"):
            if line.strip():
                logger.info(line)

    return _print
