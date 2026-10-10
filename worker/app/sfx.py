"""Short sound effects generated in-process. No third-party music."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .config import SFX_DIR

SR = 48000


def ensure_sfx() -> dict[str, Path]:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    makers = {
        "whoosh": _whoosh,
        "pop": _pop,
        "impact": _impact,
        "riser": _riser,
        "thump": _thump,
    }
    paths = {}
    for name, maker in makers.items():
        path = SFX_DIR / f"{name}.wav"
        if not path.exists() or path.stat().st_size < 1000:
            _write(path, maker())
        paths[name] = path
    return paths


def _write(path: Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples, -1, 1)
    ints = (pcm * 32767).astype(np.int16)
    stereo = np.column_stack([ints, ints]).reshape(-1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(stereo.tobytes())


def _env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    dur = n / SR
    att = np.clip(t / max(attack, 1e-4), 0, 1)
    rel = np.clip((dur - t) / max(release, 1e-4), 0, 1)
    return att * rel


def _whoosh() -> np.ndarray:
    dur = 0.28
    n = int(SR * dur)
    t = np.arange(n) / SR
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(n)
    # Crude one-pole sweep by multiplying a rising chirp.
    phase = 2 * np.pi * (500 * t + (2800 - 500) * t * t / (2 * dur))
    chirp = np.sin(phase)
    return 0.45 * (0.65 * chirp + 0.35 * noise) * _env(n, 0.02, 0.12)


def _pop() -> np.ndarray:
    n = int(SR * 0.09)
    t = np.arange(n) / SR
    freq = 1300 - (1300 - 620) * (t / t[-1])
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return 0.8 * np.sin(phase) * np.exp(-t * 40)


def _impact() -> np.ndarray:
    n = int(SR * 0.55)
    t = np.arange(n) / SR
    freq = 90 * np.exp(-t * 4) + 38
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.tanh(1.8 * np.sin(phase)) * np.exp(-t * 6)
    return 0.9 * body


def _riser() -> np.ndarray:
    n = int(SR * 0.9)
    t = np.arange(n) / SR
    freq = 180 + (1600 - 180) * (t / t[-1]) ** 2
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return 0.35 * np.sin(phase) * _env(n, 0.05, 0.08)


def _thump() -> np.ndarray:
    n = int(SR * 0.22)
    t = np.arange(n) / SR
    return 0.9 * np.sin(2 * np.pi * 70 * t) * np.exp(-t * 18)
