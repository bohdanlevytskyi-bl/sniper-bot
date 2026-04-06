@echo off
title Sniper Bot - Paper Trading
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m sniper_bot run -c config\example.yaml
pause
