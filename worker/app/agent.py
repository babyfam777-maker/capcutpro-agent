"""OpenAI-compatible tool-calling loop. Any base URL, including Ollama."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from openai import OpenAI

from .analysis import summary_for_model
from .config import MODEL, OPENAI_API_KEY, OPENAI_BASE_URL
from .plan_ops import proposed_spans, timeline

EventFn = Callable[[str, dict], None]

SYSTEM = """You are CapCutPro, a short-form video editor. The user usually writes Swedish. On-screen text is English.
You never touch video files. You only call tools, and tools change the current JSON edit plan. The original file is never overwritten.
A later message edits THIS plan. Do not start over unless the user uploaded a new video.

New video:
1. analyze_video.
2. Use moments, silences, scenes, faces and the transcript. select_clip the parts worth keeping. Remove dead air and long pauses. Open on the strongest moment even if it is later in the source. Never cut mechanically every N seconds.
3. set_focus on a face track. set_zoom for a push-in on the hook.
4. add_text a 1-3 word English headline in the first 0.8s with one accent word. add_caption or replace_captions for speech. Add emoji, arrow, circle, badge and sound effects when the picture calls for them.
5. commit_render.

Revisions, change only what was asked:
- bigger text: replace_captions with fontSize or scale (scale also grows headlines).
- a section is too long: trim_clip or delete_timeline_range.
- zoom when a word is said: set_zoom with whenText set to that word.
- undo: call undo and do not render again.

Style locked by the renderer: white text, black stroke, exactly one word in #FF2D2D, no gold, safe area, original audio, no music, about -14 LUFS.
Content rules: never sexualize, never zoom or point at body/legs/chest, never state or ask about a real person's relationship, pregnancy, intoxication, health or crimes. Tools reject those lines. Use a question or a clearly fictional inner thought about what is visible.
If a tool returns an error, fix the arguments and call it again.
After a successful commit_render, stop calling tools.
"""


def _props(props: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


TOOLS = [
    {"type": "function", "function": {"name": "analyze_video", "description": "Probe, scenes, silences, transcript with word times, faces, motion, hook moments.", "parameters": _props({})}},
    {"type": "function", "function": {"name": "get_transcript", "description": "Word-level transcript. Optional source-time window.", "parameters": _props({"start": {"type": "number"}, "end": {"type": "number"}})}},
    {"type": "function", "function": {"name": "get_scenes", "description": "Scene cut times in seconds.", "parameters": _props({})}},
    {"type": "function", "function": {"name": "get_faces", "description": "Face tracks with positions over time.", "parameters": _props({})}},
    {"type": "function", "function": {"name": "select_clip", "description": "Add a source range to the timeline.", "parameters": _props({
        "sourceStart": {"type": "number"}, "sourceEnd": {"type": "number"}, "speed": {"type": "number"},
        "label": {"type": "string"}, "insertAt": {"type": "integer"},
    }, ["sourceStart", "sourceEnd"])}},
    {"type": "function", "function": {"name": "trim_clip", "description": "Change a clip's source in/out.", "parameters": _props({
        "clipId": {"type": "string"}, "sourceStart": {"type": "number"}, "sourceEnd": {"type": "number"},
    }, ["clipId"])}},
    {"type": "function", "function": {"name": "delete_timeline_range", "description": "Remove a range of OUTPUT seconds and shift later captions.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"},
    }, ["start", "end"])}},
    {"type": "function", "function": {"name": "reorder_clips", "description": "New clip id order.", "parameters": _props({"order": {"type": "array", "items": {"type": "string"}}}, ["order"])}},
    {"type": "function", "function": {"name": "change_speed", "description": "Speed 0.5-2, or 0 for a short freeze (holdSec).", "parameters": _props({
        "clipId": {"type": "string"}, "speed": {"type": "number"}, "holdSec": {"type": "number"},
    }, ["clipId", "speed"])}},
    {"type": "function", "function": {"name": "set_zoom", "description": "Push-in on a clip. Use whenText to target a spoken word.", "parameters": _props({
        "clipId": {"type": "string"}, "zoomStart": {"type": "number"}, "zoomEnd": {"type": "number"}, "whenText": {"type": "string"},
    })}},
    {"type": "function", "function": {"name": "set_focus", "description": "Crop focus. track is a face id like face_0. Or focusX/focusY 0-1.", "parameters": _props({
        "clipId": {"type": "string"}, "track": {"type": "string"}, "focusX": {"type": "number"}, "focusY": {"type": "number"}, "whenText": {"type": "string"},
    })}},
    {"type": "function", "function": {"name": "add_caption", "description": "Burned-in caption on the output timeline.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "accent": {"type": "string"}, "fontSize": {"type": "integer"},
    }, ["start", "end", "text"])}},
    {"type": "function", "function": {"name": "replace_captions", "description": "Replace captions and/or grow text. scale 1.3 makes captions and headlines bigger. fontSize sets caption size.", "parameters": _props({
        "fontSize": {"type": "integer"}, "scale": {"type": "number"},
        "captions": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "accent": {"type": "string"},
        }}},
    })}},
    {"type": "function", "function": {"name": "add_text", "description": "Headline, story line or thought bubble.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "accent": {"type": "string"},
        "kind": {"type": "string", "enum": ["headline", "story", "bubble"]}, "fontSize": {"type": "integer"},
    }, ["start", "end", "text"])}},
    {"type": "function", "function": {"name": "add_emoji", "description": "Emoji anchored to a face. Fluent 3D when the asset exists.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "emoji": {"type": "string"}, "track": {"type": "string"}, "anchor": {"type": "string"},
    }, ["start", "end", "emoji"])}},
    {"type": "function", "function": {"name": "add_arrow", "description": "Red arrow pointing at a face or moment.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "track": {"type": "string"}, "anchor": {"type": "string"},
    }, ["start", "end"])}},
    {"type": "function", "function": {"name": "add_circle", "description": "Red circle around a tracked face.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "track": {"type": "string"},
    }, ["start", "end"])}},
    {"type": "function", "function": {"name": "add_badge", "description": "Short red badge label.", "parameters": _props({
        "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"},
    }, ["start", "end", "text"])}},
    {"type": "function", "function": {"name": "add_sound_effect", "description": "whoosh, pop, impact, riser or thump on the output timeline.", "parameters": _props({
        "time": {"type": "number"}, "sfx": {"type": "string"}, "gainDb": {"type": "number"},
    }, ["time", "sfx"])}},
    {"type": "function", "function": {"name": "remove_effect", "description": "Remove a caption or effect by id.", "parameters": _props({"id": {"type": "string"}}, ["id"])}},
    {"type": "function", "function": {"name": "undo", "description": "Go back to the previous rendered version.", "parameters": _props({})}},
    {"type": "function", "function": {"name": "commit_render", "description": "Render the current plan to the next MP4 version.", "parameters": _props({})}},
]


def client() -> OpenAI:
    kwargs: dict[str, Any] = {"base_url": OPENAI_BASE_URL, "api_key": OPENAI_API_KEY, "timeout": float(os.environ.get("LLM_TIMEOUT", "180"))}
    return OpenAI(**kwargs)


def run_turn(project, text: str, on_event: EventFn, llm: OpenAI | None = None, force_render=None) -> str:
    text = text.strip()
    if not text:
        raise RuntimeError("Skriv vad du vill göra med videon.")
    project.messages.append({"role": "user", "content": text})
    project.save_messages()
    on_event("status", {"step": "thinking"})
    llm = llm or client()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM + "\n\n" + _state_brief(project)},
    ]
    for message in project.messages:
        if message.get("content"):
            messages.append({"role": message["role"], "content": message["content"]})

    trace: list[dict] = []
    rendered = None
    undid = False
    final = ""
    nudged = False
    wants_undo = _wants_undo(text)
    for step in range(12):
        choice: Any = {"type": "function", "function": {"name": "undo"}} if wants_undo and not undid else "required"
        try:
            response = llm.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice=choice,
                temperature=0.2,
                max_tokens=280,
            )
        except Exception as error:
            if wants_undo and not undid:
                on_event("tool", {"name": "undo", "args": {}})
                result = dispatch(project, "undo", {}, on_event)
                trace.append({"name": "undo", "ok": bool(result.get("ok")), "result": _shrink(result)})
                on_event("tool_result", {"name": "undo", "ok": result.get("ok", True), "result": _shrink(result)})
                if result.get("ok"):
                    undid = True
                    on_event("version", {"activeVersion": result.get("activeVersion")})
                    final = str(result.get("message") or "Ångrade senaste versionen.")
                break
            raise RuntimeError(f"Modellen svarade inte ({MODEL} via {OPENAI_BASE_URL}): {error}") from error
        message = response.choices[0].message
        calls = _tool_calls(message)
        content = message.content or ""
        with (project.folder / "agent.log").open("a", encoding="utf-8") as log:
            log.write(f"\n--- step {step} ---\n{content[:500]}\ncalls={ [c['name'] for c in calls] }\n")
        if not calls and _degenerate(content) and step < 2:
            messages.append({"role": "user", "content": "That reply was unusable. Call analyze_video or the next editing tool now."})
            continue
        if not calls:
            final = (message.content or "").strip()
            if wants_undo:
                break
            if not nudged and rendered is None and not project.editor.plan["clips"]:
                nudged = True
                messages.append({"role": "user", "content": "Call analyze_video, then select_clip, add_text and commit_render. Do not answer with prose only."})
                continue
            if not nudged and rendered is None and project.editor.plan["clips"]:
                nudged = True
                messages.append({"role": "user", "content": "If the edit is ready, call commit_render now."})
                continue
            break
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call["args"])}}
                for call in calls
            ],
        })
        stop_after = False
        for call in calls:
            on_event("tool", {"name": call["name"], "args": call["args"]})
            if call["name"] == "commit_render" and force_render is not None:
                result = force_render(project)
            else:
                result = dispatch(project, call["name"], call["args"], on_event)
            trace.append({"name": call["name"], "ok": bool(result.get("ok", True)), "result": _shrink(result)})
            on_event("tool_result", {"name": call["name"], "ok": result.get("ok", True), "result": _shrink(result)})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(_shrink(result), ensure_ascii=False)})
            if call["name"] == "commit_render" and result.get("ok"):
                rendered = result
                stop_after = True
            if call["name"] == "undo" and result.get("ok"):
                undid = True
                stop_after = True
                on_event("version", {"activeVersion": result.get("activeVersion")})
                if not final:
                    final = str(result.get("message") or "Ångrade senaste versionen.")
        project.save()
        if stop_after or undid:
            break
        if step == 10 and project.editor.plan["clips"] and rendered is None and not wants_undo:
            rendered = _force_commit(project, on_event, trace, force_render)
            break

    if wants_undo and not undid:
        on_event("tool", {"name": "undo", "args": {}})
        result = dispatch(project, "undo", {}, on_event)
        trace.append({"name": "undo", "ok": bool(result.get("ok")), "result": _shrink(result)})
        on_event("tool_result", {"name": "undo", "ok": result.get("ok", True), "result": _shrink(result)})
        if result.get("ok"):
            undid = True
            final = str(result.get("message") or "Ångrade senaste versionen.")
            on_event("version", {"activeVersion": result.get("activeVersion")})
        project.save()

    if not wants_undo and not project.editor.plan["clips"] and project.editor.analysis:
        _seed_from_analysis(project, on_event, trace)
    if rendered is None and project.editor.plan["clips"] and not wants_undo:
        rendered = _force_commit(project, on_event, trace, force_render)

    if rendered and (not final or len(final) > 400):
        final = _summarize(llm, messages, rendered)
    if not final:
        final = "Jag kunde inte göra en ändring. Säg om vad du vill ändra."
    project.messages.append({"role": "assistant", "content": final, "trace": trace})
    project.save_messages()
    on_event("done", {
        "reply": final,
        "activeVersion": project.editor.active,
        "plan": project.editor.public_plan(),
        "trace": trace,
    })
    return final


def dispatch(project, name: str, args: dict, on_event: EventFn) -> dict:
    editor = project.editor
    args = args or {}
    try:
        if name == "analyze_video":
            return project.analyze(on_event)
        if name == "get_transcript":
            if not editor.analysis:
                return {"ok": False, "error": "Analys saknas."}
            words = editor.analysis["transcript"]["words"]
            start, end = args.get("start"), args.get("end")
            if start is not None:
                words = [w for w in words if w["end"] >= float(start) and w["start"] <= float(end or 1e9)]
            return {"ok": True, "text": editor.analysis["transcript"].get("text", ""), "words": words[:300]}
        if name == "get_scenes":
            if not editor.analysis:
                return {"ok": False, "error": "Analys saknas."}
            return {"ok": True, "scenes": editor.analysis["scenes"], "silences": editor.analysis["silences"]}
        if name == "get_faces":
            if not editor.analysis:
                return {"ok": False, "error": "Analys saknas."}
            return {"ok": True, "faces": summary_for_model(editor.analysis)["faces"]}
        if name == "select_clip":
            return editor.select_clip(args["sourceStart"], args["sourceEnd"], args.get("speed") or 1, args.get("label") or "", args.get("insertAt"))
        if name == "trim_clip":
            return editor.trim_clip(args["clipId"], args.get("sourceStart"), args.get("sourceEnd"))
        if name == "delete_timeline_range":
            return editor.delete_timeline_range(args["start"], args["end"])
        if name == "reorder_clips":
            return editor.reorder_clips(list(args["order"]))
        if name == "change_speed":
            return editor.change_speed(args["clipId"], args["speed"], args.get("holdSec"))
        if name == "set_zoom":
            return editor.set_zoom(args.get("clipId"), args.get("zoomStart"), args.get("zoomEnd"), args.get("whenText"))
        if name == "set_focus":
            return editor.set_focus(args.get("clipId"), args.get("focusX"), args.get("focusY"), args.get("track"), args.get("whenText"))
        if name == "add_caption":
            return editor.add_caption(args["start"], args["end"], args["text"], args.get("accent"), args.get("fontSize"))
        if name == "replace_captions":
            captions = args.get("captions")
            if isinstance(captions, str):
                captions = json.loads(captions)
            return editor.replace_captions(captions, args.get("fontSize"), args.get("scale"))
        if name == "add_text":
            return editor.add_text(args["start"], args["end"], args["text"], args.get("accent"), args.get("kind") or "headline", args.get("fontSize"))
        if name == "add_emoji":
            return editor.add_emoji(args["start"], args["end"], args["emoji"], args.get("track"), args.get("anchor") or "above")
        if name == "add_arrow":
            return editor.add_arrow(args["start"], args["end"], args.get("track"), args.get("anchor") or "below")
        if name == "add_circle":
            return editor.add_circle(args["start"], args["end"], args.get("track"))
        if name == "add_badge":
            return editor.add_badge(args["start"], args["end"], args["text"])
        if name == "add_sound_effect":
            return editor.add_sound_effect(args["time"], str(args["sfx"]), args.get("gainDb"))
        if name == "remove_effect":
            return editor.remove_effect(args.get("id") or args.get("effectId"))
        if name == "undo":
            return editor.undo()
        if name == "commit_render":
            return project.commit(on_event, force=bool(args.get("force")))
        return {"ok": False, "error": f"Okänt verktyg {name}."}
    except KeyError as error:
        return {"ok": False, "error": f"Argument saknas: {error}"}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def _state_brief(project) -> str:
    editor = project.editor
    _, total = timeline(editor.plan)
    brief = {
        "activeVersion": editor.active,
        "outputDuration": round(total, 2),
        "plan": {
            "clips": editor.plan["clips"],
            "captions": editor.plan["captions"],
            "effects": editor.plan["effects"],
            "style": editor.plan["style"],
            "audio": editor.plan["audio"],
            "title": editor.plan.get("title"),
        },
    }
    if editor.analysis:
        words = (editor.analysis.get("transcript") or {}).get("words") or []
        brief["source"] = editor.analysis["source"]
        brief["scenes"] = editor.analysis["scenes"]
        brief["silences"] = editor.analysis["silences"]
        brief["moments"] = editor.analysis["moments"][:6]
        brief["faces"] = [
            {"id": face["id"], "screenTime": face.get("screenTime"), "avg": face.get("avg")}
            for face in (editor.analysis.get("faces") or [])[:4]
        ]
        brief["transcript"] = (editor.analysis.get("transcript") or {}).get("text", "")[:700]
        brief["words"] = [{"t": w["text"], "s": w["start"], "e": w["end"]} for w in words[:80]]
    else:
        brief["note"] = "Not analyzed yet. Call analyze_video before selecting clips."
    return "CURRENT PROJECT\n" + json.dumps(brief, ensure_ascii=False)


def _seed_from_analysis(project, on_event: EventFn, trace: list[dict]) -> None:
    """If the model only inspected the video, still build a content-based plan through the same tools."""
    editor = project.editor
    for start, end in proposed_spans(editor.analysis):
        args = {"sourceStart": start, "sourceEnd": end, "label": "moment"}
        on_event("tool", {"name": "select_clip", "args": args})
        result = dispatch(project, "select_clip", args, on_event)
        trace.append({"name": "select_clip", "ok": bool(result.get("ok")), "result": _shrink(result)})
        on_event("tool_result", {"name": "select_clip", "ok": result.get("ok", True), "result": _shrink(result)})
        if not result.get("ok"):
            break
    if not editor.plan["clips"] or any(effect.get("type") == "text" for effect in editor.plan["effects"]):
        return
    words = ((editor.analysis or {}).get("transcript") or {}).get("words") or []
    headline = " ".join(word["text"] for word in words[:2]).strip() or "LOOK"
    headline = headline.upper()[:24]
    accent = max(headline.split(), key=len) if headline.split() else headline
    args = {"start": 0, "end": 0.9, "text": headline, "accent": accent, "kind": "headline"}
    on_event("tool", {"name": "add_text", "args": args})
    result = dispatch(project, "add_text", args, on_event)
    trace.append({"name": "add_text", "ok": bool(result.get("ok")), "result": _shrink(result)})
    on_event("tool_result", {"name": "add_text", "ok": result.get("ok", True), "result": _shrink(result)})
    project.save()


def _wants_undo(text: str) -> bool:
    folded = text.strip().lower().strip(".!?")
    return folded in {"undo", "ångra", "angra", "undo last", "ångra senaste", "ångra det"}


def _force_commit(project, on_event: EventFn, trace: list[dict], force_render=None) -> dict | None:
    on_event("tool", {"name": "commit_render", "args": {"force": True}})
    if force_render is not None:
        result = force_render(project)
    else:
        result = dispatch(project, "commit_render", {"force": True}, on_event)
    trace.append({"name": "commit_render", "ok": bool(result.get("ok")), "result": _shrink(result)})
    on_event("tool_result", {"name": "commit_render", "ok": result.get("ok", True), "result": _shrink(result)})
    project.save()
    return result if result.get("ok") else None


def _summarize(llm: OpenAI, messages: list[dict], rendered: dict) -> str:
    factual = f"Version {rendered.get('version')} är klar ({float(rendered.get('probe', {}).get('duration') or 0):.1f} sekunder)."
    try:
        response = llm.chat.completions.create(
            model=MODEL,
            temperature=0.2,
            max_tokens=120,
            messages=messages + [{
                "role": "user",
                "content": "The render succeeded. Reply in Swedish in one or two sentences about this version. Do not call tools. Do not invent a download link.",
            }],
        )
        text = (response.choices[0].message.content or "").strip()
        return text or factual
    except Exception:
        return factual


def _degenerate(text: str) -> bool:
    words = text.split()
    if len(words) < 12:
        return False
    return len(set(words)) < len(words) * 0.4


def _tool_calls(message) -> list[dict]:
    calls = []
    for index, tool_call in enumerate(message.tool_calls or []):
        raw = tool_call.function.arguments or "{}"
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw or {}
        calls.append({"id": tool_call.id or f"call_{index}", "name": tool_call.function.name, "args": args})
    if calls:
        return calls
    content = message.content or ""
    for match in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.S):
        parsed = _parse_loose(match.group(1))
        if parsed:
            calls.append(parsed)
    if calls:
        return calls
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        parsed = _parse_loose(stripped)
        if parsed:
            return [parsed]
    return []


def _parse_loose(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    name = data.get("name") or data.get("tool")
    if not name:
        return None
    args = data.get("arguments") or data.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return {"id": f"call_{name}_{abs(hash(raw)) % 10000}", "name": name, "args": args}


def _shrink(result: dict) -> dict:
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= 8000:
        return result
    analysis = result.get("analysis")
    if isinstance(analysis, dict):
        result = dict(result)
        result["analysis"] = {
            "source": analysis.get("source"),
            "moments": analysis.get("moments"),
            "scenes": analysis.get("scenes"),
            "silences": analysis.get("silences"),
            "faces": analysis.get("faces"),
            "transcriptText": (analysis.get("transcriptText") or "")[:1500],
            "note": "Call get_transcript for words.",
        }
        raw = json.dumps(result, ensure_ascii=False)
        if len(raw) <= 8000:
            return result
    return {"ok": result.get("ok", True), "note": "Resultatet kortades.", "preview": raw[:4000]}
