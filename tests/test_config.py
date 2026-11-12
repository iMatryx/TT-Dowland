"""Tests for application configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.config import Settings


def test_settings_defaults() -> None:
    """Settings expose safe development defaults."""
    settings = Settings()

    assert settings.bot_token is None
    assert settings.download_dir == Path("downloads")
    assert settings.max_file_size_mb == 50
    assert settings.max_concurrent_downloads == 3
    assert settings.log_level == "INFO"
    assert settings.cache_enabled is True


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read and coerce supported environment variables."""
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    monkeypatch.setenv("CACHE_ENABLED", "false")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.bot_token == "test-token"
    assert settings.max_file_size_mb == 100
    assert settings.cache_enabled is False
    assert settings.log_level == "DEBUG"


def test_settings_reject_invalid_log_level() -> None:
    """Unsupported log levels fail validation."""
    with pytest.raises(ValidationError):
        Settings(LOG_LEVEL="trace")


@pytest.mark.parametrize("variable", ["DOWNLOAD_DIR", "CACHE_DB_PATH"])
def test_settings_reject_blank_paths(variable: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{variable: " "})


def test_example_env_covers_all_settings_without_real_token() -> None:
    from dotenv import dotenv_values

    example = Path(__file__).resolve().parents[1] / ".env.example"
    keys = set(dotenv_values(example))
    assert keys == {field.validation_alias for field in Settings.model_fields.values()}
    assert Settings(_env_file=example).bot_token is None


def test_user_home_paths_and_whitespace_are_normalized() -> None:
    settings = Settings(DOWNLOAD_DIR="~/downloads", CACHE_DB_PATH="~/cache.sqlite3", BOT_TOKEN="  ", LOG_LEVEL=" info ")
    assert settings.download_dir == Path.home() / "downloads"
    assert settings.cache_db_path == Path.home() / "cache.sqlite3"
    assert settings.bot_token is None
    assert settings.log_level == "INFO"

