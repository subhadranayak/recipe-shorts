"""Gradio UI for the Recipe Shorts desktop app.

Defaults to full quality (1080x1920 when the source is 1080p or higher).
Captions / Whisper stay off unless the user opts in. This file is only used
by the desktop launcher — the website on Render keeps using app.py.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid

import gradio as gr

from recipe_shorts.pipeline import render_shots_file, run

JOBS = os.path.join(tempfile.gettempdir(), "recipe_shorts_jobs")
KEEP_HOURS = 24
# "auto" -> 1080x1920 when the source height is >= 1080, otherwise 720x1280.
DEFAULT_RESOLUTION = os.environ.get("DEFAULT_RESOLUTION", "auto")

STAGES = {
    "1/4": (0.05, "Listening to the voice-over..."),
    "2/4": (0.25, "Watching the video..."),
    "3/4": (0.5, "Picking the best shots..."),
    "4/4": (0.7, "Making the vertical Short..."),
}

CSS = """
.big-btn button, button.big-btn {font-size: 1.25rem !important; padding: 18px !important; min-height: 64px}
.gradio-container {max-width: 760px !important; margin: auto}
"""


def _cleanup():
    os.makedirs(JOBS, exist_ok=True)
    now = time.time()
    for d in os.listdir(JOBS):
        p = os.path.join(JOBS, d)
        try:
            if now - os.path.getmtime(p) > KEEP_HOURS * 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def _path(f):
    return f if isinstance(f, str) else getattr(f, "name", None)


def make_short(video, title, hook, use_voice, music, resolution, progress=gr.Progress()):
    if not video:
        raise gr.Error("Please choose a video first.")
    _cleanup()
    job = os.path.join(JOBS, uuid.uuid4().hex[:10])
    os.makedirs(job)
    src = _path(video)
    ext = os.path.splitext(src)[1].lower() or ".mp4"
    local = os.path.join(job, "input" + ext)
    shutil.copy(src, local)
    progress(0.02, desc="Starting...")

    def log(msg):
        msg = str(msg)
        for k, (frac, desc) in STAGES.items():
            if msg.startswith(k):
                progress(frac, desc=desc)
        print(msg, flush=True)

    try:
        run(
            local,
            os.path.join(job, "out"),
            title=(title or "").strip(),
            hook=(hook or "").strip(),
            mode="story",
            captions=bool(use_voice),
            whisper_model="tiny",
            music=_path(music),
            resolution=resolution or DEFAULT_RESOLUTION,
            log=log,
        )
    except SystemExit as e:
        raise gr.Error(str(e))
    out = os.path.join(job, "out", "story_short.mp4")
    shots = open(os.path.join(job, "out", "shots.json"), encoding="utf-8").read()
    progress(1.0, desc="Done!")
    return out, out, shots, job


def rerender(shots_text, job, music, progress=gr.Progress()):
    if not job or not os.path.isdir(job):
        raise gr.Error("Make a Short first.")
    try:
        json.loads(shots_text)
    except ValueError as e:
        raise gr.Error(f"The shot list isn't valid JSON: {e}")
    p = os.path.join(job, "out", "shots.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(shots_text)
    progress(0.3, desc="Re-cutting the Short...")
    render_shots_file(p, music=_path(music))
    out = os.path.join(job, "out", "story_short.mp4")
    return out, out


def build_demo():
    with gr.Blocks(title="Recipe Shorts", css=CSS, delete_cache=(1800, KEEP_HOURS * 3600)) as demo:
        gr.Markdown(
            "# 🍳 Recipe Shorts\n"
            "Turn a long recipe video into a vertical Short: "
            "**finished dish → prep → cooking → final dish → taste → \"full recipe on my channel\"**.\n\n"
            "This app runs on your computer, so Shorts are made at high quality "
            "(**1080×1920** when your video is 1080p or higher), including colour "
            "correction for HDR phone footage."
        )
        video = gr.File(
            label="1. Your recipe video (use the original file, not a YouTube download)",
            file_types=["video"],
            type="filepath",
        )
        title = gr.Textbox(
            label="2. Video title",
            placeholder="Gobi 65 Recipe | This Is What Cauliflower Was Missing!",
        )
        with gr.Accordion("More options (optional)", open=False):
            hook = gr.Textbox(label="Hook text on the first shot (default: made from the title)")
            resolution = gr.Radio(
                choices=[
                    ("Best for this video (1080p if your video is 1080p or higher)", "auto"),
                    ("Always 1080p", "1080"),
                    ("720p (faster, smaller file)", "720"),
                ],
                value=DEFAULT_RESOLUTION if DEFAULT_RESOLUTION in ("auto", "720", "1080") else "auto",
                label="Quality of the Short",
            )
            use_voice = gr.Checkbox(
                label="Also listen to the voice-over to pick shots (slower; leave off unless you need it)",
                value=False,
            )
            music = gr.File(label="Background music (mp3/m4a/wav)", file_types=["audio"], type="filepath")
        go = gr.Button("3. Make Short", variant="primary", size="lg", elem_classes="big-btn")
        gr.Markdown(
            "Keep this page and the small Recipe Shorts window open until it finishes. "
            "A 6-minute recipe usually takes a few minutes on a regular computer."
        )
        preview = gr.Video(label="Your Short", height=560)
        download = gr.File(label="4. Download")
        job = gr.State("")
        with gr.Accordion("Edit the shot list (optional)", open=False):
            gr.Markdown(
                "Each shot has `start`/`end` (seconds in your video), a `phase` "
                "(hero, prep, cook, finale, taste) and an optional `label` like \"Fry\". "
                "Change times, delete or reorder shots, then re-cut."
            )
            shots = gr.Code(label="shots.json", language="json")
            again = gr.Button("Re-cut with my shot list", size="lg", elem_classes="big-btn")

        go.click(make_short, [video, title, hook, use_voice, music, resolution], [preview, download, shots, job])
        again.click(rerender, [shots, job, music], [preview, download])
    return demo
