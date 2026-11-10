# Recipe Shorts desktop app

Double-click apps for Windows and macOS. No Terminal, no Python install,
no setup scripts. The app starts a local server, opens your browser, and
makes Shorts at full quality (1080×1920 when the source is 1080p or higher,
with HDR → SDR tone mapping). ffmpeg and ffprobe are bundled.

This folder is not used by the Render website. Dockerfile only copies
`app.py` and `recipe_shorts/`.

## For users

Download the zip for your computer from the GitHub Release, then follow
`FIRST_OPEN.txt` (Windows: More info → Run anyway; Mac: right-click → Open).

## For developers

```bash
# from the repository root, on the OS you want to package
python3 -m pip install -r desktop/requirements-build.txt
python3 desktop/fetch_ffmpeg.py
pyinstaller --noconfirm --clean desktop/RecipeShorts.spec
```

On Windows that produces `dist/RecipeShorts/RecipeShorts.exe`.
On macOS it produces `dist/Recipe Shorts.app`.

```bash
# smoke test (needs a >= 40s video for the full auto pipeline;
# --smoke itself uses a forced shot list and works on shorter clips too)
./dist/RecipeShorts/RecipeShorts --smoke input.mp4 out.mp4
./dist/RecipeShorts/RecipeShorts --serve --port 8765
```

The GitHub Actions workflow `.github/workflows/desktop.yml` builds both
OSes, runs those checks, and attaches the zips to the `v1.0` release.
