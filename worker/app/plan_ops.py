"""Edit-plan mutations. The model may only change the plan through these functions."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from .safety import lint_emoji, lint_text

MAX_SHORT = 60.0


def empty_plan() -> dict[str, Any]:
    return {
        "revision": 0,
        "title": "",
        "clips": [],
        "captions": [],
        "effects": [],
        "style": {
            "captionFontSize": 72,
            "headlineFontSize": 120,
            "accentColor": "#FF2D2D",
            "fillColor": "#FFFFFF",
            "strokeColor": "#0A0A0A",
            "safeTop": 220,
            "safeBottom": 320,
            "safeSide": 60,
        },
        "audio": {"keepOriginal": True, "music": False, "targetLufs": -14},
    }


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def clip_duration(clip: dict) -> float:
    if float(clip.get("speed") or 1) <= 0.001:
        return float(clip.get("holdSec") or 0.3)
    return max(0.0, (float(clip["sourceEnd"]) - float(clip["sourceStart"])) / float(clip["speed"]))


def timeline(plan: dict) -> tuple[list[dict], float]:
    rows: list[dict] = []
    cursor = 0.0
    for clip in plan["clips"]:
        dur = clip_duration(clip)
        row = dict(clip)
        row["outStart"] = cursor
        row["outEnd"] = cursor + dur
        rows.append(row)
        cursor += dur
    return rows, cursor


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("outStart", "outEnd")}


class Editor:
    def __init__(self, analysis: dict | None = None, plan: dict | None = None):
        self.analysis = analysis
        self.plan = plan or empty_plan()
        self.undo_stack: list[dict] = []
        self.versions: list[dict] = []
        self.active: int | None = None
        self.dirty = False

    def snapshot(self) -> None:
        self.undo_stack.append(copy.deepcopy(self.plan))
        self.undo_stack = self.undo_stack[-40:]
        self.dirty = True
        self.plan["revision"] = int(self.plan.get("revision") or 0) + 1

    def _duration(self) -> float:
        if not self.analysis:
            return 0.0
        return float(self.analysis["source"]["duration"])

    def _require_analysis(self) -> dict | None:
        if not self.analysis:
            return {"ok": False, "error": "Analys saknas. Anropa analyze_video först."}
        return None

    def select_clip(
        self,
        source_start: float,
        source_end: float,
        speed: float = 1,
        label: str = "",
        insert_at: int | None = None,
    ) -> dict:
        err = self._require_analysis()
        if err:
            return err
        duration = self._duration()
        start, end = _as_seconds(source_start, source_end, duration)
        start = max(0.0, start)
        end = min(duration, end)
        if end - start < 0.2:
            return {"ok": False, "error": "Klippet måste vara minst 0,2 sekunder."}
        speed = min(2.0, max(0.5, float(speed or 1)))
        clip = {
            "id": _id("c"),
            "sourceStart": round(start, 3),
            "sourceEnd": round(end, 3),
            "speed": speed,
            "zoomStart": 1.0,
            "zoomEnd": 1.0,
            "focusX": 0.5,
            "focusY": 0.45,
            "focusTrack": _primary_track(self.analysis),
            "label": (label or "")[:80],
        }
        self.snapshot()
        if insert_at is None or insert_at >= len(self.plan["clips"]):
            self.plan["clips"].append(clip)
        else:
            self.plan["clips"].insert(max(0, insert_at), clip)
        _, total = timeline(self.plan)
        if total > MAX_SHORT:
            self.plan = self.undo_stack.pop()
            self.dirty = bool(self.undo_stack)
            return {"ok": False, "error": f"Tidslinjen blir {total:.1f}s. Shorts här max {MAX_SHORT:.0f}s."}
        return {"ok": True, "clip": clip, "outputDuration": round(total, 2)}

    def trim_clip(self, clip_id: str, source_start: float | None = None, source_end: float | None = None) -> dict:
        clip = self._clip(clip_id)
        if not clip:
            return {"ok": False, "error": f"Okänt klipp {clip_id}."}
        duration = self._duration() or float(clip["sourceEnd"])
        start = float(clip["sourceStart"] if source_start is None else source_start)
        end = float(clip["sourceEnd"] if source_end is None else source_end)
        start, end = _as_seconds(start, end, duration)
        start = max(0.0, start)
        end = min(duration, end)
        if end - start < 0.2:
            return {"ok": False, "error": "Klippet skulle bli kortare än 0,2s."}
        self.snapshot()
        clip["sourceStart"] = round(start, 3)
        clip["sourceEnd"] = round(end, 3)
        return {"ok": True, "clip": clip, "outputDuration": round(timeline(self.plan)[1], 2)}

    def delete_timeline_range(self, start: float, end: float) -> dict:
        start, end = float(start), float(end)
        if end <= start:
            return {"ok": False, "error": "Sluttiden måste vara efter starttiden."}
        rows, _ = timeline(self.plan)
        if not rows:
            return {"ok": False, "error": "Tidslinjen är tom."}
        kept: list[dict] = []
        for row in rows:
            os_, oe = row["outStart"], row["outEnd"]
            if oe <= start or os_ >= end:
                kept.append(_strip(row))
                continue
            if start <= os_ and end >= oe:
                continue
            speed = float(row["speed"])
            if speed <= 0.001:
                continue

            def to_src(t: float) -> float:
                return float(row["sourceStart"]) + (t - os_) * speed

            if start > os_ + 0.05:
                left = _strip(row)
                left["sourceEnd"] = round(min(float(row["sourceEnd"]), to_src(start)), 3)
                if left["sourceEnd"] - left["sourceStart"] >= 0.2:
                    kept.append(left)
            if end < oe - 0.05:
                right = _strip(row)
                right["id"] = _id("c")
                right["sourceStart"] = round(max(float(row["sourceStart"]), to_src(end)), 3)
                if right["sourceEnd"] - right["sourceStart"] >= 0.2:
                    kept.append(right)
        if not kept:
            return {"ok": False, "error": "Det skulle ta bort hela filmen. Lämna minst ett klipp."}
        self.snapshot()
        self.plan["clips"] = kept
        self._shift_overlays(start, end)
        return {"ok": True, "outputDuration": round(timeline(self.plan)[1], 2), "clips": len(kept)}

    def _shift_overlays(self, start: float, end: float) -> None:
        cut = end - start

        def shift_span(item: dict, a: str, b: str) -> dict | None:
            s, e = float(item[a]), float(item[b])
            if e <= start:
                return item
            if s >= end:
                item[a] = round(s - cut, 3)
                item[b] = round(e - cut, 3)
                return item
            if s >= start and e <= end:
                return None
            if s < start < e:
                item[b] = round(start, 3)
                return item if item[b] - item[a] >= 0.2 else None
            return item

        captions = []
        for cap in self.plan["captions"]:
            nxt = shift_span(cap, "start", "end")
            if nxt:
                captions.append(nxt)
        self.plan["captions"] = captions
        effects = []
        for effect in self.plan["effects"]:
            if effect["type"] == "sound_effect":
                t = float(effect["time"])
                if start <= t < end:
                    continue
                if t >= end:
                    effect["time"] = round(t - cut, 3)
                effects.append(effect)
            else:
                nxt = shift_span(effect, "start", "end")
                if nxt:
                    effects.append(nxt)
        self.plan["effects"] = effects

    def reorder_clips(self, order: list[str]) -> dict:
        by_id = {c["id"]: c for c in self.plan["clips"]}
        if set(order) != set(by_id) or len(order) != len(by_id):
            return {"ok": False, "error": "order måste innehålla varje klipp-id exakt en gång."}
        self.snapshot()
        self.plan["clips"] = [by_id[i] for i in order]
        return {"ok": True, "order": order}

    def change_speed(self, clip_id: str, speed: float, hold_sec: float | None = None) -> dict:
        clip = self._clip(clip_id)
        if not clip:
            return {"ok": False, "error": f"Okänt klipp {clip_id}."}
        speed = float(speed)
        self.snapshot()
        if speed <= 0.001:
            clip["speed"] = 0
            clip["holdSec"] = min(1.5, max(0.2, float(hold_sec or 0.3)))
        else:
            clip["speed"] = min(2.0, max(0.5, speed))
            clip.pop("holdSec", None)
        return {"ok": True, "clip": clip, "outputDuration": round(timeline(self.plan)[1], 2)}

    def set_zoom(
        self,
        clip_id: str | None = None,
        zoom_start: float | None = None,
        zoom_end: float | None = None,
        when_text: str | None = None,
    ) -> dict:
        clip = self._clip_for(clip_id, when_text)
        if isinstance(clip, dict) and clip.get("ok") is False:
            src_t = find_spoken_time(self.analysis, when_text or "")
            if when_text and src_t is not None and self.plan["clips"]:
                clip = min(
                    self.plan["clips"],
                    key=lambda item: min(abs(src_t - float(item["sourceStart"])), abs(src_t - float(item["sourceEnd"]))),
                )
            else:
                return clip
        assert isinstance(clip, dict)
        self.snapshot()
        if when_text:
            self._cover_spoken_word(clip, when_text)
        if zoom_start is None and zoom_end is None:
            zs, ze = 1.55, 1.25
        else:
            zs = float(clip["zoomStart"] if zoom_start is None else zoom_start)
            ze = float(zs if zoom_end is None else zoom_end)
        zs = min(2.3, max(1.0, zs))
        ze = min(2.3, max(1.0, ze))
        clip["zoomStart"] = round(zs, 3)
        clip["zoomEnd"] = round(ze, 3)
        return {"ok": True, "clipId": clip["id"], "zoomStart": clip["zoomStart"], "zoomEnd": clip["zoomEnd"], "sourceStart": clip["sourceStart"], "sourceEnd": clip["sourceEnd"]}

    def set_focus(
        self,
        clip_id: str | None = None,
        focus_x: float | None = None,
        focus_y: float | None = None,
        track: str | None = None,
        when_text: str | None = None,
    ) -> dict:
        clip = self._clip_for(clip_id, when_text)
        if isinstance(clip, dict) and clip.get("ok") is False:
            return clip
        assert isinstance(clip, dict)
        self.snapshot()
        if track:
            known = {t["id"] for t in (self.analysis or {}).get("faces") or []}
            if track not in known:
                self.plan = self.undo_stack.pop()
                return {"ok": False, "error": f"Okänt ansiktsspår {track}. Tillgängliga: {sorted(known)}"}
            clip["focusTrack"] = track
        if focus_x is not None:
            clip["focusX"] = min(1.0, max(0.0, float(focus_x)))
        if focus_y is not None:
            clip["focusY"] = min(1.0, max(0.0, float(focus_y)))
        return {"ok": True, "clipId": clip["id"], "focusTrack": clip.get("focusTrack"), "focusX": clip["focusX"], "focusY": clip["focusY"]}

    def add_caption(self, start: float, end: float, text: str, accent: str | None = None, font_size: int | None = None) -> dict:
        problem = lint_text(text)
        if problem:
            return {"ok": False, "error": problem}
        if end <= start:
            return {"ok": False, "error": "Caption måste ha en längd."}
        cap = {
            "id": _id("cap"),
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "text": text.strip()[:80],
            "accent": (accent or _accent_word(text)).strip()[:40],
            "fontSize": int(font_size or self.plan["style"]["captionFontSize"]),
        }
        self.snapshot()
        self.plan["captions"].append(cap)
        return {"ok": True, "caption": cap}

    def replace_captions(self, captions: list[dict] | None = None, font_size: int | None = None, scale: float | None = None) -> dict:
        self.snapshot()
        if scale:
            factor = min(2.0, max(0.6, float(scale)))
            old_caption = int(self.plan["style"]["captionFontSize"])
            self.plan["style"]["captionFontSize"] = int(old_caption * factor)
            self.plan["style"]["headlineFontSize"] = int(self.plan["style"]["headlineFontSize"] * factor)
            for cap in self.plan["captions"]:
                cap["fontSize"] = int((cap.get("fontSize") or old_caption) * factor)
            for effect in self.plan["effects"]:
                if effect["type"] in ("text", "badge") and effect.get("fontSize"):
                    effect["fontSize"] = int(effect["fontSize"] * factor)
        if font_size:
            size = min(160, max(40, int(font_size)))
            self.plan["style"]["captionFontSize"] = size
            for cap in self.plan["captions"]:
                cap["fontSize"] = size
        if captions is not None:
            clean = []
            for raw in captions:
                text = str(raw.get("text") or "").strip()
                problem = lint_text(text)
                if problem:
                    self.plan = self.undo_stack.pop()
                    return {"ok": False, "error": problem}
                if not text:
                    continue
                start = float(raw.get("start") or 0)
                end = float(raw.get("end") or start + 0.8)
                clean.append({
                    "id": _id("cap"),
                    "start": round(start, 3),
                    "end": round(max(end, start + 0.35), 3),
                    "text": text[:80],
                    "accent": str(raw.get("accent") or _accent_word(text))[:40],
                    "fontSize": int(raw.get("fontSize") or self.plan["style"]["captionFontSize"]),
                })
            self.plan["captions"] = clean
        return {
            "ok": True,
            "captionFontSize": self.plan["style"]["captionFontSize"],
            "headlineFontSize": self.plan["style"]["headlineFontSize"],
            "captions": len(self.plan["captions"]),
        }

    def add_text(self, start: float, end: float, text: str, accent: str | None = None, kind: str = "headline", font_size: int | None = None) -> dict:
        problem = lint_text(text)
        if problem:
            return {"ok": False, "error": problem}
        kind = kind if kind in ("headline", "story", "bubble") else "headline"
        effect = {
            "id": _id("e"),
            "type": "text",
            "kind": kind,
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "text": text.strip()[:80],
            "accent": (accent or _accent_word(text)).strip()[:40],
            "fontSize": int(font_size or (self.plan["style"]["headlineFontSize"] if kind == "headline" else 64)),
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        if kind == "headline" and not self.plan["title"]:
            self.plan["title"] = effect["text"][:70]
        return {"ok": True, "effect": effect}

    def add_emoji(self, start: float, end: float, emoji: str, track: str | None = None, anchor: str = "above") -> dict:
        problem = lint_emoji(emoji)
        if problem:
            return {"ok": False, "error": problem}
        effect = {
            "id": _id("e"),
            "type": "emoji",
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "emoji": emoji,
            "track": track or _primary_track(self.analysis),
            "anchor": anchor if anchor in ("above", "left", "right") else "above",
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        return {"ok": True, "effect": effect}

    def add_arrow(self, start: float, end: float, track: str | None = None, anchor: str = "below") -> dict:
        effect = {
            "id": _id("e"),
            "type": "arrow",
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "track": track or _primary_track(self.analysis),
            "anchor": anchor if anchor in ("above", "below", "left", "right") else "below",
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        return {"ok": True, "effect": effect}

    def add_circle(self, start: float, end: float, track: str | None = None) -> dict:
        effect = {
            "id": _id("e"),
            "type": "circle",
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "track": track or _primary_track(self.analysis),
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        return {"ok": True, "effect": effect}

    def add_badge(self, start: float, end: float, text: str) -> dict:
        problem = lint_text(text)
        if problem:
            return {"ok": False, "error": problem}
        effect = {
            "id": _id("e"),
            "type": "badge",
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "text": text.strip()[:24],
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        return {"ok": True, "effect": effect}

    def add_sound_effect(self, time: float, sfx: str, gain_db: float | None = None) -> dict:
        kind = sfx if sfx in ("whoosh", "pop", "impact", "riser", "thump", "bass_hit") else ""
        if not kind:
            return {"ok": False, "error": "sfx måste vara whoosh, pop, impact, riser, thump eller bass_hit."}
        if kind == "bass_hit":
            kind = "impact"
        effect = {
            "id": _id("e"),
            "type": "sound_effect",
            "time": round(max(0.0, float(time)), 3),
            "sfx": kind,
            "gainDb": float(gain_db if gain_db is not None else _default_gain(kind)),
        }
        self.snapshot()
        self.plan["effects"].append(effect)
        return {"ok": True, "effect": effect}

    def remove_effect(self, effect_id: str) -> dict:
        before_c = len(self.plan["captions"])
        before_e = len(self.plan["effects"])
        captions = [c for c in self.plan["captions"] if c["id"] != effect_id]
        effects = [e for e in self.plan["effects"] if e["id"] != effect_id]
        if len(captions) == before_c and len(effects) == before_e:
            return {"ok": False, "error": f"Hittade inte {effect_id}."}
        self.snapshot()
        self.plan["captions"] = captions
        self.plan["effects"] = effects
        return {"ok": True, "removed": effect_id}

    def undo(self) -> dict:
        if self.active and self.dirty:
            saved = next(v for v in self.versions if v["version"] == self.active)
            self.plan = copy.deepcopy(saved["plan"])
            self.dirty = False
            return {"ok": True, "activeVersion": self.active, "message": f"Ocommitade ändringar är borta. Aktiv version är v{self.active}."}
        if self.active and self.active > 1:
            prev = self.active - 1
            saved = next(v for v in self.versions if v["version"] == prev)
            self.active = prev
            self.plan = copy.deepcopy(saved["plan"])
            self.dirty = False
            self.undo_stack.clear()
            return {"ok": True, "activeVersion": prev, "message": f"Tillbaka till v{prev}. Filmen för den versionen finns kvar."}
        if self.undo_stack:
            self.plan = self.undo_stack.pop()
            self.dirty = bool(self.undo_stack) or self.active is not None
            return {"ok": True, "message": "Ångrade senaste ändringen i planen."}
        return {"ok": False, "error": "Det finns ingen tidigare version att ångra."}

    def _cover_spoken_word(self, clip: dict, when_text: str) -> None:
        src_t = find_spoken_time(self.analysis, when_text)
        if src_t is None:
            return
        duration = self._duration() or src_t
        if float(clip["sourceStart"]) - 0.05 <= src_t < float(clip["sourceEnd"]) + 0.05:
            return
        clip["sourceStart"] = round(min(float(clip["sourceStart"]), max(0.0, src_t - 0.25)), 3)
        clip["sourceEnd"] = round(max(float(clip["sourceEnd"]), min(duration, src_t + 0.25)), 3)

    def _clip(self, clip_id: str | None) -> dict | None:
        if not clip_id:
            return None
        return next((c for c in self.plan["clips"] if c["id"] == clip_id), None)

    def _clip_for(self, clip_id: str | None, when_text: str | None) -> dict:
        if clip_id:
            clip = self._clip(clip_id)
            return clip or {"ok": False, "error": f"Okänt klipp {clip_id}."}
        if when_text:
            src_t = find_spoken_time(self.analysis, when_text)
            if src_t is None:
                return {"ok": False, "error": f"Hittade inte '{when_text}' i transkriptet. Anropa get_transcript."}
            for clip in self.plan["clips"]:
                if float(clip["sourceStart"]) - 0.05 <= src_t < float(clip["sourceEnd"]) + 0.05:
                    return clip
            return {"ok": False, "error": f"'{when_text}' ligger i källan vid {src_t:.2f}s men är inte med i tidslinjen. Lägg till det med select_clip."}
        if len(self.plan["clips"]) == 1:
            return self.plan["clips"][0]
        return {"ok": False, "error": "Ange clipId eller whenText."}

    def public_plan(self) -> dict:
        plan = copy.deepcopy(self.plan)
        _, total = timeline(plan)
        plan["outputDuration"] = round(total, 3)
        return plan


def _primary_track(analysis: dict | None) -> str | None:
    faces = (analysis or {}).get("faces") or []
    if not faces:
        return None
    return max(faces, key=lambda f: f.get("screenTime", 0))["id"]


def _accent_word(text: str) -> str:
    words = [w for w in text.replace("*", "").split() if w]
    if not words:
        return text
    return max(words, key=len)


def _default_gain(kind: str) -> float:
    return {"whoosh": -11, "pop": -10, "impact": -6, "riser": -6, "thump": -10}[kind]


def proposed_spans(analysis: dict | None) -> list[tuple[float, float]]:
    """Speech- or moment-sized ranges. Not a fixed cut every N seconds."""
    if not analysis:
        return []
    duration = float((analysis.get("source") or {}).get("duration") or 0)
    if duration < 0.2:
        return []
    words = ((analysis.get("transcript") or {}).get("words") or [])
    spans: list[tuple[float, float]] = []
    if words:
        start = float(words[0]["start"])
        end = float(words[0]["end"])
        for word in words[1:]:
            if float(word["start"]) - end > 0.55:
                spans.append((max(0.0, start - 0.05), min(duration, end + 0.08)))
                start, end = float(word["start"]), float(word["end"])
            else:
                end = max(end, float(word["end"]))
        spans.append((max(0.0, start - 0.05), min(duration, end + 0.12)))
        spans = [(round(a, 3), round(b, 3)) for a, b in spans if b - a >= 0.2]
    if not spans:
        moments = [m for m in (analysis.get("moments") or []) if "silence" not in (m.get("reasons") or [])]
        if moments:
            best = max(moments, key=lambda item: float(item.get("score") or 0))
            a = max(0.0, float(best["start"]) - 0.15)
            b = min(duration, max(float(best["end"]) + 0.8, a + 0.8))
            spans = [(round(a, 3), round(b, 3))]
        else:
            spans = [(0.0, round(min(duration, 8.0), 3))]
    moments = analysis.get("moments") or []
    if moments and len(spans) > 1:
        hook = (float(moments[0]["start"]) + float(moments[0]["end"])) / 2
        for index, (a, b) in enumerate(spans):
            if a <= hook < b and index:
                spans.insert(0, spans.pop(index))
                break
    return spans


def _as_seconds(start: float, end: float, duration: float) -> tuple[float, float]:
    """Models often send milliseconds (1000 meaning 1.0s)."""
    start, end = float(start), float(end)
    if duration > 0 and max(start, end) >= 100 and max(start, end) > duration * 1.5:
        start /= 1000.0
        end /= 1000.0
    return start, end


def find_spoken_time(analysis: dict | None, text: str) -> float | None:
    words = ((analysis or {}).get("transcript") or {}).get("words") or []
    needle = _norm(text)
    if not needle:
        return None
    if " " in needle:
        tokens = needle.split()
        values = [_norm(w["text"]) for w in words]
        for i in range(len(values) - len(tokens) + 1):
            if values[i : i + len(tokens)] == tokens:
                return (float(words[i]["start"]) + float(words[i + len(tokens) - 1]["end"])) / 2
        return None
    for word in words:
        if needle == _norm(word["text"]) or needle in _norm(word["text"]):
            return (float(word["start"]) + float(word["end"])) / 2
    return None


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace()).strip()


def map_words(plan: dict, words: list[dict]) -> list[dict]:
    mapped: list[dict] = []
    rows, _ = timeline(plan)
    for row in rows:
        speed = float(row["speed"])
        if speed <= 0.001:
            continue
        for word in words:
            mid = (float(word["start"]) + float(word["end"])) / 2
            if mid < float(row["sourceStart"]) or mid >= float(row["sourceEnd"]):
                continue

            def conv(t: float) -> float:
                t = min(max(t, float(row["sourceStart"])), float(row["sourceEnd"]))
                return float(row["outStart"]) + (t - float(row["sourceStart"])) / speed

            mapped.append({"text": word["text"], "start": conv(float(word["start"])), "end": conv(float(word["end"]))})
    mapped.sort(key=lambda w: w["start"])
    return mapped


def captions_from_words(plan: dict, analysis: dict) -> list[dict]:
    words = map_words(plan, (analysis.get("transcript") or {}).get("words") or [])
    if not words:
        return []
    groups: list[list[dict]] = []
    buf: list[dict] = []
    for word in words:
        if buf and (len(buf) >= 3 or word["start"] - buf[0]["start"] >= 0.7):
            groups.append(buf)
            buf = [word]
        else:
            buf.append(word)
    if buf:
        groups.append(buf)
    size = int(plan["style"]["captionFontSize"])
    captions = []
    for group in groups:
        text = " ".join(w["text"] for w in group)
        start = group[0]["start"]
        end = max(group[-1]["end"], start + 0.35 + len(text) / 15)
        captions.append({
            "id": _id("cap"),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text[:80],
            "accent": _accent_word(text),
            "fontSize": size,
        })
    return captions


def compile_plan(plan: dict, analysis: dict | None) -> dict:
    """Bake style defaults into the plan so the saved version shows them."""
    plan = copy.deepcopy(plan)
    if analysis and not plan["captions"]:
        plan["captions"] = captions_from_words(plan, analysis)
    rows, _ = timeline(plan)

    def near_sfx(t: float, kind: str, window: float = 0.18) -> bool:
        return any(
            e["type"] == "sound_effect" and e["sfx"] == kind and abs(float(e["time"]) - t) <= window
            for e in plan["effects"]
        )

    for index, row in enumerate(rows):
        if index == 0:
            continue
        t = max(0.0, float(row["outStart"]) - 0.06)
        if not near_sfx(t, "whoosh"):
            plan["effects"].append({
                "id": _id("e"), "type": "sound_effect", "sfx": "whoosh", "time": round(t, 3), "gainDb": -11,
            })
    for effect in list(plan["effects"]):
        if effect["type"] in ("emoji", "arrow", "circle", "badge"):
            t = float(effect["start"])
            if not near_sfx(t, "pop", 0.2):
                plan["effects"].append({
                    "id": _id("e"), "type": "sound_effect", "sfx": "pop", "time": round(t, 3), "gainDb": -10,
                })
        if effect["type"] == "text" and effect.get("kind") == "headline":
            t = float(effect["start"])
            if not near_sfx(t, "thump", 0.25):
                plan["effects"].append({
                    "id": _id("e"), "type": "sound_effect", "sfx": "thump", "time": round(t, 3), "gainDb": -10,
                })
    if rows and not any(e["type"] == "sound_effect" and e["sfx"] == "impact" for e in plan["effects"]):
        plan["effects"].append({
            "id": _id("e"), "type": "sound_effect", "sfx": "impact", "time": 0.0, "gainDb": -8,
        })
    return plan
