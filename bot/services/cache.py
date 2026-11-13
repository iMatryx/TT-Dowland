"""SQLite Telegram file-id cache with one dedicated database thread."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Literal, TypeVar

from bot.models import VideoFormat
from bot.models.preferences import UserPreferences

T = TypeVar("T")


@dataclass(frozen=True)
class CacheEntry:
    bot_id: int
    video_id: str
    url: str
    quality: str
    kind: Literal["video", "document", "zip", "audio"]
    file_id: str
    size: int
    source: VideoFormat | None
    verified: VideoFormat | None
    processed_at: float = field(default_factory=time.time)
    original: bool = False


def _saved_preferences(settings: str) -> UserPreferences:
    return UserPreferences.model_validate_json(settings)


class SQLiteCache:
    def __init__(self, path: Path, *, ttl: float = 7 * 86400, capacity: int = 10000) -> None:
        self.path, self.ttl, self.capacity = path, ttl, capacity
        self._executor: ThreadPoolExecutor | None = None
        self._connection: sqlite3.Connection | None = None
        self._closed = False

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = sqlite3.connect(self.path, timeout=5)
            try:
                os.chmod(self.path, 0o600)
                if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                    raise sqlite3.DatabaseError("Unsupported cache schema version")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS media (
                        bot_id INTEGER NOT NULL, video_id TEXT NOT NULL,
                        url TEXT NOT NULL, quality TEXT NOT NULL, kind TEXT NOT NULL,
                        file_id TEXT NOT NULL, size INTEGER NOT NULL,
                        processed_at REAL NOT NULL, original INTEGER NOT NULL,
                        source TEXT, verified TEXT,
                        PRIMARY KEY (bot_id, video_id, quality, kind)
                    );
                    CREATE INDEX IF NOT EXISTS media_processed ON media(processed_at);
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS media_details (
                        bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL, summary TEXT NOT NULL,
                        details TEXT NOT NULL, processed_at REAL NOT NULL,
                        PRIMARY KEY (bot_id, chat_id, message_id)
                    );
                    CREATE INDEX IF NOT EXISTS details_processed ON media_details(processed_at);
                    PRAGMA user_version=1;
                """)
            except BaseException:
                db.close()
                raise
            self._connection = db
        return self._connection

    async def _call(self, function: Callable[[sqlite3.Connection], T]) -> T:
        if self._closed:
            raise RuntimeError("Cache is closed")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-cache")
        return await asyncio.get_running_loop().run_in_executor(self._executor, lambda: function(self._db()))

    async def open(self) -> None:
        await self._call(lambda db: None)

    async def put_media_details(self, bot_id: int, chat_id: int, message_id: int,
                                summary: str, details: str) -> None:
        if len(summary) > 1024 or len(details) > 1024:
            raise ValueError("Media captions exceed Telegram's limit")
        def write(db):
            with db:
                db.execute("INSERT OR REPLACE INTO media_details VALUES (?,?,?,?,?,?)",
                           (bot_id, chat_id, message_id, summary, details, time.time()))
                db.execute("DELETE FROM media_details WHERE processed_at<=?", (time.time() - self.ttl,))
                db.execute("DELETE FROM media_details WHERE rowid IN "
                           "(SELECT rowid FROM media_details ORDER BY processed_at DESC LIMIT -1 OFFSET ?)", (self.capacity,))
        await self._call(write)

    async def get_media_details(self, bot_id: int, chat_id: int, message_id: int) -> tuple[str, str] | None:
        def read(db):
            row = db.execute("SELECT summary,details FROM media_details WHERE bot_id=? AND chat_id=? "
                             "AND message_id=? AND processed_at>?", (bot_id, chat_id, message_id, time.time() - self.ttl)).fetchone()
            return (row[0], row[1]) if row else None
        return await self._call(read)

    async def get_preferences(self, user_id: int) -> UserPreferences:
        def read(db: sqlite3.Connection) -> UserPreferences:
            row = db.execute("SELECT settings FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
            return _saved_preferences(row[0]) if row else UserPreferences()
        return await self._call(read)

    async def update_preferences(self, user_id: int, *, reset: bool = False, **changes) -> UserPreferences:
        def write(db: sqlite3.Connection) -> UserPreferences:
            # Read, validate and write within the database's serialized transaction.
            with db:
                row = db.execute("SELECT settings FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
                current = _saved_preferences(row[0]).model_dump() if row and not reset else {}
                result = UserPreferences.model_validate({**current, **changes})
                db.execute("INSERT OR REPLACE INTO user_preferences VALUES (?,?)", (user_id, result.model_dump_json()))
            return result
        return await self._call(write)

    async def get(self, bot_id: int, video_id: str, quality: str,
                  kind: str) -> CacheEntry | None:
        def lookup(db: sqlite3.Connection) -> CacheEntry | None:
            row = db.execute(
                "SELECT bot_id,video_id,url,quality,kind,file_id,size,processed_at,original,source,verified "
                "FROM media WHERE bot_id=? AND video_id=? AND quality=? AND kind=? "
                "AND processed_at>? AND (kind!='document' OR original=1)",
                (bot_id, video_id, quality, kind, time.time() - self.ttl),
            ).fetchone()
            if row is None:
                return None
            return CacheEntry(
                bot_id=row[0], video_id=row[1], url=row[2], quality=row[3], kind=row[4],
                file_id=row[5], size=row[6], processed_at=row[7], original=bool(row[8]),
                source=VideoFormat.model_validate_json(row[9]) if row[9] else None,
                verified=VideoFormat.model_validate_json(row[10]) if row[10] else None,
            )
        return await self._call(lookup)

    async def put(self, entry: CacheEntry) -> None:
        def write(db: sqlite3.Connection) -> None:
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (entry.bot_id, entry.video_id, entry.url, entry.quality, entry.kind,
                     entry.file_id, entry.size, entry.processed_at, int(entry.original),
                     entry.source.model_dump_json() if entry.source else None,
                     entry.verified.model_dump_json() if entry.verified else None),
                )
                db.execute("DELETE FROM media WHERE processed_at<=?", (time.time() - self.ttl,))
                db.execute(
                    "DELETE FROM media WHERE rowid IN (SELECT rowid FROM media ORDER BY processed_at DESC LIMIT -1 OFFSET ?)",
                    (self.capacity,),
                )
        await self._call(write)

    async def invalidate(self, entry: CacheEntry) -> None:
        def delete(db: sqlite3.Connection) -> None:
            with db:
                db.execute("DELETE FROM media WHERE bot_id=? AND video_id=? AND quality=? AND kind=?",
                           (entry.bot_id, entry.video_id, entry.quality, entry.kind))
        await self._call(delete)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        executor = self._executor
        if executor is not None:
            def finish() -> None:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
            await asyncio.get_running_loop().run_in_executor(executor, finish)
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
