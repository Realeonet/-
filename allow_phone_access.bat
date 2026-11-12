@echo off
rem Allow phones on the home network to reach the app. Run as administrator, once.
chcp 65001 >nul
net session >nul 2>nul || (echo Запустите этот файл правой кнопкой: "Запуск от имени администратора". & pause & exit /b 1)
netsh advfirewall firewall delete rule name="Finance app 8501" >nul 2>nul
netsh advfirewall firewall add rule name="Finance app 8501" dir=in action=allow protocol=TCP localport=8501 profile=private
echo Готово: порт 8501 открыт только для частной (домашней) сети.
pause
