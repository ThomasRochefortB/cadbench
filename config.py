from pathlib import Path

FREECAD_DOCKER_IMAGE = "linuxserver/freecad:0.20.2"
ARTIFACTS_DIR = Path("static") / "generated"
ARTIFACT_TTL_SECONDS = 24 * 60 * 60

DEFAULT_MODEL = "inclusionai/ling-2.6-flash:free"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_USER_MODELS_URL = "https://openrouter.ai/api/v1/models/user"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_APP_REFERER = "http://localhost:8000"
OPENROUTER_APP_TITLE = "CADBench"
DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:8000/mcp/"
MODEL_REQUEST_TIMEOUT = 10.0
GENERATION_REQUEST_TIMEOUT = 120.0
DEFAULT_OPENROUTER_MAX_TOKENS = 8192
MIN_RECOMMENDED_CONTEXT_LENGTH = 4096

MODEL_DISPLAY_NAMES = {
    "inclusionai/ling-2.6-1t:free": "inclusionAI: Ling-2.6-1T (free)",
    "tencent/hy3-preview:free": "Tencent: Hy3 preview (free)",
    "inclusionai/ling-2.6-flash:free": "inclusionAI: Ling-2.6-flash (free)",
    "google/gemma-4-26b-a4b-it:free": "Google: Gemma 4 26B A4B (free)",
    "google/gemma-4-31b-it:free": "Google: Gemma 4 31B (free)",
}
