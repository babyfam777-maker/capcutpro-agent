"""First short and chat re-render. Both go through the existing render_plan."""

from __future__ import annotations

import copy

from .mvp import build_storyboard, plan_from_storyboard, qa_output, renderer_view
from .render import render_plan
from .revisions import apply_edits


def render_first_short(project, prompt: str, on_event) -> dict:
    prompt = " ".join(str(prompt or "").split())
    if not prompt:
        raise RuntimeError("Skriv vad shortsen ska handla om.")
    project.messages.append({"role": "user", "content": prompt})
    project.save_messages()
    on_event("status", {"step": "analyze"})

    def step(name: str) -> None:
        on_event("status", {"step": name})

    found = project.analyze_v2(on_step=step)
    if not found.get("ok"):
        raise RuntimeError(found.get("error") or "Analysen misslyckades.")
    analysis = found["analysis"]
    phrases = [row for row in analysis.get("phrases") or [] if row.get("status", "SUPPORTED") == "SUPPORTED"]
    on_event("analysis", {
        "cached": bool(found.get("cached")),
        "duration": (analysis.get("source") or {}).get("duration"),
        "phrases": len(phrases),
        "faces": len([track for track in analysis.get("tracks") or [] if track.get("kind") == "face"]),
        "scenes": len(analysis.get("scenes") or []),
        "rejected": len([row for row in analysis.get("transcript") or [] if row.get("status") != "SUPPORTED"]),
    })
    on_event("status", {"step": "storyboard"})
    board = build_storyboard(analysis, prompt)
    plan = plan_from_storyboard(board, analysis)
    view = renderer_view(analysis)
    project.set_render_view(view)
    _write_storyboard(project, board)
    previous = copy.deepcopy(project.editor.plan)
    try:
        saved = _render(project, plan, view, on_event)
    except Exception:
        project.editor.plan = previous
        raise
    hook = (board.get("fields") or {}).get("hook") or ""
    reply = (
        f"v{saved['version']} är klar"
        f" ({float((saved.get('probe') or {}).get('duration') or 0):.1f}s, "
        f"{(saved.get('probe') or {}).get('width')}x{(saved.get('probe') or {}).get('height')}). "
        f"Hook: {hook}."
    )
    if board.get("limitations"):
        reply += " " + str(board["limitations"][0])
    project.messages.append({"role": "assistant", "content": reply, "trace": [{"name": "render_plan", "ok": True}]})
    project.save_messages()
    on_event("version", {"activeVersion": saved["version"]})
    on_event("done", {
        "reply": reply,
        "activeVersion": saved["version"],
        "videoUrl": saved["videoUrl"],
        "probe": saved.get("probe"),
        "qa": saved.get("qa"),
        "hook": hook,
    })
    return saved


def render_again(project, lines: list[str], on_event) -> dict:
    cleaned = [" ".join(str(line).split()) for line in lines if str(line).strip()]
    if not cleaned:
        raise RuntimeError("Skriv vad som ska ändras, till exempel: Gör hooken större.")
    if not project.render_view or not (project.editor.plan.get("clips") or project.editor.versions):
        raise RuntimeError("Gör en short först.")
    edited = apply_edits(project.editor.plan, cleaned)
    on_event("edits", {"changes": edited["changes"], "skipped": edited["skipped"]})
    if not edited["ok"]:
        raise RuntimeError(edited["error"])
    for line in cleaned:
        project.messages.append({"role": "user", "content": line})
    project.save_messages()
    previous = copy.deepcopy(project.editor.plan)
    try:
        saved = _render(project, edited["plan"], project.render_view, on_event)
    except Exception:
        project.editor.plan = previous
        raise
    reply = f"v{saved['version']} är klar. " + " ".join(edited["changes"])
    if edited["skipped"]:
        reply += " Förstod inte: " + "; ".join(edited["skipped"]) + "."
    project.messages.append({
        "role": "assistant",
        "content": reply,
        "trace": [{"name": change, "ok": True} for change in edited["changes"]],
    })
    project.save_messages()
    on_event("version", {"activeVersion": saved["version"]})
    on_event("done", {
        "reply": reply,
        "activeVersion": saved["version"],
        "videoUrl": saved["videoUrl"],
        "probe": saved.get("probe"),
        "qa": saved.get("qa"),
        "changes": edited["changes"],
        "skipped": edited["skipped"],
    })
    return saved


def _render(project, plan: dict, view: dict, on_event) -> dict:
    on_event("status", {"step": "render"})
    version, folder = project.next_version_folder()
    out = folder / "output.mp4"
    probe = render_plan(
        project.original,
        view,
        plan,
        out,
        on_progress=lambda progress: on_event("progress", {"progress": round(progress, 3)}),
    )
    on_event("status", {"step": "qa"})
    qa = None
    try:
        analysis = _read_analysis(project)
        qa = qa_output(out, plan, analysis or {"transcript": []})
    except Exception as error:
        qa = {"ok": False, "checks": [{"name": "qa", "ok": False, "detail": str(error)}]}
    on_event("qa", qa)
    return project.save_version(version, plan, probe, qa)


def _write_storyboard(project, board: dict) -> None:
    from .store import _write

    _write(project.folder / "storyboard.json", {
        "prompt": board.get("prompt"),
        "fields": board.get("fields"),
        "beats": [
            {"role": beat.get("role"), "why": beat.get("why"), "text": beat.get("text")}
            for beat in board.get("beats") or []
        ],
        "limitations": board.get("limitations") or [],
    })


def _read_analysis(project) -> dict | None:
    from .store import _read

    return _read(project.folder / "analysis_v2.json")
