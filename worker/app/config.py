import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT.parent / "data")).resolve()
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
EMOJI_DIR = ASSETS / "emoji"
SFX_DIR = ASSETS / "sfx"

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ollama")
MODEL = os.environ.get("MODEL", "qwen2.5:3b")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

OUT_W = 1080
OUT_H = 1920
FPS = 30

DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(DATA_DIR / "models"))
