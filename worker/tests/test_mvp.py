"""Storyboard and plan checks. No clip-specific names or source times."""

from app.mvp import build_storyboard, plan_from_storyboard
from app.plan_ops import min_readable_seconds


def _analysis() -> dict:
    return {
        "source": {"duration": 8.0, "width": 1080, "height": 1920, "fps": 30, "hasAudio": True},
        "scenes": [3.2],
        "tracks": [{
            "id": "trk_f0",
            "kind": "face",
            "label": None,
            "samples": [
                {"t": 1.2, "bbox": [0.3, 0.2, 0.2, 0.25]},
                {"t": 4.4, "bbox": [0.32, 0.2, 0.2, 0.25]},
                {"t": 4.6, "bbox": [0.32, 0.2, 0.2, 0.25]},
            ],
        }],
        "evidence": [
            {"id": "ev_1", "type": "smile", "t0": 4.2, "t1": 5.1, "trackId": "trk_f0", "confidence": 0.8, "source": "mediapipe_face", "payload": {}},
            {"id": "ev_2", "type": "scene_cut", "t0": 3.2, "t1": 3.25, "trackId": None, "confidence": 0.8, "source": "scenedetect", "payload": {}},
        ],
        "phrases": [
            {"id": "ph_1", "text": "look at that", "t0": 1.0, "t1": 2.2, "status": "SUPPORTED", "wordEvidenceIds": ["ev_w"]},
            {"id": "ph_2", "text": "You look beautiful", "t0": 4.0, "t1": 5.4, "status": "SUPPORTED", "wordEvidenceIds": ["ev_x"]},
        ],
        "transcript": [
            {"id": "tr_1", "status": "SUPPORTED", "text": "look at that", "t0": 1.0, "t1": 2.2, "phraseIds": ["ph_1"]},
            {"id": "tr_2", "status": "SUPPORTED", "text": "You look beautiful", "t0": 4.0, "t1": 5.4, "phraseIds": ["ph_2"]},
            {"id": "tr_3", "status": "REJECTED", "text": "invented lyrics here now", "t0": 6.0, "t1": 7.4, "phraseIds": []},
        ],
        "limitations": ["Ansikten i mörker missas ofta."],
    }


def test_supported_phrase_stays_whole_and_rejected_text_stays_out(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    board = build_storyboard(_analysis(), "make a short")
    plan = plan_from_storyboard(board, _analysis())
    texts = [effect["text"] for effect in plan["effects"] if effect.get("text")]
    assert "You look beautiful" in texts
    assert "look at that" in texts
    assert "invented lyrics here now" not in texts
    assert all(effect.get("kind") != "story" or effect["text"] in {"look at that", "You look beautiful"} for effect in plan["effects"])
    assert plan["audio"]["keepOriginal"] is True
    assert plan["audio"]["music"] is False
    assert plan["clips"][0]["label"] == "hook"
    assert plan["clips"][0]["sourceStart"] >= 3.5
    assert any(clip["label"] == "rewind" for clip in plan["clips"])
    assert any(clip["label"] == "replay" for clip in plan["clips"])
    story = next(effect for effect in plan["effects"] if effect.get("text") == "You look beautiful")
    assert story["end"] - story["start"] + 1e-6 >= min_readable_seconds(story["text"])
    assert any(effect["type"] == "sound_effect" and effect["sfx"] == "impact" for effect in plan["effects"])
    assert not any(effect.get("sfx") == "airhorn" for effect in plan["effects"])


def test_rejected_speech_still_builds_a_visual_short(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _analysis()
    doc["phrases"] = []
    doc["transcript"] = [
        {"id": "tr_1", "status": "REJECTED", "text": "invented lyrics here now", "t0": 0.2, "t1": 2.0, "phraseIds": []},
    ]
    doc["evidence"] = [event for event in doc["evidence"] if event["type"] != "smile"]
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    assert plan["clips"]
    texts = " ".join(effect.get("text") or "" for effect in plan["effects"])
    assert "invented" not in texts.lower()
    assert any(effect.get("kind") == "headline" for effect in plan["effects"])
    assert not any(clip["label"] == "replay" for clip in plan["clips"])


def test_speed_rounding_cannot_make_a_phrase_unreadable(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _analysis()
    doc["phrases"].append({
        "id": "ph_3",
        "text": "You're going to love it",
        "t0": 5.92,
        "t1": 6.94,
        "status": "SUPPORTED",
        "wordEvidenceIds": ["ev_y"],
    })
    doc["transcript"].append({
        "id": "tr_4",
        "status": "SUPPORTED",
        "text": "You're going to love it",
        "t0": 5.92,
        "t1": 6.94,
        "phraseIds": ["ph_3"],
    })
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    story = next(effect for effect in plan["effects"] if effect.get("text") == "You're going to love it")
    need = min_readable_seconds(story["text"])
    assert not (story["end"] - story["start"] + 1e-3 < need)


def test_qa_flags_a_rejected_line(tmp_path, monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    # QA on a missing file is not the point: check the text gate through the plan builder.
    doc = _analysis()
    board = build_storyboard(doc, "make a short")
    board["blockedTexts"].append("you look beautiful")
    try:
        plan_from_storyboard(board, doc)
    except RuntimeError as error:
        assert "Avvisad" in str(error)
    else:
        raise AssertionError("rejected phrase text was allowed into the plan")
    assert tmp_path is not None
