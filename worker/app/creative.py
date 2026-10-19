"""Instruction-following editor.

The model may propose timeline operations. A deterministic parser is the
fallback and is never described as language-model understanding. Neither path
adds a stock hook, badge, bubble, or music track unless the instruction asks.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any, Callable

from . import llm_local
from .bot_profile import editor_operation_context, full_behavior_context
from .plan_ops import empty_plan, timeline

_FOLD = str.maketrans("åäöéü", "aaoeu")
_NUMBERS = {
    "one": 1, "en": 1, "ett": 1,
    "two": 2, "tva": 2,
    "three": 3, "tre": 3,
    "four": 4, "fyra": 4,
    "five": 5, "fem": 5,
}
_HYPE = {"look", "wow", "wait", "omg"}
_OPS_GUIDE = """Return only a JSON object {"ops":[...],"summary":"one sentence"}.
Allowed ops:
{"op":"set_clip","sourceStart":0,"sourceEnd":1,"speed":1,"zoomStart":1,"zoomEnd":1,"focusTrack":"","reason":""}
{"op":"trim_opening","seconds":2}
{"op":"zoom_main","zoomEnd":1.55}
{"op":"punch_zoom","zoomEnd":1.3}
{"op":"remove_last_caption"}
{"op":"remove_word","word":"LOOK"}
{"op":"remove_kind","kind":"bubble"}
{"op":"add_hand_arrow"}
{"op":"zoom_touch"}
{"op":"start_reaction"}
{"op":"speed_opening","seconds":2,"speed":1.6}
{"op":"red_accent"}
{"op":"grow_opening"}
{"op":"zoom_reaction","zoomEnd":1.38}
{"op":"blur"}
{"op":"freeze"}
{"op":"speed_ramp"}
{"op":"slow_down"}
{"op":"satisfying_end"}
{"op":"set_audio","keepOriginal":true,"music":false}
Use only source times inside the video and only track ids from the analysis.
Do not invent words, people, or reactions. Do not add music. Keep the original audio unless the user says to remove it.
Do not add LOOK, a badge, or a speech bubble unless the user asked for that thing.
On a revision, change only the asked part. On a first cut, cover the video; do not invent a title card.
"""


def fold(text: str) -> str:
    return " ".join(str(text).lower().translate(_FOLD).split())


def track_view(analysis: dict) -> dict:
    """Face and hand boxes in the shape render_plan already follows."""
    faces = []
    for track in analysis.get("tracks") or []:
        if track.get("kind") not in ("face", "hand", "person"):
            continue
        samples = []
        for sample in track.get("samples") or []:
            box = sample.get("bbox") or sample.get("box")
            if not box or len(box) < 4:
                continue
            samples.append({
                "t": float(sample["t"]),
                "x": float(box[0]),
                "y": float(box[1]),
                "w": float(box[2]),
                "h": float(box[3]),
            })
        if samples:
            faces.append({
                "id": track["id"],
                "kind": track.get("kind"),
                "samples": samples,
                "screenTime": len(samples),
            })
    return {"source": analysis.get("source") or {}, "faces": faces}


def supported_phrases(analysis: dict) -> list[dict]:
    rows = []
    for phrase in analysis.get("phrases") or []:
        text = " ".join(str(phrase.get("text") or "").split())
        if not text or phrase.get("status") not in (None, "SUPPORTED"):
            continue
        rows.append({
            "id": phrase.get("id"),
            "text": text,
            "t0": float(phrase.get("t0") or 0),
            "t1": float(phrase.get("t1") or 0),
        })
    return [row for row in rows if row["t1"] - row["t0"] >= 0.2]


def _duration(analysis: dict) -> float:
    return float((analysis.get("source") or {}).get("duration") or 0)


def _tracks(analysis: dict, kind: str) -> list[dict]:
    return [track for track in analysis.get("tracks") or [] if track.get("kind") == kind and track.get("samples")]


def main_subject(analysis: dict) -> tuple[str | None, str | None]:
    """Prefer a face. If none was found, a pose box is the main subject."""
    for kind in ("face", "person"):
        tracks = _tracks(analysis, kind)
        if tracks:
            return max(tracks, key=lambda track: len(track.get("samples") or []))["id"], kind
    return None, None


def main_face_id(analysis: dict) -> str | None:
    track_id, _kind = main_subject(analysis)
    return track_id


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def initial_plan(analysis: dict) -> dict:
    """One clip of this video. Captions only from supported speech. No stock graphics."""
    duration = _duration(analysis)
    if duration < 0.2:
        raise RuntimeError("Videon är för kort för att klippas.")
    plan = empty_plan()
    plan["audio"]["keepOriginal"] = True
    plan["audio"]["music"] = False
    plan["audio"]["decorate"] = False
    plan["title"] = ""
    face = main_face_id(analysis)
    plan["clips"] = [{
        "id": "c1",
        "sourceStart": 0.0,
        "sourceEnd": round(duration, 3),
        "speed": 1.0,
        "zoomStart": 1.0,
        "zoomEnd": 1.0,
        "focusX": 0.5,
        "focusY": 0.42,
        "focusTrack": face,
        "label": "source",
        "reason": "Hela källan. Instruktionen bad inte om ett annat urval.",
    }]
    for phrase in supported_phrases(analysis):
        plan["captions"].append({
            "id": _new_id("cap"),
            "start": round(phrase["t0"], 3),
            "end": round(phrase["t1"], 3),
            "text": phrase["text"],
            "accent": "",
            "fontSize": 64,
        })
    return plan


def parse_intent(text: str) -> dict[str, Any]:
    folded = fold(text)
    intent: dict[str, Any] = {
        "undo": bool(re.search(r"\b(undo|angra|revert)\b", folded)),
        "redo": bool(re.search(r"(start over|from scratch|gor om hela|borja om|fran borjan|helt ny)", folded)),
        "trim_open": _opening_seconds(folded),
        "zoom_main": _wants_zoom(folded),
        "zoom_percent": _requested_zoom(folded),
        "remove_last_caption": bool(re.search(
            r"(remove|ta bort|radera|tabort).{0,40}(last caption|sista text|sista undertext|sista caption|den dar texten|that text|den texten)",
            folded,
        )),
        "remove_word": _remove_word(text, folded),
        "remove_kind": _remove_kind(folded),
        "add_hand_arrow": _add_hand_arrow(folded),
        "zoom_touch": bool(re.search(r"\b(zoom|zooma)\b", folded)) and bool(
            re.search(r"(touch|rort|pekar|points|armen|arm\b)", folded)
        ),
        "start_reaction": bool(re.search(r"(start with|borja med|oppna med)", folded)) and bool(
            re.search(r"(reaction|reaktion|rolig|funny|skratt|nervos)", folded)
        ) or bool(re.search(r"(his reaction|hennes reaktion|den roliga delen)", folded)),
        "speed_open": _faster_seconds(folded),
        "speed_factor": _requested_speed(folded) or 1.6,
        "red_text": bool(re.search(r"(texten rod|text red|gor texten rod|make the text red|texten röd)", folded)),
        "grow_opening": bool(re.search(r"(storre|bigger|larger)", folded)) and bool(
            re.search(r"(hook|rubrik|headline|oppning|text)", folded)
        ),
        "mute": bool(re.search(r"(remove the audio|ta bort ljudet|mute|utan ljud|no audio)", folded)),
        "want_music": bool(re.search(r"(add music|lagg musik|background music|bakgrundsmusik)", folded))
        and not bool(re.search(r"(do not|don't|dont|ingen |utan |no |inte )", folded)),
        "keep_audio": bool(re.search(r"(keep the original|original audio|original dialogue|behall .{0,12}ljud|behall .{0,16}dialog)", folded)),
        "no_music": bool(re.search(r"(no music|ingen musik|utan musik|do not add background|don't add background|inte .{0,12}musik)", folded)),
        "funnier": bool(re.search(r"(roligare|funnier|rolig text|texten roligare)", folded)),
        "zoom_reaction": bool(re.search(r"\b(zoom|zooma)\b", folded) and re.search(r"reaktion|reaction", folded)),
        "blur": bool(re.search(r"\b(blur|oskarpa|sudda)\b", folded)),
        "freeze": bool(re.search(r"(freeze|frysruta|frys bild|frysrutan)", folded)),
        "speed_ramp": bool(re.search(r"(speed ramp|tempo ramp|tempoökning|tempookning|oka tempot gradvis|ramp)", folded)),
        "slower": bool(re.search(r"(for snabbt|too fast|langsamare|slower|sakta ner)", folded)),
        "captions_bigger": bool(re.search(r"(storre|bigger|larger)", folded) and re.search(r"(caption|undertext)", folded)),
        "satisfying_end": bool(re.search(r"(slutet|ending).{0,30}(satisf|nojd)", folded) or re.search(r"(mer satisfying|more satisfying)", folded)),
        "more_viral": bool(re.search(r"(mer viral|more viral|gor den viral|make it viral)", folded)),
        "arrow_person": _arrow_person(folded),
    }
    intent["punch_zoom"] = bool(re.search(
        r"(punch[\s-]*zoom|zoom.{0,40}(peak|topp|rorelsetopp|motion peak))",
        folded,
    ))
    if intent["punch_zoom"]:
        # A punch zoom is local to a measured moment, not a zoom across the whole clip.
        intent["zoom_main"] = False
    boring = _boring_open(folded)
    if boring and intent["trim_open"] is None:
        intent["trim_open"] = boring
    if intent["add_hand_arrow"]:
        intent["remove_kind"] = None
        intent["arrow_person"] = False
    if intent["zoom_reaction"]:
        intent["zoom_main"] = False
        intent["start_reaction"] = False
    if intent["captions_bigger"]:
        intent["grow_opening"] = False
    intent["any_edit"] = any(
        intent[key] for key in (
            "redo", "trim_open", "zoom_main", "punch_zoom", "remove_last_caption", "remove_word", "remove_kind",
            "add_hand_arrow", "zoom_touch", "start_reaction", "speed_open", "red_text", "grow_opening",
            "mute", "want_music", "keep_audio", "no_music", "funnier", "zoom_reaction", "blur", "freeze",
            "speed_ramp", "slower", "captions_bigger", "satisfying_end", "more_viral", "arrow_person",
        )
    )
    return intent


def _opening_seconds(folded: str) -> float | None:
    if not re.search(r"(remove|cut|drop|ta bort|klipp bort|tabort|kapa)", folded):
        return None
    window = re.search(r"(opening|first|forsta|inled|borjan).{0,24}(second|sekund)", folded)
    if not window:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?|\b(?:one|two|tva|three|tre|four|fyra|five|fem|en|ett)\b)", window.group(0))
    if not match:
        return None
    token = match.group(1).replace(",", ".")
    if token in _NUMBERS:
        return float(_NUMBERS[token])
    try:
        return float(token)
    except ValueError:
        return None


def _faster_seconds(folded: str) -> float | None:
    cue = re.search(r"(faster|snabbare|snabba upp|speed up|hoj tempot|oka hastigheten|oka tempot)", folded)
    if not cue:
        return None
    # Only parse the span AFTER the speed instruction. Earlier trim requests may
    # contain another "first N seconds" phrase and must not override this value.
    tail = folded[cue.end():cue.end() + 100]
    match = re.search(
        r"(first|forsta|opening).{0,24}(\d+(?:[.,]\d+)?|two|tva|three|tre).{0,12}(second|sekund)",
        tail,
    )
    if not match:
        return 2.0 if re.search(r"(first two|forsta tva|opening)", tail) else None
    token = match.group(2).replace(",", ".")
    return float(_NUMBERS.get(token, token))


def _requested_zoom(folded: str) -> float | None:
    """Read an explicit zoom percentage such as 130% without confusing speed ratios."""
    match = re.search(r"(?<!\d)(\d{2,3}(?:[.,]\d+)?)\s*%", folded)
    if not match:
        return None
    try:
        return min(2.2, max(1.0, float(match.group(1).replace(",", ".")) / 100.0))
    except ValueError:
        return None


def _requested_speed(folded: str) -> float | None:
    """Read a requested playback speed near a speed-up instruction."""
    match = re.search(
        r"(?:faster|snabbare|snabba upp|speed up|hoj tempot|oka hastigheten|oka tempot).{0,55}?(?:to|till|at|i)?\s*(\d+(?:[.,]\d+)?)\s*x",
        folded,
    )
    if not match:
        return None
    try:
        return min(2.0, max(1.05, float(match.group(1).replace(",", "."))))
    except ValueError:
        return None


def _wants_zoom(folded: str) -> bool:
    if not re.search(r"\b(zoom|zooma)\b|narmare", folded):
        return False
    if re.search(r"(ta bort|remove|mindre|less|out).{0,16}\bzoom|zoom.{0,16}(mindre|less|out|bort)", folded):
        return False
    if re.search(r"\b(touch|rort|pekar|points|armen|arm)\b", folded):
        return False
    return True


def _remove_word(text: str, folded: str) -> str | None:
    if re.search(r"(last caption|sista text|sista undertext|sista caption)", folded):
        return None
    if re.search(r"(bubbl|arrow|pil |cirkel|circle|emoji)", folded):
        return None
    match = re.search(r"(?:remove|ta bort|radera) (?:the word |ordet |word |the )?([A-Za-zÅÄÖåäö0-9']{2,})", text, re.I)
    if not match:
        return None
    word = match.group(1).strip(".,!?" )
    if fold(word) in {"last", "sista", "opening", "forsta", "first", "audio", "music", "musik", "ljudet"}:
        return None
    return word


def _remove_kind(folded: str) -> str | None:
    if not re.search(r"(ta bort|tabort|remove|radera|utan |ingen )", folded):
        return None
    if re.search(r"(bubbl|bubble|tankebubbl)", folded):
        return "bubble"
    if re.search(r"(\bpil\b|pilen|arrow)", folded) and not re.search(r"(lagg|add |sätt|satt )", folded):
        return "arrow"
    if re.search(r"(cirkel|ringen|circle)", folded):
        return "circle"
    if "emoji" in folded:
        return "emoji"
    if re.search(r"\bbadge\b", folded):
        return "badge"
    return None


def _boring_open(folded: str) -> float | None:
    if not re.search(r"(trakig|boring|dead|tomgang)", folded):
        return None
    match = re.search(r"(forsta|first|opening).{0,16}(\d+(?:[.,]\d+)?|tva|two|tre|three)", folded)
    if not match:
        return 2.0
    token = match.group(2).replace(",", ".")
    return float(_NUMBERS.get(token, token))


def _arrow_person(folded: str) -> bool:
    adding = bool(re.search(r"(lagg|add |satt |sätt )", folded)) or folded.startswith("add ")
    if not adding or not re.search(r"(pil|arrow)", folded):
        return False
    if re.search(r"\bhand\b", folded):
        return False
    return bool(re.search(r"(honom|him\b|henne|her\b)", folded))


def _add_hand_arrow(folded: str) -> bool:
    adding = bool(re.search(r"(lagg|add |satt |sätt )", folded)) or folded.startswith("add ")
    if not adding:
        return False
    return bool(re.search(r"(pil|arrow)", folded)) and bool(re.search(r"(hand|hennes hand|her hand)", folded))


def revise(analysis: dict, plan: dict | None, text: str, *, first: bool, llm: Callable[[list[dict]], str] | None = None, force_rules: bool = False) -> dict:
    """Return a new plan, or an error that leaves the caller's plan untouched."""
    text = " ".join(str(text or "").split())
    if not text:
        return _fail("Skriv vad som ska göras med videon.")
    intent = parse_intent(text)
    if intent["undo"]:
        return {"ok": True, "undo": True, "plan": plan, "changes": [], "backend": "rules", "summary": "undo"}
    if not first and not intent["any_edit"]:
        return _fail(_unknown_message())
    if intent["funnier"] and not _other_than_funnier(intent):
        return _fail(
            "Jag hittar inte på en roligare text. Säg orden som ska stå, eller be mig använda en replik som faktiskt finns i ljudet. "
            "Ingen ny MP4 skapades."
        )
    status = {"ok": False, "backend": "unavailable", "model": None, "error": None, "recovery": llm_local.RECOVERY}
    model_note = None
    if not force_rules:
        status = llm_local.health() if llm is None else {"ok": True, "backend": "ollama", "model": "test", "error": None, "recovery": None}
        if status.get("ok"):
            try:
                raw = llm(_model_messages(analysis, plan, text, first)) if llm else llm_local.complete(_model_messages(analysis, plan, text, first))
                trial, reason = _trial_from_model(analysis, plan, text, first, intent, raw)
            except Exception as error:
                trial, reason = None, f"Ollama svarade inte användbart ({error})."
            if trial is not None:
                trial["decision"] = _decision("ollama", text, [], status)
                return {
                    "ok": True,
                    "undo": False,
                    "plan": trial,
                    "changes": _describe(intent, trial, analysis, first),
                    "backend": "ollama",
                    "summary": "ollama",
                    "modelNote": None,
                }
            model_note = reason
        else:
            model_note = f"{status.get('error')} {status.get('recovery') or ''}".strip()
    ruled = _apply_rules(analysis, plan, text, first, intent)
    if not ruled["ok"]:
        if model_note and model_note not in (ruled.get("error") or ""):
            ruled["error"] = (ruled.get("error") or "") + " " + model_note
        return ruled
    ruled["backend"] = "rules"
    ruled["modelNote"] = model_note
    note = _rules_notice(status, model_note)
    if note:
        ruled["changes"].append(note)
    ruled["plan"]["decision"] = _decision("rules", text, ruled["changes"], status)
    return ruled


def _other_than_funnier(intent: dict) -> bool:
    return any(intent[key] for key in (
        "trim_open", "zoom_main", "remove_last_caption", "remove_word", "remove_kind",
        "add_hand_arrow", "zoom_touch", "start_reaction", "speed_open", "red_text", "grow_opening", "redo",
        "zoom_reaction", "blur", "freeze", "speed_ramp", "slower", "captions_bigger", "satisfying_end",
        "more_viral", "arrow_person",
    ))


def _apply_rules(analysis, plan, text, first, intent) -> dict:
    if intent["funnier"]:
        return _fail("Jag hittar inte på en roligare text. Ingen ny MP4 skapades.")
    try:
        working = initial_plan(analysis) if first or intent["redo"] else copy.deepcopy(plan or empty_plan())
    except RuntimeError as error:
        return _fail(str(error))
    if not working.get("clips"):
        return _fail("Det finns ingen tidslinje att ändra. Gör en short först.")
    changes: list[str] = []
    if first or intent["redo"]:
        changes.append(_base_sentence(analysis, working))
    steps = [
        ("trim_open", lambda: _trim_opening(working, float(intent["trim_open"]), _duration(analysis))),
        ("zoom_reaction", lambda: _zoom_reaction(working, analysis)),
        ("punch_zoom", lambda: _apply_punch_zoom(working, analysis, intent.get("zoom_percent"))),
        ("zoom_main", lambda: (True, _zoom_main(working, analysis, float(intent.get("zoom_percent") or 1.55)))),

        ("blur", lambda: _add_blur(working, analysis)),
        ("freeze", lambda: _freeze(working, analysis)),
        ("speed_ramp", lambda: _speed_ramp(working)),
        ("slower", lambda: _slow_down(working)),
        ("captions_bigger", lambda: _grow_captions(working)),
        ("satisfying_end", lambda: _satisfying_end(working)),
        ("more_viral", lambda: _more_viral(working, analysis) if not first else (True, "")),
        ("arrow_person", lambda: _add_person_arrow(working, analysis)),
        ("zoom_touch", lambda: _zoom_touch(working, analysis)),
        ("start_reaction", lambda: _start_reaction(working, analysis)),
        ("speed_open", lambda: _speed_opening(working, float(intent["speed_open"]), float(intent.get("speed_factor") or 1.6))),
        ("remove_last_caption", lambda: _remove_last_caption(working)),
        ("remove_word", lambda: _drop_word(working, str(intent["remove_word"]))),
        ("remove_kind", lambda: _drop_kind(working, str(intent["remove_kind"]))),
        ("add_hand_arrow", lambda: _add_arrow(working, analysis)),
        ("red_text", lambda: _red_accent(working)),
        ("grow_opening", lambda: _grow_opening(working)),
    ]
    for key, fn in steps:
        if not intent.get(key):
            continue
        ok, message = fn()
        if not ok:
            return _fail(message + " Ingen ny MP4 skapades, och den förra planen ligger kvar.")
        if message:
            changes.append(message)
    _force_audio(working, mute=intent["mute"], music=intent["want_music"])
    if intent["keep_audio"] or intent["no_music"] or first:
        if intent["keep_audio"] or (first and not intent["mute"]):
            changes.append("Originaljudet är kvar.")
        if intent["no_music"] or (first and not intent["want_music"]):
            changes.append("Ingen bakgrundsmusik.")
    if not first and not intent["redo"] and _same_edit(plan, working):
        return _fail("Instruktionen ändrade inte tidslinjen. Ingen ny kopia renderades.")
    return {"ok": True, "undo": False, "plan": working, "changes": changes, "backend": "rules", "summary": "rules", "error": None}


def _base_sentence(analysis: dict, plan: dict) -> str:
    phrases = supported_phrases(analysis)
    track_id, kind = main_subject(analysis)
    duration = _duration(analysis)
    text = (
        f"Tidslinjen är den här videon, {duration:.1f}s, beskuren till stående 9:16. "
        f"{len(phrases)} godkända repliker blev text."
    )
    if not phrases:
        text += " Ingen replik var godkänd, så ingen påhittad text lades in."
    if kind == "face":
        text += f" Fokus följer ansiktsspåret {track_id}."
    elif kind == "person":
        text += f" Inget ansikte hittades. Fokus följer kroppsspåret {track_id}."
    else:
        text += " Inget ansikte eller kroppsspår hittades, så beskärningen sitter i mitten."
    return text


def _trim_opening(plan: dict, seconds: float, source_duration: float) -> tuple[bool, str]:
    del source_duration
    if seconds < 0.2:
        return False, "Tiden som ska tas bort är för kort."
    remain = seconds
    kept = []
    for clip in plan.get("clips") or []:
        speed = float(clip.get("speed") or 1)
        out_dur = float(clip.get("holdSec") or 0.3) if speed <= 0.001 else (float(clip["sourceEnd"]) - float(clip["sourceStart"])) / speed
        if remain <= 0.02:
            kept.append(clip)
            continue
        if out_dur <= remain + 0.02:
            remain -= out_dur
            continue
        if speed <= 0.001:
            return False, "Öppningen är en frysruta, och den kapas inte automatiskt."
        clip = copy.deepcopy(clip)
        clip["sourceStart"] = round(float(clip["sourceStart"]) + remain * speed, 3)
        if float(clip["sourceEnd"]) - clip["sourceStart"] < 0.2:
            return False, "Det som blir kvar efter öppningen är kortare än 0,2 sekunder."
        kept.append(clip)
        remain = 0
    if remain > 0.05 or not kept:
        return False, "Klippet är kortare än öppningen som skulle tas bort."
    plan["clips"] = kept
    _shift_timed(plan, seconds)
    return True, f"De första {seconds:.1f} sekunderna är borttagna."


def _shift_timed(plan: dict, seconds: float) -> None:
    captions = []
    for caption in plan.get("captions") or []:
        start = float(caption["start"]) - seconds
        end = float(caption["end"]) - seconds
        if end <= 0.08:
            continue
        caption["start"] = round(max(0.0, start), 3)
        caption["end"] = round(end, 3)
        if caption["end"] - caption["start"] >= 0.35:
            captions.append(caption)
    plan["captions"] = captions
    effects = []
    for effect in plan.get("effects") or []:
        if effect.get("type") == "sound_effect":
            effect["time"] = round(float(effect.get("time") or 0) - seconds, 3)
            if effect["time"] >= 0:
                effects.append(effect)
            continue
        start = float(effect["start"]) - seconds
        end = float(effect["end"]) - seconds
        if end <= 0.08:
            continue
        effect["start"] = round(max(0.0, start), 3)
        effect["end"] = round(end, 3)
        if effect["end"] - effect["start"] >= 0.15:
            effects.append(effect)
    plan["effects"] = effects


def _zoom_main(plan: dict, analysis: dict, zoom_end: float = 1.55) -> str:
    track_id, kind = main_subject(analysis)
    for clip in plan.get("clips") or []:
        clip["zoomStart"] = round(max(1.12, float(clip.get("zoomStart") or 1)), 3)
        clip["zoomEnd"] = round(max(zoom_end, float(clip.get("zoomEnd") or 1)), 3)
        if track_id:
            clip["focusTrack"] = track_id
    if kind == "face":
        return f"Inzoomning mot huvudmotivet, ansiktsspår {track_id}, zoom {zoom_end}."
    if kind == "person":
        return f"Inzoomning mot huvudmotivet, kroppsspår {track_id}, zoom {zoom_end}. Det är en pose-ruta, inte ett ansikte."
    return f"Inzoomning till {zoom_end} mot bildens mitt. Varken ansikte eller kropp är spårad, så zoomen följer inte en person."


def _remove_last_caption(plan: dict) -> tuple[bool, str]:
    candidates = []
    for index, caption in enumerate(plan.get("captions") or []):
        candidates.append((float(caption.get("end") or 0), 0, index, str(caption.get("text") or "")))
    for index, effect in enumerate(plan.get("effects") or []):
        if effect.get("type") in ("text", "badge") or effect.get("kind") in ("headline", "story", "bubble"):
            candidates.append((float(effect.get("end") or 0), 1, index, str(effect.get("text") or "")))
    if not candidates:
        return False, "Det finns ingen text i planen. Jag lägger inte till en text bara för att kunna ta bort den."
    _end, kind, index, text = max(candidates)
    if kind == 0:
        plan["captions"].pop(index)
    else:
        plan["effects"].pop(index)
    shown = text or "tom rad"
    return True, f"Sista texten är borttagen ({shown}). Övrig text ligger kvar."


def _drop_word(plan: dict, word: str) -> tuple[bool, str]:
    return _drop_word_once(plan, word)


def _drop_word_once(plan: dict, word: str) -> tuple[bool, str]:
    pattern = re.compile(rf"\b{re.escape(word)}\b", re.I)
    hits = 0
    captions = []
    for caption in plan.get("captions") or []:
        text = str(caption.get("text") or "")
        if not pattern.search(text):
            captions.append(caption)
            continue
        hits += 1
        cleaned = " ".join(pattern.sub("", text).split())
        if cleaned:
            caption["text"] = cleaned
            if fold(caption.get("accent") or "") == fold(word):
                caption["accent"] = ""
            captions.append(caption)
    plan["captions"] = captions
    effects = []
    for effect in plan.get("effects") or []:
        text = str(effect.get("text") or "")
        if effect.get("type") in ("text", "badge") and pattern.search(text):
            hits += 1
            cleaned = " ".join(pattern.sub("", text).split())
            if not cleaned:
                continue
            effect["text"] = cleaned
        effects.append(effect)
    plan["effects"] = effects
    if not hits:
        return False, f"Ordet {word} finns inte i planen."
    return True, f"Ordet {word} är borttaget från texten."


def _drop_kind(plan: dict, kind: str) -> tuple[bool, str]:
    before = len(plan.get("effects") or [])
    kept = []
    for effect in plan.get("effects") or []:
        if effect.get("type") == kind or effect.get("kind") == kind:
            continue
        kept.append(effect)
    if len(kept) == before:
        names = {"bubble": "bubbla", "arrow": "pil", "circle": "ring", "emoji": "emoji", "badge": "badge"}
        return False, f"Ingen {names.get(kind, kind)} fanns i planen."
    plan["effects"] = kept
    return True, f"{kind} är borttagen."


def _add_arrow(plan: dict, analysis: dict) -> tuple[bool, str]:
    hands = _tracks(analysis, "hand")
    if not hands:
        return False, (
            "Ingen hand är spårad. Handspår kräver MediaPipe, som inte gav något spår i den här analysen. "
            "Jag ritar inte en pil på en gissad punkt."
        )
    hand = max(hands, key=lambda track: len(track["samples"]))
    t0 = float(hand["samples"][0]["t"])
    t1 = float(hand["samples"][-1]["t"])
    out0 = _source_to_output(plan, t0)
    out1 = _source_to_output(plan, min(t1, t0 + 1.4))
    if out0 is None:
        return False, "Handspåret ligger utanför det aktuella klippet, så pilen läggs inte."
    end = out1 if out1 and out1 > out0 + 0.3 else out0 + 0.8
    plan.setdefault("effects", []).append({
        "id": _new_id("e"),
        "type": "arrow",
        "start": round(out0, 3),
        "end": round(end, 3),
        "track": hand["id"],
        "anchor": "below",
    })
    return True, f"En röd pil följer handspåret {hand['id']} från källtid {t0:.2f}s. Den följer rutan, inte ett namngivet föremål."


def _zoom_touch(plan: dict, analysis: dict) -> tuple[bool, str]:
    found = _touch_span(analysis)
    if not found:
        return False, "Analysen har ingen hand nära ett ansikte och ingen pekning, så jag zoomar inte på en gissad beröring."
    t0, t1, why = found
    ok, message = _zoom_range(plan, t0, t1, analysis)
    if not ok:
        return False, message
    return True, message + " " + why


def _touch_span(analysis: dict) -> tuple[float, float, str] | None:
    for event in analysis.get("evidence") or []:
        if event.get("type") in ("point", "hand_to_mouth"):
            t0 = float(event["t0"])
            t1 = max(float(event["t1"]), t0 + 0.45)
            return t0, min(t1, t0 + 1.2), f"Zoom sitter på {event['type']} {event.get('id') or ''}."
    faces = _tracks(analysis, "face")
    hands = _tracks(analysis, "hand")
    best = None
    for hand in hands:
        for sample in hand["samples"]:
            moment = float(sample["t"])
            box = sample.get("bbox") or [0, 0, 0, 0]
            for face in faces:
                face_box = _sample_at(face, moment)
                if not face_box or not _near(box, face_box):
                    continue
                if best is None or moment < best:
                    best = moment
    if best is None:
        return None
    return best, best + 0.7, "Zoom sitter där en handruta ligger intill en ansiktsruta. Det är närhet, inte en bekräftad beröring av en arm."


def _sample_at(track: dict, moment: float) -> list[float] | None:
    nearest = None
    gap = 0.25
    for sample in track.get("samples") or []:
        delta = abs(float(sample["t"]) - moment)
        if delta < gap:
            nearest = sample.get("bbox")
            gap = delta
    return nearest


def _near(hand: list[float], face: list[float]) -> bool:
    hx, hy, hw, hh = [float(v) for v in hand[:4]]
    fx, fy, fw, fh = [float(v) for v in face[:4]]
    hc = (hx + hw / 2, hy + hh / 2)
    return fx - 0.08 <= hc[0] <= fx + fw + 0.08 and fy - 0.05 <= hc[1] <= fy + fh + 0.2


def _zoom_range(
    plan: dict,
    t0: float,
    t1: float,
    analysis: dict,
    zoom_start: float = 1.2,
    zoom_end: float = 1.65,
    reason: str = "Inzoomning på intervallet instruktionen pekade ut.",
    label: str = "zoom",
) -> tuple[bool, str]:
    face = main_face_id(analysis)
    pieces = []
    hit = False
    for clip in plan.get("clips") or []:
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        if end <= t0 or start >= t1:
            pieces.append(clip)
            continue
        hit = True
        bounds = [start]
        if start + 0.15 < t0 < end - 0.15:
            bounds.append(t0)
        if start + 0.15 < t1 < end - 0.15:
            bounds.append(t1)
        bounds.append(end)
        bounds = sorted(set(round(item, 3) for item in bounds))
        for left, right in zip(bounds, bounds[1:]):
            if right - left < 0.2:
                continue
            piece = copy.deepcopy(clip)
            piece["id"] = _new_id("c")
            piece["sourceStart"] = left
            piece["sourceEnd"] = right
            if left < t1 and right > t0:
                piece["zoomStart"] = round(max(float(piece.get("zoomStart") or 1), zoom_start), 3)
                piece["zoomEnd"] = round(max(float(piece.get("zoomEnd") or 1), zoom_end), 3)
                if face:
                    piece["focusTrack"] = face
                piece["label"] = label
                piece["reason"] = reason
            pieces.append(piece)
    if not hit or not pieces:
        return False, "Intervallet ligger utanför tidslinjen."
    plan["clips"] = pieces
    return True, f"Inzoomning mellan {t0:.2f}s och {t1:.2f}s i källan. {reason}"


def _apply_punch_zoom(plan: dict, analysis: dict, requested_zoom: float | None = None) -> tuple[bool, str]:
    """Zoom only the story's measured punch interval, never the whole clip."""
    from .story import build_story

    punch = build_story(analysis).get("punch")
    if not punch:
        return False, "Ingen uppmätt rörelsetopp eller bekräftad reaktion hittades; ingen punkt att punch-zooma."
    zoom_end = float(requested_zoom or punch.get("zoomEnd") or 1.36)
    zoom_end = min(1.75, max(1.10, zoom_end))
    zoom_start = max(1.08, zoom_end - 0.12)
    reason = f"Begärd lokal punch-zoom {zoom_start:.0%}–{zoom_end:.0%} på uppmätt moment vid {float(punch.get('t0') or 0):.2f}s."
    return _zoom_range(
        plan,
        float(punch["t0"]),
        float(punch["t1"]),
        analysis,
        zoom_start=zoom_start,
        zoom_end=zoom_end,
        reason=reason,
        label="punch",
    )


def _zoom_reaction(plan: dict, analysis: dict) -> tuple[bool, str]:
    from .story import build_story

    story = build_story(analysis)
    punch = story.get("punch")
    if not punch:
        return False, "Ingen rörelsetopp, inget leende och inget skratt hittades, så jag zoomar inte på en gissad reaktion."
    zoom_end = float(punch["zoomEnd"])
    zoom_start = float(punch["zoomStart"])
    current = max((float(clip.get("zoomEnd") or 1) for clip in plan.get("clips") or []), default=1)
    cap = 1.40 if punch.get("confirmed") else 1.20
    if current >= zoom_end - 0.01:
        zoom_end = min(cap, round(current + 0.08, 3))
        zoom_start = min(zoom_end, round(max(zoom_start, current), 3))
    track_id, kind = main_subject(analysis)
    ok, message = _zoom_range(
        plan,
        float(punch["t0"]),
        float(punch["t1"]),
        analysis,
        zoom_start=zoom_start,
        zoom_end=zoom_end,
        reason=str(punch["reason"]),
        label="reaction" if punch.get("confirmed") else "motion",
    )
    if not ok:
        return False, message
    who = "en bekräftad reaktion" if punch.get("confirmed") else "den starkaste rörelsen"
    identity = ""
    if kind != "face":
        identity = " Ingen ansiktsidentitet finns, så 'hans' eller 'hennes' kan inte pekas ut."
    if kind == "person":
        identity += f" Zoomen följer kroppsspåret {track_id}, inte ett ansikte."
    elif kind is None:
        identity += " Inget personspår finns, så zoomen sitter i mitten."
    return True, message + f" Målet är {who}.{identity}"


def _add_blur(plan: dict, analysis: dict) -> tuple[bool, str]:
    rows, total = timeline(plan)
    if not rows or total < 0.4:
        return False, "Det finns inget klipp att sudda."
    start = min(0.4, total * 0.2)
    end = min(total, start + 0.7)
    track_id, kind = main_subject(analysis)
    plan.setdefault("effects", []).append({
        "id": _new_id("e"),
        "type": "blur",
        "start": round(start, 3),
        "end": round(end, 3),
        "track": track_id or "",
    })
    if kind in ("face", "person"):
        return True, f"Bakgrunden suddas {start:.1f}–{end:.1f}s. Spåret {track_id} lämnas skarpt."
    return True, f"Hela bilden suddas {start:.1f}–{end:.1f}s eftersom inget ansikte eller kroppsspår finns att skydda."


def _freeze(plan: dict, analysis: dict) -> tuple[bool, str]:
    from .story import build_story

    story = build_story(analysis)
    moment = float((story.get("peak") or {}).get("t") or 0)
    duration = _duration(analysis)
    if duration < 0.8:
        return False, "Klippet är för kort för en frysruta."
    moment = min(max(0.2, moment), max(0.2, duration - 0.3))
    held = None
    pieces = []
    for clip in plan.get("clips") or []:
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        if held is None and start <= moment < end and end - start >= 0.5:
            head = copy.deepcopy(clip)
            tail = copy.deepcopy(clip)
            head["id"] = _new_id("c")
            tail["id"] = _new_id("c")
            head["sourceEnd"] = round(moment, 3)
            tail["sourceStart"] = round(moment, 3)
            freeze = copy.deepcopy(clip)
            freeze["id"] = _new_id("c")
            freeze["sourceStart"] = round(moment, 3)
            freeze["sourceEnd"] = round(min(end, moment + 0.08), 3)
            freeze["speed"] = 0.001
            freeze["holdSec"] = 0.4
            freeze["label"] = "freeze"
            freeze["reason"] = "Frysruta på den starkaste uppmätta tidpunkten."
            if head["sourceEnd"] - float(head["sourceStart"]) >= 0.2:
                pieces.append(head)
            pieces.append(freeze)
            if float(tail["sourceEnd"]) - float(tail["sourceStart"]) >= 0.2:
                pieces.append(tail)
            held = moment
            continue
        pieces.append(clip)
    if held is None or not pieces:
        return False, "Frysrutan fick ingen plats i tidslinjen."
    plan["clips"] = pieces
    return True, f"En frysruta på 0,4s ligger vid {held:.2f}s i källan."


def _speed_ramp(plan: dict) -> tuple[bool, str]:
    if not plan.get("clips"):
        return False, "Det finns inget klipp att rampa."
    index = max(range(len(plan["clips"])), key=lambda item: float(plan["clips"][item]["sourceEnd"]) - float(plan["clips"][item]["sourceStart"]))
    clip = plan["clips"][index]
    start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
    if end - start < 1.8:
        clip["speed"] = 1.25
        return True, "Klippet är kort, så hela biten spelas i 1,25x. En tredelad ramp fick inte plats."
    cuts = [start, start + (end - start) / 3, start + 2 * (end - start) / 3, end]
    speeds = (1.0, 1.28, 1.08)
    pieces = []
    for left, right, speed in zip(cuts, cuts[1:], speeds):
        piece = copy.deepcopy(clip)
        piece["id"] = _new_id("c")
        piece["sourceStart"] = round(left, 3)
        piece["sourceEnd"] = round(right, 3)
        piece["speed"] = speed
        piece["label"] = "ramp"
        piece["reason"] = "Tempot ökar i mitten och faller tillbaka."
        pieces.append(piece)
    plan["clips"] = plan["clips"][:index] + pieces + plan["clips"][index + 1:]
    return True, "En speed ramp delar klippet i 1,00x, 1,28x och 1,08x."


def _slow_down(plan: dict) -> tuple[bool, str]:
    changed = False
    for clip in plan.get("clips") or []:
        if float(clip.get("speed") or 1) > 1.05:
            clip["speed"] = 1.0
            changed = True
    if changed:
        return True, "Delar som gick fortare än 1x är tillbaka på normalt tempo."
    if not plan.get("clips"):
        return False, "Det finns inget klipp att sakta ner."
    clip = max(plan["clips"], key=lambda item: float(item["sourceEnd"]) - float(item["sourceStart"]))
    clip["speed"] = 0.85
    return True, "Det längsta klippet spelas i 0,85x."


def _grow_captions(plan: dict) -> tuple[bool, str]:
    if not plan.get("captions"):
        return False, "Det finns inga undertexter att förstora. Jag hittar inte på text."
    for caption in plan["captions"]:
        caption["fontSize"] = min(110, int(caption.get("fontSize") or 64) + 18)
    return True, "Undertexterna är större."


def _satisfying_end(plan: dict) -> tuple[bool, str]:
    if not plan.get("clips"):
        return False, "Det finns inget slut att hålla kvar."
    clip = plan["clips"][-1]
    start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
    speed = float(clip.get("speed") or 1)
    tail = 1.2 * speed
    if end - start < tail + 0.4:
        clip["speed"] = min(speed, 0.9)
        return True, "Slutet är något långsammare. Ingen loop lades till."
    head = copy.deepcopy(clip)
    last = copy.deepcopy(clip)
    head["id"] = _new_id("c")
    last["id"] = _new_id("c")
    head["sourceEnd"] = round(end - tail, 3)
    last["sourceStart"] = head["sourceEnd"]
    last["speed"] = 0.85
    last["label"] = "ending"
    last["reason"] = "Slutet får ligga kvar lite längre. Ingen påtvingad loop."
    plan["clips"] = plan["clips"][:-1] + [head, last]
    return True, "Sista 1,2 sekunderna spelas i 0,85x. Ingen loop lades till."


def _more_viral(plan: dict, analysis: dict) -> tuple[bool, str]:
    from .story import build_story

    story = build_story(analysis)
    notes = []
    dead = float(story.get("deadOpeningS") or 0)
    if dead >= 0.8:
        ok, message = _trim_opening(plan, dead, _duration(analysis))
        if ok:
            notes.append(message)
    punch = story.get("punch")
    if punch:
        ok, message = _zoom_range(
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
    if not notes:
        return False, "Inget svagt parti eller någon rörelsetopp kunde ändras utan att hitta på en hook."
    return True, " ".join(notes)


def _add_person_arrow(plan: dict, analysis: dict) -> tuple[bool, str]:
    track_id, kind = main_subject(analysis)
    if not track_id:
        return False, "Ingen person är spårad, så en pil skulle peka på en gissad punkt. Ingen pil lades in."
    rows, total = timeline(plan)
    if not rows:
        return False, "Det finns ingen tidslinje att sätta pilen på."
    start = min(0.6, max(0.2, total * 0.15))
    plan.setdefault("effects", []).append({
        "id": _new_id("e"),
        "type": "arrow",
        "start": round(start, 3),
        "end": round(min(total, start + 1.2), 3),
        "track": track_id,
        "anchor": "below",
    })
    if kind == "face":
        return True, f"En pil följer ansiktsspåret {track_id}."
    return True, f"En pil följer kroppsspåret {track_id}. Det är en pose-ruta, inte ett ansikte och inte en identitet."


def _start_reaction(plan: dict, analysis: dict) -> tuple[bool, str]:
    span = _reaction_span(analysis)
    if not span:
        return False, "Analysen har inget leende eller skratt, så jag flyttar inte öppningen till en gissad reaktion."
    t0, t1, label = span
    first = plan["clips"][0]
    if abs(float(first["sourceStart"]) - t0) < 0.3:
        return True, "Reaktionen ligger redan först. Ordningen är oförändrad i övrigt."
    anchored = []
    for caption in plan.get("captions") or []:
        src0 = _output_to_source(plan, float(caption["start"]))
        src1 = _output_to_source(plan, float(caption["end"]))
        if src0 is None or src1 is None:
            continue
        caption["start"], caption["end"] = src0, src1
        anchored.append(caption)
    plan["captions"] = anchored
    opening = copy.deepcopy(first)
    opening["id"] = _new_id("c")
    opening["sourceStart"] = round(t0, 3)
    opening["sourceEnd"] = round(t1, 3)
    opening["label"] = "reaction"
    opening["reason"] = label
    rest = []
    for clip in plan["clips"]:
        rest.extend(_subtract_clip(clip, t0, t1))
    plan["clips"] = [opening, *rest]
    _retime_from_source(plan)
    return True, f"Öppningen är reaktionen {t0:.2f}–{t1:.2f}s ({label})."


def _reaction_span(analysis: dict) -> tuple[float, float, str] | None:
    events = [event for event in analysis.get("evidence") or [] if event.get("type") in ("smile", "laughter")]
    if not events:
        return None
    best = max(events, key=lambda event: float(event.get("confidence") or 0))
    t0 = float(best["t0"])
    t1 = min(max(float(best["t1"]), t0 + 0.6), t0 + 1.6)
    return t0, t1, f"{best['type']} {best.get('id') or ''}".strip()


def _subtract_clip(clip: dict, cut0: float, cut1: float) -> list[dict]:
    start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
    spans = []
    if start < cut0 - 0.05:
        spans.append((start, min(end, cut0)))
    if end > cut1 + 0.05:
        spans.append((max(start, cut1), end))
    pieces = []
    for left, right in spans:
        if right - left < 0.2:
            continue
        piece = copy.deepcopy(clip)
        piece["id"] = _new_id("c")
        piece["sourceStart"] = round(left, 3)
        piece["sourceEnd"] = round(right, 3)
        pieces.append(piece)
    return pieces


def _retime_from_source(plan: dict) -> None:
    """Captions keep source times only when the timeline is still one forward clip.

    After a reorder, drop captions whose source window is no longer the output clock.
    A caption is kept when its original start still falls inside a clip, shifted to output time.
    """
    moved = []
    for caption in plan.get("captions") or []:
        start = _source_to_output(plan, float(caption["start"]))
        end = _source_to_output(plan, float(caption["end"]))
        if start is None or end is None or end - start < 0.2:
            continue
        caption["start"] = round(start, 3)
        caption["end"] = round(end, 3)
        moved.append(caption)
    plan["captions"] = moved


def _speed_opening(plan: dict, seconds: float, speed: float = 1.6) -> tuple[bool, str]:
    speed = min(2.0, max(1.05, float(speed)))
    if not plan.get("clips"):
        return False, "Det finns inget klipp att snabba på."
    rows, _total = timeline(plan)
    if _total <= seconds + 0.05:
        for clip in plan["clips"]:
            clip["speed"] = speed
        return True, f"Hela klippet spelas i {speed}x eftersom det är kortare än {seconds:.0f} sekunder."
    consumed = 0.0
    pieces = []
    sped = False
    for row in rows:
        out_len = float(row["outEnd"]) - float(row["outStart"])
        if consumed >= seconds - 0.02:
            pieces.append(_strip_row(row))
            continue
        if consumed + out_len <= seconds + 0.02:
            piece = _strip_row(row)
            piece["speed"] = speed
            pieces.append(piece)
            consumed += out_len
            sped = True
            continue
        need = seconds - consumed
        src_need = need * float(row.get("speed") or 1)
        head = _strip_row(row)
        tail = _strip_row(row)
        head["id"] = _new_id("c")
        tail["id"] = _new_id("c")
        head["sourceEnd"] = round(float(row["sourceStart"]) + src_need, 3)
        tail["sourceStart"] = head["sourceEnd"]
        head["speed"] = speed
        if head["sourceEnd"] - float(head["sourceStart"]) >= 0.2:
            pieces.append(head)
            sped = True
        if float(tail["sourceEnd"]) - float(tail["sourceStart"]) >= 0.2:
            pieces.append(tail)
        consumed = seconds
    if not sped or not pieces:
        return False, "Öppningen kunde inte delas för ett högre tempo."
    plan["clips"] = pieces
    return True, f"De första {seconds:.0f} sekunderna av tidslinjen spelas i {speed}x. Resten är oförändrad."


def _strip_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in ("outStart", "outEnd")}


def _red_accent(plan: dict) -> tuple[bool, str]:
    changed = False
    for caption in plan.get("captions") or []:
        word = _accent_word(str(caption.get("text") or ""))
        if not word:
            continue
        caption["accent"] = word
        changed = True
    for effect in plan.get("effects") or []:
        if effect.get("type") != "text":
            continue
        word = _accent_word(str(effect.get("text") or ""))
        if word:
            effect["accent"] = word
            changed = True
    if not changed:
        return False, "Det finns ingen text att färga. Renderaren kan göra ett ord rött, inte en färg som inte finns i typsnittet."
    return True, "Ett ord i varje textrad är rött. Renderaren har en accentfärg, inte en fri färg per bokstav."


def _accent_word(text: str) -> str:
    words = re.findall(r"[A-Za-zÅÄÖåäö0-9']+", text)
    if not words:
        return ""
    return max(words, key=len)


def _grow_opening(plan: dict) -> tuple[bool, str]:
    grown = False
    for effect in plan.get("effects") or []:
        if effect.get("type") == "text" and effect.get("kind") == "headline":
            effect["fontSize"] = min(220, int(round(int(effect.get("fontSize") or 96) * 1.4)))
            grown = True
    if not grown and plan.get("captions"):
        first = min(plan["captions"], key=lambda caption: float(caption.get("start") or 0))
        first["fontSize"] = min(140, int(round(int(first.get("fontSize") or 64) * 1.35)))
        grown = True
    if not grown:
        return False, "Det finns ingen hook-text att förstora. Ordet LOOK läggs inte till."
    return True, "Öppningstexten är större."


def _force_audio(plan: dict, *, mute: bool, music: bool) -> None:
    audio = plan.setdefault("audio", {})
    audio["keepOriginal"] = not mute
    audio["music"] = bool(music)
    audio["decorate"] = False
    audio.setdefault("targetLufs", -14)


def _output_to_source(plan: dict, moment: float) -> float | None:
    cursor = 0.0
    for clip in plan.get("clips") or []:
        speed = float(clip.get("speed") or 1)
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        out_dur = float(clip.get("holdSec") or 0.3) if speed <= 0.001 else (end - start) / speed
        if cursor - 0.02 <= moment <= cursor + out_dur + 0.02:
            if speed <= 0.001:
                return start
            return start + max(0.0, moment - cursor) * speed
        cursor += out_dur
    return None


def _source_to_output(plan: dict, moment: float) -> float | None:
    cursor = 0.0
    for clip in plan.get("clips") or []:
        speed = float(clip.get("speed") or 1)
        start, end = float(clip["sourceStart"]), float(clip["sourceEnd"])
        out_dur = float(clip.get("holdSec") or 0.3) if speed <= 0.001 else (end - start) / speed
        if speed > 0.001 and start - 0.02 <= moment <= end + 0.02:
            return cursor + max(0.0, moment - start) / speed
        cursor += out_dur
    return None


def _same_edit(before: dict | None, after: dict) -> bool:
    if not before:
        return False
    keys = ("clips", "captions", "effects", "audio")
    return _slim(before, keys) == _slim(after, keys)


def _slim(plan: dict, keys: tuple[str, ...]) -> str:
    payload = {key: plan.get(key) for key in keys}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _model_messages(analysis: dict, plan: dict | None, text: str, first: bool) -> list[dict]:
    brief = {
        "instruction": text,
        "firstCut": first,
        "duration": _duration(analysis),
        "faces": [{"id": track["id"], "samples": len(track.get("samples") or [])} for track in _tracks(analysis, "face")],
        "hands": [{"id": track["id"], "samples": len(track.get("samples") or [])} for track in _tracks(analysis, "hand")],
        "phrases": [{"text": row["text"], "t0": row["t0"], "t1": row["t1"]} for row in supported_phrases(analysis)],
        "evidence": sorted({str(event.get("type")) for event in analysis.get("evidence") or []}),
        "limitations": list(analysis.get("unavailable") or [])[:6],
        "clips": [
            {"sourceStart": clip.get("sourceStart"), "sourceEnd": clip.get("sourceEnd"), "speed": clip.get("speed"), "zoomEnd": clip.get("zoomEnd")}
            for clip in (plan or {}).get("clips") or []
        ],
        "captions": [{"text": caption.get("text"), "start": caption.get("start"), "end": caption.get("end")} for caption in (plan or {}).get("captions") or []],
    }
    return [
        {"role": "system", "content": full_behavior_context() + "\n\n" + _OPS_GUIDE + "\n\n" + editor_operation_context()},
        {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
    ]


def _trial_from_model(analysis, plan, text, first, intent, raw: str) -> tuple[dict | None, str]:
    ops = _parse_ops(raw)
    if not ops:
        return None, "Ollama skickade ingen giltig lista med åtgärder."
    try:
        working = initial_plan(analysis) if first or intent["redo"] else copy.deepcopy(plan or empty_plan())
    except RuntimeError as error:
        return None, str(error)
    for op in ops:
        ok, message = _apply_op(working, analysis, op, intent, first)
        if not ok:
            return None, message
    _force_audio(working, mute=intent["mute"], music=bool(intent["want_music"]))
    problem = _rejects(plan, working, intent, analysis, text, first)
    if problem:
        return None, problem
    return working, ""


def _parse_ops(raw: str) -> list[dict] | None:
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    ops = payload.get("ops") if isinstance(payload, dict) else None
    if not isinstance(ops, list) or not ops:
        return None
    if not all(isinstance(item, dict) and item.get("op") for item in ops):
        return None
    return ops


def _apply_op(plan: dict, analysis: dict, op: dict, intent: dict, first: bool) -> tuple[bool, str]:
    name = str(op.get("op"))
    allowed = _allowed_ops(intent, first)
    if name not in allowed:
        return False, f"Åtgärden {name} ingick inte i instruktionen."
    if name == "set_clip":
        return _op_set_clip(plan, analysis, op)
    if name == "trim_opening":
        return _trim_opening(plan, float(op.get("seconds") or intent.get("trim_open") or 0), _duration(analysis))
    if name == "zoom_main":
        target_zoom = float(op.get("zoomEnd") or intent.get("zoom_percent") or 1.55)
        _zoom_main(plan, analysis, target_zoom)
        return True, "zoom"
    if name == "punch_zoom":
        return _apply_punch_zoom(plan, analysis, float(op.get("zoomEnd") or intent.get("zoom_percent") or 0) or None)
    if name == "remove_last_caption":
        return _remove_last_caption(plan)
    if name == "remove_word":
        return _drop_word_once(plan, str(op.get("word") or intent.get("remove_word") or ""))
    if name == "remove_kind":
        return _drop_kind(plan, str(op.get("kind") or intent.get("remove_kind") or ""))
    if name == "add_hand_arrow":
        return _add_arrow(plan, analysis)
    if name == "zoom_touch":
        return _zoom_touch(plan, analysis)
    if name == "start_reaction":
        return _start_reaction(plan, analysis)
    if name == "speed_opening":
        target_speed = float(op.get("speed") or intent.get("speed_factor") or 1.6)
        return _speed_opening(plan, float(op.get("seconds") or intent.get("speed_open") or 2), target_speed)
    if name == "red_accent":
        return _red_accent(plan)
    if name == "grow_opening":
        return _grow_opening(plan)
    if name == "zoom_reaction":
        return _zoom_reaction(plan, analysis)
    if name == "blur":
        return _add_blur(plan, analysis)
    if name == "freeze":
        return _freeze(plan, analysis)
    if name == "speed_ramp":
        return _speed_ramp(plan)
    if name == "slow_down":
        return _slow_down(plan)
    if name == "satisfying_end":
        return _satisfying_end(plan)
    if name == "set_audio":
        _force_audio(plan, mute=not bool(op.get("keepOriginal", True)), music=bool(op.get("music")))
        return True, "audio"
    return False, f"Okänd åtgärd {name}."


def _allowed_ops(intent: dict, first: bool) -> set[str]:
    ops = {"set_audio"}
    if first or intent.get("redo"):
        ops.add("set_clip")
    mapping = {
        "trim_open": "trim_opening",
        "zoom_main": "zoom_main",
        "punch_zoom": "punch_zoom",
        "remove_last_caption": "remove_last_caption",
        "remove_word": "remove_word",
        "remove_kind": "remove_kind",
        "add_hand_arrow": "add_hand_arrow",
        "zoom_touch": "zoom_touch",
        "start_reaction": "start_reaction",
        "speed_open": "speed_opening",
        "red_text": "red_accent",
        "grow_opening": "grow_opening",
        "zoom_reaction": "zoom_reaction",
        "blur": "blur",
        "freeze": "freeze",
        "speed_ramp": "speed_ramp",
        "slower": "slow_down",
        "satisfying_end": "satisfying_end",
        "more_viral": "zoom_reaction",
    }
    for key, op in mapping.items():
        if intent.get(key):
            ops.add(op)
    return ops


def _op_set_clip(plan: dict, analysis: dict, op: dict) -> tuple[bool, str]:
    duration = _duration(analysis)
    start, end = float(op.get("sourceStart") or 0), float(op.get("sourceEnd") or 0)
    if start < -0.05 or end > duration + 0.08 or end - start < 0.2:
        return False, "Klipptiden ligger utanför videon."
    face = str(op.get("focusTrack") or "") or main_face_id(analysis)
    if face and face not in {track["id"] for track in analysis.get("tracks") or []}:
        return False, f"Spåret {face} finns inte i analysen."
    zoom_end = min(2.2, max(1.0, float(op.get("zoomEnd") or 1)))
    plan["clips"] = [{
        "id": "c1",
        "sourceStart": round(max(0.0, start), 3),
        "sourceEnd": round(min(duration, end), 3),
        "speed": min(2.0, max(0.5, float(op.get("speed") or 1))),
        "zoomStart": min(2.2, max(1.0, float(op.get("zoomStart") or 1))),
        "zoomEnd": zoom_end,
        "focusX": 0.5,
        "focusY": 0.42,
        "focusTrack": face,
        "label": "model",
        "reason": str(op.get("reason") or "")[:180],
    }]
    return True, "clip"


def _rejects(before, after, intent, analysis, text, first) -> str | None:
    if after.get("audio", {}).get("music") and not intent.get("want_music"):
        return "Modellen lade på musik fast instruktionen inte bad om det."
    if not after.get("audio", {}).get("keepOriginal", True) and not intent.get("mute"):
        return "Modellen tog bort originalljudet."
    if _invented(after, analysis, text):
        return "Modellen lade in text som inte finns i repliken eller i instruktionen."
    base = initial_plan(analysis) if first or intent.get("redo") else before
    if intent.get("trim_open") is not None:
        origin = float((base or {}).get("clips", [{}])[0].get("sourceStart") or 0)
        now = float(after["clips"][0]["sourceStart"])
        if now + 0.15 < origin + float(intent["trim_open"]):
            return "Modellen tog inte bort öppningen."
    if intent.get("punch_zoom"):
        from .story import build_story
        punch = build_story(analysis).get("punch")
        if not punch:
            return "Ingen uppmätt rörelsetopp finns att punch-zooma på."
        required_zoom = float(intent.get("zoom_percent") or punch.get("zoomEnd") or 1.36)
        t0, t1 = float(punch["t0"]), float(punch["t1"])
        local = [
            clip for clip in after.get("clips") or []
            if float(clip.get("sourceStart") or 0) < t1
            and float(clip.get("sourceEnd") or 0) > t0
            and clip.get("label") == "punch"
        ]
        if not local or max(float(clip.get("zoomEnd") or 1) for clip in local) < required_zoom - 0.02:
            return f"Modellen lade inte in en lokal punch-zoom till {required_zoom:.0%} på den uppmätta rörelsetoppen."
    elif intent.get("zoom_main"):
        before_zoom = max((float(clip.get("zoomEnd") or 1) for clip in (base or {}).get("clips") or []), default=1)
        after_zoom = max(float(clip.get("zoomEnd") or 1) for clip in after["clips"])
        required_zoom = float(intent.get("zoom_percent") or 1.4)
        if after_zoom < max(required_zoom, before_zoom + 0.08):
            return f"Modellen zoomade inte till begärd nivå ({required_zoom:.0%})."
    elif intent.get("zoom_reaction"):
        after_zoom = max(float(clip.get("zoomEnd") or 1) for clip in after["clips"])
        if after_zoom < 1.10:
            return "Modellen zoomade inte på reaktionen."
        if after_zoom > 1.42:
            return "Modellen zoomade hårdare än 140%."
    elif not intent.get("zoom_touch"):
        after_zoom = max((float(clip.get("zoomEnd") or 1) for clip in after["clips"]), default=1)
        if after_zoom > 1.05:
            return "Modellen zoomade fast instruktionen inte bad om zoom."
    if intent.get("speed_open") is not None:
        required_speed = float(intent.get("speed_factor") or 1.6)
        first_start = float((after.get("clips") or [{}])[0].get("sourceStart") or 0)
        first_source = [
            clip for clip in after.get("clips") or []
            if float(clip.get("speed") or 1) >= required_speed - 0.05
            and float(clip.get("sourceStart") or 0) <= first_start + 0.08
        ]
        sped_source_seconds = sum(
            max(0.0, float(clip.get("sourceEnd") or 0) - float(clip.get("sourceStart") or 0))
            for clip in first_source
        )
        if sped_source_seconds < min(float(intent["speed_open"]), 0.8):
            return f"Modellen ökade inte öppningens hastighet till {required_speed:.2f}x."
    if intent.get("remove_last_caption"):
        if _caption_count(after) != _caption_count(base) - 1:
            return "Modellen tog inte bort exakt den sista texten."
    if not first and not intent.get("redo") and _same_edit(before, after):
        return "Modellen ändrade inte tidslinjen."
    return None


def _invented(plan: dict, analysis: dict, user: str) -> bool:
    phrases = [fold(row["text"]) for row in supported_phrases(analysis)]
    user_folded = fold(user)
    allowed = _factual_tokens(plan)
    for text in _texts(plan):
        token = fold(text)
        if not token:
            continue
        if token in allowed:
            continue
        if token in _HYPE and token not in user_folded and token not in phrases:
            return True
        if token in user_folded or any(token in phrase for phrase in phrases):
            continue
        return True
    return False


def _factual_tokens(plan: dict) -> set[str]:
    found = {fold(str(item)) for item in ((plan.get("studio") or {}).get("factualLabels") or []) if str(item).strip()}
    for effect in plan.get("effects") or []:
        if effect.get("factual") and effect.get("text"):
            found.add(fold(str(effect["text"])))
    return {token for token in found if token}


def _texts(plan: dict) -> list[str]:
    found = [str(caption.get("text") or "") for caption in plan.get("captions") or []]
    found += [str(effect.get("text") or "") for effect in plan.get("effects") or [] if effect.get("text")]
    return found


def _caption_count(plan: dict | None) -> int:
    if not plan:
        return 0
    count = len(plan.get("captions") or [])
    count += sum(1 for effect in plan.get("effects") or [] if effect.get("type") in ("text", "badge"))
    return count


def _describe(intent: dict, plan: dict, analysis: dict, first: bool) -> list[str]:
    changes = []
    if first:
        changes.append(_base_sentence(analysis, plan))
    if intent.get("trim_open"):
        changes.append(f"De första {float(intent['trim_open']):.1f} sekunderna är borttagna.")
    if intent.get("zoom_main") or intent.get("zoom_reaction"):
        face = main_face_id(analysis)
        zoom = max(float(clip.get("zoomEnd") or 1) for clip in plan["clips"])
        changes.append(f"Inzoomning till {zoom:.2f}" + (f" på {face}." if face else " mot mitten. Inget ansikte är spårat."))
    if intent.get("remove_last_caption"):
        changes.append("Sista texten är borttagen.")
    changes.append("Originaljudet är kvar." if plan["audio"].get("keepOriginal", True) else "Originalljudet är av.")
    if not plan["audio"].get("music"):
        changes.append("Ingen bakgrundsmusik.")
    return changes


def _decision(backend: str, text: str, changes: list[str], status: dict) -> dict:
    return {
        "backend": backend,
        "model": status.get("model"),
        "instruction": text,
        "changes": changes,
        "llmOk": bool(status.get("ok")),
    }


def _rules_notice(status: dict, model_note: str | None) -> str:
    if not status.get("ok"):
        return "Regelmotorn gjorde det här, inte en språkmodell. " + str(status.get("error") or "") + " " + str(status.get("recovery") or "")
    if model_note:
        return "Ollama tillfrågades, men svaret användes inte (" + model_note + "). Regelmotorn gjorde ändringen. Det är inte full språkförståelse."
    return ""


def _unknown_message() -> str:
    return (
        "Jag förstod inte instruktionen. Ingen ny MP4 skapades. "
        "Regelmotorn kan till exempel: ta bort de första två sekunderna, zooma in på personen, "
        "ta bort sista texten, ångra, ta bort ordet LOOK, lägg en pil på handen när en hand är spårad. "
        "För en fri formulering behöver lokal Ollama med qwen2.5:3b vara igång."
    )


def _fail(message: str) -> dict:
    return {"ok": False, "undo": False, "plan": None, "changes": [], "backend": "rules", "summary": None, "error": message}
