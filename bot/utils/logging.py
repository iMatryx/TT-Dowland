"""Redact bot tokens and common authorization secrets from log records."""

import logging
import re
import traceback


class SecretFilter(logging.Filter):
    def __init__(self, token: str | None = None) -> None:
        super().__init__()
        self.token = token

    def redact(self, value: str) -> str:
        if self.token:
            value = value.replace(self.token, "[REDACTED]")
        value = re.sub(r"\b[0-9]{5,}:[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        value = re.sub(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+", r"\1[REDACTED]", value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True
