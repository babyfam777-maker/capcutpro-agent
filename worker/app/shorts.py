"""First short and chat re-render.

The chat decides the timeline. Rendering still goes through render_plan.
Phone delivery is 1080x1920. Set SHORT_DELIVERY=4k for the labeled upscale.
"""

from __future__ import annotations

import copy
import json

from .config import SHORT_DELIVERY
from .bot_profile import remember_rule_if_requested
from .creative import parse_intent, revise, track_view
from .export_spec import SHORT_EXPORT, describe_export
from .ffmpeg_util import probe
from .render import render_plan
from .studio import (
    apply_feedback_now,
    apply_studio,
    finish_metadata,
    format_reply,
    improve_rendered,
    measure_output,
    parse_feedback,
    store_feedback,
)


def render_first_short(project, prompt: str, on_event) -> dict:
    prompt = " ".join(str(prompt or "").split())
    if not prompt:
        raise RuntimeError("Skriv vad shortsen ska handla om.")
    remember_rule_if_requested(prompt)
    project.messages.append({"role": "user", "content": prompt})
    project.save_messages()
    on_event("status", {"step": "analyze"})
    found = project.analyze_v2(on_step=lambda name: on_event("status", {"step": name}))
    if not found.get("ok"):
        raise RuntimeError(found.get("error") or "Analysen misslyckades.")
    analysis = found["analysis"]
    _emit_analysis(on_event, found)
    on_event("status", {"step": "storyboard"})
    edited = revise(analysis, None, prompt, first=True)
    if not edited.get("ok"):
        raise RuntimeError(edited.get("error") or "Planen kunde inte byggas.")
    on_event("status", {"step": "trends"})
    edited = apply_studio(analysis, edited, prompt, project, first=True)
    on_event("edits", {"changes": edited.get("changes") or [], "skipped": []})
    return _commit(project, analysis, edited, on_event, prompt, first=True)


def render_again(project, lines: list[str], on_event) -> dict:
    cleaned = [" ".join(str(line).split()) for line in lines if str(line).strip()]
    if not cleaned:
        raise RuntimeError("Skriv vad som ska ändras.")
    if not project.render_view or not (project.editor.plan.get("clips") or project.editor.versions):
        raise RuntimeError("Gör en short först.")
    analysis = _read_analysis(project)
    if not analysis:
        raise RuntimeError("Analysen saknas. Gör om shortsen från början, originalfilen ligger kvar.")
    plan = copy.deepcopy(project.editor.plan)
    stack = [copy.deepcopy(item["plan"]) for item in project.editor.versions]
    if not stack:
        stack = [copy.deepcopy(plan)]
    changes: list[str] = []
    backend = "rules"
    model_note = None
    for line in cleaned:
        remember_rule_if_requested(line)
        project.messages.append({"role": "user", "content": line})
        feedback = parse_feedback(line)
        if feedback:
            store_feedback(project, feedback)
        intent = parse_intent(line)
        if feedback and not intent["undo"] and not intent["any_edit"]:
            result = apply_feedback_now(plan, analysis, feedback)
            if not result["changed"]:
                project.save_messages()
                _speak(project, on_event, result["message"], plan)
                return {"ok": True, "version": project.editor.active, "reply": result["message"]}
            plan = result["plan"]
            stack.append(copy.deepcopy(plan))
            changes.append(result["message"])
            backend = "rules"
            continue
        edited = revise(analysis, plan, line, first=False)
        if edited.get("undo"):
            if len(stack) < 2:
                raise RuntimeError("Det finns ingen tidigare version att ångra. Originalfilen och den här planen ligger kvar.")
            stack.pop()
            plan = copy.deepcopy(stack[-1])
            changes.append("Senaste ändringen är ångrad. Tidslinjen är den föregående versionens plan, och den renderas igen.")
            backend = "rules"
            continue
        if not edited.get("ok"):
            project.save_messages()
            raise RuntimeError(edited.get("error") or "Ändringen stoppades.")
        plan = edited["plan"]
        stack.append(copy.deepcopy(plan))
        changes.extend(edited.get("changes") or [])
        backend = edited.get("backend") or backend
        model_note = edited.get("modelNote") or model_note
    project.save_messages()
    on_event("edits", {"changes": changes, "skipped": []})
    edited = {"ok": True, "plan": plan, "changes": changes, "backend": backend, "modelNote": model_note}
    edited = apply_studio(analysis, edited, " ".join(cleaned), project, first=False)
    return _commit(project, analysis, edited, on_event, " ".join(cleaned), first=False)


def _speak(project, on_event, reply: str, plan: dict) -> None:
    project.messages.append({"role": "assistant", "content": reply, "trace": [{"name": "analytics", "ok": True}]})
    project.save_messages()
    on_event("done", {
        "reply": reply,
        "activeVersion": project.editor.active,
        "videoUrl": f"/projects/{project.id}/versions/{project.editor.active}/video" if project.editor.active else None,
        "changes": [],
        "skipped": [],
        "backend": "rules",
        "hook": "",
        "packaging": (plan.get("studio") or {}).get("packaging"),
    })


def _commit(project, analysis: dict, edited: dict, on_event, instruction: str, first: bool = False) -> dict:
    plan = edited["plan"]
    plan.setdefault("decision", {})
    plan["decision"]["backend"] = edited.get("backend")
    plan["decision"]["instruction"] = instruction
    plan["audio"]["music"] = bool(plan.get("audio", {}).get("music"))
    if plan["audio"]["music"]:
        raise RuntimeError("Planen ville lägga på musik. Det görs inte utan en uttrycklig begäran, och ingen fil skrevs.")
    view = track_view(analysis)
    project.set_render_view(view)
    _write_timeline(project, plan, edited)
    previous = copy.deepcopy(project.editor.plan)
    try:
        saved = _render(project, plan, view, analysis, on_event, edited, instruction, first)
    except Exception:
        project.editor.plan = previous
        project.save()
        raise
    reply = format_reply(saved, {"plan": saved.get("plan") or plan, "changes": edited.get("changes")}, instruction)
    project.messages.append({
        "role": "assistant",
        "content": reply,
        "trace": [{"name": edited.get("backend") or "edit", "ok": True}],
    })
    project.save_messages()
    on_event("version", {"activeVersion": saved["version"]})
    on_event("done", {
        "reply": reply,
        "activeVersion": saved["version"],
        "videoUrl": saved["videoUrl"],
        "probe": saved.get("probe"),
        "qa": saved.get("qa"),
        "changes": edited.get("changes") or [],
        "skipped": [],
        "backend": edited.get("backend"),
        "hook": ((plan.get("studio") or {}).get("packaging") or {}).get("chosenHook") or "",
        "packaging": (plan.get("studio") or {}).get("packaging"),
    })
    return saved


def _render(project, plan: dict, view: dict, analysis: dict, on_event, edited: dict | None = None, instruction: str = "", first: bool = False) -> dict:
    on_event("status", {"step": "render"})
    saved = _produce(project, plan, view, analysis, on_event, edited, instruction)
    story = (edited or {}).get("story") or {}
    if not first or not story:
        return saved
    on_event("status", {"step": "critique"})
    on_event("version", {"activeVersion": saved["version"]})
    measured = measure_output(project.folder / "versions" / f"v{saved['version']}" / "output.mp4", plan, analysis, story)
    _stamp(project, saved["version"], plan, measured, "v1 är sparad och mätt.", second=False)
    on_event("edits", {"changes": [_measure_line(measured)], "skipped": []})
    if not measured.get("weak"):
        return saved
    revised = copy.deepcopy(plan)
    changed, note = improve_rendered(revised, analysis, story, measured)
    if not changed:
        note = "Mätningen var svag, men ingen ärlig ändring fanns att rendera om. v1 ligger kvar."
        _stamp(project, saved["version"], plan, measured, note, second=False)
        on_event("edits", {"changes": [note], "skipped": []})
        return saved
    revised.setdefault("studio", {})
    revised["studio"]["measure"] = measured
    revised["studio"]["secondPass"] = True
    revised["studio"]["critiqueNote"] = note
    revised["studio"]["parentVersion"] = saved["version"]
    on_event("edits", {"changes": [note], "skipped": []})
    on_event("status", {"step": "render"})
    try:
        return _produce(project, revised, view, analysis, on_event, edited, instruction)
    except Exception as error:
        failed = f"v2 kunde inte renderas ({error}). v1 ligger kvar."
        _stamp(project, saved["version"], plan, measured, failed, second=False)
        on_event("edits", {"changes": [failed], "skipped": []})
        saved["plan"] = plan
        return saved


def _produce(project, plan: dict, view: dict, analysis: dict, on_event, edited: dict | None, instruction: str) -> dict:
    version, folder = project.next_version_folder()
    out = folder / "output.mp4"
    delivery = SHORT_EXPORT if SHORT_DELIVERY in {"4k", "4k60", "short"} else None
    if delivery:
        plan.setdefault("export", {}).update(describe_export(analysis.get("source") or {}, delivery))
    probe_info = render_plan(
        project.original,
        view,
        plan,
        out,
        on_progress=lambda progress: on_event("progress", {"progress": round(progress, 3)}),
        delivery=delivery,
    )
    on_event("status", {"step": "qa"})
    qa = _qa(out, plan, analysis, delivery)
    story = (edited or {}).get("story") or {}
    trends = (edited or {}).get("trends") or {}
    if story:
        finish_metadata(plan, analysis, story, trends, plan.get("studio", {}).get("critique") or {}, probe_info, qa, instruction)
        gate = (plan.get("studio") or {}).get("gate") or {}
        if gate.get("hardFailures"):
            raise RuntimeError("Kvalitetsgrinden stoppade filen: " + ", ".join(gate["hardFailures"]))
    on_event("qa", qa)
    if not qa.get("playable"):
        raise RuntimeError("Filen är inte en spelbar MP4. " + " ".join(
            item["detail"] for item in qa.get("checks") or [] if not item.get("ok")
        ))
    saved = project.save_version(version, plan, probe_info, qa)
    saved["plan"] = plan
    saved["output"] = str(out)
    return saved


def _stamp(project, version: int, plan: dict, measured: dict, note: str, *, second: bool) -> None:
    studio = plan.setdefault("studio", {})
    studio["measure"] = measured
    studio["secondPass"] = second
    studio["critiqueNote"] = note
    path = project.folder / "versions" / f"v{version}" / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    for item in project.editor.versions:
        if item["version"] == version:
            item["plan"] = copy.deepcopy(plan)
    if project.editor.active == version:
        project.editor.plan = copy.deepcopy(plan)
    project.save()


def _measure_line(measured: dict) -> str:
    reasons = "; ".join(measured.get("reasons") or [])
    state = "svag" if measured.get("weak") else "inte svag"
    return (
        f"Kritik ({state}): första 3s rörelse {measured.get('openingMotion')}, "
        f"senare {measured.get('laterMotion')}, topp i output {measured.get('punchOut')}s. {reasons}"
    ).strip()


def _qa(path, plan: dict, analysis: dict, delivery: dict | None) -> dict:
    data = probe(path)
    video = next((stream for stream in data.get("streams") or [] if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in data.get("streams") or [] if stream.get("codec_type") == "audio"), {})
    duration = float((data.get("format") or {}).get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    profile = str(video.get("profile") or "")
    rate = str(video.get("avg_frame_rate") or "0/1")
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("duration", duration > 0.4, f"{duration:.3f}s")
    if delivery:
        spec = describe_export((analysis or {}).get("source") or {}, delivery)
        check("resolution", width == spec["width"] and height == spec["height"], f"{width}x{height}")
        comment = str(((data.get("format") or {}).get("tags") or {}).get("comment") or "")
        check("quality_label", comment == spec["quality"], comment or "saknas")
    else:
        check("resolution", width == 1080 and height == 1920, f"{width}x{height}")
        check("not_advertised_4k", width < 2160, f"{width}x{height}")
    ratio = (width / height) if height else 0
    check("vertical", abs(ratio - 9 / 16) < 0.02, f"{ratio:.4f}")
    check(
        "codec",
        video.get("codec_name") == "h264" and "high" in profile.lower() and video.get("pix_fmt") == "yuv420p" and audio.get("codec_name") == "aac",
        f"{video.get('codec_name')} {profile} {video.get('pix_fmt')}/{audio.get('codec_name')}",
    )
    check("fps", _fps(rate) > 1, rate)
    check("audio", bool(audio), "ljudspår finns" if audio else "ljudspår saknas")
    check(
        "original_audio",
        plan.get("audio", {}).get("keepOriginal") is True and plan.get("audio", {}).get("music") is False,
        "keepOriginal, ingen musik",
    )
    blob = path.read_bytes()[: 4_000_000]
    moov, mdat = blob.find(b"moov"), blob.find(b"mdat")
    check("faststart", moov >= 0 and (mdat < 0 or moov < mdat), f"moov={moov} mdat={mdat}")
    playable = all(item["ok"] for item in checks if item["name"] in {"duration", "codec", "resolution", "audio", "vertical"})
    return {"ok": all(item["ok"] for item in checks), "playable": playable, "checks": checks, "duration": round(duration, 3)}


def _fps(rate: str) -> float:
    if "/" not in rate:
        try:
            return float(rate)
        except ValueError:
            return 0
    num, den = rate.split("/", 1)
    try:
        return float(num) / float(den or 1)
    except ValueError:
        return 0


def _reply(saved: dict, edited: dict) -> str:
    probe_info = saved.get("probe") or {}
    quality = probe_info.get("quality")
    size = f"{probe_info.get('width')}x{probe_info.get('height')}"
    if quality and quality != "native":
        size += f", {quality}"
    elif not quality:
        size += ", 1080p"
    lines = [
        f"v{saved['version']} är klar ({float(probe_info.get('duration') or 0):.1f}s, {size}).",
        " ".join(edited.get("changes") or []),
    ]
    note = edited.get("modelNote")
    if edited.get("backend") == "ollama":
        lines.append("Instruktionen tolkades av den lokala Ollama-modellen. Ingen betald tjänst anropades.")
    elif note and "Regelmotorn" not in " ".join(edited.get("changes") or []):
        lines.append(str(note))
    return " ".join(line for line in lines if line).strip()


def _emit_analysis(on_event, found: dict) -> None:
    analysis = found["analysis"]
    phrases = [row for row in analysis.get("phrases") or [] if row.get("status", "SUPPORTED") == "SUPPORTED"]
    on_event("analysis", {
        "cached": bool(found.get("cached")),
        "duration": (analysis.get("source") or {}).get("duration"),
        "width": (analysis.get("source") or {}).get("width"),
        "height": (analysis.get("source") or {}).get("height"),
        "fps": (analysis.get("source") or {}).get("fps"),
        "phrases": len(phrases),
        "faces": len([track for track in analysis.get("tracks") or [] if track.get("kind") == "face"]),
        "hands": len([track for track in analysis.get("tracks") or [] if track.get("kind") == "hand"]),
        "scenes": len(analysis.get("scenes") or []),
        "rejected": len([row for row in analysis.get("transcript") or [] if row.get("status") != "SUPPORTED"]),
        "unavailable": [item.get("step") for item in analysis.get("unavailable") or []],
    })


def _write_timeline(project, plan: dict, edited: dict) -> None:
    from .store import _write

    _write(project.folder / "timeline.json", {
        "backend": edited.get("backend"),
        "changes": edited.get("changes") or [],
        "audio": plan.get("audio"),
        "clips": plan.get("clips"),
        "captions": plan.get("captions"),
        "effects": plan.get("effects"),
    })


def _read_analysis(project) -> dict | None:
    from .store import _read

    return _read(project.folder / "analysis_v2.json")
