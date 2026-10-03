@echo off
rem Usage: start_bot.bat scheduler.py   |   start_bot.bat exit_monitor.py
rem Runs one long-lived bot process and restarts it 60 s after it exits.
rem ASCII only on purpose: cmd.exe reads .bat files in the OEM code page, so
rem Thai text here would be garbled. Explanation lives in vps\README.md.

setlocal
if "%~1"=="" (
    echo usage: %~nx0 scheduler.py ^| exit_monitor.py
    exit /b 2
)
set "SCRIPT=%~1"
title J-Trade %SCRIPT%

rem Thai output from print(); run_wine.sh sets the same variable on the Mac.
set PYTHONUTF8=1
chcp 65001 >nul

rem Repo root = parent of this folder. scheduler/exit_monitor resolve their
rem state files (.env, trades_log.csv, logs\) relative to their own location.
cd /d "%~dp0.."

rem Prefer the py launcher pinned to 3.11 (same version as the Wine setup).
set "PY=python"
where py >nul 2>&1 && set "PY=py -3.11"

if not exist logs mkdir logs

:loop
echo [%date% %time%] start %SCRIPT% >> logs\launcher.log
%PY% %SCRIPT%
echo [%date% %time%] %SCRIPT% exited with code %errorlevel% - restart in 60 s >> logs\launcher.log
echo.
echo %SCRIPT% exited (code %errorlevel%). Restarting in 60 s - close this window to stop.
timeout /t 60 /nobreak >nul
goto loop
