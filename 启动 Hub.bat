@echo off
cd /d "%~dp0"
python hub.py --web %*
pause
