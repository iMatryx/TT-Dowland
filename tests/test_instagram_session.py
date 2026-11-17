import json
from pathlib import Path

import pytest

from bot.config import Settings
from bot.models.exceptions import InstagramAccessError, NetworkError, classify_error
from bot.services import extraction, ytdlp_worker
from bot.utils.telegram_text import error_text
from yt_dlp.utils import DownloadError, ExtractorError


URL = 'https://www.instagram.com/reel/Dfy8QznNhn5/'


def test_instagram_anonymous_limit_has_actionable_error():
    error = classify_error(
        'ERROR: [Instagram] Dfy8QznNhn5: The webpage request was redirected to the login page. '
        'You have exceeded the rate-limit for accessing posts anonymously.')
    message = error_text(error)
    assert 'Instagram' in message
    assert 'sesiun' in message.lower()
    assert 'Dfy8QznNhn5' not in message


def test_real_instagram_extractor_preserves_access_error(monkeypatch):
    """Exercise yt-dlp error formatting, including the custom extractor's name."""
    def login_required(self, url):
        self.raise_login_required(
            'The webpage request was redirected to the login page. '
            'You have exceeded the rate-limit for accessing posts anonymously')

    monkeypatch.setattr(ytdlp_worker.PublicInstagramIE, '_real_extract', login_required)
    # Initialization fetches Instagram's homepage before _real_extract.
    monkeypatch.setattr(ytdlp_worker.PublicInstagramIE, '_real_initialize', lambda self: None)
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(DownloadError) as exc:
            ydl.extract_info(URL, download=False, ie_key='Instagram')
    assert isinstance(classify_error(str(exc.value)), InstagramAccessError)


@pytest.mark.parametrize('prefix', ['Instagram', 'PublicInstagram'])
@pytest.mark.parametrize('reason', [
    'The webpage request was redirected to the login page. '
    'You have exceeded the rate-limit for accessing posts anonymously.',
    'Unable to download webpage: HTTP Error 429: Too Many Requests',
    'login_required',
])
def test_instagram_access_denials_are_not_generic_connection_errors(prefix, reason):
    error = classify_error(f'ERROR: [{prefix}] clip: {reason}')
    assert isinstance(error, InstagramAccessError)
    assert 'sesiun' in error_text(error).lower()


def test_instagram_transport_failure_remains_a_network_error():
    error = classify_error('ERROR: [Instagram] clip: Unable to download webpage: Connection reset by peer')
    assert type(error) is NetworkError


def test_optional_instagram_cookie_setting():
    assert Settings(_env_file=None, INSTAGRAM_COOKIES_FILE='').instagram_cookies_file is None
    assert Settings(_env_file=None, INSTAGRAM_COOKIES_FILE='~/cookies.txt').instagram_cookies_file == Path.home() / 'cookies.txt'


def test_instagram_cookies_are_scoped_and_never_saved(tmp_path):
    cookies = tmp_path / 'instagram.txt'
    content = ('# Netscape HTTP Cookie File\n'
               '.instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\tprivate-session\n'
               '.example.com\tTRUE\t/\tTRUE\t2147483647\tother\tunrelated-secret\n')
    cookies.write_text(content)
    cookies.chmod(0o400)
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_instagram_cookies(ydl, URL, str(cookies))
        assert 'sessionid=private-session' in ydl.cookiejar.get_cookie_header(URL)
        assert ydl.cookiejar.get_cookie_header('https://example.com/') is None
        assert ydl.params['cookiefile'] is None
    assert cookies.read_text() == content


def test_instagram_cookies_not_loaded_for_other_platforms(tmp_path):
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_instagram_cookies(ydl, 'https://www.youtube.com/shorts/AbCd_123-xy', str(tmp_path / 'missing'))
        assert not list(ydl.cookiejar)


def test_invalid_instagram_cookie_file_does_not_leak_contents(tmp_path):
    cookies = tmp_path / 'instagram.txt'
    cookies.write_text('invalid private-session')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError, match='Instagram cookies file') as exc:
            ytdlp_worker.load_instagram_cookies(ydl, URL, str(cookies))
    assert 'private-session' not in str(exc.value)


def test_expired_instagram_session_is_rejected(tmp_path):
    cookies = tmp_path / 'instagram.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.instagram.com\tTRUE\t/\tTRUE\t1\tsessionid\texpired-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError, match='expired'):
            ytdlp_worker.load_instagram_cookies(ydl, URL, str(cookies))


def test_session_cookie_with_zero_expiry_is_accepted(tmp_path):
    cookies = tmp_path / 'instagram.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsession-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        ytdlp_worker.load_instagram_cookies(ydl, URL, str(cookies))
        assert ydl.cookiejar.get_cookie_header(URL) == 'sessionid=session-secret'


def test_malformed_cookie_entries_never_print_secrets(tmp_path, capsys):
    cookies = tmp_path / 'instagram.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.instagram.com\tTRUE\t/\tTRUE\tinvalid\tsessionid\tparser-secret\n')
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError):
            ytdlp_worker.load_instagram_cookies(ydl, URL, str(cookies))
    captured = capsys.readouterr()
    assert 'parser-secret' not in captured.out + captured.err


def test_private_instagram_posts_are_rejected_before_metadata_is_discarded():
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        extractor = ydl.get_info_extractor('Instagram')
        with pytest.raises(ExtractorError, match='Private Instagram'):
            extractor._extract_product({'user': {'is_private': True}, 'pk': '123'})


@pytest.mark.parametrize('action', ['extract', 'download'])
def test_workers_use_instagram_session_at_network_boundary(tmp_path, monkeypatch, action):
    cookies = tmp_path / 'instagram.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n'
        '.instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttest-session\n')
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
        'media_dir': str(tmp_path), 'instagram_cookies_file': str(cookies)}))
    monkeypatch.setattr(ytdlp_worker.sys, 'argv', ['worker', action, str(payload)])
    assert ytdlp_worker.worker_main() == 0
    assert observed == ['sessionid=test-session']


@pytest.mark.parametrize('url,expected', [(URL, '/run/secrets/instagram.txt'),
    ('https://www.youtube.com/shorts/AbCd_123-xy', None)])
async def test_extractor_passes_cookie_path_only_for_instagram(tmp_path, monkeypatch, url, expected):
    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        assert data.get('instagram_cookies_file') == expected
        Path(data['output']).write_text(json.dumps({'id': 'clip', 'formats': []}))

    monkeypatch.setattr(extraction, 'run_worker', worker)
    extractor = extraction.YtdlpExtractor(instagram_cookies_file=Path('/run/secrets/instagram.txt'))
    await extractor.extract(url, tmp_path)
