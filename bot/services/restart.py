"""In-memory one-shot restart confirmations; no process or shell operations."""
import asyncio
from dataclasses import dataclass, field
import secrets
import time

RESTART_EXIT_CODE = 75


@dataclass
class _Confirmation:
    token: str = field(repr=False)
    chat_id: int
    expires_at: float
    message_id: int | None = None


class RestartController:
    def __init__(self, admin_ids: frozenset[int], *, enabled: bool = False, clock=time.monotonic):
        self.admin_ids = admin_ids
        self.enabled = enabled
        self.clock = clock
        self.requested = asyncio.Event()
        self._pending: dict[int, _Confirmation] = {}
        self._confirmed = False

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def begin(self, user_id: int, chat_id: int) -> str:
        if not self.enabled or not self.is_admin(user_id) or chat_id != user_id or self.requested.is_set():
            raise PermissionError('Restart is not authorized or unavailable')
        token = secrets.token_urlsafe(16)
        self._pending[user_id] = _Confirmation(token, chat_id, self.clock() + 60)
        return token

    def bind(self, user_id: int, token: str, message_id: int) -> bool:
        item = self._pending.get(user_id)
        if not item or not token.isascii() or not secrets.compare_digest(item.token, token):
            return False
        item.message_id = message_id
        return True

    def valid(self, user_id: int, chat_id: int, message_id: int, token: str) -> bool:
        item = self._pending.get(user_id)
        return bool(self.enabled and self.is_admin(user_id) and not self.requested.is_set()
            and chat_id == user_id and item and item.chat_id == chat_id
            and item.message_id == message_id and item.expires_at > self.clock()
            and token.isascii() and secrets.compare_digest(item.token, token))

    def consume(self, user_id: int, chat_id: int, message_id: int, token: str) -> bool:
        if not self.valid(user_id, chat_id, message_id, token):
            return False
        del self._pending[user_id]
        self._confirmed = True
        return True

    def cancel(self, user_id: int, chat_id: int, message_id: int, token: str) -> bool:
        if not self.valid(user_id, chat_id, message_id, token):
            return False
        del self._pending[user_id]
        return True

    def request_restart(self) -> bool:
        if not self.enabled or not self.admin_ids or not self._confirmed or self.requested.is_set():
            return False
        self._confirmed = False
        self._pending.clear()
        self.requested.set()
        return True
