@echo off
cd /d %~dp0
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe healthcheck.py
) else (
  python healthcheck.py
)
pause
