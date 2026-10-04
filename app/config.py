from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (or a local `.env`)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://examable:examable@localhost:5432/examable"
    upload_dir: str = "uploads"
    max_upload_mb: int = 25

    # When set, endpoints that mutate the question bank or spend LLM credits
    # require the `X-Admin-Token` header. Leave empty only for local development.
    admin_token: str | None = None

    multimodal_enabled: bool = False
    multimodal_min_quality: float = 0.72
    multimodal_api_base_url: str = "https://api.openai.com/v1"
    multimodal_api_key: str | None = None
    multimodal_model: str = "gpt-4.1-mini"
    multimodal_max_pages: int = 8

    correction_gen_enabled: bool = True
    correction_gen_model: str = ""
    correction_gen_batch_size: int = 5
    correction_gen_timeout_seconds: float = 300.0
    correction_gen_max_questions_per_job: int = 5000


settings = Settings()
