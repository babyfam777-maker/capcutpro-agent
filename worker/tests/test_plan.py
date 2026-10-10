import copy

from app.plan_ops import Editor


ANALYSIS = {
    "source": {"duration": 10, "width": 1080, "height": 1920, "fps": 30, "hasAudio": True},
    "faces": [{"id": "face_0", "screenTime": 8, "avg": {"x": 0.5, "y": 0.4}, "samples": []}],
    "transcript": {
        "text": "wait for the punchline look again",
        "words": [
            {"text": "wait", "start": 0.2, "end": 0.5},
            {"text": "for", "start": 0.5, "end": 0.7},
            {"text": "the", "start": 0.7, "end": 0.9},
            {"text": "punchline", "start": 1.0, "end": 1.6},
            {"text": "look", "start": 4.0, "end": 4.3},
            {"text": "again", "start": 4.3, "end": 4.8},
        ],
    },
    "silences": [{"start": 2.0, "end": 3.5}],
    "silenceTotal": 1.5,
    "scenes": [3.6],
    "moments": [],
}


def editor():
    return Editor(copy.deepcopy(ANALYSIS))


def test_content_cuts_trim_delete_reorder_and_zoom_on_words():
    ed = editor()
    first = ed.select_clip(0.1, 1.8, label="hook")
    second = ed.select_clip(3.8, 6.2, label="payoff")
    assert first["ok"] and second["ok"]
    assert len(ed.plan["clips"]) == 2
    ed.reorder_clips([second["clip"]["id"], first["clip"]["id"]])
    assert ed.plan["clips"][0]["id"] == second["clip"]["id"]
    ed.trim_clip(first["clip"]["id"], source_end=1.4)
    assert ed.plan["clips"][1]["sourceEnd"] == 1.4
    deleted = ed.delete_timeline_range(0, 0.4)
    assert deleted["ok"]
    zoom = ed.set_zoom(when_text="punchline", zoom_start=1.6, zoom_end=1.3)
    assert zoom["ok"]
    assert zoom["clipId"] == first["clip"]["id"]
    assert ed.plan["clips"][1]["zoomStart"] == 1.6


def test_undo_restores_previous_rendered_version():
    ed = editor()
    ed.select_clip(0.2, 2.0)
    ed.versions.append({"version": 1, "plan": copy.deepcopy(ed.plan)})
    ed.active = 1
    ed.dirty = False
    ed.undo_stack.clear()
    ed.select_clip(4, 6)
    ed.versions.append({"version": 2, "plan": copy.deepcopy(ed.plan)})
    ed.active = 2
    ed.dirty = False
    ed.undo_stack.clear()
    result = ed.undo()
    assert result["activeVersion"] == 1
    assert len(ed.plan["clips"]) == 1
    # The v2 plan is still stored. A later render would become v3.
    assert any(v["version"] == 2 for v in ed.versions)


def test_safety_blocks_false_claims_and_banned_emoji():
    ed = editor()
    ed.select_clip(0.2, 2)
    blocked = ed.add_text(0, 1.2, "she's pregnant", accent="PREGNANT")
    assert blocked["ok"] is False
    emoji = ed.add_emoji(0.2, 1.2, "🍆")
    assert emoji["ok"] is False
    ok = ed.add_text(0, 1.2, "WAIT", accent="WAIT")
    assert ok["ok"] is True


def test_proposed_spans_follow_speech_and_can_open_on_a_later_hook():
    from app.plan_ops import proposed_spans

    spans = proposed_spans(ANALYSIS)
    assert len(spans) == 2
    assert spans[0][1] < 2.2
    assert spans[1][0] > 3.5
    hooked = copy.deepcopy(ANALYSIS)
    hooked["moments"] = [{"start": 4.0, "end": 4.8, "score": 3, "reasons": ["speech"], "text": "look again"}]
    opened = proposed_spans(hooked)
    assert opened[0][0] > 3.5


def test_select_clip_treats_milliseconds_as_seconds():
    ed = editor()
    result = ed.select_clip(200, 1800)
    assert result["ok"]
    assert result["clip"]["sourceStart"] == 0.2
    assert result["clip"]["sourceEnd"] == 1.8


def test_set_zoom_defaults_and_includes_the_spoken_word():
    ed = editor()
    ed.select_clip(0.0, 0.8)
    result = ed.set_zoom(when_text="punchline")
    assert result["ok"]
    assert result["zoomStart"] == 1.55
    assert result["zoomEnd"] == 1.25
    assert result["sourceEnd"] > 1.0


def test_replace_captions_scales_text():
    ed = editor()
    ed.add_text(0, 1, "LOOK", font_size=100)
    ed.add_caption(0, 1, "look again", font_size=70)
    result = ed.replace_captions(scale=1.5)
    assert result["ok"]
    assert ed.plan["style"]["captionFontSize"] == int(72 * 1.5)
    assert ed.plan["effects"][0]["fontSize"] == 150
