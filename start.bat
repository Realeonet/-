@echo off
rem Start the app. Run install.bat first.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Сначала запустите install.bat & pause & exit /b 1)
echo Приложение запускается. Не закрывайте это окно, пока пользуетесь приложением.
echo На этом компьютере:  http://localhost:8501
echo С телефона дома:     http://IP-этого-компьютера:8501  (IP смотрите ниже, строка IPv4)
ipconfig | findstr /R /C:"IPv4"
echo.
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py
pause
