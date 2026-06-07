from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover - fallback for minimal local runtime
    BaseSettings = None
    SettingsConfigDict = None


def _read_dotenv(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        env[key.strip()] = value.strip()
    return env


if BaseSettings is not None:

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

        database_url: str = "postgresql+psycopg://examable:examable@localhost:5432/examable"
        redis_url: str = "redis://localhost:6379/0"
        upload_dir: str = "uploads"

        multimodal_enabled: bool = False
        multimodal_min_quality: float = 0.72
        multimodal_api_base_url: str = "https://api.openai.com/v1"
        multimodal_api_key: str | None = None
        multimodal_model: str = "gpt-4.1-mini"
        multimodal_max_pages: int = 8

        correction_gen_enabled: bool = True
        correction_gen_model: str = ""
        correction_gen_batch_size: int = 5
        correction_gen_timeout_seconds: float = 60.0
        correction_gen_max_questions_per_job: int = 5000


    settings = Settings()
else:
    dot = _read_dotenv(".env")

    @dataclass
    class Settings:
        database_url: str = dot.get(
            "DATABASE_URL", "postgresql+psycopg://examable:examable@localhost:5432/examable"
        )
        redis_url: str = dot.get("REDIS_URL", "redis://localhost:6379/0")
        upload_dir: str = dot.get("UPLOAD_DIR", "uploads")
        multimodal_enabled: bool = dot.get("MULTIMODAL_ENABLED", "false").lower() == "true"
        multimodal_min_quality: float = float(dot.get("MULTIMODAL_MIN_QUALITY", "0.72"))
        multimodal_api_base_url: str = dot.get("MULTIMODAL_API_BASE_URL", "https://api.openai.com/v1")
        multimodal_api_key: str | None = dot.get("MULTIMODAL_API_KEY") or os.getenv("MULTIMODAL_API_KEY")
        multimodal_model: str = dot.get("MULTIMODAL_MODEL", "gpt-4.1-mini")
        multimodal_max_pages: int = int(dot.get("MULTIMODAL_MAX_PAGES", "8"))
        correction_gen_enabled: bool = dot.get("CORRECTION_GEN_ENABLED", "true").lower() == "true"
        correction_gen_model: str = dot.get("CORRECTION_GEN_MODEL", "")
        correction_gen_batch_size: int = int(dot.get("CORRECTION_GEN_BATCH_SIZE", "5"))
        correction_gen_timeout_seconds: float = float(dot.get("CORRECTION_GEN_TIMEOUT_SECONDS", "60.0"))
        correction_gen_max_questions_per_job: int = int(dot.get("CORRECTION_GEN_MAX_QUESTIONS_PER_JOB", "5000"))


    settings = Settings()
