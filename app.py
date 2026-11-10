#!/usr/bin/env python3
"""Recipe Shorts - web page (Gradio). Works on a laptop and in Safari on an iPad/phone.

  python app.py         -> open http://127.0.0.1:7860
  (on a host like Render, set HOST=0.0.0.0 and PORT; default quality is 720p)

Upload a recipe video, type its title, tap "Make Short", wait, preview, download.
"""
import os
import queue
import shutil
import tempfile
import threading
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

from recipe_shorts import media
from recipe_shorts.pipeline import render_shots_file, run

JOBS = os.path.join(tempfile.gettempdir(), "recipe_shorts_jobs")
KEEP_HOURS = 2  # finished jobs are deleted after this (keeps a free host's disk clean)
DEFAULT_RESOLUTION = os.environ.get("DEFAULT_RESOLUTION", "720")
JOB_TIMEOUT = media.JOB_TIMEOUT_SECONDS
MAX_INPUT_SECONDS = media.MAX_INPUT_SECONDS

STAGES = {
    "Preparing": (0.08, "Making a smaller working copy of your video..."),
    "1/4": (0.28, "Listening to the voice-over..."),
    "2/4": (0.48, "Watching the video..."),
    "3/4": (0.68, "Picking the best shots..."),
    "4/4": (0.82, "Making the vertical Short..."),
}


def _cleanup(extra_job=None):
    os.makedirs(JOBS, exist_ok=True)
    now = time.time()
    for d in os.listdir(JOBS):
        p = os.path.join(JOBS, d)
        if extra_job and os.path.abspath(p) == os.path.abspath(extra_job):
            shutil.rmtree(p, ignore_errors=True)
            continue
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        if age > KEEP_HOURS * 3600:
            shutil.rmtree(p, ignore_errors=True)
    # leftover ffmpeg work folders from killed jobs
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        if name.startswith(("rstory_", "rshorts_")):
            p = os.path.join(tmp, name)
            try:
                if now - os.path.getmtime(p) > 600:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass


def _path(f):
    return f if isinstance(f, str) else getattr(f, "name", None)


def _friendly(err):
    if isinstance(err, gr.Error):
        return str(err)
    if isinstance(err, (media.JobTimeout, MemoryError, SystemExit)):
        if isinstance(err, MemoryError):
            return "The video was too heavy for this free server. Try a shorter clip, or keep Quality on 720p."
        return str(err)
    return str(err) or err.__class__.__name__


def _status_from_log(msg, current):
    msg = str(msg)
    for key, (frac, desc) in STAGES.items():
        if msg.startswith(key) or key.lower() in msg.lower():
            return desc, frac
    return current, None


def _run_cancellable(worker, progress, start_status="Starting… keep this page open.", wipe_job_on_fail=False):
    """Run `worker(log, holder)` in a thread. Yields UI tuples; kills ffmpeg if the
    tab closes, the job exceeds JOB_TIMEOUT, or the function is otherwise cancelled.

    worker must put the job directory in holder['job'] as soon as it exists, and
    set holder['result'] to (preview, download, shots, job) on success.
    """
    holder = {}
    logs = queue.Queue()
    done = threading.Event()

    def log(msg):
        msg = str(msg)
        logs.put(msg)
        print(msg, flush=True)

    def work():
        try:
            worker(log, holder)
        except (Exception, SystemExit) as e:
            holder["err"] = e
        finally:
            done.set()

    media.kill_running()
    media.set_deadline(JOB_TIMEOUT)
    thread = threading.Thread(target=work, daemon=True, name="recipe-shorts-job")
    thread.start()
    status = start_status
    try:
        yield None, None, "", "", gr.update(interactive=False), status
        while not done.wait(0.4):
            while True:
                try:
                    msg = logs.get_nowait()
                except queue.Empty:
                    break
                status, frac = _status_from_log(msg, status)
                if frac is not None and progress is not None:
                    progress(frac, desc=status)
            try:
                media.check_deadline()
            except media.JobTimeout as e:
                media.kill_running()
                holder["err"] = e
                break
            yield None, None, "", "", gr.update(interactive=False), status
        thread.join(timeout=5)
        # drain leftover logs
        while True:
            try:
                msg = logs.get_nowait()
            except queue.Empty:
                break
            status, frac = _status_from_log(msg, status)
            if frac is not None and progress is not None:
                progress(frac, desc=status)
        if "err" in holder:
            if wipe_job_on_fail:
                _cleanup(holder.get("job"))
            msg = _friendly(holder["err"])
            yield None, None, "", "", gr.update(interactive=True), f"**Couldn't make the Short.** {msg}"
            return
        preview, download, shots, job = holder["result"]
        if progress is not None:
            progress(1.0, desc="Done!")
        yield preview, download, shots, job, gr.update(interactive=True), "Done! You can download your Short below."
    except (GeneratorExit, KeyboardInterrupt):
        media.kill_running()
        if wipe_job_on_fail:
            _cleanup(holder.get("job"))
        raise
    finally:
        if not done.is_set():
            media.kill_running()
            if wipe_job_on_fail:
                _cleanup(holder.get("job"))
        media.clear_deadline()
        media.kill_running()


def make_short(video, title, hook, use_voice, music, resolution, progress=gr.Progress()):
    if not video:
        yield None, None, "", "", gr.update(interactive=True), "**Couldn't make the Short.** Please choose a video first."
        return

    def worker(log, holder):
        _cleanup()
        job = os.path.join(JOBS, uuid.uuid4().hex[:10])
        os.makedirs(job)
        holder["job"] = job
        src = _path(video)
        ext = os.path.splitext(src)[1].lower() or ".mp4"
        local = os.path.join(job, "input" + ext)
        shutil.copy(src, local)
        info = media.probe(local)
        minutes = info["duration"] / 60
        if info["duration"] > MAX_INPUT_SECONDS:
            raise SystemExit(
                f"This video is {minutes:.0f} minutes long. The free website can only use "
                "videos up to 15 minutes. Please trim it and try again, or use the computer app."
            )
        log("Preparing a smaller working copy of your video...")
        proxy = os.path.join(job, "proxy.mp4")
        media.make_proxy(local, proxy, log=log)
        try:
            os.remove(local)
        except OSError:
            pass
        run(proxy, os.path.join(job, "out"), title=(title or "").strip(), hook=(hook or "").strip(),
            mode="story", captions=bool(use_voice), whisper_model="tiny", music=_path(music),
            resolution=resolution or DEFAULT_RESOLUTION, log=log)
        out = os.path.join(job, "out", "story_short.mp4")
        if not os.path.exists(out):
            raise SystemExit("The Short could not be created. Please try a different clip.")
        shots = open(os.path.join(job, "out", "shots.json"), encoding="utf-8").read()
        holder["result"] = (out, out, shots, job)

    yield from _run_cancellable(worker, progress, wipe_job_on_fail=True)


def rerender(shots_text, job, music, progress=gr.Progress()):
    if not job or not os.path.isdir(job):
        yield None, None, "", "", gr.update(interactive=True), "**Couldn't re-cut.** Make a Short first."
        return

    def worker(log, holder):
        holder["job"] = job
        import json
        try:
            json.loads(shots_text)
        except ValueError as e:
            raise SystemExit(f"The shot list isn't valid JSON: {e}")
        p = os.path.join(job, "out", "shots.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(shots_text)
        log("4/4 Re-cutting the Short...")
        render_shots_file(p, music=_path(music), log=log)
        out = os.path.join(job, "out", "story_short.mp4")
        holder["result"] = (out, out, shots_text, job)

    yield from _run_cancellable(worker, progress, start_status="Re-cutting the Short… keep this page open.")


CSS = """
.big-btn button, button.big-btn {font-size: 1.25rem !important; padding: 18px !important; min-height: 64px}
.gradio-container {max-width: 760px !important; margin: auto}
"""

with gr.Blocks(title="Recipe Shorts", delete_cache=(1800, KEEP_HOURS * 3600)) as demo:
    gr.Markdown(
        "# 🍳 Recipe Shorts\n"
        "Turn a long recipe video into a vertical Short: "
        "**finished dish → prep → cooking → final dish → taste → \"full recipe on my channel\"**."
    )
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
    status = gr.Markdown("")
    gr.Markdown(
        "Videos can be up to **15 minutes**. A Short often takes several minutes, "
        "sometimes closer to 10–15, because this free computer is small. "
        "Keep this page open — if you close the tab, the job is cancelled so the next visit starts fresh. "
        "Only one Short can run at a time (a second person will be asked to try again in a few minutes). "
        "Use 720p on the free website."
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

    outputs = [preview, download, shots, job, go, status]
    go.click(
        make_short, [video, title, hook, use_voice, music, resolution], outputs,
        concurrency_limit=1, concurrency_id="jobs", trigger_mode="once",
    )
    again.click(
        rerender, [shots, job, music], outputs,
        concurrency_limit=1, concurrency_id="jobs", trigger_mode="once",
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    host = os.environ.get("HOST") or os.environ.get("GRADIO_SERVER_NAME") or "127.0.0.1"
    # max_size counts waiting jobs only, so 1 = one running + one waiting.
    # A third visitor gets "queue is full" and should try again in a few minutes.
    demo.queue(default_concurrency_limit=1, max_size=1).launch(
        server_name=host, server_port=port, css=CSS, max_file_size="500mb",
        show_error=True,
    )
