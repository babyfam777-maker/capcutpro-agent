"""Upload the Sana clip, run the four acceptance lines, and check each MP4."""

from __future__ import annotations

import json
import os
import shutil
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
OUT = Path(os.environ.get("ACCEPT_OUT", "/opt/cursor/artifacts/sana"))
LINES = [
    "Create a vertical 9:16 Short. Keep the original audio. Do not add background music.",
    "Remove the opening two seconds and zoom in on the main subject.",
    "Undo that change.",
    "Remove the last caption and render again.",
]


def main() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    OUT.mkdir(parents=True, exist_ok=True)
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
        step = {"line": line, "events": [item[0] for item in events]}
        errors = [data for name, data in events if name == "error"]
        done = next((data for name, data in events if name == "done"), None)
        step["error"] = errors[-1] if errors else None
        step["done"] = done
        if done and done.get("activeVersion"):
            version = int(done["activeVersion"])
            video = client.get(f"/projects/{project_id}/versions/{version}/video")
            video.raise_for_status()
            target = OUT / f"v{version}.mp4"
            target.write_bytes(video.content)
            step["file"] = str(target)
            step["ffprobe"] = _probe(target)
            step["faststart"] = _faststart(target)
            _frames(target, OUT / f"v{version}")
            plan = client.get(f"/projects/{project_id}/versions/{version}/plan").json()
            (OUT / f"v{version}-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            step["plan"] = {
                "clips": plan["plan"].get("clips"),
                "captions": plan["plan"].get("captions"),
                "effects": plan["plan"].get("effects"),
                "audio": plan["plan"].get("audio"),
                "decision": plan["plan"].get("decision"),
            }
        report["steps"].append(step)
        print(index, line, "ERROR" if errors else "ok", flush=True)
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


def _faststart(path: Path) -> bool:
    blob = path.read_bytes()[:4_000_000]
    return blob.find(b"moov") >= 0 and (blob.find(b"mdat") < 0 or blob.find(b"moov") < blob.find(b"mdat"))


def _frames(path: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    duration = float(_probe(path)["format"]["duration"])
    stamps = [0.4, duration / 2, max(0.2, duration - 0.4)]
    for index, stamp in enumerate(stamps, start=1):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{stamp:.3f}", "-i", str(path), "-frames:v", "1", str(folder / f"frame{index}.jpg")],
            check=True,
        )


if __name__ == "__main__":
    main()
