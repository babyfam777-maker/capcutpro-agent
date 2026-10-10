"""Storyboard and plan checks. No clip-specific names or source times."""

from app.export_spec import EXPORT_SPEC, duration_requirement, moov_before_mdat
from app.mvp import build_storyboard, plan_from_storyboard
from app.plan_ops import min_readable_seconds
from app.render import SAFE_BOTTOM, SAFE_SIDE, SAFE_TOP
from app.config import FPS, OUT_H, OUT_W


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


def test_echoed_model_reply_keeps_the_reaction_emoji(monkeypatch):
    monkeypatch.setattr(
        "app.mvp._ask_model",
        lambda prompt, fallback: {"prompt": prompt, "fallback": fallback},
    )
    board = build_storyboard(_analysis(), "make a short")
    assert board["fields"]["emoji"] == "😳"
    assert board["fields"]["llm"] is False
    assert board["fields"]["hook"] == "LOOK BEAUTIFUL"
    assert board["fields"]["caption_source"] == "fallback"
    assert board["fields"]["caption_reason"]
    plan = plan_from_storyboard(board, _analysis())
    assert plan["captionSource"]["source"] == "fallback"
    assert any(effect.get("type") == "emoji" and effect.get("emoji") == "😳" for effect in plan["effects"])


def test_fragment_and_duplicate_captions_are_not_factual(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _analysis()
    doc["phrases"] = [
        {"id": "ph_1", "text": "I'm going to go", "t0": 1.0, "t1": 2.2, "status": "SUPPORTED", "wordEvidenceIds": []},
        {"id": "ph_2", "text": "The legs bit", "t0": 2.4, "t1": 3.1, "status": "SUPPORTED", "wordEvidenceIds": []},
        {"id": "ph_3", "text": "I am going to go", "t0": 3.4, "t1": 4.4, "status": "SUPPORTED", "wordEvidenceIds": []},
        {"id": "ph_4", "text": "You look beautiful", "t0": 4.6, "t1": 5.8, "status": "SUPPORTED", "wordEvidenceIds": []},
    ]
    doc["transcript"] = [
        {"id": "tr_1", "status": "SUPPORTED", "text": "I'm going to go", "t0": 1.0, "t1": 2.2, "phraseIds": ["ph_1"]},
        {"id": "tr_2", "status": "SUPPORTED", "text": "The legs bit", "t0": 2.4, "t1": 3.1, "phraseIds": ["ph_2"]},
        {"id": "tr_3", "status": "SUPPORTED", "text": "I am going to go", "t0": 3.4, "t1": 4.4, "phraseIds": ["ph_3"]},
        {"id": "tr_4", "status": "SUPPORTED", "text": "You look beautiful", "t0": 4.6, "t1": 5.8, "phraseIds": ["ph_4"]},
    ]
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    story = [effect["text"] for effect in plan["effects"] if effect.get("kind") == "story"]
    assert story.count("I'm going to go") + story.count("I am going to go") == 1
    assert "The legs bit" not in story
    assert any(item["reason"] == "fragment utan predikat" for item in board["droppedPhrases"])
    assert any(item["reason"] == "upprepning" for item in board["droppedPhrases"])


def test_hook_and_badge_do_not_share_a_word_or_a_lane(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    board = build_storyboard(_analysis(), "make a short")
    plan = plan_from_storyboard(board, _analysis())
    headline = next(effect for effect in plan["effects"] if effect.get("kind") == "headline")
    badge = next(effect for effect in plan["effects"] if effect["type"] == "badge")
    assert set(headline["text"].upper().split()).isdisjoint(set(badge["text"].upper().split()))
    assert abs(int(headline["preferY"]) - int(badge["preferY"])) >= 400
    doc = _analysis()
    doc["phrases"] = []
    doc["transcript"] = []
    doc["evidence"] = [event for event in doc["evidence"] if event["type"] != "smile"]
    bare = plan_from_storyboard(build_storyboard(doc, "make a short"), doc)
    words = [effect.get("text", "").upper() for effect in bare["effects"] if effect.get("text")]
    assert words.count("WATCH") == 1 or "LOOK" not in words


def test_validated_model_caption_is_marked(monkeypatch):
    monkeypatch.setattr(
        "app.mvp._ask_model",
        lambda prompt, fallback: {
            "hook": "YOU LOOK",
            "accent": "LOOK",
            "emoji": "😅",
            "bubble": "",
            "caption_source": "model",
            "caption_reason": "validated",
        },
    )
    board = build_storyboard(_analysis(), "make a short")
    assert board["fields"]["caption_source"] == "model"
    assert board["fields"]["llm"] is True
    assert board["fields"]["displayHook"] == "YOU"
    assert board["fields"]["badge"] == "LOOK"
    assert board["fields"]["emoji"] == "😅"


def test_uncertain_line_stays_out_unless_the_model_keeps_it(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _analysis()
    doc["phrases"].append({
        "id": "ph_9",
        "text": "that is nice",
        "t0": 6.2,
        "t1": 7.1,
        "status": "SUPPORTED",
        "wordEvidenceIds": [],
        "transcriptIds": ["tr_9"],
    })
    doc["transcript"].append({
        "id": "tr_9",
        "status": "SUPPORTED",
        "text": "that is nice",
        "t0": 6.2,
        "t1": 7.1,
        "phraseIds": ["ph_9"],
        "confidence": 0.55,
        "avgLogprob": -0.7,
    })
    hidden = build_storyboard(doc, "make a short")
    hidden_text = " ".join(effect.get("text") or "" for effect in plan_from_storyboard(hidden, doc)["effects"])
    assert "that is nice" not in hidden_text.lower()

    def keep(prompt, fallback):
        return {"invalid": True, "caption_reason": "forced", "keep_lines": ["that is nice"]}

    monkeypatch.setattr("app.mvp._ask_model", keep)
    shown = build_storyboard(doc, "make a short")
    shown_text = " ".join(effect.get("text") or "" for effect in plan_from_storyboard(shown, doc)["effects"])
    assert "that is nice" in shown_text.lower()


def test_timeline_meets_the_export_minimum_or_records_why_not(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    board = build_storyboard(_analysis(), "make a short")
    assert board["durationPolicy"]["exception"] is None
    assert board["durationPolicy"]["timeline"] >= 13
    doc = _analysis()
    doc["source"]["duration"] = 3
    doc["phrases"] = []
    doc["transcript"] = []
    doc["evidence"] = [event for event in doc["evidence"] if event["type"] != "smile"]
    doc["scenes"] = [1.2]
    short = build_storyboard(doc, "make a short")
    assert short["durationPolicy"]["exception"] == "source_shorter_than_minimum"
    assert short["durationPolicy"]["reason"]
    assert short["durationPolicy"]["timeline"] < 13


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


def test_export_spec_rejects_a_short_file_and_matches_the_renderer(tmp_path):
    ok, detail = duration_requirement(12.6, {}, 15.1)
    assert ok is False
    assert "13" in detail
    ok, detail = duration_requirement(13.0, {}, 15.1)
    assert ok is True
    assert EXPORT_SPEC["width"] == OUT_W
    assert EXPORT_SPEC["height"] == OUT_H
    assert EXPORT_SPEC["fps"] == FPS
    assert EXPORT_SPEC["safe_top"] == SAFE_TOP
    assert EXPORT_SPEC["safe_bottom"] == SAFE_BOTTOM
    assert EXPORT_SPEC["safe_side"] == SAFE_SIDE
    assert EXPORT_SPEC["crf"] == 18
    early = tmp_path / "early.mp4"
    # moov atom, then mdat atom.
    early.write_bytes(_atom(b"moov", b"abcd") + _atom(b"mdat", b"efgh"))
    assert moov_before_mdat(early) is True
    late = tmp_path / "late.mp4"
    late.write_bytes(_atom(b"mdat", b"efgh") + _atom(b"moov", b"abcd"))
    assert moov_before_mdat(late) is False


def _atom(kind: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + kind + payload
