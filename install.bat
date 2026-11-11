@echo off
rem Установка окружения (один раз, или после обновления requirements.txt).
chcp 65001 >nul
cd /d "%~dp0"
python -m venv .venv || (echo Нужен установленный Python 3.11+ & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Готово. Теперь запустите start.bat
pause
