@echo off
title Sniper Bot - Status
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m sniper_bot status -c config\example.yaml
pause
