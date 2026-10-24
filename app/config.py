import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".doc", ".docx"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Keep prompts bounded so a huge page or resume can't blow the context window.
MAX_RESUME_CHARS = 20_000
MAX_JOB_CHARS = 15_000

# Per-IP limits (requests per hour). 0 disables a limit.
RATE_LIMIT_ANALYSES_PER_HOUR = int(os.getenv("RATE_LIMIT_ANALYSES_PER_HOUR", "5"))
RATE_LIMIT_VALIDATIONS_PER_HOUR = int(os.getenv("RATE_LIMIT_VALIDATIONS_PER_HOUR", "20"))

STATIC_DIR = Path(__file__).parent / "static"
