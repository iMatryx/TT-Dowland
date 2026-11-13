"""Explicit language selection and persistent, isolated interface state."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services.cache import SQLiteCache


async def test_language_is_persistent_and_separate_from_media_cache(tmp_path):
    path = tmp_path / 'language.db'
    store = SQLiteCache(path)
    try:
        assert await store.get_language(1) is None
        token = await store.begin_language_choice(1, 10)
        assert await store.bind_language_choice(1, token, 20)
        assert await store.select_language(1, 10, 20, token, 'ru')
        assert await store.get_language(1) == 'ru'
        assert await store.get_language(2) is None
    finally:
        await store.close()
    reopened = SQLiteCache(path)
    try:
        assert await reopened.get_language(1) == 'ru'
    finally:
        await reopened.close()


async def test_selector_is_owner_chat_message_bound_and_single_use(tmp_path):
    store = SQLiteCache(tmp_path / 'db')
    try:
        token = await store.begin_language_choice(1, 10)
        await store.bind_language_choice(1, token, 20)
        for user, chat, message in [(2, 10, 20), (1, 11, 20), (1, 10, 21)]:
            assert not await store.select_language(user, chat, message, token, 'en')
        with pytest.raises(ValueError):
            await store.select_language(1, 10, 20, token, 'de')
        assert await store.select_language(1, 10, 20, token, 'en')
        assert not await store.select_language(1, 10, 20, token, 'ru')
        assert await store.get_language(1) == 'en'
    finally:
        await store.close()


async def test_new_start_invalidates_previous_selector_and_requires_new_choice(tmp_path, monkeypatch):
    from bot.services import cache
    clock = SimpleNamespace(now=1000)
    monkeypatch.setattr(cache.time, 'time', lambda: clock.now)
    store = SQLiteCache(tmp_path / 'db')
    try:
        old = await store.begin_language_choice(1, 10)
        await store.bind_language_choice(1, old, 20)
        assert await store.select_language(1, 10, 20, old, 'ro')
        new = await store.begin_language_choice(1, 10)
        await store.bind_language_choice(1, new, 21)
        assert await store.get_language(1) is None
        assert not await store.select_language(1, 10, 20, old, 'en')
        clock.now += 601
        assert not await store.select_language(1, 10, 21, new, 'en')
    finally:
        await store.close()


async def test_locale_is_task_local():
    from bot.utils.i18n import language_context, tr
    async def render(language):
        with language_context(language):
            await asyncio.sleep(0)
            return tr('📥 Descarcă video')
    assert await asyncio.gather(render('ru'), render('en'), render('ro')) == [
        '📥 Скачать видео', '📥 Download video', '📥 Descarcă video']


def test_translation_catalogs_have_matching_placeholders():
    from string import Formatter
    from bot.locales.core import CORE
    from bot.locales.media import MEDIA
    def fields(value):
        return {field for _, field, _, _ in Formatter().parse(value) if field is not None}
    for source, variants in {**CORE, **MEDIA}.items():
        assert set(variants) == {'ru', 'en'}, source
        for translated in variants.values():
            assert fields(source) == fields(translated), source


def test_multilingual_text_survives_copy_without_changing_source_data():
    from copy import deepcopy
    from bot.utils.i18n import LocalizedText, language_context
    with language_context('en'):
        value = LocalizedText({'ro': 'Română', 'ru': 'Русский', 'en': 'English'})
        assert deepcopy(value) == 'English'
        assert deepcopy(value).translations == value.translations


async def test_language_survives_runtime_restart_with_media_cache_disabled(tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime
    settings = Settings(DOWNLOAD_DIR=tmp_path / 'downloads', CACHE_DB_PATH=tmp_path / 'db', CACHE_ENABLED=False)
    first = BotRuntime(settings)
    try:
        await first.start()
        token = await first.media_details.begin_language_choice(7, 7)
        await first.media_details.bind_language_choice(7, token, 2)
        assert await first.media_details.select_language(7, 7, 2, token, 'en')
    finally:
        await first.close()
    second = BotRuntime(settings)
    try:
        await second.start()
        assert second.cache is None
        assert await second.media_details.get_language(7) == 'en'
    finally:
        await second.close()
