"""Deterministic cut. The model does not choose source ranges on the first render."""

from __future__ import annotations

import copy
from typing import Any

from .plan_ops import _id, _primary_track, captions_from_words, min_readable_seconds, timeline

MIN_CLIP = 0.2
# A fraction of a second past the probed duration is rounding, not a different video.
TIME_SLOP = 0.05


def build_auto_plan(analysis: dict | None) -> dict[str, Any]:
    """Clips from speech and confirmed moments. No zoom and no sound effects."""
    if not analysis or not (analysis.get("source") or {}).get("duration"):
        return _fail("Analys saknas. Planen kan inte byggas, och originalvideon används inte som reserv.")
    duration = float(analysis["source"]["duration"])
    if duration < MIN_CLIP:
        return _fail("Videon är för kort för att klippas, och originalet används inte som reserv.")
    words = ((analysis.get("transcript") or {}).get("words") or [])
    content = _content_span(analysis, duration, words)
    if content is None:
        return _fail("Analysen hittade inga repliker eller händelser att behålla. Originalvideon används inte som reserv.")
    start, end = content
    pieces = _without_internal_silence(start, end, analysis, words)
    hook = confirmed_hook(analysis, start, end, words)
    ranges: list[tuple[float, float, str, str]] = []
    if hook:
        kept: list[tuple[float, float]] = []
        for piece_start, piece_end in pieces:
            kept.extend(_subtract(piece_start, piece_end, hook["sourceStart"], hook["sourceEnd"]))
        hook_reason = (
            f"Starkaste ögonblicket är {hook['momentStart']:.2f}–{hook['momentEnd']:.2f} s "
            f"({hook['text'] or 'rörelse'}; {', '.join(hook['reasons'])}; poäng {hook['score']}). "
            f"Klippet börjar {hook['sourceStart']:.2f} s så orden i det ögonblicket inte kapas."
        )
        ranges.append((hook["sourceStart"], hook["sourceEnd"], "hook", hook_reason))
        ranges.extend(
            (a, b, "kept", "Repliker och händelser runt öppningen. De tas inte bort bara för att göra klippet kort.")
            for a, b in kept
            if b - a >= MIN_CLIP
        )
    else:
        ranges.extend(
            (a, b, "kept", "Så här långt sträcker sig talet eller det starkaste ögonblicket. Inget senare ögonblick var starkt nog att flyttas först.")
            for a, b in pieces
            if b - a >= MIN_CLIP
        )
    if not ranges:
        return _fail("Inga giltiga klipp kunde byggas. Originalvideon används inte som reserv.")
    removed = _removed_spans(duration, [(a, b) for a, b, _, _ in ranges], words)
    return {
        "ok": True,
        "clips": [
            {"sourceStart": round(a, 3), "sourceEnd": round(b, 3), "label": label, "reason": reason}
            for a, b, label, reason in ranges
        ],
        "hook": hook,
        "removed": [{"start": round(a, 3), "end": round(b, 3), "reason": why} for a, b, why in removed if b - a >= MIN_CLIP],
    }


def confirmed_hook(analysis: dict, content_start: float, content_end: float, words: list[dict]) -> dict | None:
    """The strongest moment, only when several signals agree and it is not already the opening."""
    moments = [m for m in (analysis.get("moments") or []) if "silence" not in (m.get("reasons") or [])]
    if not moments:
        return None
    best = max(moments, key=lambda item: float(item.get("score") or 0))
    reasons = list(best.get("reasons") or [])
    signals = set(reasons) & {"speech", "motion", "face", "scene-cut"}
    score = float(best.get("score") or 0)
    if len(signals) < 2 or score < 1:
        return None
    moment_start, moment_end = float(best["start"]), float(best["end"])
    if moment_end <= content_start + 0.05 or moment_start >= content_end - 0.05:
        return None
    overlapping = [
        word for word in words
        if float(word["end"]) > moment_start and float(word["start"]) < moment_end
    ]
    if overlapping:
        hook_start = min(float(word["start"]) for word in overlapping)
        hook_end = max(float(word["end"]) for word in overlapping)
    else:
        hook_start, hook_end = moment_start, moment_end
    hook_start = max(content_start, hook_start)
    hook_end = min(content_end, hook_end)
    if hook_end - hook_start > 1.4:
        limit = hook_start + 1.4
        fitting = [word for word in overlapping if float(word["start"]) >= hook_start - 0.02 and float(word["end"]) <= limit + 0.02]
        hook_end = max(float(word["end"]) for word in fitting) if fitting else limit
    if hook_end - hook_start < MIN_CLIP:
        return None
    # Already the opening of the material we keep: moving it would not change the first frame.
    if hook_start <= content_start + 0.3:
        return None
    return {
        "sourceStart": round(hook_start, 3),
        "sourceEnd": round(hook_end, 3),
        "momentStart": round(moment_start, 3),
        "momentEnd": round(moment_end, 3),
        "score": round(score, 3),
        "reasons": reasons,
        "text": str(best.get("text") or "")[:80],
    }


def apply_auto_plan(editor, built: dict) -> None:
    editor.snapshot()
    focus = _primary_track(editor.analysis)
    editor.plan["clips"] = [
        {
            "id": _id("c"),
            "sourceStart": spec["sourceStart"],
            "sourceEnd": spec["sourceEnd"],
            "speed": 1.0,
            "zoomStart": 1.0,
            "zoomEnd": 1.0,
            "focusX": 0.5,
            "focusY": 0.45,
            "focusTrack": focus,
            "label": spec["label"],
            "reason": spec.get("reason") or "",
        }
        for spec in built["clips"]
    ]
    editor.plan["effects"] = []
    editor.plan["captions"] = captions_from_words(editor.plan, editor.analysis or {})
    editor.plan["audio"]["keepOriginal"] = True
    editor.plan["audio"]["music"] = False
    editor.plan["audio"]["decorate"] = False
    editor.plan["autoEdit"] = {
        "hook": built.get("hook"),
        "removed": built.get("removed") or [],
    }
    ensure_text(editor.plan)


def prepare_for_render(editor, *, first_cut: bool) -> dict[str, Any]:
    """Use a content-based cut on the first render. Never export a plan that drops the speech."""
    note = None
    if first_cut or not editor.plan["clips"]:
        model_clips = copy.deepcopy(editor.plan["clips"])
        rejected = clip_problems(editor) + content_problems(editor) if model_clips else [
            "Planen är tom. Det finns inga klipp, och originalvideon används inte som reserv.",
        ]
        built = build_auto_plan(editor.analysis)
        if not built.get("ok"):
            problems = rejected + [built["error"]]
            return {"ok": False, "error": _join(problems), "problems": problems}
        apply_auto_plan(editor, built)
        note = _replacement_note(model_clips, rejected, built)
        editor.plan["autoEdit"]["note"] = note
        editor.plan["autoEdit"]["modelClips"] = [
            {"sourceStart": clip.get("sourceStart"), "sourceEnd": clip.get("sourceEnd")} for clip in model_clips
        ]
    else:
        rejected = clip_problems(editor) + content_problems(editor)
        if rejected:
            return {"ok": False, "error": _join(rejected), "problems": rejected}
    problems = clip_problems(editor) + content_problems(editor)
    if problems:
        return {"ok": False, "error": _join(problems), "problems": problems}
    ensure_text(editor.plan)
    problems = text_problems(editor.plan)
    if problems:
        return {"ok": False, "error": _join(problems), "problems": problems}
    return {"ok": True, "note": note}


def content_problems(editor) -> list[str]:
    """A short slice is refused when it leaves out most of the speech or every strong moment."""
    analysis = editor.analysis
    clips = editor.plan.get("clips") or []
    if not analysis or not clips:
        return []
    span = _meaningful_span(analysis)
    if span is None:
        return ["Analysen har inget tal eller ögonblick att behålla. Den här planen kan inte användas."]
    start, end = span
    length = end - start
    covered = _covered_between(clips, start, end)
    problems = []
    if length >= 3 and covered < length * 0.5:
        problems.append(
            f"Klippet behåller {covered:.1f} s av {length:.1f} s innehåll "
            f"(från {start:.2f} s till {end:.2f} s). Det mesta av videons tal eller händelser skulle försvinna, "
            "så den här längden är orimlig för den här filen."
        )
    moments = _strong_moments(analysis)
    if moments and length >= 3 and not any(_covered_between(clips, float(moment["start"]), float(moment["end"])) > 0.05 for moment in moments):
        listed = ", ".join(
            f"{float(moment['start']):.1f}–{float(moment['end']):.1f} s ({moment.get('text') or 'ögonblick'})"
            for moment in moments[:3]
        )
        problems.append(f"Inget klipp träffar de starka ögonblicken i analysen: {listed}.")
    return problems


def _replacement_note(model_clips: list[dict], rejected: list[str], built: dict) -> str:
    parts = []
    if model_clips and rejected:
        spans = ", ".join(f"{float(clip['sourceStart']):.2f}–{float(clip['sourceEnd']):.2f} s" for clip in model_clips)
        parts.append(f"Modellens klipp ({spans}) avvisades. {' '.join(rejected)}")
    elif model_clips:
        parts.append("Modellens tidsval användes inte. Klippningen gjordes från videons analys.")
    else:
        parts.append("Modellen lämnade ingen giltig klippplan. Klippningen gjordes från videons analys.")
    hook = built.get("hook")
    if hook:
        parts.append(
            f"Öppningen är {hook['sourceStart']:.2f}–{hook['sourceEnd']:.2f} s eftersom det starkaste ögonblicket är "
            f"{hook['momentStart']:.2f}–{hook['momentEnd']:.2f} s ({hook.get('text') or 'ögonblick'}, "
            f"{', '.join(hook['reasons'])}, poäng {hook['score']})."
        )
    else:
        parts.append("Inget ögonblick var starkt nog att flyttas först, så det som behålls ligger i tidsordning.")
    removed = built.get("removed") or []
    if removed:
        bits = ", ".join(f"{item['start']:.2f}–{item['end']:.2f} s" for item in removed)
        parts.append(f"Borttaget: {bits}.")
    _, total = timeline({"clips": [
        {"sourceStart": clip["sourceStart"], "sourceEnd": clip["sourceEnd"], "speed": 1} for clip in built["clips"]
    ]})
    parts.append(f"Resultatet är {total:.1f} s. Originalets ljud är kvar. Ingen bakgrundsmusik.")
    return " ".join(parts)


def _meaningful_span(analysis: dict) -> tuple[float, float] | None:
    words = ((analysis.get("transcript") or {}).get("words") or [])
    if words:
        return float(words[0]["start"]), float(words[-1]["end"])
    moments = _strong_moments(analysis)
    if not moments:
        return None
    return min(float(item["start"]) for item in moments), max(float(item["end"]) for item in moments)


def _strong_moments(analysis: dict) -> list[dict]:
    ranked = []
    for moment in analysis.get("moments") or []:
        reasons = set(moment.get("reasons") or [])
        if "silence" in reasons:
            continue
        if float(moment.get("score") or 0) < 1:
            continue
        if not (reasons & {"speech", "motion", "face", "scene-cut"}):
            continue
        ranked.append(moment)
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked[:4]


def _covered_between(clips: list[dict], start: float, end: float) -> float:
    total = 0.0
    for clip in clips:
        left = max(start, float(clip["sourceStart"]))
        right = min(end, float(clip["sourceEnd"]))
        if right > left:
            total += right - left
    return total


def _removed_spans(duration: float, spans: list[tuple[float, float]], words: list[dict]) -> list[tuple[float, float, str]]:
    gaps = _uncovered(duration, spans)
    first = float(words[0]["start"]) if words else None
    last = float(words[-1]["end"]) if words else None
    labeled = []
    for start, end in gaps:
        if first is not None and end <= first + 0.05:
            why = "Dödtid före första repliken."
        elif last is not None and start >= last - 0.05:
            why = "Dödtid efter sista repliken."
        else:
            why = "Tystnad eller uppehåll utan replik."
        labeled.append((start, end, why))
    return labeled


def clip_problems(editor) -> list[str]:
    clips = editor.plan["clips"]
    duration = float((editor.analysis or {}).get("source", {}).get("duration") or 0)
    if not clips:
        return ["Planen är tom. Det finns inga klipp, och originalvideon används inte som reserv."]
    problems = []
    for clip in clips:
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        if duration and (start < -TIME_SLOP or end > duration + TIME_SLOP):
            problems.append(
                f"Klipptiden {start:.2f}–{end:.2f} s ligger utanför videons längd 0–{duration:.2f} s."
            )
        if end - start < MIN_CLIP and float(clip.get("speed") or 1) > 0:
            problems.append(f"Klippet {clip.get('id') or ''} är kortare än 0,2 sekunder och kan inte användas.")
    ordered = sorted(clips, key=lambda clip: float(clip["sourceStart"]))
    for left, right in zip(ordered, ordered[1:]):
        overlap = float(left["sourceEnd"]) - float(right["sourceStart"])
        if overlap > TIME_SLOP:
            problems.append(
                f"Klippen {left.get('id')} och {right.get('id')} överlappar i källan "
                f"({left['sourceStart']}–{left['sourceEnd']} s och {right['sourceStart']}–{right['sourceEnd']} s)."
            )
    dead = removable_dead(editor.analysis) if editor.analysis else []
    dead_total = sum(end - start for start, end in dead)
    still_inside = sum(_covered_between(clips, start, end) for start, end in dead)
    if dead_total >= 0.25 and still_inside >= dead_total * 0.8:
        spans = ", ".join(f"{start:.2f}–{end:.2f} s" for start, end in dead)
        problems.append(
            f"Planen ligger kvar över dödtid ({spans}). Originalvideon används inte oförändrad."
        )
    return problems


def text_problems(plan: dict) -> list[str]:
    _, total = timeline(plan)
    problems = []
    items = list(plan.get("captions") or [])
    items += [effect for effect in plan.get("effects") or [] if effect.get("type") in ("text", "badge")]
    for item in items:
        start, end = float(item.get("start") or 0), float(item.get("end") or 0)
        label = str(item.get("text") or "text")[:40]
        need = min_readable_seconds(label) if item.get("type") in ("text", "badge") else MIN_CLIP
        if end - start < need or start < -TIME_SLOP:
            problems.append(
                f"Texten \"{label}\" är {end - start:.2f} s ({start:.2f}–{end:.2f} s) och hinner inte läsas."
            )
        elif total > 0 and start >= total - 0.01:
            problems.append(f"Texten \"{label}\" börjar efter filmens slut ({start:.2f} s, filmen är {total:.2f} s).")
    if total <= 0:
        return problems
    for sample in (0.3, 1.0, 3.0):
        if sample < total and not text_visible(plan, sample):
            problems.append(f"Ingen synlig text vid {sample:.1f} s. Planen kan inte användas så.")
    return problems


def text_visible(plan: dict, at: float) -> bool:
    for caption in plan.get("captions") or []:
        if float(caption["start"]) <= at < float(caption["end"]):
            return True
    for effect in plan.get("effects") or []:
        if effect.get("type") in ("text", "badge") and float(effect["start"]) <= at < float(effect["end"]):
            return True
    return False


def ensure_text(plan: dict) -> None:
    """Drop unusable times and keep some text on screen through the first seconds."""
    _, total = timeline(plan)
    plan["captions"] = [item for item in plan.get("captions") or [] if _span_ok(item)]
    plan["effects"] = [
        item for item in plan.get("effects") or []
        if item.get("type") not in ("text", "badge") or _span_ok(item)
    ]
    if total <= 0:
        return
    if not plan["captions"] and not any(item.get("type") in ("text", "badge") for item in plan["effects"]):
        plan["captions"] = [{
            "id": _id("cap"),
            "start": 0.0,
            "end": round(min(total, 5.0), 3),
            "text": "LOOK",
            "accent": "LOOK",
            "fontSize": int(plan["style"]["captionFontSize"]),
        }]
    if not plan["captions"]:
        return
    captions = sorted(plan["captions"], key=lambda item: float(item["start"]))
    captions[0]["start"] = 0.0
    for index in range(len(captions) - 1):
        nxt = float(captions[index + 1]["start"])
        start = float(captions[index]["start"])
        # One line at a time: meet the next line, and do not draw two on top of each other.
        if nxt - start >= MIN_CLIP:
            captions[index]["end"] = round(nxt, 3)
        elif float(captions[index]["end"]) < nxt:
            captions[index]["end"] = round(nxt, 3)
    target = min(5.0, total)
    if float(captions[-1]["end"]) < target:
        captions[-1]["end"] = round(target, 3)
    for caption in captions:
        caption["start"] = round(max(0.0, float(caption["start"])), 3)
        caption["end"] = round(max(float(caption["end"]), float(caption["start"]) + MIN_CLIP), 3)
    plan["captions"] = captions


def removable_dead(analysis: dict | None) -> list[tuple[float, float]]:
    if not analysis:
        return []
    duration = float((analysis.get("source") or {}).get("duration") or 0)
    words = ((analysis.get("transcript") or {}).get("words") or [])
    spans: list[tuple[float, float]] = []
    if words:
        lead = float(words[0]["start"])
        tail = float(words[-1]["end"])
        if lead >= 0.25:
            spans.append((0.0, lead))
        if duration - tail >= 0.25:
            spans.append((tail, duration))
    for silence in analysis.get("silences") or []:
        start, end = float(silence["start"]), float(silence["end"])
        if end - start < 0.45:
            continue
        if any(start <= (float(word["start"]) + float(word["end"])) / 2 <= end for word in words):
            continue
        spans.append((start, end))
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a >= 0.25]


def restyle_request(text: str) -> set[str]:
    folded = text.lower()
    kind: set[str] = set()
    if any(token in folded for token in ("större", "storre", "bigger", "larger", "textstorlek", "fontstorlek", "font size", "öka text", "oka text")):
        kind.add("size")
    if any(token in folded for token in ("färg", "farg", "color", "colour")):
        kind.add("color")
    return kind


def locks_structure(text: str) -> bool:
    if not restyle_request(text):
        return False
    folded = text.lower()
    extra = ("pil", "arrow", "zoom", "klipp", "trim", "ta bort", "emoji", "cirkel", "circle", "badge", "musik", "music")
    return not any(token in folded for token in extra)


def color_from_text(text: str) -> str | None:
    folded = text.lower()
    named = (
        ("röd", "#FF2D2D"), ("rod", "#FF2D2D"), ("red", "#FF2D2D"),
        ("vit", "#FFFFFF"), ("white", "#FFFFFF"),
        ("blå", "#2F6BFF"), ("bla", "#2F6BFF"), ("blue", "#2F6BFF"),
    )
    for word, color in named:
        if word in folded:
            return color
    return None


def apply_restyle(project) -> None:
    """A size or color request keeps every existing line and the rest of the edit."""
    kind = getattr(project, "_restyle", None)
    snap = getattr(project, "_restyle_snapshot", None)
    if not kind or not snap or getattr(project, "_restyle_applied", False):
        return
    editor = project.editor
    if getattr(project, "_restyle_lock", False):
        editor.plan["clips"] = copy.deepcopy(snap["clips"])
        editor.plan["effects"] = copy.deepcopy(snap["effects"])
        editor.plan["captions"] = copy.deepcopy(snap["captions"])
        editor.plan["audio"] = copy.deepcopy(snap["audio"])
        editor.plan["style"] = copy.deepcopy(snap["style"])
        if "autoEdit" in snap:
            editor.plan["autoEdit"] = copy.deepcopy(snap["autoEdit"])
    elif len(editor.plan["captions"]) < len(snap["captions"]):
        editor.plan["captions"] = copy.deepcopy(snap["captions"])
        editor.plan["style"]["captionFontSize"] = snap["style"]["captionFontSize"]
        editor.plan["style"]["headlineFontSize"] = snap["style"]["headlineFontSize"]
    if "size" in kind and int(editor.plan["style"]["captionFontSize"]) <= int(snap["style"]["captionFontSize"]):
        editor.replace_captions(scale=1.35)
    if "color" in kind:
        color = color_from_text(getattr(project, "_restyle_text", "") or "")
        if color:
            editor.plan["style"]["accentColor"] = color
    project._restyle_applied = True


def _content_span(analysis: dict, duration: float, words: list[dict]) -> tuple[float, float] | None:
    if words:
        start = max(0.0, float(words[0]["start"]) - 0.04)
        end = min(duration, float(words[-1]["end"]) + 0.08)
        if end - start >= MIN_CLIP:
            return (start, end)
    moments = [m for m in (analysis.get("moments") or []) if "silence" not in (m.get("reasons") or []) and float(m.get("score") or 0) > 0]
    if not moments:
        return None
    best = max(moments, key=lambda item: float(item.get("score") or 0))
    start = max(0.0, float(best["start"]) - 0.1)
    end = min(duration, float(best["end"]) + 0.6)
    if duration > 1 and end - start >= duration * 0.92:
        start, end = float(best["start"]), float(best["end"])
    if end - start < MIN_CLIP:
        return None
    return (start, end)


def _without_internal_silence(start: float, end: float, analysis: dict, words: list[dict]) -> list[tuple[float, float]]:
    pieces = [(start, end)]
    for silence in analysis.get("silences") or []:
        sil_start, sil_end = float(silence["start"]), float(silence["end"])
        if sil_end - sil_start < 0.45:
            continue
        if any(sil_start <= (float(word["start"]) + float(word["end"])) / 2 <= sil_end for word in words):
            continue
        nxt = []
        for piece_start, piece_end in pieces:
            nxt.extend(_subtract(piece_start, piece_end, sil_start, sil_end))
        pieces = nxt
    return [(a, b) for a, b in pieces if b - a >= MIN_CLIP]


def _subtract(start: float, end: float, cut_start: float, cut_end: float) -> list[tuple[float, float]]:
    if cut_end <= start or cut_start >= end:
        return [(start, end)]
    pieces = []
    if cut_start > start + 0.05:
        pieces.append((start, min(cut_start, end)))
    if cut_end < end - 0.05:
        pieces.append((max(cut_end, start), end))
    return [(a, b) for a, b in pieces if b - a >= MIN_CLIP]


def _uncovered(duration: float, spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cursor = 0.0
    gaps = []
    for start, end in sorted(spans):
        if start > cursor + 0.02:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 0.02:
        gaps.append((cursor, duration))
    return gaps


def _span_ok(item: dict) -> bool:
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return start >= -TIME_SLOP and end - start >= MIN_CLIP


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _join(problems: list[str]) -> str:
    return "Planen kan inte användas. " + " ".join(problems)
