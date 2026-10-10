import subprocess
from pathlib import Path

from app.plan_ops import Editor, compile_plan
from app.render import render_plan


def _source(path: Path) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
    )


def test_render_writes_1080x1920_h264(tmp_path: Path):
    source = tmp_path / "in.mp4"
    _source(source)
    analysis = {
        "source": {"duration": 2, "width": 640, "height": 360, "fps": 30, "hasAudio": True},
        "faces": [{
            "id": "face_0",
            "screenTime": 2,
            "avg": {"x": 0.5, "y": 0.4},
            "samples": [
                {"t": 0.0, "x": 0.3, "y": 0.3, "w": 0.2, "h": 0.25},
                {"t": 1.0, "x": 0.55, "y": 0.35, "w": 0.2, "h": 0.25},
                {"t": 2.0, "x": 0.7, "y": 0.4, "w": 0.18, "h": 0.22},
            ],
        }],
        "transcript": {"text": "hello there", "words": [
            {"text": "hello", "start": 0.2, "end": 0.6},
            {"text": "there", "start": 0.7, "end": 1.1},
        ]},
        "silences": [],
        "silenceTotal": 0,
    }
    editor = Editor(analysis)
    assert editor.select_clip(0.1, 1.6, label="hook")["ok"]
    editor.plan["clips"][0]["zoomStart"] = 1.15
    editor.plan["clips"][0]["zoomEnd"] = 1.35
    editor.add_text(0.0, 1.2, "LOOK AGAIN", accent="AGAIN")
    editor.add_emoji(0.3, 1.2, "👀")
    editor.add_arrow(0.2, 1.1)
    before = source.read_bytes()
    plan = compile_plan(editor.plan, analysis)
    out = tmp_path / "out.mp4"
    probe = render_plan(source, analysis, plan, out)
    assert source.read_bytes() == before
    assert out.stat().st_size > 10_000
    assert probe["width"] == 1080
    assert probe["height"] == 1920
    assert probe["videoCodec"] == "h264"
    assert probe["audioCodec"] == "aac"
    assert probe["pixFmt"] == "yuv420p"
    assert probe["sampleRate"] == 48000
    assert 0.8 < probe["duration"] < 2.5
    assert probe["loudness"] is not None
    assert -16.5 < probe["loudness"] < -11.5
