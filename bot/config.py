"""Application configuration loaded from environment variables."""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the application."""

    bot_token: str | None = Field(default=None, validation_alias="BOT_TOKEN", repr=False)
    download_dir: Path = Field(default=Path("downloads"), validation_alias="DOWNLOAD_DIR")
    max_file_size_mb: int = Field(default=50, ge=1, validation_alias="MAX_FILE_SIZE_MB")
    max_retained_originals_mb: int = Field(default=512, ge=0, validation_alias="MAX_RETAINED_ORIGINALS_MB")
    max_concurrent_downloads: int = Field(
        default=3,
        ge=1,
        validation_alias="MAX_CONCURRENT_DOWNLOADS",
    )
    max_concurrent_conversions: int = Field(default=1, ge=1, le=32, validation_alias="MAX_CONCURRENT_CONVERSIONS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    cache_enabled: bool = Field(default=True, validation_alias="CACHE_ENABLED")
    analysis_cache_ttl_seconds: float = Field(default=300, gt=0, allow_inf_nan=False, validation_alias="ANALYSIS_CACHE_TTL_SECONDS")
    analysis_cache_capacity: int = Field(default=512, ge=1, le=10000, validation_alias="ANALYSIS_CACHE_CAPACITY")
    cache_db_path: Path = Field(default=Path("data/cache.sqlite3"), validation_alias="CACHE_DB_PATH")
    cache_ttl_seconds: int = Field(default=604800, ge=1, validation_alias="CACHE_TTL_SECONDS")
    max_pending_requests: int = Field(default=32, ge=1, le=1000, validation_alias="MAX_PENDING_REQUESTS")
    user_requests_per_minute: int = Field(default=5, ge=1, validation_alias="USER_REQUESTS_PER_MINUTE")
    user_cooldown_seconds: float = Field(default=2, ge=0, allow_inf_nan=False, validation_alias="USER_COOLDOWN_SECONDS")
    max_user_operations: int = Field(default=1, ge=1, validation_alias="MAX_USER_OPERATIONS")
    operation_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False, validation_alias="OPERATION_TIMEOUT_SECONDS")
    upload_timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False, validation_alias="UPLOAD_TIMEOUT_SECONDS")
    http_max_connections: int = Field(default=6, ge=1, le=100, validation_alias="HTTP_MAX_CONNECTIONS")
    cleanup_interval_seconds: float = Field(default=300, gt=0, allow_inf_nan=False, validation_alias="CLEANUP_INTERVAL_SECONDS")
    abandoned_file_seconds: float = Field(default=3600, gt=0, allow_inf_nan=False, validation_alias="ABANDONED_FILE_SECONDS")
    shutdown_grace_seconds: float = Field(default=15, gt=0, allow_inf_nan=False, validation_alias="SHUTDOWN_GRACE_SECONDS")
    verify_full_decode: bool = Field(default=False, validation_alias="VERIFY_FULL_DECODE")
    telegram_api_base: str | None = Field(default=None, validation_alias="TELEGRAM_API_BASE")
    telegram_local_upload_limit_mb: int = Field(default=2000, ge=1, le=2000,
                                                validation_alias="TELEGRAM_LOCAL_UPLOAD_LIMIT_MB")

    @field_validator("telegram_api_base", mode="before")
    @classmethod
    def validate_api_base(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("Expected an HTTP(S) Bot API server base URL")
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or parts.path not in {"", "/"}):
            raise ValueError("Expected a Bot API origin without credentials, path or query")
        return value.rstrip("/")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    @field_validator("bot_token", mode="before")
    @classmethod
    def normalize_token(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("download_dir", "cache_db_path", mode="before")
    @classmethod
    def reject_empty_paths(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("Path must not be empty")
        return value

    @field_validator("download_dir", "cache_db_path")
    @classmethod
    def expand_paths(cls, value: Path) -> Path:
        try:
            return value.expanduser()
        except RuntimeError as exc:
            raise ValueError("Unable to expand path") from exc

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Normalize log levels while rejecting unsupported values."""
        normalized = value.strip().upper()
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed_levels:
            raise ValueError(f"Unsupported log level: {value}")
        return normalized



