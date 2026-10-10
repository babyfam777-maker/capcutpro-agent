"""Plan checks and the deterministic cut. The 9.381s fixture is the owner's logged analysis."""

import copy
import os
import shutil
import subprocess
from pathlib import Path

from app.auto_edit import build_auto_plan, prepare_for_render, text_visible
from app.plan_ops import Editor, compile_plan
from app.store import Store

# Word times and the ranked moment are copied from the qwen25_3b_r2 event log.
ZEN = {
    "source": {"duration": 9.381, "width": 608, "height": 1080, "fps": 30, "hasAudio": True},
    "faces": [{"id": "face_16", "screenTime": 3.88, "avg": {"x": 0.465, "y": 0.16}, "samples": []}],
    "transcript": {
        "text": "We can't stop it, not the other way Cause I know I can't be brave I never felt seen",
        "words": [
            {"text": "We", "start": 0.75, "end": 0.93},
            {"text": "can't", "start": 0.93, "end": 1.41},
            {"text": "stop", "start": 1.41, "end": 1.81},
            {"text": "it,", "start": 1.81, "end": 2.25},
            {"text": "not", "start": 2.61, "end": 2.63},
            {"text": "the", "start": 2.63, "end": 2.75},
            {"text": "other", "start": 2.75, "end": 3.17},
            {"text": "way", "start": 3.17, "end": 3.81},
            {"text": "Cause", "start": 3.81, "end": 4.27},
            {"text": "I", "start": 4.27, "end": 4.41},
            {"text": "know", "start": 4.41, "end": 4.69},
            {"text": "I", "start": 4.69, "end": 5.01},
            {"text": "can't", "start": 5.01, "end": 5.29},
            {"text": "be", "start": 5.29, "end": 5.45},
            {"text": "brave", "start": 5.45, "end": 6.09},
            {"text": "I", "start": 6.09, "end": 6.41},
            {"text": "never", "start": 6.41, "end": 6.91},
            {"text": "felt", "start": 6.91, "end": 7.29},
            {"text": "seen", "start": 7.29, "end": 8.07},
        ],
    },
    "silences": [],
    "silenceTotal": 0,
    "scenes": [1.533],
    "moments": [
        {"start": 1.5, "end": 2.0, "score": 2.256, "reasons": ["speech", "motion", "face", "scene-cut"], "text": "stop"},
        {"start": 2.5, "end": 3.0, "score": 1.82, "reasons": ["speech", "face"], "text": "not the other"},
        {"start": 0.5, "end": 1.0, "score": 1.556, "reasons": ["speech", "face"], "text": "We"},
    ],
}


def test_times_outside_the_video_are_rejected_and_not_clamped():
    ed = Editor(copy.deepcopy(ZEN))
    refused = ed.select_clip(0, 10)
    assert refused["ok"] is False
    assert ed.plan["clips"] == []
    assert "utanför" in refused["error"]
    assert "original" in refused["error"].lower()
    # 120 was the model's millisecond-sized miss. It must not become the whole file.
    missed = ed.select_clip(0, 120)
    assert missed["ok"] is False
    assert ed.plan["clips"] == []


def test_empty_plan_does_not_fall_back_to_the_original(tmp_path: Path):
    source = tmp_path / "in.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    before = source.read_bytes()
    project = Store(tmp_path / "projects").create("in.mp4", source)
    project.editor.analysis = {
        "source": {"duration": 5, "width": 320, "height": 240, "fps": 30, "hasAudio": False},
        "transcript": {"text": "", "words": []},
        "moments": [],
        "silences": [],
        "faces": [],
    }
    result = project.commit(lambda *_args, **_kwargs: None)
    assert result["ok"] is False
    assert "användas" in result["error"] or "reserv" in result["error"]
    assert project.editor.versions == []
    assert not list((project.folder / "versions").glob("v*/output.mp4"))
    assert project.original.read_bytes() == before


def test_overlapping_segments_are_rejected():
    ed = Editor(copy.deepcopy(ZEN))
    assert ed.select_clip(0.8, 2.2)["ok"]
    overlap = ed.select_clip(1.5, 3.2)
    assert overlap["ok"] is False
    assert "överlappar" in overlap["error"]
    assert len(ed.plan["clips"]) == 1


def test_text_needs_a_real_time_span():
    ed = Editor(copy.deepcopy(ZEN))
    zero = ed.add_text(0.2, 0.2, "Wow")
    assert zero["ok"] is False
    assert "0,2" in zero["error"]
    assert ed.plan["effects"] == []
    assert ed.add_text(0.0, 0.9, "STOP", accent="STOP")["ok"]


def test_size_change_keeps_existing_lines():
    ed = Editor(copy.deepcopy(ZEN))
    ed.add_caption(0.0, 1.0, "We can't stop")
    ed.add_caption(1.0, 2.0, "not the other")
    ed.add_caption(2.0, 3.2, "way Cause I")
    before = [cap["text"] for cap in ed.plan["captions"]]
    result = ed.replace_captions(scale=1.35, captions=[{"start": 1.12, "end": 1.47, "text": "skrattar"}])
    assert result["ok"]
    assert [cap["text"] for cap in ed.plan["captions"]] == before
    assert len(ed.plan["captions"]) == 3
    assert ed.plan["style"]["captionFontSize"] > 72
    assert all(cap["fontSize"] > 72 for cap in ed.plan["captions"])


def test_logged_analysis_opens_on_the_confirmed_moment_and_removes_dead_time():
    built = build_auto_plan(ZEN)
    assert built["ok"]
    hook = built["hook"]
    assert hook is not None
    assert hook["reasons"] == ["speech", "motion", "face", "scene-cut"]
    assert hook["momentStart"] == 1.5
    # "stop" begins at 1.41s, inside the 1.5–2.0 moment, so the cut starts there instead of mid-word.
    assert 1.3 <= built["clips"][0]["sourceStart"] <= 1.7
    assert built["clips"][0]["label"] == "hook"
    removed = built["removed"]
    assert any(span["start"] == 0 and span["end"] <= 0.75 for span in removed)
    assert any(span["end"] == 9.381 and span["start"] >= 8.0 for span in removed)
    covered = sorted((clip["sourceStart"], clip["sourceEnd"]) for clip in built["clips"])
    assert covered[0][0] > 0.5
    assert covered[-1][1] < 8.4
    for left, right in zip(covered, covered[1:]):
        assert left[1] <= right[0] + 0.05

    ed = Editor(copy.deepcopy(ZEN))
    ed.select_clip(0, 9.381)
    prepared = prepare_for_render(ed, first_cut=True)
    assert prepared["ok"], prepared
    assert ed.plan["clips"][0]["sourceStart"] == built["clips"][0]["sourceStart"]
    assert all(clip["zoomStart"] == 1 and clip["zoomEnd"] == 1 for clip in ed.plan["clips"])
    assert ed.plan["audio"]["keepOriginal"] is True
    assert ed.plan["audio"]["music"] is False
    compiled = compile_plan(ed.plan, ZEN)
    assert not any(effect["type"] == "sound_effect" for effect in compiled["effects"])
    for sample in (0.3, 1.0, 3.0):
        assert text_visible(compiled, sample), sample
    assert len(compiled["captions"]) >= 3


def test_weak_moment_is_not_used_as_a_hook():
    weak = copy.deepcopy(ZEN)
    weak["moments"] = [{"start": 1.5, "end": 2.0, "score": 0.4, "reasons": ["speech"], "text": "stop"}]
    built = build_auto_plan(weak)
    assert built["ok"]
    assert built["hook"] is None
    assert built["clips"][0]["sourceStart"] < 0.8


def _source(path: Path) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x101018:s=608x1080:r=30:d=9.381",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=9.381",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
    )


def _white_pixels(frame: Path) -> int:
    from PIL import Image

    image = Image.open(frame).convert("RGB")
    return sum(1 for red, green, blue in image.getdata() if red > 220 and green > 220 and blue > 220)


def _frame(video: Path, at: float, dest: Path) -> None:
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", str(dest)],
        stdout=subprocess.DEVNULL,
    )


def test_rendered_cut_is_a_playable_mp4_and_bigger_text_keeps_lines(tmp_path: Path):
    source = tmp_path / "in.mp4"
    _source(source)
    before = source.read_bytes()
    project = Store(tmp_path / "projects").create("clip.mp4", source)
    project.editor.analysis = copy.deepcopy(ZEN)
    # The bad plan from the log: one clip of the entire source and a zero-length headline.
    project.editor.plan["clips"] = [{
        "id": "cwhole",
        "sourceStart": 0,
        "sourceEnd": 9.381,
        "speed": 1,
        "zoomStart": 1,
        "zoomEnd": 1,
        "focusX": 0.5,
        "focusY": 0.45,
        "focusTrack": "face_16",
        "label": "all",
    }]
    first = project.commit(lambda *_args, **_kwargs: None)
    assert first["ok"], first
    v1 = project.folder / "versions" / "v1" / "output.mp4"
    assert v1.exists()
    assert project.original.read_bytes() == before
    probe = first["probe"]
    assert probe["width"] == 1080 and probe["height"] == 1920
    assert probe["videoCodec"] == "h264"
    subprocess.check_call(["ffmpeg", "-v", "error", "-i", str(v1), "-f", "null", "-"], stdout=subprocess.DEVNULL)
    lines_before = [cap["text"] for cap in project.editor.plan["captions"]]
    assert len(lines_before) >= 3
    size_before = int(project.editor.plan["style"]["captionFontSize"])
    whites_before = {}
    for sample in (0.3, 1.0, 3.0):
        frame = tmp_path / f"v1-{sample}.png"
        _frame(v1, sample, frame)
        whites_before[sample] = _white_pixels(frame)
        assert whites_before[sample] > 80, (sample, whites_before[sample])

    project._restyle = {"size"}
    project._restyle_text = "gör texten större"
    project._restyle_lock = True
    project._restyle_snapshot = copy.deepcopy(project.editor.plan)
    project._restyle_applied = False
    project.editor.plan["captions"] = [project.editor.plan["captions"][0]]
    project.editor.plan["clips"] = project.editor.plan["clips"][:1]
    second = project.commit(lambda *_args, **_kwargs: None)
    assert second["ok"], second
    assert [cap["text"] for cap in project.editor.plan["captions"]] == lines_before
    assert int(project.editor.plan["style"]["captionFontSize"]) > size_before
    v1_bytes = v1.read_bytes()
    v2 = project.folder / "versions" / "v2" / "output.mp4"
    assert v2.exists()
    assert v1.read_bytes() == v1_bytes
    assert project.original.read_bytes() == before
    subprocess.check_call(["ffmpeg", "-v", "error", "-i", str(v2), "-f", "null", "-"], stdout=subprocess.DEVNULL)
    for sample in (0.3, 1.0, 3.0):
        frame = tmp_path / f"v2-{sample}.png"
        _frame(v2, sample, frame)
        assert _white_pixels(frame) > whites_before[sample]

    artifacts = os.environ.get("CAPCUT_ARTIFACTS")
    if artifacts:
        root = Path(artifacts)
        root.mkdir(parents=True, exist_ok=True)
        shutil.copy(v1, root / "auto-v1.mp4")
        shutil.copy(v2, root / "auto-v2-storre.mp4")
        for sample in (0.3, 1.0, 3.0):
            shutil.copy(tmp_path / f"v1-{sample}.png", root / f"frame-v1-{sample}.png")
            shutil.copy(tmp_path / f"v2-{sample}.png", root / f"frame-v2-{sample}.png")
