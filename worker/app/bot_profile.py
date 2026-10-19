"""CupCat's behavior profile and memory loader.

The full personality specification is stored in the repository root. The editor
model receives a concise subset suitable for strict JSON edit-plan generation;
the full conversational prompt is exposed separately for future chat calls.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT_PATH = ROOT / "CUPCAT_SYSTEM_PROMPT.md"
MEMORY_RULES_PATH = ROOT / "cupcat_memory_rules.yaml"
CLONE_SPEC_PATH = ROOT / "CUPCAT_BOT_CLONE_SPEC.md"

_SECRET_MARKERS = re.compile(
    r"(api[ _-]?key|access[ _-]?token|password|passwd|lösenord|secret|private key|"
    r"kaggle_key|github token|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)
_RULE_MARKERS = re.compile(
    r"(kom ihåg|spara som regel|ny regel|från och med nu|fran och med nu|"
    r"alltid|aldrig|permanent|från nu|from now on|always|never|every short|"
    r"for every video|in all future videos)",
    re.IGNORECASE,
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_system_prompt() -> str:
    """Return the full user-provided prompt; never fabricate a missing prompt."""
    return _read_text(SYSTEM_PROMPT_PATH)


def load_memory_rules() -> dict[str, Any]:
    """Load the user-provided YAML; a missing/invalid YAML returns an empty dict."""
    text = _read_text(MEMORY_RULES_PATH)
    if not text:
        return {}
    try:
        import yaml

        value = yaml.safe_load(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _memory_path() -> Path:
    configured = os.environ.get("CUPCAT_MEMORY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DATA_DIR / "cupcat_memory_overrides.json"


def load_user_rules() -> list[dict[str, str]]:
    path = _memory_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rules") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[-100:]:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("text") or "").split())
        if text and not _SECRET_MARKERS.search(text):
            result.append({
                "text": text[:500],
                "created_at": str(row.get("created_at") or ""),
            })
    return result


def remember_rule_if_requested(text: str) -> bool:
    """Persist an explicit general rule, not a one-off edit instruction.

    The default file is in DATA_DIR. For persistence across server resets, set
    CUPCAT_MEMORY_FILE to a path on storage that survives those resets.
    """
    clean = " ".join(str(text or "").split())
    if len(clean) < 8 or len(clean) > 500:
        return False
    if not _RULE_MARKERS.search(clean) or _SECRET_MARKERS.search(clean):
        return False

    path = _memory_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        rows = payload.get("rules")
        if not isinstance(rows, list):
            rows = []
        normalized = clean.casefold()
        if any(" ".join(str(row.get("text") or "").split()).casefold() == normalized for row in rows if isinstance(row, dict)):
            return False
        rows.append({
            "text": clean,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        payload["schema_version"] = 1
        payload["rules"] = rows[-100:]
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return True
    except OSError:
        return False


def editor_operation_context() -> str:
    """Relevant bot rules for the JSON-only timeline planner."""
    memory = load_memory_rules()
    permanent = memory.get("permanent_memory") or {}
    style = permanent.get("style") or {}
    current_style = style.get("current") or {}
    safe_zone = style.get("safe_zone") or {}
    delivery = permanent.get("delivery") or {}
    safety = memory.get("truth_and_safety") or {}
    extras = load_user_rules()

    caption = current_style.get("captions") or {}
    pointer = current_style.get("pointer") or {}
    tracking = current_style.get("tracking") or {}
    lines = [
        "CUPCAT PERSONAL EDIT PROFILE — apply these rules before choosing operations.",
        "The user-visible chat is Swedish; authored on-screen copy and upload packaging are English.",
        "Truth outranks virality: never invent a reaction, person, dialogue, event, quote, number or claim.",
        "Use only supported speech for captions. Do not create dialogue from a guess.",
        "On revisions, modify only what the user asked for; preserve the rest of the plan and original audio.",
        "Keep original audio by default. Background music is off unless expressly requested.",
        "No generic LOOK label, no large top headline, no black text box or pill behind captions.",
        "Do not remove, blur, cover, or crop away another creator's logo or watermark.",
        "No zooms, arrows, emojis, slow motion, name labels or mockery focused on a minor.",
        "If a visual target cannot be grounded in the analysis, do not invent a track or add a pointer.",
        "A measured motion peak is not automatically a confirmed face, emotion or reaction.",
        "Keep captions away from faces and inside the safe zone; when no safe placement exists, reduce or omit the element.",
        "Do not claim a feature, AI understanding, QA pass, or rendered output unless the backend actually confirms it.",
        f"Current style: name tag {((current_style.get('name_tag') or {}).get('case', 'lower'))}, "
        f"captions {caption.get('feel', 'minimal')}, fill {caption.get('color', 'white')}, "
        f"stroke {caption.get('stroke', 'black')}, key-word color {caption.get('key_word_color', '#FF2D2D')}, "
        f"caption background box={caption.get('background_box', False)}.",
        f"Pointer policy: types={pointer.get('type', ['red_arrow', 'red_ring'])}; "
        f"count={pointer.get('count', 1)}; emoji count={current_style.get('emoji_count', 1)}; "
        f"top headline={current_style.get('top_headline', False)}.",
        f"Safe zone percentages: x={safe_zone.get('x_min_pct', 6)}–{safe_zone.get('x_max_pct', 88)}%, "
        f"y={safe_zone.get('y_min_pct', 15)}–{safe_zone.get('y_max_pct', 70)}%.",
        f"Delivery reference: master={delivery.get('master', {})}; phone={delivery.get('phone_preview', {})}; "
        f"target loudness={delivery.get('loudness_lufs', -14)} LUFS.",
        f"Tracking reference: {tracking}. Respect missing/failed tracks; never fake tracking.",
        "Do not produce anything except the JSON format and allowed operations specified above.",
    ]
    if extras:
        lines.append("User's explicit global rules to apply:")
        lines.extend(f"- {row['text']}" for row in extras[-20:])
    return "\n".join(lines)


def full_behavior_context() -> str:
    """Full system prompt plus current remembered rules for conversational calls."""
    prompt = load_system_prompt().strip()
    rules = load_user_rules()
    if not rules:
        return prompt
    suffix = "\n\n## User's saved global rules\n" + "\n".join(f"- {row['text']}" for row in rules[-100:])
    return (prompt + suffix).strip()
