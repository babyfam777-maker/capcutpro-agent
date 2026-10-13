"""Source timeline: roles, hook type, emotion, and the hidden viral score.

Every label comes from measured evidence. Missing evidence stays unclassified.
"""

from __future__ import annotations

EMOTIONS = ("funny", "shocking", "cute", "emotional", "dramatic", "awkward", "satisfying", "curious", "relatable", "tense")
HOOKS = ("immediate action", "reaction first", "result first", "curiosity", "conflict", "surprise", "emotional", "comedic")


def build_story(analysis: dict) -> dict:
    duration = float((analysis.get("source") or {}).get("duration") or 0)
    motion = [row for row in analysis.get("motion") or [] if "energy" in row]
    evidence = list(analysis.get("evidence") or [])
    phrases = [
        row for row in analysis.get("phrases") or []
        if row.get("status", "SUPPORTED") == "SUPPORTED" and str(row.get("text") or "").strip()
    ]
    peak = _peak(motion, evidence, duration)
    reaction = _span(evidence, ("smile", "laughter"))
    surprise = _span(evidence, ("scream",))
    applause = _span(evidence, ("applause",))
    music = any(event.get("type") in ("music",) or (event.get("payload") or {}).get("label") == "music" for event in evidence)
    silence = _longest(evidence, "silence")
    emotion, emotion_confidence, emotion_why = _emotion(reaction, surprise, applause, phrases)
    hook_type, hook_why = _hook(reaction, surprise, phrases, peak, duration)
    dead = _dead_opening(motion, peak, phrases, duration)
    punch = _punch(peak, reaction, surprise, duration)
    segments = _segments(duration, punch, reaction, dead)
    questions = _questions(peak, reaction, phrases, emotion, hook_type)
    tracking = _tracking(analysis)
    score = _score(emotion_confidence, phrases, peak, tracking, dead)
    return {
        "duration": round(duration, 3),
        "segments": segments,
        "hookType": hook_type,
        "hookWhy": hook_why,
        "emotion": emotion,
        "emotionConfidence": emotion_confidence,
        "emotionWhy": emotion_why,
        "peak": peak,
        "reaction": reaction,
        "deadOpeningS": dead,
        "punch": punch,
        "silence": silence,
        "musicDominant": music and not phrases,
        "questions": questions,
        "tracking": tracking,
        "viralScore": score,
        "supportedPhrases": len(phrases),
    }


def _peak(motion: list[dict], evidence: list[dict], duration: float) -> dict | None:
    best = None
    for row in motion:
        energy = float(row.get("energy") or 0)
        if best is None or energy > best["energy"]:
            best = {"t": float(row["t"]), "energy": energy, "source": "farneback"}
    for event in evidence:
        if event.get("type") != "motion":
            continue
        energy = float((event.get("payload") or {}).get("energy") or event.get("confidence") or 0)
        moment = float(event.get("t0") or 0)
        if best is None or energy > best["energy"]:
            best = {"t": moment, "energy": energy, "source": "motion-evidence"}
    if best is None:
        return None
    best["t"] = round(min(max(0.0, best["t"]), max(0.0, duration)), 3)
    best["energy"] = round(best["energy"], 4)
    return best


def _span(evidence: list[dict], kinds: tuple[str, ...]) -> dict | None:
    rows = [event for event in evidence if event.get("type") in kinds]
    if not rows:
        return None
    best = max(rows, key=lambda event: float(event.get("confidence") or 0))
    t0 = float(best.get("t0") or 0)
    t1 = max(float(best.get("t1") or t0), t0 + 0.6)
    return {
        "t0": round(t0, 3),
        "t1": round(min(t1, t0 + 1.6), 3),
        "type": best.get("type"),
        "id": best.get("id"),
        "confidence": float(best.get("confidence") or 0),
    }


def _longest(evidence: list[dict], kind: str) -> dict | None:
    rows = [event for event in evidence if event.get("type") == kind]
    if not rows:
        return None
    best = max(rows, key=lambda event: float(event.get("t1") or 0) - float(event.get("t0") or 0))
    return {"t0": float(best.get("t0") or 0), "t1": float(best.get("t1") or 0), "type": kind}


def _emotion(reaction, surprise, applause, phrases) -> tuple[str, float, str]:
    if reaction and reaction["type"] == "laughter":
        return "funny", 0.75, f"Skratt {reaction.get('id') or ''} finns i ljudet."
    if reaction and reaction["type"] == "smile":
        return "funny", 0.62, f"Leende {reaction.get('id') or ''} finns i ansiktsspåret."
    if surprise:
        return "shocking", 0.6, f"Ett skrik {surprise.get('id') or ''} finns i ljudet."
    if applause:
        return "satisfying", 0.55, f"Applåd {applause.get('id') or ''} ligger i ljudet."
    for phrase in phrases:
        text = str(phrase.get("text") or "")
        if "?" in text:
            return "curious", 0.5, "En godkänd replik är en fråga."
    return "unclassified", 0.2, "Inget leende, skratt, skrik, applåd eller frågereplik stödjer en känsla från listan."


def _hook(reaction, surprise, phrases, peak, duration: float) -> tuple[str, str]:
    if reaction:
        early = float(reaction["t0"]) <= max(2.5, duration * 0.25)
        kind = "comedic" if reaction["type"] == "laughter" else "reaction first"
        if early and reaction["type"] == "smile":
            kind = "reaction first"
        return kind, "Hooktypen kommer från ett leende eller skratt, inte från en gissad reaktion."
    if surprise:
        return "surprise", "Hooktypen kommer från ett skrik i ljudet."
    for phrase in phrases:
        if "?" in str(phrase.get("text") or ""):
            return "curiosity", "Hooktypen kommer från en godkänd frågereplik."
    if peak and float(peak["t"]) <= 2.0:
        return "immediate action", "Den starkaste rörelsen ligger i öppningen. Det är rörelse, inte en namngiven handling."
    if peak:
        return "immediate action", "Den starkaste rörelsen ligger senare. Öppningen flyttas dit bara om den är mätbart svagare, så ordningen inte hittas på."
    return "immediate action", "Ingen rörelsetopp hittades. Hooken är öppningen som den är."


def _dead_opening(motion: list[dict], peak: dict | None, phrases: list[dict], duration: float) -> float:
    if duration < 3 or not motion or not peak or float(peak["t"]) < 1.6:
        return 0.0
    early = [float(row["energy"]) for row in motion if float(row["t"]) <= 1.2]
    all_energy = sorted(float(row["energy"]) for row in motion)
    if not early or not all_energy:
        return 0.0
    med = all_energy[len(all_energy) // 2]
    if sum(early) / len(early) >= med * 0.7:
        return 0.0
    for phrase in phrases:
        if float(phrase.get("t0") or 0) < 1.4:
            return 0.0
    cut = min(2.5, max(0.8, float(peak["t"]) - 0.35), duration * 0.35)
    return round(cut, 3)


def _punch(peak, reaction, surprise, duration: float) -> dict | None:
    if reaction:
        return {
            "t0": reaction["t0"],
            "t1": reaction["t1"],
            "zoomStart": 1.25,
            "zoomEnd": 1.38,
            "confirmed": True,
            "reason": f"Punch-zoom 125–138% på {reaction['type']} {reaction.get('id') or ''}. Det är en uppmätt reaktion.",
        }
    if surprise:
        return {
            "t0": surprise["t0"],
            "t1": surprise["t1"],
            "zoomStart": 1.25,
            "zoomEnd": 1.36,
            "confirmed": True,
            "reason": "Punch-zoom på skriket. Det är den uppmätta överraskningen.",
        }
    if not peak or duration <= 0:
        return None
    t0 = max(0.0, float(peak["t"]) - 0.35)
    t1 = min(duration, float(peak["t"]) + 0.85)
    if t1 - t0 < 0.45:
        return None
    return {
        "t0": round(t0, 3),
        "t1": round(t1, 3),
        "zoomStart": 1.10,
        "zoomEnd": 1.18,
        "confirmed": False,
        "reason": (
            f"Punch-zoom 110–118% på rörelsetoppen vid {float(peak['t']):.2f}s. "
            "Det är inte en bekräftad reaktion eller ett ansikte."
        ),
    }


def _segments(duration: float, punch: dict | None, reaction: dict | None, dead: float) -> list[dict]:
    if duration <= 0:
        return []
    ranges = []
    cursor = 0.0
    if dead >= 0.8:
        ranges.append((0.0, min(dead, duration), "setup", "Svag öppning som kan kapas."))
        cursor = min(dead, duration)
    hook_end = min(duration, cursor + 1.6)
    if punch and float(punch["t0"]) >= cursor:
        ranges.append((cursor, min(duration, float(punch["t0"])), "setup", "Fram till rörelsetoppen."))
        ranges.append((float(punch["t0"]), min(duration, float(punch["t1"])), "hook", punch["reason"]))
        cursor = min(duration, float(punch["t1"]))
    else:
        ranges.append((cursor, hook_end, "hook", "Öppningen är hooken eftersom ingen senare topp fick flyttas först."))
        cursor = hook_end
    if reaction and float(reaction["t0"]) >= cursor:
        ranges.append((cursor, float(reaction["t0"]), "escalation", "Bygger mot den uppmätta reaktionen."))
        ranges.append((float(reaction["t0"]), min(duration, float(reaction["t1"])), "reaction", reaction["type"]))
        cursor = min(duration, float(reaction["t1"]))
    payoff_at = max(cursor, duration * 0.72)
    if payoff_at > cursor + 0.3:
        ranges.append((cursor, payoff_at, "escalation", "Mitten får ligga kvar i ursprunglig ordning."))
        cursor = payoff_at
    ending_at = max(cursor, duration - 1.0)
    if ending_at > cursor + 0.2:
        ranges.append((cursor, ending_at, "payoff", "Sista delen före slutet. Ingen påhittad payoff-text."))
        cursor = ending_at
    if duration > cursor + 0.05:
        ranges.append((cursor, duration, "ending", "Slutet behålls. Ingen påtvingad loop."))
    cleaned = []
    for t0, t1, role, why in ranges:
        if t1 - t0 < 0.12:
            continue
        cleaned.append({"t0": round(t0, 3), "t1": round(min(duration, t1), 3), "role": role, "why": why})
    return cleaned


def _questions(peak, reaction, phrases, emotion: str, hook_type: str) -> dict:
    strongest = f"Rörelsetopp vid {peak['t']:.2f}s." if peak else "Ingen rörelsetopp mättes."
    return {
        "whyStop": strongest if peak else "Öppningen har inget uppmätt skäl att stoppa scrollningen.",
        "whyStay": "Godkänd dialog finns." if phrases else "Ingen godkänd dialog. Tittaren stannar bara om bilden själv bär klippet.",
        "waitingFor": f"En uppmätt {reaction['type']}." if reaction else "Inget avslöjande eller någon replik att vänta på hittades.",
        "payoff": "Applåd eller sista rörelsen får vara slutet." if emotion == "satisfying" else "Slutet är källans slut, inte en påhittad payoff.",
        "rewatch": "Ingen loop läggs in. Ett naturligt omtittande kräver en replik eller en reaktion som faktiskt finns.",
        "shareMoment": strongest,
        "hookType": hook_type,
    }


def _tracking(analysis: dict) -> dict:
    kinds = {}
    for track in analysis.get("tracks") or []:
        kind = str(track.get("kind") or "")
        if track.get("samples"):
            kinds.setdefault(kind, []).append(track.get("id"))
    unavailable = [str(item.get("step") or "") for item in analysis.get("unavailable") or []]
    def state(kind: str, step: str) -> str:
        if kinds.get(kind):
            return "ok"
        if any(step in item for item in unavailable):
            return "failed"
        return "none"
    note = "Ansiktsspårning hittade inget." if not kinds.get("face") else "Ansiktsspår finns."
    if kinds.get("person") and not kinds.get("face"):
        note = "Inget ansikte. Ett kroppsspår från pose finns och kan följas. Det är inte en identitet."
    if not kinds.get("face") and not kinds.get("person"):
        note = "Varken ansikte eller kropp är spårad. Beskärningen kan inte följa en person."
    return {
        "face": state("face", "face"),
        "hand": state("hand", "hand"),
        "person": state("person", "pose"),
        "faceIds": kinds.get("face") or [],
        "handIds": kinds.get("hand") or [],
        "personIds": kinds.get("person") or [],
        "note": note,
    }


def _score(emotion_confidence: float, phrases, peak, tracking: dict, dead: float) -> dict:
    """Decision support. Hidden unless the user asks for the score."""
    hook = 70 if peak and float(peak["t"]) <= 2 else (58 if peak else 35)
    if dead >= 0.8:
        hook = 42
    parts = {
        "hook": hook,
        "curiosity": 64 if phrases else 36,
        "emotion": round(emotion_confidence * 100),
        "broadAppeal": 40,
        "clarity": 74 if phrases else 60,
        "payoff": 55 if peak else 34,
        "rewatch": 48,
        "comment": 32,
        "share": 34,
        "trendFit": 30,
        "visual": 50 if tracking.get("face") == "ok" or tracking.get("person") == "ok" else 28,
        "pacing": 52 if dead < 0.8 else 40,
        "originality": 70,
    }
    parts["overall"] = round(sum(parts.values()) / len(parts))
    return parts
