import json
import subprocess
from pathlib import Path

from app.agent import _tool_calls, run_turn
from app.store import Store


ANALYSIS = {
    "source": {"duration": 4, "width": 320, "height": 240, "fps": 30, "hasAudio": False},
    "faces": [],
    "transcript": {"text": "", "words": []},
    "silences": [],
    "silenceTotal": 0,
    "scenes": [],
    "moments": [{"start": 0.2, "end": 1.4, "score": 1, "reasons": ["motion"], "text": ""}],
    "speakers": [],
    "speakerMethod": "none",
    "motion": [],
}


class _Fn:
    def __init__(self, name: str, args: dict):
        self.name = name
        self.arguments = json.dumps(args)


class _Call:
    def __init__(self, name: str, args: dict):
        self.id = f"call_{name}"
        self.function = _Fn(name, args)


class _Message:
    def __init__(self, content: str = "", tool=None):
        self.content = content
        self.tool_calls = [_Call(*tool)] if tool else None


class _Choice:
    def __init__(self, message: _Message):
        self.message = message


class _Response:
    def __init__(self, message: _Message):
        self.choices = [_Choice(message)]


class FakeLLM:
    def __init__(self):
        self.chat = self
        self.completions = self
        self.step = 0

    def create(self, **kwargs):
        if "tools" not in kwargs:
            return _Response(_Message("Versionen är klar."))
        self.step += 1
        steps = [
            ("analyze_video", {}),
            ("select_clip", {"sourceStart": 0.2, "sourceEnd": 1.6, "label": "hook"}),
            ("add_text", {"start": 0, "end": 1.2, "text": "LOOK", "accent": "LOOK"}),
            ("commit_render", {}),
        ]
        if self.step <= len(steps):
            return _Response(_Message(tool=steps[self.step - 1]))
        return _Response(_Message("klar"))


def test_loose_tool_call_parser():
    message = _Message('<tool_call>{"name":"undo","arguments":{}}</tool_call>')
    calls = _tool_calls(message)
    assert calls[0]["name"] == "undo"


def test_agent_loop_mutates_plan_and_commits(tmp_path: Path):
    source = tmp_path / "in.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    project = Store(tmp_path / "projects").create("in.mp4", source)
    project.editor.analysis = ANALYSIS

    def fake_render(current):
        current.editor.active = 1
        current.editor.dirty = False
        current.editor.versions.append({"version": 1, "plan": current.editor.plan, "probe": {"duration": 1.4, "width": 1080, "height": 1920, "videoCodec": "h264"}})
        return {"ok": True, "version": 1, "probe": {"duration": 1.4, "width": 1080, "height": 1920, "videoCodec": "h264"}}

    events = []
    reply = run_turn(project, "Gör den här till en viral Short", lambda event, data: events.append(event), llm=FakeLLM(), force_render=fake_render)
    assert project.editor.plan["clips"]
    assert project.editor.plan["effects"]
    assert project.editor.active == 1
    assert "klar" in reply.lower()
    assert "done" in events


def test_stalled_model_still_commits_a_plan_from_analysis(tmp_path: Path):
    source = tmp_path / "in.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    project = Store(tmp_path / "projects").create("in.mp4", source)
    project.editor.analysis = ANALYSIS

    class Stall(FakeLLM):
        def create(self, **kwargs):
            if "tools" not in kwargs:
                return _Response(_Message("Versionen är klar."))
            self.step += 1
            if self.step == 1:
                return _Response(_Message(tool=("analyze_video", {})))
            return _Response(_Message("Here is a long summary instead of an edit."))

    def fake_render(current):
        current.editor.active = 1
        current.editor.dirty = False
        current.editor.versions.append({"version": 1, "plan": current.editor.plan, "probe": {"duration": 1.2, "width": 1080, "height": 1920, "videoCodec": "h264"}})
        return {"ok": True, "version": 1, "probe": {"duration": 1.2, "width": 1080, "height": 1920, "videoCodec": "h264"}}

    run_turn(project, "Gör den här till en viral Short", lambda *_: None, llm=Stall(), force_render=fake_render)
    assert project.editor.plan["clips"]
    assert project.editor.active == 1


def test_bigger_text_keeps_lines_even_if_the_model_replaces_them(tmp_path: Path):
    source = tmp_path / "in.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    project = Store(tmp_path / "projects").create("in.mp4", source)
    project.editor.analysis = ANALYSIS
    project.editor.select_clip(0.2, 2.0)
    project.editor.add_caption(0.0, 0.8, "one line")
    project.editor.add_caption(0.8, 1.6, "two line")
    project.editor.add_caption(1.6, 2.4, "three line")
    project.editor.versions.append({"version": 1, "plan": json.loads(json.dumps(project.editor.plan)), "probe": {}})
    project.editor.active = 1
    project.editor.dirty = False
    texts = [cap["text"] for cap in project.editor.plan["captions"]]

    class Wipe(FakeLLM):
        def create(self, **kwargs):
            if "tools" not in kwargs:
                return _Response(_Message("Versionen är klar."))
            self.step += 1
            if self.step == 1:
                return _Response(_Message(tool=("replace_captions", {"captions": [{"start": 0.2, "end": 0.6, "text": "ONLY"}]})))
            return _Response(_Message(tool=("commit_render", {})))

    def fake_render(current):
        current.editor.active = 2
        current.editor.dirty = False
        return {"ok": True, "version": 2, "probe": {"duration": 1.8, "width": 1080, "height": 1920, "videoCodec": "h264"}}

    run_turn(project, "gör texten större", lambda *_: None, llm=Wipe(), force_render=fake_render)
    assert [cap["text"] for cap in project.editor.plan["captions"]] == texts
    assert project.editor.plan["style"]["captionFontSize"] > 72
    assert len(project.editor.plan["clips"]) == 1


def test_undo_message_steps_back_without_a_new_render(tmp_path: Path):
    source = tmp_path / "in.mp4"
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.DEVNULL,
    )
    project = Store(tmp_path / "projects").create("in.mp4", source)
    project.editor.analysis = ANALYSIS
    project.editor.select_clip(0.2, 1.6)
    project.editor.versions.append({"version": 1, "plan": json.loads(json.dumps(project.editor.plan)), "probe": {}})
    project.editor.active = 1
    project.editor.dirty = False
    project.editor.undo_stack.clear()
    project.editor.select_clip(2.0, 3.2)
    project.editor.versions.append({"version": 2, "plan": json.loads(json.dumps(project.editor.plan)), "probe": {}})
    project.editor.active = 2
    project.editor.dirty = False
    project.editor.undo_stack.clear()

    class UndoThenRender(FakeLLM):
        def create(self, **kwargs):
            if "tools" not in kwargs:
                return _Response(_Message("Versionen är klar."))
            choice = kwargs.get("tool_choice")
            if isinstance(choice, dict):
                return _Response(_Message(tool=("undo", {})))
            return _Response(_Message(tool=("commit_render", {})))

    renders = []

    def fake_render(current):
        renders.append(1)
        return {"ok": True, "version": 9, "probe": {"duration": 1, "width": 1080, "height": 1920, "videoCodec": "h264"}}

    reply = run_turn(project, "undo", lambda *_: None, llm=UndoThenRender(), force_render=fake_render)
    assert project.editor.active == 1
    assert renders == []
    assert "v1" in reply
