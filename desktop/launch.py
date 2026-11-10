#!/usr/bin/env python3
"""Recipe Shorts desktop launcher.

Double-click the app: a local server starts, your browser opens the tool,
and a small window stays open so you can quit.

Developer / CI flags:
  --serve --port N     start the web UI only (no window, no browser)
  --headless           same as normal start, but no window/browser; write the
                       port to $RECIPE_SHORTS_PORT_FILE (default: port.txt)
  --smoke IN OUT       render a high-quality test Short with bundled ffmpeg, then exit
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> str:
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _repo_root() -> str:
    if _frozen():
        return _exe_dir()
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup_path() -> None:
    if not _frozen():
        root = _repo_root()
        if root not in sys.path:
            sys.path.insert(0, root)
        # Allow "import app_ui" when running from source.
        desk = os.path.dirname(os.path.abspath(__file__))
        if desk not in sys.path:
            sys.path.insert(0, desk)


def _setup_ffmpeg_path() -> None:
    from recipe_shorts.media import ensure_ffmpeg_on_path
    ensure_ffmpeg_on_path()


def _patch_opencv_data() -> None:
    """Make sure the bundled Haar cascade is visible after PyInstaller packing."""
    try:
        import cv2
    except Exception:
        return
    name = "haarcascade_frontalface_default.xml"
    try:
        current = getattr(cv2, "data", None)
        if current is not None and os.path.isfile(os.path.join(current.haarcascades, name)):
            return
    except Exception:
        pass
    roots = []
    if _frozen():
        roots.append(getattr(sys, "_MEIPASS", ""))
        roots.append(_exe_dir())
        roots.append(os.path.join(_exe_dir(), "_internal"))
    for root in roots:
        if not root:
            continue
        for rel in ("cv2/data", "cv2/data/haarcascades", "data/haarcascades"):
            folder = os.path.join(root, rel)
            if os.path.isfile(os.path.join(folder, name)):
                if not hasattr(cv2, "data"):
                    class _Data:
                        haarcascades = folder + os.sep
                    cv2.data = _Data()
                else:
                    cv2.data.haarcascades = folder if folder.endswith(os.sep) else folder + os.sep
                return


def _pick_port(preferred: int | None = None) -> int:
    if preferred:
        return preferred
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout: float = 60.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if 200 <= getattr(r, "status", 200) < 500:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _write_port_file(port: int) -> None:
    path = os.environ.get("RECIPE_SHORTS_PORT_FILE") or os.path.join(os.getcwd(), "recipe-shorts-port.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(port))
    print(f"port file: {path}", flush=True)


def start_server(port: int, inbrowser: bool = False):
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    os.environ.setdefault("GRADIO_SERVER_NAME", "127.0.0.1")
    from app_ui import build_demo, CSS  # noqa: WPS433  (local package)

    # CSS is already applied on Blocks; imported only so packaging traces it.
    _ = CSS
    demo = build_demo()
    demo.queue(default_concurrency_limit=1).launch(
        server_name="127.0.0.1",
        server_port=port,
        inbrowser=inbrowser,
        prevent_thread_lock=True,
        max_file_size="2gb",
        show_error=True,
    )
    return demo


def _show_window(url: str) -> None:
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("Recipe Shorts")
    root.geometry("520x340")
    root.minsize(420, 240)

    tk.Label(root, text="Recipe Shorts is running", font=("Helvetica", 16, "bold")).pack(pady=(16, 6))
    tk.Label(
        root,
        text="A browser window should have opened with the tool.\n"
             "Keep this window open while you make a Short.\n"
             "Click Quit when you are finished.",
        justify="center",
    ).pack(pady=4)
    tk.Label(root, text=url, fg="#0b57d0").pack(pady=4)

    log = scrolledtext.ScrolledText(root, height=8, state="normal", wrap="word")
    log.pack(fill="both", expand=True, padx=12, pady=8)
    log.insert("end", f"Opened {url}\nClose this window or click Quit to stop the app.\n")
    log.configure(state="disabled")

    def open_again():
        webbrowser.open(url)

    def quit_app():
        root.destroy()
        os._exit(0)

    btn = tk.Frame(root)
    btn.pack(pady=(0, 12))
    tk.Button(btn, text="Open in browser", command=open_again, width=18).pack(side="left", padx=6)
    tk.Button(btn, text="Quit", command=quit_app, width=12).pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def run_ui(port: int, headless: bool) -> int:
    url = f"http://127.0.0.1:{port}"
    print(f"Starting Recipe Shorts at {url}", flush=True)
    start_server(port, inbrowser=False)
    if not _wait_http(url, timeout=90):
        print("The app started but the page did not become ready in time.", flush=True)
        return 1
    _write_port_file(port)
    if headless:
        print("headless: ready", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    webbrowser.open(url)
    try:
        _show_window(url)
    except Exception:
        # If Tk is missing, keep the server running in the terminal.
        print("Could not open the small status window. The tool is still running.", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        traceback.print_exc()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    return 0


def run_smoke(src: str, out: str) -> int:
    """Render a forced-shot Short so CI can check bundled ffmpeg + high quality."""
    from recipe_shorts import media
    from recipe_shorts.pipeline import render_shots_file, choose_output_size

    media.require_ffmpeg()
    if not os.path.isfile(src):
        print(f"smoke: input not found: {src}", flush=True)
        return 2
    info = media.probe(src)
    import shutil as _sh
    print(
        f"smoke: input {info['width']}x{info['height']}  {info['duration']:.1f}s  "
        f"ffmpeg={_sh.which('ffmpeg')}  ffprobe={_sh.which('ffprobe')}",
        flush=True,
    )
    out_w, out_h = choose_output_size(info["height"], "auto")
    print(f"smoke: target {out_w}x{out_h}", flush=True)

    duration = info["duration"]
    def clamp(a, b):
        a = max(0.0, min(a, max(0.0, duration - 0.4)))
        b = max(a + 0.3, min(b, duration))
        return round(a, 2), round(b, 2)

    # Five short forced shots so framing quality cannot drop them.
    marks = [
        (0.5, 3.0, "hero", None),
        (duration * 0.20, duration * 0.20 + 2.5, "prep", "Prep"),
        (duration * 0.45, duration * 0.45 + 2.5, "cook", "Cook"),
        (duration * 0.70, duration * 0.70 + 2.5, "finale", "Finish"),
        (duration * 0.85, duration * 0.85 + 2.5, "taste", None),
    ]
    shots = []
    for a, b, phase, label in marks:
        s, e = clamp(a, b)
        item = {"start": s, "end": e, "phase": phase, "force": True}
        if label:
            item["label"] = label
        shots.append(item)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    plan_path = os.path.splitext(out)[0] + ".shots.json"
    plan = {
        "source": os.path.abspath(src),
        "title": "Smoke Test",
        "hook": "Smoke Test Short",
        "mode": "story",
        "file": os.path.basename(out),
        "output_size": [out_w, out_h],
        "shots": shots,
        "endcard": True,
    }
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    log_lines = []

    def log(msg):
        print(msg, flush=True)
        log_lines.append(str(msg))

    render_shots_file(plan_path, out=os.path.abspath(out), src=os.path.abspath(src), log=log)
    if not os.path.isfile(out) or os.path.getsize(out) < 1000:
        print("smoke: FAIL output missing or too small", flush=True)
        return 3
    out_info = media.probe(out)
    print(
        f"smoke: output {out_info['width']}x{out_info['height']}  "
        f"{out_info['duration']:.1f}s  {os.path.getsize(out)} bytes",
        flush=True,
    )
    if out_info["width"] != out_w or out_info["height"] != out_h:
        print(f"smoke: FAIL expected {out_w}x{out_h}", flush=True)
        return 4
    result = os.environ.get("RECIPE_SHORTS_SMOKE_LOG") or os.path.join(os.getcwd(), "smoke-result.txt")
    with open(result, "w", encoding="utf-8") as f:
        f.write("PASS\n")
        f.write(f"{out_info['width']}x{out_info['height']}\n")
        f.write(f"duration={out_info['duration']:.2f}\n")
        f.write(f"size={os.path.getsize(out)}\n")
    print(f"smoke: PASS  ({result})", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Avoid thread over-subscription; leave cores free for ffmpeg on a laptop.
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    parser = argparse.ArgumentParser(description="Recipe Shorts desktop app")
    parser.add_argument("--serve", action="store_true", help="start the web UI only")
    parser.add_argument("--headless", action="store_true", help="no browser, no status window")
    parser.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    parser.add_argument("--smoke", nargs=2, metavar=("IN", "OUT"), help="render a test Short and exit")
    args = parser.parse_args(argv)

    if os.environ.get("RECIPE_SHORTS_HEADLESS", "").lower() in ("1", "true", "yes"):
        args.headless = True

    _setup_path()
    _setup_ffmpeg_path()
    _patch_opencv_data()

    if args.smoke:
        return run_smoke(args.smoke[0], args.smoke[1])

    port = _pick_port(args.port or int(os.environ.get("PORT") or 0) or None)
    if args.serve:
        start_server(port, inbrowser=False)
        url = f"http://127.0.0.1:{port}"
        if not _wait_http(url, timeout=90):
            print("serve: page did not become ready", flush=True)
            return 1
        _write_port_file(port)
        print(f"serve: ready at {url}", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    return run_ui(port, headless=args.headless)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        log = os.environ.get("RECIPE_SHORTS_ERROR_LOG") or os.path.join(
            os.path.expanduser("~"), "RecipeShorts-error.txt"
        )
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                traceback.print_exc(file=f)
                f.write("\n")
        except Exception:
            pass
        raise
