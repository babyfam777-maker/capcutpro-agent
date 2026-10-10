"""Project files. The original video is written once and never replaced."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import threading
import uuid
from pathlib import Path

from .analysis import analyze
from .config import DATA_DIR
from .ffmpeg_util import video_meta
from .plan_ops import Editor, compile_plan, timeline
from .render import render_plan


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def _read(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


class Project:
    def __init__(self, folder: Path):
        self.folder = folder
        self.id = folder.name
        self.state = _read(folder / "state.json") or {}
        self.filename = self.state.get("filename") or "video.mp4"
        self.original = folder / "original" / self.state.get("originalName", "source.mp4")
        self.original_hash = self.state.get("originalHash")
        self.meta = self.state.get("meta") or {}
        self.messages = _read(folder / "messages.json") or []
        analysis = _read(folder / "analysis.json")
        plan = _read(folder / "plan.json")
        self.editor = Editor(analysis, plan)
        self.editor.active = self.state.get("activeVersion")
        self.editor.dirty = bool(self.state.get("dirty"))
        self.editor.versions = self._load_versions()
        self.busy = False

    def _load_versions(self) -> list[dict]:
        root = self.folder / "versions"
        versions = []
        if not root.exists():
            return versions
        for path in sorted(root.glob("v*/plan.json")):
            version = int(path.parent.name[1:])
            versions.append({
                "version": version,
                "plan": json.loads(path.read_text()),
                "probe": _read(path.parent / "probe.json"),
                "output": str(path.parent / "output.mp4"),
            })
        return versions

    def public(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "meta": self.meta,
            "analyzed": self.editor.analysis is not None,
            "activeVersion": self.editor.active,
            "dirty": self.editor.dirty,
            "busy": self.busy,
            "originalHash": self.original_hash,
            "versions": [
                {
                    "version": v["version"],
                    "duration": (v.get("probe") or {}).get("duration"),
                    "loudness": (v.get("probe") or {}).get("loudness"),
                    "width": (v.get("probe") or {}).get("width"),
                    "height": (v.get("probe") or {}).get("height"),
                }
                for v in self.editor.versions
            ],
            "plan": self.editor.public_plan(),
            "messages": [{"role": m["role"], "content": m.get("content", ""), "trace": m.get("trace") or []} for m in self.messages],
        }

    def save(self) -> None:
        _write(self.folder / "plan.json", self.editor.plan)
        _write(self.folder / "messages.json", self.messages)
        self.state["activeVersion"] = self.editor.active
        self.state["dirty"] = self.editor.dirty
        _write(self.folder / "state.json", self.state)

    def save_messages(self) -> None:
        _write(self.folder / "messages.json", self.messages)

    def analyze(self, on_event) -> dict:
        if self.editor.analysis:
            from .analysis import summary_for_model
            return {"ok": True, "cached": True, "analysis": summary_for_model(self.editor.analysis)}
        on_event("status", {"step": "analyze"})
        analysis = analyze(self.original, on_progress=lambda step: on_event("status", {"step": step}))
        self.editor.analysis = analysis
        _write(self.folder / "analysis.json", analysis)
        from .analysis import summary_for_model
        return {"ok": True, "analysis": summary_for_model(analysis)}

    def commit(self, on_event, force: bool = False) -> dict:
        problems = _problems(self.editor)
        if problems and not force:
            return {"ok": False, "error": "Planen behöver justeras innan render.", "problems": problems}
        if not self.editor.plan["clips"]:
            return {"ok": False, "error": "Det finns inga klipp att rendera."}
        on_event("status", {"step": "render"})
        compiled = compile_plan(self.editor.plan, self.editor.analysis)
        version = max([v["version"] for v in self.editor.versions], default=0) + 1
        folder = self.folder / "versions" / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "output.mp4"
        probe = render_plan(
            self.original,
            self.editor.analysis or {"source": self.meta, "faces": []},
            compiled,
            out,
            on_progress=lambda progress: on_event("progress", {"progress": round(progress, 3)}),
        )
        _write(folder / "plan.json", compiled)
        _write(folder / "probe.json", probe)
        before = self.original_hash
        after = hashlib.sha256(self.original.read_bytes()).hexdigest()
        if before and before != after:
            raise RuntimeError("Originalfilen ändrades under render. Det ska inte kunna hända.")
        self.editor.plan = compiled
        self.editor.dirty = False
        self.editor.undo_stack.clear()
        self.editor.active = version
        self.editor.versions.append({"version": version, "plan": copy.deepcopy(compiled), "probe": probe, "output": str(out)})
        self.save()
        _, total = timeline(compiled)
        return {
            "ok": True,
            "version": version,
            "videoUrl": f"/projects/{self.id}/versions/{version}/video",
            "probe": probe,
            "outputDuration": round(total, 3),
            "summary": f"v{version} rendered, {probe['duration']:.1f}s, {probe['width']}x{probe['height']} {probe['videoCodec']}.",
        }


def _problems(editor: Editor) -> list[str]:
    rows, total = timeline(editor.plan)
    problems = []
    if not rows:
        problems.append("Inga klipp. Anropa select_clip på de starkaste ögonblicken.")
        return problems
    if total > 60:
        problems.append(f"Tidslinjen är {total:.1f}s. Korta den till under 60s.")
    if editor.analysis and len(rows) == 1:
        silence = float(editor.analysis.get("silenceTotal") or 0)
        duration = float(editor.analysis["source"]["duration"])
        covered = float(rows[0]["sourceEnd"]) - float(rows[0]["sourceStart"])
        if silence > 0.8 and covered > duration * 0.92:
            problems.append("Nästan hela källan är kvar trots tystnad. Klipp bort död tid med flera select_clip.")
    texts = [e for e in editor.plan["effects"] if e["type"] == "text"] + list(editor.plan["captions"])
    if not any(float(item["start"]) < 0.8 for item in texts):
        problems.append("Ingen text i första 0,8s. add_text en engelsk headline på 1-3 ord.")
    return problems


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or DATA_DIR / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._projects: dict[str, Project] = {}

    def create(self, filename: str, source: Path) -> Project:
        project_id = uuid.uuid4().hex[:12]
        folder = self.root / project_id
        original_dir = folder / "original"
        original_dir.mkdir(parents=True)
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)[-80:] or "video.mp4"
        original = original_dir / safe
        shutil.copyfile(source, original)
        meta = video_meta(original)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        state = {
            "id": project_id,
            "filename": filename,
            "originalName": safe,
            "originalHash": digest,
            "meta": meta,
            "activeVersion": None,
            "dirty": False,
        }
        _write(folder / "state.json", state)
        _write(folder / "plan.json", Editor().plan)
        _write(folder / "messages.json", [])
        project = Project(folder)
        with self._lock:
            self._projects[project_id] = project
        return project

    def get(self, project_id: str) -> Project:
        with self._lock:
            cached = self._projects.get(project_id)
            if cached:
                return cached
        folder = self.root / project_id
        if not (folder / "state.json").exists():
            raise KeyError(project_id)
        project = Project(folder)
        with self._lock:
            self._projects[project_id] = project
        return project


store = Store()
