"""Central configuration, loaded once from environment variables / `.env`."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"
RESULTS_DIR = SAMPLES_DIR / "results"

load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Project ------------------------------------------------------------------
PROJECT_NAME = os.getenv("PROJECT_NAME", "Multi-Modal Plant Leaf Disease Diagnosis & Treatment System")
PROJECT_SLUG = os.getenv("PROJECT_SLUG", "plant-leaf")
PROJECT_NUMBER = _int("PROJECT_NUMBER", 1)
VERSION = "1.3.0"

# --- Server -----------------------------------------------------------------
HOST = os.getenv("HOST", "127.0.0.1")
PORT = _int("PORT", 8001)
MAX_UPLOAD_MB = max(1, _int("MAX_UPLOAD_MB", 8))
DB_PATH = BASE_DIR / os.getenv("DB_FILE", "agri_pathology.db")
TRUST_PROXY = _flag("TRUST_PROXY")

# --- Demo mode (off for the student download) ------------------------------------
DEMO_MODE = _flag("DEMO_MODE")
DEMO_TICKET_SECRET = os.getenv("DEMO_TICKET_SECRET", "").strip()
DEMO_DAILY_CAP = max(1, _int("DEMO_DAILY_CAP", 200))
DEMO_MAX_UPLOAD_MB = max(1, _int("DEMO_MAX_UPLOAD_MB", 3))
UPLOAD_LIMIT_MB = DEMO_MAX_UPLOAD_MB if DEMO_MODE else MAX_UPLOAD_MB

# --- AI (Groq only) -------------------------------------------------------------
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
GROQ_VISION_FALLBACK_MODEL = os.getenv("GROQ_VISION_FALLBACK_MODEL", "qwen/qwen3.8-27b")
KEY_PLACEHOLDER = "your_groq_key_here"
LLM_TIMEOUT_SECONDS = 60          # compare mode
GATE_TIMEOUT_SECONDS = 12.0       # node 1
GATE_MAX_TOKENS = 150
BUNDLE_TIMEOUT_SECONDS = 30.0     # consolidated nodes 2 + 4 + 5
BUNDLE_MAX_TOKENS = 1100
WEATHER_TIMEOUT_SECONDS = 5.0     # node 3

PROVIDERS = ("groq",)
ENV_KEY_NAMES = {"groq": "GROQ_API_KEY"}
