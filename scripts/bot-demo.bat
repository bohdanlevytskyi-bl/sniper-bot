@echo off
title Sniper Bot - Demo Trading
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m sniper_bot run -c config\example.yaml --demo
pause
