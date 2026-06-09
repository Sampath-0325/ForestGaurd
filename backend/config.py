import os
import logging
import secrets
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Find .env file ───────────────────────────────────────────────────────────
_THIS_DIR    = Path(__file__).resolve().parent          # backend/
_PROJECT_DIR = _THIS_DIR.parent                         # project root
_CWD         = Path.cwd()

ENV_FILE = None
for candidate in [_PROJECT_DIR / ".env", _CWD / ".env", _THIS_DIR / ".env"]:
    if candidate.exists():
        ENV_FILE = candidate
        break

if ENV_FILE is None:
    ENV_FILE = _PROJECT_DIR / ".env"

# ── Load .env into os.environ FIRST ──────────────────────────────────────────
# override=True so the Huey worker process (separate PID) always gets values.
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=True)
except ImportError:
    pass


# ── Settings ─────────────────────────────────────────────────────────────────
class Settings(BaseSettings):
    # Database
    database_url: str = Field("sqlite:///./forestguard.db", env="DATABASE_URL")

    # Auth
    secret_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        env="SECRET_KEY"
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    # Environment
    environment: str = Field("development", env="ENVIRONMENT")

    # ── OpenRouter AI ─────────────────────────────────────────────────────────
    # Get a free key at https://openrouter.ai/keys  (no credit card needed)
    openrouter_api_key: str = Field("", env="OPENROUTER_API_KEY")

    # "openrouter/free" = auto-selects any working free model.
    # If you want a specific model set e.g.:
    #   OPENROUTER_MODEL=meta-llama/llama-4-scout:free
    openrouter_model: str = Field("openrouter/free", env="OPENROUTER_MODEL")

    use_mock_chatbot: bool = Field(False, env="USE_MOCK_CHATBOT")

    # ── Email ─────────────────────────────────────────────────────────────────
    smtp_host:     str = Field("smtp.gmail.com", env="SMTP_HOST")
    smtp_port:     int = Field(587,              env="SMTP_PORT")
    smtp_user:     str = Field("",               env="SMTP_USER")
    smtp_password: str = Field("",               env="SMTP_PASSWORD")
    smtp_from:     str = Field("",               env="SMTP_FROM")
    app_base_url:  str = Field("http://127.0.0.1:8000", env="APP_BASE_URL")

    # ── Twilio SMS (optional) ─────────────────────────────────────────────────
    twilio_account_sid: str = Field("", env="TWILIO_ACCOUNT_SID")
    twilio_auth_token:  str = Field("", env="TWILIO_AUTH_TOKEN")
    twilio_from_number: str = Field("", env="TWILIO_FROM_NUMBER")

    # ── GEE ───────────────────────────────────────────────────────────────────
    gee_service_account_path:       str = Field("", env="GEE_SERVICE_ACCOUNT_PATH")
    google_application_credentials: str = Field("", env="GOOGLE_APPLICATION_CREDENTIALS")

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


logger = logging.getLogger("forestguard.config")


def get_settings() -> Settings:
    return Settings()


settings = get_settings()

if "pytest" not in sys.modules:
    logger.info(f"[CONFIG] .env path:              {ENV_FILE}")
    logger.info(f"[CONFIG] .env exists:             {ENV_FILE.exists()}")
    logger.info(f"[CONFIG] OPENROUTER_API_KEY set:  {bool(settings.openrouter_api_key)}")
    logger.info(f"[CONFIG] OPENROUTER_MODEL:        {settings.openrouter_model}")
    logger.info(f"[CONFIG] SMTP_USER set:           {bool(settings.smtp_user)}")
    logger.info(f"[CONFIG] SMTP_PASSWORD set:       {bool(settings.smtp_password)}")
    if settings.openrouter_api_key:
        logger.info(f"[CONFIG] Key prefix:              {settings.openrouter_api_key[:14]}...")
    else:
        logger.warning("[CONFIG] OPENROUTER_API_KEY is empty — chat will not work!")
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("[CONFIG] SMTP credentials missing — email alerts will not send!")


