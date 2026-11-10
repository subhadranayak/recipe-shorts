"""Small ffmpeg/ffprobe helpers."""
import json
import os
import signal
import subprocess
import threading
import time

JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", "1200"))  # 20 minutes
MAX_INPUT_SECONDS = int(os.environ.get("MAX_INPUT_SECONDS", "900"))  # 15 minutes
TIMEOUT_MESSAGE = (
    "This Short took too long on the free server (over 20 minutes). "
    "Please try a shorter video, or use the computer app for high-quality videos."
)
TOO_LONG_MESSAGE = (
    "This video is over 15 minutes long. The free website can only use videos "
    "up to 15 minutes. Please trim it and try again, or use the computer app."
)


class JobTimeout(Exception):
    """The current website job hit its hard time limit."""


_lock = threading.Lock()
_procs: set[subprocess.Popen] = set()
_deadline: float | None = None  # time.monotonic() seconds, or None


def set_deadline(seconds):
    global _deadline
    with _lock:
        _deadline = None if seconds is None else time.monotonic() + float(seconds)


def clear_deadline():
    set_deadline(None)


def remaining_time():
    with _lock:
        if _deadline is None:
            return None
        return _deadline - time.monotonic()


def check_deadline():
    left = remaining_time()
    if left is not None and left <= 0:
        kill_running()
        raise JobTimeout(TIMEOUT_MESSAGE)
    return left


def _kill_proc(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def kill_running():
    with _lock:
        procs = list(_procs)
        _procs.clear()
    for p in procs:
        _kill_proc(p)
        try:
            p.wait(timeout=2)
        except Exception:
            pass


def _with_threads(cmd):
    """Insert -threads 1 after ffmpeg so the free 0.1 CPU host stays stable."""
    threads = os.environ.get("FFMPEG_THREADS", "1").strip() or "1"
    if not cmd or cmd[0] != "ffmpeg" or "-threads" in cmd:
        return list(cmd)
    return [cmd[0], "-threads", threads, *cmd[1:]]


def run_cmd(cmd, timeout=None, **kwargs):
    """Run ffmpeg/ffprobe, registered so a job timeout or cancel can kill it."""
    left = check_deadline()
    if timeout is None:
        timeout = left
    elif left is not None:
        timeout = min(timeout, max(0.1, left))
    cmd = _with_threads(cmd)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    p = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    with _lock:
        _procs.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill_proc(p)
        try:
            p.communicate(timeout=5)
        except Exception:
            pass
        raise JobTimeout(TIMEOUT_MESSAGE)
    finally:
        with _lock:
            _procs.discard(p)


def popen_cmd(cmd, **kwargs):
    check_deadline()
    cmd = _with_threads(cmd)
    p = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    with _lock:
        _procs.add(p)
    return p


def close_popen(p, kill=True):
    try:
        if p.stdout:
            p.stdout.close()
        if p.stderr:
            p.stderr.close()
    except Exception:
        pass
    if kill and p.poll() is None:
        _kill_proc(p)
    try:
        p.wait(timeout=5)
    except Exception:
        _kill_proc(p)
        try:
            p.wait(timeout=2)
        except Exception:
            pass
    with _lock:
        _procs.discard(p)


def require_ffmpeg():
    import shutil
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"'{tool}' was not found on PATH. Install ffmpeg first (see README).")


def probe(path):
    """Return dict with duration, width, height, fps, has_audio."""
    r = run_cmd(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,pix_fmt,color_transfer,color_primaries:format=duration",
         "-of", "json", str(path)],
        timeout=60, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"Could not read this video file. Try exporting it as an MP4 and upload again.")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise SystemExit(f"No video stream found in {path}")
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": float(num) / float(den or 1),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
        "transfer": v.get("color_transfer", ""),
        "primaries": v.get("color_primaries", ""),
        "pix_fmt": v.get("pix_fmt", ""),
    }


def sdr_filter(info):
    """ffmpeg filter that converts HDR (HLG / PQ, BT.2020 - e.g. phone footage uploaded as
    VP9 profile 2) to normal SDR BT.709 with Hable tone-mapping, so colours aren't washed
    out. Returns '' for normal SDR video. Put it AFTER crop/scale-down (cheaper)."""
    if info.get("transfer") in ("arib-std-b67", "smpte2084") or info.get("primaries") == "bt2020":
        # desat=0: the default highlight desaturation turns bright yellows (turmeric!) pink.
        return ("zscale=t=linear:npl=100,format=gbrpf32le,tonemap=tonemap=hable:desat=0,"
                "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p,eq=saturation=0.9")
    return ""


_PROBE_CACHE = {}


def sdr_for(path):
    if path not in _PROBE_CACHE:
        _PROBE_CACHE[path] = sdr_filter(probe(path))
    return _PROBE_CACHE[path]


def _scale_filter(width, height, short_side=720):
    """Downscale so the short side is `short_side` (even dimensions). Do not upscale."""
    if min(width, height) <= short_side:
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if height <= width:
        return f"scale=-2:{short_side}:flags=bilinear"
    return f"scale={short_side}:-2:flags=bilinear"


def make_proxy(src, dst, log=print, short_side=720, fps=24, preset="ultrafast"):
    """Transcode a smaller SDR working copy. Analysis and render should use this file.

    Free-host friendly: 720 short side, 24 fps, 8-bit 4:2:0, ultrafast, 1 thread.
    HDR (HLG/PQ/BT.2020) is tone-mapped to SDR after the scale.
    """
    info = probe(src)
    out_fps = min(max(info["fps"] or fps, 1), fps)
    tm = sdr_filter(info)
    scale = _scale_filter(info["width"], info["height"], short_side)
    vf = f"{scale},fps={out_fps:.3f}"
    vf = f"{vf},{tm}" if tm else f"{vf},format=yuv420p"
    threads = os.environ.get("FFMPEG_THREADS", "1").strip() or "1"
    log(f"  making a smaller working copy ({info['width']}x{info['height']} → "
        f"short side {min(short_side, min(info['width'], info['height']))}, {out_fps:.0f} fps)...")

    def _cmd(video_filter):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-threads", threads, "-filter_threads", threads,
            "-i", str(src),
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", preset, "-crf", "23",
            "-pix_fmt", "yuv420p", "-tune", "fastdecode",
            "-movflags", "+faststart",
        ]
        if info["has_audio"]:
            cmd += ["-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "44100"]
        else:
            cmd += ["-an"]
        cmd += [str(dst)]
        return cmd

    r = run_cmd(_cmd(vf))
    if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        # zscale/tonemap may be missing on some builds — fall back to a plain 8-bit copy.
        if tm:
            log("  HDR conversion wasn't available; retrying with a simpler colour conversion...")
            if os.path.exists(dst):
                os.remove(dst)
            r = run_cmd(_cmd(f"{scale},fps={out_fps:.3f},format=yuv420p"))
    if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        err = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", "replace")
        raise SystemExit("Could not read this video. Try exporting it as an MP4 and upload again."
                         + (f" ({err[-400:]})" if err else ""))
    return dst


def audio_has_sound(path, duration):
    """True if the audio track is not (almost) entirely silent."""
    r = run_cmd(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-45dB:d=2", "-f", "null", "-"],
        text=True)
    silent = 0.0
    for line in (r.stderr or "").splitlines():
        if "silence_duration:" in line:
            silent += float(line.rsplit("silence_duration:", 1)[1].strip())
    return silent < 0.9 * duration


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{sec % 60:04.1f}"
