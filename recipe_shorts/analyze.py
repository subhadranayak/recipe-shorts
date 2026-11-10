"""Visual analysis of a cooking video.

We sample frames at a low rate (default 2 fps, 320x180) through an ffmpeg pipe and
compute cheap per-sample features with numpy/OpenCV:

  motion  - how much the picture changes (sizzle, stirring, tossing, pouring)
  food    - share of warm, saturated pixels (curries, fried golden food, masala)
  face    - size of the largest detected frontal face (talking-head detector)
  cut     - whether this sample starts a new shot (histogram jump)
  colprof - per-column "interest" profile, used later to aim the 9:16 crop

No ML models are needed for this part; it runs in well under a minute for a 6-minute video.
"""
import subprocess

import cv2
import numpy as np

SAMPLE_W, SAMPLE_H = 320, 180
N_COLS = 32  # horizontal resolution of the crop-aiming profile


def _frames(path, fps):
    """Yield (t, bgr_frame) at `fps` samples per second, letterbox-free 320x180."""
    from .media import sdr_for
    tm = sdr_for(str(path))
    from .media import check_deadline, close_popen, popen_cmd
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vf", f"fps={fps},scale={SAMPLE_W}:{SAMPLE_H}" + (f",{tm}" if tm else ""),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = popen_cmd(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = SAMPLE_W * SAMPLE_H * 3
    i = 0
    try:
        while True:
            check_deadline()
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(SAMPLE_H, SAMPLE_W, 3)
            i += 1
    finally:
        close_popen(p)


def analyze_video(path, fps=2.0, log=print):
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    ts, motion, food, face, cut, colprof, face_x = [], [], [], [], [], [], []
    sharp, bright, cropfill, hists = [], [], [], []
    crop_cols = int(round(SAMPLE_H * 9 / 16))
    prev_small, prev_hist = None, None
    for t, frame in _frames(path, fps):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (80, 45), interpolation=cv2.INTER_AREA).astype(np.float32)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

        # "Food-ness": warm hues (red/orange/yellow/brown, OpenCV hue 0-35 or >160)
        # that are reasonably saturated and not too dark. Greens (curry leaves,
        # capsicum) get partial credit.
        warm = ((h <= 35) | (h >= 160)) & (s > 80) & (v > 60)
        green = (h > 35) & (h < 85) & (s > 80) & (v > 60)
        foodmask = warm.astype(np.float32) + 0.5 * green.astype(np.float32)
        food.append(float(foodmask.mean()))

        # Motion: mean absolute difference with the previous sample.
        if prev_small is None:
            diff = np.zeros_like(small)
        else:
            diff = np.abs(small - prev_small)
        motion.append(float(diff.mean()))

        # Shot cuts: big change in the colour histogram.
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        is_cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < 0.6
        cut.append(bool(is_cut))

        # Faces (talking head). Haar on a contrast-equalised frame; strict params
        # to avoid false hits on food textures.
        faces = cascade.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.15,
                                         minNeighbors=6, minSize=(28, 28))
        if len(faces):
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            face.append(float(fw * fh) / (SAMPLE_W * SAMPLE_H))
            face_x.append((fx + fw / 2) / SAMPLE_W)
        else:
            face.append(0.0)
            face_x.append(-1.0)

        # Column-interest profile: motion (upsampled to full width) + food colour.
        motion_cols = cv2.resize(diff, (SAMPLE_W, SAMPLE_H)).mean(axis=0)
        food_cols = foodmask.mean(axis=0)
        prof = motion_cols / (motion_cols.mean() + 1e-6) + 1.5 * food_cols / (food_cols.mean() + 1e-6)
        colprof.append(prof.reshape(N_COLS, -1).mean(axis=1))

        # Picture quality: sharpness (variance of Laplacian), brightness, and how much
        # food fills the best 9:16 window (what the viewer will actually see).
        sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        bright.append(float(gray.mean()))
        fc = foodmask.sum(axis=0)
        win = np.convolve(fc, np.ones(crop_cols), mode="valid")
        cropfill.append(float(win.max() / (crop_cols * SAMPLE_H)))

        hists.append(hist.flatten().copy())

        ts.append(t)
        prev_small, prev_hist = small, hist
    if not ts:
        raise SystemExit("Could not decode any frames from the video.")
    cut[0] = True
    log(f"  analysed {len(ts)} frames ({len(ts) / fps:.0f}s of video)")
    return {
        "fps": fps,
        "t": np.array(ts),
        "motion": np.array(motion),
        "food": np.array(food),
        "face": np.array(face),
        "face_x": np.array(face_x),
        "cut": np.array(cut),
        "colprof": np.array(colprof),
        "sharp": np.array(sharp),
        "bright": np.array(bright),
        "cropfill": np.array(cropfill),
        "hist": np.array(hists, dtype=np.float32),
    }
