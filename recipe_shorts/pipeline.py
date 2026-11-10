"""End-to-end pipeline: video -> analysis -> clip picks -> vertical mp4s + plan.json.

plan.json is the sidecar: timestamps, reasons, hook titles, captions. You can
edit the hook in plan.json or the captions in clip_N.srt and re-render with
`python cli.py render <out_dir>/plan.json` (or the web UI's re-render button).
"""
import json
import os
import re
import time

from . import media
from .analyze import analyze_video
from .captions import clip_captions, read_srt, write_srt
from .hooks import clean_title, make_hooks
from .story import auto_story, render_story
from .vertical import frame_shot
from .render import render_clip
from .select_clips import build_timeline, pick_montage


def download_url(url, out_dir, log=print):
    """Optional: fetch a video URL with yt-dlp (if installed). Returns (path, title)."""
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit("yt-dlp is not installed. Download the video yourself and pass the file instead.")
    opts = {"format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
            "outtmpl": os.path.join(out_dir, "source.%(ext)s"), "merge_output_format": "mp4",
            "quiet": True, "noprogress": True}
    log(f"Downloading {url} with yt-dlp ...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):
        path = os.path.splitext(path)[0] + ".mp4"
    return path, info.get("title", "")


def choose_output_size(src_h, resolution):
    if resolution == "1080":
        return 1080, 1920
    if resolution == "720":
        return 720, 1280
    # auto: full HD only when the source can actually fill it (crop width >= ~700px).
    return (1080, 1920) if src_h >= 1080 else (720, 1280)


def run(src, out_dir, title="", n_clips=3, whisper_model="base", language="en", captions=True,
        layout="crop", resolution="auto", mode="story", hook="", music=None, min_len=25.0, target_len=34.0, max_len=45.0, log=print):
    media.require_ffmpeg()
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    if re.match(r"^https?://", str(src)):
        src, url_title = download_url(src, out_dir, log)
        title = title or url_title
    if not os.path.exists(src):
        raise SystemExit(f"Input not found: {src}")
    title = title or clean_title(os.path.basename(src))

    info = media.probe(src)
    log(f"Input: {os.path.basename(src)}  {info['width']}x{info['height']}  "
        f"{info['duration']:.0f}s  audio={'yes' if info['has_audio'] else 'no'}")
    if info["duration"] < 40:
        raise SystemExit("Video is shorter than 40 seconds - nothing to cut.")
    has_audio = info["has_audio"] and media.audio_has_sound(src, info["duration"])

    log("1/4 Transcribing speech ...")
    transcript = None
    if captions and has_audio:
        from .transcribe import transcribe
        transcript = transcribe(src, whisper_model, language, log)
    elif not has_audio:
        log("  no usable audio - captions skipped (hook titles still added)")
    else:
        log("  captions turned off")

    log("2/4 Analysing picture (motion, food colour, faces, cuts) ...")
    feat = analyze_video(src, log=log)
    tl = build_timeline(feat, info["duration"], transcript)
    log(f"  talking-head time: {tl['talk_mask'].sum() * tl['dt']:.0f}s; "
        f"usable footage ends at {media.fmt_ts(tl['end_cut'])}")

    if mode == "story":
        return _run_story(src, out_dir, info, tl, feat, title, hook, transcript, log, t0, music,
                         resolution=resolution)

    log("3/4 Picking clips ...")
    picks = pick_montage(tl, info["duration"], transcript, n_clips, min_len, target_len, max_len, log)
    if not picks:
        raise SystemExit("Could not find any 30s+ stretch of usable footage.")

    out_w, out_h = choose_output_size(info["height"], resolution)
    clips = []
    for p in picks:
        segs = []
        for s, e in p["segments"]:
            fr = frame_shot(src, s, e, info["width"], info["height"])
            segs.append({"start": round(s, 2), "end": round(e, 2), "x": fr["x"], "y": fr["y"],
                         "w": fr["w"], "h": fr["h"], "src_h": info["height"]})
        clip_text = " ".join(seg["text"] for seg in transcript or []
                             if any(seg["start"] < e and seg["end"] > s for s, e in p["segments"]))
        hook, alts = make_hooks(p["slot"], title, transcript, clip_text)
        caps = clip_captions(transcript, p["segments"]) if transcript else []
        name = f"clip_{p['rank']}_{p['slot']}"
        clips.append({
            "rank": p["rank"], "slot": p["slot"], "file": name + ".mp4", "srt": name + ".srt" if caps else None,
            "source_range": ", ".join(f"{media.fmt_ts(s)}-{media.fmt_ts(e)}" for s, e in p["segments"]),
            "duration": round(p["duration"], 1), "score": round(p["score"], 3), "reason": p["reason"],
            "hook": hook, "hook_alternatives": alts, "segments": segs, "captions": caps,
        })

    plan = {"source": os.path.abspath(src), "title": title, "duration": round(info["duration"], 1),
            "source_size": [info["width"], info["height"]], "output_size": [out_w, out_h],
            "layout": layout, "has_audio": has_audio, "captions": bool(transcript),
            "whisper_model": whisper_model if transcript else None, "language": language,
            "clips": clips}
    plan_path = os.path.join(out_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    for c in clips:
        if c["captions"]:
            write_srt(c["captions"], os.path.join(out_dir, c["srt"]))

    log(f"4/4 Rendering {len(clips)} clips at {out_w}x{out_h} ...")
    render_plan(plan_path, log=log, reload_srt=False)
    log(f"Done in {time.time() - t0:.0f}s -> {out_dir}")
    return plan_path


def render_plan(plan_path, only=None, log=print, reload_srt=True):
    """(Re-)render clips from plan.json. If reload_srt, captions come from the
    clip_N.srt files (so hand-edited captions are used)."""
    out_dir = os.path.dirname(os.path.abspath(plan_path))
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    out_w, out_h = plan["output_size"]
    for c in plan["clips"]:
        if only and c["rank"] not in only:
            continue
        if reload_srt and c.get("srt") and os.path.exists(os.path.join(out_dir, c["srt"])):
            c["captions"] = read_srt(os.path.join(out_dir, c["srt"]))
        t = time.time()
        render_clip(plan["source"], c, os.path.join(out_dir, c["file"]), out_w, out_h,
                    plan["has_audio"], layout=plan.get("layout", "crop"), log=log)
        log(f"  #{c['rank']} {c['slot']:7s} {c['duration']:4.1f}s  "
            f"\"{c['hook']}\"  ({time.time() - t:.0f}s)")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    return plan


def _run_story(src, out_dir, info, tl, feat, title, hook, transcript, log, t0, music=None,
               resolution="auto"):
    """Story mode: one vertical Short (hero -> prep -> cook -> finale)."""
    from .select_clips import _runs
    t = tl["t"]
    talk = [(float(t[a]), float(t[b - 1])) for a, b in _runs(tl["talk_mask"]) if t[a] < 0.25 * info["duration"]]
    intro = talk[0] if talk else (0.0, 0.0)
    log("3/4 Picking story shots (hero, prep, cook, finale) ...")
    shots = auto_story(tl, feat, src, info["width"], info["height"], intro, log=log)
    if len(shots) < 4:
        raise SystemExit("Not enough usable cooking shots for a story Short.")
    hook = hook or make_hooks("hook", title, transcript)[0]
    name = "story_short.mp4"
    log(f"4/4 Rendering story Short ({len(shots)} shots) ...")
    out_w, out_h = choose_output_size(info["height"], resolution)
    light = os.environ.get("LIGHT_MODE", "").lower() in ("1", "true", "yes") or resolution == "720"
    preset = os.environ.get("FFMPEG_PRESET") or ("veryfast" if light else "medium")
    crf = int(os.environ.get("FFMPEG_CRF") or (20 if light else 18))
    framed, total = render_story(src, shots, hook, os.path.join(out_dir, name), info["width"], info["height"],
                                 out_w=out_w, out_h=out_h, music=music, crf=crf, preset=preset, log=log)
    plan = {"source": os.path.abspath(src), "title": title, "hook": hook, "mode": "story",
            "file": name, "duration": round(total, 1), "output_size": [out_w, out_h],
            "shots": [{k: v for k, v in s.items() if k in ("start", "end", "phase", "label", "crop", "audio")}
                      for s in framed]}
    for s in plan["shots"]:
        s["crop"] = {k: (float(v) if k == "quality" else int(v)) for k, v in s["crop"].items()
                     if k in ("x", "y", "w", "h", "quality")}
    with open(os.path.join(out_dir, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    log(f"Done in {time.time() - t0:.0f}s -> {os.path.join(out_dir, name)}  (edit shots.json and run "
        f"`cli.py story shots.json` to re-cut)")
    return os.path.join(out_dir, "shots.json")


def render_shots_file(shots_path, out=None, src=None, music=None, log=print):
    """Re-render a story from a (hand-edited) shots.json."""
    with open(shots_path, encoding="utf-8") as f:
        d = json.load(f)
    src = src or d["source"]
    info = media.probe(src)
    out = out or os.path.join(os.path.dirname(os.path.abspath(shots_path)), d.get("file", "story_short.mp4"))
    hook = d.get("hook") or make_hooks("hook", d.get("title", ""))[0]
    from .story import END_HANDLE, END_TEXT
    size = d.get("output_size") or choose_output_size(info["height"], "auto")
    light = size[1] <= 1280
    preset = os.environ.get("FFMPEG_PRESET") or ("veryfast" if light else "medium")
    crf = int(os.environ.get("FFMPEG_CRF") or (20 if light else 18))
    return render_story(src, d["shots"], hook, out, info["width"], info["height"],
                        out_w=size[0], out_h=size[1],
                        endcard=d.get("endcard", True), end_text=d.get("end_text", END_TEXT),
                        handle=d.get("handle", END_HANDLE), music=music, crf=crf, preset=preset, log=log)
