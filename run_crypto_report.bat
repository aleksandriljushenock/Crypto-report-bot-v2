@echo off
cd /d C:\Users\user\crypto_report_service
echo Starting crypto report...
echo Current directory: %cd%
echo.

C:\Users\user\crypto_report_service\.venv\Scripts\python.exe app.py

echo.
echo Finished with exit code %errorlevel%
pause