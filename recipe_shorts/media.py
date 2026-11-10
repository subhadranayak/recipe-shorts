"""Small ffmpeg/ffprobe helpers."""
import json
import os
import shutil
import subprocess
import sys


def _bin_candidates():
    found = []
    env = os.environ.get("RECIPE_SHORTS_BIN")
    if env:
        found.append(env)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        found.extend([
            exe_dir,
            os.path.join(exe_dir, "bin"),
            os.path.join(exe_dir, "_internal"),
            os.path.join(exe_dir, "_internal", "bin"),
        ])
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            found.extend([meipass, os.path.join(meipass, "bin")])
        if sys.platform == "darwin":
            contents = os.path.dirname(exe_dir)
            found.extend([
                os.path.join(contents, "Resources"),
                os.path.join(contents, "Resources", "bin"),
                os.path.join(contents, "Frameworks"),
                os.path.join(contents, "Frameworks", "bin"),
                os.path.join(contents, "Frameworks", "_internal"),
            ])
    # Running the desktop launcher from source: desktop/bin
    here = os.path.dirname(os.path.abspath(__file__))
    found.append(os.path.join(os.path.dirname(here), "desktop", "bin"))
    return found


def _tool_in(directory, name):
    if not directory:
        return None
    for cand in (name, name + ".exe"):
        p = os.path.join(directory, cand)
        if os.path.isfile(p):
            return p
    return None


def ensure_ffmpeg_on_path():
    """If bundled ffmpeg/ffprobe exist, put their folder first on PATH.

    No-op when the tools are already on PATH and we are not a frozen app
    (this is the Render / Docker case).
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe") and not getattr(sys, "frozen", False):
        return
    for d in _bin_candidates():
        if _tool_in(d, "ffmpeg") and _tool_in(d, "ffprobe"):
            os.environ["PATH"] = os.path.abspath(d) + os.pathsep + os.environ.get("PATH", "")
            return


def require_ffmpeg():
    ensure_ffmpeg_on_path()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"'{tool}' was not found on PATH. Install ffmpeg first (see README).")


def probe(path):
    """Return dict with duration, width, height, fps, has_audio."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,pix_fmt,color_transfer,color_primaries:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
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
        "pix_fmt": v.get("pix_fmt", ""),
        "primaries": v.get("color_primaries", ""),
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


def audio_has_sound(path, duration):
    """True if the audio track is not (almost) entirely silent."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-45dB:d=2", "-f", "null", "-"],
        capture_output=True, text=True)
    silent = 0.0
    for line in r.stderr.splitlines():
        if "silence_duration:" in line:
            silent += float(line.rsplit("silence_duration:", 1)[1].strip())
    return silent < 0.9 * duration


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{sec % 60:04.1f}"
