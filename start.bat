@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [AI Gateway] 正在启动 http://127.0.0.1:8080 ...
py -m uvicorn app.main:app --host 127.0.0.1 --port 8080
pause
