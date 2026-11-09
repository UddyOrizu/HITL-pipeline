"""Runtime settings, read from environment variables prefixed POR_."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split(value: str | None) -> list[str]:
    return [v.strip().rstrip("/") for v in (value or "").replace(";", ",").split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POR_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./var/por.db"
    secret_key: str = "dev-only-insecure-secret-change-me-in-production"
    # Key for encrypting stored secrets (connector credentials, tenant SSO client secrets). Defaults to a key
    # derived from secret_key; set it separately so rotating one does not affect the other.
    encryption_key: str | None = None
    public_base_url: str = "http://localhost:8000"
    # Where people open the web app (SSO redirects, links in emails). Defaults to public_base_url.
    app_base_url: str | None = None

    # Browser origins allowed to call the API directly (comma-separated). Empty means same-origin only, which is
    # what the bundled web app needs (it is served by the API, or proxied by Vite in development).
    cors_origins: str = ""

    # Jobs: run the processing pipeline inline (dev/test) or via Redis Streams workers.
    inline_jobs: bool = True
    redis_url: str | None = None
    job_max_attempts: int = 5  # event handlers and jobs are retried with backoff, then dead-lettered
    processing_max_attempts: int = 3  # transient processing failures (conversion, model calls) are retried
    stuck_after_minutes: int = 15  # submissions stuck in received/processing longer than this are re-queued
    # Run periodic tasks (webhook retries, SLAs, queued assignments, polling, retention) inside the API process.
    # Defaults to on when jobs run inline and no separate worker is expected.
    scheduler_in_api: bool | None = None
    scheduler_interval_seconds: float = 15.0

    # Object storage: local directory, S3/MinIO when s3_bucket is set, Azure Blob when azure_storage_account_url is set.
    storage_dir: str = "./var/storage"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    azure_storage_account_url: str | None = None  # https://<account>.blob.core.windows.net (managed identity)
    azure_storage_container: str = "por-files"

    # Ingestion limits and helpers.
    max_upload_mb: int = 50
    libreoffice_url: str | None = None  # LibreOffice worker HTTP endpoint
    soffice_path: str = "soffice"
    clamav_host: str | None = None
    clamav_port: int = 3310
    # Refuse uploads when no virus scanner is configured. Defaults to on in prod.
    require_virus_scan: bool | None = None

    # Model gateway. "heuristic" is deterministic and offline.
    model_provider: str = "heuristic"  # heuristic | anthropic | openai
    model_id: str = "claude-sonnet-5-5"
    model_api_key: str | None = None
    model_base_url: str | None = None  # customer-supplied endpoint (anthropic or openai provider)

    # Auth.
    dev_login_enabled: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None  # the client id; ID tokens must be issued to it
    oidc_jwks_url: str | None = None  # optional: discovered from the issuer when not set
    oidc_client_id: str | None = None  # defaults to oidc_audience
    oidc_client_secret: str | None = None  # optional: public clients use PKCE only
    oidc_authorization_endpoint: str | None = None  # optional: discovered from the issuer
    oidc_token_endpoint: str | None = None  # optional: discovered from the issuer
    oidc_scopes: str = "openid email profile"
    token_ttl_minutes: int = 12 * 60
    # Signed-in sessions refresh themselves while in use, up to this many hours after signing in.
    session_max_hours: int = 24

    # Local passwords, for users who sign in without SSO. Works in every environment, unlike dev login.
    password_login_enabled: bool = True
    password_min_length: int = 12
    login_max_attempts: int = 5  # failed attempts before the account is locked
    login_lockout_minutes: int = 15
    password_reset_ttl_minutes: int = 60

    # Rate limits (per client IP for sign-in endpoints, per source app for submissions).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 20
    rate_limit_submissions_per_minute: int = 300
    trust_forwarded_for: bool = False  # use X-Forwarded-For for the client IP (behind a trusted proxy only)

    # Initial admin, created at API start-up when there is no user with this email in the tenant yet.
    # The email is the username. An existing account is never changed, so the password can be removed afterwards.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Administrator"
    bootstrap_tenant_name: str = "Default"

    # Webhooks.
    webhook_retry_window_hours: int = 24
    webhook_timeout_seconds: float = 10.0
    # Allow webhook and connector URLs on private networks (and plain http). Defaults to off in prod.
    allow_private_targets: bool | None = None

    # Email (password resets and notifications). Without smtp_host, emails are written to mail_dir.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_from: str = "Proof of Review <no-reply@localhost>"
    mail_dir: str = "./var/mail"

    # Observability.
    metrics_enabled: bool = True
    metrics_token: str | None = None  # when set, GET /metrics needs "Authorization: Bearer <token>"
    log_format: str = "text"  # text | json
    sentry_dsn: str | None = None

    # Reading speed used for expected review time and effort estimates.
    words_per_minute: int = 200

    # --- Derived values ---------------------------------------------------------------

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def cors_origin_list(self) -> list[str]:
        return _split(self.cors_origins)

    @property
    def app_url(self) -> str:
        return (self.app_base_url or self.public_base_url).rstrip("/")

    @property
    def virus_scan_required(self) -> bool:
        return self.is_prod if self.require_virus_scan is None else self.require_virus_scan

    @property
    def private_targets_allowed(self) -> bool:
        return (not self.is_prod) if self.allow_private_targets is None else self.allow_private_targets

    @property
    def run_scheduler_in_api(self) -> bool:
        if self.scheduler_in_api is not None:
            return self.scheduler_in_api
        return self.env != "test" and (self.inline_jobs or not self.redis_url)

    @property
    def oidc_client(self) -> str | None:
        return self.oidc_client_id or self.oidc_audience

    @property
    def global_sso_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client)


@lru_cache
def get_settings() -> Settings:
    # POR_ENV_FILE picks another env file; set it empty to ignore .env (the test suite does).
    import os

    return Settings(_env_file=os.environ.get("POR_ENV_FILE", ".env") or None)
