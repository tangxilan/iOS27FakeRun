"""Build standalone Windows EXE bundle and release ZIP for iOS27FakeRun."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Build iOS27FakeRun Windows EXE + Release ZIP")
    parser.add_argument("--version", default="v1.1.0", help="Release version tag (e.g. v1.1.0)")
    args = parser.parse_args()

    version = args.version.strip() or "v1.1.0"
    dist_app = ROOT / "dist" / "iOS27FakeRun"
    build_dir = ROOT / "build"

    if dist_app.exists():
        shutil.rmtree(dist_app)
    if build_dir.exists():
        shutil.rmtree(build_dir)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "iOS27FakeRun.spec"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    # Write pure-ASCII CRLF LAN launcher inside the packaged app folder
    lan_bat = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0iOS27FakeRun.exe\" --lan\r\n"
    ).encode("ascii")
    (dist_app / "局域网远程启动.bat").write_bytes(lan_bat)
    shutil.copy2(ROOT / "README.md", dist_app / "README.md")
    shutil.copy2(ROOT / "LICENSE", dist_app / "LICENSE")

    zip_path = ROOT / "dist" / f"iOS27FakeRun-{version}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in sorted(dist_app.rglob("*")):
            if file_path.is_file():
                arcname = Path("iOS27FakeRun") / file_path.relative_to(dist_app)
                zf.write(file_path, arcname.as_posix())

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Built EXE: {dist_app / 'iOS27FakeRun.exe'}")
    print(f"Built ZIP: {zip_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
