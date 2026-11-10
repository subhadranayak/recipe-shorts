"""Render one vertical clip with ffmpeg: cut the stitched segments, frame each
one (static crop, or fit-to-width over a blurred background when the vessel is
wider than 9:16 we still crop full-bleed on the vessel centre), concat, burn hook + captions (ASS), encode H.264/AAC mp4."""
import os
import shutil
import subprocess
import tempfile

from .captions import write_ass

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _video_chain(k, seg, out_w, out_h, fps, layout=None, tm=""):
    """Full-bleed 9:16: static crop (x, y, w, h) per segment, scaled to fill the screen."""
    return (f"[{k}:v]crop={seg['w']}:{seg.get('h', seg['src_h'])}:{seg['x']}:{seg.get('y', 0)},"
            + (tm + "," if tm else "") +
            f"scale={out_w}:{out_h}:flags=lanczos,fps={fps},setsar=1,format=yuv420p[v{k}]")


def render_clip(src, clip, out_path, out_w, out_h, has_audio, layout="crop", hook_secs=3.0,
                fps=30, preset="medium", crf=18, log=print):
    from .media import sdr_for
    tm = sdr_for(str(src))
    segs = clip["segments"]
    inputs, chains, concat_in = [], [], []
    for k, seg in enumerate(segs):
        s, e = seg["start"], seg["end"]
        inputs += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", os.path.abspath(src)]
        chains.append(_video_chain(k, seg, out_w, out_h, fps, layout, tm))
        concat_in.append(f"[v{k}]")
        if has_audio:
            d = e - s
            chains.append(f"[{k}:a]aresample=48000,afade=t=in:d=0.08,"
                          f"afade=t=out:st={max(0, d - 0.15):.3f}:d=0.15[a{k}]")
            concat_in.append(f"[a{k}]")
    n = len(segs)
    chains.append("".join(concat_in) + f"concat=n={n}:v=1:a={1 if has_audio else 0}"
                  + ("[vc][ac]" if has_audio else "[vc]"))

    work = tempfile.mkdtemp(prefix="rshorts_")
    try:
        write_ass(os.path.join(work, "subs.ass"), out_w, out_h, clip.get("hook", ""), hook_secs,
                  clip.get("captions", []))
        # Relative names inside a temp dir: avoids Windows path escaping in filter strings.
        shutil.copytree(FONT_DIR, os.path.join(work, "fonts"))
        chains.append("[vc]ass=subs.ass:fontsdir=fonts[vout]")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
               "-filter_complex", ";".join(chains), "-map", "[vout]"]
        if has_audio:
            cmd += ["-map", "[ac]", "-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", os.path.abspath(out_path)]
        r = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {out_path}:\n{r.stderr[-2000:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
