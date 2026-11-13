"""Bounded subprocess output, deadlines, and process-group cleanup."""

import asyncio
import math
import os
import signal

from bot.models.exceptions import DownloadTimeoutError, ProcessOutputLimitError


async def run_process(
    *args: str, timeout: float, max_output_bytes: int = 4 * 1024 * 1024,
) -> tuple[int, bytes, bytes]:
    """Capture bounded output; kill and reap the group on any interrupted run."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Process timeout must be positive and finite.")
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise ValueError("Process output limit must be a positive integer.")

    # Shield creation so cancellation cannot lose the handle to a newly spawned child.
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    ))
    process = None
    readers: list[asyncio.Task] = []
    captured_bytes = 0

    async def capture(stream: asyncio.StreamReader) -> bytes:
        nonlocal captured_bytes
        chunks = bytearray()
        while chunk := await stream.read(min(64 * 1024, max_output_bytes - captured_bytes + 1)):
            if captured_bytes + len(chunk) > max_output_bytes:
                raise ProcessOutputLimitError("External operation exceeded its output limit.")
            captured_bytes += len(chunk)
            chunks.extend(chunk)
        return bytes(chunks)

    async def discard(stream: asyncio.StreamReader) -> None:
        while await stream.read(64 * 1024):
            pass

    async def cleanup() -> None:
        child = process
        if child is None:
            try:
                child = await spawning
            except Exception:
                return  # Creation failed; there is no process to reap.
        try:
            # Kill even if the leader exited: descendants may still own its pipes.
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Drain without retaining bytes, including output already buffered at overflow.
        await asyncio.gather(discard(child.stdout), discard(child.stderr), child.wait())

    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.shield(spawning)
            readers = [asyncio.create_task(capture(process.stdout)),
                       asyncio.create_task(capture(process.stderr))]
            stdout, stderr, _ = await asyncio.gather(*readers, process.wait())
    except BaseException as exc:
        cleaning = asyncio.create_task(cleanup())
        # Repeated cancellation must not abandon children or pending pipe readers.
        while True:
            try:
                await asyncio.shield(cleaning)
                break
            except asyncio.CancelledError:
                if cleaning.done():
                    cleaning.result()
                    break
        if isinstance(exc, TimeoutError):
            raise DownloadTimeoutError("External operation exceeded its deadline.") from exc
        raise
    return process.returncode or 0, stdout, stderr
