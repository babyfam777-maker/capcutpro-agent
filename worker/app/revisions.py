"""Map a chat line onto the current edit plan. The renderer is not replaced."""

from __future__ import annotations

import copy
import re

from .plan_ops import min_readable_seconds, timeline
from .safety import lint_text

_FOLD = str.maketrans("åäöéü", "aaoeu")


def apply_edits(plan: dict, lines: list[str]) -> dict:
    """Apply every recognized line. Unrecognized lines are reported and do not change the plan."""
    current = copy.deepcopy(plan)
    changes: list[str] = []
    skipped: list[str] = []
    mutated = False
    for raw in lines:
        line = " ".join(str(raw).split())
        if not line:
            continue
        notes, did = _apply_line(current, line)
        if notes and did:
            changes.extend(notes)
            mutated = True
        elif notes and not did:
            changes.extend(notes)
        else:
            skipped.append(line)
    if not mutated:
        detail = " ".join(changes) if changes else ""
        hint = (
            "Jag kunde inte ändra planen. Prova till exempel: "
            "Gör hooken större, Ta bort pilen, Mer zoom på personen, Gör texten roligare."
        )
        return {
            "ok": False,
            "plan": plan,
            "changes": changes,
            "skipped": skipped,
            "error": (detail + " " + hint).strip(),
        }
    return {"ok": True, "plan": current, "changes": changes, "skipped": skipped, "error": None}


def _apply_line(plan: dict, line: str) -> tuple[list[str], bool]:
    folded = _fold(line)
    notes: list[str] = []
    mutated = False
    recognized = False

    if _is_hook_size(folded):
        recognized = True
        text, did = _grow_hook(plan)
        notes.append(text)
        mutated = mutated or did
    elif _is_text_size(folded):
        recognized = True
        text, did = _grow_text(plan)
        notes.append(text)
        mutated = mutated or did

    if _is_remove(folded, ("pil", "arrow")):
        recognized = True
        text, did = _drop_type(plan, "arrow", "pilen", "Ingen pil fanns i planen.")
        notes.append(text)
        mutated = mutated or did
    if _is_remove(folded, ("cirkel", "ringen", "ring", "circle")):
        recognized = True
        text, did = _drop_type(plan, "circle", "ringen", "Ingen ring fanns i planen.")
        notes.append(text)
        mutated = mutated or did
    if _is_remove(folded, ("emoji", "emojin")):
        recognized = True
        text, did = _drop_type(plan, "emoji", "emojin", "Ingen emoji fanns i planen.")
        notes.append(text)
        mutated = mutated or did
    if _is_remove(folded, ("bubbl", "bubble", "tankebubbl")):
        recognized = True
        text, did = _drop_kind(plan, "bubble", "bubblan", "Ingen tankebubbla fanns i planen.")
        notes.append(text)
        mutated = mutated or did

    if _is_more_zoom(folded):
        recognized = True
        text, did = _more_zoom(plan)
        notes.append(text)
        mutated = mutated or did
    elif _is_less_zoom(folded):
        recognized = True
        text, did = _less_zoom(plan)
        notes.append(text)
        mutated = mutated or did

    if _is_funnier(folded):
        recognized = True
        text, did = _funnier(plan)
        notes.append(text)
        mutated = mutated or did

    if not recognized:
        return [], False
    return notes, mutated


def _fold(text: str) -> str:
    return text.lower().translate(_FOLD)


def _is_hook_size(folded: str) -> bool:
    bigger = any(token in folded for token in ("storre", "bigger", "larger", "oka storlek", "increase"))
    hook = any(token in folded for token in ("hook", "rubrik", "headline", "oppn", "oppning"))
    return bigger and hook


def _is_text_size(folded: str) -> bool:
    return any(token in folded for token in ("storre", "bigger", "larger", "textstorlek")) and any(
        token in folded for token in ("text", "caption", "undertext", "story")
    )


def _is_remove(folded: str, nouns: tuple[str, ...]) -> bool:
    if not any(token in folded for token in ("ta bort", "tabort", "remove", "radera", "utan ", "ingen ", "inget ")):
        return False
    return any(noun in folded for noun in nouns)


def _is_more_zoom(folded: str) -> bool:
    if not any(token in folded for token in ("zoom", "zooma", "narmare", "closer")):
        return False
    return not any(token in folded for token in ("ta bort", "mindre", "less", "decrease"))


def _is_less_zoom(folded: str) -> bool:
    return "zoom" in folded and any(token in folded for token in ("mindre", "less", "decrease"))


def _is_funnier(folded: str) -> bool:
    return any(token in folded for token in ("rolig", "funny", "roligare", "kulare", "punch"))


def _grow_hook(plan: dict) -> tuple[str, bool]:
    grown = False
    before = None
    after = None
    for effect in plan.get("effects") or []:
        if effect.get("type") == "text" and effect.get("kind") == "headline":
            size = int(effect.get("fontSize") or plan["style"]["headlineFontSize"])
            before = size if before is None else before
            effect["fontSize"] = min(220, max(size + 16, int(round(size * 1.4))))
            after = effect["fontSize"]
            grown = True
        elif effect.get("type") == "badge":
            size = int(effect.get("fontSize") or 54)
            effect["fontSize"] = min(120, max(size + 10, int(round(size * 1.35))))
            grown = True
    style = plan.setdefault("style", {})
    old_head = int(style.get("headlineFontSize") or 120)
    style["headlineFontSize"] = min(220, max(old_head + 16, int(round(old_head * 1.4))))
    for clip in plan.get("clips") or []:
        if clip.get("label") == "hook":
            clip["zoomEnd"] = round(min(1.9, float(clip.get("zoomEnd") or 1.35) + 0.12), 3)
            grown = True
    if not grown:
        return "Det fanns ingen hook att förstora.", False
    if before is not None and after is not None:
        return f"Hooken är nu {after} px (var {before}).", True
    return "Hookens storlek är höjd.", True


def _grow_text(plan: dict) -> tuple[str, bool]:
    grown = False
    for effect in plan.get("effects") or []:
        if effect.get("type") in ("text", "badge") and effect.get("kind") != "headline":
            size = int(effect.get("fontSize") or (64 if effect.get("kind") == "story" else 48))
            effect["fontSize"] = min(140, int(round(size * 1.3)))
            grown = True
    for caption in plan.get("captions") or []:
        size = int(caption.get("fontSize") or plan["style"]["captionFontSize"])
        caption["fontSize"] = min(140, int(round(size * 1.3)))
        grown = True
    if "style" in plan:
        plan["style"]["captionFontSize"] = min(140, int(round(int(plan["style"]["captionFontSize"]) * 1.3)))
    if not grown:
        return "Det fanns ingen text att förstora.", False
    return "Texten är större.", True


def _drop_type(plan: dict, kind: str, name: str, missing: str) -> tuple[str, bool]:
    effects = plan.get("effects") or []
    kept = [effect for effect in effects if effect.get("type") != kind]
    removed = len(effects) - len(kept)
    plan["effects"] = kept
    if removed:
        return f"Tog bort {name}.", True
    return missing, False


def _drop_kind(plan: dict, kind: str, name: str, missing: str) -> tuple[str, bool]:
    effects = plan.get("effects") or []
    kept = [effect for effect in effects if not (effect.get("type") == "text" and effect.get("kind") == kind)]
    removed = len(effects) - len(kept)
    plan["effects"] = kept
    if removed:
        return f"Tog bort {name}.", True
    return missing, False


def _more_zoom(plan: dict) -> tuple[str, bool]:
    clips = [clip for clip in plan.get("clips") or [] if clip.get("label") != "rewind"]
    tracked = [clip for clip in clips if clip.get("focusTrack")]
    targets = tracked or clips
    if not targets:
        return "Det fanns inget klipp att zooma.", False
    samples = []
    for clip in targets:
        start = float(clip.get("zoomStart") or 1)
        end = float(clip.get("zoomEnd") or start)
        clip["zoomStart"] = round(min(2.0, start + 0.16), 3)
        clip["zoomEnd"] = round(min(2.2, max(end + 0.28, clip["zoomStart"] + 0.08)), 3)
        if not clip.get("focusTrack"):
            clip["focusY"] = round(min(float(clip.get("focusY") or 0.42), 0.38), 3)
        samples.append(float(clip["zoomEnd"]))
    who = "personen" if tracked else "bilden"
    return f"Mer zoom på {who}. Zoom slutar på {max(samples):.2f}.", True


def _less_zoom(plan: dict) -> tuple[str, bool]:
    clips = [clip for clip in plan.get("clips") or [] if clip.get("label") != "rewind"]
    if not clips:
        return "Det fanns inget klipp att zooma ut.", False
    for clip in clips:
        clip["zoomStart"] = round(max(1.0, float(clip.get("zoomStart") or 1) - 0.12), 3)
        clip["zoomEnd"] = round(max(clip["zoomStart"], float(clip.get("zoomEnd") or 1) - 0.18), 3)
    return "Mindre zoom.", True


def _funnier(plan: dict) -> tuple[str, bool]:
    _, total = timeline(plan)
    changed: list[str] = []
    for effect in plan.get("effects") or []:
        if effect.get("type") != "text" or effect.get("kind") not in ("story", "bubble"):
            continue
        old = str(effect.get("text") or "")
        new = _punch(old)
        if not new or new == old or lint_text(new):
            continue
        effect["text"] = new
        _fit_time(effect, total)
        _sync_story(plan, old, new)
        changed.append(new)
    if changed:
        shown = changed[0]
        extra = f" ({len(changed)} rader)" if len(changed) > 1 else ""
        return f"Texten är roligare{extra}: {shown}", True
    home = _bubble_home(plan)
    if not home:
        return "Det fanns ingen tidslinje att lägga roligare text på.", False
    text = "wait... what was that?!"
    if lint_text(text):
        return "Den roligare texten stoppades av innehållsreglerna.", False
    start = float(home["outStart"])
    end = min(float(home["outEnd"]), start + max(1.1, min_readable_seconds(text)))
    if end - start + 1e-3 < min_readable_seconds(text):
        end = min(total, start + min_readable_seconds(text))
    plan.setdefault("effects", []).append({
        "id": _next_id(plan),
        "type": "text",
        "kind": "bubble",
        "start": round(start, 3),
        "end": round(max(end, start + 0.8), 3),
        "text": text,
        "accent": "",
        "fontSize": 48,
    })
    return f"Texten är roligare: {text}", True


def _punch(text: str) -> str:
    raw = " ".join(str(text).split())
    starred = len(raw) >= 2 and raw.startswith("*") and raw.endswith("*")
    inner = raw.strip("*").strip()
    if not inner:
        return raw
    if starred:
        nxt = f"*{inner.rstrip('.!')}... no way*"
    elif _fold(inner).startswith("wait"):
        nxt = inner.rstrip(".!") + "?!"
    else:
        body = inner[0].lower() + inner[1:] if inner[:1].isupper() else inner
        nxt = "wait... " + body.rstrip(".!") + "?!"
    nxt = re.sub(r"\s+", " ", nxt).strip()
    if len(nxt) > 90:
        nxt = nxt[:86].rstrip() + "?!"
    return nxt


def _fit_time(effect: dict, total: float) -> None:
    start = float(effect.get("start") or 0)
    end = float(effect.get("end") or start)
    need = min_readable_seconds(str(effect.get("text") or ""))
    if end - start + 1e-3 < need:
        effect["end"] = round(min(max(total, end), start + need), 3)


def _sync_story(plan: dict, old: str, new: str) -> None:
    for row in plan.get("storyCaptions") or []:
        if row.get("text") == old:
            row["text"] = new


def _bubble_home(plan: dict) -> dict | None:
    rows, _ = timeline(plan)
    for row in rows:
        if row.get("label") != "rewind" and float(row["outEnd"]) - float(row["outStart"]) >= 0.8:
            return row
    return rows[0] if rows else None


def _next_id(plan: dict) -> str:
    numbers = []
    for effect in plan.get("effects") or []:
        match = re.fullmatch(r"e(\d+)", str(effect.get("id") or ""))
        if match:
            numbers.append(int(match.group(1)))
    return f"e{max(numbers, default=0) + 1}"
