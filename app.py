#!/usr/bin/env python3
"""Recipe Shorts - web page (Gradio). Works on a laptop and in Safari on an iPad/phone.

  python app.py         -> open http://127.0.0.1:7860
  (on a host like Render, set HOST=0.0.0.0 and PORT; default quality is 720p)

Upload a recipe video, type its title, tap "Make Short", wait, preview, download.
"""
import json
import os
import shutil
import tempfile
import time
import uuid

# Keep CPU/RAM use low on free 512 MB hosts.
for _k, _v in (
    ("OMP_NUM_THREADS", "1"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("CV2_NUM_THREADS", "1"),
    ("FFMPEG_THREADS", os.environ.get("FFMPEG_THREADS", "1")),
):
    os.environ.setdefault(_k, _v)

import gradio as gr

from recipe_shorts.pipeline import render_shots_file, run

JOBS = os.path.join(tempfile.gettempdir(), "recipe_shorts_jobs")
KEEP_HOURS = 2  # finished jobs are deleted after this (keeps a free host's disk clean)
DEFAULT_RESOLUTION = os.environ.get("DEFAULT_RESOLUTION", "720")


def _cleanup():
    os.makedirs(JOBS, exist_ok=True)
    now = time.time()
    for d in os.listdir(JOBS):
        p = os.path.join(JOBS, d)
        if now - os.path.getmtime(p) > KEEP_HOURS * 3600:
            shutil.rmtree(p, ignore_errors=True)


def _path(f):
    return f if isinstance(f, str) else getattr(f, "name", None)


STAGES = {"1/4": (0.05, "Listening to the voice-over..."), "2/4": (0.25, "Watching the video..."),
          "3/4": (0.5, "Picking the best shots..."), "4/4": (0.7, "Making the vertical Short...")}


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
        run(local, os.path.join(job, "out"), title=(title or "").strip(), hook=(hook or "").strip(),
            mode="story", captions=bool(use_voice), whisper_model="tiny", music=_path(music),
            resolution=resolution or DEFAULT_RESOLUTION, log=log)
    except SystemExit as e:  # friendly messages from the pipeline
        raise gr.Error(str(e))
    except MemoryError:
        raise gr.Error("The video was too heavy for this free server. Try a shorter clip, or keep Quality on 720p.")
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


CSS = """
.big-btn button, button.big-btn {font-size: 1.25rem !important; padding: 18px !important; min-height: 64px}
.gradio-container {max-width: 760px !important; margin: auto}
"""

with gr.Blocks(title="Recipe Shorts", delete_cache=(1800, KEEP_HOURS * 3600)) as demo:
    gr.Markdown("# 🍳 Recipe Shorts\nTurn a long recipe video into a vertical Short: "
                "**finished dish → prep → cooking → final dish → taste → \"full recipe on my channel\"**.")
    video = gr.File(label="1. Your recipe video (from Photos or Files)", file_types=["video"], type="filepath")
    title = gr.Textbox(label="2. Video title", placeholder="Gobi 65 Recipe | This Is What Cauliflower Was Missing!")
    with gr.Accordion("More options (optional)", open=False):
        hook = gr.Textbox(label="Hook text on the first shot (default: made from the title)")
        resolution = gr.Radio(
            choices=[("720p (faster, recommended)", "720"), ("1080p (sharper, needs more memory)", "1080")],
            value=DEFAULT_RESOLUTION if DEFAULT_RESOLUTION in ("720", "1080") else "720",
            label="Quality of the Short",
        )
        use_voice = gr.Checkbox(label="Also listen to the voice-over to pick shots (slower, uses more memory)", value=False)
        music = gr.File(label="Background music (mp3/m4a/wav)", file_types=["audio"], type="filepath")
    go = gr.Button("3. Make Short", variant="primary", size="lg", elem_classes="big-btn")
    gr.Markdown("Takes a few minutes for a 6-minute video (longer the first time the site wakes up). "
                "Keep this page open. Use 720p on the free website.")
    preview = gr.Video(label="Your Short", height=560)
    download = gr.File(label="4. Download")
    job = gr.State("")
    with gr.Accordion("Edit the shot list (optional)", open=False):
        gr.Markdown("Each shot has `start`/`end` (seconds in your video), a `phase` "
                    "(hero, prep, cook, finale, taste) and an optional `label` like \"Fry\". "
                    "Change times, delete or reorder shots, then re-cut.")
        shots = gr.Code(label="shots.json", language="json")
        again = gr.Button("Re-cut with my shot list", size="lg", elem_classes="big-btn")

    go.click(make_short, [video, title, hook, use_voice, music, resolution], [preview, download, shots, job])
    again.click(rerender, [shots, job, music], [preview, download])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("HOST") or os.environ.get("GRADIO_SERVER_NAME") or "127.0.0.1"
    demo.queue(default_concurrency_limit=1).launch(
        server_name=host, server_port=port, css=CSS, max_file_size="500mb",
    )
