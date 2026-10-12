"""Viral analyst, story timeline, packaging, and the extra edit ops."""

import json
from pathlib import Path

from PIL import Image

from app.creative import initial_plan, revise
from app.render import Canvas, _CANVAS, _avoid
from app.story import build_story
from app.studio import format_reply, package, parse_feedback, quality_gate
from app.trending import analyze_trending_data, preprocess_rows


def _analysis():
    return {
        "source": {"duration": 11.0, "width": 1080, "height": 1920, "fps": 30, "hasAudio": True},
        "tracks": [{
            "id": "trk_p0",
            "kind": "person",
            "samples": [{"t": index / 2, "bbox": [0.3, 0.2, 0.3, 0.5]} for index in range(10)],
        }],
        "motion": [{"t": index / 2, "energy": 0.2 if index < 3 else 1.4} for index in range(16)],
        "evidence": [],
        "phrases": [],
        "unavailable": [],
    }


def test_trending_bundle_is_historical_and_not_a_view_promise():
    report = analyze_trending_data("music", "Music", "US")
    assert report["vintage"] == "historical"
    assert report["current"] is None
    assert report["views_available"] is False
    assert report["historical"]["vintage"] == "historical"
    assert "not a view guarantee" in report["note"]
    assert report["credentialNeeded"]
    assert "KAGGLE_USERNAME" in report["credentialNeeded"]
    blob = json.dumps(report)
    assert "Babish" not in blob
    assert "Binging" not in blob


def test_csv_with_views_is_labelled_and_keeps_engagement():
    stats = preprocess_rows(
        [
            {"title": "How does this end?", "views": "1000", "likes": "100", "comment_count": "10", "category_id": "24", "channel_title": "A", "publishedAt": "2024-01-02T10:00:00Z"},
            {"title": "10 facts", "views": "5000", "likes": "400", "comment_count": "40", "category_id": "24", "channel_title": "A", "publishedAt": "2024-01-03T11:00:00Z"},
            {"title": "A quiet day", "views": "200", "likes": "4", "comment_count": "1", "category_id": "10", "channel_title": "B", "publishedAt": "2024-01-04T12:00:00Z"},
        ],
        vintage="current",
        source="fixture",
        region="US",
    )
    assert stats["vintage"] == "current"
    assert stats["views"]["median"] > 0
    assert stats["engagement"]["likePerView"]["mean"] > 0
    assert stats["title_structures"]["question"] > 0


def test_story_does_not_invent_an_emotion_without_evidence():
    story = build_story(_analysis())
    assert story["emotion"] == "unclassified"
    assert story["hookType"] in {"immediate action", "curiosity"}
    assert story["punch"]["confirmed"] is False
    assert story["punch"]["zoomEnd"] <= 1.20
    assert "viralScore" in story
    assert any(row["role"] == "ending" for row in story["segments"])


def test_story_uses_laughter_when_it_exists():
    doc = _analysis()
    doc["evidence"] = [{"id": "ev1", "type": "laughter", "t0": 4.0, "t1": 4.8, "confidence": 0.8}]
    story = build_story(doc)
    assert story["emotion"] == "funny"
    assert story["punch"]["confirmed"] is True
    assert 1.25 <= story["punch"]["zoomEnd"] <= 1.40


def test_more_zoom_on_his_reaction_is_motion_when_no_reaction_exists():
    result = revise(_analysis(), initial_plan(_analysis()), "mer zoom på hans reaktion", first=False, force_rules=True)
    assert result["ok"]
    zooms = [clip["zoomEnd"] for clip in result["plan"]["clips"]]
    assert max(zooms) >= 1.10
    assert max(zooms) <= 1.20
    text = " ".join(result["changes"]).lower()
    assert "bekräftad" in text or "rörelsen" in text
    assert "LOOK" not in text


def test_confirmed_reaction_zoom_stays_inside_the_strong_band():
    doc = _analysis()
    doc["evidence"] = [{"id": "ev1", "type": "smile", "t0": 4.0, "t1": 4.7, "confidence": 0.7}]
    doc["tracks"].append({
        "id": "trk_f0",
        "kind": "face",
        "samples": [{"t": index / 2, "bbox": [0.4, 0.2, 0.2, 0.2]} for index in range(8)],
    })
    result = revise(doc, initial_plan(doc), "mer zoom på hans reaktion", first=False, force_rules=True)
    assert result["ok"]
    assert max(clip["zoomEnd"] for clip in result["plan"]["clips"]) <= 1.40
    assert max(clip["zoomEnd"] for clip in result["plan"]["clips"]) >= 1.25


def test_blur_freeze_and_speed_ramp_change_the_timeline():
    base = initial_plan(_analysis())
    blurred = revise(_analysis(), base, "blur the background", first=False, force_rules=True)
    assert blurred["ok"]
    assert any(effect["type"] == "blur" for effect in blurred["plan"]["effects"])
    frozen = revise(_analysis(), base, "add a freeze frame", first=False, force_rules=True)
    assert frozen["ok"]
    assert any(float(clip.get("speed") or 1) <= 0.001 for clip in frozen["plan"]["clips"])
    ramped = revise(_analysis(), base, "add a speed ramp", first=False, force_rules=True)
    assert ramped["ok"]
    assert len(ramped["plan"]["clips"]) >= 3


def test_packaging_has_five_titles_and_does_not_copy_a_channel():
    story = build_story(_analysis())
    trends = analyze_trending_data("", None, "US")
    packed = package(_analysis(), story, trends, initial_plan(_analysis()))
    assert len(packed["titles"]) == 5
    assert len(packed["hooks"]) == 5
    assert 5 <= len(packed["hashtags"]) <= 8
    assert packed["bestTitle"]
    assert packed["onScreenHook"] == ""
    blob = json.dumps(packed)
    assert "Babish" not in blob
    assert "view guarantee" in packed["description"] or "not a prediction" in packed["description"]


def test_score_stays_out_of_the_reply_until_asked():
    story = build_story(_analysis())
    trends = analyze_trending_data("", None, "US")
    plan = initial_plan(_analysis())
    plan["studio"] = {"packaging": package(_analysis(), story, trends, plan), "viralScore": {"overall": 41}, "critique": {"frames": {"ok": True}}, "gate": {"ok": True, "checks": []}}
    saved = {"version": 1, "probe": {"duration": 11, "width": 1080, "height": 1920}}
    quiet = format_reply(saved, {"plan": plan, "changes": ["zoom"]}, "mer zoom")
    assert "41" not in quiet
    loud = format_reply(saved, {"plan": plan, "changes": ["zoom"]}, "visa viralpoängen")
    assert "41" in loud


def test_analytics_feedback_maps_an_early_drop_to_the_hook():
    feedback = parse_feedback("Views: 1200. AVD: 4. Swiped: 70%. Early drop in the first second.")
    assert feedback
    assert "hook" in feedback["actions"]
    assert feedback["metrics"]["views"] == "1200"


def test_gate_blocks_an_invented_caption():
    plan = initial_plan(_analysis())
    plan["captions"] = [{"id": "c", "start": 0.2, "end": 1.0, "text": "LOOK", "fontSize": 64}]
    story = build_story(_analysis())
    gate = quality_gate(plan, _analysis(), story, {"copiedTitles": False}, {"width": 1080, "height": 1920, "duration": 10}, {"playable": True, "duration": 10})
    assert gate["ok"] is False
    assert "captions" in gate["hardFailures"]


def test_caption_lane_stays_above_seventy_percent():
    token = _CANVAS.set(Canvas(1080, 1920, 30))
    try:
        sprite = Image.new("RGBA", (500, 140), (255, 255, 255, 255))
        faces = [{"id": "f", "x": 200, "y": 500, "w": 680, "h": 700}]
        _x, y = _avoid(sprite, faces, prefer_y=1500)
        assert y + sprite.height <= int(1920 * 0.70) + 1
    finally:
        _CANVAS.reset(token)


def test_trends_change_pacing_label_and_name_the_signal():
    from app.studio import _shape, drive_with_trends

    analysis = _analysis()
    story = build_story(analysis)
    plan = initial_plan(analysis)
    _shape(plan, analysis, story)
    trends = analyze_trending_data("music", None, "US")
    driven = drive_with_trends(plan, analysis, story, trends)
    kinds = {row["choice"] for row in driven["choices"]}
    assert {"hook", "structure", "pacing", "title", "on-screen text", "payoff", "rewatch"} <= kinds
    assert any("title_structures.number" in row["signal"] for row in driven["choices"])
    assert any(abs(float(clip.get("speed") or 1) - 1.12) < 0.001 for clip in plan["clips"])
    assert any(effect.get("factual") and str(effect.get("text") or "").endswith("s") for effect in plan["effects"])
    assert not any(str(effect.get("text") or "") == "LOOK" for effect in plan["effects"])
    gate = quality_gate(
        plan,
        analysis,
        story,
        {"copiedTitles": False},
        {"width": 1080, "height": 1920, "duration": 11},
        {"playable": True, "duration": 11},
    )
    assert "captions" not in gate["hardFailures"]
    assert gate["ok"] is True


def test_question_pattern_is_the_chosen_title_without_copying_a_channel():
    story = build_story(_analysis())
    trends = analyze_trending_data("", None, "US")
    packed = package(_analysis(), story, trends, initial_plan(_analysis()))
    assert packed["bestTitle"].startswith("What happens at")
    assert "Babish" not in packed["bestTitle"]
    assert float((trends.get("title_structures") or {}).get("question") or 0) >= 0.08


def test_second_pass_moves_a_late_peak_to_the_front():
    from app.studio import _shape, improve_rendered

    analysis = _analysis()
    analysis["motion"] = [{"t": index / 2, "energy": 0.3 if index < 16 else 8.0} for index in range(22)]
    story = build_story(analysis)
    assert story["punch"]["t0"] > 3
    plan = initial_plan(analysis)
    _shape(plan, analysis, story)
    before = float(plan["clips"][0]["sourceStart"])
    changed, note = improve_rendered(
        plan,
        analysis,
        story,
        {"weak": True, "reasons": ["first 3s motion 1.20 vs later 7.40; punch zoom output at 8.00s"]},
    )
    assert changed is True
    assert float(plan["clips"][0]["sourceStart"]) > before + 2
    assert abs(float(plan["clips"][0]["sourceStart"]) - float(story["punch"]["t0"])) < 0.05
    assert "inte en bekräftad reaktion" in note
    assert float(plan["clips"][0]["zoomEnd"]) <= 1.20


def test_second_pass_leaves_a_strong_render_alone():
    from app.studio import improve_rendered

    plan = initial_plan(_analysis())
    changed, note = improve_rendered(plan, _analysis(), build_story(_analysis()), {"weak": False, "reasons": []})
    assert changed is False
    assert note == ""
    assert float(plan["clips"][0]["sourceStart"]) == 0


def test_trending_failure_does_not_raise(monkeypatch):
    import app.trending as trending

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaggle down")

    monkeypatch.setattr(trending, "_analyze_trending_data", boom)
    report = trending.analyze_trending_data("music", None, "US")
    assert report["vintage"] == "fallback"
    assert report["trend_score"] == 0
    assert report["views_available"] is False
    assert "fortsätter" in report["note"]


def test_factual_timestamp_passes_the_gate():
    plan = initial_plan(_analysis())
    plan["studio"] = {"factualLabels": ["1.5s"]}
    plan["effects"] = [{
        "id": "e1",
        "type": "text",
        "kind": "headline",
        "text": "1.5s",
        "factual": True,
        "start": 0.2,
        "end": 1.4,
        "fontSize": 96,
    }]
    story = build_story(_analysis())
    gate = quality_gate(
        plan,
        _analysis(),
        story,
        {"copiedTitles": False},
        {"width": 1080, "height": 1920, "duration": 11},
        {"playable": True, "duration": 11},
    )
    assert gate["ok"] is True


def test_preprocess_file_roundtrip(tmp_path: Path, monkeypatch):
    path = tmp_path / "us.csv"
    path.write_text("title,views,category_id\nHello there,10,24\n", encoding="utf-8")
    import app.trending as trending
    monkeypatch.setattr(trending, "TREND_DIR", tmp_path / "trend")
    stats = trending.preprocess_csv(path, "historical", "unit", "US")
    assert stats["rows"] == 1
    assert stats["region"] == "US"
    assert (tmp_path / "trend" / "stats" / "historical_us.json").exists()
