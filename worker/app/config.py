import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT.parent / "data")).resolve()
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
EMOJI_DIR = ASSETS / "emoji"
SFX_DIR = ASSETS / "sfx"

# Local Ollama by default. Nothing in this process calls Gemini, OpenAI, Anthropic, or Grok.
# A remote OpenAI-compatible server is used only when LLM_ALLOW_REMOTE=1 is set on purpose.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ollama")
MODEL = os.environ.get("MODEL", "qwen2.5:3b")
LLM_ALLOW_REMOTE = os.environ.get("LLM_ALLOW_REMOTE", "").strip() in {"1", "true", "yes"}
# phone: 1080x1920 at 30 fps, the file a phone can play on a free CPU.
# 4k: PR #8's 2160x3840 at 60 fps. The file is labeled upscaled when the source is smaller.
SHORT_DELIVERY = os.environ.get("SHORT_DELIVERY", "phone").strip().lower()
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

OUT_W = 1080
OUT_H = 1920
FPS = 30

DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(DATA_DIR / "models"))
