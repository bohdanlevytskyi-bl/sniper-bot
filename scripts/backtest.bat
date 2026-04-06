@echo off
title Sniper Bot - Backtest
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m sniper_bot backtest -c config\example.yaml
pause
