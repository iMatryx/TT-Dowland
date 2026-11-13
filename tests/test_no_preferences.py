"""No user preference state remains; source policy is fixed."""
import sqlite3
import time
from unittest.mock import AsyncMock
from types import SimpleNamespace

from bot.handlers.keyboards import main_menu
from bot.handlers.commands import register_commands
from bot.services.cache import SQLiteCache


def test_main_menu_has_only_download_help_and_limits():
    assert [b.text for row in main_menu().keyboard for b in row] == [
        '📥 Descarcă video', '❓ Cum folosesc botul', 'ℹ️ Limite și calitate']


async def test_registered_commands_do_not_include_settings():
    bot = SimpleNamespace(set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
    await register_commands(bot)
    commands = bot.set_my_commands.call_args.args[0]
    assert 'settings' not in [command.command for command in commands]


async def test_old_preference_table_is_removed_without_losing_media_details(tmp_path):
    path = tmp_path / 'cache.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE user_preferences (user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL)')
        db.execute('INSERT INTO user_preferences VALUES (1, ?)', ('{"quality":"low"}',))
        db.execute('CREATE TABLE media_details (bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, '
                   'message_id INTEGER NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL, '
                   'processed_at REAL NOT NULL, PRIMARY KEY (bot_id,chat_id,message_id))')
        db.execute('INSERT INTO media_details VALUES (1,2,3,?,?,?)', ('summary', 'details', time.time()))
    store = SQLiteCache(path)
    try:
        await store.open()
        tables = await store._call(lambda db: {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        assert 'user_preferences' not in tables
        assert await store.get_media_details(1, 2, 3) == ('summary', 'details')
        assert not hasattr(store, 'get_preferences') and not hasattr(store, 'update_preferences')
    finally:
        await store.close()


async def test_media_info_persists_without_media_cache_or_preferences(tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime
    settings = Settings(DOWNLOAD_DIR=tmp_path / 'downloads', CACHE_DB_PATH=tmp_path / 'cache.db', CACHE_ENABLED=False)
    first = BotRuntime(settings)
    try:
        await first.start()
        assert first.cache is None and not hasattr(first, 'preferences')
        await first.media_details.put_media_details(1, 2, 3, 'summary', 'details')
    finally:
        await first.close()
    second = BotRuntime(settings)
    try:
        await second.start()
        assert second.cache is None
        assert await second.media_details.get_media_details(1, 2, 3) == ('summary', 'details')
    finally:
        await second.close()
