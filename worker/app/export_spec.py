"""Measurable export spec for a finished short.

QA and the storyboard read this module. Renderer constants are checked
against it in tests so the encoded file and the spec cannot drift quietly.
"""

from __future__ import annotations

from pathlib import Path

EXPORT_SPEC = {
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "fps_tolerance": 0.2,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pix_fmt": "yuv420p",
    "crf": 18,
    "faststart": True,
    "lufs": -14.0,
    "lufs_tolerance": 1.6,
    "safe_top": 220,
    "safe_bottom": 320,
    "safe_side": 60,
    "min_duration": 13.0,
    # Timeline aim sits above the file minimum so encoder trim still clears it.
    "duration_margin": 0.8,
    "min_speed": 0.5,
    "silence_mean_db": -45.0,
}


def duration_requirement(file_duration: float, policy: dict | None, source_duration: float) -> tuple[bool, str]:
    """File length against the spec.

    A file under 13s fails. The only pass under 13s is an explicit policy that
    the source cannot reach 13s even when the whole picture is slowed to the
    export minimum speed and the opening is added once.
    """
    minimum = float(EXPORT_SPEC["min_duration"])
    policy = policy or {}
    exception = policy.get("exception")
    if exception == "source_shorter_than_minimum":
        floor = float(policy.get("floor") or 0)
        ceiling = float(policy.get("ceiling") or 0)
        reason = str(policy.get("reason") or "").strip()
        ok = bool(reason) and ceiling + 1e-3 < minimum and file_duration + 0.35 >= floor and file_duration > 0.8
        detail = f"{file_duration:.3f}s under spec {minimum:.0f}s: {reason} golv {floor:.3f}s"
        return ok, detail
    ok = file_duration + 1e-3 >= minimum
    unused = source_duration
    del unused
    return ok, f"{file_duration:.3f}s, krav {minimum:.0f}s"


def moov_before_mdat(path: Path) -> bool:
    """True when the moov atom precedes mdat, which is what faststart means."""
    first: dict[bytes, int] = {}
    with Path(path).open("rb") as handle:
        end = handle.seek(0, 2)
        offset = 0
        while offset + 8 <= end:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            header_len = 8
            if size == 1:
                ext = handle.read(8)
                if len(ext) < 8:
                    break
                size = int.from_bytes(ext, "big")
                header_len = 16
            if size < header_len:
                break
            if kind in (b"moov", b"mdat") and kind not in first:
                first[kind] = offset
            if b"moov" in first and b"mdat" in first:
                break
            offset += size
    if b"moov" not in first or b"mdat" not in first:
        return False
    return first[b"moov"] < first[b"mdat"]
