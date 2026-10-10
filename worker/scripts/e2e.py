"""Real video + Ollama tool calls + FFmpeg. Writes evidence under /opt/cursor/artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))

from app.agent import run_turn  # noqa: E402
from app.ffmpeg_util import probe  # noqa: E402
from app.plan_ops import timeline  # noqa: E402
from app.store import Store  # noqa: E402

SAMPLE = Path(
    "/home/ubuntu/.cursor/projects/workspace/uploads/"
    "56d15103b08203b90e053f547b9adafafdfdbdbb5e858e50cfcb22457d147f4f_387c.mp4"
)
ART = Path("/opt/cursor/artifacts")
WORK = Path("/tmp/capcut-e2e")


def log(event: str, data: dict) -> None:
    preview = json.dumps(data, ensure_ascii=False)[:240]
    print(f"[{event}] {preview}", flush=True)


def make_talking(sample: Path, dest: Path) -> None:
    wav = dest.with_suffix(".wav")
    subprocess.check_call(
        ["espeak-ng", "-v", "en", "-s", "130", "-w", str(wav), "Wait for the punchline. Look again now."],
        stdout=subprocess.DEVNULL,
    )
    # Keep the real picture. Replace the unclear audio with speech so word-zoom can be checked.
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(sample), "-i", str(wav),
            "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            str(dest),
        ],
        stdout=subprocess.DEVNULL,
    )


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    if WORK.exists():
        shutil.rmtree(WORK)
    talking = WORK / "talking.mp4"
    talking.parent.mkdir(parents=True)
    make_talking(SAMPLE, talking)
    sample_hash = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()

    store = Store(WORK / "projects")
    project = store.create("sample.mp4", talking)
    before = project.original_hash

    turns = [
        "Gör den här till en viral Short",
        "gör texten större",
        "första delen är för lång",
        "zooma in när han säger punchline",
        "undo",
    ]
    replies = []
    for text in turns:
        print("\n=== USER:", text, flush=True)
        reply = run_turn(project, text, log)
        clips = project.editor.plan["clips"]
        zoom = max([max(float(c.get("zoomStart", 1)), float(c.get("zoomEnd", 1))) for c in clips], default=1)
        _, total = timeline(project.editor.plan)
        replies.append({
            "user": text,
            "assistant": reply,
            "active": project.editor.active,
            "clips": len(clips),
            "font": project.editor.plan["style"]["captionFontSize"],
            "zoom": zoom,
            "duration": round(total, 3),
        })
        print("ASSISTANT:", reply, flush=True)

    versions = []
    for version in project.editor.versions:
        path = Path(version["output"])
        info = probe(path)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        versions.append({
            "version": version["version"],
            "bytes": path.stat().st_size,
            "width": video["width"],
            "height": video["height"],
            "codec": video["codec_name"],
            "pix_fmt": video["pix_fmt"],
            "duration": float(info["format"]["duration"]),
            "probe": version.get("probe"),
        })
        shutil.copy(path, ART / f"v{version['version']}.mp4")
        frame = ART / f"v{version['version']}-frame.jpg"
        subprocess.check_call(
            ["ffmpeg", "-y", "-v", "error", "-ss", "0.4", "-i", str(path), "-frames:v", "1", "-q:v", "3", str(frame)],
            stdout=subprocess.DEVNULL,
        )
        shutil.copy(project.folder / "versions" / f"v{version['version']}" / "plan.json", ART / f"v{version['version']}-plan.json")

    after = hashlib.sha256(project.original.read_bytes()).hexdigest()
    report = {
        "sampleSha256": sample_hash,
        "projectOriginalUnchanged": before == after,
        "activeVersion": project.editor.active,
        "versions": versions,
        "turns": replies,
        "finalPlanClips": project.editor.plan["clips"],
        "captionFontSize": project.editor.plan["style"]["captionFontSize"],
        "headlineFontSize": project.editor.plan["style"]["headlineFontSize"],
    }
    (ART / "e2e-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not versions or versions[0]["width"] != 1080 or versions[0]["height"] != 1920 or versions[0]["codec"] != "h264":
        raise SystemExit("v1 is not a 1080x1920 h264 file")
    if before != after:
        raise SystemExit("original was modified")
    if replies[0]["active"] is None:
        raise SystemExit("first prompt did not render a version")
    fonts = [v["plan"]["style"]["captionFontSize"] for v in project.editor.versions]
    if max(fonts) <= 72 and max(fonts) <= min(fonts):
        raise SystemExit(f"text size never changed: {fonts}")
    zoomed = any(
        float(clip.get("zoomStart", 1)) > 1.05 or float(clip.get("zoomEnd", 1)) > 1.05
        for version in project.editor.versions
        for clip in version["plan"]["clips"]
    )
    if not zoomed:
        raise SystemExit("no rendered plan zoomed in")
    durations = [item["duration"] for item in versions]
    if max(durations) - min(durations) < 0.15:
        raise SystemExit(f"trim did not change duration: {durations}")
    newest = max(item["version"] for item in versions)
    if project.editor.active >= newest:
        raise SystemExit(f"undo did not step back from v{newest} (active {project.editor.active})")


if __name__ == "__main__":
    main()
