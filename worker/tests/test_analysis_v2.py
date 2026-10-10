"""Generic checks for VideoAnalysis v2. No clip-specific names or times."""

import subprocess
from pathlib import Path

from app.analysis_v2 import (
    build_video_analysis,
    drop_mouth_open_during_speech,
    music_evidence_from_bins,
    segment_phrases,
    validate_video_analysis,
)
from app.store import Store


def _word(text: str, t0: float, t1: float | None = None) -> dict:
    return {"text": text, "t0": t0, "t1": t0 + 0.2 if t1 is None else t1}


def _phrase_texts(words: list[dict]) -> list[str]:
    return [" ".join(word["text"] for word in phrase) for phrase in segment_phrases(words)]


def test_phrases_do_not_cross_a_pause_or_punctuation():
    words = [
        _word("look", 0.10, 0.30),
        _word("at", 0.30, 0.50),
        _word("that.", 0.50, 0.80),
        _word("hello", 1.50, 1.80),
        _word("there", 1.80, 2.10),
    ]
    texts = _phrase_texts(words)
    assert texts == ["look at that.", "hello there"]
    for phrase in segment_phrases(words):
        ids_proxy = [word["text"] for word in phrase]
        assert not ("that." in ids_proxy and "hello" in ids_proxy)


def test_a_repeated_token_loop_is_dropped_and_not_glued_into_phrases():
    from app.analysis_v2 import _drop_token_loops

    looped = [_word("please,", i * 0.1) for i in range(8)]
    kept_tail = _word("hello", 2.0, 2.2)
    unavailable: list[dict] = []
    kept = _drop_token_loops(looped + [kept_tail], unavailable)
    assert [word["text"] for word in kept] == ["hello"]
    assert any(item["step"] == "whisper" for item in unavailable)
    assert _phrase_texts(kept) == ["hello"]

    short = [_word("ok,", i * 0.1) for i in range(5)]
    assert [word["text"] for word in _drop_token_loops(short, [])] == ["ok,"] * 5


def test_long_run_splits_at_six_words_without_reusing_them():
    words = [_word(f"w{i}", i * 0.2) for i in range(8)]
    phrases = segment_phrases(words)
    assert [len(phrase) for phrase in phrases] == [6, 2]
    flat = [word["text"] for phrase in phrases for word in phrase]
    assert flat == [word["text"] for word in words]
    assert len(flat) == len(set(flat))


def test_schema_accepts_a_contiguous_phrase_and_rejects_a_join():
    doc = _valid_doc()
    assert validate_video_analysis(doc) == []

    duplicate = _valid_doc()
    duplicate["evidence"].append(dict(duplicate["evidence"][0]))
    assert any("dubblerat" in error for error in validate_video_analysis(duplicate))

    late = _valid_doc()
    late["evidence"][0]["t1"] = 9
    assert any("utanför" in error for error in validate_video_analysis(late))

    empty = _valid_doc()
    empty["tracks"][0]["samples"] = []
    assert any("inga prov" in error for error in validate_video_analysis(empty))

    joined = _valid_doc()
    joined["evidence"].append({
        "id": "ev_4",
        "type": "speech_word",
        "t0": 1.2,
        "t1": 1.4,
        "trackId": None,
        "confidence": 0.6,
        "source": "whisper",
        "payload": {"text": "on"},
    })
    joined["phrases"] = [{
        "id": "ph_1",
        "text": "look on",
        "t0": 0.0,
        "t1": 1.4,
        "wordEvidenceIds": ["ev_1", "ev_4"],
    }]
    assert any("sammanhängande" in error for error in validate_video_analysis(joined))


def test_music_bins_are_flagged_and_speech_bins_are_not():
    evid = music_evidence_from_bins([
        {"t0": 0.0, "t1": 0.5, "label": "music", "confidence": 0.8},
        {"t0": 0.5, "t1": 1.0, "label": "music", "confidence": 0.6},
        {"t0": 1.0, "t1": 1.5, "label": "speech", "confidence": 0.9},
        {"t0": 2.0, "t1": 2.5, "label": "music", "confidence": 0.4},
    ])
    assert [(item["t0"], item["t1"]) for item in evid] == [(0.0, 1.0), (2.0, 2.5)]
    assert {item["type"] for item in evid} == {"music"}


def test_open_mouth_during_a_phrase_is_not_kept():
    words = [_word("hello", 1.0, 1.4)]
    phrases = segment_phrases(words)
    events = [
        {"type": "mouth_open", "t0": 1.05, "t1": 1.3, "trackId": "trk_f0", "confidence": 0.5, "source": "mediapipe_face"},
        {"type": "mouth_open", "t0": 2.4, "t1": 2.8, "trackId": "trk_f0", "confidence": 0.5, "source": "mediapipe_face"},
    ]
    kept = drop_mouth_open_during_speech(events, phrases)
    assert [event["t0"] for event in kept] == [2.4]


def test_missing_libraries_add_no_invented_events(tmp_path: Path, monkeypatch):
    source = tmp_path / "plain.mp4"
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x224466:s=160x160:r=10:d=3",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        stdout=subprocess.DEVNULL,
    )
    monkeypatch.setattr("app.analysis_v2._import_mediapipe", lambda: None)
    monkeypatch.setattr("app.analysis_v2._import_onnxruntime", lambda: None)
    monkeypatch.setattr(
        "app.analysis_v2.transcribe",
        lambda path: {"words": [
            {"text": "look", "start": 0.1, "end": 0.3},
            {"text": "at", "start": 0.3, "end": 0.5},
            {"text": "that.", "start": 0.5, "end": 0.8},
            {"text": "hello", "start": 1.5, "end": 1.8},
        ]},
    )
    doc = build_video_analysis(source)
    types = {event["type"] for event in doc["evidence"]}
    assert types.isdisjoint({"smile", "mouth_open", "point", "hand_to_mouth", "held_object", "laughter", "applause", "scream", "music"})
    steps = {item["step"] for item in doc["unavailable"]}
    assert "mediapipe_face" in steps
    assert "mediapipe_hand" in steps
    assert "yamnet" in steps
    assert _phrase_texts([
        {"text": event["payload"]["text"], "t0": event["t0"], "t1": event["t1"]}
        for event in doc["evidence"] if event["type"] == "speech_word"
    ]) == ["look at that.", "hello"]
    assert validate_video_analysis(doc) == []
    assert doc["source"]["duration"] > 2


def test_v2_cache_does_not_touch_the_v1_analysis_file(tmp_path: Path, monkeypatch):
    source = tmp_path / "tiny.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    project = Store(tmp_path / "projects").create("tiny.mp4", source)
    document = {"version": 2, "source": {"sha256": project.original_hash, "duration": 0.4}, "tracks": [], "evidence": [], "phrases": []}
    monkeypatch.setattr("app.analysis_v2.build_video_analysis", lambda path, sha256=None: document)
    first = project.analyze_v2()
    assert first["ok"] is True and first["cached"] is False
    assert (project.folder / "analysis_v2.json").exists()
    assert not (project.folder / "analysis.json").exists()
    second = project.analyze_v2()
    assert second["cached"] is True


def _valid_doc() -> dict:
    return {
        "version": 2,
        "source": {"duration": 4.0, "width": 100, "height": 100, "fps": 30, "hasAudio": True, "sha256": "abc"},
        "scenes": [],
        "tracks": [{
            "id": "trk_f0",
            "kind": "face",
            "label": None,
            "samples": [{"t": 0.2, "bbox": [0.1, 0.1, 0.2, 0.2]}],
        }],
        "evidence": [
            {"id": "ev_1", "type": "speech_word", "t0": 0.0, "t1": 0.2, "trackId": None, "confidence": 0.6, "source": "whisper", "payload": {"text": "look"}},
            {"id": "ev_2", "type": "speech_word", "t0": 0.2, "t1": 0.4, "trackId": None, "confidence": 0.6, "source": "whisper", "payload": {"text": "at"}},
            {"id": "ev_3", "type": "smile", "t0": 0.2, "t1": 0.5, "trackId": "trk_f0", "confidence": 0.8, "source": "mediapipe_face", "payload": {}},
        ],
        "phrases": [{"id": "ph_1", "text": "look at", "t0": 0.0, "t1": 0.4, "wordEvidenceIds": ["ev_1", "ev_2"]}],
        "motion": [],
        "audioBins": [],
        "unavailable": [],
    }
