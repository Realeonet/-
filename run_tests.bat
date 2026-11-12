@echo off
rem Run the test suite.
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pytest
pause
