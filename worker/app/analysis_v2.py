"""VideoAnalysis v2: evidence, tracks, and phrase segments.

This module does not render and does not write analysis.json. Heavy models are
loaded one at a time and released. A missing library omits that evidence; it is
never replaced with a guess.
"""

from __future__ import annotations

import gc
import logging
import math
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from .analysis import _load_audio, detect_scenes, transcribe_detailed
from .ffmpeg_util import video_meta

log = logging.getLogger(__name__)

EVIDENCE_TYPES = {
    "scene_cut",
    "motion",
    "face_track",
    "smile",
    "mouth_open",
    "point",
    "hand_to_mouth",
    "held_object",
    "speech_phrase",
    "speech_word",
    "silence",
    "music",
    "laughter",
    "applause",
    "scream",
    "onset",
}
TRACK_KINDS = {"person", "face", "hand", "object"}
PHRASE_GAP_S = 0.45
PHRASE_MAX_WORDS = 6
LOGPROB_REJECT = -1.0
COMPRESSION_REJECT = 2.4
NO_SPEECH_REJECT = 0.6
LANG_PROB_REJECT = 0.5
SPEECH_SUPPORT_MIN = 0.35
_NONSPEECH = (
    ("music", 132),
    ("singing", 24),
    ("laughter", 13),
    ("applause", 62),
    ("scream", 11),
    ("cheering", 61),
    ("crowd", 64),
    ("chatter", 63),
    ("hubbub", 65),
)
# Face and hand boxes are evidence, not identities. These limits are documented
# here so a later storyboard does not treat a miss as a fact.
FACE_HAND_LIMITATIONS = [
    "Ansikten i mörker, i profil och på avstånd missas ofta. Tröskeln anpassas inte efter ett enstaka klipp.",
    "Handspår hamnar ofta på armar och överkropp. Grepp betyder bara en knuten hand, inte ett namngivet föremål. Pekning är en heuristik.",
    "Ett leende är inte skratt. Skratt, applåd och skrik kommer bara från ljudklassningen.",
]
SMILE_MIN = 0.45
SMILE_MIN_S = 0.20
JAW_MIN = 0.40
JAW_MIN_S = 0.15
GESTURE_MIN_S = 0.20
YAMNET_MIN = 0.35
YAMNET_MIN_S = 0.20
SILENCE_RMS = 0.01
SAMPLE_FPS = 8

_FACE_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
_HAND_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
_YAMNET_URL = "https://huggingface.co/audiomagic/yamnet-onnx/resolve/main/yamnet.onnx"
_YAMNET_MAP_URL = "https://huggingface.co/audiomagic/yamnet-onnx/resolve/main/yamnet_class_map.csv"
_YAMNET_INDEX = {"speech": 0, "scream": 11, "laughter": 13, "singing": 24, "applause": 62, "music": 132}


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def ev(self) -> str:
        self._n += 1
        return f"ev_{self._n}"


def build_video_analysis(path: Path, sha256: str | None = None) -> dict:
    """Read the file duration, then collect evidence. Raises if the duration is missing."""
    path = Path(path)
    rss_before = _rss_mb()
    timings: dict[str, float] = {}
    unavailable: list[dict] = []

    def timed(name, fn):
        start = __import__("time").perf_counter()
        try:
            return fn()
        finally:
            timings[name] = round(__import__("time").perf_counter() - start, 3)

    try:
        meta = timed("probe", lambda: video_meta(path))
    except Exception as error:
        raise RuntimeError(f"Videons längd kunde inte läsas från filen ({error}). Ingen analys v2 skapas.") from error
    duration = float(meta.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Videons längd kunde inte läsas från filen. Ingen analys v2 skapas.")

    scenes = timed("scenes", lambda: _scenes(path, duration, unavailable))
    motion_series, motion_evidence = timed("motion", lambda: _motion(path, meta, unavailable))
    face_tracks, face_events = timed("faces", lambda: _faces(path, meta, unavailable))
    hand_tracks, hand_events = timed("hands", lambda: _hands(path, meta, face_tracks, unavailable))
    raw = timed("whisper", lambda: _read_transcript(path, bool(meta.get("hasAudio")), unavailable))
    prepared: dict = {}

    def _prepare_audio() -> None:
        if not meta.get("hasAudio"):
            prepared["audio"] = None
            prepared["scores"] = None
            prepared["hop"] = 0.48
            return
        audio = _load_audio(path)
        scores = _yamnet_scores(audio, unavailable)
        hop = 0.48
        if scores is not None and len(scores) and len(audio):
            hop = max(0.05, (len(audio) / 16000) / len(scores))
        prepared["audio"] = audio
        prepared["scores"] = scores
        prepared["hop"] = hop

    timed("audio", _prepare_audio)
    profiles: list[dict | None] | None
    if prepared["scores"] is None:
        profiles = [None] * len(raw["segments"])
    else:
        profiles = [
            _audio_profile(prepared["scores"], prepared["hop"], float(segment.get("t0") or 0), float(segment.get("t1") or 0))
            for segment in raw["segments"]
        ]
    transcript = gate_transcript(
        raw["segments"],
        language=raw.get("language"),
        language_probability=raw.get("languageProbability"),
        profiles=profiles,
    )
    words = supported_words(transcript)
    phrases = segment_phrases(words, scene_times=scenes)
    mouth = drop_mouth_open_during_speech(
        [event for event in face_events if event["type"] == "mouth_open"],
        phrases,
    )
    face_events = [event for event in face_events if event["type"] != "mouth_open"] + mouth
    bins, audio_events = _audio(
        path,
        duration,
        bool(meta.get("hasAudio")),
        words,
        unavailable,
        prepared=prepared,
    )

    ids = _Ids()
    evidence: list[dict] = []

    def take(event: dict) -> None:
        event = dict(event)
        event["id"] = ids.ev()
        event.setdefault("trackId", None)
        evidence.append(event)

    for cut in scenes:
        take({
            "type": "scene_cut",
            "t0": cut,
            "t1": round(min(duration, cut + 0.05), 3),
            "trackId": None,
            "confidence": 0.8,
            "source": "scenedetect",
            "payload": {},
        })
    for event in motion_evidence + face_events + hand_events + audio_events:
        take(event)
    for word in words:
        take({
            "type": "speech_word",
            "t0": word["t0"],
            "t1": word["t1"],
            "trackId": None,
            "confidence": float(word.get("confidence") or 0.6),
            "source": "whisper",
            "payload": {"text": word["text"]},
        })
        word["id"] = evidence[-1]["id"]
    phrase_docs = []
    for index, phrase in enumerate(phrases, start=1):
        ids_for_phrase = [word["id"] for word in phrase]
        text = " ".join(word["text"] for word in phrase)
        phrase_id = f"ph_{index}"
        transcript_ids = sorted({word["transcriptId"] for word in phrase if word.get("transcriptId")})
        confidence = round(sum(float(word.get("confidence") or 0.6) for word in phrase) / len(phrase), 3)
        phrase_docs.append({
            "id": phrase_id,
            "text": text,
            "t0": phrase[0]["t0"],
            "t1": phrase[-1]["t1"],
            "wordEvidenceIds": ids_for_phrase,
            "status": "SUPPORTED",
            "transcriptIds": transcript_ids,
        })
        for row in transcript:
            if row["id"] in transcript_ids:
                row["phraseIds"].append(phrase_id)
        take({
            "type": "speech_phrase",
            "t0": phrase[0]["t0"],
            "t1": phrase[-1]["t1"],
            "trackId": None,
            "confidence": confidence,
            "source": "phrase_segmenter",
            "payload": {"text": text},
        })
    for row in transcript:
        row.pop("words", None)

    tracks = [_export_track(track, "face") for track in face_tracks]
    tracks += [_export_track(track, "hand") for track in hand_tracks]
    doc = {
        "version": 2,
        "source": {
            "duration": round(duration, 3),
            "width": int(meta["width"]),
            "height": int(meta["height"]),
            "fps": float(meta["fps"]),
            "hasAudio": bool(meta["hasAudio"]),
            "sha256": sha256 or _sha256(path),
        },
        "scenes": scenes,
        "tracks": tracks,
        "evidence": evidence,
        "phrases": phrase_docs,
        "transcript": transcript,
        "limitations": list(FACE_HAND_LIMITATIONS),
        "motion": motion_series,
        "audioBins": bins,
        "unavailable": unavailable,
        "timingsSec": timings,
        "rssBeforeMb": rss_before,
        "peakRssMb": _hwm_mb(),
    }
    problems = validate_video_analysis(doc)
    if problems:
        doc["validationErrors"] = problems
        log.warning("analysis_v2 validation: %s", "; ".join(problems[:8]))
    return doc


def segment_phrases(words: list[dict], scene_times: list[float] | None = None) -> list[list[dict]]:
    """Build phrases from word times. A scene cut is not a boundary.

    A new sentence (capital, end punctuation, or a pause) closes the previous
    phrase before the 6-word safety cap can cut inside it. Commas stay inside
    the phrase so a clause is not shortened mid-sentence.
    """
    del scene_times  # kept in the signature so callers cannot pass cuts as boundaries by accident
    phrases: list[list[dict]] = []
    bucket: list[dict] = []
    prev_end = None

    def close() -> None:
        nonlocal bucket
        if bucket:
            phrases.append(bucket)
            bucket = []

    for word in words:
        start = float(word["t0"])
        if bucket and prev_end is not None and start - prev_end >= PHRASE_GAP_S:
            close()
        if bucket and _is_sentence_start(str(word.get("text") or "")):
            close()
        if len(bucket) >= PHRASE_MAX_WORDS:
            close()
        bucket.append(word)
        prev_end = float(word["t1"])
        token = str(word.get("text") or "")
        if token and token[-1] in ".?!…":
            close()
    close()
    return phrases


def _is_sentence_start(text: str) -> bool:
    raw = text.lstrip("\"'“‘(¿¡")
    if raw == "I":
        return True
    if len(raw) < 2 or not raw[0].isupper() or raw[0].isdigit():
        return False
    return True


class UnusableTranscriptError(ValueError):
    """Raised when a caption or claim tries to use transcript that is not SUPPORTED."""


def phrases_for_editplan(doc: dict, phrase_ids: list[str] | None = None) -> list[dict]:
    """Return only SUPPORTED phrases. REJECTED and UNCERTAIN cannot be captions or claims."""
    allowed = {
        phrase["id"]: phrase
        for phrase in doc.get("phrases") or []
        if phrase.get("status", "SUPPORTED") == "SUPPORTED"
    }
    blocked: set[str] = set()
    for row in doc.get("transcript") or []:
        if row.get("status") == "SUPPORTED":
            continue
        blocked.add(str(row.get("id") or ""))
        for phrase_id in row.get("phraseIds") or []:
            blocked.add(str(phrase_id))
    chosen = list(allowed) if phrase_ids is None else list(phrase_ids)
    refused = [phrase_id for phrase_id in chosen if phrase_id in blocked or phrase_id not in allowed]
    if refused:
        raise UnusableTranscriptError(
            "Texten " + ", ".join(refused) + " är inte SUPPORTED och får inte bli undertext eller påstående."
        )
    return [allowed[phrase_id] for phrase_id in chosen]


def gate_transcript(
    segments: list[dict],
    *,
    language: str | None,
    language_probability: float | None,
    profiles: list[dict | None] | None,
) -> list[dict]:
    """Mark each Whisper segment SUPPORTED, UNCERTAIN, or REJECTED.

    Whisper is not usable by itself. A missing audio profile cannot be SUPPORTED.
    Music, laughter, applause, crowd and other non-speech reject the words.
    """
    rows = []
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "").strip()
        words = [dict(word) for word in segment.get("words") or [] if str(word.get("text") or "").strip()]
        if not text and not words:
            continue
        profile = None if profiles is None else (profiles[index] if index < len(profiles) else None)
        reasons: list[str] = []
        status = "SUPPORTED"
        if segment.get("hallucination"):
            status = "REJECTED"
            reasons.append("Känd Whisper-hallucination. Talet redovisas som saknat.")
        if segment.get("loop"):
            status = "REJECTED"
            reasons.append("Samma ord upprepades och togs bort. Det är en Whisper-loop, inte tal.")
        if _non_latin_script(text) or (language not in (None, "", "en")):
            status = "REJECTED"
            reasons.append(
                f"Språket {language or 'okänt'} ger inte engelsk text som kan användas. Tal saknas hellre än att orden hittas på."
            )
        if language_probability is not None and float(language_probability) < LANG_PROB_REJECT:
            status = "REJECTED"
            reasons.append(f"Språkdetektorn är osäker ({float(language_probability):.2f}).")
        has_log = segment.get("avgLogprob") is not None
        has_noise = segment.get("noSpeechProb") is not None
        has_compression = segment.get("compressionRatio") is not None
        avg_logprob = float(segment["avgLogprob"]) if has_log else 0.0
        no_speech = float(segment["noSpeechProb"]) if has_noise else 0.0
        compression = float(segment["compressionRatio"]) if has_compression else 0.0
        if not (has_log and has_noise and has_compression):
            if status == "SUPPORTED":
                status = "UNCERTAIN"
            reasons.append("Whisper-signalerna saknas, så texten kan inte räknas som tal.")
        if has_log and avg_logprob < LOGPROB_REJECT:
            status = "REJECTED"
            reasons.append(f"Whisper avg_logprob {avg_logprob:.2f} är för låg.")
        if has_compression and compression > COMPRESSION_REJECT:
            status = "REJECTED"
            reasons.append(f"Whisper compression_ratio {compression:.2f} tyder på upprepning.")
        speech = nonspeech = None
        nonspeech_name = None
        if profile is None:
            if status == "SUPPORTED":
                status = "UNCERTAIN"
            reasons.append("Ljudklassning saknas, så Whisper räknas inte som tal.")
        else:
            speech = float(profile.get("speech") or 0)
            nonspeech = float(profile.get("nonspeech") or 0)
            nonspeech_name = profile.get("name")
            if nonspeech >= YAMNET_MIN and nonspeech > speech + 0.05:
                status = "REJECTED"
                reasons.append(f"Ljudet är främst {nonspeech_name or 'icke-tal'}, inte tal.")
            elif status == "SUPPORTED" and speech < SPEECH_SUPPORT_MIN:
                status = "UNCERTAIN"
                reasons.append(f"Tal-score {speech:.2f} räcker inte för att texten ska användas.")
        if has_noise and no_speech > NO_SPEECH_REJECT and (speech is None or speech < SPEECH_SUPPORT_MIN):
            status = "REJECTED"
            reasons.append(f"Whisper no_speech_prob {no_speech:.2f} säger att det inte är tal.")
        if not words:
            status = "REJECTED"
            reasons.append("Segmentet har inga ord.")
        if status == "SUPPORTED" and not reasons:
            reasons.append("Engelskt tal, tillräcklig Whisper-signal och ljudklassning som tal.")
        confidence = _transcript_confidence(status, language_probability, avg_logprob, speech)
        rows.append({
            "text": text or " ".join(word["text"] for word in words),
            "t0": float(segment.get("t0") if segment.get("t0") is not None else words[0]["t0"]),
            "t1": float(segment.get("t1") if segment.get("t1") is not None else words[-1]["t1"]),
            "status": status,
            "confidence": confidence,
            "reasons": reasons,
            "language": language,
            "languageProbability": None if language_probability is None else round(float(language_probability), 3),
            "avgLogprob": None if segment.get("avgLogprob") is None else round(avg_logprob, 3),
            "noSpeechProb": None if segment.get("noSpeechProb") is None else round(no_speech, 3),
            "compressionRatio": None if segment.get("compressionRatio") is None else round(compression, 3),
            "speechScore": None if speech is None else round(speech, 3),
            "nonspeechName": nonspeech_name,
            "nonspeechScore": None if nonspeech is None else round(nonspeech, 3),
            "words": words,
            "phraseIds": [],
        })
    _reject_repeated_segments(rows)
    for index, row in enumerate(rows, start=1):
        row["id"] = f"tr_{index}"
    return rows


def supported_words(rows: list[dict]) -> list[dict]:
    """Words a later edit plan is allowed to read. REJECTED and UNCERTAIN are omitted."""
    words = []
    for row in rows:
        if row.get("status") != "SUPPORTED":
            continue
        for word in row.get("words") or []:
            words.append({
                "text": word["text"],
                "t0": float(word.get("t0", word.get("start"))),
                "t1": float(word.get("t1", word.get("end"))),
                "transcriptId": row["id"],
                "confidence": row["confidence"],
            })
    words.sort(key=lambda word: (word["t0"], word["t1"]))
    return words


def _reject_repeated_segments(rows: list[dict]) -> None:
    index = 0
    while index < len(rows):
        token = _norm_token(rows[index].get("text") or "")
        end = index + 1
        while end < len(rows) and token and _norm_token(rows[end].get("text") or "") == token:
            end += 1
        if token and end - index >= 3:
            for row in rows[index:end]:
                row["status"] = "REJECTED"
                row["confidence"] = min(float(row["confidence"]), 0.2)
                row["reasons"] = [reason for reason in row["reasons"] if not reason.startswith("Engelskt tal")] + [
                    "Samma text upprepades och behandlas som en Whisper-loop. Tal saknas."
                ]
        index = end


def _transcript_confidence(status: str, language_probability: float | None, avg_logprob: float, speech: float | None) -> float:
    lang = 0.0 if language_probability is None else max(0.0, min(1.0, float(language_probability)))
    log_score = max(0.0, min(1.0, avg_logprob + 1.0))
    speech_score = 0.0 if speech is None else max(0.0, min(1.0, speech))
    raw = 0.4 * lang + 0.3 * log_score + 0.3 * speech_score
    if status == "REJECTED":
        raw = min(raw, 0.34)
    elif status == "UNCERTAIN":
        raw = min(raw, 0.55)
    return round(raw, 3)


def _non_latin_script(text: str) -> bool:
    import unicodedata
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 2:
        return False
    latin = 0
    for ch in letters:
        if "LATIN" in unicodedata.name(ch, ""):
            latin += 1
    return latin / len(letters) < 0.6


def _audio_profile(scores, hop: float, t0: float, t1: float) -> dict:
    chosen = []
    for index in range(len(scores)):
        start = index * hop
        end = start + 0.96
        if start < t1 and end > t0:
            chosen.append(scores[index])
    if not chosen:
        return {"speech": 0.0, "nonspeech": 0.0, "name": None}
    mean = np.mean(np.stack(chosen), axis=0)
    speech = float(mean[_YAMNET_INDEX["speech"]])
    best_name, best_score = None, 0.0
    for name, column in _NONSPEECH:
        score = float(mean[column])
        if score > best_score:
            best_name, best_score = name, score
    return {"speech": speech, "nonspeech": best_score, "name": best_name}


def drop_mouth_open_during_speech(events: list[dict], phrases: list[list[dict]]) -> list[dict]:
    spans = [(float(phrase[0]["t0"]), float(phrase[-1]["t1"])) for phrase in phrases if phrase]
    kept = []
    for event in events:
        if any(event["t0"] < end and event["t1"] > start for start, end in spans):
            continue
        kept.append(event)
    return kept


def music_evidence_from_bins(bins: list[dict], ids: _Ids | None = None) -> list[dict]:
    """Merge consecutive music bins into evidence. Other labels are not called music."""
    ids = ids or _Ids()
    merged: list[dict] = []
    current = None
    for bin_ in bins:
        if bin_.get("label") != "music":
            current = None
            continue
        if current and abs(float(bin_["t0"]) - float(current["t1"])) < 0.02:
            current["t1"] = float(bin_["t1"])
            current["confidence"] = round(max(current["confidence"], float(bin_.get("confidence") or 0)), 3)
            continue
        current = {
            "id": ids.ev(),
            "type": "music",
            "t0": float(bin_["t0"]),
            "t1": float(bin_["t1"]),
            "trackId": None,
            "confidence": round(float(bin_.get("confidence") or 0), 3),
            "source": "yamnet",
            "payload": {"scoreName": "Music"},
        }
        merged.append(current)
    return merged


def validate_video_analysis(doc: dict) -> list[str]:
    errors: list[str] = []
    source = doc.get("source") or {}
    try:
        duration = float(source.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        errors.append("Källans längd saknas.")
    seen: set[str] = set()
    tracks = {}
    for track in doc.get("tracks") or []:
        tid = str(track.get("id") or "")
        if tid in tracks:
            errors.append(f"Spår-id {tid} är dubblerat.")
        tracks[tid] = track
        if not _match(tid, "trk_"):
            errors.append(f"Spår-id {tid} har fel form.")
        if track.get("kind") not in TRACK_KINDS:
            errors.append(f"Spår {tid} har okänd sort.")
        if track.get("label") is not None:
            errors.append(f"Spår {tid} har ett namn. Analysen sätter inte namn.")
        samples = track.get("samples") or []
        if not samples:
            errors.append(f"Spår {tid} har inga prov.")
        for sample in samples:
            bbox = sample.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or any(not _unit(v) for v in bbox):
                errors.append(f"Spår {tid} har en ruta utanför bilden.")
                break
    word_order: list[str] = []
    for event in doc.get("evidence") or []:
        eid = str(event.get("id") or "")
        if eid in seen:
            errors.append(f"Bevis-id {eid} är dubblerat.")
        seen.add(eid)
        if not _match(eid, "ev_"):
            errors.append(f"Bevis-id {eid} har fel form.")
        if event.get("type") not in EVIDENCE_TYPES:
            errors.append(f"Bevis {eid} har okänd typ.")
        try:
            t0, t1 = float(event["t0"]), float(event["t1"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Bevis {eid} saknar tid.")
            continue
        if t0 < -0.05 or t1 > duration + 0.15 or t1 + 1e-3 < t0:
            errors.append(f"Bevis {eid} ligger utanför filens längd.")
        try:
            conf = float(event["confidence"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Bevis {eid} saknar säkerhet.")
            conf = -1
        if not 0 <= conf <= 1:
            errors.append(f"Bevis {eid} har en säkerhet utanför 0–1.")
        if "trackId" not in event:
            errors.append(f"Bevis {eid} saknar trackId.")
        tid = event.get("trackId")
        if tid is not None and tid not in tracks:
            errors.append(f"Bevis {eid} pekar på okänt spår {tid}.")
        if not event.get("source"):
            errors.append(f"Bevis {eid} saknar källa.")
        if event.get("type") == "speech_word":
            word_order.append(eid)
    position = {eid: index for index, eid in enumerate(word_order)}
    used: set[str] = set()
    for phrase in doc.get("phrases") or []:
        if not _match(str(phrase.get("id") or ""), "ph_"):
            errors.append("En fras har fel id.")
        wids = phrase.get("wordEvidenceIds") or []
        if not wids:
            errors.append(f"Fras {phrase.get('id')} har inga ord.")
            continue
        indexes = [position.get(wid) for wid in wids]
        if any(index is None for index in indexes):
            errors.append(f"Fras {phrase.get('id')} citerar ett ord som inte finns.")
            continue
        expect = list(range(indexes[0], indexes[0] + len(indexes)))
        if indexes != expect:
            errors.append(f"Fras {phrase.get('id')} är inte ett sammanhängande ordspann.")
        if phrase.get("status") not in (None, "SUPPORTED"):
            errors.append(f"Fras {phrase.get('id')} är inte SUPPORTED och får inte ligga bland fraserna.")
        if used.intersection(wids):
            errors.append(f"Fras {phrase.get('id')} återanvänder ord från en annan fras.")
        used.update(wids)
        try:
            if float(phrase["t1"]) + 1e-3 < float(phrase["t0"]):
                errors.append(f"Fras {phrase.get('id')} har en bakvänd tid.")
        except (KeyError, TypeError, ValueError):
            errors.append(f"Fras {phrase.get('id')} saknar tid.")
    phrase_ids = {str(phrase.get("id") or "") for phrase in doc.get("phrases") or []}
    speech_texts = {
        str((event.get("payload") or {}).get("text") or "")
        for event in doc.get("evidence") or []
        if event.get("type") == "speech_phrase"
    }
    for row in doc.get("transcript") or []:
        if row.get("status") not in {"SUPPORTED", "UNCERTAIN", "REJECTED"}:
            errors.append(f"Transkript {row.get('id')} har okänd status.")
            continue
        if row.get("status") == "SUPPORTED":
            continue
        for phrase_id in row.get("phraseIds") or []:
            if phrase_id in phrase_ids:
                errors.append(f"Transkript {row.get('id')} är {row.get('status')} men är kopplat till fras {phrase_id}.")
        blocked = str(row.get("text") or "")
        if blocked and blocked in speech_texts:
            errors.append(f"Transkript {row.get('id')} är {row.get('status')} men texten ligger som talbevis.")
    return errors


def annotate_frames(video: Path, analysis: dict, dest: Path, stem: str, count: int = 4) -> list[Path]:
    """Draw track boxes and the evidence active at a few moments. JPG, 540 px wide."""
    import cv2

    dest.mkdir(parents=True, exist_ok=True)
    times = _annotation_times(analysis, count)
    written = []
    for index, moment in enumerate(times):
        frame = _jpeg_frame(video, moment)
        if frame is None:
            continue
        height, width = frame.shape[:2]
        active = [
            event for event in analysis.get("evidence") or []
            if float(event["t0"]) - 0.08 <= moment <= float(event["t1"]) + 0.08
            and event["type"] not in {"speech_word", "silence"}
        ]
        for track in analysis.get("tracks") or []:
            sample = _nearest_sample(track["samples"], moment)
            if sample is None:
                continue
            x, y, w, h = sample["bbox"]
            p1 = (int(x * width), int(y * height))
            p2 = (int((x + w) * width), int((y + h) * height))
            color = (40, 180, 60) if track["kind"] == "face" else (0, 140, 255)
            cv2.rectangle(frame, p1, p2, color, 2)
            labels = [track["id"]]
            labels += [event["type"] for event in active if event.get("trackId") == track["id"]]
            cv2.putText(frame, " ".join(labels)[:40], (p1[0], max(16, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        loose = [event["type"] for event in active if not event.get("trackId")]
        phrase = next((event.get("payload", {}).get("text") for event in active if event["type"] == "speech_phrase"), "")
        header = f"t={moment:.2f}s " + " ".join(loose[:6])
        if phrase:
            header = f"{header} | {phrase}"[:90]
        cv2.rectangle(frame, (0, 0), (width, 28), (0, 0, 0), -1)
        cv2.putText(frame, header, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        out = dest / f"{stem}-{index}.jpg"
        cv2.imwrite(str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        written.append(out)
    return written


def _annotation_times(analysis: dict, count: int) -> list[float]:
    duration = float(analysis["source"]["duration"])
    preferred = []
    for event in analysis.get("evidence") or []:
        if event["type"] in {"smile", "point", "hand_to_mouth", "held_object", "laughter", "applause", "scream", "mouth_open"}:
            preferred.append(round((float(event["t0"]) + float(event["t1"])) / 2, 3))
    times: list[float] = []
    for moment in preferred:
        if all(abs(moment - have) > 0.45 for have in times):
            times.append(moment)
        if len(times) >= count:
            return times[:count]
    for frac in (0.2, 0.45, 0.7, 0.9):
        moment = round(min(duration - 0.05, max(0.05, duration * frac)), 3)
        if all(abs(moment - have) > 0.45 for have in times):
            times.append(moment)
        if len(times) >= count:
            break
    return times[:count] or [0.1]


def _scenes(path: Path, duration: float, unavailable: list[dict]) -> list[float]:
    try:
        return [round(float(cut), 3) for cut in detect_scenes(path, duration)]
    except Exception as error:
        _unavailable(unavailable, "scenedetect", error)
        return []


def _motion(path: Path, meta: dict, unavailable: list[dict]) -> tuple[list[dict], list[dict]]:
    try:
        import cv2
    except Exception as error:
        _unavailable(unavailable, "farneback", error)
        return [], []
    width, height = _proxy_size(meta, width=320)
    series = []
    prev = None
    index = 0
    try:
        for frame in _frames(path, 4, width, height):
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            moment = round(index / 4, 3)
            if prev is not None:
                flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                dx = float(np.mean(flow[..., 0]))
                dy = float(np.mean(flow[..., 1]))
                energy = float(np.mean(np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)))
                norm = max(1e-6, math.hypot(dx, dy))
                series.append({
                    "t": moment,
                    "energy": round(energy, 4),
                    "dx": round(dx, 4),
                    "dy": round(dy, 4),
                    "dirX": round(dx / norm, 3),
                    "dirY": round(dy / norm, 3),
                })
            prev = gray
            index += 1
    except Exception as error:
        _unavailable(unavailable, "farneback", error)
        return series, []
    if len(series) < 4:
        return series, []
    energies = sorted(item["energy"] for item in series)
    cutoff = max(0.4, energies[int(0.9 * (len(energies) - 1))])
    evidence = []
    for item in series:
        if item["energy"] < cutoff:
            continue
        evidence.append({
            "type": "motion",
            "t0": item["t"],
            "t1": round(min(float(meta["duration"]), item["t"] + 0.25), 3),
            "trackId": None,
            "confidence": round(min(1.0, item["energy"] / cutoff * 0.5), 3),
            "source": "farneback",
            "payload": {"dx": item["dx"], "dy": item["dy"], "energy": item["energy"], "dirX": item["dirX"], "dirY": item["dirY"]},
        })
    return series, evidence


def _faces(path: Path, meta: dict, unavailable: list[dict]):
    """Face tracks only. Darkness, profile, and distance are often missed; that miss is not a fact."""
    loaded = _landmarker("face", unavailable)
    if loaded is None:
        return [], []
    mp, landmarker = loaded
    width, height = _proxy_size(meta, max_side=640)
    detections: list[tuple[float, list[dict]]] = []
    try:
        index = 0
        for frame in _frames(path, SAMPLE_FPS, width, height):
            moment = index / SAMPLE_FPS
            result = landmarker.detect_for_video(_mp_image(mp, frame), int(round(moment * 1000)) + index)
            boxes = []
            blends = result.face_blendshapes or []
            for face_i, landmarks in enumerate(result.face_landmarks or []):
                bbox = _bbox_from_landmarks(landmarks)
                if bbox is None:
                    continue
                scores = _blend_map(blends[face_i]) if face_i < len(blends) else {}
                smile = _mean_score(scores, ("mouthSmileLeft", "mouthSmileRight"))
                jaw = scores.get("jawOpen")
                boxes.append({"bbox": bbox, "smile": smile, "jaw": jaw})
            detections.append((moment, boxes))
            index += 1
    except Exception as error:
        _unavailable(unavailable, "mediapipe_face", error)
        return [], []
    finally:
        _close(landmarker)
    tracks = _link(detections, "f")
    events = []
    for track in tracks:
        if not track["samples"]:
            continue
        events.append({
            "type": "face_track",
            "t0": round(track["samples"][0]["t"], 3),
            "t1": round(track["samples"][-1]["t"] + 1 / SAMPLE_FPS, 3),
            "trackId": track["id"],
            "confidence": 0.7,
            "source": "mediapipe_face",
            "payload": {},
        })
        events.extend(_score_runs(track, "smile", SMILE_MIN, SMILE_MIN_S, "smile", "mediapipe_face"))
        events.extend(_score_runs(track, "jaw", JAW_MIN, JAW_MIN_S, "mouth_open", "mediapipe_face"))
    return tracks, events


def _hands(path: Path, meta: dict, face_tracks: list[dict], unavailable: list[dict]):
    """Hand tracks only. Boxes on arms or torso are a known limit, not a named object."""
    loaded = _landmarker("hand", unavailable)
    if loaded is None:
        return [], []
    mp, landmarker = loaded
    width, height = _proxy_size(meta, max_side=640)
    detections: list[tuple[float, list[dict]]] = []
    try:
        index = 0
        for frame in _frames(path, SAMPLE_FPS, width, height):
            moment = index / SAMPLE_FPS
            result = landmarker.detect_for_video(_mp_image(mp, frame), int(round(moment * 1000)) + index)
            boxes = []
            for landmarks in result.hand_landmarks or []:
                bbox = _bbox_from_landmarks(landmarks)
                if bbox is None or max(bbox[2], bbox[3]) > 0.5:
                    continue
                boxes.append({
                    "bbox": bbox,
                    "point": _is_pointing(landmarks),
                    "angle": _point_angle(landmarks),
                    "at_mouth": _near_mouth(bbox, landmarks, face_tracks, moment),
                    "grip": _is_grip(landmarks),
                })
            detections.append((moment, boxes))
            index += 1
    except Exception as error:
        _unavailable(unavailable, "mediapipe_hand", error)
        return [], []
    finally:
        _close(landmarker)
    tracks = _link(detections, "h")
    events = []
    for track in tracks:
        point = _flag_runs(track, "point", "point", "mediapipe_hand", stable_angle=True)
        mouth = _flag_runs(track, "at_mouth", "hand_to_mouth", "mediapipe_hand")
        grip = _flag_runs(track, "grip", "held_object", "mediapipe_hand", confidence=0.55)
        mouth_spans = [(event["t0"], event["t1"]) for event in mouth]
        point = [event for event in point if not _overlaps_any(event, mouth_spans)]
        point_spans = [(event["t0"], event["t1"]) for event in point]
        grip = [event for event in grip if not _overlaps_any(event, mouth_spans + point_spans)]
        events.extend(point + mouth + grip)
    return tracks, events


def _read_transcript(path: Path, has_audio: bool, unavailable: list[dict]) -> dict:
    empty = {"language": None, "languageProbability": None, "segments": []}
    if not has_audio:
        _unavailable(unavailable, "whisper", "Filen har inget ljudspår.")
        return empty
    try:
        detailed = transcribe_detailed(path)
    except Exception as error:
        _unavailable(unavailable, "whisper", error)
        return empty
    finally:
        _release_whisper()
    segments = []
    for segment in detailed.get("segments") or []:
        words = []
        for word in segment.get("words") or []:
            text = str(word.get("text") or "").strip()
            if not text:
                continue
            start = float(word.get("t0", word.get("start")))
            end = float(word.get("t1", word.get("end", start)))
            if end < start:
                end = start
            words.append({"text": text, "t0": round(start, 3), "t1": round(end, 3)})
        kept = _drop_token_loops(words, unavailable)
        segments.append({
            "text": " ".join(word["text"] for word in kept) if kept else str(segment.get("text") or ""),
            "t0": segment.get("t0"),
            "t1": segment.get("t1"),
            "avgLogprob": segment.get("avgLogprob"),
            "noSpeechProb": segment.get("noSpeechProb"),
            "compressionRatio": segment.get("compressionRatio"),
            "hallucination": bool(segment.get("hallucination")),
            "loop": len(kept) < len(words),
            "words": kept,
        })
    return {
        "language": detailed.get("language"),
        "languageProbability": detailed.get("languageProbability"),
        "segments": segments,
    }


def _drop_token_loops(words: list[dict], unavailable: list[dict]) -> list[dict]:
    """Whisper at temperature 0 can still repeat one token. That is not a phrase."""
    kept: list[dict] = []
    index = 0
    dropped = 0
    while index < len(words):
        token = _norm_token(words[index]["text"])
        end = index + 1
        while end < len(words) and _norm_token(words[end]["text"]) == token:
            end += 1
        if end - index >= 6:
            dropped += end - index
            index = end
            continue
        kept.extend(words[index:end])
        index = end
    if dropped:
        _unavailable(unavailable, "whisper", f"Samma ord upprepades {dropped} gånger och togs bort. Det är en Whisper-loop, inte en fras.")
    return kept


def _norm_token(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _audio(path: Path, duration: float, has_audio: bool, words: list[dict], unavailable: list[dict], prepared: dict | None = None):
    if not has_audio:
        return [], []
    if prepared and prepared.get("audio") is not None:
        audio = prepared["audio"]
        scores = prepared.get("scores")
        hop = float(prepared.get("hop") or 0.48)
    else:
        audio = _load_audio(path)
        scores = _yamnet_scores(audio, unavailable)
        hop = 0.48
        if scores is not None and len(scores):
            hop = max(0.05, (len(audio) / 16000) / len(scores))
    bins = []
    step = int(0.5 * 16000)
    for start in range(0, max(1, len(audio) - 800), step):
        chunk = audio[start:start + step]
        if len(chunk) < 1600:
            continue
        t0 = round(start / 16000, 3)
        t1 = round(min(duration, (start + len(chunk)) / 16000), 3)
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        speech = music = singing = 0.0
        if scores is not None:
            speech, music, singing = _yamnet_bin(scores, hop, t0, t1)
        label, confidence = _bin_label(rms, speech, music, singing, _covers(words, t0, t1), scores is not None)
        bins.append({"t0": t0, "t1": t1, "label": label, "confidence": round(confidence, 3)})
    events = []
    events.extend(_spans_from_bins(bins, "silence", "silence", "energy_vad"))
    events.extend(music_evidence_from_bins(bins))
    # music_evidence_from_bins assigns its own ids; the builder replaces ids.
    for event in events:
        event.pop("id", None)
    if scores is not None:
        events.extend(_class_events(scores, hop, duration, "laughter", _YAMNET_INDEX["laughter"], "Laughter"))
        events.extend(_class_events(scores, hop, duration, "applause", _YAMNET_INDEX["applause"], "Applause"))
        events.extend(_class_events(scores, hop, duration, "scream", _YAMNET_INDEX["scream"], "Screaming"))
        events.extend(_onsets(audio, duration))
    else:
        events.extend(_onsets(audio, duration))
    return bins, events


def _bin_label(rms: float, speech: float, music: float, singing: float, words: bool, have_classifier: bool):
    if rms < SILENCE_RMS and not words:
        return "silence", round(min(1.0, (SILENCE_RMS - rms) / SILENCE_RMS), 3)
    if have_classifier:
        music_like = max(music, singing * 0.9)
        if music_like >= 0.30 and music_like > speech + 0.05:
            return "music", round(min(1.0, music_like), 3)
        if speech >= 0.2 or words:
            return "speech", round(min(1.0, max(speech, 0.6 if words else speech)), 3)
        if rms < SILENCE_RMS * 2:
            return "silence", 0.4
        return "unknown", 0.0
    if words:
        return "speech", 0.6
    if rms < SILENCE_RMS * 2:
        return "silence", 0.4
    return "unknown", 0.0


def _yamnet_scores(audio: np.ndarray, unavailable: list[dict]):
    loaded = _import_onnxruntime()
    if loaded is None:
        _unavailable(unavailable, "yamnet", "onnxruntime är inte installerat.")
        return None
    model = _ensure_model("yamnet.onnx", _YAMNET_URL)
    if model is None:
        _unavailable(unavailable, "yamnet", "YAMNet-modellen kunde inte hämtas.")
        return None
    session = None
    try:
        session = loaded.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        scores, _emb, _mel = session.run(None, {"waveform": np.ascontiguousarray(audio, dtype=np.float32)})
        return np.asarray(scores)
    except Exception as error:
        _unavailable(unavailable, "yamnet", error)
        return None
    finally:
        del session
        gc.collect()


def _yamnet_bin(scores: np.ndarray, hop: float, t0: float, t1: float) -> tuple[float, float, float]:
    chosen = []
    for index in range(len(scores)):
        start = index * hop
        end = start + 0.96
        if start < t1 and end > t0:
            chosen.append(scores[index])
    if not chosen:
        return 0.0, 0.0, 0.0
    mean = np.mean(np.stack(chosen), axis=0)
    return float(mean[_YAMNET_INDEX["speech"]]), float(mean[_YAMNET_INDEX["music"]]), float(mean[_YAMNET_INDEX["singing"]])


def _class_events(scores: np.ndarray, hop: float, duration: float, kind: str, column: int, score_name: str) -> list[dict]:
    hot = []
    for index, row in enumerate(scores):
        score = float(row[column])
        if score >= YAMNET_MIN:
            hot.append((index * hop, min(duration, index * hop + 0.96), score))
    return _merge_hits(hot, kind, "yamnet", YAMNET_MIN_S, {"scoreName": score_name})


def _merge_hits(hits: list[tuple[float, float, float]], kind: str, source: str, min_s: float, payload: dict) -> list[dict]:
    if not hits:
        return []
    groups = []
    start, end, score = hits[0]
    for t0, t1, value in hits[1:]:
        if t0 <= end + 0.05:
            end = max(end, t1)
            score = max(score, value)
            continue
        groups.append((start, end, score))
        start, end, score = t0, t1, value
    groups.append((start, end, score))
    events = []
    for t0, t1, score in groups:
        if t1 - t0 < min_s:
            continue
        events.append({
            "type": kind,
            "t0": round(t0, 3),
            "t1": round(t1, 3),
            "trackId": None,
            "confidence": round(min(1.0, score), 3),
            "source": source,
            "payload": dict(payload),
        })
    return events


def _onsets(audio: np.ndarray, duration: float) -> list[dict]:
    step = int(0.5 * 16000)
    events = []
    prev = None
    for start in range(0, max(1, len(audio) - step), step):
        chunk = audio[start:start + step]
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
        if prev is not None and rms - prev > 0.02 and rms > prev * 1.8 and rms > SILENCE_RMS * 2:
            t0 = round(start / 16000, 3)
            events.append({
                "type": "onset",
                "t0": t0,
                "t1": round(min(duration, t0 + 0.15), 3),
                "trackId": None,
                "confidence": round(min(1.0, (rms - prev) / 0.05), 3),
                "source": "energy_vad",
                "payload": {},
            })
        prev = rms
    return events


def _spans_from_bins(bins: list[dict], label: str, kind: str, source: str) -> list[dict]:
    events = []
    current = None
    for bin_ in bins:
        if bin_["label"] != label:
            current = None
            continue
        if current and abs(bin_["t0"] - current["t1"]) < 0.02:
            current["t1"] = bin_["t1"]
            continue
        current = {
            "type": kind,
            "t0": bin_["t0"],
            "t1": bin_["t1"],
            "trackId": None,
            "confidence": float(bin_["confidence"]),
            "source": source,
            "payload": {},
        }
        events.append(current)
    return events


def _landmarker(kind: str, unavailable: list[dict]):
    imported = _import_mediapipe()
    if imported is None:
        _unavailable(unavailable, f"mediapipe_{kind}", "mediapipe är inte installerat.")
        return None
    mp, vision, base = imported
    url = _FACE_URL if kind == "face" else _HAND_URL
    filename = "face_landmarker.task" if kind == "face" else "hand_landmarker.task"
    model = _ensure_model(filename, url)
    if model is None:
        _unavailable(unavailable, f"mediapipe_{kind}", "Modellen kunde inte hämtas.")
        return None
    options_cls = vision.FaceLandmarkerOptions if kind == "face" else vision.HandLandmarkerOptions
    maker = vision.FaceLandmarker if kind == "face" else vision.HandLandmarker
    kwargs = {
        "base_options": base.BaseOptions(model_asset_path=str(model)),
        "running_mode": vision.RunningMode.VIDEO,
    }
    if kind == "face":
        kwargs["num_faces"] = 4
        kwargs["output_face_blendshapes"] = True
    else:
        kwargs["num_hands"] = 2
    try:
        return mp, maker.create_from_options(options_cls(**kwargs))
    except Exception as error:
        _unavailable(unavailable, f"mediapipe_{kind}", error)
        return None


def _link(detections: list[tuple[float, list[dict]]], prefix: str) -> list[dict]:
    tracks: list[dict] = []
    next_id = 0
    for moment, boxes in detections:
        used = set()
        for box in boxes:
            best_i, best_score = -1, 0.25
            for index, track in enumerate(tracks):
                if index in used:
                    continue
                last = track["samples"][-1]
                if moment - last["t"] > 0.6:
                    continue
                score = _iou(box["bbox"], last["bbox"])
                if score > best_score:
                    best_i, best_score = index, score
            sample = {"t": round(moment, 3), **box}
            if best_i >= 0:
                tracks[best_i]["samples"].append(sample)
                used.add(best_i)
            else:
                tracks.append({"id": f"trk_{prefix}{next_id}", "samples": [sample]})
                next_id += 1
                used.add(len(tracks) - 1)
    return [track for track in tracks if len(track["samples"]) >= 3]


def _score_runs(track: dict, key: str, threshold: float, min_s: float, kind: str, source: str) -> list[dict]:
    events = []
    run: list[dict] = []

    def flush() -> None:
        nonlocal run
        if not run:
            return
        t0 = run[0]["t"]
        t1 = run[-1]["t"] + 1 / SAMPLE_FPS
        if t1 - t0 >= min_s:
            scores = [float(sample[key]) for sample in run if sample.get(key) is not None]
            events.append({
                "type": kind,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "trackId": track["id"],
                "confidence": round(min(1.0, sum(scores) / len(scores)), 3),
                "source": source,
                "payload": {},
            })
        run = []

    for sample in track["samples"]:
        value = sample.get(key)
        if value is not None and float(value) >= threshold:
            run.append(sample)
        else:
            flush()
    flush()
    return events


def _flag_runs(track: dict, key: str, kind: str, source: str, confidence: float = 0.7, stable_angle: bool = False) -> list[dict]:
    events = []
    run: list[dict] = []

    def flush() -> None:
        nonlocal run
        if not run:
            return
        t0 = run[0]["t"]
        t1 = run[-1]["t"] + 1 / SAMPLE_FPS
        angles = [sample["angle"] for sample in run if sample.get("angle") is not None]
        steady = (not stable_angle) or _angle_span(angles) <= math.radians(35)
        if t1 - t0 >= GESTURE_MIN_S and steady:
            events.append({
                "type": kind,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "trackId": track["id"],
                "confidence": confidence,
                "source": source,
                "payload": {},
            })
        run = []

    for sample in track["samples"]:
        if sample.get(key):
            run.append(sample)
        else:
            flush()
    flush()
    return events


def _is_pointing(landmarks) -> bool:
    return _extended(landmarks, 8, 6) and not _extended(landmarks, 12, 10) and not _extended(landmarks, 16, 14) and not _extended(landmarks, 20, 18)


def _is_grip(landmarks) -> bool:
    # A closed hand is the only cheap sign of holding. It is not a named object.
    return not any(_extended(landmarks, tip, pip) for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)))


def _extended(landmarks, tip: int, pip: int) -> bool:
    return _dist(landmarks[tip], landmarks[0]) > _dist(landmarks[pip], landmarks[0]) * 1.15


def _point_angle(landmarks) -> float:
    wx, wy = _xy(landmarks[0])
    tx, ty = _xy(landmarks[8])
    return math.atan2(ty - wy, tx - wx)


def _near_mouth(bbox, landmarks, face_tracks: list[dict], moment: float) -> bool:
    point = _xy(landmarks[8])
    for track in face_tracks:
        sample = _nearest_sample(track["samples"], moment, slack=0.25)
        if sample is None:
            continue
        x, y, w, h = sample["bbox"]
        mouth = (x + w / 2, y + h * 0.72)
        reach = max(0.12, 0.55 * max(w, h))
        if math.hypot(point[0] - mouth[0], point[1] - mouth[1]) <= reach:
            return True
    return False


def _angle_span(angles: list[float]) -> float:
    if len(angles) < 2:
        return 0.0
    mean = math.atan2(sum(math.sin(a) for a in angles), sum(math.cos(a) for a in angles))
    return max(abs(math.atan2(math.sin(a - mean), math.cos(a - mean))) for a in angles)


def _bbox_from_landmarks(landmarks) -> tuple[float, float, float, float] | None:
    xs = [_xy(point)[0] for point in landmarks]
    ys = [_xy(point)[1] for point in landmarks]
    if not xs:
        return None
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad_x = (x1 - x0) * 0.08
    pad_y = (y1 - y0) * 0.08
    box = (_clamp01(x0 - pad_x), _clamp01(y0 - pad_y), _clamp01(x1 + pad_x) - _clamp01(x0 - pad_x), _clamp01(y1 + pad_y) - _clamp01(y0 - pad_y))
    if box[2] <= 0.01 or box[3] <= 0.01:
        return None
    return tuple(round(v, 4) for v in box)


def _blend_map(blendshapes) -> dict:
    scores = {}
    items = getattr(blendshapes, "categories", None)
    if items is None:
        items = blendshapes or []
    for category in items:
        name = getattr(category, "category_name", None)
        if name:
            scores[name] = float(getattr(category, "score", 0) or 0)
    return scores


def _mean_score(scores: dict, names: tuple[str, ...]) -> float | None:
    values = [scores[name] for name in names if name in scores]
    if not values:
        return None
    return sum(values) / len(values)


def _export_track(track: dict, kind: str) -> dict:
    return {
        "id": track["id"],
        "kind": kind,
        "label": None,
        "samples": [
            {"t": round(float(sample["t"]), 3), "bbox": [round(float(v), 4) for v in sample["bbox"]]}
            for sample in track["samples"]
        ],
    }


def _frames(path: Path, fps: float, width: int, height: int):
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps={fps},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frame_bytes = width * height * 3
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()
    finally:
        proc.stdout.close()
        proc.wait()


def _proxy_size(meta: dict, width: int | None = None, max_side: int | None = None) -> tuple[int, int]:
    src_w, src_h = int(meta["width"]), int(meta["height"])
    if max_side:
        scale = max_side / max(src_w, src_h)
        return _even(src_w * scale), _even(src_h * scale)
    out_w = _even(width or 320)
    return out_w, _even(src_h * (out_w / src_w))


def _even(value: float) -> int:
    number = max(2, int(value))
    return number if number % 2 == 0 else number - 1


def _mp_image(mp, frame: np.ndarray):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))


def _import_mediapipe():
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core import base_options as base
        return mp, vision, base
    except Exception as error:
        log.warning("mediapipe saknas: %s", error)
        return None


def _import_onnxruntime():
    try:
        import onnxruntime
        return onnxruntime
    except Exception as error:
        log.warning("onnxruntime saknas: %s", error)
        return None


def _ensure_model(filename: str, url: str) -> Path | None:
    import os
    root = Path(os.environ.get("CAPCUT_MODEL_DIR", Path.home() / ".cache" / "capcutpro" / "models"))
    root.mkdir(parents=True, exist_ok=True)
    dest = root / filename
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    except Exception as error:
        log.warning("kunde inte hämta %s: %s", filename, error)
        tmp.unlink(missing_ok=True)
        return None
    return dest if dest.exists() else None


def _release_whisper() -> None:
    from . import analysis as analysis_mod
    analysis_mod._whisper = None
    gc.collect()


def _close(landmarker) -> None:
    try:
        landmarker.close()
    except Exception:
        pass
    del landmarker
    gc.collect()


def _unavailable(bucket: list[dict], step: str, error) -> None:
    reason = str(error)
    log.warning("analysis_v2 %s otillgänglig: %s", step, reason)
    bucket.append({"step": step, "reason": reason})


def _covers(words: list[dict], t0: float, t1: float) -> bool:
    return any(float(word["t0"]) < t1 and float(word["t1"]) > t0 for word in words)


def _overlaps_any(event: dict, spans: list[tuple[float, float]]) -> bool:
    return any(event["t0"] < end and event["t1"] > start for start, end in spans)


def _nearest_sample(samples: list[dict], moment: float, slack: float = 0.2):
    best = None
    best_gap = slack
    for sample in samples:
        gap = abs(float(sample["t"]) - moment)
        if gap <= best_gap:
            best, best_gap = sample, gap
    return best


def _jpeg_frame(video: Path, moment: float):
    import cv2
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{moment:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=540:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
        capture_output=True,
        check=False,
    ).stdout
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _xy(point) -> tuple[float, float]:
    if hasattr(point, "x"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def _dist(a, b) -> float:
    ax, ay = _xy(a)
    bx, by = _xy(b)
    return math.hypot(ax - bx, ay - by)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _unit(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return 0 <= number <= 1


def _match(value: str, prefix: str) -> bool:
    import re
    return bool(re.fullmatch(prefix + r"[a-z0-9]+", value))


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rss_mb() -> float | None:
    return _proc_kb("VmRSS:")


def _hwm_mb() -> float | None:
    return _proc_kb("VmHWM:")


def _proc_kb(label: str) -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(label):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None
