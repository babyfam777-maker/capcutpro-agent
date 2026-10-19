"""First-cut shaping, critique, packaging, analytics memory, and the quality gate.

The chat reply carries the packaging. The viral score stays off the reply
unless the user asks for it.
"""

from __future__ import annotations

import copy
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
    # Keep the chosen punch point inside the active clip selection. A prior cut may
    # already have removed the global source-video peak.
    if first and plan.get("clips"):
        active_punch = creative._punch_window(plan, analysis)
        if active_punch:
            story["punch"] = active_punch
    intent = creative.parse_intent(instruction)
    if intent.get("punch_zoom") and story.get("punch"):
        requested_zoom = float(intent.get("zoom_percent") or story["punch"].get("zoomEnd") or 1.36)
        story["punch"]["zoomEnd"] = requested_zoom
        story["punch"]["zoomStart"] = max(1.08, requested_zoom - 0.12)
        story["punch"]["reason"] = (
            f"Begärd lokal punch-zoom {story['punch']['zoomStart']:.0%}–{requested_zoom:.0%} "
            f"på uppmätt moment vid {float(story['punch'].get('t0') or 0):.2f}s."
        )
    trends = analyze_trending_data(_topic(instruction), None, _region(instruction))
    changes = list(edited.get("changes") or [])
    learnings = _load(project)
    influences: list[dict] = []
    if first and not _KEEP_WHOLE.search(creative.fold(instruction)):
        changes.extend(_shape(plan, analysis, story))
        driven = drive_with_trends(plan, analysis, story, trends)
        influences = driven["choices"]
        changes.extend(driven["lines"])
    else:
        applied = _apply_pending(plan, analysis, learnings, creative.parse_intent(instruction))
        if applied:
            changes.append(applied)
            _save(project, learnings)
    critique = _score_edit(plan, analysis, story, trends, frames=None)
    if first and critique["weak"]:
        extra = _repair(plan, analysis, story)
        if extra:
            changes.append(extra)
            critique = _score_edit(plan, analysis, story, trends, frames=None)
            critique["preRenderRepair"] = extra
    prior = plan.get("studio") or {}
    plan["studio"] = {
        "story": _public_story(story),
        "viralScore": critique["viralScore"],
        "trends": _public_trends(trends),
        "critique": critique,
        "secondPass": False,
        "trendChoices": influences,
        "factualLabels": list(prior.get("factualLabels") or []),
        "titlePreference": prior.get("titlePreference"),
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
    structures = trends.get("title_structures") or {}
    question_share = float(structures.get("question") or 0)
    number_share = float(structures.get("number") or 0)
    titles = []
    if peak and question_share >= 0.08:
        titles.append(f"What happens at {peak_t:.1f}s?")
    if phrases:
        titles.append(_clip(phrases[0]["text"], 70))
    if peak and number_share >= 0.1:
        titles.append(f"{peak_t:.1f}s is the busiest moment")
    elif peak:
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
    if peak and question_share >= 0.08:
        hooks.append(f"What happens at {peak_t:.1f} seconds?")
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
    if peak and question_share >= 0.08 and not phrases:
        best = next((title for title in titles if title.startswith("What happens")), titles[0])
        chosen = next((hook for hook in hooks if hook.startswith("What happens")), hooks[0])
    elif peak and not phrases:
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
    lines = [f"v{saved['version']} är klar ({float(probe.get('duration') or 0):.1f}s, {size})."]
    lines.extend(str(change) for change in (edited.get("changes") or []) if str(change).strip())
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
    if critique.get("preRenderRepair"):
        lines.append("Innan första render justerades planen: " + str(critique.get("preRenderRepair")))
    for choice in studio.get("trendChoices") or []:
        lines.append(f"Trendval {choice.get('choice')}: {choice.get('signal')} → {choice.get('decision')}")
    measure = studio.get("measure") or {}
    if measure:
        reasons = " ".join(str(item) for item in (measure.get("reasons") or []))
        verdict = f"Svag. {reasons}".strip() if measure.get("weak") else "Inte svag mot de uppmätta bildrutorna."
        lines.append(
            "Kritik av renderad fil: "
            f"första 3s rörelse {measure.get('openingMotion')}, senare {measure.get('laterMotion')}, "
            f"rörelsetopp i output vid {measure.get('punchOut')}s. {verdict}"
        )
    if studio.get("secondPass"):
        parent = studio.get("parentVersion")
        lines.append(
            "En ny MP4 renderades efter kritiken"
            + (f" (v{parent} finns kvar och kan spelas)." if parent else ".")
            + " "
            + str(studio.get("critiqueNote") or "")
        )
    elif studio.get("critiqueNote"):
        lines.append(str(studio["critiqueNote"]))
    elif measure:
        lines.append("Andra passet mätte den renderade filen och gjorde ingen ny version, eftersom inget av kontrollerna var svagt.")
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


def drive_with_trends(plan: dict, analysis: dict, story: dict, trends: dict) -> dict:
    """Let compact trend stats change the first cut. Every choice names its signal."""
    structures = trends.get("title_structures") or {}
    number = float(structures.get("number") or 0)
    question = float(structures.get("question") or 0)
    median = trends.get("title_length_median")
    vintage = trends.get("vintage") or "missing"
    source = trends.get("source") or "none"
    views = bool(trends.get("views_available"))
    peak = story.get("peak") or {}
    punch = story.get("punch") or {}
    choices: list[dict] = []
    lines: list[str] = []

    def record(choice: str, signal: str, decision: str) -> None:
        choices.append({"choice": choice, "signal": signal, "decision": decision})
        lines.append(f"Trend → {choice}: {signal} → {decision}")

    studio = plan.setdefault("studio", {})
    if number >= 0.1 and peak:
        record(
            "hook",
            f"title_structures.number={number:.3f} ({vintage}, {source})",
            f"The punch stays on the measured peak at {float(peak['t']):.1f}s. v1 does not invent an earlier hook.",
        )
    elif punch:
        record(
            "hook",
            f"title_structures.number={number:.3f} ({vintage})",
            "Number share is too low to move the hook, so the measured peak stays where the footage put it.",
        )
    else:
        record("hook", f"title_structures.number={number:.3f}; no measured peak", "No hook was invented from other channels' titles.")

    if number >= 0.1 and punch:
        record(
            "structure",
            f"title_structures.number={number:.3f}",
            "The timeline is split into the peak window and the rest: one concrete moment, not a flat clip.",
        )
    else:
        record("structure", f"title_structures.number={number:.3f}", "No extra split. The measured clips stay in order.")

    sped = False
    if (question >= 0.08 or number >= 0.08) and punch:
        t0 = float(punch["t0"])
        for clip in plan.get("clips") or []:
            if float(clip["sourceEnd"]) <= t0 + 0.05 and float(clip.get("speed") or 1) <= 1.05:
                clip["speed"] = 1.12
                sped = True
        if sped:
            record(
                "pacing",
                f"question={question:.3f} number={number:.3f}",
                "Setup clips that end before the peak play at 1.12x. The peak and the ending stay at 1.0.",
            )
        else:
            record(
                "pacing",
                f"question={question:.3f} number={number:.3f}",
                "No setup clip sits fully before the peak, so the speed stays 1.0.",
            )
    else:
        record("pacing", f"question={question:.3f} number={number:.3f}", "Shares are too low to change the pace. Speed stays with the footage.")

    if clips := plan.get("clips") or []:
        last = clips[-1]
        if float(last.get("speed") or 1) > 1.0:
            last["speed"] = 1.0
    for effect in plan.get("effects") or []:
        if effect.get("type") == "sound_effect":
            mapped = creative._source_to_output(plan, float(effect.get("time") or 0))
            if mapped is not None:
                effect["time"] = round(mapped, 3)

    if question >= 0.08 and peak:
        studio["titlePreference"] = "question"
        record(
            "title",
            f"title_structures.question={question:.3f}; title length median {median}",
            f"The chosen title is a question about the measured peak at {float(peak['t']):.1f}s. It is shorter than the median because it only names that timestamp. No channel name is copied.",
        )
    else:
        studio["titlePreference"] = "fact"
        record("title", f"title_structures.question={question:.3f}", "The title stays a factual description of this clip.")

    if number >= 0.1 and peak and punch:
        label = f"{float(peak['t']):.1f}s"
        moment = creative._source_to_output(plan, float(punch.get("t0") or peak["t"]))
        if moment is not None:
            plan.setdefault("effects", [])
            plan["effects"] = [effect for effect in plan["effects"] if not (effect.get("type") == "text" and effect.get("factual"))]
            plan["effects"].append({
                "id": creative._new_id("e"),
                "type": "text",
                "kind": "headline",
                "text": label,
                "start": round(max(0.05, moment), 3),
                "end": round(max(0.05, moment) + 1.2, 3),
                "fontSize": 96,
                "factual": True,
            })
            studio["factualLabels"] = [label]
            record(
                "on-screen text",
                f"title_structures.number={number:.3f}",
                f"One factual label '{label}' sits on the peak. It is the measured timestamp, not dialogue.",
            )
        else:
            studio["factualLabels"] = []
            record("on-screen text", f"title_structures.number={number:.3f}", "The peak is not on the timeline, so no label was drawn.")
    else:
        studio["factualLabels"] = list(studio.get("factualLabels") or [])
        record(
            "on-screen text",
            f"title_structures.number={number:.3f}",
            "No number pattern strong enough. No on-screen text was invented.",
        )

    creative._retime_from_source(plan)
    record(
        "payoff",
        f"views_available={str(views).lower()}; vintage={vintage}",
        "This file has no retention curve, so the source ending is kept and the last clip stays at 1.0x or slower.",
    )
    record(
        "rewatch",
        f"views_available={str(views).lower()}; no retention curve in {vintage}",
        "No forced loop on v1. If the rendered first 3 seconds are weaker than the later peak, the second pass opens on that measured moment.",
    )
    return {"choices": choices, "lines": lines}


def measure_output(path: Path, plan: dict, analysis: dict, story: dict) -> dict:
    """Score the rendered MP4. Weak means a second encode should change pixels."""
    duration = _media_duration(path)
    punch = story.get("punch") or {}
    punch_out = creative._source_to_output(plan, float(punch["t0"])) if punch else None
    samples = _motion_samples(path, duration, punch_out)
    rendered_audio = _has_audio_stream(path)
    source_audio = bool((analysis.get("source") or {}).get("hasAudio"))
    stats = {
        "duration": round(duration, 3),
        "openingMotion": samples["openingMotion"],
        "laterMotion": samples["laterMotion"],
        "crossDiff": samples["crossDiff"],
        "endingLuma": samples["endingLuma"],
        "framesRead": samples["framesRead"],
        "punchOut": None if punch_out is None else round(float(punch_out), 3),
        "renderedAudio": rendered_audio,
        "sourceAudio": source_audio,
    }
    reasons = weakness_reasons(stats, plan, analysis)
    return {**stats, "weak": bool(reasons), "reasons": reasons}


def weakness_reasons(stats: dict, plan: dict, analysis: dict) -> list[str]:
    reasons = []
    opening = float(stats.get("openingMotion") or 0)
    later = float(stats.get("laterMotion") or 0)
    cross = float(stats.get("crossDiff") or 0)
    punch_out = stats.get("punchOut")
    frames = int(stats.get("framesRead") or 0)
    opening_zoom = _opening_zoom(plan)
    if frames < 4:
        reasons.append("rendered frames could not be read, so the opening could not be measured")
    elif punch_out is not None and float(punch_out) > 3.0 and (
        later > max(1.0, opening * 1.25) or (cross > 8 and opening_zoom < 1.08)
    ):
        reasons.append(
            f"first 3s motion {opening:.2f} vs later {later:.2f}; "
            f"frame diff against the peak {cross:.2f}; punch zoom output at {float(punch_out):.2f}s"
        )
    if punch_out is not None and float(punch_out) > 3.0 and opening_zoom < 1.08 and frames >= 4 and cross > 8:
        if not any("punch zoom" in reason for reason in reasons):
            reasons.append(f"opening zoom is flat ({opening_zoom:.2f}) while the punch lands at {float(punch_out):.2f}s")
    phrases = creative.supported_phrases(analysis)
    if phrases and not (plan.get("captions") or []):
        reasons.append("supported speech has no captions")
    tracks = [track for track in analysis.get("tracks") or [] if track.get("kind") in ("face", "person")]
    if tracks and not any(clip.get("focusTrack") for clip in plan.get("clips") or []):
        reasons.append("crop has no focusTrack although a person track exists")
    audio = plan.get("audio") or {}
    if audio.get("music"):
        reasons.append("music is on")
    if stats.get("sourceAudio") and stats.get("renderedAudio") is False:
        reasons.append("audio missing on the rendered file")
    ending = stats.get("endingLuma")
    if ending is not None and float(ending) < 8:
        reasons.append("ending is nearly black")
    speeds = [float(clip.get("speed") or 1) for clip in plan.get("clips") or []]
    if speeds and max(speeds) - min(speeds) < 0.04 and punch_out is not None and float(punch_out) > 3 and later > max(1.0, opening * 1.25):
        reasons.append("pacing is flat while the later picture moves more than the first 3 seconds")
    if speeds and speeds[-1] > 1.05:
        reasons.append("payoff plays faster than real time")
    return reasons


def improve_rendered(plan: dict, analysis: dict, story: dict, measure: dict) -> tuple[bool, str]:
    """Change the plan when the rendered file was weak. Returns whether a re-render differs."""
    if not measure.get("weak"):
        return False, ""
    before = creative._slim(plan, ("clips", "captions", "effects", "audio"))
    notes = []
    text = " ".join(measure.get("reasons") or [])
    if any(token in text for token in ("first 3s", "punch zoom", "opening zoom", "pacing is flat", "frames could not")):
        ok, message = _promote_peak(plan, analysis, story)
        if ok:
            notes.append(message)
    if "focusTrack" in text:
        track_id, kind = creative.main_subject(analysis)
        if track_id:
            for clip in plan.get("clips") or []:
                clip["focusTrack"] = track_id
            notes.append(f"Beskärningen följer {track_id} ({kind}).")
    if "captions" in text:
        added = _add_real_captions(plan, analysis)
        if added:
            notes.append(added)
    if "music is on" in text:
        plan.setdefault("audio", {})["music"] = False
        plan["audio"]["keepOriginal"] = True
        notes.append("Musik togs bort. Originalljudet är kvar.")
    if "audio missing" in text:
        plan.setdefault("audio", {})["keepOriginal"] = True
        plan["audio"]["music"] = False
        notes.append("Nästa render tvingar på originalljudet.")
    if "nearly black" in text or "payoff plays faster" in text:
        clips = plan.get("clips") or []
        if clips and float(clips[-1].get("speed") or 1) > 1.0:
            clips[-1]["speed"] = 1.0
            notes.append("Sista klippet spelas i 1.0x.")
        elif "nearly black" in text:
            ok, message = creative._satisfying_end(plan)
            if ok:
                notes.append(message)
    if notes:
        _reseat_factual(plan, story)
    after = creative._slim(plan, ("clips", "captions", "effects", "audio"))
    if before == after:
        return False, ""
    return True, " ".join(notes)


def _promote_peak(plan: dict, analysis: dict, story: dict) -> tuple[bool, str]:
    punch = story.get("punch") or {}
    if not punch or not plan.get("clips"):
        return False, ""
    t0 = float(punch["t0"])
    t1 = float(punch["t1"])
    if t1 - t0 < 0.45:
        return False, ""
    first = plan["clips"][0]
    if abs(float(first.get("sourceStart") or 0) - t0) < 0.25:
        return False, ""
    _clock_to_source(plan)
    track_id, kind = creative.main_subject(analysis)
    opening = copy.deepcopy(first)
    opening["id"] = creative._new_id("c")
    opening["sourceStart"] = round(t0, 3)
    opening["sourceEnd"] = round(t1, 3)
    opening["speed"] = 1.0
    opening["zoomStart"] = round(float(punch.get("zoomStart") or 1.10), 3)
    opening["zoomEnd"] = round(float(punch.get("zoomEnd") or 1.18), 3)
    opening["label"] = "peak"
    opening["reason"] = "Second pass moved the measured motion peak to the opening because the rendered first 3 seconds were weaker."
    if track_id:
        opening["focusTrack"] = track_id
    rest = []
    for clip in plan["clips"]:
        rest.extend(creative._subtract_clip(clip, t0, t1))
    plan["clips"] = [opening, *rest]
    _clock_from_source(plan)
    if kind == "face":
        who = f"ansiktsspåret {track_id}"
    elif kind == "person":
        who = f"kroppsspåret {track_id}"
    else:
        who = "mitten, eftersom inget personspår finns"
    return True, (
        f"Andra passet flyttade det uppmätta rörelsefönstret {t0:.2f}–{t1:.2f}s till öppningen "
        f"med zoom {opening['zoomStart']:.2f}–{opening['zoomEnd']:.2f}. Det är inte en bekräftad reaktion. Fokus: {who}."
    )


def _reseat_factual(plan: dict, story: dict) -> None:
    labels = list((plan.get("studio") or {}).get("factualLabels") or [])
    if not labels:
        return
    text = str(labels[0])
    punch = story.get("punch") or {}
    peak = story.get("peak") or {}
    moment = None
    if punch:
        moment = creative._source_to_output(plan, float(punch.get("t0") or 0))
    if moment is None and peak:
        moment = creative._source_to_output(plan, float(peak.get("t") or 0))
    if moment is None:
        moment = 0.12
    plan["effects"] = [effect for effect in plan.get("effects") or [] if not (effect.get("type") == "text" and effect.get("factual"))]
    plan.setdefault("effects", []).append({
        "id": creative._new_id("e"),
        "type": "text",
        "kind": "headline",
        "text": text,
        "start": round(max(0.05, float(moment)), 3),
        "end": round(max(0.05, float(moment)) + 1.2, 3),
        "fontSize": 96,
        "factual": True,
    })
    plan.setdefault("studio", {})["factualLabels"] = [text]


def _add_real_captions(plan: dict, analysis: dict) -> str:
    phrases = creative.supported_phrases(analysis)
    if not phrases or plan.get("captions"):
        return ""
    for phrase in phrases:
        start = creative._source_to_output(plan, float(phrase["t0"]))
        end = creative._source_to_output(plan, float(phrase["t1"]))
        if start is None or end is None or end - start < 0.2:
            continue
        plan.setdefault("captions", []).append({
            "id": creative._new_id("cap"),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": phrase["text"],
            "accent": "",
            "fontSize": 64,
        })
    if not plan.get("captions"):
        return ""
    return "Undertexter lades in från de godkända replikerna."


def _clock_to_source(plan: dict) -> None:
    for caption in plan.get("captions") or []:
        src0 = creative._output_to_source(plan, float(caption["start"]))
        src1 = creative._output_to_source(plan, float(caption["end"]))
        if src0 is None or src1 is None:
            caption["_drop"] = True
            continue
        caption["start"] = src0
        caption["end"] = src1
    plan["captions"] = [caption for caption in plan.get("captions") or [] if not caption.get("_drop")]
    for effect in plan.get("effects") or []:
        if effect.get("type") == "sound_effect":
            src = creative._output_to_source(plan, float(effect.get("time") or 0))
            if src is None:
                effect["_drop"] = True
            else:
                effect["time"] = src
                effect["_src"] = True
            continue
        if "start" not in effect:
            continue
        src0 = creative._output_to_source(plan, float(effect["start"]))
        src1 = creative._output_to_source(plan, float(effect["end"]))
        if src0 is None or src1 is None:
            effect["_drop"] = True
            continue
        effect["start"] = src0
        effect["end"] = src1
        effect["_src"] = True


def _clock_from_source(plan: dict) -> None:
    creative._retime_from_source(plan)
    kept = []
    for effect in plan.get("effects") or []:
        if effect.get("_drop"):
            continue
        if effect.get("type") == "sound_effect" and effect.get("_src"):
            out = creative._source_to_output(plan, float(effect.get("time") or 0))
            effect.pop("_src", None)
            if out is None:
                continue
            effect["time"] = round(max(0.0, out), 3)
            kept.append(effect)
            continue
        if effect.get("_src") and "start" in effect:
            start = creative._source_to_output(plan, float(effect["start"]))
            end = creative._source_to_output(plan, float(effect["end"]))
            effect.pop("_src", None)
            if start is None or end is None or end - start < 0.12:
                continue
            effect["start"] = round(start, 3)
            effect["end"] = round(end, 3)
        kept.append(effect)
    plan["effects"] = kept


def _opening_zoom(plan: dict) -> float:
    zoom = 1.0
    cursor = 0.0
    for clip in plan.get("clips") or []:
        speed = float(clip.get("speed") or 1)
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        out_dur = float(clip.get("holdSec") or 0.3) if speed <= 0.001 else max(0.0, (end - start) / max(speed, 0.001))
        if cursor < 3.0 and cursor + out_dur > 0.05:
            zoom = max(zoom, float(clip.get("zoomEnd") or 1))
        cursor += out_dur
        if cursor >= 3.0:
            break
    return zoom


def _motion_samples(path: Path, duration: float, punch_out: float | None) -> dict:
    empty = {"openingMotion": 0.0, "laterMotion": 0.0, "crossDiff": 0.0, "endingLuma": None, "framesRead": 0}
    if duration <= 0.4 or not path.exists():
        return empty
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return empty
    times = [moment for moment in (0.2, 0.8, 1.5, 2.2, 2.8, 4.0, 6.0, 8.0, 9.5, 10.5) if moment < duration - 0.08]
    if punch_out is not None and 0.3 < float(punch_out) < duration - 0.08:
        times.append(round(float(punch_out), 2))
    if duration > 0.6:
        times.append(round(max(0.2, duration - 0.35), 2))
    times = sorted(set(times))
    frames: list[tuple[float, object]] = []
    with tempfile.TemporaryDirectory(prefix="capcut-critique-") as tmp:
        folder = Path(tmp)
        for index, moment in enumerate(times):
            dest = folder / f"f{index}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=160:284", str(dest)],
                capture_output=True,
            )
            if not dest.exists():
                continue
            frames.append((moment, np.asarray(Image.open(dest).convert("RGB"), dtype=np.float32)))
    if len(frames) < 2:
        return empty
    diffs = []
    for (left_t, left), (right_t, right) in zip(frames, frames[1:]):
        diffs.append((left_t, right_t, float(np.abs(left - right).mean())))
    opening = [value for _left, right, value in diffs if right < 3.05]
    later = [value for left, _right, value in diffs if left >= 3.0]
    opening_motion = float(sum(opening) / len(opening)) if opening else 0.0
    later_motion = float(sum(later) / len(later)) if later else 0.0
    cross = 0.0
    early = next((image for moment, image in frames if moment <= 1.0), frames[0][1])
    if punch_out is not None:
        target = min(frames, key=lambda item: abs(item[0] - float(punch_out)))
        cross = float(np.abs(early - target[1]).mean())
    ending_luma = float(frames[-1][1].mean())
    return {
        "openingMotion": round(opening_motion, 3),
        "laterMotion": round(later_motion, 3),
        "crossDiff": round(cross, 3),
        "endingLuma": round(ending_luma, 2),
        "framesRead": len(frames),
    }


def _media_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(proc.stdout.strip() or 0)
    except ValueError:
        return 0.0


def _has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return bool(proc.stdout.strip())


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
    allowed = creative._factual_tokens(plan)
    for text in creative._texts(plan):
        token = creative.fold(text)
        if not token:
            continue
        if token in allowed:
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
        "title_structures": trends.get("title_structures") or {},
        "title_length_median": trends.get("title_length_median"),
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
