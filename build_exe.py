"""
打包脚本：将 launcher.py 编译为 launcher.exe
运行方式：python build_exe.py

输出：dist/launcher.exe（单文件，双击即用）
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    # 确认 PyInstaller 已安装
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller", "-q"],
            check=True,
        )

    print("开始打包 launcher.exe ...")
    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--onefile",                  # 单文件
            "--noconsole",                # 不显示黑色控制台窗口（有自己的窗口）
            "--name", "ChaseBase",        # 输出文件名：ChaseBase.exe
            "--add-data", f"{ROOT / 'proxy_server.py'};.",   # 把 proxy_server.py 打进去
            "--hidden-import", "uvicorn.logging",
            "--hidden-import", "uvicorn.loops",
            "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols",
            "--hidden-import", "uvicorn.protocols.http",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan",
            "--hidden-import", "uvicorn.lifespan.on",
            str(ROOT / "launcher.py"),
        ],
        cwd=str(ROOT),
    )

    if result.returncode == 0:
        exe = ROOT / "dist" / "ChaseBase.exe"
        print(f"\n✅ 打包成功：{exe}")
        print("将 ChaseBase.exe 复制到项目根目录，双击即可一键启动。")
        print("\n注意：.exe 需要与 .venv、.env、app/ 等文件夹在同一目录下运行。")
    else:
        print("\n❌ 打包失败，请检查上方错误信息。")


if __name__ == "__main__":
    main()
