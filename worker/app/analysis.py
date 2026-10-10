"""Structured read of a video. Never writes back into the original file."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .ffmpeg_util import run, video_meta

_HALLUCINATIONS = (
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "subtitles by",
)
_whisper = None


def analyze(path: Path, on_progress=None) -> dict:
    def progress(step: str) -> None:
        if on_progress:
            on_progress(step)

    progress("probe")
    meta = video_meta(path)
    progress("scenes")
    scenes = detect_scenes(path, meta["duration"])
    progress("silence")
    silences = detect_silences(path, meta["duration"]) if meta["hasAudio"] else []
    progress("motion-faces")
    motion, faces = motion_and_faces(path, meta)
    progress("transcript")
    transcript = transcribe(path) if meta["hasAudio"] else {"language": None, "text": "", "words": [], "note": "Ingen ljudspår."}
    speakers = speaker_turns(transcript.get("words") or [])
    moments = candidate_moments(meta["duration"], scenes, silences, motion, faces, transcript.get("words") or [])
    return _plain({
        "source": {
            "duration": round(meta["duration"], 3),
            "width": meta["width"],
            "height": meta["height"],
            "fps": meta["fps"],
            "hasAudio": meta["hasAudio"],
            "audioRate": meta["audioRate"],
            "channels": meta["channels"],
        },
        "scenes": scenes,
        "silences": silences,
        "silenceTotal": round(sum(s["end"] - s["start"] for s in silences), 3),
        "motion": motion,
        "faces": faces,
        "transcript": transcript,
        "speakers": speakers,
        "speakerMethod": "pause-separated turns (not biometric diarization)",
        "moments": moments,
    })


def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def summary_for_model(analysis: dict) -> dict:
    words = analysis.get("transcript", {}).get("words") or []
    compact_words = words if len(words) <= 220 else words[:220]
    faces = []
    for face in analysis.get("faces") or []:
        samples = face["samples"]
        step = max(1, len(samples) // 12)
        faces.append({
            "id": face["id"],
            "screenTime": face["screenTime"],
            "avg": face["avg"],
            "samples": samples[::step][:12],
        })
    return {
        "source": analysis["source"],
        "scenes": analysis["scenes"],
        "silences": analysis["silences"],
        "faces": faces,
        "speakers": analysis["speakers"],
        "speakerMethod": analysis["speakerMethod"],
        "moments": analysis["moments"],
        "transcriptText": analysis.get("transcript", {}).get("text", ""),
        "words": [{"t": w["text"], "s": w["start"], "e": w["end"]} for w in compact_words],
        "wordsTruncated": len(words) > 220,
    }


def detect_scenes(path: Path, duration: float) -> list[float]:
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        video = open_video(str(path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=27))
        manager.detect_scenes(video)
        cuts = [round(s[0].get_seconds(), 3) for s in manager.get_scene_list()]
        return [c for c in cuts if 0.05 < c < duration - 0.05]
    except Exception:
        pass
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-an", "-vf", "scale=320:-2,select='gt(scene,0.30)',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    log = (proc.stderr or "") + (proc.stdout or "")
    cuts = []
    for match in re.finditer(r"pts_time:([\d.]+)", log):
        t = round(float(match.group(1)), 3)
        if 0.05 < t < duration - 0.05:
            cuts.append(t)
    # Collapse cuts that are essentially the same frame.
    cleaned: list[float] = []
    for t in cuts:
        if not cleaned or t - cleaned[-1] > 0.2:
            cleaned.append(t)
    return cleaned


def detect_silences(path: Path, duration: float) -> list[dict]:
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-vn", "-af", "silencedetect=noise=-35dB:d=0.35", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    log = proc.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", log)]
    spans = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration
        if end - start >= 0.3:
            spans.append({"start": round(start, 3), "end": round(min(end, duration), 3)})
    return spans


def motion_and_faces(path: Path, meta: dict) -> tuple[list[dict], list[dict]]:
    import cv2

    duration = max(meta["duration"], 0.1)
    sample_fps = 8 if duration < 30 else 4
    sw, sh = int(meta["width"]), int(meta["height"])
    out_w = 480
    out_h = max(2, int(round(sh * (out_w / sw) / 2) * 2))
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps={sample_fps},scale={out_w}:{out_h}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = out_w * out_h * 3
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    motion: list[dict] = []
    detections: list[tuple[float, list[tuple[float, float, float, float]]]] = []
    prev = None
    index = 0
    assert proc.stdout is not None
    while True:
        raw = proc.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape((out_h, out_w, 3))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        t = index / sample_fps
        if prev is not None:
            energy = float(np.mean(cv2.absdiff(gray, prev))) / 255.0
            motion.append({"t": round(t, 3), "energy": round(energy, 4)})
        prev = gray
        rects = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(36, 36))
        boxes = []
        for (x, y, w, h) in rects:
            boxes.append((x / out_w, y / out_h, w / out_w, h / out_h))
        detections.append((t, boxes))
        index += 1
    proc.wait()
    return motion, _track_faces(detections, sample_fps)


def _track_faces(detections, sample_fps: float) -> list[dict]:
    tracks: list[dict] = []
    next_id = 0
    for t, boxes in detections:
        used = set()
        for box in boxes:
            best_i, best_iou = -1, 0.25
            for i, track in enumerate(tracks):
                if i in used:
                    continue
                last = track["samples"][-1]
                if t - last["t"] > 0.75:
                    continue
                score = _iou(box, (last["x"], last["y"], last["w"], last["h"]))
                if score > best_iou:
                    best_i, best_iou = i, score
            sample = {"t": round(t, 3), "x": round(box[0], 4), "y": round(box[1], 4), "w": round(box[2], 4), "h": round(box[3], 4)}
            if best_i >= 0:
                tracks[best_i]["samples"].append(sample)
                used.add(best_i)
            else:
                tracks.append({"id": f"face_{next_id}", "samples": [sample]})
                next_id += 1
                used.add(len(tracks) - 1)
    dt = 1 / sample_fps
    cleaned = []
    for track in tracks:
        if len(track["samples"]) < 2:
            continue
        xs = [s["x"] + s["w"] / 2 for s in track["samples"]]
        ys = [s["y"] + s["h"] / 2 for s in track["samples"]]
        track["screenTime"] = round(len(track["samples"]) * dt, 2)
        track["avg"] = {"x": round(sum(xs) / len(xs), 3), "y": round(sum(ys) / len(ys), 3)}
        cleaned.append(track)
    cleaned.sort(key=lambda t: t["screenTime"], reverse=True)
    return cleaned[:6]


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _load_audio(path: Path) -> np.ndarray:
    # ffmpeg instead of PyAV: some av builds reject faster-whisper's open() kwargs.
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
    )
    if not raw:
        return np.zeros(16000, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32).copy()


def transcribe(path: Path) -> dict:
    global _whisper
    from faster_whisper import WhisperModel

    if _whisper is None:
        _whisper = WhisperModel(os_whisper_model(), device="cpu", compute_type="int8")
    audio = _load_audio(path)
    # temperature as a single 0 keeps the greedy decode. The library default is a
    # fallback list that starts sampling when the first pass looks weak, and that
    # sampling invented a transcript ("Menacing … Seattle") for a clip whose
    # greedy decode is the real speech.
    words, texts, info = _decode(audio, vad_filter=True)
    if _timestamps_unusable(words):
        words, texts, info = _decode(audio, vad_filter=False)
    return {
        "language": getattr(info, "language", None),
        "text": " ".join(texts).strip(),
        "words": words,
    }


def _decode(audio: np.ndarray, *, vad_filter: bool):
    segments, info = _whisper.transcribe(
        audio,
        word_timestamps=True,
        vad_filter=vad_filter,
        beam_size=1,
        temperature=0.0,
    )
    words, texts = _collect(segments)
    return words, texts, info


def _timestamps_unusable(words: list[dict]) -> bool:
    """A missing transcript, or one word stretched across several seconds."""
    if not words:
        return True
    return any(float(word["end"]) - float(word["start"]) > 3.0 for word in words)


def _collect(segments) -> tuple[list[dict], list[str]]:
    words = []
    texts = []
    for segment in segments:
        text = (segment.text or "").strip()
        low = text.lower()
        if any(h in low for h in _HALLUCINATIONS) and (segment.no_speech_prob or 0) > 0.4:
            continue
        texts.append(text)
        if segment.words:
            for word in segment.words:
                token = (word.word or "").strip()
                if token:
                    words.append({
                        "text": token,
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                    })
        elif text:
            words.extend(_spread(text, float(segment.start), float(segment.end)))
    return words, texts


def os_whisper_model() -> str:
    import os
    return os.environ.get("WHISPER_MODEL", "base")


def _spread(text: str, start: float, end: float) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return []
    span = max(0.05, end - start)
    step = span / len(tokens)
    return [
        {"text": token, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3)}
        for i, token in enumerate(tokens)
    ]


def speaker_turns(words: list[dict]) -> list[dict]:
    if not words:
        return []
    turns = []
    current = {"speaker": "turn-1", "start": words[0]["start"], "end": words[0]["end"], "text": words[0]["text"]}
    for word in words[1:]:
        if float(word["start"]) - float(current["end"]) > 0.6:
            turns.append(current)
            current = {
                "speaker": f"turn-{len(turns) + 1}",
                "start": word["start"],
                "end": word["end"],
                "text": word["text"],
            }
        else:
            current["end"] = word["end"]
            current["text"] += " " + word["text"]
    turns.append(current)
    for turn in turns:
        turn["start"] = round(float(turn["start"]), 3)
        turn["end"] = round(float(turn["end"]), 3)
    return turns


def candidate_moments(duration, scenes, silences, motion, faces, words) -> list[dict]:
    if duration <= 0:
        return []
    bins = []
    t = 0.0
    energies = [m["energy"] for m in motion] or [0]
    peak = max(energies) or 1
    while t < duration - 0.15:
        t1 = min(duration, t + 0.5)
        energy = 0.0
        count = 0
        for m in motion:
            if t <= m["t"] < t1:
                energy += m["energy"]
                count += 1
        energy = (energy / count) / peak if count else 0
        speech = any(t <= (w["start"] + w["end"]) / 2 < t1 for w in words)
        silent = any(s["start"] <= t and s["end"] >= t1 - 0.05 for s in silences)
        face = False
        face_area = 0.0
        for track in faces:
            for sample in track["samples"]:
                if t <= sample["t"] < t1:
                    face = True
                    face_area = max(face_area, sample["w"] * sample["h"])
        near_cut = any(abs(c - t) < 0.25 for c in scenes)
        score = energy * 1.3 + (0.8 if speech else 0) + (0.35 if face else 0) + face_area + (0.25 if near_cut else 0)
        if silent and not speech:
            score -= 0.9
        snippet = " ".join(w["text"] for w in words if t <= (w["start"] + w["end"]) / 2 < t1)
        reasons = []
        if speech:
            reasons.append("speech")
        if energy > 0.55:
            reasons.append("motion")
        if face:
            reasons.append("face")
        if near_cut:
            reasons.append("scene-cut")
        if silent and not speech:
            reasons.append("silence")
        bins.append({
            "start": round(t, 3),
            "end": round(t1, 3),
            "score": round(score, 3),
            "reasons": reasons,
            "text": snippet[:80],
        })
        t += 0.5
    ranked = sorted(bins, key=lambda b: b["score"], reverse=True)
    picked = []
    for moment in ranked:
        if any(abs(moment["start"] - p["start"]) < 1.0 for p in picked):
            continue
        picked.append(moment)
        if len(picked) == 6:
            break
    return picked


def ffprobe_ok() -> bool:
    try:
        run(["ffmpeg", "-version"], "ffmpeg")
        return True
    except Exception:
        return False
