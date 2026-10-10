"""Content rules from Unguarded App Guide v2, section 8."""

import re

# Guide 8.3, plus a few Swedish equivalents. Sexual terms always block.
_BLOCK = re.compile(
    r"engaged|fianc[eé]|married|wedding|pregnan|baby bump|expecting|dating|"
    r"hooking up|cheat|affair|divorc|break ?up|\bsplit\b|drunk|wasted|"
    r"\bhigh\b|stoned|rehab|overdose|nude|naked|\bnip\b|braless|upskirt|"
    r"wardrobe malfunction|thirst|sexy|hot (body|legs)|curves|onlyfans|"
    r"\barrest|\bfired\b|\bsick\b|cancer|"
    r"gravid|otrogen|förlovad|naken|berusad|arresterad|häktad",
    re.IGNORECASE,
)

BANNED_EMOJI = set("💦🥵🍆🍑👅🫦🤤😈💋👙🍒💍👶🤰🍼🍷🍺🥴💊🌿")

ALLOWED_EMOJI = [
    "😳", "😅", "😮‍💨", "🫣", "😏", "🙈", "👀", "🫢", "😭", "🔥", "💯",
    "‼️", "❗", "🤔", "🙄", "😬", "☺️", "😊", "😂", "💀", "🥹", "🤭", "🫶",
]


def lint_text(text: str) -> str | None:
    if not text:
        return None
    for ch in text:
        if ch in BANNED_EMOJI:
            return "Den emojin är inte tillåten (sexualisering, relation, berusning eller droger)."
    if _BLOCK.search(text):
        return (
            "Texten bryter mot innehållsreglerna: inga falska påståenden om riktiga personer "
            "(relation, graviditet, berusning, brott, hälsa) och ingen sexualisering. "
            "Beskriv bara det som syns, eller gör en fråga / inre tanke utan de orden."
        )
    return None


def lint_emoji(emoji: str) -> str | None:
    if any(ch in BANNED_EMOJI for ch in emoji):
        return "Den emojin är inte tillåten."
    if emoji not in ALLOWED_EMOJI:
        return f"Emoji {emoji} finns inte i listan. Välj en av: {' '.join(ALLOWED_EMOJI)}"
    return None
