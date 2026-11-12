"""Cancellable subprocess execution with a deadline and process-group cleanup."""

import asyncio
import os
import signal

from bot.models.exceptions import DownloadTimeoutError


async def run_process(*args: str, timeout: float) -> tuple[int, bytes, bytes]:
    """Wait for a child; timeout/cancellation kills and reaps its entire group."""
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise DownloadTimeoutError("External operation exceeded its deadline.") from exc
    return process.returncode or 0, stdout, stderr
