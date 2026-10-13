"""The chat edit is a timeline change, not a stock hook."""

import json

from app.creative import initial_plan, revise
from app.llm_local import health


def _analysis():
    return {
        "source": {"duration": 11.0, "width": 1080, "height": 1920, "fps": 60, "hasAudio": True},
        "tracks": [{
            "id": "trk_f0",
            "kind": "face",
            "samples": [{"t": index / 2, "bbox": [0.4, 0.2, 0.2, 0.25]} for index in range(8)],
        }],
        "evidence": [{"id": "ev_smile", "type": "smile", "t0": 4.0, "t1": 4.8, "confidence": 0.8}],
        "phrases": [
            {"id": "ph_1", "text": "hello there", "t0": 1.0, "t1": 2.2, "status": "SUPPORTED"},
            {"id": "ph_2", "text": "look at him", "t0": 6.0, "t1": 7.4, "status": "SUPPORTED"},
        ],
        "unavailable": [],
    }


def _texts(plan) -> str:
    parts = [caption.get("text") or "" for caption in plan.get("captions") or []]
    parts += [effect.get("text") or "" for effect in plan.get("effects") or []]
    return " ".join(parts)


def test_first_short_keeps_audio_and_does_not_invent_look():
    result = revise(
        _analysis(),
        None,
        "Create a vertical 9:16 Short. Keep the original audio. Do not add background music.",
        first=True,
        force_rules=True,
    )
    assert result["ok"]
    assert result["backend"] == "rules"
    plan = result["plan"]
    assert plan["audio"]["keepOriginal"] is True
    assert plan["audio"]["music"] is False
    assert plan["clips"][0]["sourceStart"] == 0
    assert plan["clips"][0]["sourceEnd"] == 11
    assert plan["clips"][0]["focusTrack"] == "trk_f0"
    assert plan["effects"] == []
    assert "LOOK" not in _texts(plan)
    assert "wait" not in _texts(plan).lower()
    assert [caption["text"] for caption in plan["captions"]] == ["hello there", "look at him"]


def test_trim_and_zoom_change_only_those_parts():
    base = initial_plan(_analysis())
    result = revise(
        _analysis(),
        base,
        "Remove the opening two seconds and zoom in on the main subject.",
        first=False,
        force_rules=True,
    )
    assert result["ok"]
    clip = result["plan"]["clips"][0]
    assert clip["sourceStart"] == 2
    assert clip["sourceEnd"] == 11
    assert clip["zoomEnd"] >= 1.55
    assert clip["focusTrack"] == "trk_f0"
    assert result["plan"]["audio"]["music"] is False
    assert [caption["text"] for caption in result["plan"]["captions"]] == ["look at him"]
    assert result["plan"]["captions"][0]["start"] == 4


def test_remove_last_caption_leaves_the_earlier_line():
    base = initial_plan(_analysis())
    result = revise(_analysis(), base, "Remove the last caption and render again.", first=False, force_rules=True)
    assert result["ok"]
    assert [caption["text"] for caption in result["plan"]["captions"]] == ["hello there"]
    assert result["plan"]["clips"] == base["clips"]


def test_undo_is_a_revert_not_a_new_idea():
    result = revise(_analysis(), initial_plan(_analysis()), "Undo that change.", first=False, force_rules=True)
    assert result["undo"] is True


def test_unknown_line_does_not_pretend_success():
    result = revise(_analysis(), initial_plan(_analysis()), "byt musik till jazz", first=False, force_rules=True)
    assert result["ok"] is False
    assert result["plan"] is None


def test_remove_look_only_strips_that_word():
    base = initial_plan(_analysis())
    result = revise(_analysis(), base, "Remove the word LOOK", first=False, force_rules=True)
    assert result["ok"]
    assert result["plan"]["captions"][0]["text"] == "hello there"
    assert result["plan"]["captions"][1]["text"] == "at him"


def test_missing_caption_is_not_invented_so_it_can_be_deleted():
    doc = _analysis()
    doc["phrases"] = []
    base = initial_plan(doc)
    result = revise(doc, base, "Remove the last caption and render again.", first=False, force_rules=True)
    assert result["ok"] is False
    assert "ingen text" in result["error"].lower()


def test_hand_arrow_uses_the_hand_track_and_refuses_without_one():
    doc = _analysis()
    doc["tracks"].append({
        "id": "trk_h0",
        "kind": "hand",
        "samples": [{"t": 3 + index / 4, "bbox": [0.62, 0.48, 0.08, 0.08]} for index in range(6)],
    })
    base = initial_plan(doc)
    placed = revise(doc, base, "Add a red arrow on her hand", first=False, force_rules=True)
    assert placed["ok"]
    arrow = next(effect for effect in placed["plan"]["effects"] if effect["type"] == "arrow")
    assert arrow["track"] == "trk_h0"
    refused = revise(_analysis(), initial_plan(_analysis()), "lägg en pil på hennes hand", first=False, force_rules=True)
    assert refused["ok"] is False
    assert "hand" in refused["error"].lower()


def test_bubble_removal_and_hook_growth_do_not_create_look():
    base = initial_plan(_analysis())
    base["effects"].append({"id": "e1", "type": "text", "kind": "bubble", "start": 1, "end": 2, "text": "wait", "fontSize": 48})
    removed = revise(_analysis(), base, "ta bort bubblan", first=False, force_rules=True)
    assert removed["ok"]
    assert not any(effect.get("kind") == "bubble" for effect in removed["plan"]["effects"])
    grown = revise(_analysis(), initial_plan(_analysis()), "Gör hooken större", first=False, force_rules=True)
    assert grown["ok"]
    assert "LOOK" not in _texts(grown["plan"])
    assert grown["plan"]["captions"][0]["fontSize"] > 64


def test_ollama_plan_is_used_only_when_it_follows_the_instruction():
    base = initial_plan(_analysis())

    def good(_messages):
        return json.dumps({"ops": [
            {"op": "trim_opening", "seconds": 2},
            {"op": "zoom_main", "zoomEnd": 1.55},
            {"op": "set_audio", "keepOriginal": True, "music": False},
        ], "summary": "trim and zoom"})

    used = revise(_analysis(), base, "Remove the opening two seconds and zoom in on the main subject.", first=False, llm=good)
    assert used["backend"] == "ollama"
    assert used["plan"]["clips"][0]["sourceStart"] == 2

    def junk(_messages):
        return "I would add the word LOOK and some music."

    fallback = revise(_analysis(), base, "Remove the opening two seconds and zoom in on the main subject.", first=False, llm=junk)
    assert fallback["backend"] == "rules"
    assert fallback["plan"]["clips"][0]["sourceStart"] == 2
    assert "LOOK" not in _texts(fallback["plan"])
    assert "inte full språkförståelse" in " ".join(fallback["changes"])


def test_remote_model_is_not_contacted_unless_explicitly_allowed(monkeypatch):
    monkeypatch.setattr("app.llm_local.OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr("app.llm_local.LLM_ALLOW_REMOTE", False)
    report = health()
    assert report["ok"] is False
    assert "inte lokal" in report["error"]
    assert report["recovery"]
