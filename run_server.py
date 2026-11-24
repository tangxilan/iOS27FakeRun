"""Windows SelectorEventLoop entrypoint for iOS27FakeRun."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32" and VENV_DIR.exists() and not os.environ.get("IOS27FAKERUN_BOOTSTRAPPED"):
    try:
        in_venv = Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        in_venv = False
    if not in_venv:
        target_py = VENV_DIR / "Scripts" / "python.exe"
        if target_py.exists():
            env = os.environ.copy()
            env["IOS27FAKERUN_BOOTSTRAPPED"] = "1"
            raise SystemExit(subprocess.call([str(target_py), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=str(BASE_DIR), env=env))

# Must be set BEFORE importing pymobiledevice3 / uvicorn on Windows so
# pmd-pytcp and DTX strip-ssl sockets run on SelectorEventLoop instead of ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

from backend.api import create_app


def _find_browser_exe() -> str | None:
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _open_app_window(url: str) -> None:
    time.sleep(0.9)
    exe = _find_browser_exe()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1380,880"])
            return
        except Exception:
            pass
    webbrowser.open(url)


def _get_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="iOS27FakeRun 本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（手机远程访问请用 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--open-app", action="store_true", help="启动后自动打开桌面应用窗口")
    args = parser.parse_args()

    app = create_app(server_port=args.port)
    local_url = f"http://127.0.0.1:{args.port}"
    print("============================================================")
    print("  iOS27FakeRun 已启动 (支持 iOS 17 ~ iOS 27+ 免越狱虚拟定位)")
    print(f"  本机访问地址: {local_url}")
    if args.host == "0.0.0.0":
        lan_ip = _get_lan_ip()
        if lan_ip:
            print(f"  安卓/局域网手机访问地址: http://{lan_ip}:{args.port}")
    print("  按 Ctrl+C 停止服务")
    print("============================================================")

    if args.open_app:
        threading.Thread(target=_open_app_window, args=(local_url,), daemon=True).start()

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        loop="asyncio",
        log_level="info",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
