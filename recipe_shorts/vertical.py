"""Full-bleed 9:16 framing: find the vessel (pan/bowl rim with HoughCircles, falling back
to the food-colour centroid) and the action (motion), and place ONE static crop per shot
centred between them. Also scores how well a shot works vertically, so bad shots can be skipped.
"""
import subprocess

import cv2
import numpy as np

AW, AH = 320, 180  # analysis size


def grab_frames(src, start, end, n=4):
    """n evenly spaced frames (BGR, 320x180, SDR) from [start, end]."""
    from .media import sdr_for
    tm = sdr_for(str(src))
    frames = []
    for k in range(n):
        t = start + (end - start) * (k + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
                              "-frames:v", "1", "-vf", f"scale={AW}:{AH}" + (f",{tm}" if tm else ""), "-f", "rawvideo",
                              "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
        if len(raw) == AW * AH * 3:
            frames.append(np.frombuffer(raw, np.uint8).reshape(AH, AW, 3))
    return frames


def _food_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    warm = ((h <= 35) | (h >= 160)) & (s > 70) & (v > 70)
    green = (h > 35) & (h < 85) & (s > 70) & (v > 60)
    pale = (s < 50) & (v > 170)  # raw cauliflower / flour / curd
    return warm.astype(np.float32) + 0.6 * green + 0.5 * pale


def _vessel_circle(gray):
    g = cv2.medianBlur(gray, 5)
    c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.5, minDist=AW, param1=120, param2=45,
                         minRadius=int(AH * 0.28), maxRadius=int(AH * 0.75))
    if c is None:
        return None
    x, y, r = c[0][0]
    return float(x), float(y), float(r)


def frame_shot(src, start, end, src_w, src_h, zoom=1.0):
    """Return dict(x, w, h, y, quality, vessel) for a static full-bleed 9:16 crop."""
    frames = grab_frames(src, start, end)
    if not frames:
        return None
    crop_h = int(src_h / zoom) // 2 * 2
    crop_w = int(round(crop_h * 9 / 16)) // 2 * 2
    if crop_w >= src_w:
        return {"x": 0, "y": 0, "w": src_w, "h": crop_h, "quality": 1.0, "vessel": None}
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    circles = [c for c in (_vessel_circle(g) for g in grays) if c]
    food = np.mean([_food_mask(f) for f in frames], axis=0)
    motion = np.mean([cv2.absdiff(a, b).astype(np.float32) for a, b in zip(grays, grays[1:])], axis=0) \
        if len(grays) > 1 else np.zeros_like(food)
    motion = cv2.GaussianBlur(motion, (0, 0), 6)
    cols_food = food.sum(axis=0)
    cols_mot = motion.sum(axis=0)

    def centroid(cols):
        return float((cols * np.arange(AW)).sum() / cols.sum()) if cols.sum() > 1e-3 else AW / 2

    food_x = centroid(cols_food)
    act_x = centroid(cols_mot) if cols_mot.sum() > 0.02 * AW * AH else food_x
    vessel = None
    if circles:
        vx = float(np.median([c[0] for c in circles]))
        vr = float(np.median([c[2] for c in circles]))
        vessel = (vx, vr)
        cx = 0.55 * vx + 0.25 * food_x + 0.20 * act_x
        # Keep the crop inside the vessel when the vessel is wider than the crop.
        half = crop_w / src_w * AW / 2
        if vr > half:
            cx = float(np.clip(cx, vx - vr + half, vx + vr - half))
    else:
        cx = 0.6 * food_x + 0.4 * act_x
    half_px = crop_w / 2
    x = int(np.clip(cx / AW * src_w - half_px, 0, src_w - crop_w))
    y = int((src_h - crop_h) / 2)
    # Quality: share of food + action that lands inside the crop, plus sharpness/brightness.
    a0, a1 = int(x / src_w * AW), int((x + crop_w) / src_w * AW)
    inside = lambda cols: cols[a0:a1].sum() / max(cols.sum(), 1e-6)
    sharp = float(np.mean([cv2.Laplacian(g, cv2.CV_64F).var() for g in grays]))
    bright = float(np.mean([g.mean() for g in grays]))
    food_fill = float(food[:, a0:a1].mean())
    quality = (0.4 * inside(cols_food) + 0.3 * inside(cols_mot + 1e-3) + 0.3 * min(1.0, food_fill * 2)) \
        * min(1.0, sharp / 60) * min(1.0, bright / 90)
    return {"x": x, "y": y, "w": crop_w, "h": crop_h, "quality": round(quality, 3),
            "vessel": vessel, "food_x": food_x, "act_x": act_x}


def frame_face(src, start, end, src_w, src_h):
    """Full-bleed 9:16 crop centred on the face (+ spoon/food below it) for taste-check shots."""
    frames = grab_frames(src, start, end)
    crop_h = src_h // 2 * 2
    crop_w = int(round(crop_h * 9 / 16)) // 2 * 2
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    xs = []
    for f in frames:
        g = cv2.equalizeHist(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
        faces = cascade.detectMultiScale(g, 1.15, 5, minSize=(24, 24))
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            xs.append(x + w / 2)
    if not frames:
        return None
    if not xs:  # face hidden behind the spoon: he sits centre-frame at the wrap-up
        xs = [AW / 2]
    food_x = np.mean([(_food_mask(f).sum(axis=0) * np.arange(AW)).sum() / max(_food_mask(f).sum(), 1e-6)
                      for f in frames])
    cx = 0.75 * float(np.median(xs)) + 0.25 * food_x
    x = int(np.clip(cx / AW * src_w - crop_w / 2, 0, src_w - crop_w))
    return {"x": x, "y": 0, "w": crop_w, "h": crop_h, "quality": 1.0}
