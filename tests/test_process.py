import asyncio
from pathlib import Path
import sys

import pytest

from bot.models.exceptions import DownloadTimeoutError
from bot.utils.process import run_process


@pytest.mark.asyncio
async def test_process_runs_without_shell_interpretation() -> None:
    code, stdout, _ = await run_process(
        sys.executable, "-c", "import sys; print(sys.argv[1])", "$(unsafe);literal", timeout=5,
    )
    assert code == 0
    assert stdout.strip() == b"$(unsafe);literal"


@pytest.mark.asyncio
async def test_process_timeout_kills_before_late_write(tmp_path: Path) -> None:
    marker = tmp_path / "unexpected"
    with pytest.raises(DownloadTimeoutError):
        await run_process(sys.executable, "-c",
                          "import sys,time,pathlib; time.sleep(0.2); pathlib.Path(sys.argv[1]).touch()",
                          str(marker), timeout=0.02)
    await asyncio.sleep(0.3)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_cancellation_kills_descendants_before_workspace_cleanup(tmp_path: Path) -> None:
    ready, late_write = tmp_path / "ready", tmp_path / "late-write"
    child_code = "import sys,time,pathlib; time.sleep(0.4); pathlib.Path(sys.argv[1]).touch()"
    parent_code = (
        "import sys,subprocess,pathlib,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
        "pathlib.Path(sys.argv[3]).touch(); time.sleep(10)"
    )
    task = asyncio.create_task(run_process(
        sys.executable, "-c", parent_code, child_code, str(late_write), str(ready), timeout=5,
    ))
    try:
        async with asyncio.timeout(3):
            while not ready.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)
        assert not late_write.exists()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
