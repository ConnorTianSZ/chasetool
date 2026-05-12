"""
ChaseBase 一键启动器
====================
双击此脚本（或编译后的 launcher.exe）即可启动：
  1. LLM 本地代理 (proxy_server.py，后台)
  2. ChaseBase 主应用 (uvicorn，前台)
  3. 自动打开浏览器

打包命令：
  python build_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# 项目根目录（.exe 所在目录，或脚本所在目录）
if getattr(sys, "frozen", False):
    # PyInstaller 打包后，exe 所在目录
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ENV_FILE    = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

APP_HOST = "127.0.0.1"
APP_PORT = 8000
PROXY_PORT = 11434


def find_python() -> str:
    """优先用 .venv，没有就用系统 Python。"""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def ensure_env():
    """如果没有 .env，从 .env.example 复制并提示填写。"""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            import shutil
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
        else:
            ENV_FILE.write_text("API_KEY=\nAPI_BASE=http://127.0.0.1:11434/v1\nLLM_PROVIDER=openai\n")

        # 用记事本打开让用户填写
        subprocess.Popen(["notepad.exe", str(ENV_FILE)])
        input("\n请在打开的记事本中填写 API_KEY，保存后按回车继续...")


def ensure_venv():
    """如果没有 .venv 就创建并安装依赖。"""
    if not VENV_PYTHON.exists():
        print("[启动器] 首次运行，正在创建虚拟环境...")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        req = ROOT / "requirements.txt"
        if req.exists():
            print("[启动器] 安装依赖（首次约需 1-2 分钟）...")
            subprocess.run(
                [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(req), "-q"],
                check=True,
            )


def start_proxy(python: str) -> subprocess.Popen:
    """后台启动 proxy_server.py，返回进程对象。"""
    proxy_script = ROOT / "proxy_server.py"
    print(f"[启动器] 正在启动 LLM 代理 → http://127.0.0.1:{PROXY_PORT}/v1")
    proc = subprocess.Popen(
        [python, str(proxy_script)],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE,  # Windows：新窗口（最小化）
    )
    return proc


def start_app(python: str) -> subprocess.Popen:
    """前台启动 ChaseBase uvicorn 服务。"""
    print(f"[启动器] 正在启动 ChaseBase → http://{APP_HOST}:{APP_PORT}")
    proc = subprocess.Popen(
        [
            python, "-m", "uvicorn",
            "app.main:app",
            "--host", APP_HOST,
            "--port", str(APP_PORT),
            "--reload",
            "--reload-dir", "app",
        ],
        cwd=str(ROOT),
    )
    return proc


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """轮询端口直到可连接或超时。"""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    os.chdir(ROOT)

    print("=" * 50)
    print("  ChaseBase 启动器")
    print("=" * 50)

    ensure_env()
    ensure_venv()
    python = find_python()

    # 1. 启动代理
    proxy_proc = start_proxy(python)
    print(f"[启动器] 等待代理就绪...")
    if wait_for_port("127.0.0.1", PROXY_PORT, timeout=10):
        print(f"[启动器] ✓ 代理已就绪")
    else:
        print(f"[启动器] ⚠ 代理启动超时，继续启动主应用...")

    # 2. 启动主应用
    app_proc = start_app(python)

    # 3. 等待主应用就绪后打开浏览器
    print(f"[启动器] 等待应用就绪...")
    if wait_for_port(APP_HOST, APP_PORT, timeout=30):
        print(f"[启动器] ✓ 应用已就绪，打开浏览器...")
        webbrowser.open(f"http://{APP_HOST}:{APP_PORT}")
    else:
        print(f"[启动器] ⚠ 应用启动超时，请手动打开 http://{APP_HOST}:{APP_PORT}")

    # 4. 等待主应用退出
    try:
        app_proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[启动器] 正在关闭代理...")
        proxy_proc.terminate()


if __name__ == "__main__":
    main()
