@echo off
cd /d "%~dp0"

if not exist .env (
    copy .env.example .env
    echo [ChaseBase] .env created. Please fill in API_KEY and restart.
    notepad .env
    exit /b
)

if not exist .venv (
    echo [ChaseBase] Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate

echo [ChaseBase] Installing dependencies...
pip install -r requirements.txt -q

:: ── [1/2] 启动本地 LLM 代理（后台窗口）──────────────────────────
echo [ChaseBase] Starting LLM proxy at http://127.0.0.1:11434 ...
start "ChaseBase LLM Proxy" /min python proxy_server.py

:: 等待代理就绪
timeout /t 2 /nobreak > nul

:: ── [2/2] 启动 ChaseBase 主应用 ──────────────────────────────────
echo [ChaseBase] Starting app at http://127.0.0.1:8000
start "" powershell -NoProfile -Command "Start-Sleep 3; Start-Process 'http://127.0.0.1:8000'"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app

pause
