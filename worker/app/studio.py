"""First-cut shaping, critique, packaging, analytics memory, and the quality gate.

The chat reply carries the packaging. The viral score stays off the reply
unless the user asks for it.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import creative
from .story import build_story
from .trending import CREDENTIAL, analyze_trending_data

_KEEP_WHOLE = re.compile(r"(klipp inte|don't cut|do not cut|hela videon|keep the full|entire video|utan att klippa)")


def apply_studio(analysis: dict, edited: dict, instruction: str, project, *, first: bool) -> dict:
    if not edited.get("ok") or edited.get("undo"):
        return edited
    plan = edited["plan"]
    story = build_story(analysis)
    trends = analyze_trending_data(_topic(instruction), None, _region(instruction))
    changes = list(edited.get("changes") or [])
    learnings = _load(project)
    if first and not _KEEP_WHOLE.search(creative.fold(instruction)):
        changes.extend(_shape(plan, analysis, story))
    else:
        applied = _apply_pending(plan, analysis, learnings, creative.parse_intent(instruction))
        if applied:
            changes.append(applied)
            _save(project, learnings)
    critique = _score_edit(plan, analysis, story, trends, frames=None)
    second = False
    if first and critique["weak"]:
        extra = _repair(plan, analysis, story)
        if extra:
            changes.append(extra)
            second = True
            critique = _score_edit(plan, analysis, story, trends, frames=None)
            critique["preRenderRepair"] = extra
    plan["studio"] = {
        "story": _public_story(story),
        "viralScore": critique["viralScore"],
        "trends": _public_trends(trends),
        "critique": critique,
        "secondPass": second,
        "learningsApplied": [row.get("id") for row in learnings if row.get("applied")],
    }
    edited = dict(edited)
    edited["plan"] = plan
    edited["changes"] = changes
    edited["story"] = story
    edited["trends"] = trends
    return edited


def review_frames(path: Path, plan: dict, analysis: dict, story: dict, trends: dict, *, first: bool) -> tuple[dict, dict, bool]:
    """Look at the rendered frames. Re-edit only when the opening is actually weak."""
    frames = _sample(path)
    critique = _score_edit(plan, analysis, story, trends, frames)
    critique["frames"] = frames
    changed = False
    if first and not plan.get("studio", {}).get("secondPass") and critique["weak"] and frames.get("openingDark"):
        extra = _repair(plan, analysis, story)
        if extra:
            critique["frameRepair"] = extra
            changed = True
    return plan, critique, changed


def finish_metadata(plan: dict, analysis: dict, story: dict, trends: dict, critique: dict, probe: dict, qa: dict, instruction: str) -> dict:
    packaging = package(analysis, story, trends, plan)
    gate = quality_gate(plan, analysis, story, packaging, probe, qa)
    studio = plan.setdefault("studio", {})
    studio["packaging"] = packaging
    studio["critique"] = critique
    studio["gate"] = gate
    studio["viralScore"] = critique.get("viralScore") or studio.get("viralScore")
    if not _wants_score(instruction):
        studio["scoreHidden"] = True
    return packaging


def package(analysis: dict, story: dict, trends: dict, plan: dict) -> dict:
    phrases = creative.supported_phrases(analysis)
    duration = float((analysis.get("source") or {}).get("duration") or story.get("duration") or 0)
    peak = story.get("peak") or {}
    peak_t = float(peak.get("t") or 0)
    tracking = story.get("tracking") or {}
    titles = []
    if phrases:
        titles.append(_clip(phrases[0]["text"], 70))
    if peak:
        titles.append(f"The busiest moment is at {peak_t:.1f}s")
    titles.append(f"{duration:.0f}s vertical cut, original audio kept")
    if tracking.get("face") == "ok":
        titles.append("Crop follows the tracked face")
    elif tracking.get("person") == "ok":
        titles.append("No face found, crop follows a body box")
    else:
        titles.append("No person track, center crop")
    if not phrases:
        titles.append("No supported dialogue, so no caption")
    else:
        titles.append(f"{len(phrases)} spoken line{'s' if len(phrases) != 1 else ''} kept as captions")
    titles = _unique(titles)[:5]
    while len(titles) < 5:
        titles.append(f"9:16 reframing of the {duration:.0f}s upload")
    hooks = []
    if phrases:
        hooks.append(_clip(phrases[0]["text"], 80))
    if peak:
        hooks.append(f"The busiest moment is at {peak_t:.1f} seconds.")
    hooks.append("Original audio stays. No music was added.")
    hooks.append(tracking.get("note") or "Tracking status is in the analysis.")
    if not phrases:
        hooks.append("There is no supported line of dialogue, so none was written on screen.")
    else:
        hooks.append("On-screen words are the supported dialogue only.")
    hooks.append(story.get("hookWhy") or "Hook type follows the measured footage.")
    hooks = _unique(hooks)[:5]
    while len(hooks) < 5:
        hooks.append("The cut does not invent a reaction or a quote.")
    best = titles[0]
    chosen = hooks[0]
    if peak and not phrases:
        best = next((title for title in titles if "busiest" in title), titles[0])
        chosen = next((hook for hook in hooks if "busiest" in hook), hooks[0])
    description = (
        f"Vertical 9:16 cut of the uploaded video ({duration:.1f}s). "
        f"Hook type: {story.get('hookType')}. Emotion: {story.get('emotion')} ({story.get('emotionWhy')}) "
        f"Original audio is kept and no music bed was added. "
        f"{tracking.get('note') or ''} "
        f"Trend file: {trends.get('vintage')} / {trends.get('source')}. {trends.get('note')}"
    )
    tags = ["#shorts", "#verticalvideo", "#originalaudio", "#1080p"]
    if not phrases:
        tags.append("#nocaption")
    if story.get("musicDominant"):
        tags.append("#musicinoriginal")
    if tracking.get("person") == "ok" and tracking.get("face") != "ok":
        tags.append("#bodytrack")
    tags = _unique(tags)[:8]
    while len(tags) < 5:
        tags.append("#clip")
    return {
        "titles": titles[:5],
        "bestTitle": best,
        "hooks": hooks[:5],
        "chosenHook": chosen,
        "description": " ".join(description.split()),
        "hashtags": tags[:8],
        "onScreenHook": phrases[0]["text"] if phrases else "",
        "trendVintage": trends.get("vintage"),
        "copiedTitles": False,
    }


def quality_gate(plan: dict, analysis: dict, story: dict, packaging: dict, probe: dict | None, qa: dict | None) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str, hard: bool = False) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "hard": hard})

    source = analysis.get("source") or {}
    add("uploaded", bool(source.get("duration")), f"{source.get('duration')}s source")
    add("analyzed", bool(analysis.get("tracks") is not None or analysis.get("evidence") is not None), "analysis v2")
    add("audio", "hasAudio" in source, "audio probed" if source.get("hasAudio") else "no audio track")
    add("story", bool(story.get("segments")), f"{len(story.get('segments') or [])} segments")
    add("strongest", bool(story.get("peak")) or story.get("hookWhy", "").startswith("Ingen"), story.get("hookWhy") or "peak missing")
    add("hook", bool(story.get("hookType")), str(story.get("hookType")))
    add("cuts", bool(plan.get("clips")), f"{len(plan.get('clips') or [])} clips")
    width = int((probe or {}).get("width") or 0)
    height = int((probe or {}).get("height") or 0)
    add("composition", (width, height) in {(1080, 1920), (2160, 3840)}, f"{width}x{height}", hard=True)
    invented = _invented_caption(plan, analysis)
    add("captions", not invented, "captions match supported speech" if not invented else "invented caption", hard=True)
    audio = plan.get("audio") or {}
    add("audio_kept", audio.get("keepOriginal") is True and audio.get("music") is False, "original on, music off", hard=True)
    add("sfx", not _sfx_over_speech(plan, analysis), "sfx clear of speech" if not _sfx_over_speech(plan, analysis) else "sfx overlaps speech")
    zooms = [float(clip.get("zoomEnd") or 1) for clip in plan.get("clips") or []]
    in_range = all(1.0 <= zoom <= 1.7 for zoom in zooms) if zooms else False
    add("zooms", in_range, ", ".join(f"{zoom:.2f}" for zoom in zooms) or "none")
    add("payoff", any(row.get("role") == "payoff" or row.get("role") == "ending" for row in story.get("segments") or []), "ending kept in the story")
    add("ending", bool(plan.get("clips")), "timeline has an end")
    add("originality", packaging.get("copiedTitles") is False, "titles are facts from this clip, not another channel")
    duration = float((probe or {}).get("duration") or (qa or {}).get("duration") or 0)
    add("mp4", duration > 0.4 and (qa or {}).get("playable", True), f"{duration:.2f}s playable", hard=True)
    hard_fail = [row["name"] for row in checks if row["hard"] and not row["ok"]]
    return {"ok": not hard_fail, "checks": checks, "hardFailures": hard_fail}


def format_reply(saved: dict, edited: dict, instruction: str) -> str:
    probe = saved.get("probe") or {}
    quality = probe.get("quality")
    size = f"{probe.get('width')}x{probe.get('height')}"
    if quality and quality != "native":
        size += f", {quality}"
    elif not quality:
        size += ", 1080p"
    plan = edited.get("plan") or {}
    studio = plan.get("studio") or {}
    packaging = studio.get("packaging") or {}
    critique = studio.get("critique") or {}
    gate = studio.get("gate") or {}
    lines = [
        f"v{saved['version']} är klar ({float(probe.get('duration') or 0):.1f}s, {size}).",
        " ".join(edited.get("changes") or []),
    ]
    if packaging:
        lines.append(f"Titel: {packaging.get('bestTitle')}")
        others = [title for title in packaging.get("titles") or [] if title != packaging.get("bestTitle")]
        if others:
            lines.append("Andra titlar: " + " | ".join(others))
        lines.append(f"Hook: {packaging.get('chosenHook')}")
        other_hooks = [hook for hook in packaging.get("hooks") or [] if hook != packaging.get("chosenHook")]
        if other_hooks:
            lines.append("Andra hooktexter: " + " | ".join(other_hooks))
        lines.append(f"Beskrivning: {packaging.get('description')}")
        lines.append("Hashtags: " + " ".join(packaging.get("hashtags") or []))
    if critique.get("frameRepair") or critique.get("preRenderRepair") or studio.get("secondPass"):
        lines.append("En andra genomgång ändrade klippet innan det rapporterades klart.")
    elif critique.get("frames"):
        lines.append("Renderade bildrutor lästes igen. Ingen andra ändring behövdes.")
    if gate:
        failed = [row["name"] for row in gate.get("checks") or [] if not row.get("ok")]
        lines.append("Kvalitetsgrind: " + ("alla hårda kontroller klara." if gate.get("ok") else "stopp: " + ", ".join(gate.get("hardFailures") or failed)))
    if _wants_score(instruction):
        score = (studio.get("viralScore") or {}).get("overall")
        lines.append(f"Intern viralpoäng (beslutsstöd, inte en tittargaranti): {score}.")
    trends = studio.get("trends") or {}
    if trends.get("credentialNeeded") and trends.get("vintage") != "current":
        lines.append("Trenddata som används är historisk, inte dagens lista. " + str(trends.get("credentialNeeded")))
    return "\n".join(line for line in lines if line).strip()


def parse_feedback(text: str) -> dict | None:
    folded = creative.fold(text)
    metrics = {}
    for key, pattern in (
        ("views", r"\bviews?\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("engaged_views", r"engaged views?[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("avd", r"\bavd\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("apv", r"\bapv\b[^0-9]{0,8}([0-9][0-9,\.]*)"),
        ("likes", r"\blikes?\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("comments", r"\bcomments?\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("subs", r"\bsubs(?:cribers)?\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("swiped", r"\bswiped?\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
        ("viewed", r"\bviewed\b[^0-9]{0,12}([0-9][0-9,\.]*)"),
    ):
        match = re.search(pattern, folded)
        if match:
            metrics[key] = match.group(1).rstrip(".")
    early = bool(re.search(r"(early drop|tidig tapp|borjan tapp|retention.{0,12}borjan|drop.{0,16}start)", folded))
    mid = bool(re.search(r"(mid drop|mitten tapp|retention.{0,12}mid)", folded))
    late = bool(re.search(r"(late drop|sen tapp|slutet tapp|retention.{0,12}end)", folded))
    spike = bool(re.search(r"(spike|topp i retention|retention spike)", folded))
    high_swipe = bool(re.search(r"(high swipe|manga swipes|swiped away|hog swipe)", folded)) or (
        "swiped" in metrics and _percent(metrics.get("swiped")) >= 50
    )
    high_ret_low_reach = bool(re.search(r"(high retention|hog retention|bra retention).{0,40}(low reach|lag rackvidd|fa visningar)", folded))
    if not metrics and not any((early, mid, late, spike, high_swipe, high_ret_low_reach)):
        return None
    actions = []
    if early or high_swipe:
        actions.append("hook")
    if mid:
        actions.append("pacing")
    if late:
        actions.append("payoff")
    if spike:
        actions.append("investigate")
    if high_ret_low_reach:
        actions.append("packaging")
    return {"metrics": metrics, "actions": actions, "raw": " ".join(text.split())[:500]}


def store_feedback(project, feedback: dict) -> None:
    rows = _load(project)
    rows.append({
        "id": f"fb{len(rows) + 1}",
        "metrics": feedback.get("metrics") or {},
        "actions": feedback.get("actions") or [],
        "raw": feedback.get("raw") or "",
        "applied": False,
    })
    _save(project, rows[-20:])


def apply_feedback_now(plan: dict, analysis: dict, feedback: dict) -> dict:
    story = build_story(analysis)
    notes = []
    changed = False
    for action in feedback.get("actions") or []:
        if action == "hook":
            note = _repair(plan, analysis, story) or _bump_zoom(plan, analysis, story)
            if note:
                notes.append("Tidig tapp eller hög swipe: " + note)
                changed = True
        elif action == "pacing":
            ok, message = creative._speed_ramp(plan)
            if ok:
                notes.append("Mitten tappar: " + message)
                changed = True
        elif action == "payoff":
            ok, message = creative._satisfying_end(plan)
            if ok:
                notes.append("Sen tapp: " + message)
                changed = True
        elif action == "investigate":
            notes.append("En retention-topp sparades. Ingen effekt lades på en gissad orsak.")
        elif action == "packaging":
            notes.append("Hög retention och låg räckvidd sparades som ett paketproblem, inte som ett skäl att byta klipp.")
    if not notes:
        notes.append("Analysen sparades. Inget i tidslinjen kunde ändras utan att hitta på ett skäl.")
    return {"changed": changed, "plan": plan, "message": " ".join(notes)}


def _shape(plan: dict, analysis: dict, story: dict) -> list[str]:
    notes = []
    dead = float(story.get("deadOpeningS") or 0)
    if dead >= 0.8:
        ok, message = creative._trim_opening(plan, dead, float(story.get("duration") or 0))
        if ok:
            notes.append(message + " Öppningen var mätbart svagare än resten.")
    punch = story.get("punch")
    if punch:
        ok, message = creative._zoom_range(
            plan,
            float(punch["t0"]),
            float(punch["t1"]),
            analysis,
            zoom_start=float(punch["zoomStart"]),
            zoom_end=float(punch["zoomEnd"]),
            reason=str(punch["reason"]),
            label="punch",
        )
        if ok:
            notes.append(message)
        if punch.get("confirmed") and not _speech_at(analysis, float(punch["t0"]), float(punch["t1"])):
            moment = creative._source_to_output(plan, float(punch["t0"]))
            if moment is not None:
                plan.setdefault("effects", []).append({
                    "id": creative._new_id("e"),
                    "type": "sound_effect",
                    "sfx": "whoosh",
                    "time": round(max(0.0, moment), 3),
                    "gainDb": -16,
                })
                notes.append("Ett kort whoosh ligger på den uppmätta reaktionen, inte över en replik.")
    if story.get("emotion") == "unclassified":
        notes.append(story.get("emotionWhy") or "")
    notes.append(story.get("tracking", {}).get("note") or "")
    notes.append(f"Hooktyp: {story.get('hookType')}. {story.get('hookWhy')}")
    return [note for note in notes if note]


def _repair(plan: dict, analysis: dict, story: dict) -> str:
    dead = float(story.get("deadOpeningS") or 0)
    start = float((plan.get("clips") or [{}])[0].get("sourceStart") or 0)
    if dead >= 0.8 and start < dead - 0.2:
        ok, message = creative._trim_opening(plan, max(0.8, dead - start), float(story.get("duration") or 0))
        if ok:
            return message
    return _bump_zoom(plan, analysis, story)


def _bump_zoom(plan: dict, analysis: dict, story: dict) -> str:
    punch = story.get("punch")
    if not punch:
        return ""
    zooms = [float(clip.get("zoomEnd") or 1) for clip in plan.get("clips") or []]
    if zooms and max(zooms) >= float(punch["zoomEnd"]) - 0.01:
        return ""
    ok, message = creative._zoom_range(
        plan,
        float(punch["t0"]),
        float(punch["t1"]),
        analysis,
        zoom_start=float(punch["zoomStart"]),
        zoom_end=float(punch["zoomEnd"]),
        reason=str(punch["reason"]),
        label="punch",
    )
    return message if ok else ""


def _score_edit(plan: dict, analysis: dict, story: dict, trends: dict, frames: dict | None) -> dict:
    base = dict(story.get("viralScore") or {})
    zooms = [float(clip.get("zoomEnd") or 1) for clip in plan.get("clips") or []]
    varied = len(set(round(zoom, 2) for zoom in zooms)) > 1
    in_band = all(1.0 <= zoom <= 1.4 or zoom <= 1.7 for zoom in zooms)
    base["visual"] = 74 if varied and in_band else (48 if zooms and max(zooms) > 1.02 else 36)
    base["trendFit"] = round(float(trends.get("trend_score") or 30))
    base["hook"] = 72 if varied or float((plan.get("clips") or [{}])[0].get("sourceStart") or 0) >= 0.8 else base.get("hook", 40)
    if frames and frames.get("openingDark"):
        base["hook"] = min(int(base.get("hook") or 40), 36)
    if frames and frames.get("bottomHeavy") and _has_text(plan):
        base["clarity"] = 30
    numbers = [int(value) for key, value in base.items() if key != "overall"]
    base["overall"] = round(sum(numbers) / max(1, len(numbers)))
    start = float((plan.get("clips") or [{}])[0].get("sourceStart") or 0)
    opening_still_dead = float(story.get("deadOpeningS") or 0) >= 0.8 and start < 0.4
    zoom_missing = bool(story.get("punch")) and (not zooms or max(zooms) < 1.08)
    text_in_ui = bool(frames and frames.get("bottomHeavy") and _has_text(plan))
    dark = bool(frames and frames.get("openingDark"))
    return {
        "viralScore": base,
        "weak": bool(opening_still_dead or zoom_missing or text_in_ui or dark),
        "frames": frames,
    }


def _sample(path: Path) -> dict:
    try:
        import numpy as np
        from PIL import Image
    except Exception as error:
        return {"ok": False, "error": str(error)}
    notes = {"ok": True, "openingDark": False, "bottomHeavy": False, "frames": 0}
    with tempfile.TemporaryDirectory(prefix="capcut-frames-") as tmp:
        folder = Path(tmp)
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        try:
            duration = float(proc.stdout.strip() or 0)
        except ValueError:
            duration = 0
        times = [0.35, max(0.4, duration / 2), max(0.5, duration - 0.35)]
        lumas = []
        for index, moment in enumerate(times):
            dest = folder / f"f{index}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1", str(dest)],
                capture_output=True,
            )
            if not dest.exists():
                continue
            image = np.asarray(Image.open(dest).convert("RGB"))
            lumas.append(float(image.mean()))
            notes["frames"] += 1
            if index == 0:
                notes["openingDark"] = float(image.mean()) < 12
                height = image.shape[0]
                cut = int(height * 0.70)
                top = _edges(image[:cut])
                bottom = _edges(image[cut:])
                notes["bottomHeavy"] = bottom > top * 1.8 and bottom > 12
        notes["luma"] = [round(value, 2) for value in lumas]
    return notes


def _edges(image) -> float:
    import numpy as np
    gray = image.mean(axis=2)
    if gray.shape[0] < 3:
        return 0.0
    return float(np.abs(np.diff(gray, axis=0)).mean())


def _invented_caption(plan: dict, analysis: dict) -> bool:
    phrases = [creative.fold(row["text"]) for row in creative.supported_phrases(analysis)]
    for text in creative._texts(plan):
        token = creative.fold(text)
        if not token:
            continue
        if any(token in phrase or phrase in token for phrase in phrases):
            continue
        return True
    return False


def _sfx_over_speech(plan: dict, analysis: dict) -> bool:
    phrases = creative.supported_phrases(analysis)
    for effect in plan.get("effects") or []:
        if effect.get("type") != "sound_effect":
            continue
        moment = float(effect.get("time") or 0)
        source = creative._output_to_source(plan, moment)
        if source is None:
            continue
        for phrase in phrases:
            if float(phrase["t0"]) - 0.05 <= source <= float(phrase["t1"]) + 0.05:
                return True
    return False


def _speech_at(analysis: dict, t0: float, t1: float) -> bool:
    for phrase in creative.supported_phrases(analysis):
        if float(phrase["t1"]) > t0 and float(phrase["t0"]) < t1:
            return True
    return False


def _has_text(plan: dict) -> bool:
    return bool(plan.get("captions") or any(effect.get("type") == "text" for effect in plan.get("effects") or []))


def _apply_pending(plan: dict, analysis: dict, learnings: list[dict], intent: dict) -> str:
    if intent.get("undo") or intent.get("trim_open") or intent.get("zoom_main") or intent.get("zoom_reaction"):
        return ""
    pending = next((row for row in reversed(learnings) if not row.get("applied") and row.get("actions")), None)
    if not pending:
        return ""
    result = apply_feedback_now(plan, analysis, pending)
    pending["applied"] = True
    if result["changed"]:
        return "Sparad YouTube-analys användes: " + result["message"]
    return ""


def _load(project) -> list:
    path = Path(project.folder) / "learnings.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save(project, rows: list) -> None:
    path = Path(project.folder) / "learnings.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))


def _public_story(story: dict) -> dict:
    hidden = dict(story)
    hidden.pop("viralScore", None)
    return hidden


def _public_trends(trends: dict) -> dict:
    return {
        "vintage": trends.get("vintage"),
        "source": trends.get("source"),
        "region": trends.get("region"),
        "region_used": trends.get("region_used"),
        "trend_score": trends.get("trend_score"),
        "topic_interest": trends.get("topic_interest"),
        "confidence": trends.get("confidence"),
        "common_title_patterns": trends.get("common_title_patterns"),
        "recommended_hook_patterns": trends.get("recommended_hook_patterns"),
        "views_available": trends.get("views_available"),
        "as_of": trends.get("as_of"),
        "note": trends.get("note"),
        "credentialNeeded": trends.get("credentialNeeded"),
        "historical": trends.get("historical"),
        "current": trends.get("current"),
    }


def _topic(text: str) -> str:
    folded = creative.fold(text)
    tokens = [token for token in re.findall(r"[a-z0-9]+", folded) if len(token) > 3]
    return " ".join(tokens[:4])


def _region(text: str) -> str:
    folded = creative.fold(text)
    if re.search(r"(sverige|sweden|svenska)", folded):
        return "SE"
    if re.search(r"\buk\b|britain", folded):
        return "GB"
    return "US"


def _wants_score(text: str) -> bool:
    return bool(re.search(r"(viral score|viralpoang|poang|poängen|score)", creative.fold(text or "")))


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _percent(value: str | None) -> float:
    try:
        return float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return 0.0
