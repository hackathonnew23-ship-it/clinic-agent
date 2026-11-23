"""Central configuration. Everything comes from environment variables / .env.

No secrets are hardcoded anywhere in the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# Repo-root .env first, then backend/.env (neither overrides real environment variables).
load_dotenv(REPO_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    booking_provider: str
    clinic_api_url: str
    clinic_timezone: str
    hold_ttl_seconds: int
    sweeper_interval_seconds: int
    cors_origins: tuple[str, ...]


def get_settings() -> Settings:
    """Read settings from the environment on every call (cheap, and test friendly)."""
    default_db = f"sqlite:///{(BACKEND_DIR / 'clinic.db').as_posix()}"
    origins = os.getenv("CORS_ORIGINS", "*")
    return Settings(
        database_url=os.getenv("DATABASE_URL", default_db),
        booking_provider=os.getenv("BOOKING_PROVIDER", "mock_rest"),
        clinic_api_url=os.getenv("CLINIC_API_URL") or f"http://127.0.0.1:{os.getenv('PORT', '8000')}",
        clinic_timezone=os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata"),
        hold_ttl_seconds=int(os.getenv("HOLD_TTL_SECONDS", "300")),
        sweeper_interval_seconds=int(os.getenv("SWEEPER_INTERVAL_SECONDS", "30")),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
    )
