"""End-to-end short: VideoAnalysis v2, a storyboard, the existing renderer, then QA.

This module does not change the v1 edit path. It calls render_plan as it stands.
"""

from __future__ import annotations

import json
import math
import re
import urllib.request
from pathlib import Path

from .analysis_v2 import phrases_for_editplan
from .config import MODEL, OPENAI_API_KEY, OPENAI_BASE_URL
from .export_spec import SHORT_EXPORT, describe_export
from .ffmpeg_util import probe, run
from .plan_ops import empty_plan, min_readable_seconds, timeline
from .render import render_plan
from .safety import lint_emoji, lint_text

_FILLER = {"um", "uh", "hmm", "ah", "er", "but", "like", "so", "yeah", "ok", "okay"}
_HOOK_FALLBACK = "LOOK"
# A Short should land on at least this much real timeline. The extra tenth
# covers frame rounding so the encoded file still probes at >= 13s.
MIN_SHORT_S = 13.0
_AIM_S = 13.25
_SLOW_SPEED = 0.5


def produce_short(video: Path, analysis: dict, prompt: str, out_path: Path) -> dict:
    """Analysis is already built. Returns the storyboard, plan, and QA."""
    board = build_storyboard(analysis, prompt)
    plan = plan_from_storyboard(board, analysis)
    view = renderer_view(analysis)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = render_plan(Path(video), view, plan, Path(out_path), delivery=SHORT_EXPORT)
    qa = qa_output(Path(out_path), plan, analysis)
    return {"storyboard": board, "plan": plan, "render": info, "qa": qa}


def renderer_view(analysis: dict) -> dict:
    """The existing renderer reads faces as x/y/w/h samples, not v2 boxes."""
    faces = []
    for track in analysis.get("tracks") or []:
        if track.get("kind") != "face":
            continue
        samples = []
        for sample in track.get("samples") or []:
            box = sample.get("bbox") or [0.4, 0.2, 0.2, 0.2]
            samples.append({
                "t": float(sample["t"]),
                "x": float(box[0]),
                "y": float(box[1]),
                "w": float(box[2]),
                "h": float(box[3]),
            })
        if samples:
            faces.append({"id": track["id"], "samples": samples, "screenTime": len(samples)})
    return {"source": analysis.get("source") or {}, "faces": faces}


def build_storyboard(analysis: dict, prompt: str) -> dict:
    duration = float((analysis.get("source") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Videons längd saknas. Ingen storyboard byggs.")
    blocked = _blocked_texts(analysis)
    phrases = _usable_phrases(analysis, blocked)
    smiles = _events(analysis, "smile")
    points = _events(analysis, "point")
    laughs = _events(analysis, "laughter")
    motions = _events(analysis, "motion")
    cuts = [float(t) for t in analysis.get("scenes") or []]

    hook_phrase = _best_phrase(phrases, smiles, cuts)
    if hook_phrase:
        hook_span = (float(hook_phrase["t0"]), float(hook_phrase["t1"]))
        hook_why = f"Fras {hook_phrase['id']} är det starkaste stödda ögonblicket."
    else:
        hook_span, hook_why = _visual_hook(duration, cuts, motions, smiles, laughs)
    hook_span = _clamp_span(*hook_span, duration)
    # The headline needs about a second on screen. Extend with real frames
    # around the moment instead of dropping the hook text.
    hook_span = _room_for_hook(hook_span, duration, 1.05)

    beats = [{
        "role": "HOOK",
        "sourceStart": round(hook_span[0], 3),
        "sourceEnd": round(hook_span[1], 3),
        "speed": 1.0,
        "zoomStart": 1.0,
        "zoomEnd": 1.35,
        "why": hook_why,
        "phraseId": hook_phrase["id"] if hook_phrase else None,
    }]
    if hook_span[0] > 0.45:
        for moment in _rewind_times(hook_span[0]):
            beats.append({
                "role": "REWIND",
                "sourceStart": round(moment, 3),
                "sourceEnd": round(min(duration, moment + 0.08), 3),
                "speed": 0.001,
                "holdSec": 0.11,
                "zoomStart": 1.12,
                "zoomEnd": 1.12,
                "why": "Kort tillbakaspolning mot början efter öppningen.",
                "phraseId": None,
            })

    story = []
    kept_phrases: list[dict] = []
    for phrase in phrases:
        if _is_filler(phrase["text"]):
            continue
        if _extends_another(phrase, kept_phrases):
            continue
        kept_phrases.append(phrase)
        span = _clamp_span(float(phrase["t0"]), float(phrase["t1"]), duration, limit=4.0)
        source_start = round(span[0], 3)
        source_end = round(span[1], 3)
        source_span = round(source_end - source_start, 3)
        if source_span < 0.2:
            continue
        story.append({
            "role": "STORY",
            "sourceStart": source_start,
            "sourceEnd": source_end,
            "speed": _readable_speed(source_span, min_readable_seconds(phrase["text"])),
            "zoomStart": 1.0,
            "zoomEnd": 1.06,
            "why": f"Hel fras {phrase['id']}.",
            "phraseId": phrase["id"],
            "text": phrase["text"],
            "status": "SUPPORTED",
        })
    if not story:
        for span, why in _visual_beats(duration, hook_span, cuts, motions, smiles):
            story.append({
                "role": "STORY",
                "sourceStart": round(span[0], 3),
                "sourceEnd": round(span[1], 3),
                "speed": 1.0,
                "zoomStart": 1.0,
                "zoomEnd": 1.08,
                "why": why,
                "phraseId": None,
            })
    beats.extend(story)

    smile = _strongest(smiles)
    if smile and float(smile["t1"]) - float(smile["t0"]) >= 0.35:
        span = _clamp_span(float(smile["t0"]), min(float(smile["t1"]), float(smile["t0"]) + 1.1), duration)
        beats.append({
            "role": "REPLAY",
            "sourceStart": round(span[0], 3),
            "sourceEnd": round(span[1], 3),
            "speed": 0.55,
            "zoomStart": 1.12,
            "zoomEnd": 1.28,
            "why": f"Slow motion på leende {smile['id']}.",
            "phraseId": None,
            "trackId": smile.get("trackId"),
            "evidenceId": smile["id"],
        })

    fields = _fields(prompt, hook_phrase, bool(smile or laughs), bool(phrases))
    note = _reach_target(beats, duration, analysis)
    limitations = list(analysis.get("limitations") or [])
    if note:
        limitations.append(note)
    seconds = round(_seconds(beats), 3)
    return {
        "prompt": prompt,
        "beats": beats,
        "fields": fields,
        "blockedTexts": sorted(blocked),
        "limitations": limitations,
        "length": {
            "target": MIN_SHORT_S,
            "seconds": seconds,
            "reached": seconds + 1e-3 >= MIN_SHORT_S,
            "reason": note,
        },
    }


def plan_from_storyboard(board: dict, analysis: dict) -> dict:
    plan = empty_plan()
    plan["audio"]["keepOriginal"] = True
    plan["audio"]["music"] = False
    plan["audio"]["decorate"] = False
    plan["title"] = board["fields"]["hook"][:70]
    clips = []
    for index, beat in enumerate(board["beats"], start=1):
        focus = beat.get("trackId") or _face_during(analysis, beat["sourceStart"], beat["sourceEnd"])
        clips.append({
            "id": f"c{index}",
            "sourceStart": beat["sourceStart"],
            "sourceEnd": beat["sourceEnd"],
            "speed": beat["speed"],
            "holdSec": beat.get("holdSec") or 0.12,
            "zoomStart": beat["zoomStart"],
            "zoomEnd": beat["zoomEnd"],
            "focusX": 0.5,
            "focusY": 0.42,
            "focusTrack": focus,
            "label": beat["role"].lower(),
            "reason": beat["why"],
        })
    plan["clips"] = clips
    rows, _ = timeline(plan)
    effects = []
    captions = []
    counter = 1

    def add_effect(effect: dict) -> None:
        nonlocal counter
        effect["id"] = f"e{counter}"
        counter += 1
        effects.append(effect)

    hook_row = next((row for row in rows if row["label"] == "hook"), None)
    fields = board["fields"]
    if hook_row:
        hook_end = min(float(hook_row["outEnd"]), float(hook_row["outStart"]) + 1.25)
        hook_end = max(hook_end, float(hook_row["outStart"]) + min_readable_seconds(fields["hook"]))
        hook_end = min(hook_end, float(hook_row["outEnd"]))
        if hook_end - float(hook_row["outStart"]) >= 0.8:
            add_effect({
                "type": "text",
                "kind": "headline",
                "start": 0.0,
                "end": round(hook_end, 3),
                "text": fields["hook"],
                "accent": fields["accent"],
                "fontSize": 120,
            })
            add_effect({
                "type": "badge",
                "start": 0.0,
                "end": round(hook_end, 3),
                "text": fields["accent"],
            })
        add_effect({"type": "sound_effect", "sfx": "impact", "time": 0.02, "gainDb": -8})

    rewind = next((row for row in rows if row["label"] == "rewind"), None)
    if rewind:
        add_effect({"type": "sound_effect", "sfx": "whoosh", "time": round(float(rewind["outStart"]), 3), "gainDb": -12})

    for row in rows:
        if row["label"] != "story":
            continue
        beat = board["beats"][int(row["id"][1:]) - 1]
        text = beat.get("text")
        if not text or beat.get("status") != "SUPPORTED":
            continue
        if _blocked(text, board["blockedTexts"]):
            continue
        start = float(row["outStart"])
        end = float(row["outEnd"])
        if end - start + 1e-3 < min_readable_seconds(text):
            continue
        add_effect({
            "type": "text",
            "kind": "story",
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "accent": "",
            "fontSize": 64,
        })
        captions.append({
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
            "source": "phrase",
            "status": "SUPPORTED",
            "phraseId": beat.get("phraseId"),
        })
        if not any(effect.get("sfx") == "pop" for effect in effects):
            add_effect({"type": "sound_effect", "sfx": "pop", "time": round(start, 3), "gainDb": -16})

    if fields.get("bubble") and not _blocked(fields["bubble"], board["blockedTexts"]):
        home = next(
            (row for row in rows if float(row["outEnd"]) - float(row["outStart"]) >= 0.9 and row["label"] != "rewind"),
            None,
        )
        if home and float(home["outEnd"]) - float(home["outStart"]) >= 0.9:
            add_effect({
                "type": "text",
                "kind": "bubble",
                "start": round(float(home["outStart"]), 3),
                "end": round(min(float(home["outEnd"]), float(home["outStart"]) + 1.1), 3),
                "text": fields["bubble"],
                "accent": "",
                "fontSize": 48,
            })

    replay = next((row for row in rows if row["label"] == "replay"), None)
    if replay:
        add_effect({
            "type": "sound_effect",
            "sfx": "riser",
            "time": round(max(0.0, float(replay["outStart"]) - 0.05), 3),
            "gainDb": -14,
        })
        if fields.get("emoji") and not lint_emoji(fields["emoji"]):
            add_effect({
                "type": "emoji",
                "emoji": fields["emoji"],
                "start": round(float(replay["outStart"]), 3),
                "end": round(float(replay["outEnd"]), 3),
                "track": replay.get("focusTrack"),
                "anchor": "above",
            })
        if replay.get("focusTrack"):
            add_effect({
                "type": "circle",
                "start": round(float(replay["outStart"]), 3),
                "end": round(float(replay["outEnd"]), 3),
                "track": replay.get("focusTrack"),
            })

    point = _strongest(_events(analysis, "point"))
    if point:
        placed = _place_on_rows(rows, float(point["t0"]), float(point["t1"]))
        face = _face_during(analysis, float(point["t0"]), float(point["t1"]))
        if placed and face:
            add_effect({
                "type": "arrow",
                "start": round(placed[0], 3),
                "end": round(placed[1], 3),
                "track": face,
                "anchor": "below",
            })

    plan["effects"] = effects
    plan["storyCaptions"] = captions
    length = board.get("length") or {}
    plan["export"] = describe_export(analysis.get("source") or {})
    plan["export"]["targetSeconds"] = MIN_SHORT_S
    plan["export"]["plannedSeconds"] = length.get("seconds")
    plan["export"]["lengthReached"] = bool(length.get("reached"))
    if length.get("reason"):
        plan["export"]["lengthLimit"] = length["reason"]
    _assert_plan_safe(plan, board["blockedTexts"])
    return plan


def qa_output(path: Path, plan: dict, analysis: dict) -> dict:
    data = probe(path)
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), {})
    duration = float((data.get("format") or {}).get("duration") or 0)
    rate = str(video.get("avg_frame_rate") or "0/1")
    fps = _fps(rate)
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    spec = describe_export(analysis.get("source") or {})
    limit = (plan.get("export") or {}).get("lengthLimit")
    if limit:
        check("duration", duration > 0.8, f"{duration:.3f}s; {limit}")
    else:
        check("duration", duration + 0.05 >= MIN_SHORT_S, f"{duration:.3f}s")
    check(
        "resolution",
        int(video.get("width") or 0) == spec["width"] and int(video.get("height") or 0) == spec["height"],
        f"{video.get('width')}x{video.get('height')}",
    )
    profile = str(video.get("profile") or "")
    check(
        "codec",
        video.get("codec_name") == "h264"
        and "high" in profile.lower()
        and video.get("pix_fmt") == "yuv420p"
        and audio.get("codec_name") == "aac",
        f"{video.get('codec_name')} {profile} {video.get('pix_fmt')}/{audio.get('codec_name')}",
    )
    check("fps", abs(fps - spec["fps"]) < 0.8, rate)
    comment = str(((data.get("format") or {}).get("tags") or {}).get("comment") or "")
    honest = comment == spec["quality"] and (spec["quality"] == "native" or "native" not in comment.split(","))
    check("quality_label", honest, f"{comment or 'saknas'} (källa {spec['quality']})")
    check("audio", bool(audio), "ljudspår finns" if audio else "ljudspår saknas")
    loud = _mean_volume(path)
    check("not_silent", loud is not None and loud > -45, f"mean {loud} dB" if loud is not None else "okänd")
    lufs = _integrated_lufs(path)
    check("lufs", lufs is not None and abs(lufs + 14) <= 1.6, f"{lufs} LUFS" if lufs is not None else "okänd")
    check("original_audio", plan.get("audio", {}).get("keepOriginal") is True and plan.get("audio", {}).get("music") is False, "keepOriginal, ingen musik")
    blocked = _blocked_texts(analysis)
    texts = _plan_texts(plan)
    leaked = [text for text in texts if _blocked(text, blocked)]
    check("no_rejected_transcript", not leaked, ", ".join(leaked) or "ingen avvisad text")
    late = []
    short = []
    for item in _timed_texts(plan):
        if item["start"] < -0.05 or item["end"] > duration + 0.2:
            late.append(item["text"])
        if item["end"] - item["start"] + 1e-3 < min_readable_seconds(item["text"]):
            short.append(item["text"])
    check("text_inside_duration", not late, ", ".join(late) or "inom längden")
    check("readable", not short, ", ".join(short) or "läsbara tider")
    return {"ok": all(item["ok"] for item in checks), "checks": checks, "duration": round(duration, 3), "lufs": lufs}


def _usable_phrases(analysis: dict, blocked: set[str]) -> list[dict]:
    try:
        rows = phrases_for_editplan(analysis)
    except Exception:
        rows = []
    clean = []
    for phrase in rows:
        text = str(phrase.get("text") or "").strip()
        if not text or phrase.get("status") not in (None, "SUPPORTED"):
            continue
        if _blocked(text, blocked):
            continue
        clean.append(phrase)
    return clean


def _blocked_texts(analysis: dict) -> set[str]:
    blocked = set()
    for row in analysis.get("transcript") or []:
        if row.get("status") == "SUPPORTED":
            continue
        text = _norm(row.get("text") or "")
        if text:
            blocked.add(text)
    return blocked


def _blocked(text: str, blocked: set[str]) -> bool:
    token = _norm(text)
    if not token:
        return False
    return any(token == item or (len(token) > 8 and token in item) or (len(item) > 8 and item in token) for item in blocked)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _events(analysis: dict, kind: str) -> list[dict]:
    return [event for event in analysis.get("evidence") or [] if event.get("type") == kind]


def _best_phrase(phrases: list[dict], smiles: list[dict], cuts: list[float]) -> dict | None:
    best = None
    best_score = -1.0
    for phrase in phrases:
        if _is_filler(phrase["text"]):
            continue
        score = 1.0 + min(2.0, float(phrase["t1"]) - float(phrase["t0"]))
        if _overlaps(phrase, smiles):
            score += 2.0
        if any(float(phrase["t0"]) < cut < float(phrase["t1"]) for cut in cuts):
            score += 0.6
        if float(phrase["t0"]) > 0.8:
            score += 0.4
        if _extends_another(phrase, phrases):
            score -= 3.0
        if score > best_score:
            best, best_score = phrase, score
    return best


def _visual_hook(duration, cuts, motions, smiles, laughs):
    smile = _strongest(smiles)
    if smile:
        return _clamp_span(float(smile["t0"]), float(smile["t1"]), duration), f"Leende {smile['id']} är öppningen."
    laugh = _strongest(laughs)
    if laugh:
        return _clamp_span(float(laugh["t0"]), min(float(laugh["t1"]), float(laugh["t0"]) + 1.2), duration), f"Skratt {laugh['id']} är öppningen."
    if cuts:
        cut = cuts[0]
        return _clamp_span(max(0.0, cut - 0.35), cut + 0.75, duration), f"Scenklipp vid {cut:.2f} s är öppningen."
    motion = _strongest(motions)
    if motion:
        return _clamp_span(float(motion["t0"]), float(motion["t1"]) + 0.4, duration), f"Rörelse {motion['id']} är öppningen."
    return (0.0, min(duration, 1.2)), "Öppningen är början av filen. Inget starkare ögonblick fanns."


def _visual_beats(duration, hook_span, cuts, motions, smiles):
    candidates = []
    for cut in cuts:
        span = _clamp_span(max(0.0, cut - 0.45), cut + 1.05, duration, minimum=1.5)
        if span[1] - span[0] >= 0.45:
            candidates.append((span, 2.0, f"Scenklipp vid {cut:.2f} s."))
    for motion in motions:
        span = _clamp_span(float(motion["t0"]) - 0.35, float(motion["t1"]) + 0.9, duration, minimum=1.5)
        if span[1] - span[0] >= 0.45:
            candidates.append((span, float(motion.get("confidence") or 0.4), f"Rörelse {motion['id']}."))
    for smile in smiles:
        span = _clamp_span(float(smile["t0"]), float(smile["t1"]), duration)
        candidates.append((span, 1.5 + float(smile.get("confidence") or 0), f"Leende {smile['id']}."))
    candidates.sort(key=lambda item: -item[1])
    chosen = []
    for span, _score, why in candidates:
        if _close(span, hook_span) or _near_center(span, hook_span, 1.5):
            continue
        if any(_close(span, have) or _near_center(span, have, 1.5) for have, _ in chosen):
            continue
        chosen.append((span, why))
        if len(chosen) >= 3:
            break
    if len(chosen) < 3 and duration > 3:
        tail = _clamp_span(duration - 1.6, duration, duration, minimum=1.2)
        if not _close(tail, hook_span) and not any(_close(tail, have) for have, _ in chosen):
            chosen.append((tail, "Slutet av filen, som avslutning."))
    if not chosen and duration > 1.5:
        chosen.append((_clamp_span(0.0, min(duration, 1.4), duration), "Början av filen, eftersom inget senare bildbevis valdes."))
    return chosen


def _room_for_hook(span: tuple[float, float], duration: float, minimum: float) -> tuple[float, float]:
    start, end = span
    if end - start >= minimum or duration < minimum:
        if duration < minimum:
            return 0.0, duration
        return start, end
    extra = minimum - (end - start)
    start = max(0.0, start - extra / 2)
    end = min(duration, start + minimum)
    start = max(0.0, end - minimum)
    return start, end


def _seconds(beats: list[dict]) -> float:
    return sum(_beat_seconds(beat) for beat in beats)


def _beat_seconds(beat: dict) -> float:
    speed = float(beat.get("speed") or 1)
    if speed <= 0.001:
        return float(beat.get("holdSec") or 0.11)
    return max(0.0, (float(beat["sourceEnd"]) - float(beat["sourceStart"])) / speed)


def _needs_more(beats: list[dict]) -> bool:
    return _seconds(beats) + 1e-3 < _AIM_S


def _reach_target(beats: list[dict], duration: float, analysis: dict) -> str | None:
    """Use real footage until the timeline can be a Short.

    A source shorter than 13s may be retold with a flash-forward hook (already
    chosen), one full chronological pass, and one slow-motion replay. Nothing
    here adds black, a freeze, or a second loop of the same device.
    """
    if duration <= 0 or not _needs_more(beats):
        return None
    if duration + 0.02 < MIN_SHORT_S:
        if not _has_full_pass(beats, duration):
            _insert_before_replay(beats, _chrono_beat(
                0.0,
                duration,
                "Hela klippet i tidsordning. Bara bilder som finns i källan.",
                duration,
            ))
        if _needs_more(beats):
            _add_slowmo(beats, duration, analysis, _AIM_S - _seconds(beats))
    else:
        _add_unused(beats, duration)
        if _needs_more(beats):
            need = _AIM_S - _seconds(beats)
            _insert_before_replay(beats, _chrono_beat(
                0.0,
                min(duration, max(need, 0.2)),
                "Kronologisk del av källan så shortsen når 13 sekunder.",
                duration,
            ))
        if _needs_more(beats):
            _add_slowmo(beats, duration, analysis, _AIM_S - _seconds(beats))
    if not _needs_more(beats):
        return None
    got = _seconds(beats)
    return (
        f"Källan är {duration:.2f} s. En hook av ett verkligt ögonblick, hela klippet i tidsordning "
        f"och en slow-motion-repris räcker till {got:.2f} s. "
        f"13 s skulle kräva svarta rutor, frysrutor eller fler loopar, och de läggs inte till."
    )


def _add_unused(beats: list[dict], duration: float) -> None:
    need = _AIM_S - _seconds(beats)
    extras = []
    for start, end in _gaps(_covered(beats), duration):
        if need <= 0.02:
            break
        if end - start < 0.2:
            continue
        take = min(end - start, need)
        extras.append(_chrono_beat(
            start,
            start + take,
            "Kronologiskt material från källan som urvalet hade hoppat över.",
            duration,
        ))
        need -= take
    _insert_before_replay(beats, *[beat for beat in extras if beat])


def _add_slowmo(beats: list[dict], duration: float, analysis: dict, need: float) -> None:
    if need <= 0.02:
        return
    hook = next((beat for beat in beats if beat.get("role") == "HOOK"), None)
    hook_span = (
        (float(hook["sourceStart"]), float(hook["sourceEnd"]))
        if hook else (0.0, min(duration, 1.2))
    )
    t0, t1, why = _reaction_span(analysis, hook_span)
    existing = next((beat for beat in beats if beat.get("role") == "REPLAY"), None)
    target_out = need + (_beat_seconds(existing) if existing else 0.0)
    event_len = max(0.2, t1 - t0)
    want = min(duration, max(event_len, target_out * _SLOW_SPEED, 0.45))
    center = (t0 + t1) / 2
    start = max(0.0, center - want / 2)
    end = min(duration, start + want)
    start = max(0.0, end - want)
    if end - start < 0.2:
        return
    speed = _SLOW_SPEED
    produced = (end - start) / speed
    if produced > target_out + 1.25 and target_out > 0:
        speed = min(0.75, max(_SLOW_SPEED, (end - start) / target_out))
        speed = math.floor(speed * 1000 + 1e-9) / 1000
        produced = (end - start) / speed
    if existing and produced <= _beat_seconds(existing) + 0.02:
        return
    beat = {
        "role": "REPLAY",
        "sourceStart": round(start, 3),
        "sourceEnd": round(end, 3),
        "speed": round(max(_SLOW_SPEED, speed), 3),
        "zoomStart": 1.12,
        "zoomEnd": 1.28,
        "why": why,
        "phraseId": None,
    }
    face = _face_during(analysis, start, end)
    if face:
        beat["trackId"] = face
    if existing:
        existing.clear()
        existing.update(beat)
        return
    beats.append(beat)


def _reaction_span(analysis: dict, hook_span: tuple[float, float]) -> tuple[float, float, str]:
    named = (
        ("smile", "leendet"),
        ("laughter", "skrattet"),
        ("mouth_open", "reaktionen"),
        ("point", "gesten"),
    )
    for kind, label in named:
        event = _strongest(_events(analysis, kind))
        if event and float(event["t1"]) - float(event["t0"]) >= 0.2:
            return float(event["t0"]), float(event["t1"]), f"Slow motion av {label} ({event['id']})."
    motion = _strongest(_events(analysis, "motion"))
    if motion and float(motion["t1"]) - float(motion["t0"]) >= 0.2:
        return float(motion["t0"]), float(motion["t1"]), f"Slow motion av rörelsen {motion['id']}."
    return hook_span[0], hook_span[1], "Slow motion av det starkaste ögonblicket i källan."


def _chrono_beat(start: float, end: float, why: str, duration: float) -> dict | None:
    start = max(0.0, min(start, duration))
    end = max(start, min(end, duration))
    if end - start < 0.2:
        return None
    return {
        "role": "STORY",
        "sourceStart": round(start, 3),
        "sourceEnd": round(end, 3),
        "speed": 1.0,
        "zoomStart": 1.0,
        "zoomEnd": 1.06,
        "why": why,
        "phraseId": None,
    }


def _insert_before_replay(beats: list[dict], *extra: dict | None) -> None:
    rows = [beat for beat in extra if beat]
    if not rows:
        return
    for index, beat in enumerate(beats):
        if beat.get("role") == "REPLAY":
            beats[index:index] = rows
            return
    beats.extend(rows)


def _has_full_pass(beats: list[dict], duration: float) -> bool:
    for beat in beats:
        if beat.get("role") == "REPLAY" or float(beat.get("speed") or 1) <= 0.001:
            continue
        if abs(float(beat.get("speed") or 1) - 1) > 0.08:
            continue
        if float(beat["sourceStart"]) <= 0.08 and float(beat["sourceEnd"]) >= duration - 0.08:
            return True
    return False


def _covered(beats: list[dict]) -> list[tuple[float, float]]:
    spans = []
    for beat in beats:
        if float(beat.get("speed") or 1) <= 0.001:
            continue
        spans.append((float(beat["sourceStart"]), float(beat["sourceEnd"])))
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _gaps(spans: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    cursor = 0.0
    gaps = []
    for start, end in spans:
        if start > cursor + 0.05:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 0.05:
        gaps.append((cursor, duration))
    return gaps


def _fields(prompt: str, phrase: dict | None, reaction: bool, has_speech: bool) -> dict:
    hook, accent = _hook_from_phrase(phrase) if phrase else (_HOOK_FALLBACK, _HOOK_FALLBACK)
    emoji = "😳" if reaction else ""
    bubble = "" if has_speech else "wait"
    fallback = {"hook": hook, "accent": accent, "emoji": emoji, "bubble": bubble, "llm": False}
    if lint_text(hook) or lint_text(accent) or (emoji and lint_emoji(emoji)) or (bubble and lint_text(bubble)):
        fallback = {"hook": _HOOK_FALLBACK, "accent": _HOOK_FALLBACK, "emoji": "", "bubble": "", "llm": False}
    choice = _ask_model(prompt, fallback)
    return _accept_fields(choice, fallback, reaction, has_speech)


def _hook_from_phrase(phrase: dict) -> tuple[str, str]:
    words = [re.sub(r"[^A-Za-z0-9']", "", word) for word in str(phrase["text"]).split()]
    words = [word for word in words if word]
    if not words:
        return _HOOK_FALLBACK, _HOOK_FALLBACK
    accent = max(words, key=len).upper()[:12]
    if len(accent) < 3:
        accent = words[-1].upper()[:12] or _HOOK_FALLBACK
    index = next((i for i, word in enumerate(words) if word.upper()[:12] == accent), len(words) - 1)
    if index > 0:
        hook = f"{words[index - 1].upper()[:12]} {accent}"
        if len(hook) > 18:
            hook = accent
    else:
        hook = accent
    return hook, accent


def _ask_model(prompt: str, fallback: dict) -> dict | None:
    body = {
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return one JSON object. Keys: hook, accent, emoji, bubble. "
                    "hook must equal the fallback hook. accent must equal the fallback accent. "
                    "emoji must be the fallback emoji or an empty string. "
                    "bubble must be the fallback bubble or an empty string. No other words."
                ),
            },
            {"role": "user", "content": json.dumps({"prompt": prompt[:240], "fallback": fallback}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        OPENAI_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode())
        content = payload["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(match.group(0) if match else content)
    except Exception:
        return None


def _accept_fields(choice: dict | None, fallback: dict, reaction: bool, has_speech: bool) -> dict:
    fields = dict(fallback)
    if not isinstance(choice, dict):
        return fields
    hook = str(choice.get("hook") or "").strip().upper()
    accent = str(choice.get("accent") or "").strip().upper()
    # A reply that does not repeat the chosen hook is not a constrained answer.
    # qwen2.5:3b often echoes the user JSON, and missing keys must not wipe the fallback.
    if hook != fallback["hook"] or accent != fallback["accent"] or lint_text(hook):
        return fields
    fields["llm"] = True
    emoji = str(choice.get("emoji") or "").strip()
    if reaction and emoji in ("😳", "😅", "‼️", "🫣") and not lint_emoji(emoji):
        fields["emoji"] = emoji
    bubble = str(choice.get("bubble") or "").strip().lower()
    if not has_speech and bubble == "wait" and not lint_text(bubble):
        fields["bubble"] = bubble
    if has_speech:
        fields["bubble"] = ""
    return fields


def _assert_plan_safe(plan: dict, blocked: set[str]) -> None:
    for text in _plan_texts(plan):
        if _blocked(text, blocked):
            raise RuntimeError(f"Avvisad transkripttext kom in i planen: {text}")
        problem = lint_text(text)
        if problem:
            raise RuntimeError(problem)
    if plan["audio"].get("music"):
        raise RuntimeError("Planen fick inte musik.")
    if not plan["audio"].get("keepOriginal", True):
        raise RuntimeError("Originalljudet måste vara på.")


def _plan_texts(plan: dict) -> list[str]:
    texts = [item["text"] for item in _timed_texts(plan)]
    texts += [item.get("text") or "" for item in plan.get("storyCaptions") or []]
    return [text for text in texts if text]


def _timed_texts(plan: dict) -> list[dict]:
    rows = []
    for effect in plan.get("effects") or []:
        if effect.get("type") in ("text", "badge") and effect.get("text"):
            rows.append({"text": effect["text"], "start": float(effect["start"]), "end": float(effect["end"])})
    for caption in plan.get("captions") or []:
        if caption.get("text"):
            rows.append({"text": caption["text"], "start": float(caption["start"]), "end": float(caption["end"])})
    return rows


def _face_during(analysis: dict, t0: float, t1: float) -> str | None:
    best = None
    best_n = 0
    for track in analysis.get("tracks") or []:
        if track.get("kind") != "face":
            continue
        count = sum(1 for sample in track.get("samples") or [] if t0 - 0.3 <= float(sample["t"]) <= t1 + 0.3)
        if count > best_n:
            best, best_n = track["id"], count
    return best


def _place_on_rows(rows: list[dict], t0: float, t1: float) -> tuple[float, float] | None:
    for row in rows:
        if row["label"] == "rewind" or float(row["speed"]) <= 0.001:
            continue
        if float(row["sourceEnd"]) <= t0 or float(row["sourceStart"]) >= t1:
            continue
        start = max(t0, float(row["sourceStart"]))
        end = min(t1, float(row["sourceEnd"]))
        out0 = _map_time(row, start)
        out1 = _map_time(row, end)
        if out1 - out0 >= 0.35:
            return out0, out1
    return None


def _map_time(row: dict, src_t: float) -> float:
    span = float(row["sourceEnd"]) - float(row["sourceStart"])
    if span <= 0.001 or float(row["speed"]) <= 0.001:
        return float(row["outStart"])
    u = min(1.0, max(0.0, (src_t - float(row["sourceStart"])) / span))
    return float(row["outStart"]) + (float(row["outEnd"]) - float(row["outStart"])) * u


def _rewind_times(hook_start: float) -> list[float]:
    times = []
    moment = hook_start * 0.72
    for _ in range(4):
        if moment < 0.12:
            break
        times.append(moment)
        moment *= 0.55
    return times


def _extends_another(phrase: dict, phrases: list[dict]) -> bool:
    text = str(phrase.get("text") or "").strip().lower()
    if not text:
        return False
    for other in phrases:
        if other is phrase or other.get("phraseId") == phrase.get("id"):
            continue
        other_text = str(other.get("text") or "").strip().lower()
        if not other_text or other_text == text:
            continue
        if text.startswith(other_text) and float(phrase["t0"]) <= float(other.get("t1", phrase["t0"])) + 0.3:
            return True
    return False


def _near_center(a: tuple[float, float], b: tuple[float, float], gap: float) -> bool:
    return abs(((a[0] + a[1]) / 2) - ((b[0] + b[1]) / 2)) < gap


def _is_filler(text: str) -> bool:
    words = [re.sub(r"[^a-z]", "", word.lower()) for word in str(text).split()]
    words = [word for word in words if word]
    return bool(words) and all(word in _FILLER for word in words)


def _overlaps(phrase: dict, events: list[dict]) -> bool:
    return any(float(phrase["t0"]) < float(event["t1"]) and float(phrase["t1"]) > float(event["t0"]) for event in events)


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] - 0.15 and a[1] > b[0] + 0.15


def _readable_speed(source_span: float, need: float) -> float:
    """Speed that keeps a caption readable after it is stored to 3 decimals.

    Rounding 0.72857 up to 0.729 turned a 1.40s line into 1.399s, and the
    readable check then failed by a fraction of a millisecond. Aim past the
    minimum and floor the speed so the clip only gets longer.
    """
    target = need + 0.12
    if source_span <= 0:
        return 1.0
    speed = 1.12
    if source_span < target * speed:
        speed = source_span / target
    speed = min(1.12, max(0.5, speed))
    floored = math.floor(speed * 1000 + 1e-9) / 1000
    return round(min(1.12, max(0.5, floored)), 3)


def _clamp_span(t0: float, t1: float, duration: float, minimum: float = 0.25, limit: float = 3.2) -> tuple[float, float]:
    start = min(max(0.0, t0), max(0.0, duration - minimum))
    end = min(duration, max(start + minimum, t1))
    if end - start > limit:
        end = start + limit
    return start, end


def _strongest(events: list[dict]) -> dict | None:
    if not events:
        return None
    return max(events, key=lambda event: float(event.get("confidence") or 0))


def _fps(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/", 1)
        den_f = float(den or 1)
        return float(num) / den_f if den_f else 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _mean_volume(path: Path) -> float | None:
    result = run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        "volym",
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr or "")
    return float(match.group(1)) if match else None


def _integrated_lufs(path: Path) -> float | None:
    result = run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        "loudnorm-mätning",
    )
    match = re.search(r"\{[^{}]*input_i[^{}]*\}", result.stderr or "", re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0)).get("input_i")
        return None if value in (None, "-inf") else float(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
