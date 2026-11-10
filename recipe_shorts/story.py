"""Story mode (default): ONE vertical Short that tells the recipe.

  hero  (0-3s)  most appetising finished-dish shot + hook title
  prep          2-4 shots (raw veg, coating, spices)
  cook          3-5 shots (into the oil, frying, lift-out, tempering, sauce, toss)
  finale        2-4 shots (final toss, garnish, close-up, spoon lift)

Recipe order after the opener, fast cuts (~1.5-3.5s), no talking head, no end card.
Every shot is a full-bleed static 9:16 crop centred on the vessel + action
(see vertical.py); shots that can't be framed well vertically are dropped.
Shots can be picked automatically (auto_story) or hand-picked in a shots.json.
"""
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np

from .render import FONT_DIR
from .vertical import frame_face, frame_shot

MIN_QUALITY = 0.3


def _ass(path, W, H, hook, labels, total, endcard=None):
    from .captions import _ass_escape, _ass_ts
    hs, ls = int(H * 0.06), int(H * 0.036)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,DejaVu Sans,{hs},&H0000D7FF,&H000000FF,&H00101010,&HA0000000,-1,0,0,0,100,100,0,0,3,{hs // 5},0,8,{int(W * .07)},{int(W * .07)},{int(H * .14)},1
Style: End,DejaVu Sans,{int(H * .062)},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{max(4, int(H * .005))},3,5,{int(W * .08)},{int(W * .08)},0,1
Style: Arrow,DejaVu Sans,{int(H * .09)},&H0000D7FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{max(4, int(H * .005))},3,5,0,0,0,1
Style: Handle,DejaVu Sans,{int(H * .034)},&H0000D7FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,2,5,0,0,0,1
Style: Step,DejaVu Sans,{ls},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,2,0,3,{ls // 4},0,2,40,40,{int(H * .2)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{_ass_ts(0)},{_ass_ts(min(3.0, total))},Hook,,0,0,0,,"
                  f"{{\\fad(120,250)}}{_ass_escape(hook.upper())}")
    for t0, t1, text in labels:
        ev.append(f"Dialogue: 0,{_ass_ts(t0)},{_ass_ts(t1)},Step,,0,0,0,,{{\\fad(100,150)}}{_ass_escape(text.upper())}")
    if endcard:
        t0, t1, text, handle = endcard
        a, b = _ass_ts(t0 + 0.1), _ass_ts(t1)
        ev.append(f"Dialogue: 2,{a},{b},End,,0,0,0,,{{\\pos({W // 2},{int(H * .40)})\\fad(200,0)}}{_ass_escape(text.upper())}")
        ev.append(f"Dialogue: 2,{_ass_ts(t0 + 0.35)},{b},Arrow,,0,0,0,,{{\\pos({W // 2},{int(H * .56)})\\fad(150,0)}}\u2193")
        if handle:
            ev.append(f"Dialogue: 2,{_ass_ts(t0 + 0.35)},{b},Handle,,0,0,0,,{{\\pos({W // 2},{int(H * .66)})\\fad(150,0)}}{_ass_escape(handle)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")


END_TEXT = "Full recipe on my channel"
END_HANDLE = "@whatsinmypan"
END_SECS = 2.5


def output_size(src_h):
    """Native 1080x1920 for 1080p/1440p/4K sources (a 9:16 crop of 1440p is 810x1440),
    720x1280 for smaller ones."""
    return (1080, 1920) if src_h >= 1080 else (720, 1280)


def render_story(src, shots, hook, out_path, src_w, src_h, out_w=None, out_h=None, fps=30,
                 zoom=1.0, keep_audio=False, endcard=True, end_text=END_TEXT, handle=END_HANDLE,
                 end_time=None, music=None, crf=18, preset="medium", log=print):
    """shots: [{start,end,phase,label?,audio?,crop?}] in play order.

    phase "taste" = him tasting at the end: framed on his face + spoon; its original
    audio is kept only if the shot has "audio": true. All other shots are silent
    (chopped voice-over sounds bad; add music in the YouTube/Instagram editor).
    endcard: 2.5s slow push-in on the hero (finished dish) frame with
    "FULL RECIPE ON MY CHANNEL", an arrow and the channel handle.
    """
    from . import media
    info = media.probe(src)
    has_audio = info["has_audio"]
    if not out_w:
        out_w, out_h = output_size(src_h)
    tm = media.sdr_filter(info)
    tm = (tm + ",") if tm else ""  # HDR (HLG/PQ) -> SDR bt709, applied after the crop
    framed = []
    for s in shots:
        if s.get("crop"):
            fr = s["crop"]
        elif s.get("phase") == "taste":
            fr = frame_face(src, s["start"], s["end"], src_w, src_h)
        else:
            fr = frame_shot(src, s["start"], s["end"], src_w, src_h, zoom)
        if fr is None or (fr["quality"] < MIN_QUALITY and not s.get("force")):
            log(f"  drop {s['start']:.1f}-{s['end']:.1f}: can't frame it well vertically")
            continue
        framed.append(dict(s, crop={k: fr[k] for k in ("x", "y", "w", "h", "quality")}))
    look = "unsharp=5:5:0.6,eq=saturation=1.12:contrast=1.04"
    labels, t = [], 0.0
    for s in framed:
        d = s["end"] - s["start"]
        if s.get("label"):
            labels.append((t + 0.1, t + min(d, 1.6), s["label"]))
        t += d
    ec = None
    if endcard:
        ec = (t, t + END_SECS, end_text, handle)
        t += END_SECS

    def _ff(cmd, cwd=None):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout or "ffmpeg failed")[-2000:])

    extra = []
    threads = os.environ.get("FFMPEG_THREADS", "").strip()
    if threads.isdigit() and int(threads) > 0:
        extra += ["-threads", threads]
    x264 = extra + ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                    "-profile:v", "high", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]

    def _encode_clip(start, dur, vf, out, audio_filter=None):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", os.path.abspath(src)]
        if audio_filter:
            cmd += ["-vf", vf, "-af", audio_filter, "-map", "0:v:0", "-map", "0:a:0"]
        else:
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf", vf, "-map", "0:v:0", "-map", "1:a:0"]
        cmd += x264 + ["-t", f"{dur:.3f}", out]
        _ff(cmd, cwd=work)

    work = tempfile.mkdtemp(prefix="rstory_")
    try:
        parts = []
        for k, s in enumerate(framed):
            d = s["end"] - s["start"]
            c = s["crop"]
            vf = (f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},{tm}scale={out_w}:{out_h}:flags=lanczos,"
                  f"{look},fps={fps},setsar=1,format=yuv420p")
            dest = os.path.join(work, f"shot_{k:02d}.mp4")
            af = None
            if s.get("audio") and has_audio:
                af = (f"aresample=48000,aformat=channel_layouts=stereo,atrim=duration={d:.3f},"
                      f"afade=t=in:d=0.1,afade=t=out:st={max(0, d - 0.2):.3f}:d=0.2")
            _encode_clip(s["start"], d, vf, dest, af)
            parts.append(dest)
        if endcard:
            hero = next((s for s in framed if s.get("phase") == "hero"), framed[0])
            et = end_time if end_time is not None else (hero["start"] + hero["end"]) / 2
            c = hero["crop"]
            frames = int(END_SECS * fps)
            mid_w, mid_h = (out_w * 2, out_h * 2) if out_h >= 1920 else (out_w, out_h)
            vf = (f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},{tm}scale={mid_w}:{mid_h}:flags=lanczos,"
                  f"trim=end_frame=1,tpad=stop_mode=clone:stop={frames},"
                  f"zoompan=z='1+0.08*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
                  f"s={out_w}x{out_h}:fps={fps},{look},eq=brightness=-0.12,setsar=1,format=yuv420p")
            dest = os.path.join(work, "endcard.mp4")
            _encode_clip(et, END_SECS, vf, dest, None)
            parts.append(dest)
        _ass(os.path.join(work, "subs.ass"), out_w, out_h, hook, labels, t, ec)
        shutil.copytree(FONT_DIR, os.path.join(work, "fonts"))
        lst = os.path.join(work, "concat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for part in parts:
                f.write(f"file '{part}'\n")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", lst]
        if music:
            cmd += ["-stream_loop", "-1", "-i", os.path.abspath(music)]
            fc = (f"[0:v]ass=subs.ass:fontsdir=fonts[vout];"
                  f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={t:.3f},"
                  f"volume=0.7,afade=t=in:d=0.5,afade=t=out:st={max(0, t - 2):.3f}:d=2[mus];"
                  f"[0:a][mus]amix=inputs=2:duration=first:normalize=0[ac]")
            cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[ac]"]
        else:
            cmd += ["-map", "0:v", "-map", "0:a", "-vf", "ass=subs.ass:fontsdir=fonts"]
        cmd += x264 + ["-movflags", "+faststart", os.path.abspath(out_path)]
        _ff(cmd, cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log(f"  story: {len(framed)} shots{' + end card' if endcard else ''}, {t:.1f}s -> {out_path}")
    return framed, t


def _norm(x, lo_p=10, hi_p=90):
    lo, hi = np.percentile(x, lo_p), np.percentile(x, hi_p)
    return np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)


def shot_scores(tl, feat):
    """Per-sample 'would an editor use this frame?' score.

    + food filling the 9:16 window, visible motion (sizzle/stir/pour), sharp, bright
    + stove words in the voice-over ("fry", "oil", "golden" ...)
    - dark or blurry frames, whip-pans (extreme motion), counter prep words
    Talking head / outro / end card are excluded separately (tl["usable"]).
    """
    smooth = lambda x: np.convolve(x, np.ones(3) / 3, mode="same")
    fill = _norm(smooth(feat["cropfill"]))
    sharp = _norm(smooth(np.log1p(feat["sharp"])))
    bright = np.clip((smooth(feat["bright"]) - 45) / 60, 0, 1)
    motion = tl["motion"] / 1.2
    whip = np.clip((feat["motion"] - np.percentile(feat["motion"], 95)) / 10, 0, 1)
    edu = tl["eye"] - (0.5 * tl["motion"] + 0.5 * tl["food"])  # keyword part of the eye score
    # Novelty: the picture changed vs ~4s ago (an ingredient goes in, a new step starts) -
    # editors cut on these "something happens" moments.
    prof = feat["colprof"]
    lag = int(4 * tl["fps"])
    nov = np.zeros(len(prof))
    nov[lag:] = np.abs(prof[lag:] - prof[:-lag]).mean(axis=1)
    nov = _norm(smooth(nov))
    score = (0.9 * fill + 0.45 * np.minimum(motion, 1) + 0.45 * sharp + 0.25 * bright + 0.6 * edu + 0.35 * nov
             - 0.6 * whip - 0.5 * (bright < 0.25))
    return score, fill, sharp


def auto_story(tl, feat, src, src_w, src_h, intro=(0.0, 0.0), counts=(4, 6, 4), win=2.5,
               check_framing=True, log=print):
    """Pick hero / prep / cook / finale / taste shots automatically, in recipe order.

    Candidates are `win`-second windows of usable footage with no shot cut inside.
    Body (after the intro, before the outro) is split ~30% prep / 45% cook / 25% finale;
    each stage takes its best windows, spread out (>= 5s apart). Picks whose vertical
    framing is weak (vertical.frame_shot quality) are replaced by the next best.
    """
    fps, t, n = tl["fps"], tl["t"], tl["n"]
    score, fill, sharp = shot_scores(tl, feat)
    w = int(win * fps)
    cands = []
    for i in range(0, n - w, max(1, int(0.5 * fps))):
        if tl["usable"][i:i + w].all() and not tl["cuts"][i + 1:i + w].any():
            cands.append({"start": round(float(t[i]), 2), "end": round(float(t[i]) + win, 2),
                          "score": float(score[i:i + w].mean()), "fill": float(fill[i:i + w].mean()),
                          "sharp": float(sharp[i:i + w].mean()), "still": float(feat["motion"][i:i + w].mean())})
    if not cands:
        return []
    used = []

    def ok(c):
        if any(abs(c["start"] - u) < 5 for u in used):
            return None
        fr = frame_shot(src, c["start"], c["end"], src_w, src_h) if check_framing else {"quality": 1}
        return fr if fr and fr["quality"] >= MIN_QUALITY else None

    def take(pool, k, key, phase, spread=False):
        """Best k candidates; with spread=True one per equal time slice of the stage
        (so the story covers the whole stage like an editor would), then top up."""
        out = []
        if spread and pool:
            lo, hi = min(c["start"] for c in pool), max(c["end"] for c in pool)
            for j in range(k):
                a, b = lo + (hi - lo) * j / k, lo + (hi - lo) * (j + 1) / k
                for c in sorted([c for c in pool if a <= c["start"] < b], key=key, reverse=True):
                    fr = ok(c)
                    if fr:
                        out.append((c, fr))
                        used.append(c["start"])
                        break
        for c in sorted(pool, key=key, reverse=True):
            if len(out) >= k:
                break
            fr = ok(c)
            if fr:
                out.append((c, fr))
                used.append(c["start"])
        res = [{"start": c["start"], "end": c["end"], "phase": phase, **({"crop": fr} if check_framing else {})}
               for c, fr in out]
        return sorted(res, key=lambda c: c["start"])

    from .select_clips import _runs
    runs = [(a, b) for a, b in _runs(tl["talk_mask"]) if t[a] > 0.6 * t[-1]]
    wrap = float(t[runs[0][0]]) if runs else None
    body0, body1 = intro[1], tl["end_cut"]
    if wrap is not None:
        body1 = min(body1, wrap - 6)  # the seconds before he talks are usually the taste lead-in
    L = max(body1 - body0, 1)
    # Hero: the prettiest finished-dish shot - teaser before the intro (or late finale):
    # food filling the frame, sharp, fairly steady.
    hero_pool = [c for c in cands if c["end"] <= intro[0] + 0.01] if intro[0] > 5 else []
    hero_pool = hero_pool or [c for c in cands if c["start"] >= body0 + 0.8 * L]
    story = take(hero_pool, 1, lambda c: c["fill"] + 0.5 * c["sharp"] - 0.01 * c["still"], "hero")
    stages = [("prep", 0.0, 0.32, counts[0], "Prep"), ("cook", 0.32, 0.78, counts[1], "Cook"),
              ("finale", 0.78, 1.0, counts[2], "Finish")]
    for name, a, b, k, label in stages:
        lo, hi = body0 + a * L, body0 + b * L
        picks = take([c for c in cands if c["start"] >= lo and c["end"] <= hi], k, lambda c: c["score"], name,
                     spread=True)
        if picks:
            picks[0]["label"] = label
        story += picks

    # Taste check: him eating at the wrap-up = most food in frame (spoon at his face)
    # around the start of the last talking-to-camera run. Uses all footage, not just usable.
    if wrap is not None:
        a = runs[0][0]
        lo, hi = max(0, a - int(8 * fps)), min(n - w, a + int(20 * fps))
        if hi > lo:
            face = (feat["face"] > 0).astype(float)
            best = max(range(lo, hi), key=lambda i: tl["food"][i:i + w].mean() + 0.3 * face[i:i + w].mean())
            story.append({"start": round(float(t[best]), 2), "end": round(float(t[best]) + win, 2),
                          "phase": "taste"})
    else:
        log("  no taste-check shot found (no talking-to-camera wrap-up) - skipped")
    return story
