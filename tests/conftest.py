"""Keep local environment files and credentials out of unit-test defaults."""

import pytest


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    from bot.config import Settings

    # Explicit live invocations use the server environment, never the local .env.
    if request.node.get_closest_marker("live"):
        monkeypatch.setitem(Settings.model_config, "env_file", None)
        return
    for field in Settings.model_fields.values():
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
