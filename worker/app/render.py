"""Turn a plan into a real 1080x1920 H.264 MP4. The model never calls FFmpeg itself."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from . import gfx
from .config import FPS, OUT_H, OUT_W
from .ffmpeg_util import probe, run
from .plan_ops import timeline
from .sfx import ensure_sfx

SAFE_TOP = 220
SAFE_BOTTOM = 320
SAFE_SIDE = 60


def render_plan(original: Path, analysis: dict, plan: dict, out_path: Path, on_progress=None) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows, _ = timeline(plan)
    if not rows:
        raise RuntimeError("Planen har inga klipp.")
    sfx = ensure_sfx()
    with tempfile.TemporaryDirectory(prefix="capcut-") as tmp:
        tmp_path = Path(tmp)
        speech = tmp_path / "speech.wav"
        mastered = tmp_path / "master.wav"
        _build_speech(original, analysis, plan, speech)
        _mix(speech, plan, sfx, tmp_path / "mixed.wav")
        _loudnorm(tmp_path / "mixed.wav", mastered)
        _encode(original, analysis, plan, mastered, out_path, on_progress)
    info = _inspect(out_path)
    return info


def _build_speech(original: Path, analysis: dict, plan: dict, dest: Path) -> None:
    rows, _ = timeline(plan)
    has_audio = bool(analysis["source"]["hasAudio"]) and plan["audio"].get("keepOriginal", True)
    parts = []
    labels = []
    for i, row in enumerate(rows):
        frames = max(1, round((row["outEnd"] - row["outStart"]) * FPS))
        dur = frames / FPS
        if not has_audio or float(row["speed"]) <= 0.001:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.4f},asetpts=PTS-STARTPTS[a{i}]")
        else:
            speed = float(row["speed"])
            chain = (
                f"[0:a]atrim=start={float(row['sourceStart']):.4f}:end={float(row['sourceEnd']):.4f},"
                "asetpts=PTS-STARTPTS"
            )
            if abs(speed - 1) > 0.02:
                chain += f",atempo={speed:.4f}"
            chain += (
                f",aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=0:{dur:.4f},apad=pad_dur={dur:.4f},atrim=0:{dur:.4f}[a{i}]"
            )
            parts.append(chain)
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={len(rows)}:v=0:a=1[speech]")
    script = dest.with_suffix(".txt")
    script.write_text(";\n".join(parts))
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(original), "-filter_complex_script", str(script),
         "-map", "[speech]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dest)],
        "Ljudklipp",
    )


def _mix(speech: Path, plan: dict, sfx: dict[str, Path], dest: Path) -> None:
    events = [e for e in plan["effects"] if e["type"] == "sound_effect" and e["sfx"] in sfx]
    if not events:
        dest.write_bytes(speech.read_bytes())
        return
    parts = []
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(speech)]
    labels = []
    for i, event in enumerate(events):
        cmd += ["-i", str(sfx[event["sfx"]])]
        delay = max(0, int(float(event["time"]) * 1000))
        volume = 10 ** (float(event["gainDb"]) / 20)
        parts.append(
            f"[{i+1}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay},volume={volume:.5f}[s{i}]"
        )
        labels.append(f"[s{i}]")
    parts.append(f"[0:a]{''.join(labels)}amix=inputs={1+len(labels)}:duration=first:dropout_transition=0:normalize=0[mix]")
    script = dest.with_suffix(".txt")
    script.write_text(";\n".join(parts))
    cmd += ["-filter_complex_script", str(script), "-map", "[mix]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dest)]
    run(cmd, "Ljudeffekter")


def _loudnorm(src: Path, dest: Path) -> None:
    # Peaks from impact SFX would otherwise stop loudnorm from reaching -14 LUFS.
    chain = "acompressor=threshold=-18dB:ratio=4:attack=5:release=60:makeup=6,alimiter=limit=0.63,loudnorm=I=-14:TP=-1.5:LRA=11"
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", chain, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dest)],
        "Loudnorm",
    )
    measured = _loudness(dest)
    if measured is None or abs(measured + 14) <= 0.6:
        return
    gain = max(-8.0, min(8.0, -14 - measured))
    nudged = dest.with_name("nudged.wav")
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(dest), "-af", f"volume={gain:.2f}dB,alimiter=limit=0.89",
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(nudged)],
        "Loudnorm-korrigering",
    )
    nudged.replace(dest)


def _encode(original: Path, analysis: dict, plan: dict, audio: Path, out_path: Path, on_progress) -> None:
    rows, _ = timeline(plan)
    sw, sh = int(analysis["source"]["width"]), int(analysis["source"]["height"])
    jobs = []
    total = 0
    for row in rows:
        frames = max(1, round((row["outEnd"] - row["outStart"]) * FPS))
        jobs.append((row, frames))
        total += frames
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "pipe:0",
        "-i", str(audio),
        "-vf", "unsharp=5:5:0.28:5:5:0.0",
        "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-shortest", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    cache: dict = {}
    smooth = None
    done = 0
    flash = any(e["type"] == "text" and e.get("kind") == "headline" and float(e["start"]) < 0.2 for e in plan["effects"])
    try:
        for row, frames in jobs:
            decoded = _decode_clip(original, row, frames, sw, sh)
            for index, frame in enumerate(decoded):
                u = index / max(1, frames - 1)
                out_t = float(row["outStart"]) + index / FPS
                src_span = float(row["sourceEnd"]) - float(row["sourceStart"])
                src_t = float(row["sourceStart"]) if float(row["speed"]) <= 0.001 else float(row["sourceStart"]) + src_span * u
                target = _focus_point(analysis, row, src_t)
                if smooth is None:
                    smooth = target
                else:
                    smooth = (smooth[0] * 0.65 + target[0] * 0.35, smooth[1] * 0.65 + target[1] * 0.35)
                zoom = float(row["zoomStart"]) + (float(row["zoomEnd"]) - float(row["zoomStart"])) * u
                image, window = _crop(frame, smooth[0], smooth[1], zoom)
                if flash and done < 2:
                    mix = 0.42 if done == 0 else 0.18
                    image = np.clip(image.astype(np.float32) * (1 - mix) + 255 * mix, 0, 255).astype(np.uint8)
                faces = _project_faces(analysis, src_t, window, sw, sh)
                _composite(image, plan, out_t, faces, cache)
                proc.stdin.write(image.tobytes())
                done += 1
                if on_progress and done % 10 == 0:
                    on_progress(done / total)
        proc.stdin.close()
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"FFmpeg-kodning misslyckades:\n{stderr[-2000:]}")
    except Exception:
        proc.kill()
        raise
    if on_progress:
        on_progress(1)


def _decode_clip(original: Path, row: dict, frames: int, sw: int, sh: int) -> list[np.ndarray]:
    if float(row["speed"]) <= 0.001:
        one = _read_frames(original, float(row["sourceStart"]), float(row["sourceStart"]) + 0.08, 1, sw, sh)
        return one * frames
    return _read_frames(original, float(row["sourceStart"]), float(row["sourceEnd"]), frames, sw, sh)


def _read_frames(original: Path, start: float, end: float, count: int, sw: int, sh: int) -> list[np.ndarray]:
    span = max(0.04, end - start)
    fps = count / span
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(original),
        "-ss", f"{start:.3f}", "-to", f"{max(end, start + 0.04):.3f}",
        "-vf", f"fps={fps:.5f},scale={sw}:{sh}:flags=bilinear",
        "-frames:v", str(count),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    needed = sw * sh * 3
    frames = []
    for _ in range(count):
        raw = proc.stdout.read(needed)
        if len(raw) < needed:
            break
        frames.append(np.frombuffer(raw, dtype=np.uint8).reshape((sh, sw, 3)).copy())
    proc.wait()
    if not frames:
        frames.append(np.zeros((sh, sw, 3), dtype=np.uint8))
    while len(frames) < count:
        frames.append(frames[-1])
    return frames


def _focus_point(analysis: dict, row: dict, src_t: float) -> tuple[float, float]:
    box = _face_at(analysis, row.get("focusTrack"), src_t)
    if box:
        return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    return float(row.get("focusX") or 0.5), float(row.get("focusY") or 0.45)


def _face_at(analysis: dict | None, track_id: str | None, src_t: float) -> dict | None:
    if not analysis or not track_id:
        return None
    track = next((f for f in analysis.get("faces") or [] if f["id"] == track_id), None)
    if not track or not track["samples"]:
        return None
    samples = track["samples"]
    if src_t <= samples[0]["t"]:
        return samples[0]
    if src_t >= samples[-1]["t"]:
        return samples[-1]
    for a, b in zip(samples, samples[1:]):
        if a["t"] <= src_t <= b["t"]:
            span = max(1e-4, b["t"] - a["t"])
            u = (src_t - a["t"]) / span
            return {k: a[k] + (b[k] - a[k]) * u for k in ("x", "y", "w", "h")}
    return samples[-1]


def _crop(frame: np.ndarray, fx: float, fy: float, zoom: float) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    import cv2

    sh, sw = frame.shape[:2]
    zoom = min(2.4, max(1.0, zoom))
    target = OUT_W / OUT_H
    if sw / sh > target:
        vis_h = sh / zoom
        vis_w = vis_h * target
    else:
        vis_w = sw / zoom
        vis_h = vis_w / target
    cx, cy = min(max(fx, 0), 1) * sw, min(max(fy, 0), 1) * sh
    x0 = min(max(cx - vis_w / 2, 0), max(0, sw - vis_w))
    y0 = min(max(cy - vis_h / 2, 0), max(0, sh - vis_h))
    x1, y1 = x0 + vis_w, y0 + vis_h
    crop = frame[int(y0) : max(int(y0) + 1, int(y1)), int(x0) : max(int(x0) + 1, int(x1))]
    if crop.size == 0:
        crop = frame
    resized = cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA if zoom <= 1.05 else cv2.INTER_LINEAR)
    return resized, (x0, y0, vis_w, vis_h)


def _project_faces(analysis: dict, src_t: float, window, sw: int, sh: int) -> list[dict]:
    x0, y0, vw, vh = window
    faces = []
    for track in analysis.get("faces") or []:
        box = _face_at(analysis, track["id"], src_t)
        if not box:
            continue
        ox = (box["x"] * sw - x0) / vw * OUT_W
        oy = (box["y"] * sh - y0) / vh * OUT_H
        ow = box["w"] * sw / vw * OUT_W
        oh = box["h"] * sh / vh * OUT_H
        if ow < 12 or oh < 12 or ox > OUT_W or oy > OUT_H or ox + ow < 0 or oy + oh < 0:
            continue
        faces.append({"id": track["id"], "x": ox, "y": oy, "w": ow, "h": oh})
    return faces


def _composite(image: np.ndarray, plan: dict, out_t: float, faces: list[dict], cache: dict) -> None:
    order = {"circle": 0, "arrow": 1, "emoji": 2, "badge": 3, "text": 4}
    effects = [e for e in plan["effects"] if e["type"] != "sound_effect" and float(e["start"]) <= out_t < float(e["end"])]
    effects.sort(key=lambda e: order.get(e["type"], 5))
    for effect in effects:
        sprite = _effect_sprite(effect, cache)
        local = out_t - float(effect["start"])
        if effect["type"] == "emoji":
            sprite = gfx.scale_sprite(sprite, gfx.pop_scale(local))
        elif effect["type"] == "text" and effect.get("kind") == "headline":
            sprite = gfx.scale_sprite(sprite, gfx.slam_scale(local))
        elif effect["type"] == "circle":
            sprite = gfx.scale_sprite(sprite, 1.015 + 0.065 * np.sin(2 * np.pi * 2 * out_t))
        x, y = _anchor(effect, sprite, faces)
        gfx.blit(image, sprite, x, y)
    for caption in plan["captions"]:
        if float(caption["start"]) <= out_t < float(caption["end"]):
            sprite = _caption_sprite(caption, cache)
            local = out_t - float(caption["start"])
            sprite = gfx.scale_sprite(sprite, gfx.pop_scale(local) if local < 0.18 else 1)
            x, y = _avoid(sprite, faces, prefer_y=int(OUT_H * 0.72))
            gfx.blit(image, sprite, x, y)


def _effect_sprite(effect: dict, cache: dict):
    key = (effect["id"], effect["type"], effect.get("text"), effect.get("emoji"), effect.get("fontSize"), effect.get("kind"))
    if key in cache:
        return cache[key]
    if effect["type"] == "text":
        kind = effect.get("kind") or "headline"
        if kind == "story":
            sprite = gfx.story_sprite(effect["text"], int(effect.get("fontSize") or 64))
        elif kind == "bubble":
            sprite = gfx.bubble_sprite(effect["text"], int(effect.get("fontSize") or 48))
        else:
            sprite = gfx.headline_sprite(effect["text"], effect.get("accent"), int(effect.get("fontSize") or 120))
    elif effect["type"] == "emoji":
        sprite = gfx.emoji_sprite(effect["emoji"], 150)
    elif effect["type"] == "arrow":
        sprite = gfx.arrow_sprite().rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
    elif effect["type"] == "circle":
        sprite = gfx.circle_sprite(100)
    else:
        sprite = gfx.badge_sprite(effect.get("text") or "")
    cache[key] = sprite
    return sprite


def _caption_sprite(caption: dict, cache: dict):
    key = ("cap", caption["id"], caption["text"], caption.get("accent"), caption.get("fontSize"))
    if key not in cache:
        sprites = gfx.caption_sprites(caption["text"], caption.get("accent") or "", int(caption.get("fontSize") or 72))
        cache[key] = gfx._row(sprites, gap=16)
    return cache[key]


def _anchor(effect: dict, sprite: Image.Image, faces: list[dict]) -> tuple[int, int]:
    face = _match_face(effect.get("track"), faces)
    anchor = effect.get("anchor") or "above"
    if effect["type"] == "text" and effect.get("kind") == "headline":
        return _avoid(sprite, faces, prefer_y=262)
    if effect["type"] == "text" and effect.get("kind") == "story":
        return _avoid(sprite, faces, prefer_y=520)
    if effect["type"] == "badge":
        return _avoid(sprite, faces, prefer_y=240)
    if face is None:
        return _avoid(sprite, faces, prefer_y=300)
    if effect["type"] == "circle":
        x = int(face["x"] + face["w"] / 2 - sprite.width / 2)
        y = int(face["y"] + face["h"] / 2 - sprite.height / 2)
        return x, y
    if anchor == "left":
        x = int(face["x"] - sprite.width - 16)
        y = int(face["y"] + face["h"] / 2 - sprite.height / 2)
    elif anchor == "right":
        x = int(face["x"] + face["w"] + 16)
        y = int(face["y"] + face["h"] / 2 - sprite.height / 2)
    elif anchor == "below" or effect["type"] == "arrow":
        x = int(face["x"] + face["w"] / 2 - sprite.width / 2)
        y = int(face["y"] + face["h"] + 12)
    else:
        x = int(face["x"] + face["w"] / 2 - sprite.width / 2)
        y = int(face["y"] - sprite.height - 24)
    x = int(min(max(x, SAFE_SIDE), OUT_W - SAFE_SIDE - sprite.width))
    y = int(min(max(y, SAFE_TOP), OUT_H - SAFE_BOTTOM - sprite.height))
    return x, y


def _match_face(track: str | None, faces: list[dict]) -> dict | None:
    if track:
        found = next((f for f in faces if f["id"] == track), None)
        if found:
            return found
    if not faces:
        return None
    return max(faces, key=lambda f: f["w"] * f["h"])


def _overlap(ax, ay, aw, ah, bx, by, bw, bh) -> float:
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _avoid(sprite: Image.Image, faces: list[dict], prefer_y: int) -> tuple[int, int]:
    x = int((OUT_W - sprite.width) / 2)
    x = int(min(max(x, SAFE_SIDE), max(SAFE_SIDE, OUT_W - SAFE_SIDE - sprite.width)))
    candidates = [prefer_y, 280, 460, 700, 980, 1280, 1480]
    best = None
    for y in candidates:
        y = int(min(max(y, SAFE_TOP), OUT_H - SAFE_BOTTOM - sprite.height))
        hit = sum(_overlap(x, y, sprite.width, sprite.height, f["x"], f["y"], f["w"], f["h"]) for f in faces)
        # Prefer the requested lane when nothing collides.
        score = hit + abs(y - prefer_y) * 0.01
        if best is None or score < best[0]:
            best = (score, x, y)
    assert best is not None
    return best[1], best[2]


def _inspect(path: Path) -> dict:
    data = probe(path)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    loudness = _loudness(path)
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "videoCodec": video.get("codec_name"),
        "profile": video.get("profile"),
        "pixFmt": video.get("pix_fmt"),
        "audioCodec": audio.get("codec_name") if audio else None,
        "sampleRate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": int(audio["channels"]) if audio and audio.get("channels") else 0,
        "duration": float(data["format"]["duration"]),
        "loudness": loudness,
    }


def _loudness(path: Path) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    log = proc.stderr or ""
    marker = "Integrated loudness:"
    if marker not in log:
        return None
    tail = log.split(marker)[-1]
    for line in tail.splitlines():
        if "I:" in line and "LUFS" in line:
            try:
                return float(line.split("I:")[1].split("LUFS")[0].strip())
            except ValueError:
                return None
    return None
