"""Speech-to-text with faster-whisper (local, CPU, no API keys).

We decode audio with ffmpeg ourselves (16 kHz mono float) so we don't depend on
PyAV's container handling. Default: language="en", which makes Whisper write
English captions even when the speaker talks Hindi/Hinglish (it effectively
translates). Use language="hi" for Hindi (Devanagari) output, or None for auto.
"""
import subprocess

import numpy as np


def load_audio(path):
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def transcribe(path, model_size="small", language="en", log=print):
    """Return list of segments: {start, end, text, words:[{start,end,word}]}.

    Returns None (and logs why) if faster-whisper is unavailable or fails, so the
    rest of the pipeline can still export clips without captions.
    """
    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # pragma: no cover - depends on install
        log(f"  [captions skipped] faster-whisper not available: {e}")
        return None
    try:
        audio = load_audio(path)
        if audio.size == 0:
            log("  [captions skipped] no audio samples")
            return None
        log(f"  loading Whisper '{model_size}' (first run downloads the model)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segs, info = model.transcribe(audio, language=language or None, vad_filter=True,
                                      word_timestamps=True, beam_size=5,
                                      temperature=0.0, condition_on_previous_text=False)  # deterministic, fewer loops
        out = []
        for s in segs:
            words = [{"start": float(w.start), "end": float(w.end), "word": w.word}
                     for w in (s.words or [])]
            out.append({"start": float(s.start), "end": float(s.end),
                        "text": s.text.strip(), "words": words})
        log(f"  transcribed {len(out)} phrases (detected language: {info.language})")
        return out
    except Exception as e:
        log(f"  [captions skipped] transcription failed: {e}")
        return None
