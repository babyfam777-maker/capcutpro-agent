"""Delivery spec for a Short. The v1 renderer stays 1080x1920 at 30 fps."""

from __future__ import annotations

TARGET_W = 2160
TARGET_H = 3840
TARGET_FPS = 60.0

SHORT_EXPORT = {"width": TARGET_W, "height": TARGET_H, "fps": TARGET_FPS}


def describe_export(source: dict | None, target: dict | None = None) -> dict:
    """Say upscaled or interpolated when the source is below the delivery spec.

    A lower-resolution or lower-frame-rate source is never labeled native.
    """
    target = target or SHORT_EXPORT
    width = int((source or {}).get("width") or 0)
    height = int((source or {}).get("height") or 0)
    fps = float((source or {}).get("fps") or 0)
    target_w = int(target["width"])
    target_h = int(target["height"])
    target_fps = float(target["fps"])
    upscaled = width < target_w or height < target_h
    interpolated = fps + 0.4 < target_fps
    parts = []
    if upscaled:
        parts.append("upscaled")
    if interpolated:
        parts.append("interpolated")
    quality = ",".join(parts) if parts else "native"
    if quality != "native" and "native" in quality:
        quality = ",".join(part for part in parts if part != "native")
    return {
        "width": target_w,
        "height": target_h,
        "fps": target_fps,
        "upscaled": upscaled,
        "interpolated": interpolated,
        "quality": quality,
    }
