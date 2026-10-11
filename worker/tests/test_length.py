"""Segment length, honest upscale labels, and re-render of the story plan."""

import json
import subprocess
from pathlib import Path

from app.export_spec import SHORT_EXPORT, describe_export
from app.mvp import build_storyboard, plan_from_storyboard
from app.plan_ops import Editor, compile_plan, timeline
from app.render import render_plan
from app.revisions import apply_edits


def _quiet(duration: float, **source) -> dict:
    meta = {"duration": duration, "width": 1080, "height": 1920, "fps": 30, "hasAudio": True}
    meta.update(source)
    return {
        "source": meta,
        "scenes": [4.2] if duration > 4 else [],
        "tracks": [],
        "evidence": [
            {"id": "ev_motion", "type": "motion", "t0": 1.2, "t1": 1.8, "trackId": None, "confidence": 0.7, "source": "farneback", "payload": {}},
        ] if duration > 2 else [],
        "phrases": [],
        "transcript": [],
        "limitations": [],
    }


def _texts(plan: dict) -> str:
    parts = [effect.get("text") or "" for effect in plan.get("effects") or []]
    parts += [caption.get("text") or "" for caption in plan.get("captions") or []]
    return " ".join(parts).lower()


def test_export_labels_follow_the_source_not_a_native_claim():
    upscaled = describe_export({"width": 1080, "height": 1920, "fps": 60})
    assert upscaled["quality"] == "upscaled"
    assert upscaled["upscaled"] is True
    assert upscaled["interpolated"] is False
    assert "native" not in upscaled["quality"]

    both = describe_export({"width": 640, "height": 360, "fps": 24})
    assert both["quality"] == "upscaled,interpolated"
    assert "native" not in both["quality"].split(",")

    native = describe_export({"width": 2160, "height": 3840, "fps": 60})
    assert native == {
        "width": 2160,
        "height": 3840,
        "fps": 60.0,
        "upscaled": False,
        "interpolated": False,
        "quality": "native",
    }


def test_ten_second_source_becomes_a_real_short(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _quiet(10.0, fps=60)
    board = build_storyboard(doc, "Sana makes her dancer nervous + his reaction")
    plan = plan_from_storyboard(board, doc)
    _, total = timeline(plan)
    assert total >= 13
    assert plan["export"]["quality"] == "upscaled"
    assert plan["export"]["lengthReached"] is True
    assert "lengthLimit" not in plan["export"]
    assert plan["audio"]["keepOriginal"] is True
    assert plan["audio"]["music"] is False
    full = [
        clip for clip in plan["clips"]
        if float(clip["sourceStart"]) <= 0.08 and float(clip["sourceEnd"]) >= 9.9 and abs(float(clip["speed"]) - 1) < 0.05
    ]
    assert full, plan["clips"]
    holds = sum(
        float(clip.get("holdSec") or 0)
        for clip in plan["clips"]
        if float(clip.get("speed") or 1) <= 0.001
    )
    assert holds < 1.5
    for clip in plan["clips"]:
        assert 0 <= float(clip["sourceStart"]) < float(clip["sourceEnd"]) <= 10.05
    blob = _texts(plan)
    assert "sana" not in blob
    assert "nervous" not in blob
    assert "dancer" not in blob
    assert plan["clips"][0]["label"] == "hook"
    assert any(effect["type"] == "sound_effect" for effect in plan["effects"])
    assert any(effect.get("kind") == "headline" for effect in plan["effects"])


def test_a_short_motion_hook_keeps_the_headline(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _quiet(11.0, fps=60)
    doc["scenes"] = []
    doc["evidence"] = [{
        "id": "ev_1",
        "type": "motion",
        "t0": 9.5,
        "t1": 9.75,
        "trackId": None,
        "confidence": 0.8,
        "source": "farneback",
        "payload": {},
    }]
    plan = plan_from_storyboard(build_storyboard(doc, "make a short"), doc)
    hook = plan["clips"][0]
    assert hook["label"] == "hook"
    assert float(hook["sourceEnd"]) - float(hook["sourceStart"]) >= 1.0
    assert float(hook["sourceEnd"]) <= 11.05
    headline = next(effect for effect in plan["effects"] if effect.get("kind") == "headline")
    assert headline["text"]
    edited = apply_edits(plan, ["Gör hooken större"])
    grown = next(effect for effect in edited["plan"]["effects"] if effect.get("kind") == "headline")
    assert int(grown["fontSize"]) > int(headline["fontSize"])


def test_two_second_source_reports_why_it_cannot_reach_13(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _quiet(2.0)
    doc["scenes"] = []
    doc["evidence"] = []
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    _, total = timeline(plan)
    assert total < 13
    limit = plan["export"]["lengthLimit"]
    assert "13" in limit
    assert "frys" in limit.lower() or "loop" in limit.lower()
    assert plan["export"]["lengthReached"] is False
    holds = sum(
        float(clip.get("holdSec") or 0)
        for clip in plan["clips"]
        if float(clip.get("speed") or 1) <= 0.001
    )
    assert holds < 1.0
    speeds = [float(clip["speed"]) for clip in plan["clips"] if float(clip["speed"]) > 0.001]
    assert speeds
    assert min(speeds) >= 0.5


def test_long_source_uses_unused_footage_instead_of_a_tiny_window(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _quiet(20.0, fps=60, width=2160, height=3840)
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    _, total = timeline(plan)
    assert total >= 13
    assert plan["export"]["quality"] == "native"
    longest = max(
        float(clip["sourceEnd"]) - float(clip["sourceStart"])
        for clip in plan["clips"]
        if float(clip.get("speed") or 1) > 0.2
    )
    # The old selector never kept a window longer than 3.2s.
    assert longest > 4
    assert total < 40


def test_rerender_grows_the_hook_and_keeps_the_cut(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _quiet(10.0, fps=60)
    plan = plan_from_storyboard(build_storyboard(doc, "make a short"), doc)
    headline = next(effect for effect in plan["effects"] if effect.get("kind") == "headline")
    before_size = int(headline["fontSize"])
    before_zoom = next(float(clip["zoomEnd"]) for clip in plan["clips"] if clip["label"] == "hook")
    _, before = timeline(plan)
    ranges = [(clip["sourceStart"], clip["sourceEnd"], clip["label"]) for clip in plan["clips"]]
    edited = apply_edits(plan, ["Gör hooken större"])
    assert edited["ok"], edited
    assert any("Hooken" in change for change in edited["changes"])
    grown = next(effect for effect in edited["plan"]["effects"] if effect.get("kind") == "headline")
    assert int(grown["fontSize"]) > before_size
    after_zoom = next(float(clip["zoomEnd"]) for clip in edited["plan"]["clips"] if clip["label"] == "hook")
    assert after_zoom > before_zoom
    _, after = timeline(edited["plan"])
    assert abs(after - before) < 0.05
    assert [(clip["sourceStart"], clip["sourceEnd"], clip["label"]) for clip in edited["plan"]["clips"]] == ranges
    assert any(effect["type"] == "sound_effect" for effect in edited["plan"]["effects"])


def _tiny(path: Path) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=1.2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.2",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
    )


def test_delivery_labels_the_upscale_and_rerender_still_encodes(tmp_path: Path):
    source = tmp_path / "in.mp4"
    _tiny(source)
    analysis = {
        "source": {"duration": 1.2, "width": 320, "height": 240, "fps": 15, "hasAudio": True},
        "faces": [],
        "transcript": {"text": "", "words": []},
    }
    editor = Editor(analysis)
    assert editor.select_clip(0.0, 1.0, label="hook")["ok"]
    assert editor.add_text(0.0, 1.0, "LOOK", accent="LOOK")["ok"]
    plan = compile_plan(editor.plan, analysis)
    out = tmp_path / "v1.mp4"
    probe = render_plan(source, analysis, plan, out, delivery=SHORT_EXPORT)
    assert probe["width"] == 2160 and probe["height"] == 3840
    assert probe["videoCodec"] == "h264"
    assert probe["pixFmt"] == "yuv420p"
    assert "high" in str(probe["profile"]).lower()
    assert abs(probe["fps"] - 60) < 0.8
    assert probe["quality"] == "upscaled,interpolated"
    assert probe["upscaled"] is True and probe["interpolated"] is True
    assert "native" not in probe["quality"]
    tagged = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-print_format", "json", str(out)],
        text=True,
    ))
    assert tagged["format"]["tags"]["comment"] == "upscaled,interpolated"
    head = out.read_bytes()[:1_500_000]
    assert b"ftyp" in head[:64]
    assert b"moov" in head
    grown = apply_edits(plan, ["Gör hooken större"])
    assert grown["ok"], grown
    out2 = tmp_path / "v2.mp4"
    probe2 = render_plan(source, analysis, grown["plan"], out2, delivery=SHORT_EXPORT)
    assert probe2["quality"] == "upscaled,interpolated"
    assert probe2["width"] == 2160 and abs(probe2["fps"] - 60) < 0.8
    assert out2.stat().st_size > 10_000
    subprocess.check_call(["ffmpeg", "-v", "error", "-i", str(out2), "-f", "null", "-"], stdout=subprocess.DEVNULL)
