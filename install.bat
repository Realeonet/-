@echo off
rem Install or update the environment. Run once, and again after every update.
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Не найден Python. Установите Python 3.11 или новее с python.org и отметьте "Add python.exe to PATH". & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" python -m venv .venv || (echo Не удалось создать окружение .venv & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Установка библиотек не удалась. Проверьте интернет и повторите. & pause & exit /b 1)
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Готово. Запускайте start.bat
pause
