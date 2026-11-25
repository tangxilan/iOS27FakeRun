"""Runtime path helpers supporting both source execution and PyInstaller frozen builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_VERSION = "1.1.0"


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_resource_dir() -> Path:
    """Return directory containing bundled read-only assets (e.g., static/)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Return writable directory for local user data (routes, pins, logs)."""
    if is_frozen():
        exe_data = Path(sys.executable).resolve().parent / "data"
        try:
            exe_data.mkdir(parents=True, exist_ok=True)
            probe = exe_data / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return exe_data
        except OSError:
            local_app = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "iOS27FakeRun" / "data"
            local_app.mkdir(parents=True, exist_ok=True)
            return local_app

    src_data = Path(__file__).resolve().parent.parent / "data"
    src_data.mkdir(parents=True, exist_ok=True)
    return src_data
