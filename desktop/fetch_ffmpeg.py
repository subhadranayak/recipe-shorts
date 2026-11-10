#!/usr/bin/env python3
"""Download static ffmpeg + ffprobe into desktop/bin for the current platform.

Windows: BtbN GPL build (libx264, libass, zimg / zscale, tonemap).
macOS:   Martin Riedl 9.0.2 static build (same filters).
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile

# Pinned so CI builds stay reproducible.
BTBN_TAG = "autobuild-2026-11-09-12-42"
BTBN_ZIP = "ffmpeg-n9.0.2-48-gd4070a14ce-win64-gpl-9.0.zip"
BTBN_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{BTBN_TAG}/{BTBN_ZIP}"

# Martin Riedl release 9.0.2 (libass + zimg).
MRIEDL = {
    "arm64": {
        "ffmpeg": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
        "ffprobe": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffprobe.zip",
    },
    "x86_64": {
        "ffmpeg": "https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2/ffmpeg.zip",
        "ffprobe": "https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2/ffprobe.zip",
    },
}


def _download(url: str, dest: str) -> None:
    print(f"Downloading {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "recipe-shorts-desktop/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    print(f"  saved {os.path.getsize(dest)} bytes -> {dest}", flush=True)


def _extract_named(zip_path: str, names: set[str], dest_dir: str) -> list[str]:
    found = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = os.path.basename(info.filename)
            if base.lower() in names and not info.is_dir():
                target = os.path.join(dest_dir, base)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                found.append(target)
    return found


def fetch(dest: str, os_name: str | None = None, arch: str | None = None) -> None:
    os.makedirs(dest, exist_ok=True)
    os_name = (os_name or sys.platform).lower()
    machine = arch or platform.machine()
    work = tempfile.mkdtemp(prefix="ffetch_")
    try:
        if os_name.startswith("win"):
            zpath = os.path.join(work, "ffmpeg.zip")
            _download(BTBN_URL, zpath)
            found = _extract_named(zpath, {"ffmpeg.exe", "ffprobe.exe"}, dest)
            if len(found) < 2:
                raise SystemExit(f"Could not find ffmpeg.exe/ffprobe.exe in {BTBN_ZIP}")
        elif os_name == "darwin" or os_name == "macos":
            key = "arm64" if machine.lower() in ("arm64", "aarch64") else "x86_64"
            urls = MRIEDL[key]
            for tool, url in urls.items():
                zpath = os.path.join(work, f"{tool}.zip")
                _download(url, zpath)
                found = _extract_named(zpath, {tool}, dest)
                if not found:
                    # Some zips store the binary as just "ffmpeg" at the top, or as a nested folder.
                    with zipfile.ZipFile(zpath) as z:
                        members = [i for i in z.infolist() if not i.is_dir()]
                        if len(members) == 1:
                            target = os.path.join(dest, tool)
                            with z.open(members[0]) as src, open(target, "wb") as out:
                                shutil.copyfileobj(src, out)
                            found = [target]
                if not found:
                    raise SystemExit(f"Could not find {tool} in {url}")
        else:
            raise SystemExit(f"No pinned ffmpeg build for {os_name}/{machine}")
        for p in os.listdir(dest):
            path = os.path.join(dest, p)
            os.chmod(path, 0o755)
            print(f"  ready {path} ({os.path.getsize(path)} bytes)", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dest", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
    p.add_argument("--os", dest="os_name", default=None)
    p.add_argument("--arch", default=None)
    args = p.parse_args()
    fetch(args.dest, args.os_name, args.arch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
