import json
from pathlib import Path

import pytest

from bot.config import Settings
from bot.models.exceptions import classify_error, PrivateVideoError
from bot.services import extraction, ytdlp_worker
from bot.utils.telegram_text import error_text


URL = 'https://www.youtube.com/shorts/16ECzizrWR8'


def test_youtube_antibot_has_actionable_error():
    error = classify_error(
        'ERROR: [youtube] 16ECzizrWR8: Sign in to confirm you’re not a bot. '
        'This helps protect our community. Use --cookies for authentication.')
    message = error_text(error)
    assert 'YouTube' in message
    assert 'sesiun' in message.lower()
    assert '16ECzizrWR8' not in message
    assert 'privată' not in message


def test_private_youtube_media_still_rejected_with_authenticated_metadata():
    with pytest.raises(PrivateVideoError):
        extraction.metadata_from_ytdlp({'id': '16ECzizrWR8', 'availability': 'private'}, URL)


def test_explicit_youtube_private_error_is_not_an_antibot_error():
    assert type(classify_error('ERROR: [youtube] Private video. Sign in if you have access.')) is PrivateVideoError


def test_optional_youtube_cookie_setting():
    assert Settings(_env_file=None, YOUTUBE_COOKIES_FILE='').youtube_cookies_file is None
    assert Settings(_env_file=None, YOUTUBE_COOKIES_FILE='~/cookies.txt').youtube_cookies_file == Path.home() / 'cookies.txt'


def test_youtube_cookies_are_scoped_and_never_saved(tmp_path):
    cookies = tmp_path / 'youtube.txt'
    content = ('# Netscape HTTP Cookie File\n'
               '.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tprivate-session\n'
               '.example.com\tTRUE\t/\tTRUE\t2147483647\tother\tunrelated-secret\n')
    cookies.write_text(content)
    cookies.chmod(0o400)
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_youtube_cookies(ydl, URL, str(cookies))
        assert 'SAPISID=private-session' in ydl.cookiejar.get_cookie_header(URL)
        assert ydl.cookiejar.get_cookie_header('https://example.com/') is None
        assert ydl.params['cookiefile'] is None
    assert cookies.read_text() == content


def test_youtube_cookies_not_loaded_for_other_platforms(tmp_path):
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_youtube_cookies(ydl, 'https://www.instagram.com/reel/ABC/', str(tmp_path / 'missing'))
        assert not list(ydl.cookiejar)


def test_invalid_youtube_cookie_file_does_not_leak_contents(tmp_path):
    cookies = tmp_path / 'youtube.txt'
    cookies.write_text('invalid private-session')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError, match='YouTube cookies file') as exc:
            ytdlp_worker.load_youtube_cookies(ydl, URL, str(cookies))
    assert 'private-session' not in str(exc.value)


def test_expired_youtube_session_is_rejected(tmp_path):
    cookies = tmp_path / 'youtube.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.youtube.com\tTRUE\t/\tTRUE\t1\tSAPISID\texpired-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError, match='expired'):
            ytdlp_worker.load_youtube_cookies(ydl, URL, str(cookies))


def test_session_cookie_with_zero_expiry_is_accepted(tmp_path):
    cookies = tmp_path / 'youtube.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tsession-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_youtube_cookies(ydl, URL, str(cookies))
        assert ydl.cookiejar.get_cookie_header(URL) == 'SAPISID=session-secret'


def test_malformed_cookie_entries_never_print_secrets(tmp_path, capsys):
    cookies = tmp_path / 'youtube.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.youtube.com\tTRUE\t/\tTRUE\tinvalid\tSAPISID\tparser-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError):
            ytdlp_worker.load_youtube_cookies(ydl, URL, str(cookies))
    captured = capsys.readouterr()
    assert 'parser-secret' not in captured.out + captured.err


@pytest.mark.parametrize('action', ['extract', 'download'])
def test_workers_use_youtube_session_at_network_boundary(tmp_path, monkeypatch, action):
    cookies = tmp_path / 'youtube.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\ttest-session\n')
    info = {'id': 'clip', 'webpage_url': URL, 'formats': [
        {'format_id': 'source', 'url': 'https://cdn.example/video.mp4', 'protocol': 'https'}]}
    observed = []

    def network_boundary(ydl, *args, **kwargs):
        observed.append(ydl.cookiejar.get_cookie_header(URL))
        return info

    monkeypatch.setattr(ytdlp_worker, 'extract_job', lambda url, ydl: network_boundary(ydl))
    monkeypatch.setattr(ytdlp_worker.YoutubeDL, 'process_ie_result', network_boundary)
    info_path = tmp_path / 'info.json'
    info_path.write_text(json.dumps(info))
    payload = tmp_path / 'job.json'
    payload.write_text(json.dumps({'url': URL, 'output': str(tmp_path / 'out.json'),
        'info_path': str(info_path), 'video_id': 'source', 'max_bytes': 100,
        'media_dir': str(tmp_path), 'youtube_cookies_file': str(cookies)}))
    monkeypatch.setattr(ytdlp_worker.sys, 'argv', ['worker', action, str(payload)])
    assert ytdlp_worker.worker_main() == 0
    assert observed == ['SAPISID=test-session']


@pytest.mark.parametrize('url,expected', [(URL, '/run/secrets/youtube.txt'),
    ('https://www.instagram.com/reel/ABC/', None)])
async def test_extractor_passes_cookie_path_only_for_youtube(tmp_path, monkeypatch, url, expected):
    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        assert data.get('youtube_cookies_file') == expected
        Path(data['output']).write_text(json.dumps({'id': 'clip', 'formats': []}))

    monkeypatch.setattr(extraction, 'run_worker', worker)
    extractor = extraction.YtdlpExtractor(youtube_cookies_file=Path('/run/secrets/youtube.txt'))
    await extractor.extract(url, tmp_path)
