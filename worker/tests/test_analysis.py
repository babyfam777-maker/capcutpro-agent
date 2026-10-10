import subprocess
from pathlib import Path

from app.analysis import analyze


def _talking_clip(path: Path) -> None:
    wav = path.with_suffix(".wav")
    subprocess.check_call(["espeak-ng", "-v", "en", "-w", str(wav), "Wait for the punchline. Look again."], stdout=subprocess.DEVNULL)
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x224466:s=640x360:r=30:d=2",
            "-f", "lavfi", "-i", "color=c=0x662222:s=640x360:r=30:d=2",
            "-i", str(wav),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
    )


def test_analysis_has_structure_and_words(tmp_path: Path):
    source = tmp_path / "talk.mp4"
    _talking_clip(source)
    result = analyze(source)
    source_meta = result["source"]
    assert source_meta["width"] == 640
    assert source_meta["height"] == 360
    assert source_meta["hasAudio"] is True
    assert source_meta["duration"] > 1
    assert isinstance(result["scenes"], list)
    assert result["silences"] == [] or all("start" in s for s in result["silences"])
    assert isinstance(result["motion"], list)
    assert isinstance(result["faces"], list)
    text = result["transcript"]["text"].lower()
    words = result["transcript"]["words"]
    assert words, text
    assert any(word["end"] > word["start"] for word in words)
    joined = " ".join(word["text"].lower() for word in words)
    assert "punchline" in joined or "look" in joined or "wait" in joined
    assert result["moments"]
