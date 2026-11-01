"""Runtime settings, read from environment variables prefixed POR_."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POR_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./var/por.db"
    secret_key: str = "dev-only-insecure-secret-change-me-in-production"
    public_base_url: str = "http://localhost:8000"

    # Jobs: run the processing pipeline inline (dev/test) or via Redis Streams workers.
    inline_jobs: bool = True
    redis_url: str | None = None

    # Object storage: local directory, or S3/MinIO when s3_endpoint/s3_bucket are set.
    storage_dir: str = "./var/storage"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # Ingestion limits and helpers.
    max_upload_mb: int = 50
    libreoffice_url: str | None = None  # LibreOffice worker HTTP endpoint
    soffice_path: str = "soffice"
    clamav_host: str | None = None
    clamav_port: int = 3310

    # Model gateway. "heuristic" is deterministic and offline.
    model_provider: str = "heuristic"  # heuristic | anthropic | openai
    model_id: str = "claude-sonnet-5-5"
    model_api_key: str | None = None
    model_base_url: str | None = None  # customer-supplied endpoint

    # Auth.
    dev_login_enabled: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    token_ttl_minutes: int = 12 * 60

    # Webhooks.
    webhook_retry_window_hours: int = 24
    webhook_timeout_seconds: float = 10.0

    # Reading speed used for expected review time and effort estimates.
    words_per_minute: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
