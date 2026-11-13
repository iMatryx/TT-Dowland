from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from bot.models.exceptions import InvalidURLError
from bot.services.urls import normalize_url, resolve_url
from bot.handlers.links import message_url
from bot.services import ytdlp_worker


@pytest.mark.parametrize('value,expected', [
    ('youtube.com/shorts/AbCd_123-xy?si=share', 'https://www.youtube.com/shorts/AbCd_123-xy'),
    ('https://m.youtube.com/shorts/AbCd_123-xy/', 'https://www.youtube.com/shorts/AbCd_123-xy'),
    ('https://youtu.be/AbCd_123-xy?si=x', 'https://www.youtube.com/shorts/AbCd_123-xy'),
    ('instagram.com/reel/ABC_123-xy/?igsh=share', 'https://www.instagram.com/reel/ABC_123-xy/'),
    ('https://www.instagram.com/reels/ABC_123-xy/', 'https://www.instagram.com/reel/ABC_123-xy/'),
])
def test_platform_urls(value, expected):
    assert normalize_url(value) == expected
    assert message_url(SimpleNamespace(text='Descarcă ' + value, entities=[])) == expected


@pytest.mark.parametrize('url', [
    'https://youtube.com.evil.test/shorts/AbCd_123-xy',
    'https://youtube.com/playlist?list=123', 'https://youtube.com/@author',
    'https://youtube.com/watch?v=AbCd_123-xy', 'https://youtube.com/shorts/invalid',
    'https://instagram.com/accounts/login/', 'https://instagram.com/author/',
    'https://instagram.com.evil.test/reel/ABC/', 'https://name:pass@instagram.com/reel/ABC/',
    'https://instagram.com:123/reel/ABC/',
])
def test_rejects_non_clip_or_hostile_urls(url):
    with pytest.raises(InvalidURLError):
        normalize_url(url)


@pytest.mark.asyncio
async def test_new_platforms_do_not_use_tiktok_redirect_requests():
    def unexpected(request):
        raise AssertionError('Canonical clips need no redirect resolution')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        assert await resolve_url('https://youtu.be/AbCd_123-xy', client) == 'https://www.youtube.com/shorts/AbCd_123-xy'


@pytest.mark.asyncio
async def test_tiktok_redirect_cannot_switch_platforms():
    transport = httpx.MockTransport(lambda r: httpx.Response(302, headers={
        'location': 'https://www.instagram.com/reel/ABC/'}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(InvalidURLError):
            await resolve_url('https://vm.tiktok.com/ABC/', client)


@pytest.mark.parametrize('url,key', [
    ('https://www.youtube.com/shorts/AbCd_123-xy', 'Youtube'),
    ('https://www.instagram.com/reel/ABC/', 'Instagram'),
])
def test_platform_extractors_are_explicit(url, key):
    ydl = SimpleNamespace(extract_info=Mock(return_value={'id': 'clip'}))
    assert ytdlp_worker.extract_job(url, ydl) == {'id': 'clip'}
    ydl.extract_info.assert_called_once_with(url, download=False, ie_key=key)
