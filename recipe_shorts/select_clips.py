"""Pick 2-3 Shorts-worthy moments from the analysed video.

Strategy (tuned for What's in My Pan style recipe videos, but generic enough):

  * Mark "talking head" time (a face fills the frame for a few seconds) and the
    outro/end card, and never put those in a clip.
  * Score every half second for action (motion + food colour) and payoff
    (food colour heavy). Transcript keywords nudge scores ("fry", "add" ->
    action; "ready", "taste", "garnish" -> payoff; "subscribe" -> outro).
  * Fill three slots, like a human editor would:
      hook   - starts at 0:00 (these videos open on the finished dish)
      payoff - best late-video stretch (toss, spoon lift, final plate)
      action - best mid-video stretch (frying, tempering, sauce going in)
  * A clip may skip over talking-head bits inside it, so its source span can be
    longer than its final duration. Clip ends snap to phrase ends / shot cuts so
    we don't chop a word in half.
"""
import re

import numpy as np

# "Heat" words = the satisfying on-the-stove moments Shorts viewers stop for.
HEAT_WORDS = r"\b(fry|frying|fried|oil|sizzl\w*|tadka|temper\w*|toss\w*|roast\w*|flame|golden|crisp\w*|smok\w*|high heat|stove|pan)\b"
ACTION_WORDS = r"\b(stir\w*|add\w*|pour\w*|garlic|ginger|chill?i\w*|masala|sauce|paste|cook\w*)\b"
# Prep at the counter (mixing in a bowl, measuring) is less exciting than the stove.
PREP_WORDS = r"\b(bowl|mix\w* well with|hands|measure\w*|chop\w*|cut\w*|wash\w*|soak\w*|maida|flour)\b"
PAYOFF_WORDS = r"\b(ready|taste|tasty|bite|garnish\w*|serve\w*|final|finally|done|delicious|wow|plate|yum\w*)\b"
OUTRO_WORDS = r"\b(subscribe|thank you for watching|thanks for watching|comment section|like,? share|see you|bell icon)\b"


def _rolling_mean(x, n):
    if n <= 1:
        return x.astype(float)
    k = np.ones(n) / n
    return np.convolve(x.astype(float), k, mode="same")


def _runs(mask):
    """Return list of (start_idx, end_idx_exclusive) runs where mask is True."""
    runs, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def build_timeline(feat, duration, transcript):
    """Derive per-sample scores and masks used for selection."""
    fps = feat["fps"]
    t = feat["t"]
    n = len(t)
    dt = 1.0 / fps

    def norm(x):
        hi = np.percentile(x, 90) or 1.0
        return np.clip(x / hi, 0, 1.2)

    motion = norm(_rolling_mean(feat["motion"], 3))
    food = norm(_rolling_mean(feat["food"], 3))

    # Talking head: a face is detected in at least half the samples of a ~3s window.
    has_face = (feat["face"] > 0.004).astype(float)
    talking = _rolling_mean(has_face, int(3 * fps)) >= 0.5
    # Only treat runs >= 2s as talking-head (ignore one-off detections).
    talk_mask = np.zeros(n, bool)
    for a, b in _runs(talking):
        if (b - a) * dt >= 2.0:
            talk_mask[max(0, a - 1):min(n, b + 1)] = True

    # Bridge short gaps (<2s) between talking runs (a glance down at the pan).
    runs0 = _runs(talk_mask)
    for (a, b), (c, d) in zip(runs0, runs0[1:]):
        if (c - b) * dt < 2.0:
            talk_mask[b:c] = True

    # Transcript keyword boosts.
    action_kw = np.zeros(n)
    heat_kw = np.zeros(n)
    prep_kw = np.zeros(n)
    payoff_kw = np.zeros(n)
    outro_start = None
    for seg in transcript or []:
        txt = seg["text"].lower()
        i0, i1 = int(seg["start"] * fps), int(np.ceil(seg["end"] * fps))
        if re.search(HEAT_WORDS, txt):
            heat_kw[i0:i1] = 1
        if re.search(ACTION_WORDS, txt):
            action_kw[i0:i1] = 1
        if re.search(PREP_WORDS, txt):
            prep_kw[i0:i1] = 1
        if re.search(PAYOFF_WORDS, txt):
            payoff_kw[i0:i1] = 1
        if re.search(OUTRO_WORDS, txt) and seg["start"] > 0.6 * duration and outro_start is None:
            outro_start = seg["start"]

    # End of usable footage: the earliest of
    #   - the start of an outro phrase ("thank you for watching", "subscribe"),
    #   - the start of a final talking-head run that lasts to (near) the end,
    #   - a frozen tail (end card), else the last 3 seconds.
    end_cut = duration - 3.0
    if outro_start is not None:
        end_cut = min(end_cut, outro_start)
    runs = _runs(talk_mask)
    if runs and t[min(runs[-1][1], n - 1)] > duration - 20 and t[runs[-1][0]] > 0.6 * duration:
        end_cut = min(end_cut, t[runs[-1][0]])
    still = feat["motion"] < 1.0
    tail = n
    while tail > 0 and still[tail - 1]:
        tail -= 1
    if tail < n:
        end_cut = min(end_cut, t[tail - 1] if tail else 0)

    usable = (~talk_mask) & (t < end_cut)

    return {
        "fps": fps, "t": t, "dt": dt, "n": n,
        "motion": motion, "food": food,
        "talk_mask": talk_mask, "usable": usable, "end_cut": float(end_cut),
        "action": 0.6 * motion + 0.4 * food + 0.35 * heat_kw + 0.1 * action_kw - 0.15 * prep_kw,
        "payoff": 0.35 * motion + 0.65 * food + 0.3 * payoff_kw,
        "hook": 0.5 * motion + 0.5 * food,
        # "Eye-catching" score for stitched clips: movement + food on screen + stove words.
        "eye": 0.5 * motion + 0.5 * food + 0.3 * heat_kw + 0.1 * payoff_kw - 0.25 * prep_kw,
        "cuts": feat["cut"],
    }


def _snap_points(tl, transcript):
    """Good places to end a clip: phrase ends and shot cuts (in seconds)."""
    pts = [float(tl["t"][i]) for i in np.nonzero(tl["cuts"])[0]]
    for seg in transcript or []:
        pts.append(seg["end"] + 0.25)
    return sorted(pts)


def make_clip(tl, start, min_len, target_len, max_len, transcript, max_span=110.0):
    """Collect usable samples from `start` forward into a list of (start,end) segments.

    Returns (segments, kept_duration) or (None, 0) if not enough usable footage.
    """
    fps, t, dt, usable = tl["fps"], tl["t"], tl["dt"], tl["usable"]
    i = int(round(start * fps))
    kept_idx = []
    while i < tl["n"] and t[i] - start <= max_span and len(kept_idx) * dt < max_len:
        if usable[i]:
            kept_idx.append(i)
        i += 1
    if len(kept_idx) * dt < min_len:
        return None, 0.0

    # Choose an end point between min_len and max_len of kept footage, preferring
    # the first phrase end / cut at or after target_len.
    cum_times = [(k + 1) * dt for k in range(len(kept_idx))]
    src_end_at = {round(c, 3): t[idx] + dt for c, idx in zip(cum_times, kept_idx)}
    snaps = _snap_points(tl, transcript)
    chosen = None
    for c, idx in zip(cum_times, kept_idx):
        if c < target_len:
            continue
        src_end = t[idx] + dt
        if any(abs(src_end - p) <= dt / 2 + 1e-6 for p in snaps):
            chosen = c
            break
    if chosen is None:
        chosen = min(target_len, cum_times[-1])
    n_keep = int(round(chosen / dt))
    kept_idx = kept_idx[:n_keep]

    # Turn kept indices into contiguous segments; drop tiny fragments (<1.5s).
    segs, a, prev = [], kept_idx[0], kept_idx[0]
    for idx in kept_idx[1:]:
        if idx != prev + 1:
            segs.append((a, prev + 1))
            a = idx
        prev = idx
    segs.append((a, prev + 1))
    segs = [(float(t[a]), float(min(t[b - 1] + dt, tl["end_cut"]))) for a, b in segs
            if (b - a) * dt >= 1.5]
    kept = sum(e - s for s, e in segs)
    if kept < min_len - 1.0:
        return None, 0.0
    return segs, kept


def _clip_score(tl, segs, key, tail_weight=0.0):
    idx = np.concatenate([np.arange(int(s * tl["fps"]), int(e * tl["fps"])) for s, e in segs])
    idx = idx[idx < tl["n"]]
    vals = tl[key][idx]
    score = float(vals.mean())
    if tail_weight:
        tail = vals[-int(12 * tl["fps"]):]
        score = (1 - tail_weight) * score + tail_weight * float(tail.mean())
    return score


def _overlap(a, b, gap=10.0):
    """Overlap in seconds between two clips' source spans, treating clips closer
    than `gap` seconds as overlapping (back-to-back clips feel like duplicates)."""
    sa, ea = a[0][0] - gap / 2, a[-1][1] + gap / 2
    sb, eb = b[0][0] - gap / 2, b[-1][1] + gap / 2
    return max(0.0, min(ea, eb) - max(sa, sb))


def _snap_start(tl, s, window=2.0):
    """Nudge a start time onto a nearby shot cut, if there is one."""
    t = tl["t"]
    near = [float(t[i]) for i in np.nonzero(tl["cuts"])[0] if abs(t[i] - s) <= window]
    return min(near, key=lambda x: abs(x - s)) if near else s


def pick_clips(tl, duration, transcript, n_clips=3, min_len=30.0, target_len=38.0, max_len=58.0,
               log=print):
    """Return a ranked list of clip dicts (slot, segments, score, reason)."""
    end_cut = tl["end_cut"]
    picks = []

    def search(key, lo, hi, step=1.0, tail_weight=0.0, exclude=()):
        best = None
        s = lo
        while s <= hi:
            ss = _snap_start(tl, s)
            segs, kept = make_clip(tl, ss, min_len, target_len, max_len, transcript)
            if segs and all(_overlap(segs, ex) <= 0.0 for ex in exclude):
                sc = _clip_score(tl, segs, key, tail_weight)
                if best is None or sc > best[0]:
                    best = (sc, segs, kept)
            s += step
        return best

    # 1) Hook: the opening. Start at the first usable sample in the first 10s.
    first = next((float(tl["t"][i]) for i in range(tl["n"]) if tl["usable"][i]), 0.0)
    if first <= 10:
        segs, kept = make_clip(tl, first, min_len, target_len, max_len, transcript)
        if segs:
            picks.append({"slot": "hook", "segments": segs, "duration": kept,
                          "score": _clip_score(tl, segs, "hook") + 0.25})

    exclude = [p["segments"] for p in picks]
    # 2) Payoff: late video, weight the last seconds of the clip (the reveal).
    lo, hi = 0.5 * duration, end_cut - min_len
    if hi > lo:
        best = search("payoff", lo, hi, tail_weight=0.4, exclude=exclude)
        if best:
            picks.append({"slot": "payoff", "segments": best[1], "duration": best[2],
                          "score": best[0] + 0.1})
            exclude.append(best[1])
    # 3) Action: middle of the video.
    lo, hi = 0.15 * duration, min(0.75 * duration, end_cut - min_len)
    if hi > lo:
        best = search("action", lo, hi, exclude=exclude)
        if best:
            picks.append({"slot": "action", "segments": best[1], "duration": best[2],
                          "score": best[0]})
            exclude.append(best[1])

    # Fallback for unusual videos: fill remaining slots with the best anywhere.
    while len(picks) < n_clips:
        best = search("action", 0, max(0, end_cut - min_len), step=2.0, exclude=exclude)
        if not best:
            break
        picks.append({"slot": "extra", "segments": best[1], "duration": best[2], "score": best[0]})
        exclude.append(best[1])

    for p in picks:
        p["reason"] = describe(tl, p, transcript)
    picks.sort(key=lambda p: -p["score"])
    picks = picks[:n_clips]
    for r, p in enumerate(picks, 1):
        p["rank"] = r
    return picks


def describe(tl, clip, transcript):
    """Short plain-English reason for picking this clip."""
    segs = clip["segments"]
    idx = np.concatenate([np.arange(int(s * tl["fps"]), int(e * tl["fps"])) for s, e in segs])
    idx = idx[idx < tl["n"]]
    m, f = float(tl["motion"][idx].mean()), float(tl["food"][idx].mean())
    bits = {
        "hook": "Opening of the video (these videos start on the finished dish / a cooking teaser)",
        "payoff": "Late-video payoff (final toss, plating, spoon lift)",
        "action": "Mid-video cooking action",
        "extra": "Extra high-energy stretch",
    }[clip["slot"]]
    detail = []
    if m > 0.7:
        detail.append("lots of movement (sizzle/stirring)")
    if f > 0.7:
        detail.append("food fills the frame")
    said = [seg["text"] for seg in transcript or []
            if any(seg["start"] < e and seg["end"] > s for s, e in segs)]
    if said:
        key = PAYOFF_WORDS if clip["slot"] == "payoff" else HEAT_WORDS
        best = next((x for x in said if re.search(key, x.lower())), said[0])
        quote = best if len(best) < 60 else best[:57] + "..."
        detail.append(f'audio: "{quote}"')
    return bits + (": " + "; ".join(detail) if detail else "")


# ---------------------------------------------------------------------------
# v2: stitched "highlight" clips. Instead of one continuous window, each clip is
# built from short punchy pieces (one shot or up to ~6s of it), keeping only the
# eye-catching ones and cutting the dull stretches in between.
# ---------------------------------------------------------------------------

def _pieces(tl, max_piece=6.0, min_piece=1.5):
    """Split usable footage into pieces at shot cuts (and every max_piece s)."""
    fps, t, dt = tl["fps"], tl["t"], tl["dt"]
    pieces, a = [], None
    for i in range(tl["n"] + 1):
        boundary = (i == tl["n"] or not tl["usable"][i] or tl["cuts"][i]
                    or (a is not None and (i - a) * dt >= max_piece))
        if a is not None and boundary:
            s, e = float(t[a]), float(t[i - 1] + dt)
            if tl["cuts"][a] and a > 0:
                s += 0.25  # skip the transition frame right at a cut
            e = min(e, tl["end_cut"])
            if e - s >= min_piece:
                idx = np.arange(int(s * fps), min(tl["n"], int(e * fps)))
                pieces.append({"start": round(s, 2), "end": round(e, 2),
                               "score": float(tl["eye"][idx].mean()),
                               "payoff": float(tl["payoff"][idx].mean())})
            a = None
        if i < tl["n"] and tl["usable"][i] and a is None:
            a = i
    return pieces


def pick_montage(tl, duration, transcript, n_clips=3, min_len=25.0, target_len=34.0, max_len=45.0,
                 log=print):
    t = tl["t"]
    pieces = _pieces(tl)
    if not pieces:
        return []
    dull = np.percentile([p["score"] for p in pieces], 35)

    # Find the talking-head intro (first talking run in the first ~25% of the video).
    talk_runs = [(t[a], t[b - 1]) for a, b in _runs(tl["talk_mask"]) if t[a] < 0.25 * duration]
    intro_s, intro_e = (talk_runs[0][0], talk_runs[0][1]) if talk_runs else (0.0, 0.0)
    body0, body1 = intro_e, tl["end_cut"]
    third = (body1 - body0) / 3
    regions = [("hook", 0.0, body0 + third), ("action", body0 + third, body0 + 2 * third),
               ("payoff", body0 + 2 * third, body1)]

    # Hook clip is built last: teaser + the most eye-catching leftovers from anywhere
    # in the body (not the counter-top prep that usually follows the intro).
    regions = regions[1:] + [("hook", 0.0, body1)]
    strong = np.percentile([p["score"] for p in pieces], 65)
    used = []
    clips = []
    for slot, lo, hi in regions[-max(n_clips, 1):]:
        cand = [p for p in pieces if p["start"] >= lo and p["end"] <= hi + 0.01
                and not any(p["start"] < ue + 3 and p["end"] > us - 3 for us, ue in used)]
        if slot == "hook":
            cand = [p for p in cand if p["end"] <= intro_s + 0.01 or p["score"] >= strong]
        key = "payoff" if slot == "payoff" else "score"
        chosen = []
        if slot == "hook":  # always keep the food teaser before the intro
            chosen = [p for p in cand if p["end"] <= intro_s + 0.01]
        rest = sorted([p for p in cand if p not in chosen], key=lambda p: -p[key])
        total = sum(p["end"] - p["start"] for p in chosen)
        for p in rest:
            if total >= target_len:
                break
            d = p["end"] - p["start"]
            if total + d > max_len:
                continue
            if p[key] < dull and total >= min_len:
                break
            chosen.append(p)
            total += d
        if total < min_len - 0.5:
            log(f"  ({slot}: only {total:.0f}s of usable footage, skipped)")
            continue
        chosen.sort(key=lambda p: p["start"])
        segs = [(p["start"], p["end"]) for p in chosen]
        if slot != "hook":
            # Hook first: open with the last ~3.5s of the best piece (the peak), then
            # play the rest in recipe order.
            best = max(chosen, key=lambda p: p[key])
            cold = (max(best["start"], best["end"] - 3.5), best["end"])
            segs = [cold] + [(s, e) if (s, e) != (best["start"], best["end"]) else (s, cold[0])
                             for s, e in segs]
            segs = [(s, e) for s, e in segs if e - s >= 1.2]
        score = float(np.mean([p[key] for p in chosen])) + (0.25 if slot == "hook" else 0)
        used += segs
        clips.append({"slot": slot, "segments": segs, "duration": sum(e - s for s, e in segs),
                      "score": score})
    for c in clips:
        c["reason"] = describe(tl, c, transcript)
        c["reason"] += f" [{len(c['segments'])} cuts stitched, dull bits removed]"
    clips.sort(key=lambda c: -c["score"])
    for r, c in enumerate(clips, 1):
        c["rank"] = r
    return clips
