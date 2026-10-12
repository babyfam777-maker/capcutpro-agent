"""Compact YouTube trending stats. The model never sees the raw rows.

Current daily files need a free Kaggle token. Without one, the bundled file is
the public 2019–2020 title mirror, labelled historical. It has no view counts.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

from .config import DATA_DIR

BUNDLED = Path(__file__).resolve().parents[1] / "assets" / "trending" / "historical_global.json"
TREND_DIR = DATA_DIR / "trending"
CURRENT_DATASET = "rsrishav/youtube-trending-video-dataset"
HISTORY_DATASET = "datasnaek/youtube-new"
CREDENTIAL = (
    "A free Kaggle API token is required for the daily YouTube Trending dataset. "
    "Create one at https://www.kaggle.com/settings (Account → Create New Token). "
    "Set KAGGLE_USERNAME and KAGGLE_KEY, or save the file as ~/.kaggle/kaggle.json. "
    "The worker then downloads rsrishav/youtube-trending-video-dataset (current, daily) "
    "and datasnaek/youtube-new (older history that includes view counts). "
    "kagglehub anonymous download is not available for these datasets, and no public "
    "mirror of the daily dump was reachable. No token was set in this environment."
)
CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
    "17": "Sports", "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs",
    "23": "Comedy", "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
    "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism",
}
_STOP = set("a an the and of to in for on with you your this that from is it at by my we be or if so just not are was i me our but as its how what when who why new video official".split())
_NOTE = "Patterns only. This is not a prediction and not a view guarantee."


def status() -> dict:
    current = _read_stats("current")
    historical = _read_stats("historical") or _bundled()
    return {
        "current": bool(current),
        "historical": bool(historical),
        "historicalSource": (historical or {}).get("source"),
        "historicalVintage": (historical or {}).get("vintage"),
        "views": bool((current or historical or {}).get("views")),
        "credentialNeeded": None if current else CREDENTIAL,
    }


def analyze_trending_data(topic: str = "", category: str | None = None, region: str = "US") -> dict:
    """Return pattern stats for one region. Historical and current stay separate."""
    region = (region or "US").upper()
    current = _region_stats("current", region)
    historical = _region_stats("historical", region) or _bundled()
    primary = current or historical or _empty(region)
    vintage = primary.get("vintage") or "missing"
    if current and region != current.get("region") and current.get("region") not in (region, "GLOBAL"):
        primary = current
    interest = _topic_interest(primary, topic, category)
    structures = primary.get("title_structures") or {}
    patterns = _pattern_names(structures)
    high = list(primary.get("high_performing_patterns") or [])
    if not high:
        high = ["View counts are not in this file, so nothing is ranked as high-performing."]
    score = _trend_score(primary, interest, bool(current))
    confidence = _confidence(primary, interest, bool(current))
    packaging = {
        "titleLengthTarget": (primary.get("title_length") or {}).get("median"),
        "preferQuestion": float(structures.get("question") or 0) >= 0.12,
        "hashtagCount": 6,
        "note": _NOTE,
    }
    return {
        "trend_score": score,
        "topic_interest": interest,
        "common_title_patterns": patterns,
        "high_performing_patterns": high,
        "recommended_hook_patterns": _hook_patterns(structures),
        "recommended_packaging": packaging,
        "confidence": confidence,
        "vintage": vintage,
        "region": region,
        "region_used": primary.get("region") or region,
        "source": primary.get("source"),
        "as_of": primary.get("date_max"),
        "rows": primary.get("rows") or 0,
        "views_available": bool(primary.get("views")),
        "historical": _public_summary(historical),
        "current": _public_summary(current),
        "note": _NOTE,
        "credentialNeeded": None if current else CREDENTIAL,
    }


def preprocess_csv(path: Path, vintage: str, source: str, region: str) -> dict:
    with Path(path).open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    stats = preprocess_rows(rows, vintage=vintage, source=source, region=region)
    dest = TREND_DIR / "stats" / f"{vintage}_{region.lower()}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


def preprocess_rows(rows: list[dict], vintage: str, source: str, region: str) -> dict:
    titles = []
    views = []
    like_ratios = []
    comment_ratios = []
    categories = Counter()
    hours = [0] * 24
    weekdays = [0] * 7
    words = Counter()
    bigrams = Counter()
    structures = Counter()
    channels = Counter()
    dates = []
    for row in rows:
        title = str(_first(row, "title", "Title") or "")
        if not title:
            continue
        titles.append(title)
        view = _num(_first(row, "view_count", "views", "Views"))
        likes = _num(_first(row, "likes", "like_count", "Likes"))
        comments = _num(_first(row, "comment_count", "comments", "Comments"))
        if view and view > 0:
            views.append(view)
            if likes is not None:
                like_ratios.append(likes / view)
            if comments is not None:
                comment_ratios.append(comments / view)
        category = str(_first(row, "category", "category_id", "categoryId") or "")
        categories[CATEGORIES.get(category, category or "unknown")] += 1
        channels[str(_first(row, "channel_title", "channelTitle", "channel_name") or "")] += 1
        _count_title(title, words, bigrams, structures)
        moment = _parse_time(_first(row, "publishedAt", "publish_time", "published_at", "trending_date"))
        if moment:
            hours[moment.hour] += 1
            weekdays[moment.weekday()] += 1
            dates.append(moment.date().isoformat())
    count = len(titles) or 1
    view_stats = _ratio_block(views) if views else None
    engagement = None
    if like_ratios or comment_ratios:
        engagement = {
            "likePerView": _ratio_block(like_ratios),
            "commentPerView": _ratio_block(comment_ratios),
        }
    high = []
    outliers = None
    if views:
        cutoff = _percentile(views, 0.95)
        outliers = {"p95Views": round(cutoff, 1), "count": sum(1 for item in views if item >= cutoff)}
        high.append(f"Titles in the top 5% of views sit at or above {int(cutoff)} views in this file.")
        if float(( _share(structures, "question", count)) ) >= 0.12:
            high.append("Question-shaped titles are common among the rows that also have view counts.")
    return {
        "vintage": vintage,
        "source": source,
        "source_note": "Preprocessed from a local CSV. Titles of other channels are not copied into edits.",
        "region": region.upper(),
        "rows": len(titles),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "views": view_stats,
        "engagement": engagement,
        "top_categories": [
            {"id": name, "name": name, "count": amount, "share": round(amount / count, 4)}
            for name, amount in categories.most_common(12)
        ],
        "title_length": _ratio_block([len(title) for title in titles]) if titles else None,
        "common_words": words.most_common(80),
        "common_phrases": bigrams.most_common(40),
        "title_structures": {key: round(structures[key] / count, 4) for key in ("question", "number", "curiosity", "emotion", "conflict", "reveal")},
        "publish_hour_utc": hours,
        "publish_weekday": weekdays,
        "recurring_channels": [{"name": name, "count": amount} for name, amount in channels.most_common(12) if name],
        "outliers": outliers,
        "high_performing_patterns": high,
    }


def _bundled() -> dict | None:
    if not BUNDLED.exists():
        return None
    return json.loads(BUNDLED.read_text())


def _read_stats(vintage: str) -> dict | None:
    folder = TREND_DIR / "stats"
    if not folder.exists():
        return None
    files = sorted(folder.glob(f"{vintage}_*.json"))
    if not files:
        return None
    return json.loads(files[0].read_text())


def _region_stats(vintage: str, region: str) -> dict | None:
    path = TREND_DIR / "stats" / f"{vintage}_{region.lower()}.json"
    if path.exists():
        return json.loads(path.read_text())
    global_path = TREND_DIR / "stats" / f"{vintage}_global.json"
    if global_path.exists():
        return json.loads(global_path.read_text())
    found = _read_stats(vintage)
    return found


def _empty(region: str) -> dict:
    return {"vintage": "missing", "region": region, "rows": 0, "title_structures": {}, "source": None}


def _topic_interest(stats: dict, topic: str, category: str | None) -> float:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (topic or "").lower()) if token not in _STOP and len(token) > 2]
    words = {str(item[0]).lower(): int(item[1]) for item in stats.get("common_words") or [] if isinstance(item, (list, tuple)) and len(item) == 2}
    total = sum(words.values()) or 1
    if tokens and words:
        hits = sum(words.get(token, 0) for token in tokens)
        word_score = min(1.0, hits / max(1, total * 0.02))
    else:
        word_score = 0.0
    category_score = 0.0
    if category:
        folded = category.lower()
        for row in stats.get("top_categories") or []:
            if folded in str(row.get("name") or "").lower():
                category_score = float(row.get("share") or 0)
                break
    if tokens or category:
        return round(min(1.0, word_score * 0.7 + category_score * 0.6), 3)
    return 0.0


def _trend_score(stats: dict, interest: float, has_current: bool) -> float:
    structures = stats.get("title_structures") or {}
    structure = max(float(structures.get(key) or 0) for key in ("question", "number", "curiosity", "emotion")) if structures else 0
    base = 20 + interest * 40 + structure * 25
    if stats.get("views"):
        base += 10
    if not has_current or stats.get("vintage") != "current":
        base = min(base, 55)
    return round(max(0.0, min(100.0, base)), 1)


def _confidence(stats: dict, interest: float, has_current: bool) -> float:
    if not stats.get("rows"):
        return 0.0
    if has_current and stats.get("views"):
        base = 0.72
    elif stats.get("views"):
        base = 0.48
    elif stats.get("vintage") == "historical":
        base = 0.28
    else:
        base = 0.15
    if interest <= 0:
        base -= 0.08
    return round(max(0.05, min(0.9, base)), 2)


def _pattern_names(structures: dict) -> list[str]:
    named = []
    for key, label in (
        ("question", "question titles"),
        ("number", "a number in the title"),
        ("curiosity", "curiosity words"),
        ("emotion", "emotion words"),
        ("conflict", "conflict words such as versus"),
        ("reveal", "reveal words"),
    ):
        share = float(structures.get(key) or 0)
        if share >= 0.08:
            named.append(f"{label} ({share:.0%} of this file)")
    return named or ["No title structure passed 8% in this file."]


def _hook_patterns(structures: dict) -> list[str]:
    patterns = []
    if float(structures.get("question") or 0) >= 0.1:
        patterns.append("A short question, only when the clip actually asks or shows one.")
    if float(structures.get("number") or 0) >= 0.15:
        patterns.append("A concrete number taken from the clip, such as a timestamp, not a made-up count.")
    patterns.append("One specific visible fact. Do not copy another channel's title.")
    return patterns[:4]


def _public_summary(stats: dict | None) -> dict | None:
    if not stats:
        return None
    return {
        "vintage": stats.get("vintage"),
        "source": stats.get("source"),
        "region": stats.get("region"),
        "rows": stats.get("rows"),
        "date_min": stats.get("date_min"),
        "date_max": stats.get("date_max"),
        "views_available": bool(stats.get("views")),
        "as_of": stats.get("date_max"),
    }


def _count_title(title: str, words: Counter, bigrams: Counter, structures: Counter) -> None:
    low = title.lower()
    tokens = re.findall(r"[a-z0-9']+", low)
    kept = [token for token in tokens if token not in _STOP and len(token) > 2]
    words.update(kept)
    bigrams.update(f"{a} {b}" for a, b in zip(kept, kept[1:]))
    if "?" in title or (tokens and tokens[0] in {"who", "what", "why", "how", "when", "where"}):
        structures["question"] += 1
    if re.search(r"\d", title):
        structures["number"] += 1
    if any(word in low for word in ("secret", "actually", "wait", "truth", "why", "how")):
        structures["curiosity"] += 1
    if any(word in low for word in ("love", "cry", "shock", "funny", "cute", "sad", "angry")):
        structures["emotion"] += 1
    if any(word in low for word in (" vs ", " versus ", " against ", " fight")):
        structures["conflict"] += 1
    if any(word in low for word in ("revealed", "reveal", "ending", "exposed")):
        structures["reveal"] += 1


def _ratio_block(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "mean": round(sum(ordered) / len(ordered), 4),
        "median": round(median(ordered), 4),
        "p25": round(_percentile(ordered, 0.25), 4),
        "p75": round(_percentile(ordered, 0.75), 4),
    }


def _percentile(ordered: list[float], portion: float) -> float:
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * portion
    low, high = int(index), min(len(ordered) - 1, int(index) + 1)
    weight = index - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def _share(structures: Counter, key: str, count: int) -> float:
    return structures[key] / max(1, count)


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    folded = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key.lower() in folded and folded[key.lower()] not in (None, ""):
            return folded[key.lower()]
    return None


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y.%m.%d", "%y.%d.%m"):
        try:
            return datetime.strptime(text.replace("Z", "+0000") if fmt.endswith("%z") else text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def try_kaggle_download() -> dict:
    """Download the daily and historical Kaggle sets when a free token is present."""
    username = os.environ.get("KAGGLE_USERNAME", "").strip()
    key = os.environ.get("KAGGLE_KEY", "").strip()
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    if not ((username and key) or kaggle_json.exists()):
        return {"ok": False, "error": CREDENTIAL}
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except Exception as error:
        return {"ok": False, "error": f"Kagglepaketet saknas ({error}). Kör pip install kaggle i workern. {CREDENTIAL}"}
    raw = TREND_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    api = KaggleApi()
    api.authenticate()
    saved = []
    for slug, vintage in ((CURRENT_DATASET, "current"), (HISTORY_DATASET, "historical")):
        dest = raw / slug.split("/")[-1]
        dest.mkdir(parents=True, exist_ok=True)
        api.dataset_download_files(slug, path=str(dest), unzip=True, quiet=True)
        saved.append({"dataset": slug, "vintage": vintage, "path": str(dest)})
    return {"ok": True, "saved": saved}
