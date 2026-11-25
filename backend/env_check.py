"""Windows runtime environment detection and safe external URL validation."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

from .paths import APP_VERSION, is_frozen

APPLE_DEVICES_STORE_URL = "https://apps.microsoft.com/detail/9np83lwlpz9k"
APPLE_DEVICES_STORE_URI = "ms-windows-store://pdp/?productid=9NP83LWLPZ9K"
ITUNES_WIN64_URL = "https://www.apple.com/itunes/download/win64"
WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

_WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_CREATE_NO_WINDOW = 0x08000000


def _query_windows_service(service_name: str) -> tuple[bool, bool]:
    """Return (installed, running) for a Windows service without flashing any console."""
    if sys.platform != "win32":
        return False, False

    installed = False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{service_name}",
        ):
            installed = True
    except OSError:
        installed = False

    running = False
    try:
        proc = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_CREATE_NO_WINDOW,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            installed = True
            running = "RUNNING" in out.upper()
    except Exception:
        pass
    return installed, running


def _detect_webview2_version() -> str:
    """Return installed Edge WebView2 Runtime version string, or empty if missing."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        locations = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
        ]
        for hive, subkey in locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "pv")
                    if val:
                        return str(val)
            except OSError:
                continue
    except Exception:
        pass
    return ""


def check_environment() -> dict[str, Any]:
    """Inspect host Windows dependencies required for iOS location simulation."""
    apple_installed, apple_running = _query_windows_service("Apple Mobile Device Service")
    if apple_running:
        apple_status = "ok"
        apple_label = "Apple 驱动服务运行正常"
        apple_detail = "已检测到 Apple Mobile Device Service 正在运行，可以正常识别 USB 连接的 iPhone。"
    elif apple_installed:
        apple_status = "stopped"
        apple_label = "Apple 驱动服务未启动"
        apple_detail = (
            "系统已安装 Apple 驱动，但 Apple Mobile Device Service 当前未运行。"
            "可在 Windows“服务”中启动它，或重装微软商店的“Apple 设备”/ iTunes。"
        )
    else:
        apple_status = "missing"
        apple_label = "缺少 Apple 手机驱动"
        apple_detail = (
            "未检测到 Apple Mobile Device Service。请先安装微软商店的【Apple 设备】或【iTunes】，"
            "否则电脑无法识别 iPhone。"
        )

    bonjour_installed, bonjour_running = _query_windows_service("Bonjour Service")
    if bonjour_running:
        bonjour_status = "ok"
        bonjour_label = "Bonjour 无线发现服务正常"
    elif bonjour_installed:
        bonjour_status = "stopped"
        bonjour_label = "Bonjour 服务未运行（仅影响 Wi-Fi 无线连接）"
    else:
        bonjour_status = "missing"
        bonjour_label = "未安装 Bonjour（USB 连接不受影响，无线连接需安装 iTunes）"

    wv2_version = _detect_webview2_version()
    if wv2_version:
        wv2_status = "ok"
        wv2_label = f"Edge WebView2 正常 ({wv2_version})"
    else:
        wv2_status = "missing"
        wv2_label = "未检测到 Edge WebView2 Runtime（如使用浏览器模式可忽略）"

    return {
        "ready": apple_status == "ok",
        "apple_driver": {
            "status": apple_status,
            "label": apple_label,
            "detail": apple_detail,
            "store_url": APPLE_DEVICES_STORE_URL,
            "store_uri": APPLE_DEVICES_STORE_URI,
            "itunes_url": ITUNES_WIN64_URL,
        },
        "bonjour": {
            "status": bonjour_status,
            "label": bonjour_label,
        },
        "webview2": {
            "status": wv2_status,
            "version": wv2_version,
            "label": wv2_label,
            "download_url": WEBVIEW2_DOWNLOAD_URL,
        },
        "runtime": {
            "version": APP_VERSION,
            "frozen": is_frozen(),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
    }


def is_allowed_external_url(url: str) -> bool:
    """Allow only safe HTTPS or Microsoft Store deep-link URLs to be opened externally."""
    if not isinstance(url, str):
        return False
    stripped = url.strip()
    if not stripped or len(stripped) > 512:
        return False
    parsed = urlparse(stripped)
    if parsed.scheme == "https" and bool(parsed.netloc):
        return True
    if parsed.scheme == "ms-windows-store" and bool(parsed.netloc):
        return True
    return False
