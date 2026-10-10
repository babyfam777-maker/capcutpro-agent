"""Factual captions: plausibility, dedupe, and a constrained model overlay.

The model may only pick a hook from a list built out of a supported phrase.
Anything else is a visible fallback. A phrase that fails the lexical or
confidence checks never becomes a factual line.
"""

from __future__ import annotations

import json
import re
import urllib.request

from .config import MODEL, OPENAI_API_KEY, OPENAI_BASE_URL

_EMOJI = ("😳", "😅", "‼️", "🫣")
_CLOSED = {
    "the", "a", "an", "to", "of", "and", "or", "in", "on", "at", "for", "with",
    "from", "some", "that", "this", "it", "i", "you", "we", "they", "he", "she",
    "my", "your", "our", "um", "uh", "hey", "oh", "just", "so", "yeah", "ok",
    "okay", "all", "but", "not", "no", "yes",
}
_PREDICATE = {
    "is", "are", "was", "were", "am", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "go", "going", "gone", "went", "look", "looks", "looking", "looked",
    "love", "loved", "wait", "see", "saw", "say", "said", "want", "need",
    "come", "came", "make", "made", "get", "got", "know", "think", "feel",
    "cry", "crying", "tell", "told", "give", "gave", "take", "took", "let",
    "can", "will", "would", "could", "should", "gonna", "wanna",
}
_EXPAND = (
    ("i'm", "i am"), ("you're", "you are"), ("they're", "they are"),
    ("we're", "we are"), ("it's", "it is"), ("that's", "that is"),
    ("don't", "do not"), ("can't", "cannot"), ("won't", "will not"),
    ("i'll", "i will"), ("let's", "let us"), ("gonna", "going to"),
    ("wanna", "want to"),
)


def expand_tokens(text: str) -> list[str]:
    raw = str(text).lower().replace("’", "'")
    for src, dst in _EXPAND:
        raw = raw.replace(src, dst)
    return re.findall(r"[a-z']+", raw)


def content_tokens(text: str) -> list[str]:
    return [token for token in expand_tokens(text) if token not in _CLOSED]


def same_caption(left: str, right: str) -> bool:
    """Identical or the same content words, including expanded contractions."""
    a, b = expand_tokens(left), expand_tokens(right)
    if a and a == b:
        return True
    ca, cb = content_tokens(left), content_tokens(right)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    inter = len(set(ca) & set(cb))
    union = len(set(ca) | set(cb))
    return union > 0 and inter / union >= 0.8


def has_predicate(text: str) -> bool:
    return any(token in _PREDICATE for token in expand_tokens(text))


def _gibberish(token: str) -> bool:
    if len(token) < 4 or token in _PREDICATE or token in _CLOSED:
        return False
    return not any(ch in "aeiou" for ch in token)


def phrase_signals(phrase: dict, analysis: dict) -> tuple[float | None, float | None]:
    ids = {str(item) for item in phrase.get("wordEvidenceIds") or []}
    confs = []
    for event in analysis.get("evidence") or []:
        if str(event.get("id")) in ids and event.get("confidence") is not None:
            confs.append(float(event["confidence"]))
    logps = []
    transcript_ids = {str(item) for item in phrase.get("transcriptIds") or []}
    for row in analysis.get("transcript") or []:
        linked = str(row.get("id")) in transcript_ids or phrase.get("id") in (row.get("phraseIds") or [])
        if not linked:
            continue
        if not confs and row.get("confidence") is not None:
            confs.append(float(row["confidence"]))
        if row.get("avgLogprob") is not None:
            logps.append(float(row["avgLogprob"]))
    conf = sum(confs) / len(confs) if confs else None
    logp = sum(logps) / len(logps) if logps else None
    return conf, logp


def classify_phrase(phrase: dict, analysis: dict) -> tuple[str, str]:
    """keep, drop, or review. Drop is final. Review needs the model to keep it."""
    text = str(phrase.get("text") or "").strip()
    tokens = expand_tokens(text)
    if not tokens:
        return "drop", "tom fras"
    if any(_gibberish(token) for token in tokens):
        return "drop", "otydliga ord"
    conf, logp = phrase_signals(phrase, analysis)
    if logp is not None and logp < -0.9:
        return "drop", "låg logprob"
    if conf is not None and conf < 0.5:
        return "drop", "låg ordkonfidens"
    if not has_predicate(text) and len(tokens) < 6:
        return "drop", "fragment utan predikat"
    if (conf is not None and conf < 0.65) or (logp is not None and logp < -0.8):
        return "review", "osäker signal"
    return "keep", ""


def allowed_hooks(phrase: dict | None) -> list[str]:
    if not phrase:
        return ["WATCH"]
    words = [re.sub(r"[^A-Za-z0-9']", "", word) for word in str(phrase.get("text") or "").split()]
    words = [word.upper()[:12] for word in words if word]
    hooks = []
    for index in range(len(words) - 1):
        pair = f"{words[index]} {words[index + 1]}"
        if len(pair) <= 22 and pair not in hooks:
            hooks.append(pair)
    if not hooks and words:
        hooks.append(words[-1])
    return hooks[:4] or ["WATCH"]


def split_overlay(hook: str, accent: str) -> tuple[str, str]:
    """Headline words and the red-box word stay disjoint."""
    words = [word for word in str(hook).upper().split() if word]
    mark = str(accent).upper().strip()
    if len(words) >= 2 and mark in words:
        display = " ".join(word for word in words if word != mark).strip()
        if display and mark not in display.split():
            return display, mark
    return " ".join(words), ""


def overlay_messages(allowed: list[str], *, speech: bool, reaction: bool) -> list[dict]:
    system = (
        "Return one JSON object with keys hook, accent, emoji, bubble. "
        "hook must be copied exactly from Allowed hooks. "
        "accent must be one word inside hook. "
        "emoji must be empty or one of 😳 😅 ‼️ 🫣. "
        "If Speech is yes, bubble is empty. If Speech is no, bubble is empty or wait."
    )
    example_user = "Allowed hooks: WAIT GORGEOUS | THAT IS\nReaction: yes\nSpeech: yes"
    example = '{"hook":"WAIT GORGEOUS","accent":"GORGEOUS","emoji":"😳","bubble":""}'
    user = (
        "Allowed hooks: " + " | ".join(allowed) + "\n"
        + f"Reaction: {'yes' if reaction else 'no'}\n"
        + f"Speech: {'yes' if speech else 'no'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": example_user},
        {"role": "assistant", "content": example},
        {"role": "user", "content": user},
    ]


def retry_note(problem: str, allowed: list[str], *, speech: bool) -> str:
    bubble = "bubble must be empty." if speech else "bubble must be empty or wait."
    return (
        f"Invalid: {problem}. hook must be exactly one of: " + " | ".join(allowed) + ". "
        "emoji must be 😳 or 😅 or ‼️ or 🫣 or empty. " + bubble + " Return JSON only."
    )


def sanity_messages(dropped: list[str], review: list[str]) -> list[dict]:
    system = (
        "Return one JSON object with keys keep and agree. "
        "keep may only repeat lines from Reviewable. "
        "agree may only repeat lines from Already dropped. "
        "agree lists lines that are not real remarks."
    )
    example_user = "Reviewable: you look beautiful\nAlready dropped: the qx zz"
    example = '{"keep":["you look beautiful"],"agree":["the qx zz"]}'
    shown = " | ".join(review) or "(none)"
    dropped_line = " | ".join(dropped[:6]) or "(none)"
    user = f"Reviewable: {shown}\nAlready dropped: {dropped_line}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": example_user},
        {"role": "assistant", "content": example},
        {"role": "user", "content": user},
    ]


def parse_json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip().strip("`").strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def validate_overlay(choice: dict | None, allowed: list[str], *, speech: bool, reaction: bool) -> str | None:
    if not isinstance(choice, dict):
        return "not a JSON object"
    hook = str(choice.get("hook") or "").strip().upper()
    accent = str(choice.get("accent") or "").strip().upper()
    emoji = str(choice.get("emoji") or "").strip()
    bubble = str(choice.get("bubble") or "").strip().lower()
    if hook not in allowed:
        return "hook is outside the allowed list"
    if accent not in hook.split():
        return "accent is not a word in the hook"
    if emoji and (emoji not in _EMOJI or not reaction):
        return "emoji is not allowed"
    if speech and bubble:
        return "bubble must be empty when there is speech"
    if bubble not in ("", "wait"):
        return "bubble is not allowed"
    return None


def kept_review_lines(payload: dict | None, review: list[str]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    wanted = payload.get("keep")
    if isinstance(wanted, str):
        wanted = [wanted]
    if not isinstance(wanted, list):
        return []
    saved = []
    for item in wanted:
        text = str(item or "").strip()
        match = next((line for line in review if line.lower() == text.lower()), None)
        if match and match not in saved:
            saved.append(match)
    return saved


def complete_json(messages: list[dict]) -> str:
    body = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    request = urllib.request.Request(
        OPENAI_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())
    return str(payload["choices"][0]["message"]["content"])
