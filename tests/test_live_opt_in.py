"""Local checks ensure optional server probes never contact services implicitly."""
import pytest


@pytest.mark.parametrize('missing', ['opt_in', 'server_context', 'fixture'])
async def test_public_live_probe_requires_all_opt_ins(tmp_path, monkeypatch, missing):
    from tests import test_live
    monkeypatch.setenv('RUN_PLATFORM_LIVE', '1')
    monkeypatch.setenv('LIVE_TEST_CONTEXT', 'server')
    monkeypatch.setenv('TIKTOK_TEST_URL', 'https://www.tiktok.com/@u/video/123')
    key = {'opt_in': 'RUN_PLATFORM_LIVE', 'server_context': 'LIVE_TEST_CONTEXT', 'fixture': 'TIKTOK_TEST_URL'}[missing]
    monkeypatch.delenv(key)
    def forbidden(*args, **kwargs):
        pytest.fail('No engine may be created before live opt-in requirements pass')
    monkeypatch.setattr(test_live, 'TikTokDownloader', forbidden)
    with pytest.raises(pytest.skip.Exception):
        await test_live.test_public_media_live(tmp_path, 'tiktok', 'TIKTOK_TEST_URL', 'video')


async def test_telegram_live_send_requires_separate_authorization(tmp_path, monkeypatch):
    from tests import test_live
    monkeypatch.delenv('RUN_TELEGRAM_LIVE', raising=False)
    with pytest.raises(pytest.skip.Exception):
        await test_live.test_real_telegram_delivery(tmp_path)
