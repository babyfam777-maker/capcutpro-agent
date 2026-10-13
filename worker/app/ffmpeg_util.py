import json
import subprocess
from pathlib import Path


def run(cmd: list[str], description: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-20:]
        raise RuntimeError(f"{description} misslyckades:\n" + "\n".join(detail))
    return result


def probe(path: Path) -> dict:
    result = run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        "ffprobe",
    )
    return json.loads(result.stdout or "{}")


def video_meta(path: Path) -> dict:
    data = probe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or not video.get("width") or not video.get("height"):
        raise RuntimeError("Filen har ingen videospelare som FFmpeg kan läsa.")
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = abs(int(side["rotation"]))
    if not rotation:
        rotation = abs(int((video.get("tags") or {}).get("rotate") or 0))
    rotated = rotation in (90, 270)
    width = int(video["height"] if rotated else video["width"])
    height = int(video["width"] if rotated else video["height"])
    rate = video.get("avg_frame_rate") or "30/1"
    num, _, den = rate.partition("/")
    fps = float(num) / float(den) if den else float(num or 30)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "hasAudio": audio is not None,
        "audioRate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": int(audio["channels"]) if audio and audio.get("channels") else 0,
    }
