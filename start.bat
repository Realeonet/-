@echo off
rem Запуск приложения. Сначала один раз выполните install.bat (см. README.md).
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Не найдено окружение .venv. Сначала запустите install.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
rem Слушаем только локальную сеть; телеметрия Streamlit выключена.
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true --browser.gatherUsageStats false
pause
