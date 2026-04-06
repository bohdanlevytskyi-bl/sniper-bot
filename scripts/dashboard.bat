@echo off
title Sniper Bot - Dashboard
cd /d "%~dp0\.."
echo Starting dashboard at http://127.0.0.1:8080 ...
start http://127.0.0.1:8080
.venv\Scripts\python.exe -m sniper_bot dashboard -c config\example.yaml
pause
