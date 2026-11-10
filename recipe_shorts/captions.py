"""Caption timing (Shorts-style short chunks), SRT read/write, and the ASS
subtitle file that ffmpeg burns in (hook title + captions in one pass)."""
import re


def _collapse_repeats(toks):
    """'and mix well mix well' -> 'and mix well' (Whisper stutters / repeated phrases)."""
    changed = True
    while changed:
        changed = False
        for n in range(min(4, len(toks) // 2), 0, -1):
            for i in range(len(toks) - 2 * n + 1):
                if [t.lower().strip(",.") for t in toks[i:i + n]] == [t.lower().strip(",.") for t in toks[i + n:i + 2 * n]]:
                    toks = toks[:i + n] + toks[i + 2 * n:]
                    changed = True
                    break
            if changed:
                break
    return toks


def clip_captions(transcript, segments, min_words=3, max_words=7, min_dur=1.2, max_dur=4.0):
    """Captions for a stitched clip, in clip seconds: [{start,end,text}].

    Works phrase by phrase (Whisper segments) inside each kept piece. Each phrase's
    words are split evenly into 3-7 word chunks; fragments of fewer than 3 words
    (a phrase chopped by an edit) are dropped rather than shown as lone words.
    Each caption stays up for at least ~1.2 s.
    """
    if not transcript:
        return []
    out, offset = [], 0.0
    for s, e in segments:
        piece = []
        for seg in transcript:
            if seg["end"] < s or seg["start"] > e:
                continue
            ws = [w for w in (seg["words"] or []) if w["word"].strip()
                  and s <= (w["start"] + w["end"]) / 2 < e]
            toks = _collapse_repeats([w["word"].strip() for w in ws])
            if len(toks) < min_words:
                continue
            # keep timing of the surviving words (collapse removes from the middle/end)
            ws = ws[:len(toks)] if len(toks) < len(ws) else ws
            n_chunks = -(-len(toks) // max_words)  # ceil
            size = -(-len(toks) // n_chunks)
            for i in range(0, len(toks), size):
                ct, cw = toks[i:i + size], ws[i:i + size]
                if len(ct) < min_words and piece:   # tail fragment: append to previous chunk
                    piece[-1]["text"] += " " + " ".join(ct)
                    piece[-1]["last"] = cw[-1]["end"] - s + offset
                    continue
                piece.append({"start": offset + max(0.0, cw[0]["start"] - s),
                              "last": offset + min(e - s, cw[-1]["end"] - s),
                              "text": " ".join(ct).strip(" ,")})
        for c in piece:
            c["end"] = min(max(c["last"] + 0.4, c["start"] + min_dur), c["start"] + max_dur,
                           offset + (e - s))
        out += piece
        offset += e - s
    for a, b in zip(out, out[1:]):
        a["end"] = min(a["end"], b["start"])
    return [{"start": round(c["start"], 2), "end": round(c["end"], 2), "text": c["text"]}
            for c in out if c["end"] - c["start"] >= 0.8]


def _srt_ts(x):
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(caps, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, 1):
            f.write(f"{i}\n{_srt_ts(c['start'])} --> {_srt_ts(c['end'])}\n{c['text']}\n\n")


def read_srt(path):
    caps = []
    with open(path, encoding="utf-8-sig") as f:
        blocks = re.split(r"\n\s*\n", f.read().strip())
    for b in blocks:
        lines = [l for l in b.strip().splitlines() if l.strip()]
        m = next((re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", l) for l in lines
                  if "-->" in l), None)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        idx = next(i for i, l in enumerate(lines) if "-->" in l)
        text = " ".join(lines[idx + 1:]).strip()
        if text:
            caps.append({"start": st, "end": en, "text": text})
    return caps


def _ass_ts(x):
    cs = int(round(max(0.0, x) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(t):
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def write_ass(path, width, height, hook, hook_secs, caps, font="DejaVu Sans"):
    """Hook: big yellow text with a dark box near the top. Captions: white bold
    with black outline, placed above where the Shorts/Reels UI sits."""
    hook_size = int(height * 0.058)
    cap_size = int(height * 0.044)
    margin_lr = int(width * 0.07)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},{hook_size},&H0000D7FF,&H000000FF,&H00101010,&HA0000000,-1,0,0,0,100,100,0,0,3,{max(6, hook_size // 5)},0,8,{margin_lr},{margin_lr},{int(height * 0.14)},1
Style: Cap,{font},{cap_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{max(3, cap_size // 12)},2,2,{margin_lr},{margin_lr},{int(height * 0.24)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ass_ts(0)},{_ass_ts(hook_secs)},Hook,,0,0,0,,"
                     f"{{\\fad(150,300)}}{_ass_escape(hook.upper())}")
    for c in caps:
        # Don't fight the hook for attention in the first seconds: captions still show,
        # they're at the bottom so they don't overlap.
        lines.append(f"Dialogue: 0,{_ass_ts(c['start'])},{_ass_ts(c['end'])},Cap,,0,0,0,,{_ass_escape(c['text'])}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
