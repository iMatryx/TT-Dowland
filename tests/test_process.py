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


@pytest.mark.asyncio
async def test_process_returns_exact_combined_output_limit() -> None:
    code, stdout, stderr = await run_process(
        sys.executable, '-c', "import sys; sys.stdout.write('abc'); sys.stderr.write('de'); sys.exit(7)",
        timeout=5, max_output_bytes=5,
    )
    assert (code, stdout, stderr) == (7, b'abc', b'de')


@pytest.mark.asyncio
@pytest.mark.parametrize('destination', ['stdout', 'stderr', 'both'])
async def test_output_overflow_kills_group_before_late_write(tmp_path: Path, destination: str) -> None:
    from bot.models.exceptions import ApplicationError

    late_write = tmp_path / 'late-write'
    child = "import sys,time,pathlib; time.sleep(0.4); pathlib.Path(sys.argv[1]).touch()"
    script = (
        "import sys,subprocess,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
        "destination=sys.argv[3]; "
        "sys.stdout.buffer.write(b'SENSITIVE' * (32 if destination != 'stderr' else 0)); sys.stdout.flush(); "
        "sys.stderr.buffer.write(b'SENSITIVE' * (32 if destination != 'stdout' else 0)); sys.stderr.flush(); "
        "time.sleep(10)"
    )
    with pytest.raises(ApplicationError) as caught:
        await run_process(sys.executable, '-c', script, child, str(late_write), destination,
                          timeout=3, max_output_bytes=500 if destination == 'both' else 250)
    assert type(caught.value).__name__ == 'ProcessOutputLimitError'
    assert 'SENSITIVE' not in str(caught.value)
    await asyncio.sleep(0.5)
    assert not late_write.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'timeout': 0}, {'timeout': -1}, {'timeout': float('nan')},
    {'timeout': 5, 'max_output_bytes': 0}, {'timeout': 5, 'max_output_bytes': -1},
])
async def test_invalid_process_limits_rejected_before_spawn(tmp_path: Path, kwargs: dict) -> None:
    marker = tmp_path / 'unexpected'
    with pytest.raises(ValueError):
        await run_process(sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).touch()',
                          str(marker), **kwargs)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_process_drains_both_pipes_concurrently() -> None:
    script = (
        "import sys,threading; "
        "t=threading.Thread(target=lambda: sys.stderr.buffer.write(b'e'*200000)); t.start(); "
        "sys.stdout.buffer.write(b'o'*200000); t.join()"
    )
    code, stdout, stderr = await run_process(sys.executable, '-c', script,
                                           timeout=5, max_output_bytes=400000)
    assert (code, stdout, stderr) == (0, b'o' * 200000, b'e' * 200000)


@pytest.mark.asyncio
async def test_default_output_limit_stops_continuous_flood(tmp_path: Path) -> None:
    from bot.models.exceptions import ProcessOutputLimitError

    marker = tmp_path / 'unexpected'
    script = (
        "import sys,time,pathlib; "
        "sys.stdout.buffer.write(b'x' * (5*1024*1024)); sys.stdout.flush(); "
        "time.sleep(0.3); pathlib.Path(sys.argv[1]).touch()"
    )
    with pytest.raises(ProcessOutputLimitError):
        await run_process(sys.executable, '-c', script, str(marker), timeout=5)
    await asyncio.sleep(0.4)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_timeout_kills_descendant_after_leader_exits(tmp_path: Path) -> None:
    marker = tmp_path / 'unexpected'
    child = "import sys,time,pathlib; time.sleep(0.4); pathlib.Path(sys.argv[1]).touch()"
    script = "import sys,subprocess; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])"
    with pytest.raises(DownloadTimeoutError):
        await run_process(sys.executable, '-c', script, child, str(marker), timeout=0.15)
    await asyncio.sleep(0.5)
    assert not marker.exists()
