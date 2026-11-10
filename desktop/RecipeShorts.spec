# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Recipe Shorts desktop app.
# Run from the repository root:
#   pyinstaller --noconfirm --clean desktop/RecipeShorts.spec

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPECDIR = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPECDIR)

datas, binaries, hiddenimports = [], [], []


def _gather(pkg):
    try:
        return collect_all(pkg)
    except Exception:
        return [], [], []


for pkg in (
    "gradio",
    "gradio_client",
    "safehttpx",
    "groovy",
    "fastapi",
    "starlette",
    "uvicorn",
    "huggingface_hub",
    "httpx",
    "httpcore",
    "anyio",
    "jinja2",
    "orjson",
    "pydantic",
    "pydantic_core",
    "typer",
    "rich",
    "yaml",
    "pillow",
    "numpy",
    "cv2",
):
    d, b, h = _gather(pkg)
    datas += d
    binaries += b
    hiddenimports += h

try:
    datas += collect_data_files("cv2")
except Exception:
    pass

try:
    datas += collect_data_files("recipe_shorts")
except Exception:
    pass
hiddenimports += collect_submodules("recipe_shorts")
hiddenimports += ["app_ui", "desktop.app_ui"]

fonts = os.path.join(ROOT, "recipe_shorts", "fonts")
if os.path.isdir(fonts):
    datas.append((fonts, os.path.join("recipe_shorts", "fonts")))

bindir = os.path.join(SPECDIR, "bin")
if os.path.isdir(bindir):
    for name in ("ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"):
        path = os.path.join(bindir, name)
        if os.path.isfile(path):
            # Lands under _internal/ (and we also copy next to the exe in CI).
            binaries.append((path, "."))

block_cipher = None

a = Analysis(
    [os.path.join(SPECDIR, "launch.py")],
    pathex=[ROOT, SPECDIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["faster_whisper", "torch", "torchaudio", "whisper", "yt_dlp", "matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RecipeShorts",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RecipeShorts",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Recipe Shorts.app",
        icon=None,
        bundle_identifier="com.whatsinmypan.recipeshorts",
        info_plist={
            "CFBundleName": "Recipe Shorts",
            "CFBundleDisplayName": "Recipe Shorts",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Recipe Shorts does not record the microphone; it only uses videos you choose.",
        },
    )
