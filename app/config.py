"""Central configuration, loaded once from environment variables / `.env`."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = STATIC_DIR / "samples"

load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


# --- Server -----------------------------------------------------------------
HOST = os.getenv("HOST", "127.0.0.1")
PORT = _int("PORT", 8001)
MAX_UPLOAD_MB = max(1, _int("MAX_UPLOAD_MB", 8))
DB_PATH = BASE_DIR / os.getenv("DB_FILE", "agri_pathology.db")
TRUST_PROXY = _flag("TRUST_PROXY")
ALLOW_REMOTE_KEY_EDIT = _flag("ALLOW_REMOTE_KEY_EDIT")

# --- Demo quota ---------------------------------------------------------------
RATE_LIMIT_PER_HOUR = max(1, _int("RATE_LIMIT_PER_HOUR", 15))
RATE_WINDOW_SECONDS = 3600

# --- AI providers -------------------------------------------------------------
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_VISION_MODEL_FALLBACK = os.getenv("GROQ_VISION_MODEL_FALLBACK", "llama-3.2-11b-vision-preview")
GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
LLM_TIMEOUT_SECONDS = 60          # compare mode
GATE_TIMEOUT_SECONDS = 12.0       # node 1
GATE_MAX_TOKENS = 150
BUNDLE_TIMEOUT_SECONDS = 22.0     # consolidated nodes 2 + 4 + 5
BUNDLE_MAX_TOKENS = 1100
WEATHER_TIMEOUT_SECONDS = 5.0     # node 3

PROVIDERS = ("groq",)
ENV_KEY_NAMES = {"groq": "GROQ_API_KEY"}
