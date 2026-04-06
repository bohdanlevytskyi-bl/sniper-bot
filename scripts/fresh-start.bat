@echo off
title Sniper Bot - Fresh Start
cd /d "%~dp0\.."
echo Deleting old database...
del /f /q config\data\paper.sqlite 2>nul
echo Starting bot with fresh database...
.venv\Scripts\python.exe -m sniper_bot run -c config\example.yaml
pause
