"""Sana clip: viral first cut, Swedish zoom on his reaction, then undo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("SHORT_DELIVERY", "phone")
os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("MODEL", "qwen2.5:3b")

SOURCE = Path(os.environ.get(
    "SANA_CLIP",
    "/home/ubuntu/.cursor/projects/workspace/uploads/9010595258876bd5290f5c776e3f192aa07445c04ff4a16a499c30315bea06c6_5b48.mp4",
))
OUT = Path(os.environ.get("ACCEPT_OUT", "/opt/cursor/artifacts/sana-viral"))
LINES = [
    "Gör den här till en viral Short. Behåll originalljudet. Ingen musik.",
    "mer zoom på hans reaktion",
    "undo",
]


def main() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    OUT.mkdir(parents=True, exist_ok=True)
    if not SOURCE.exists():
        raise SystemExit(f"missing source {SOURCE}")
    client = TestClient(app)
    health = client.get("/health")
    (OUT / "health.json").write_text(health.text)
    with SOURCE.open("rb") as handle:
        created = client.post("/projects", files={"file": ("sana.mp4", handle, "video/mp4")})
    created.raise_for_status()
    project = created.json()
    project_id = project["id"]
    original_hash = project["originalHash"]
    report = {"project": project_id, "originalHash": original_hash, "health": health.json(), "steps": []}
    for index, line in enumerate(LINES, start=1):
        path = "/short" if index == 1 else "/rerender"
        body = {"prompt": line} if index == 1 else {"lines": [line]}
        events = _read_sse(client, f"/projects/{project_id}{path}", body)
        errors = [data for name, data in events if name == "error"]
        done = next((data for name, data in events if name == "done"), None)
        step = {
            "line": line,
            "events": [name for name, _data in events],
            "error": errors[-1] if errors else None,
            "reply": (done or {}).get("reply"),
            "backend": (done or {}).get("backend"),
            "packaging": (done or {}).get("packaging"),
        }
        if done and done.get("activeVersion"):
            version = int(done["activeVersion"])
            video = client.get(f"/projects/{project_id}/versions/{version}/video")
            video.raise_for_status()
            target = OUT / f"v{version}.mp4"
            target.write_bytes(video.content)
            probe = _probe(target)
            step["file"] = str(target)
            step["ffprobe"] = _brief(probe)
            step["faststart"] = _faststart(target)
            _frames(target, OUT / f"v{version}")
            plan = client.get(f"/projects/{project_id}/versions/{version}/plan").json()["plan"]
            (OUT / f"v{version}-plan.json").write_text(json.dumps(_plan_brief(plan), ensure_ascii=False, indent=2))
            (OUT / f"v{version}-packaging.json").write_text(json.dumps((plan.get("studio") or {}).get("packaging") or {}, ensure_ascii=False, indent=2))
            step["clips"] = [
                {
                    "sourceStart": clip.get("sourceStart"),
                    "sourceEnd": clip.get("sourceEnd"),
                    "speed": clip.get("speed"),
                    "zoomStart": clip.get("zoomStart"),
                    "zoomEnd": clip.get("zoomEnd"),
                    "focusTrack": clip.get("focusTrack"),
                    "label": clip.get("label"),
                }
                for clip in plan.get("clips") or []
            ]
            step["audio"] = plan.get("audio")
            step["hookType"] = ((plan.get("studio") or {}).get("story") or {}).get("hookType")
            step["emotion"] = ((plan.get("studio") or {}).get("story") or {}).get("emotion")
            step["secondPass"] = (plan.get("studio") or {}).get("secondPass")
            step["gate"] = (plan.get("studio") or {}).get("gate")
        report["steps"].append(step)
        print(index, "ERROR" if errors else "ok", line, flush=True)
    current = client.get(f"/projects/{project_id}").json()
    report["originalUnchanged"] = current["originalHash"] == original_hash
    report["versions"] = current["versions"]
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["originalUnchanged"]:
        raise SystemExit("original file hash changed")
    failed = [step for step in report["steps"] if step.get("error") or not step.get("ffprobe")]
    if failed:
        raise SystemExit(f"{len(failed)} step(s) failed; see {OUT / 'report.json'}")


def _read_sse(client, path: str, body: dict) -> list[tuple[str, dict]]:
    events = []
    with client.stream("POST", path, json=body) as response:
        response.raise_for_status()
        name = "message"
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    events.append((name, json.loads("\n".join(data_lines))))
                name, data_lines = "message", []
                continue
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((name, json.loads("\n".join(data_lines))))
    return events


def _probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def _brief(data: dict) -> dict:
    video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in data["streams"] if stream["codec_type"] == "audio")
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "codec": video.get("codec_name"),
        "profile": video.get("profile"),
        "pixFmt": video.get("pix_fmt"),
        "audio": audio.get("codec_name"),
        "duration": (data.get("format") or {}).get("duration"),
        "fps": video.get("avg_frame_rate"),
    }


def _faststart(path: Path) -> bool:
    blob = path.read_bytes()[:4_000_000]
    return blob.find(b"moov") >= 0 and (blob.find(b"mdat") < 0 or blob.find(b"moov") < blob.find(b"mdat"))


def _frames(path: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    duration = float(_probe(path)["format"]["duration"])
    for index, moment in enumerate((0.4, duration / 2, max(0.2, duration - 0.4)), start=1):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1", str(folder / f"frame{index}.jpg")],
            check=True,
        )


def _plan_brief(plan: dict) -> dict:
    studio = plan.get("studio") or {}
    return {
        "clips": plan.get("clips"),
        "captions": plan.get("captions"),
        "effects": plan.get("effects"),
        "audio": plan.get("audio"),
        "decision": plan.get("decision"),
        "story": studio.get("story"),
        "packaging": studio.get("packaging"),
        "critique": studio.get("critique"),
        "gate": studio.get("gate"),
        "trends": studio.get("trends"),
        "secondPass": studio.get("secondPass"),
    }


if __name__ == "__main__":
    main()
