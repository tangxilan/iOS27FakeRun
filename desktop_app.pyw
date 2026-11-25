"""Native Windows desktop GUI launcher for iOS27FakeRun (WebView2 + SelectorEventLoop backend)."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()

IS_FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent

if not IS_FROZEN and str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# If launched from source via system Python instead of .venv, re-launch inside .venv.
if not IS_FROZEN and sys.platform == "win32" and not os.environ.get("IOS27FAKERUN_BOOTSTRAPPED"):
    VENV_DIR = BASE_DIR / ".venv"
    if VENV_DIR.exists():
        try:
            in_venv = Path(sys.prefix).resolve() == VENV_DIR.resolve()
        except OSError:
            in_venv = False
        if not in_venv:
            exe_name = Path(sys.executable).name.lower()
            target_name = "pythonw.exe" if "pythonw" in exe_name else "python.exe"
            target_py = VENV_DIR / "Scripts" / target_name
            if not target_py.exists():
                target_py = VENV_DIR / "Scripts" / "pythonw.exe"
            if target_py.exists():
                env = os.environ.copy()
                env["IOS27FAKERUN_BOOTSTRAPPED"] = "1"
                subprocess.Popen(
                    [str(target_py), str(Path(__file__).resolve()), *sys.argv[1:]],
                    cwd=str(BASE_DIR),
                    env=env,
                )
                sys.exit(0)

from backend.paths import APP_VERSION, get_data_dir

DATA_DIR = get_data_dir()
LOG_FILE = DATA_DIR / "desktop.log"

# pythonw.exe / PyInstaller windowed EXEs set sys.stdout / sys.stderr to None on Windows,
# which crashes libraries calling .isatty() or writing to stdio unless redirected first.
if sys.stdout is None or sys.stderr is None:
    _log_fp = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fp
    if sys.stderr is None:
        sys.stderr = _log_fp

import argparse
import asyncio
import ctypes
import shutil
import socket
import threading
import time
import traceback
import urllib.request
import webbrowser

# Must be set BEFORE importing pymobiledevice3 / uvicorn on Windows so
# pmd-pytcp and DTX strip-ssl sockets run on SelectorEventLoop instead of ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(line)
    except OSError:
        pass


def _show_error_box(title: str, text: str) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
        except Exception:
            pass


def _pick_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def _wait_for_health(url: str, timeout_s: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _find_edge_or_chrome() -> str | None:
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


def main() -> None:
    import uvicorn
    from backend.api import create_app

    parser = argparse.ArgumentParser(description="iOS27FakeRun 桌面图形界面")
    parser.add_argument("--lan", action="store_true", help="同时允许同一局域网下的安卓手机访问控制")
    parser.add_argument("--port", type=int, default=8765, help="本地服务端口")
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    port = _pick_port(args.port)
    _log(f"Starting backend v{APP_VERSION} on {host}:{port} (frozen={IS_FROZEN})")
    app = create_app(server_port=port)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        loop="asyncio",
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _run_backend() -> None:
        try:
            if sys.platform == "win32":
                loop = asyncio.SelectorEventLoop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(server.serve())
                finally:
                    loop.close()
            else:
                asyncio.run(server.serve())
        except Exception as exc:
            _log(f"Backend error: {exc!r}")

    backend_thread = threading.Thread(target=_run_backend, name="ios27fakerun-backend", daemon=True)
    backend_thread.start()

    local_url = f"http://127.0.0.1:{port}"
    healthy = _wait_for_health(f"{local_url}/api/health", timeout_s=12.0)
    _log(f"Health check result: {healthy}")

    try:
        import webview  # type: ignore[import-untyped]

        title = f"iOS27FakeRun v{APP_VERSION} — iOS 27 免越狱校园跑 & 定点定位打卡"
        if args.lan:
            title += " [局域网遥控已开启]"
        _log("Launching pywebview edgechromium window")
        webview.create_window(
            title=title,
            url=local_url,
            width=1380,
            height=880,
            min_size=(960, 640),
            resizable=True,
            text_select=True,
        )
        webview.start(gui="edgechromium")
        _log("pywebview window closed normally")
    except Exception as exc:
        _log(f"pywebview failed ({exc!r}), falling back to Edge/Chrome app window")
        exe = _find_edge_or_chrome()
        if exe:
            profile_dir = DATA_DIR / "edge_app_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [
                    exe,
                    f"--app={local_url}",
                    f"--user-data-dir={profile_dir}",
                    "--window-size=1380,880",
                ]
            )
            proc.wait()
        else:
            webbrowser.open(local_url)
            try:
                while backend_thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        server.should_exit = True
        backend_thread.join(timeout=3.0)
        _log("Exited cleanly")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        tb = traceback.format_exc()
        _log(f"Fatal startup error:\n{tb}")
        _show_error_box(
            "iOS27FakeRun 启动失败",
            f"程序启动时发生错误：{exc}\n\n详细日志已保存至：\n{LOG_FILE}",
        )
        sys.exit(1)
