@echo off
title Sniper Bot - Market Scan
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m sniper_bot scan -c config\example.yaml
pause
